"""Banco de jugadores con distintas estrategias, para compararlos entre sí.

Todas las estrategias comparten la misma firma, así que `game_runner.play_game`
puede correr cualquiera y `benchmark.py` puede medirlas en igualdad de
condiciones. La idea es quedarse con las que mejor rinden y descartar el resto.

Aprendizaje ya medido: la diversidad *entre estrategias* rinde (fue lo que cruzó
los umbrales de los torneos), mientras que reaeatorizar una misma estrategia con
semillas distintas no cambia casi nada.
"""

from __future__ import annotations

import heapq
import random
from collections import deque

from .board import Board
from .pathfinder import (
    PathfinderConfig,
    choose_path,
    strip_self_touches,
)
from .rules import Coord, find_any_path


def manhattan(origin: Coord, target: Coord) -> int:
    return abs(origin[0] - target[0]) + abs(origin[1] - target[1])


def remove_loops(path: list[Coord]) -> list[Coord]:
    """Corta los ciclos, dejando la primera visita de cada celda.

    Hace falta al empalmar dos búsquedas: los frentes pueden compartir celdas y
    el empalme quedaría con celdas repetidas.
    """
    result: list[Coord] = []
    seen: dict[Coord, int] = {}
    for coord in path:
        previous = seen.get(coord)
        if previous is not None:
            result = result[: previous + 1]
            seen = {cell: index for index, cell in enumerate(result)}
        else:
            seen[coord] = len(result)
            result.append(coord)
    return result


def _usable(board: Board, coord: Coord, connection_id: int) -> bool:
    cell = board.cell(*coord)
    return cell.capacity > 0 and cell.is_free_for(connection_id)


# --------------------------------------------------------------- estrategias


def bidirectional_path(
    board: Board, start: Coord, end: Coord, connection_id: int
) -> list[Coord] | None:
    """Doble mapeo: A* guiado por Manhattan desde el inicio, BFS desde el final.

    Ambos frentes avanzan alternadamente y el camino se arma en el punto donde
    se encuentran. El frente delantero tira en línea recta hacia el destino
    mientras el trasero se expande de forma uniforme, así que el punto de
    encuentro queda sesgado hacia el final: el camino resultante difiere del BFS
    puro aunque tenga un largo parecido. Esa diferencia es el aporte real de esta
    estrategia, no la velocidad.
    """
    forward_from: dict[Coord, Coord | None] = {start: None}
    backward_from: dict[Coord, Coord | None] = {end: None}
    forward_heap: list[tuple[int, int, Coord]] = [(manhattan(start, end), 0, start)]
    backward_queue: deque[Coord] = deque([end])
    meeting: Coord | None = None

    while meeting is None and (forward_heap or backward_queue):
        if forward_heap:
            _, travelled, current = heapq.heappop(forward_heap)
            for neighbor in board.neighbors(*current):
                if neighbor in forward_from:
                    continue
                if neighbor != end and not _usable(board, neighbor, connection_id):
                    continue
                forward_from[neighbor] = current
                if neighbor in backward_from:
                    meeting = neighbor
                    break
                heapq.heappush(
                    forward_heap,
                    (travelled + 1 + manhattan(neighbor, end), travelled + 1, neighbor),
                )
        if meeting is not None:
            break

        if backward_queue:
            current = backward_queue.popleft()
            for neighbor in board.neighbors(*current):
                if neighbor in backward_from:
                    continue
                if neighbor != start and not _usable(board, neighbor, connection_id):
                    continue
                backward_from[neighbor] = current
                if neighbor in forward_from:
                    meeting = neighbor
                    break
                backward_queue.append(neighbor)

    if meeting is None:
        return None

    path: list[Coord] = []
    node: Coord | None = meeting
    while node is not None:
        path.append(node)
        node = forward_from[node]
    path.reverse()

    node = backward_from[meeting]
    while node is not None:
        path.append(node)
        node = backward_from[node]

    path = strip_self_touches(board, remove_loops(path))
    if path[0] != start or path[-1] != end:
        return None
    return path


def distances_to(
    board: Board, target: Coord, connection_id: int, source: Coord
) -> dict[Coord, int]:
    """Distancia mínima de cada celda transitable hasta `target` (BFS)."""
    distance = {target: 0}
    queue: deque[Coord] = deque([target])
    while queue:
        current = queue.popleft()
        for neighbor in board.neighbors(*current):
            if neighbor in distance:
                continue
            if neighbor != source and not _usable(board, neighbor, connection_id):
                continue
            distance[neighbor] = distance[current] + 1
            if neighbor != source:
                # No se expande a través de los conectores: no son transitables.
                queue.append(neighbor)
    return distance


def cheapest_shortest_path(
    board: Board,
    start: Coord,
    end: Coord,
    connection_id: int,
    cell_cost,
) -> list[Coord] | None:
    """De entre TODOS los caminos de largo mínimo, el de menor costo acumulado.

    Los caminos mínimos forman un DAG (cada paso baja en uno la distancia al
    destino), así que se puede encontrar el óptimo por programación dinámica en
    una sola pasada, sin muestrear ni arriesgar rodeos.

    Esto arregla el problema medido de la penalización por conectores: aplicada
    sobre todo el tablero provocaba caminos larguísimos que actuaban como muro,
    pero restringida a este DAG sólo puede desempatar entre caminos igual de
    cortos. Además, un camino de largo mínimo nunca puede tocarse a sí mismo
    (una adyacencia entre celdas no consecutivas implicaría un atajo), así que
    siempre es válido para el servidor.
    """
    distance = distances_to(board, end, connection_id, start)
    if start not in distance:
        return None

    by_distance: dict[int, list[Coord]] = {}
    for coord, value in distance.items():
        by_distance.setdefault(value, []).append(coord)

    best: dict[Coord, float] = {end: 0.0}
    next_step: dict[Coord, Coord] = {}

    for level in range(1, max(by_distance) + 1):
        for coord in by_distance.get(level, []):
            options = [
                (best[neighbor], neighbor)
                for neighbor in board.neighbors(*coord)
                if distance.get(neighbor) == level - 1 and neighbor in best
            ]
            if not options:
                continue
            cost, chosen = min(options)
            best[coord] = cost + (0.0 if coord == start else cell_cost(coord))
            next_step[coord] = chosen

    if start != end and start not in next_step:
        return None

    path = [start]
    while path[-1] != end:
        path.append(next_step[path[-1]])
    return path


DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def min_turns_shortest_path(
    board: Board, start: Coord, end: Coord, connection_id: int
) -> list[Coord] | None:
    """De entre los caminos de largo mínimo, el que dobla menos veces.

    Un camino con pocos quiebres es casi una recta o una "L": corta el tablero
    limpiamente en dos mitades grandes en vez de dejar recortes irregulares. Es
    la hipótesis opuesta a la de `shortest_hug`, y como el largo es el mismo, la
    comparación entre ambas aísla exactamente el efecto de la *forma*.

    El costo de doblar depende de por dónde se entró a la celda, así que la
    programación dinámica va sobre pares (celda, dirección de salida) en vez de
    sobre celdas sueltas.
    """
    distance = distances_to(board, end, connection_id, start)
    if start not in distance:
        return None
    if distance[start] == 0:
        return [start]

    by_distance: dict[int, list[Coord]] = {}
    for coord, value in distance.items():
        by_distance.setdefault(value, []).append(coord)

    best: dict[tuple[Coord, tuple[int, int]], int] = {}
    follow: dict[tuple[Coord, tuple[int, int]], tuple[int, int] | None] = {}

    for level in range(1, max(by_distance) + 1):
        for coord in by_distance.get(level, []):
            for direction in DIRECTIONS:
                ahead = (coord[0] + direction[0], coord[1] + direction[1])
                if distance.get(ahead) != level - 1:
                    continue
                if level == 1:
                    best[(coord, direction)] = 0
                    follow[(coord, direction)] = None
                    continue
                options = [
                    (best[(ahead, nxt)] + (0 if nxt == direction else 1), nxt)
                    for nxt in DIRECTIONS
                    if (ahead, nxt) in best
                ]
                if not options:
                    continue
                turns, chosen = min(options)
                best[(coord, direction)] = turns
                follow[(coord, direction)] = chosen

    starts = [(best[(start, d)], d) for d in DIRECTIONS if (start, d) in best]
    if not starts:
        return None

    _, direction = min(starts)
    path = [start]
    coord = start
    while coord != end:
        ahead = (coord[0] + direction[0], coord[1] + direction[1])
        path.append(ahead)
        following = follow[(coord, direction)]
        coord = ahead
        if following is None:
            break
        direction = following
    return path


def _connector_pressure(board: Board, future_connectors: list[Coord]):
    """Costo por celda: cuánto estorba a los conectores que aún faltan por usar."""
    from .pathfinder import free_neighbors

    access = {coord: len(free_neighbors(board, coord)) for coord in future_connectors}

    def cost(coord: Coord) -> float:
        value = 0.0
        for neighbor in board.neighbors(*coord):
            remaining = access.get(neighbor)
            if remaining is not None:
                value += 1.0 / max(remaining - 1, 0.5)
        return value

    return cost


def strategy_shortest_safe(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Camino de largo mínimo que menos estorba a los conectores pendientes."""
    return cheapest_shortest_path(
        board,
        start,
        end,
        connection_id,
        _connector_pressure(board, future_connectors),
    )


def strategy_shortest_open(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Camino de largo mínimo que se mantiene en zonas despejadas.

    Prefiere celdas con muchos vecinos libres, con la idea de que atravesar un
    pasillo estrecho lo tapa por completo mientras que cruzar una zona abierta
    deja alternativas. Es una variante de diversidad frente a `shortest_safe`.
    """
    from .pathfinder import free_neighbors

    def cost(coord: Coord) -> float:
        return float(4 - len(free_neighbors(board, coord)))

    return cheapest_shortest_path(board, start, end, connection_id, cost)


def strategy_shortest_hug(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Camino de largo mínimo pegado a los bordes y a lo ya ocupado.

    Es la hipótesis inversa a `shortest_open`: una celda con pocos vecinos libres
    ya está contra un muro, así que ocuparla casi no resta espacio maniobrable;
    en cambio cruzar por el medio de una zona abierta la parte en dos. Es la
    heurística que usa una persona jugando Numberlink a mano.
    """
    from .pathfinder import free_neighbors

    def cost(coord: Coord) -> float:
        return float(len(free_neighbors(board, coord)))

    return cheapest_shortest_path(board, start, end, connection_id, cost)


def strategy_shortest_turns(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    return min_turns_shortest_path(board, start, end, connection_id)


def _minimal_candidates(
    board: Board, start: Coord, end: Coord, connection_id: int, future_connectors
) -> list[list[Coord]]:
    """Todos los caminos de largo mínimo que sabemos generar, sin repetidos."""
    from .pathfinder import free_neighbors

    builders = [
        lambda: find_any_path(board, start, end, connection_id),
        lambda: cheapest_shortest_path(
            board, start, end, connection_id, _connector_pressure(board, future_connectors)
        ),
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
        lambda: min_turns_shortest_path(board, start, end, connection_id),
        lambda: bidirectional_path(board, start, end, connection_id),
    ]

    candidates: list[list[Coord]] = []
    seen: set[tuple[Coord, ...]] = set()
    for build in builders:
        path = build()
        if path is None:
            continue
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    return candidates


def _damage(
    board: Board, path: list[Coord], connection_id: int, future_connectors: list[Coord]
) -> tuple:
    """Daño que el camino le hace al tablero. Menor es mejor (orden lexicográfico).

    Se mide sobre el tablero resultante de verdad, no con un proxy por celda:
    cuántas solicitudes futuras siguen siendo posibles (dos conectores sólo
    pueden unirse si comparten componente libre), cuántos conectores quedan
    ahogados, y recién al final el largo.

    A propósito no hay pesos que ajustar: el orden lexicográfico evita el
    problema medido en §5 del estado del proyecto, donde ponderar la
    satisfacibilidad con un peso grande hacía aceptar rodeos larguísimos. Aquí
    los candidatos ya son todos de largo mínimo, así que comparar por daño no
    puede pagar el precio de un camino más largo.
    """
    from .pathfinder import _components, free_neighbors

    trial = board.clone()
    try:
        trial.mark_path(path, connection_id)
    except ValueError:
        return (float("inf"),)

    component_of, sizes = _components(trial)
    reachable = [0] * len(sizes)
    choked = 0
    for coord in future_connectors:
        free = free_neighbors(trial, coord)
        if len(free) <= 1:
            choked += 1
        touched = {component_of[n] for n in free if n in component_of}
        for index in touched:
            reachable[index] += 1

    satisfiable = sum(count // 2 for count in reachable)
    return (-satisfiable, choked, len(path))


def strategy_fragmentation(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Genera todos los caminos mínimos conocidos y elige el que menos fragmenta.

    Es un cambio de principio de selección, no de búsqueda: en vez de aproximar
    el daño con un costo por celda (lo que hacen `shortest_safe`/`_open`/`_hug`),
    construye cada tablero resultante y lo mide. Como todos los candidatos tienen
    el mismo largo, elegir entre ellos es gratis en espacio.
    """
    candidates = _minimal_candidates(
        board, start, end, connection_id, future_connectors
    )
    if not candidates:
        return None
    return min(
        candidates, key=lambda path: _damage(board, path, connection_id, future_connectors)
    )


def _bfs(
    board: Board,
    start: Coord,
    goal: Coord,
    connection_id: int,
    banned: frozenset = frozenset(),
) -> list[Coord] | None:
    """BFS entre dos celdas cualesquiera (no sólo conectores), evitando `banned`."""
    previous: dict[Coord, Coord | None] = {start: None}
    queue: deque[Coord] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in board.neighbors(*current):
            if neighbor in previous or neighbor in banned:
                continue
            cell = board.cell(*neighbor)
            if neighbor != goal and (
                cell.capacity == 0 or not cell.is_free_for(connection_id)
            ):
                continue
            previous[neighbor] = current
            if neighbor == goal:
                path: list[Coord] = []
                node: Coord | None = neighbor
                while node is not None:
                    path.append(node)
                    node = previous[node]
                return path[::-1]
            queue.append(neighbor)
    return None


def path_via(
    board: Board, start: Coord, waypoint: Coord, end: Coord, connection_id: int
) -> list[Coord] | None:
    """Camino start -> waypoint -> end, obligado a pasar por el punto intermedio."""
    if waypoint in (start, end):
        return _bfs(board, start, end, connection_id)
    first = _bfs(board, start, waypoint, connection_id)
    if first is None:
        return None
    second = _bfs(
        board, waypoint, end, connection_id, banned=frozenset(first[:-1])
    )
    if second is None:
        return None
    return strip_self_touches(board, first + second[1:])


def waypoint_candidates(
    board: Board,
    start: Coord,
    end: Coord,
    connection_id: int,
    samples: int = 48,
    slack: float = 1.5,
) -> list[list[Coord]]:
    """Caminos estructuralmente distintos, forzados a pasar por puntos intermedios.

    Reemplaza la diversificación por ruido, que quedó medida como inútil: en id 14
    ronda 1, 122 intentos con ruido produjeron **2** caminos distintos, mientras
    que los waypoints produjeron 16, de 7 a 21 pasos.

    Se recortan los caminos que exceden `slack` veces el mínimo, porque ya está
    medido que en tableros densos un camino largo actúa como muro y cuesta más
    que el riesgo que evita.
    """
    from .pathfinder import is_usable

    shortest = _bfs(board, start, end, connection_id)
    if shortest is None:
        return []
    limit = int(len(shortest) * slack)

    usable = [
        (row, col)
        for row in range(board.rows)
        for col in range(board.cols)
        if is_usable(board.cell(row, col))
    ]
    # Muestreo por zancada en vez de aleatorio: cubre el tablero de forma pareja
    # y hace la estrategia determinista, que es lo que permite compararla.
    stride = max(1, len(usable) // samples)
    chosen = usable[::stride]

    candidates = [shortest]
    seen = {tuple(shortest)}
    for waypoint in chosen:
        path = path_via(board, start, waypoint, end, connection_id)
        if path is None or len(path) > limit:
            continue
        if path[0] != start or path[-1] != end:
            continue
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    return candidates


def strategy_waypoints(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Diversidad estructural real: muchos caminos distintos, se elige por daño.

    Es la única estrategia del banco que explora caminos que *no* son de largo
    mínimo, y por eso puede encontrar rodeos que un BFS jamás propondría — por
    ejemplo bordear una zona en vez de partirla en dos. El recorte por `slack`
    la protege del modo de falla ya medido (§5 del estado del proyecto).
    """
    candidates = waypoint_candidates(board, start, end, connection_id)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda path: _damage(board, path, connection_id, future_connectors),
    )


def strategy_bidirectional(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    return bidirectional_path(board, start, end, connection_id)


def strategy_shortest(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Línea base: BFS puro, sin mirar el futuro. Sirve como piso de comparación."""
    return find_any_path(board, start, end, connection_id)


def _candidates(overrides: dict):
    """Estrategia de candidatos evaluados (`pathfinder.choose_path`) con pesos fijos."""

    def strategy(
        board, start, end, connection_id, future_connectors, config, rng, known_future
    ):
        return choose_path(
            board,
            start,
            end,
            connection_id,
            future_connectors,
            PathfinderConfig(**overrides),
            rng,
            known_future,
        )

    return strategy


def strategy_hybrid(
    board, start, end, connection_id, future_connectors, config, rng, known_future
):
    """Mezcla: agrega el camino bidireccional al conjunto de candidatos evaluados.

    Prueba si el camino de doble mapeo aporta algo que los candidatos de Dijkstra
    no estaban generando, usando el mismo criterio de evaluación para elegir.
    """
    from .pathfinder import evaluate_path

    config = config or PathfinderConfig()
    options = []

    evaluated = choose_path(
        board, start, end, connection_id, future_connectors, config, rng, known_future
    )
    if evaluated is not None:
        options.append(evaluated)

    crossing = bidirectional_path(board, start, end, connection_id)
    if crossing is not None:
        options.append(crossing)

    if not options:
        return None
    return min(
        options,
        key=lambda path: evaluate_path(
            board, path, connection_id, future_connectors, config, known_future
        ),
    )


# Banco activo: sólo las estrategias que ganan alguna partida **en exclusiva**.
# El servidor guarda el mejor puntaje por partida, así que una estrategia que
# nunca es la mejor en ninguna no aporta un solo punto por más que su total
# propio sea alto. Juntas rinden 1718 de 3000 sobre las 16 partidas.
STRATEGIES = {
    # Mejor estrategia individual medida (1582) y la mejor en id 25.
    "shortest": strategy_shortest,
    # 1562, segunda mejor individual. Mejor en id 15 (162, récord del proyecto).
    "shortest_turns": strategy_shortest_turns,
    # 1444, pero es la mejor en id 17 (225, récord del proyecto).
    "shortest_safe": strategy_shortest_safe,
    # 1512, mejor en id 18. Su aporte es la forma distinta del camino.
    "bidirectional": strategy_bidirectional,
    # 1373, la más floja del banco, pero la única que saca 100 en id 12.
    "evasivo": _candidates({"connector_penalty": 6.0, "risky_connector_weight": 25.0}),
}

# Descartadas: implementadas, medidas contra el servidor real y sin ninguna
# partida propia. Se dejan en el módulo porque sus resultados son la evidencia
# de la ley que guía el diseño ("el largo le gana a cualquier métrica de daño"),
# y porque su maquinaria sirve para diagnosticar tableros.
#
#   shortest_open   1376  preferir zonas despejadas
#   shortest_hug    1371  preferir pegarse a los muros (hipótesis inversa)
#   fragmentacion   1413  mide el tablero resultante en vez de aproximarlo
#   waypoints       1144  diversidad estructural real + rodeos hasta 1.5x
#   corto/cortisimo/hibrido  1492/1540/1492  candidatos evaluados de pathfinder
DISCARDED = {
    "shortest_open": strategy_shortest_open,
    "shortest_hug": strategy_shortest_hug,
    "fragmentacion": strategy_fragmentation,
    "waypoints": strategy_waypoints,
}
