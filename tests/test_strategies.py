import random

import pytest

from gridlink.board import Board
from gridlink.pathfinder import PathfinderConfig
from gridlink.rules import find_any_path, order_path
from gridlink.strategies import (
    DISCARDED,
    STRATEGIES,
    _damage,
    _minimal_candidates,
    bidirectional_path,
    manhattan,
    min_turns_shortest_path,
    remove_loops,
)

# Toda estrategia, activa o descartada, debe seguir entregando caminos legales.
ALL_STRATEGIES = STRATEGIES | DISCARDED

BOARD = "C1,N,N,N,N,N|N,N,L,N,N,N|N,N,N,N,L,N|N,C3,N,N,N,N|N,N,N,L,N,C2"


def test_manhattan_distance():
    assert manhattan((0, 0), (2, 3)) == 5


def test_remove_loops_cuts_repeated_cells():
    path = [(0, 0), (0, 1), (0, 2), (0, 1), (1, 1)]

    assert remove_loops(path) == [(0, 0), (0, 1), (1, 1)]


def test_remove_loops_leaves_clean_path():
    path = [(0, 0), (0, 1), (0, 2)]

    assert remove_loops(path) == path


def test_bidirectional_connects_both_connectors():
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()

    path = bidirectional_path(board, connectors[1], connectors[2], 1)

    assert path is not None
    assert path[0] == connectors[1]
    assert path[-1] == connectors[2]


def test_bidirectional_path_is_rule_valid():
    """El camino del doble mapeo debe pasar la misma validación que aplica el servidor."""
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()

    path = bidirectional_path(board, connectors[1], connectors[2], 1)

    order_path(board, path[1:-1], start=connectors[1], end=connectors[2])


def test_bidirectional_returns_none_when_unreachable():
    board = Board.from_string("C1,L,C2|L,L,L|N,N,N")

    assert bidirectional_path(board, (0, 0), (0, 2), 1) is None


def test_bidirectional_handles_adjacent_connectors():
    board = Board.from_string("C1,C2|N,N")

    assert bidirectional_path(board, (0, 0), (0, 1), 1) == [(0, 0), (0, 1)]


OPEN_BOARD = "C1,N,N,N,N|N,N,N,N,N|N,N,N,N,N|N,N,N,N,N|N,N,N,N,C2"


def _turns(path):
    steps = [
        (b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])
    ]
    return sum(1 for a, b in zip(steps, steps[1:]) if a != b)


def test_min_turns_path_is_an_l_shape():
    """En un tablero despejado el camino de menos quiebres dobla una sola vez."""
    board = Board.from_string(OPEN_BOARD)

    path = min_turns_shortest_path(board, (0, 0), (4, 4), 1)

    assert len(path) == 9  # sigue siendo de largo mínimo
    assert _turns(path) == 1


def test_min_turns_path_respects_blocks():
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()

    path = min_turns_shortest_path(board, connectors[1], connectors[2], 1)
    shortest = find_any_path(board, connectors[1], connectors[2], 1)

    assert len(path) == len(shortest)
    order_path(board, path[1:-1], start=connectors[1], end=connectors[2])


def test_minimal_candidates_are_all_shortest_except_bidirectional():
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()
    minimum = len(find_any_path(board, connectors[1], connectors[2], 1))

    candidates = _minimal_candidates(
        board, connectors[1], connectors[2], 1, [connectors[3]]
    )

    assert candidates
    assert all(len(path) >= minimum for path in candidates)


def test_damage_prefers_keeping_a_connector_reachable():
    """Un camino que ahoga al conector pendiente debe puntuar peor que uno que no."""
    board = Board.from_string("C1,N,N|N,N,N|C3,N,C2")
    harmless = [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]
    choking = [(0, 0), (1, 0), (1, 1), (2, 1), (2, 2)]

    assert _damage(board, harmless, 1, [(2, 0)]) < _damage(board, choking, 1, [(2, 0)])


@pytest.mark.parametrize("name", sorted(ALL_STRATEGIES))
def test_every_strategy_returns_a_rule_valid_path(name):
    """Ninguna estrategia puede entregar un camino que el servidor rechazaría."""
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()

    path = ALL_STRATEGIES[name](
        board,
        connectors[1],
        connectors[2],
        1,
        [connectors[3]],
        PathfinderConfig(),
        random.Random(0),
        None,
    )

    assert path is not None, f"la estrategia {name} no encontró camino"
    order_path(board, path[1:-1], start=connectors[1], end=connectors[2])


@pytest.mark.parametrize("name", sorted(ALL_STRATEGIES))
def test_every_strategy_returns_none_when_unreachable(name):
    board = Board.from_string("C1,L,C2|L,L,L|N,N,N")

    path = ALL_STRATEGIES[name](
        board, (0, 0), (0, 2), 1, [], PathfinderConfig(), random.Random(0), None
    )

    assert path is None
