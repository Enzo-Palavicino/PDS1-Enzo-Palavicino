"""Elección del camino a construir en cada ronda.

Este módulo genera varios caminos candidatos con distintos sesgos y elige el que
menos daña las rondas futuras, medido sobre el tablero resultante.

**Advertencia medida (2026-08-10):** la premisa original de este módulo era que
`request_count == connector_count / 2` y que por lo tanto todo conector termina
siendo solicitado, de modo que dejar uno sin acceso equivale a perder una ronda.
Eso es **falso** en 12 de las 16 partidas: en id 18 sobran 40 conectores de 160
que nunca se piden. Como no hay forma de saber cuáles, proteger a todos por igual
es demasiado conservador, y por eso todas las heurísticas de este módulo miden
*peor* que un BFS pelado (`strategies.strategy_shortest`, 1582 contra 1492).
El módulo se conserva porque su configuración "evasivo" gana id 12 en exclusiva,
pero no es el camino a seguir. Ver `ESTADO_PROYECTO.md` §5.
"""

from __future__ import annotations

import heapq
import random
import time
from collections import deque
from dataclasses import dataclass

from .board import Board, Cell
from .rules import Coord

INFEASIBLE = float("inf")


@dataclass
class PathfinderConfig:
    """Pesos de la búsqueda. Pensados para ajustarse midiendo contra partidas reales."""

    # Costo de atravesar una celda cualquiera.
    base_cost: float = 1.0
    # Penalización por usar una celda vecina a un conector aún no solicitado.
    # Medido contra el servidor real: con 6.0 el camino serpentea para esquivar
    # conectores y termina 14 pasos más largo de lo necesario, lo que en un
    # tablero denso actúa como muro y parte el espacio. Bajarla a 1.0 subió la
    # media de solicitudes completadas de 20% a 27% (id 17: 11/40 -> 24/40).
    connector_penalty: float = 1.0
    # Ruido relativo para diversificar candidatos entre repeticiones.
    noise: float = 0.6
    # Peso de cada término al evaluar el tablero resultante.
    length_weight: float = 1.0
    # Mismo motivo: castigar de más los conectores con un solo acceso hacía
    # aceptar caminos mucho más largos, y el espacio gastado cuesta más que el
    # riesgo evitado.
    risky_connector_weight: float = 2.0
    # Castigo por conector que queda fuera de la componente libre más grande.
    isolated_connector_weight: float = 400.0
    # Premio por solicitud futura que sigue siendo satisfacible (cota por
    # componente). Medido contra el servidor real: subirlo a 400 con
    # `isolated_connector_weight` en 0 empeoró el total (1410 -> 1370), porque
    # acepta caminos larguísimos con tal de no partir componentes y ese derroche
    # de espacio se paga en las rondas siguientes. Queda en 0 por defecto y es
    # el primer parámetro a barrer al afinar.
    satisfiable_pair_weight: float = 0.0
    # Castigo por cada solicitud futura *conocida* que quedaría sin camino.
    # Domina al resto porque es información dura, no una heurística.
    known_request_weight: float = 1500.0
    # Cantidad de candidatos aleatorizados, además de los deterministas.
    random_candidates: int = 24
    # Tope de tiempo por jugada, para respetar los 10 min por lote de 10 partidas.
    time_budget: float = 1.0


def is_usable(cell: Cell) -> bool:
    """¿Puede una conexión futura pasar por esta celda?"""
    return cell.capacity > len(cell.occupants)


def free_neighbors(board: Board, coord: Coord) -> list[Coord]:
    return [
        neighbor
        for neighbor in board.neighbors(*coord)
        if is_usable(board.cell(*neighbor))
    ]


def strip_self_touches(board: Board, path: list[Coord]) -> list[Coord]:
    """Elimina los tramos que hacen que el camino se toque a sí mismo.

    El servidor rechaza caminos donde dos celdas no consecutivas son vecinas
    ortogonales. Pero esa adyacencia es justamente un atajo: si `path[i]` es
    vecino de `path[j]` con j > i+1, todo el tramo intermedio sobra y puede
    cortarse. Repetir el corte siempre termina, porque el camino se acorta.
    """
    result = list(path)
    changed = True
    while changed:
        changed = False
        index = {coord: position for position, coord in enumerate(result)}
        for position, coord in enumerate(result):
            for neighbor in board.neighbors(*coord):
                other = index.get(neighbor)
                if other is None or other <= position + 1:
                    continue
                result = result[: position + 1] + result[other:]
                changed = True
                break
            if changed:
                break
    return result


def weighted_path(
    board: Board,
    start: Coord,
    end: Coord,
    connection_id: int,
    cell_cost,
) -> list[Coord] | None:
    """Dijkstra entre dos conectores, con costo por celda."""
    distances: dict[Coord, float] = {start: 0.0}
    came_from: dict[Coord, Coord | None] = {start: None}
    heap: list[tuple[float, Coord]] = [(0.0, start)]

    while heap:
        distance, current = heapq.heappop(heap)
        if current == end:
            path: list[Coord] = []
            node: Coord | None = current
            while node is not None:
                path.append(node)
                node = came_from[node]
            return path[::-1]
        if distance > distances.get(current, INFEASIBLE):
            continue

        for neighbor in board.neighbors(*current):
            if neighbor == end:
                candidate = distance  # llegar al conector destino no cuesta
            else:
                cell = board.cell(*neighbor)
                if cell.capacity == 0 or not cell.is_free_for(connection_id):
                    continue
                candidate = distance + cell_cost(neighbor)
            if candidate < distances.get(neighbor, INFEASIBLE):
                distances[neighbor] = candidate
                came_from[neighbor] = current
                heapq.heappush(heap, (candidate, neighbor))

    return None


def _make_cost(
    board: Board,
    protected: dict[Coord, int],
    config: PathfinderConfig,
    connector_penalty: float,
    rng: random.Random | None,
):
    """Costo por celda: castiga acercarse a conectores que aún faltan por usar.

    La penalización crece a medida que al conector le quedan menos accesos, así
    que gastar el último vecino libre de un conector resulta carísimo.
    """

    def cost(coord: Coord) -> float:
        value = config.base_cost
        for neighbor in board.neighbors(*coord):
            remaining = protected.get(neighbor)
            if remaining is not None:
                value += connector_penalty / max(remaining - 1, 0.5)
        if rng is not None and config.noise:
            value *= 1.0 + rng.uniform(0.0, config.noise)
        return value

    return cost


def _components(board: Board) -> tuple[dict[Coord, int], list[int]]:
    """Componentes conexas del espacio todavía transitable."""
    component_of: dict[Coord, int] = {}
    sizes: list[int] = []

    for row in range(board.rows):
        for col in range(board.cols):
            origin = (row, col)
            if origin in component_of or not is_usable(board.cell(row, col)):
                continue
            index = len(sizes)
            size = 0
            queue = deque([origin])
            component_of[origin] = index
            while queue:
                current = queue.popleft()
                size += 1
                for neighbor in board.neighbors(*current):
                    if neighbor in component_of or not is_usable(board.cell(*neighbor)):
                        continue
                    component_of[neighbor] = index
                    queue.append(neighbor)
            sizes.append(size)

    return component_of, sizes


def evaluate_path(
    board: Board,
    path: list[Coord],
    connection_id: int,
    protected_connectors: list[Coord],
    config: PathfinderConfig,
    known_future_requests: list[tuple[Coord, Coord]] | None = None,
) -> float:
    """Puntúa el tablero que quedaría tras construir `path`. Menor es mejor."""
    trial = board.clone()
    try:
        trial.mark_path(path, connection_id)
    except ValueError:
        return INFEASIBLE

    access = {coord: len(free_neighbors(trial, coord)) for coord in protected_connectors}
    if any(count == 0 for count in access.values()):
        # Un conector sin accesos ya no podrá conectarse nunca: descartar.
        return INFEASIBLE

    # Cuántas solicitudes futuras siguen siendo posibles. Dos conectores solo
    # pueden unirse si comparten una componente del espacio libre, así que cada
    # componente aporta como mucho floor(conectores / 2) conexiones. Maximizar
    # esa cota discrimina mucho mejor que contar conectores aislados: estos
    # tableros ya vienen fragmentados por sus propios conectores, así que
    # "quedar fuera de la componente mayor" es casi una constante.
    component_of, sizes = _components(trial)
    reachable_connectors = [0] * len(sizes)
    for coord in protected_connectors:
        touched = {
            component_of[neighbor]
            for neighbor in free_neighbors(trial, coord)
            if neighbor in component_of
        }
        for index in touched:
            reachable_connectors[index] += 1

    # Si conocemos las solicitudes que vienen (ver `knowledge.py`), podemos medir
    # exactamente lo que importa: cuáles de ellas siguen siendo conectables. Es
    # mucho más preciso que cualquier heurística sobre conectores sueltos. Las
    # más próximas pesan más, porque quedarse sin camino en la ronda siguiente
    # termina la partida de inmediato, mientras que una solicitud lejana quizá
    # nunca se alcance.
    doomed = 0.0
    for position, (first, second) in enumerate(known_future_requests or []):
        touched_first = {
            component_of[neighbor]
            for neighbor in free_neighbors(trial, first)
            if neighbor in component_of
        }
        touched_second = {
            component_of[neighbor]
            for neighbor in free_neighbors(trial, second)
            if neighbor in component_of
        }
        if not (touched_first & touched_second):
            doomed += 1.0 / (position + 1)

    satisfiable = sum(count // 2 for count in reachable_connectors)
    largest = max(range(len(sizes)), key=lambda index: sizes[index]) if sizes else None
    isolated = 0
    if largest is not None:
        for coord in protected_connectors:
            touched = {
                component_of[neighbor]
                for neighbor in free_neighbors(trial, coord)
                if neighbor in component_of
            }
            if largest not in touched:
                isolated += 1

    risky = sum(1 for count in access.values() if count == 1)

    return (
        config.length_weight * len(path)
        + config.risky_connector_weight * risky
        + config.isolated_connector_weight * isolated
        + config.known_request_weight * doomed
        - config.satisfiable_pair_weight * satisfiable
    )


def choose_path(
    board: Board,
    start: Coord,
    end: Coord,
    connection_id: int,
    future_connectors: list[Coord],
    config: PathfinderConfig | None = None,
    rng: random.Random | None = None,
    known_future_requests: list[tuple[Coord, Coord]] | None = None,
) -> list[Coord] | None:
    """Elige el mejor camino entre dos conectores para sobrevivir más rondas.

    `future_connectors` son las posiciones de los conectores que todavía no se
    han usado, excluyendo los dos de la solicitud actual. Devuelve None si no
    existe ningún camino válido.
    """
    config = config or PathfinderConfig()
    rng = rng or random.Random(0)
    deadline = time.monotonic() + config.time_budget

    protected = {
        coord: len(free_neighbors(board, coord)) for coord in future_connectors
    }

    # Candidatos deterministas: desde ignorar por completo a los otros conectores
    # (camino más corto) hasta evitarlos agresivamente.
    penalties = [0.0, config.connector_penalty, config.connector_penalty * 3]
    attempts: list[tuple[float, random.Random | None]] = [
        (penalty, None) for penalty in penalties
    ]
    attempts += [(config.connector_penalty, rng)] * config.random_candidates

    best_path: list[Coord] | None = None
    best_score = INFEASIBLE
    fallback: list[Coord] | None = None

    for penalty, noise_source in attempts:
        if best_path is not None and time.monotonic() > deadline:
            break

        cost = _make_cost(board, protected, config, penalty, noise_source)
        path = weighted_path(board, start, end, connection_id, cost)
        if path is None:
            break  # sin penalizaciones tampoco lo habría: los conectores no se alcanzan

        path = strip_self_touches(board, path)
        if fallback is None or len(path) < len(fallback):
            fallback = path

        score = evaluate_path(
            board,
            path,
            connection_id,
            future_connectors,
            config,
            known_future_requests,
        )
        if score < best_score:
            best_score = score
            best_path = path

    # Si todo candidato deja algún conector aislado, igual hay que jugar: se
    # entrega el camino más corto encontrado antes que perder la ronda.
    return best_path if best_path is not None else fallback
