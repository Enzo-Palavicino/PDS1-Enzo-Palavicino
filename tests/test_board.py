import pytest

from gridlink.board import Board, CellKind


def test_round_trip_simple_board():
    # Ejemplo tomado de la respuesta de POST /api/games/<id>/start/
    board_str = "C1,N,N|N,L,N|N,N,C2"
    board = Board.from_string(board_str)

    assert board.rows == 3
    assert board.cols == 3
    assert board.to_string() == board_str


def test_round_trip_board_with_used_path():
    # Ejemplo tomado de GET /api/games/<id>/status/
    board_str = "C1,K1,C2|N,N,N|N,N,N"
    board = Board.from_string(board_str)

    assert board.to_string() == board_str
    used_cell = board.cell(0, 1)
    assert used_cell.kind == CellKind.NORMAL
    assert used_cell.occupants == [1]


def test_connector_parsing_and_lookup():
    board = Board.from_string("C1,N,N|N,L,N|N,N,C2")

    connectors = board.find_connectors()
    assert connectors == {1: (0, 0), 2: (2, 2)}
    assert board.cell(0, 0).kind == CellKind.CONNECTOR
    assert board.cell(0, 0).connector_id == 1


def test_blocked_cell_has_no_capacity():
    board = Board.from_string("N,L,N")
    blocked = board.cell(0, 1)

    assert blocked.kind == CellKind.BLOCKED
    assert blocked.capacity == 0
    assert not blocked.is_free_for(1)


def test_connector_cell_has_no_capacity():
    board = Board.from_string("C1,N,C2")
    connector = board.cell(0, 0)

    assert connector.capacity == 0
    assert not connector.is_free_for(1)


@pytest.mark.parametrize(
    "token, expected_occupants",
    [
        ("B", []),
        ("B:1", [1]),
        ("B:1+2", [1, 2]),
        ("B:2+1", [1, 2]),  # el orden de entrada no importa, se normaliza
    ],
)
def test_bridge_parsing(token, expected_occupants):
    board = Board.from_string(token)
    bridge = board.cell(0, 0)

    assert bridge.kind == CellKind.BRIDGE
    assert bridge.occupants == expected_occupants
    assert bridge.capacity == 2


def test_bridge_serialization_after_normalization():
    board = Board.from_string("B:2+1")
    assert board.to_string() == "B:1+2"


def test_potential_bridge_parsing():
    board = Board.from_string("P,N")
    potential = board.cell(0, 0)

    assert potential.kind == CellKind.POTENTIAL_BRIDGE
    assert potential.capacity == 1
    assert potential.serialize() == "P"


def test_occupy_respects_capacity():
    board = Board.from_string("N")
    normal = board.cell(0, 0)

    normal.occupy(1)
    assert normal.occupants == [1]
    assert normal.serialize() == "K1"

    with pytest.raises(ValueError):
        normal.occupy(2)  # una celda normal solo admite una conexión


def test_occupy_is_idempotent_for_same_connection():
    board = Board.from_string("N")
    normal = board.cell(0, 0)

    normal.occupy(1)
    normal.occupy(1)  # no debe lanzar ni duplicar
    assert normal.occupants == [1]


def test_bridge_accepts_two_distinct_connections():
    board = Board.from_string("B")
    bridge = board.cell(0, 0)

    bridge.occupy(1)
    bridge.occupy(2)
    assert bridge.occupants == [1, 2]
    assert bridge.serialize() == "B:1+2"

    with pytest.raises(ValueError):
        bridge.occupy(3)  # ya tiene dos conexiones


def test_vacate_removes_occupant():
    board = Board.from_string("N")
    normal = board.cell(0, 0)

    normal.occupy(1)
    normal.vacate(1)
    assert normal.occupants == []
    assert normal.serialize() == "N"


def test_neighbors_respects_bounds():
    board = Board.from_string("N,N|N,N")

    assert set(board.neighbors(0, 0)) == {(1, 0), (0, 1)}
    assert set(board.neighbors(1, 1)) == {(0, 1), (1, 0)}


def test_mark_path_marks_only_intermediate_cells():
    board = Board.from_string("C1,N,N,C2")

    board.mark_path([(0, 0), (0, 1), (0, 2), (0, 3)], connection_id=1)

    assert board.to_string() == "C1,K1,K1,C2"
    # los conectores en los extremos no deben marcarse como ocupados
    assert board.cell(0, 0).occupants == []
    assert board.cell(0, 3).occupants == []


def test_mark_path_requires_at_least_two_cells():
    board = Board.from_string("C1")

    with pytest.raises(ValueError):
        board.mark_path([(0, 0)], connection_id=1)


def test_non_rectangular_board_raises():
    with pytest.raises(ValueError):
        Board.from_string("N,N,N|N,N")


def test_unknown_token_raises():
    with pytest.raises(ValueError):
        Board.from_string("X")


def test_clone_is_independent():
    board = Board.from_string("N,N")
    clone = board.clone()

    clone.cell(0, 0).occupy(1)

    assert board.cell(0, 0).occupants == []
    assert clone.cell(0, 0).occupants == [1]
