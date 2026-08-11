import pytest

from gridlink.board import Board
from gridlink.rules import (
    RuleViolation,
    bridge_sides_used,
    check_bridge_sides,
    check_capacity,
    extract_added_cells,
    find_any_path,
    order_path,
)


def test_extract_added_cells_detects_new_path():
    previous = Board.from_string("C1,N,N,C2")
    submitted = Board.from_string("C1,K1,K1,C2")

    assert extract_added_cells(previous, submitted, 1) == [(0, 1), (0, 2)]


def test_extract_added_cells_rejects_moved_connector():
    previous = Board.from_string("C1,N,N,C2")
    submitted = Board.from_string("N,C1,N,C2")

    with pytest.raises(RuleViolation):
        extract_added_cells(previous, submitted, 1)


def test_extract_added_cells_rejects_changed_connector_id():
    previous = Board.from_string("C1,N,N,C2")
    submitted = Board.from_string("C5,N,N,C2")

    with pytest.raises(RuleViolation, match="conector"):
        extract_added_cells(previous, submitted, 1)


def test_extract_added_cells_rejects_erasing_previous_connection():
    # En Supervivencia una conexión ya construida no puede modificarse.
    previous = Board.from_string("C1,K1,C2|N,N,N|C3,N,C4")
    submitted = Board.from_string("C1,N,C2|N,N,N|C3,K2,C4")

    with pytest.raises(RuleViolation):
        extract_added_cells(previous, submitted, 2)


def test_extract_added_cells_rejects_foreign_connection_id():
    previous = Board.from_string("C1,N,N,C2")
    submitted = Board.from_string("C1,K7,K7,C2")

    with pytest.raises(RuleViolation):
        extract_added_cells(previous, submitted, 1)


def test_extract_added_cells_rejects_removed_block():
    previous = Board.from_string("C1,L,N,C2")
    submitted = Board.from_string("C1,K1,N,C2")

    with pytest.raises(RuleViolation, match="tipo"):
        extract_added_cells(previous, submitted, 1)


def test_extract_added_cells_rejects_dimension_change():
    previous = Board.from_string("C1,N,C2")
    submitted = Board.from_string("C1,N,C2|N,N,N")

    with pytest.raises(RuleViolation, match="dimensiones"):
        extract_added_cells(previous, submitted, 1)


def test_extract_added_cells_allows_potential_bridge_rendered_as_used():
    # Una celda potencial ocupada se serializa como K<n>, no como P.
    previous = Board.from_string("C1,P,C2")
    submitted = Board.from_string("C1,K1,C2")

    assert extract_added_cells(previous, submitted, 1) == [(0, 1)]


def test_check_capacity_rejects_occupied_normal_cell():
    board = Board.from_string("C1,K1,C2")

    with pytest.raises(RuleViolation, match="ocupada"):
        check_capacity(board, [(0, 1)], connection_id=2)


def test_check_capacity_allows_second_connection_on_bridge():
    board = Board.from_string("C1,B:1,C2")

    check_capacity(board, [(0, 1)], connection_id=2)  # no debe lanzar


def test_check_capacity_rejects_block_and_connector():
    board = Board.from_string("C1,L,C2")

    with pytest.raises(RuleViolation, match="bloqueo"):
        check_capacity(board, [(0, 1)], connection_id=1)
    with pytest.raises(RuleViolation, match="conector"):
        check_capacity(board, [(0, 2)], connection_id=1)


def test_order_path_returns_ordered_path():
    board = Board.from_string("C1,N,N,C2")

    path = order_path(board, [(0, 2), (0, 1)], start=(0, 0), end=(0, 3))

    assert path == [(0, 0), (0, 1), (0, 2), (0, 3)]


def test_order_path_handles_adjacent_connectors():
    board = Board.from_string("C1,C2")

    assert order_path(board, [], start=(0, 0), end=(0, 1)) == [(0, 0), (0, 1)]


def test_order_path_rejects_non_adjacent_connectors_without_cells():
    board = Board.from_string("C1,N,C2")

    with pytest.raises(RuleViolation):
        order_path(board, [], start=(0, 0), end=(0, 2))


def test_order_path_rejects_disconnected_cells():
    board = Board.from_string("C1,N,N,N,C2")

    with pytest.raises(RuleViolation):
        order_path(board, [(0, 1), (0, 3)], start=(0, 0), end=(0, 4))


def test_order_path_rejects_extra_cells():
    board = Board.from_string("C1,N,C2|N,N,N")

    # La celda (1,1) sobra: no puede formar parte de un camino simple C1->C2.
    with pytest.raises(RuleViolation):
        order_path(board, [(0, 1), (1, 1)], start=(0, 0), end=(0, 2))


def test_order_path_finds_l_shaped_route():
    board = Board.from_string("C1,N,N|N,N,N|N,N,C2")

    path = order_path(
        board, [(0, 1), (0, 2), (1, 2)], start=(0, 0), end=(2, 2)
    )

    assert path == [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]


def test_order_path_follows_route_away_from_the_direct_line():
    board = Board.from_string("C1,N,N|N,N,N|N,N,C2")

    path = order_path(
        board, [(1, 0), (1, 1), (1, 2)], start=(0, 0), end=(2, 2)
    )

    assert path == [(0, 0), (1, 0), (1, 1), (1, 2), (2, 2)]


def test_order_path_rejects_self_touching_route():
    """El servidor real rechaza caminos que corren pegados a sí mismos.

    Reproduce el caso exacto probado contra la API en la partida 12: el camino
    no repite celdas, pero (7,2) es vecino de (7,3), que va más adelante.
    """
    board = Board.from_string(
        "N,N,N,L,N,C5,N,N|N,L,C10,N,N,N,N,N|N,N,N,N,N,N,N,N|"
        "N,C1,N,N,C2,N,N,N|N,N,N,N,C3,N,N,C4|N,N,N,C9,N,N,N,N|"
        "N,C6,N,N,N,C8,N,N|N,N,C7,N,N,N,N,N"
    )
    self_touching = [(6, 2), (6, 3), (7, 3), (7, 4), (6, 4), (5, 4)]

    with pytest.raises(RuleViolation):
        order_path(board, self_touching, start=(7, 2), end=(5, 3))


def test_order_path_rejects_branching():
    board = Board.from_string("C1,N,C2|N,N,N|N,N,N")

    # (1,1) cuelga del camino como una rama muerta.
    with pytest.raises(RuleViolation):
        order_path(board, [(0, 1), (1, 1)], start=(0, 0), end=(0, 2))


def test_order_path_rejects_connector_marked_as_path():
    board = Board.from_string("C1,N,C2")

    with pytest.raises(RuleViolation, match="conectores"):
        order_path(board, [(0, 0), (0, 1)], start=(0, 0), end=(0, 2))


def test_bridge_sides_used_reports_crossing_directions():
    board = Board.from_string("N,N,N|N,B,N|N,N,N")
    path = [(1, 0), (1, 1), (1, 2)]

    sides = bridge_sides_used(board, path)

    assert sides == {(1, 1): {(0, -1), (0, 1)}}


def test_check_bridge_sides_allows_perpendicular_crossing():
    board = Board.from_string("N,C3,N|C1,B:1,C2|N,C4,N")
    horizontal = [(1, 0), (1, 1), (1, 2)]
    vertical = [(0, 1), (1, 1), (2, 1)]

    check_bridge_sides(board, vertical, {1: horizontal})  # no debe lanzar


def test_check_bridge_sides_rejects_shared_side():
    board = Board.from_string("N,C3,N|C1,B:1,C2|N,C4,N")
    horizontal = [(1, 0), (1, 1), (1, 2)]
    shares_left_side = [(1, 0), (1, 1), (0, 1)]

    with pytest.raises(RuleViolation, match="lado"):
        check_bridge_sides(board, shares_left_side, {1: horizontal})


def test_find_any_path_returns_shortest_route():
    board = Board.from_string("C1,N,N,C2")

    path = find_any_path(board, (0, 0), (0, 3), connection_id=1)

    assert path == [(0, 0), (0, 1), (0, 2), (0, 3)]


def test_find_any_path_avoids_blocks_and_used_cells():
    board = Board.from_string("C1,L,C2|N,K1,N|N,N,N")

    path = find_any_path(board, (0, 0), (0, 2), connection_id=2)

    assert path is not None
    assert (0, 1) not in path  # bloqueo
    assert (1, 1) not in path  # ya usada por la conexión 1


def test_find_any_path_returns_none_when_unreachable():
    board = Board.from_string("C1,L,C2|L,L,L|N,N,N")

    assert find_any_path(board, (0, 0), (0, 2), connection_id=1) is None


def test_find_any_path_respects_blocked_bridge_sides():
    board = Board.from_string("C1,B:1,C2")

    unrestricted = find_any_path(board, (0, 0), (0, 2), connection_id=2)
    assert unrestricted is not None

    restricted = find_any_path(
        board,
        (0, 0),
        (0, 2),
        connection_id=2,
        blocked_bridge_sides={(0, 1): {(0, -1), (0, 1)}},
    )
    assert restricted is None
