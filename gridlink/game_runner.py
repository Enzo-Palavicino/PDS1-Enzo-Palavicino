"""Ciclo de juego de una partida completa.

Funciona indistintamente con `simulator.GameSimulator` y con
`api_client.RemoteGame`, porque ambos exponen `start`/`move`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .board import Board
from .pathfinder import PathfinderConfig, choose_path
from .rules import Coord


@dataclass
class GameResult:
    game_id: int
    completed_requests: int = 0
    total_requests: int = 0
    score: int | None = None
    elapsed: float = 0.0
    error: str | None = None
    rounds: list[dict] = field(default_factory=list)
    observed_requests: list[list[int]] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.error is not None


def _pick_request(
    board: Board,
    requests: list[list[int]],
    connectors: dict[int, Coord],
    used: set[int],
    connection_id: int,
    config: PathfinderConfig,
    rng: random.Random,
    known_future: list[tuple[Coord, Coord]] | None = None,
    strategy=None,
) -> tuple[list[int], list[Coord]] | None:
    """Elige qué solicitud resolver y con qué camino.

    Con ventana de tamaño 1 no hay elección de solicitud, pero con ventanas de 2
    o 3 conviene resolver primero la que menos daña el tablero, así que se evalúa
    cada opción y se queda la de mejor camino.
    """
    best: tuple[float, list[int], list[Coord]] | None = None

    for request in requests:
        start, end = connectors[request[0]], connectors[request[1]]
        future = [
            position
            for connector_id, position in connectors.items()
            if connector_id not in used | {request[0], request[1]}
        ]
        if strategy is None:
            path = choose_path(
                board, start, end, connection_id, future, config, rng, known_future
            )
        else:
            path = strategy(
                board, start, end, connection_id, future, config, rng, known_future
            )
        if path is None:
            continue
        # Preferimos la solicitud que se resuelve con el camino más corto: gasta
        # menos espacio y deja más margen para las rondas siguientes.
        if best is None or len(path) < best[0]:
            best = (len(path), request, path)

    if best is None:
        return None
    return best[1], best[2]


def play_game(
    game,
    game_id: int = 0,
    config: PathfinderConfig | None = None,
    seed: int = 0,
    verbose: bool = False,
    known_sequence: list[list[int]] | None = None,
    strategy=None,
) -> GameResult:
    """Juega una partida de principio a fin y devuelve el resultado."""
    config = config or PathfinderConfig()
    rng = random.Random(seed)
    result = GameResult(game_id=game_id)
    started = time.monotonic()

    try:
        payload = game.start()
    except Exception as error:  # noqa: BLE001 - queremos registrar cualquier fallo
        result.error = f"start falló: {error}"
        result.elapsed = time.monotonic() - started
        return result

    result.total_requests = payload.get("params", {}).get("request_count", 0)
    board_str = payload["board"]
    requests = payload["requests"]
    used: set[int] = set()
    completed = 0

    while True:
        connection_id = completed + 1
        board = Board.from_string(board_str)
        connectors = board.find_connectors()

        for visible in requests:
            if list(visible) not in result.observed_requests:
                result.observed_requests.append(list(visible))

        # Las solicitudes que ya sabemos que vienen después de la actual,
        # traducidas a posiciones de conectores.
        known_future: list[tuple[Coord, Coord]] = []
        for upcoming in (known_sequence or [])[completed + 1 :]:
            if upcoming[0] in connectors and upcoming[1] in connectors:
                known_future.append((connectors[upcoming[0]], connectors[upcoming[1]]))

        round_started = time.monotonic()
        choice = _pick_request(
            board,
            requests,
            connectors,
            used,
            connection_id,
            config,
            rng,
            known_future,
            strategy,
        )
        if choice is None:
            # Ninguna solicitud visible tiene camino: la partida está bloqueada.
            break
        request, path = choice
        think_time = time.monotonic() - round_started

        board.mark_path(path, connection_id)
        try:
            response = game.move(board.to_string())
        except Exception as error:  # noqa: BLE001
            result.error = f"move falló en la ronda {connection_id}: {error}"
            break

        if not response.get("valid"):
            result.error = f"jugada inválida en la ronda {connection_id}: {response.get('error')}"
            break

        used |= {request[0], request[1]}
        completed = response["completed_requests"]
        result.rounds.append(
            {
                "round": connection_id,
                "request": list(request),
                "path_length": len(path),
                "think_time": round(think_time, 3),
            }
        )
        if verbose:
            print(
                f"  ronda {connection_id}: {request} camino={len(path)} "
                f"({think_time:.2f}s)"
            )

        if response.get("status") != "in_progress":
            result.score = response.get("score")
            break

        board_str = response["board"]
        requests = response["requests"]

    result.completed_requests = completed
    result.elapsed = time.monotonic() - started
    return result
