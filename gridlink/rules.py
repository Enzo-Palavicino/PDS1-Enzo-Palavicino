"""Reglas de GridLink compartidas entre el simulador y el validador del jugador.

Este módulo es la única fuente de verdad sobre qué constituye una jugada legal.
Tanto `simulator.py` (que hace de servidor local) como `move_validator.py` (que
revisa nuestras jugadas antes de enviarlas) lo usan, para que no puedan
desviarse entre sí.
"""

from __future__ import annotations

from collections import deque

from .board import Board, CellKind

Coord = tuple[int, int]

# Direcciones ortogonales, usadas para identificar por qué "lado" de una celda
# entra o sale un camino (relevante para la regla de puentes).
UP = (-1, 0)
DOWN = (1, 0)
LEFT = (0, -1)
RIGHT = (0, 1)
DIRECTIONS = (UP, DOWN, LEFT, RIGHT)


class RuleViolation(Exception):
    """La jugada rompe una regla del juego."""


def direction_between(origin: Coord, target: Coord) -> tuple[int, int]:
    return (target[0] - origin[0], target[1] - origin[1])


def _kinds_compatible(previous: CellKind, submitted: CellKind) -> bool:
    """Compara tipos de celda tolerando la ambigüedad de las celdas potenciales.

    Una celda potencial ocupada se serializa como `K<n>`, indistinguible de una
    celda normal ocupada, así que al reparsear se pierde su "P". Por eso normal
    y potencial se consideran compatibles entre sí; el resto de los tipos debe
    coincidir exactamente.
    """
    interchangeable = (CellKind.NORMAL, CellKind.POTENTIAL_BRIDGE)
    if previous in interchangeable:
        return submitted in interchangeable
    return previous == submitted


def extract_added_cells(
    previous: Board, submitted: Board, connection_id: int
) -> list[Coord]:
    """Devuelve las celdas que el jugador marcó como usadas por `connection_id`.

    Sirve para modalidad Supervivencia: cualquier otro cambio en el tablero
    (mover conectores, borrar una conexión anterior, marcar celdas con otra
    conexión) es ilegal y levanta `RuleViolation`. La modalidad Menos celdas,
    donde sí se pueden rehacer conexiones previas, necesitará otra variante.
    """
    if previous.rows != submitted.rows or previous.cols != submitted.cols:
        raise RuleViolation(
            f"Las dimensiones del tablero cambiaron: "
            f"{previous.rows}x{previous.cols} -> {submitted.rows}x{submitted.cols}"
        )

    added: list[Coord] = []
    for row in range(previous.rows):
        for col in range(previous.cols):
            before = previous.cell(row, col)
            after = submitted.cell(row, col)

            if not _kinds_compatible(before.kind, after.kind):
                raise RuleViolation(
                    f"El tipo de la celda ({row},{col}) cambió: "
                    f"{before.kind.value} -> {after.kind.value}"
                )
            if before.connector_id != after.connector_id:
                raise RuleViolation(
                    f"El conector de la celda ({row},{col}) cambió: "
                    f"{before.connector_id} -> {after.connector_id}"
                )
            if before.occupants == after.occupants:
                continue
            if after.occupants == sorted([*before.occupants, connection_id]):
                added.append((row, col))
                continue
            raise RuleViolation(
                f"La celda ({row},{col}) cambió de forma no permitida: "
                f"{before.occupants} -> {after.occupants}"
            )
    return added


def check_capacity(board: Board, cells: list[Coord], connection_id: int) -> None:
    """Verifica que cada celda del camino admita la nueva conexión."""
    for row, col in cells:
        cell = board.cell(row, col)
        if cell.kind == CellKind.BLOCKED:
            raise RuleViolation(f"El camino atraviesa un bloqueo en ({row},{col})")
        if cell.kind == CellKind.CONNECTOR:
            raise RuleViolation(f"El camino atraviesa el conector en ({row},{col})")
        if not cell.is_free_for(connection_id):
            raise RuleViolation(
                f"La celda ({row},{col}) ya está ocupada por {cell.occupants}"
            )


def order_path(board: Board, cells: list[Coord], start: Coord, end: Coord) -> list[Coord]:
    """Ordena las celdas marcadas como un camino simple de `start` a `end`.

    `start` y `end` son las posiciones de los dos conectores y no forman parte
    de `cells`. Devuelve el camino completo (conectores incluidos) o levanta
    `RuleViolation` si las celdas no forman exactamente un camino, lo que cubre
    caminos discontinuos, ramificados, con ciclos o con celdas sobrantes.

    El camino además no puede tocarse a sí mismo: dos celdas no consecutivas del
    camino no pueden ser vecinas ortogonales, aunque no se repita ninguna celda.
    Verificado contra el servidor real, que rechaza esos caminos con
    "La conexión debe comenzar y terminar en sus conectores" (su reconstrucción
    del camino se vuelve ambigua). Por eso el criterio es el grado de cada celda
    dentro del propio camino, y no hace falta backtracking: si los grados son
    correctos el recorrido es único.
    """
    unique = set(cells)
    if len(unique) != len(cells):
        raise RuleViolation("El camino contiene celdas repetidas")
    if start in unique or end in unique:
        raise RuleViolation("Los conectores no pueden marcarse como parte del camino")

    nodes = unique | {start, end}
    degree = {
        node: sum(1 for neighbor in board.neighbors(*node) if neighbor in nodes)
        for node in nodes
    }

    for endpoint in (start, end):
        if degree[endpoint] != 1:
            raise RuleViolation(
                f"El conector en {endpoint} toca {degree[endpoint]} celdas del "
                "camino; debe tocar exactamente una"
            )
    for cell in unique:
        if degree[cell] != 2:
            raise RuleViolation(
                f"La celda {cell} toca {degree[cell]} celdas del camino; un "
                "camino simple exige exactamente dos (sin ramificaciones ni "
                "tramos que se toquen entre sí)"
            )

    # Con los grados verificados el recorrido es determinista: desde cada celda
    # hay exactamente un vecino sin visitar.
    path = [start]
    visited = {start}
    while path[-1] != end:
        current = path[-1]
        following = [
            neighbor
            for neighbor in board.neighbors(*current)
            if neighbor in nodes and neighbor not in visited
        ]
        if not following:
            raise RuleViolation("El camino está cortado antes de llegar al conector")
        path.append(following[0])
        visited.add(following[0])

    if len(path) != len(nodes):
        raise RuleViolation(
            "Quedaron celdas marcadas fuera del camino que une ambos conectores"
        )
    return path


def bridge_sides_used(board: Board, path: list[Coord]) -> dict[Coord, set[tuple[int, int]]]:
    """Lados por los que un camino entra y sale de cada puente que atraviesa."""
    sides: dict[Coord, set[tuple[int, int]]] = {}
    for index in range(1, len(path) - 1):
        position = path[index]
        if board.cell(*position).kind != CellKind.BRIDGE:
            continue
        sides[position] = {
            direction_between(position, path[index - 1]),
            direction_between(position, path[index + 1]),
        }
    return sides


def check_bridge_sides(
    board: Board,
    new_path: list[Coord],
    existing_paths: dict[int, list[Coord]],
) -> None:
    """Verifica que dos conexiones no compartan un lado del mismo puente.

    NOTA (supuesto a confirmar contra el servidor real): el enunciado dice que
    "cada conexión debe entrar y salir del puente por lados distintos", lo que
    leído literalmente es trivial. Lo interpretamos como que las dos conexiones
    que comparten un puente deben usar lados disjuntos, que es la lectura
    geométrica de un cruce. Es la interpretación conservadora: si el servidor
    fuera más permisivo perdemos algunas jugadas legales, pero nunca enviamos
    una jugada inválida (que costaría la partida completa).
    """
    new_sides = bridge_sides_used(board, new_path)
    if not new_sides:
        return

    for connection_id, path in existing_paths.items():
        for position, sides in bridge_sides_used(board, path).items():
            overlap = new_sides.get(position, set()) & sides
            if overlap:
                raise RuleViolation(
                    f"El puente en {position} ya usa ese lado para la conexión "
                    f"{connection_id}"
                )


def find_any_path(
    board: Board,
    start: Coord,
    end: Coord,
    connection_id: int,
    blocked_bridge_sides: dict[Coord, set[tuple[int, int]]] | None = None,
) -> list[Coord] | None:
    """Busca cualquier camino válido entre dos conectores (BFS, el más corto).

    Se usa para detectar el fin de partida en Supervivencia y como línea base
    del pathfinder. `blocked_bridge_sides` indica, para cada puente ya ocupado,
    qué lados no pueden reutilizarse.
    """
    blocked_bridge_sides = blocked_bridge_sides or {}
    queue: deque[Coord] = deque([start])
    came_from: dict[Coord, Coord | None] = {start: None}

    while queue:
        current = queue.popleft()
        if current == end:
            path = []
            node: Coord | None = current
            while node is not None:
                path.append(node)
                node = came_from[node]
            return path[::-1]

        for neighbor in board.neighbors(*current):
            if neighbor in came_from:
                continue
            if neighbor != end:
                cell = board.cell(*neighbor)
                if cell.capacity == 0 or not cell.is_free_for(connection_id):
                    continue
                used_sides = blocked_bridge_sides.get(neighbor)
                if used_sides and direction_between(neighbor, current) in used_sides:
                    continue
            came_from[neighbor] = current
            queue.append(neighbor)

    return None
