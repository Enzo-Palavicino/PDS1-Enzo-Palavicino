"""Simulador local de GridLink: genera partidas y hace de servidor.

Replica la forma de las respuestas de la API real (ver enunciado/GridLink API -
Docs.md) para poder desarrollar y medir la estrategia sin depender de la red ni
gastar partidas reales. Mantiene su propio estado de verdad del tablero, que
incluye información que el jugador no puede deducir del string (por ejemplo,
qué celdas ocupadas eran originalmente puentes potenciales).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .board import Board, Cell, CellKind
from .rules import (
    Coord,
    RuleViolation,
    bridge_sides_used,
    check_bridge_sides,
    check_capacity,
    extract_added_cells,
    find_any_path,
    order_path,
)
from .scoring import survival_score


@dataclass
class GameConfig:
    """Parámetros de generación de una partida."""

    rows: int = 8
    cols: int = 8
    request_count: int = 4
    request_window_size: int = 1
    mode: str = "survival"
    block_ratio: float = 0.10
    bridge_ratio: float = 0.0
    potential_ratio: float = 0.0
    bridge_probability: float = 0.0
    fewest_cells_penalty: float = 1.0
    allow_player_resets: bool = True
    seed: int | None = None


def partial_delivery_config(**overrides) -> GameConfig:
    """Configuración de la entrega parcial: supervivencia, ventana 1, sin potenciales."""
    defaults = {
        "mode": "survival",
        "request_window_size": 1,
        "potential_ratio": 0.0,
        "bridge_probability": 0.0,
    }
    defaults.update(overrides)
    return GameConfig(**defaults)


@dataclass
class GeneratedGame:
    board: Board
    requests: list[tuple[int, int]]
    config: GameConfig
    reference_paths: dict[int, list[Coord]] = field(default_factory=dict)


def _is_corner(coord: Coord, rows: int, cols: int) -> bool:
    return coord[0] in (0, rows - 1) and coord[1] in (0, cols - 1)


def _is_edge(coord: Coord, rows: int, cols: int) -> bool:
    return coord[0] in (0, rows - 1) or coord[1] in (0, cols - 1)


def _free_neighbor_count(board: Board, coord: Coord) -> int:
    """Vecinos ortogonales que no son bloqueos ni conectores."""
    return sum(
        1
        for neighbor in board.neighbors(*coord)
        if board.cell(*neighbor).kind not in (CellKind.BLOCKED, CellKind.CONNECTOR)
    )


class GameGenerator:
    """Genera tableros que respetan las restricciones del enunciado.

    Para garantizar que la partida tenga solución (equivalente al
    `has_verified_solution` del servidor real), primero traza caminos disjuntos
    entre pares de celdas y recién después coloca bloqueos fuera de ellos. Los
    extremos de cada camino se convierten en los conectores de una solicitud.
    """

    def __init__(self, config: GameConfig):
        self.config = config
        self.rng = random.Random(config.seed)

    def generate(self) -> GeneratedGame:
        config = self.config
        board = Board(
            rows=config.rows,
            cols=config.cols,
            grid=[
                [Cell(kind=CellKind.NORMAL) for _ in range(config.cols)]
                for _ in range(config.rows)
            ],
        )
        reserved: set[Coord] = set()
        connector_positions: dict[int, Coord] = {}
        requests: list[tuple[int, int]] = []
        reference_paths: dict[int, list[Coord]] = {}

        for index in range(config.request_count):
            carved = self._carve_request(board, reserved, connector_positions)
            if carved is None:
                break  # el tablero se saturó; la partida queda con menos solicitudes
            path, id_a, id_b = carved
            requests.append((id_a, id_b))
            reference_paths[index + 1] = path
            reserved.update(path)

        self._place_blocks(board, reserved)
        self._place_special_cells(board, CellKind.BRIDGE, config.bridge_ratio)
        self._place_special_cells(board, CellKind.POTENTIAL_BRIDGE, config.potential_ratio)
        self._repair_connector_neighborhoods(board)

        self.rng.shuffle(requests)
        return GeneratedGame(
            board=board,
            requests=requests,
            config=config,
            reference_paths=reference_paths,
        )

    def _carve_request(
        self,
        board: Board,
        reserved: set[Coord],
        connector_positions: dict[int, Coord],
    ) -> tuple[list[Coord], int, int] | None:
        """Traza un camino libre y convierte sus extremos en un par de conectores."""
        rows, cols = board.rows, board.cols
        candidates = [
            (r, c)
            for r in range(rows)
            for c in range(cols)
            if (r, c) not in reserved
            and board.cell(r, c).kind == CellKind.NORMAL
            and not _is_corner((r, c), rows, cols)
        ]
        self.rng.shuffle(candidates)

        for attempt in range(200):
            if len(candidates) < 2:
                return None
            start = self.rng.choice(candidates)
            end = self.rng.choice(candidates)
            if start == end or end in board.neighbors(*start):
                continue
            # Los conectores no deben quedar pegados a otros conectores, para no
            # comprometer la regla de "al menos tres vecinos libres".
            if self._touches_connector(board, start) or self._touches_connector(board, end):
                continue

            path = self._search_free_path(board, start, end, reserved)
            if path is None:
                continue

            id_a = len(connector_positions) + 1
            id_b = id_a + 1
            board.grid[start[0]][start[1]] = Cell(
                kind=CellKind.CONNECTOR, connector_id=id_a
            )
            board.grid[end[0]][end[1]] = Cell(kind=CellKind.CONNECTOR, connector_id=id_b)
            connector_positions[id_a] = start
            connector_positions[id_b] = end
            return path, id_a, id_b
        return None

    def _touches_connector(self, board: Board, coord: Coord) -> bool:
        return any(
            board.cell(*neighbor).kind == CellKind.CONNECTOR
            for neighbor in board.neighbors(*coord)
        )

    def _search_free_path(
        self, board: Board, start: Coord, end: Coord, reserved: set[Coord]
    ) -> list[Coord] | None:
        """BFS entre dos celdas usando solo celdas normales aún no reservadas."""
        from collections import deque

        queue = deque([start])
        came_from: dict[Coord, Coord | None] = {start: None}
        while queue:
            current = queue.popleft()
            if current == end:
                path: list[Coord] = []
                node: Coord | None = current
                while node is not None:
                    path.append(node)
                    node = came_from[node]
                return path[::-1]
            for neighbor in board.neighbors(*current):
                if neighbor in came_from or neighbor in reserved:
                    continue
                if neighbor != end and board.cell(*neighbor).kind != CellKind.NORMAL:
                    continue
                came_from[neighbor] = current
                queue.append(neighbor)
        return None

    def _place_blocks(self, board: Board, reserved: set[Coord]) -> None:
        total_cells = board.rows * board.cols
        budget = int(total_cells * self.config.block_ratio)
        candidates = [
            (r, c)
            for r in range(board.rows)
            for c in range(board.cols)
            if (r, c) not in reserved and board.cell(r, c).kind == CellKind.NORMAL
        ]
        self.rng.shuffle(candidates)

        placed = 0
        for coord in candidates:
            if placed >= budget:
                break
            board.grid[coord[0]][coord[1]] = Cell(kind=CellKind.BLOCKED)
            # Un bloqueo no puede dejar a un conector con menos de 3 vecinos libres.
            if any(
                board.cell(*neighbor).kind == CellKind.CONNECTOR
                and _free_neighbor_count(board, neighbor) < 3
                for neighbor in board.neighbors(*coord)
            ):
                board.grid[coord[0]][coord[1]] = Cell(kind=CellKind.NORMAL)
                continue
            placed += 1

    def _place_special_cells(self, board: Board, kind: CellKind, ratio: float) -> None:
        """Coloca puentes o puentes potenciales respetando sus restricciones.

        Ninguno puede ir en la orilla del tablero ni tener bloqueos como vecinos
        ortogonales.
        """
        if ratio <= 0:
            return
        budget = int(board.rows * board.cols * ratio)
        candidates = [
            (r, c)
            for r in range(board.rows)
            for c in range(board.cols)
            if board.cell(r, c).kind == CellKind.NORMAL
            and not _is_edge((r, c), board.rows, board.cols)
            and all(
                board.cell(*neighbor).kind != CellKind.BLOCKED
                for neighbor in board.neighbors(r, c)
            )
        ]
        self.rng.shuffle(candidates)
        for coord in candidates[:budget]:
            board.grid[coord[0]][coord[1]] = Cell(kind=kind)

    def _repair_connector_neighborhoods(self, board: Board) -> None:
        """Libera vecinos hasta que todo conector tenga al menos tres libres."""
        for connector_id, coord in board.find_connectors().items():
            for neighbor in board.neighbors(*coord):
                if _free_neighbor_count(board, coord) >= 3:
                    break
                if board.cell(*neighbor).kind == CellKind.BLOCKED:
                    board.grid[neighbor[0]][neighbor[1]] = Cell(kind=CellKind.NORMAL)


class GameSimulator:
    """Servidor local de una partida, con la misma interfaz que la API real."""

    def __init__(self, config: GameConfig, game_id: int = 1):
        generated = GameGenerator(config).generate()
        self.game_id = game_id
        self.config = config
        self.reference_paths = generated.reference_paths

        self._board = generated.board
        self._connectors = self._board.find_connectors()
        self._all_requests = generated.requests
        self._pending = list(generated.requests)
        self._visible: list[tuple[int, int]] = []
        self._paths: dict[int, list[Coord]] = {}
        self._completed = 0
        self._rng = random.Random(
            None if config.seed is None else config.seed + 9973
        )

        self.status = "created"
        self._blocked = False
        self.last_error = ""
        self.started_at: str | None = None
        self.ended_at: str | None = None
        self.score: int | None = None

    # ------------------------------------------------------------------ API

    def start(self) -> dict:
        self.status = "in_progress"
        self.started_at = _now()
        self._refill_window()
        return {
            "game_id": self.game_id,
            "board": self._board.to_string(),
            "params": self._params(),
            "requests": [list(request) for request in self._visible],
        }

    def get_status(self) -> dict:
        payload = {
            "game_id": self.game_id,
            "status": self.status,
            "board": self._board.to_string(),
            "params": self._params(),
            "requests": [list(request) for request in self._visible],
            "current_turn": self._completed + 1,
            "completed_requests": self._completed,
            "removed_cells_total": 0,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "updated_at": _now(),
        }
        if self.score is not None:
            payload["score"] = self.score
        return payload

    def reset(self) -> dict:
        if not self.config.allow_player_resets:
            return {"reset": False, "game_id": self.game_id}
        self.__init__(self.config, self.game_id)
        return {"reset": True, "game_id": self.game_id}

    def move(self, board_str: str) -> dict:
        if self.status != "in_progress":
            return self._invalid(f"La partida no está en curso (status={self.status})")

        connection_id = self._completed + 1
        try:
            submitted = Board.from_string(board_str)
        except ValueError as error:
            return self._invalid(f"Tablero mal formado: {error}")

        try:
            added = extract_added_cells(self._board, submitted, connection_id)
            check_capacity(self._board, added, connection_id)
            request, path = self._match_request(added)
            check_bridge_sides(self._board, path, self._paths)
        except RuleViolation as error:
            return self._invalid(str(error))

        self._apply(request, path, connection_id)
        return self._move_result()

    # -------------------------------------------------------------- interno

    def _params(self) -> dict:
        kinds = [cell.kind for row in self._board.grid for cell in row]
        return {
            "mode": self.config.mode,
            "rows": self._board.rows,
            "cols": self._board.cols,
            "connector_count": len(self._connectors),
            "request_count": len(self._all_requests),
            "blocked_count": kinds.count(CellKind.BLOCKED),
            "bridge_count": kinds.count(CellKind.BRIDGE),
            "potential_count": kinds.count(CellKind.POTENTIAL_BRIDGE),
            "bridge_probability": self.config.bridge_probability,
            "request_window_size": self.config.request_window_size,
            "fewest_cells_penalty": str(self.config.fewest_cells_penalty),
            "allow_player_resets": self.config.allow_player_resets,
            "has_verified_solution": True,
        }

    def _match_request(self, added: list[Coord]) -> tuple[tuple[int, int], list[Coord]]:
        """Determina a qué solicitud visible corresponde el camino enviado."""
        errors = []
        for request in self._visible:
            start = self._connectors[request[0]]
            end = self._connectors[request[1]]
            try:
                return request, order_path(self._board, added, start, end)
            except RuleViolation as error:
                errors.append(f"{request}: {error}")
        raise RuleViolation(
            "El camino no conecta ninguna solicitud visible (" + "; ".join(errors) + ")"
        )

    def _apply(
        self, request: tuple[int, int], path: list[Coord], connection_id: int
    ) -> None:
        self._board.mark_path(path, connection_id)
        self._paths[connection_id] = path
        self._completed += 1
        self._visible.remove(request)
        self._refill_window()
        self._maybe_transform_potential_bridge()

        if not self._visible:
            self._finish()
        elif not any(self._is_solvable(request) for request in self._visible):
            self._finish(blocked=True)

    def _refill_window(self) -> None:
        while len(self._visible) < self.config.request_window_size and self._pending:
            self._visible.append(self._pending.pop(0))

    def _blocked_bridge_sides(self) -> dict[Coord, set[tuple[int, int]]]:
        used: dict[Coord, set[tuple[int, int]]] = {}
        for path in self._paths.values():
            for position, sides in bridge_sides_used(self._board, path).items():
                used.setdefault(position, set()).update(sides)
        return used

    def _is_solvable(self, request: tuple[int, int]) -> bool:
        return (
            find_any_path(
                self._board,
                self._connectors[request[0]],
                self._connectors[request[1]],
                connection_id=self._completed + 1,
                blocked_bridge_sides=self._blocked_bridge_sides(),
            )
            is not None
        )

    def _maybe_transform_potential_bridge(self) -> None:
        if self._rng.random() >= self.config.bridge_probability:
            return
        candidates = [
            (r, c)
            for r in range(self._board.rows)
            for c in range(self._board.cols)
            if self._board.cell(r, c).kind == CellKind.POTENTIAL_BRIDGE
        ]
        if not candidates:
            return
        row, col = self._rng.choice(candidates)
        # La conexión que ya pasaba por la celda se mantiene; solo gana capacidad.
        self._board.grid[row][col].kind = CellKind.BRIDGE

    def _finish(self, blocked: bool = False) -> None:
        self.status = "completed"
        self.ended_at = _now()
        self.score = survival_score(self._completed, len(self._all_requests))
        self._blocked = blocked

    def _move_result(self) -> dict:
        completed = self.status == "completed"
        payload = {
            "valid": True,
            "completed": completed,
            "blocked": self._blocked,
            "board": self._board.to_string(),
            "requests": [list(request) for request in self._visible],
            "current_turn": self._completed + 1,
            "completed_requests": self._completed,
            "removed_cells_count": 0,
            "removed_cells_total": 0,
            "status": self.status,
        }
        if completed:
            payload["score"] = self.score
        return payload

    def _invalid(self, message: str) -> dict:
        self.status = "failed"
        self.last_error = message
        self.ended_at = _now()
        self.score = 0
        return {"valid": False, "error": message, "session_status": "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
