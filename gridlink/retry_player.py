"""Jugador de reintentos: usa el servidor como oráculo y busca offline.

Tres hechos verificados del servidor lo hacen posible (ver `CLAUDE.md`):

1. La secuencia de solicitudes es **fija y determinista** por partida.
2. Los resets están permitidos y el servidor **guarda el mejor puntaje** de cada
   partida, así que un reintento fallido no puede empeorar lo ya conseguido.
3. Con `bridge_probability = 0` y `potential_count = 0` el tablero evoluciona de
   forma determinista, así que `Board.mark_path` reproduce localmente lo mismo
   que haría el servidor. Por eso la búsqueda es offline y el HTTP se gasta sólo
   en verificar. Si esos dos parámetros no son cero (posible en la entrega
   final), este jugador se abstiene y hay que volver a `game_runner.play_game`.

La limitación que le da forma al algoritmo: **cuando la partida muere el servidor
devuelve `requests: []`**, así que nunca vemos la solicitud que nos mató. Un
reintento no puede optimizar contra ella; sólo puede proponer una línea distinta
para las solicitudes ya conocidas y dejar que el servidor juzgue. De ahí que la
diversidad entre intentos sea el motor de este jugador, y no una búsqueda dirigida.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .board import Board
from .game_runner import GameResult
from .rules import Coord, find_any_path
from .strategies import (
    bidirectional_path,
    cheapest_shortest_path,
    min_turns_shortest_path,
)

Request = tuple[int, int]
Line = list[list[Coord]]


@dataclass
class RetryConfig:
    """Presupuestos. Los valores por defecto respetan 10 partidas en 10 minutos."""

    # Tope de segundos por partida, incluyendo red.
    time_budget: float = 55.0
    # Tope de jugadas enviadas al servidor. Es el recurso escaso: cada reintento
    # que no comparte prefijo con lo ya enviado cuesta una línea completa.
    move_budget: int = 400
    # Tope de nodos expandidos por búsqueda offline.
    search_nodes: int = 30_000
    # Cuántas líneas se generan offline por intento antes de elegir cuál enviar.
    lines_per_attempt: int = 12
    # Elegir entre esas líneas por salud del tablero (`line_health`) en vez de
    # tomar la primera. **Apagado porque está medido que no sirve**: no mejoró
    # ninguna partida y empeoró id 26 (12/28 -> 10/28), pese a ser el escenario
    # más favorable posible para una métrica de daño, ya que todas las líneas
    # comparadas completan exactamente las mismas solicitudes y por lo tanto
    # elegir por daño no cuesta ni un paso de más. Ver `ESTADO_PROYECTO.md`.
    use_line_health: bool = False


def candidate_paths(
    board: Board,
    start: Coord,
    end: Coord,
    connection_id: int,
    future_connectors: list[Coord] | None = None,
) -> list[list[Coord]]:
    """Caminos distintos entre dos conectores, del mejor al peor según lo medido.

    El orden replica el ranking medido de las estrategias: primero el BFS pelado
    (1582), después el de menos quiebres (1562), después los desempates por daño
    entre caminos de largo mínimo.

    **Los rodeos van al final, y eso es deliberado.** Está medido tres veces que
    permitir rodeos empeora a un jugador sin reintentos (ver `ESTADO_PROYECTO.md`,
    "la ley de diseño"), pero un buscador con backtracking no paga ese precio:
    sólo llega a los rodeos cuando ya agotó todos los caminos mínimos, es decir
    cuando la alternativa era perder la ronda. Medido: sin rodeos el buscador
    saca 3/5 en id 12, donde `evasivo` (que sí los usa) saca 5/5.
    """
    from .pathfinder import (
        PathfinderConfig,
        _make_cost,
        free_neighbors,
        strip_self_touches,
        weighted_path,
    )

    protected = {
        coord: len(free_neighbors(board, coord)) for coord in (future_connectors or [])
    }
    settings = PathfinderConfig()

    def detour(penalty: float):
        def build():
            cost = _make_cost(board, protected, settings, penalty, None)
            path = weighted_path(board, start, end, connection_id, cost)
            return None if path is None else strip_self_touches(board, path)

        return build

    builders = (
        lambda: find_any_path(board, start, end, connection_id),
        lambda: min_turns_shortest_path(board, start, end, connection_id),
        lambda: cheapest_shortest_path(
            board,
            start,
            end,
            connection_id,
            lambda coord: float(4 - len(free_neighbors(board, coord))),
        ),
        lambda: cheapest_shortest_path(
            board,
            start,
            end,
            connection_id,
            lambda coord: float(len(free_neighbors(board, coord))),
        ),
        lambda: bidirectional_path(board, start, end, connection_id),
        detour(6.0),
        detour(18.0),
    )

    paths: list[list[Coord]] = []
    seen: set[tuple[Coord, ...]] = set()
    for build in builders:
        path = build()
        if path is None:
            continue
        key = tuple(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


@dataclass
class _Search:
    config: RetryConfig
    deadline: float
    rng: random.Random | None
    # Primera ronda que se permite variar. Las anteriores usan siempre el orden
    # determinista, así que dos intentos comparten prefijo y el servidor sólo
    # tiene que recibir la cola. Ver `_Session.submit`.
    vary_from: int = 0
    nodes: int = 0

    @property
    def exhausted(self) -> bool:
        return self.nodes >= self.config.search_nodes or time.monotonic() > self.deadline


def _descend(board: Board, requests: list[Request], depth: int, state: _Search) -> Line | None:
    """Backtracking cronológico sobre qué camino usar en cada ronda."""
    if depth == len(requests):
        return []
    if state.exhausted:
        return None

    state.nodes += 1
    connectors = board.find_connectors()
    first, second = requests[depth]
    if first not in connectors or second not in connectors:
        return None

    used = {identifier for pair in requests[: depth + 1] for identifier in pair}
    future = [
        position for identifier, position in connectors.items() if identifier not in used
    ]
    options = candidate_paths(
        board, connectors[first], connectors[second], depth + 1, future
    )
    if state.rng is not None and depth >= state.vary_from:
        # La diversidad entre intentos es el motor del jugador: sin barajar, dos
        # intentos sobre las mismas solicitudes devuelven la misma línea. Se
        # baraja el conjunto completo, rodeos incluidos: como el DFS completa las
        # solicitudes conocidas con caminos mínimos casi siempre, dejar los
        # rodeos al final equivale a no explorarlos nunca, y son justamente los
        # que ganan partidas como id 12.
        state.rng.shuffle(options)

    for path in options:
        trial = board.clone()
        try:
            trial.mark_path(path, depth + 1)
        except ValueError:
            continue
        rest = _descend(trial, requests, depth + 1, state)
        if rest is not None:
            return [path] + rest
        if state.exhausted:
            return None
    return None


def search_line(
    board_string: str,
    requests: list[Request],
    config: RetryConfig,
    rng: random.Random | None = None,
    deadline: float | None = None,
    vary_from: int = 0,
) -> Line | None:
    """Busca offline una línea de jugadas que complete todas `requests`."""
    state = _Search(
        config=config,
        deadline=deadline if deadline is not None else time.monotonic() + config.time_budget,
        rng=rng,
        vary_from=vary_from,
    )
    return _descend(Board.from_string(board_string), requests, 0, state)


def line_health(board_string: str, line: Line, used: set[int]) -> float:
    """Fracción de pares de conectores libres que siguen siendo conectables.

    Es el criterio para elegir entre líneas que completan *lo mismo*. Morimos en
    una solicitud que el servidor nunca nos muestra, así que no se puede apuntar
    a ella; lo único accionable es dejar vivos la mayor cantidad de pares
    posibles, porque la solicitud desconocida es uno de ellos.

    Esto no contradice la ley de "el largo le gana a cualquier métrica de daño":
    esa ley vale cuando la métrica puede hacer que un camino se alargue. Aquí
    todas las líneas comparadas completan exactamente las mismas solicitudes, así
    que elegir por salud del tablero no cuesta nada.

    Medido en id 13: tras 5 rondas sólo 18 de 45 pares seguían conectables, y la
    solicitud 6 cayó entre los 27 muertos.
    """
    from .pathfinder import _components, free_neighbors

    board = Board.from_string(board_string)
    for index, path in enumerate(line):
        try:
            board.mark_path(path, index + 1)
        except ValueError:
            return -1.0

    connectors = board.find_connectors()
    free = [
        identifier for identifier in connectors if identifier not in used
    ]
    if len(free) < 2:
        return 0.0

    component_of, _ = _components(board)
    touched = {
        identifier: {
            component_of[neighbor]
            for neighbor in free_neighbors(board, connectors[identifier])
            if neighbor in component_of
        }
        for identifier in free
    }
    alive = sum(
        1
        for index, first in enumerate(free)
        for second in free[index + 1 :]
        if touched[first] & touched[second]
    )
    return alive / (len(free) * (len(free) - 1) / 2)


class _Session:
    """Estado del servidor más el prefijo de jugadas ya aplicado.

    Enviar una línea que comparte prefijo con lo que el servidor ya tiene cuesta
    sólo las jugadas nuevas. Es la diferencia entre un costo total de O(M) y uno
    de O(M²) en llamadas HTTP, y con el presupuesto de 10 min por lote esa
    diferencia decide si el jugador es usable en partidas largas.
    """

    def __init__(self, game, initial_board: str):
        self.game = game
        self.initial_board = initial_board
        self.applied: Line = []
        self.moves = 0
        self.alive = True
        self.last_response: dict | None = None

    def _common_prefix(self, line: Line) -> int:
        length = 0
        for mine, theirs in zip(self.applied, line):
            if mine != theirs:
                break
            length += 1
        return length

    def submit(self, line: Line, budget_left: int) -> tuple[dict | None, int]:
        """Deja el servidor en el estado de `line`. Devuelve la última respuesta."""
        shared = self._common_prefix(line) if self.alive else 0
        if shared < len(self.applied) or not self.alive:
            self.game.reset()
            self.game.start()
            self.applied = []
            self.alive = True
            shared = 0

        # Reconstruir el tablero del prefijo compartido desde cero es más simple
        # y más seguro que arrastrar estado mutable entre intentos.
        board = Board.from_string(self.initial_board)
        for index, path in enumerate(line[:shared]):
            board.mark_path(path, index + 1)

        response: dict | None = None
        for index in range(shared, len(line)):
            if self.moves >= budget_left:
                break
            board.mark_path(line[index], index + 1)
            response = self.game.move(board.to_string())
            self.last_response = response
            self.moves += 1
            if not response.get("valid"):
                self.alive = False
                return response, index
            self.applied = line[: index + 1]
            if response.get("status") != "in_progress":
                self.alive = False
                return response, index + 1
        return response, len(line)


def play_with_retries(
    game,
    game_id: int = 0,
    config: RetryConfig | None = None,
    seed: int = 0,
) -> GameResult:
    """Juega una partida reintentando líneas distintas cuando muere.

    El ciclo es: buscar offline una línea que complete todas las solicitudes
    conocidas, enviarla, y según lo que responda el servidor o bien aprender la
    solicitud siguiente (la partida sigue viva) o bien reintentar con otra línea
    para las mismas solicitudes (la partida murió).
    """
    config = config or RetryConfig()
    result = GameResult(game_id=game_id)
    started = time.monotonic()
    deadline = started + config.time_budget

    try:
        payload = game.start()
    except Exception as error:  # noqa: BLE001
        result.error = f"start falló: {error}"
        result.elapsed = time.monotonic() - started
        return result

    params = payload.get("params", {})
    result.total_requests = params.get("request_count", 0)

    if params.get("potential_count") or float(params.get("bridge_probability") or 0):
        result.error = (
            "el tablero muta entre rondas (puentes potenciales); la simulación "
            "offline no sería fiel, usar game_runner.play_game"
        )
        result.elapsed = time.monotonic() - started
        return result
    if params.get("request_window_size", 1) != 1:
        result.error = "ventana de solicitudes > 1 todavía no soportada"
        result.elapsed = time.monotonic() - started
        return result
    if not params.get("allow_player_resets", True):
        # Todo este jugador se apoya en reiniciar la partida para probar líneas
        # distintas. Sin resets no puede hacer nada, y hay que abstenerse
        # explícitamente: si se intentara igual, el `reset` fallaría a mitad de
        # camino y la partida quedaría en 0. El enunciado avisa que los lotes
        # evaluados vienen "con diferentes parámetros", así que este caso hay
        # que darlo por posible.
        result.error = "la partida no permite resets; usar game_runner.play_game"
        result.elapsed = time.monotonic() - started
        return result

    initial_board = payload["board"]
    session = _Session(game, initial_board)
    known: list[Request] = [tuple(payload["requests"][0])]
    rng = random.Random(seed)

    best_completed = 0
    best_score: int | None = None
    attempts = 0
    plain = True  # el primer intento va sin barajar: la línea más prometedora
    tried: set[tuple] = set()
    window = 1  # cuántas rondas finales se permite variar

    while time.monotonic() < deadline and session.moves < config.move_budget:
        attempts += 1
        # Reenviar una línea ya probada gasta HTTP sin aportar información, así
        # que se insiste en la búsqueda hasta dar con una nueva. La ventana de
        # variación arranca en la última ronda y se ensancha hacia atrás sólo
        # cuando la cola ya se agotó: morimos en la solicitud siguiente a la
        # última conocida, así que cambiar la ronda 1 cuando morimos en la 20 es
        # casi siempre irrelevante y además rompe el prefijo compartido.
        #
        # Se generan varias líneas offline y se envía sólo la más sana: el
        # cómputo local es gratis y las llamadas HTTP son el recurso escaso.
        used_connectors = {identifier for pair in known for identifier in pair}
        found: list[Line] = []
        for _ in range(config.lines_per_attempt):
            candidate = search_line(
                initial_board,
                known,
                config,
                rng=None if plain else rng,
                deadline=min(deadline, time.monotonic() + config.time_budget / 4),
                vary_from=max(0, len(known) - window),
            )
            if candidate is None:
                break
            key = tuple(tuple(path) for path in candidate)
            if key not in tried:
                tried.add(key)
                found.append(candidate)
            else:
                plain = False  # la línea determinista ya se probó: barajar
                window = min(window + 1, len(known))
            wanted = config.lines_per_attempt if config.use_line_health else 1
            if len(found) >= wanted or time.monotonic() > deadline:
                break
        if not found:
            line = None
        elif config.use_line_health:
            line = max(
                found,
                key=lambda option: line_health(initial_board, option, used_connectors),
            )
        else:
            line = found[0]
        if line is None:
            # O no hay ninguna combinación que complete las solicitudes conocidas,
            # o ya se probaron todas las que la búsqueda sabe generar.
            break

        response, reached = session.submit(line, config.move_budget)
        if response is None:
            break
        if not response.get("valid"):
            result.error = f"jugada inválida en la ronda {reached + 1}: {response.get('error')}"
            break

        completed = response.get("completed_requests", reached)
        if completed > best_completed:
            best_completed = completed
        if response.get("score") is not None:
            if best_score is None or response["score"] > best_score:
                best_score = response["score"]

        result.rounds.append(
            {
                "attempt": attempts,
                "known_requests": len(known),
                "completed": completed,
                "moves": session.moves,
                "blocked": bool(response.get("blocked")),
            }
        )

        if response.get("status") == "in_progress":
            upcoming = response.get("requests") or []
            if not upcoming:
                break
            known.append(tuple(upcoming[0]))
            plain = True  # línea nueva y viva: seguir por el camino prometedor
            window = 1
            continue

        # La partida terminó. Si fue por bloqueo, reintentar con otra línea para
        # las mismas solicitudes; el servidor se queda con el mejor puntaje, así
        # que reintentar nunca puede perder puntos.
        if not response.get("blocked"):
            break  # se completaron todas las solicitudes
        plain = False

    # **Una sesión a medio jugar vale 0.** El servidor sólo asigna `score` cuando
    # la partida termina, así que salir del bucle con la partida viva —por
    # agotar el presupuesto de tiempo o de jugadas— tira a la basura todo lo
    # conseguido. Pasó de verdad: id 26 quedó en 0 después de haber sacado 86.
    # Terminarla es barato (un BFS y una jugada por ronda), así que se hace
    # siempre, aunque el reloj ya se haya pasado.
    if session.alive and (session.last_response or {}).get("status") == "in_progress":
        finished = _finish_greedily(session)
        if finished is not None:
            best_completed = max(best_completed, finished.get("completed_requests", 0))
            if finished.get("score") is not None:
                best_score = max(best_score or 0, finished["score"])

    result.observed_requests = [list(request) for request in known]
    result.completed_requests = best_completed
    result.score = best_score
    result.elapsed = time.monotonic() - started
    return result


def _finish_greedily(session: _Session) -> dict | None:
    """Cierra una partida viva jugando el camino más corto en cada ronda.

    No busca calidad, sino que la sesión quede **terminada**: una partida sin
    terminar no recibe puntaje del servidor, así que cualquier final es
    infinitamente mejor que ninguno.
    """
    response = session.last_response
    while response is not None and response.get("status") == "in_progress":
        visible = response.get("requests") or []
        if not visible:
            break
        board = Board.from_string(response["board"])
        connectors = board.find_connectors()
        first, second = visible[0][0], visible[0][1]
        if first not in connectors or second not in connectors:
            break
        connection_id = response.get("completed_requests", 0) + 1
        path = find_any_path(board, connectors[first], connectors[second], connection_id)
        if path is None:
            break  # el servidor cerrará la partida por bloqueo en la próxima jugada
        board.mark_path(path, connection_id)
        try:
            response = session.game.move(board.to_string())
        except Exception:  # noqa: BLE001
            return session.last_response
        session.last_response = response
        if not response.get("valid"):
            break
    return response
