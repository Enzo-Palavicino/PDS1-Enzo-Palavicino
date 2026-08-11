"""Modelo del tablero de GridLink: parseo, serialización y estado de celdas.

Formato del tablero (ver enunciado/GridLink API - Docs.md):
    - Celdas separadas por ",", filas separadas por "|".
    - N: celda normal | L: bloqueo | P: celda potencial (puente potencial)
    - B: puente libre | C<id>: conector | K<n>: celda usada por la conexión n
    - B:<n> / B:<n>+<m>: puente usado por una o dos conexiones
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CellKind(Enum):
    NORMAL = "N"
    BLOCKED = "L"
    POTENTIAL_BRIDGE = "P"
    BRIDGE = "B"
    CONNECTOR = "C"


@dataclass
class Cell:
    kind: CellKind
    connector_id: int | None = None
    occupants: list[int] = field(default_factory=list)

    @property
    def capacity(self) -> int:
        """Cantidad de conexiones distintas que puede sostener esta celda."""
        if self.kind == CellKind.BRIDGE:
            return 2
        if self.kind in (CellKind.NORMAL, CellKind.POTENTIAL_BRIDGE):
            return 1
        return 0  # bloqueos y conectores nunca son atravesados por un camino

    def is_free_for(self, connection_id: int) -> bool:
        if connection_id in self.occupants:
            return True
        return len(self.occupants) < self.capacity

    def occupy(self, connection_id: int) -> None:
        if connection_id in self.occupants:
            return
        if len(self.occupants) >= self.capacity:
            raise ValueError(
                f"La celda no tiene capacidad para la conexión {connection_id}"
            )
        self.occupants.append(connection_id)
        self.occupants.sort()

    def vacate(self, connection_id: int) -> None:
        if connection_id in self.occupants:
            self.occupants.remove(connection_id)

    def serialize(self) -> str:
        if self.kind == CellKind.BLOCKED:
            return "L"
        if self.kind == CellKind.CONNECTOR:
            return f"C{self.connector_id}"
        if self.kind == CellKind.BRIDGE:
            if not self.occupants:
                return "B"
            return "B:" + "+".join(str(c) for c in self.occupants)
        # NORMAL o POTENTIAL_BRIDGE (antes de transformarse en puente)
        if self.occupants:
            return f"K{self.occupants[0]}"
        return self.kind.value  # "N" o "P"

    def clone(self) -> Cell:
        return Cell(
            kind=self.kind,
            connector_id=self.connector_id,
            occupants=list(self.occupants),
        )


def _parse_cell(token: str) -> Cell:
    token = token.strip()
    if token == "N":
        return Cell(kind=CellKind.NORMAL)
    if token == "L":
        return Cell(kind=CellKind.BLOCKED)
    if token == "P":
        return Cell(kind=CellKind.POTENTIAL_BRIDGE)
    if token == "B":
        return Cell(kind=CellKind.BRIDGE)
    if token.startswith("B:"):
        occupants = sorted(int(part) for part in token[2:].split("+"))
        return Cell(kind=CellKind.BRIDGE, occupants=occupants)
    if token.startswith("K"):
        return Cell(kind=CellKind.NORMAL, occupants=[int(token[1:])])
    if token.startswith("C"):
        return Cell(kind=CellKind.CONNECTOR, connector_id=int(token[1:]))
    raise ValueError(f"Token de celda desconocido: {token!r}")


@dataclass
class Board:
    rows: int
    cols: int
    grid: list[list[Cell]]

    @classmethod
    def from_string(cls, board_str: str) -> Board:
        row_tokens = board_str.split("|")
        grid = [[_parse_cell(tok) for tok in row.split(",")] for row in row_tokens]
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        if any(len(row) != cols for row in grid):
            raise ValueError("El tablero no es rectangular")
        return cls(rows=rows, cols=cols, grid=grid)

    def to_string(self) -> str:
        return "|".join(
            ",".join(cell.serialize() for cell in row) for row in self.grid
        )

    def cell(self, row: int, col: int) -> Cell:
        return self.grid[row][col]

    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.rows and 0 <= col < self.cols

    def neighbors(self, row: int, col: int) -> list[tuple[int, int]]:
        candidates = [(row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)]
        return [(r, c) for r, c in candidates if self.in_bounds(r, c)]

    def find_connectors(self) -> dict[int, tuple[int, int]]:
        positions: dict[int, tuple[int, int]] = {}
        for r in range(self.rows):
            for c in range(self.cols):
                cell = self.grid[r][c]
                if cell.kind == CellKind.CONNECTOR:
                    positions[cell.connector_id] = (r, c)
        return positions

    def mark_path(self, path: list[tuple[int, int]], connection_id: int) -> None:
        """Marca las celdas intermedias de un camino como usadas por una conexión.

        `path` incluye ambos conectores como extremos; solo se marcan las
        celdas intermedias, ya que los conectores no llevan marca de uso.
        """
        if len(path) < 2:
            raise ValueError("Un camino necesita al menos dos celdas (los conectores)")
        for row, col in path[1:-1]:
            self.grid[row][col].occupy(connection_id)

    def clone(self) -> Board:
        return Board(
            rows=self.rows,
            cols=self.cols,
            grid=[[c.clone() for c in row] for row in self.grid],
        )

    def __str__(self) -> str:
        return self.to_string()
