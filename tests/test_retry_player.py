import random

from gridlink.board import Board
from gridlink.retry_player import (
    RetryConfig,
    candidate_paths,
    play_with_retries,
    search_line,
)
from gridlink.rules import order_path
from gridlink.simulator import GameSimulator, partial_delivery_config

BOARD = "C1,N,N,N,N,N|N,N,L,N,N,N|N,N,N,N,L,N|N,C3,N,N,N,N|N,N,N,L,N,C2"


def test_candidate_paths_are_all_distinct_and_legal():
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()

    paths = candidate_paths(board, connectors[1], connectors[2], 1)

    assert len(paths) >= 2
    assert len({tuple(path) for path in paths}) == len(paths)
    for path in paths:
        order_path(board, path[1:-1], start=connectors[1], end=connectors[2])


def test_search_line_solves_a_two_request_sequence():
    line = search_line(BOARD, [(1, 2)], RetryConfig())

    assert line is not None
    assert len(line) == 1
    board = Board.from_string(BOARD)
    connectors = board.find_connectors()
    assert line[0][0] == connectors[1]
    assert line[0][-1] == connectors[2]


def test_search_line_backtracks_past_a_blocking_first_choice():
    """Un pasillo obliga a que la primera conexión ceda paso a la segunda.

    El camino más corto de C1 a C2 tapa el único acceso de C3 a C4, así que sin
    backtracking la segunda ronda quedaría sin camino.
    """
    board = "C1,N,N,C2|L,N,L,L|C3,N,N,C4"

    line = search_line(board, [(1, 2), (3, 4)], RetryConfig())

    assert line is not None, "la búsqueda debía retroceder y elegir otro camino"
    assert len(line) == 2
    trial = Board.from_string(board)
    for index, path in enumerate(line):
        trial.mark_path(path, index + 1)


def test_search_line_returns_none_when_impossible():
    board = "C1,L,C2|L,L,L|N,N,N"

    assert search_line(board, [(1, 2)], RetryConfig()) is None


def test_search_line_respects_the_node_budget():
    config = RetryConfig(search_nodes=0)

    assert search_line(BOARD, [(1, 2)], config) is None


def test_play_with_retries_completes_a_solvable_simulated_game():
    game = GameSimulator(partial_delivery_config(rows=10, cols=10, request_count=4, seed=7))

    result = play_with_retries(game, game_id=1, config=RetryConfig(time_budget=10.0))

    assert result.error is None
    assert result.completed_requests >= 1
    assert result.elapsed < 30


def test_play_with_retries_refuses_boards_that_mutate():
    """Con puentes potenciales la simulación offline dejaría de ser fiel."""
    game = GameSimulator(partial_delivery_config(rows=10, cols=10, request_count=4, seed=7))
    original = game.start

    def start_with_potentials():
        payload = original()
        payload["params"]["potential_count"] = 3
        return payload

    game.start = start_with_potentials
    result = play_with_retries(game, game_id=1)

    assert result.error is not None
    assert "offline" in result.error


def test_never_leaves_the_session_unfinished():
    """Una sesión a medio jugar no recibe puntaje del servidor: vale 0.

    Con un presupuesto ínfimo el bucle de reintentos sale de inmediato, pero la
    partida igual tiene que quedar terminada. Regresión real: id 26 quedó en 0
    después de haber sacado 86.
    """
    game = GameSimulator(
        partial_delivery_config(rows=12, cols=12, request_count=6, seed=3)
    )

    result = play_with_retries(
        game, game_id=1, config=RetryConfig(time_budget=0.001)
    )

    status = game.get_status()
    assert status["status"] == "completed", "la partida quedó a medio jugar"
    assert result.score is not None, "una partida terminada siempre tiene puntaje"
