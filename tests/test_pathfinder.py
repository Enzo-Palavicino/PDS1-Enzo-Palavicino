import random

from gridlink.board import Board
from gridlink.pathfinder import (
    INFEASIBLE,
    PathfinderConfig,
    choose_path,
    evaluate_path,
    free_neighbors,
    strip_self_touches,
    weighted_path,
)
from gridlink.rules import RuleViolation, order_path

# Dos rutas de igual largo entre C1(0,0) y C2(2,3): por la fila 0 o por la fila 1.
# La de la fila 1 usa (1,1), que es el único acceso libre de C3.
TRAP_BOARD = "C1,N,N,N|N,N,N,N|L,C3,L,C2"


def test_weighted_path_finds_a_route():
    board = Board.from_string(TRAP_BOARD)

    path = weighted_path(board, (0, 0), (2, 3), 1, lambda _: 1.0)

    assert path is not None
    assert path[0] == (0, 0) and path[-1] == (2, 3)


def test_weighted_path_returns_none_when_unreachable():
    board = Board.from_string("C1,L,C2|L,L,L|N,N,N")

    assert weighted_path(board, (0, 0), (0, 2), 1, lambda _: 1.0) is None


def test_strip_self_touches_removes_the_detour():
    """Reproduce el camino que el servidor real rechazó en la partida 12."""
    board = Board.from_string(
        "N,N,N,L,N,C5,N,N|N,L,C10,N,N,N,N,N|N,N,N,N,N,N,N,N|"
        "N,C1,N,N,C2,N,N,N|N,N,N,N,C3,N,N,C4|N,N,N,C9,N,N,N,N|"
        "N,C6,N,N,N,C8,N,N|N,N,C7,N,N,N,N,N"
    )
    self_touching = [(7, 2), (6, 2), (6, 3), (7, 3), (7, 4), (6, 4), (5, 4), (5, 3)]

    cleaned = strip_self_touches(board, self_touching)

    assert cleaned[0] == (7, 2) and cleaned[-1] == (5, 3)
    assert len(cleaned) < len(self_touching)
    # El resultado ya debe ser aceptable para las reglas del servidor.
    order_path(board, cleaned[1:-1], start=(7, 2), end=(5, 3))


def test_strip_self_touches_leaves_clean_paths_untouched():
    board = Board.from_string("C1,N,N,C2")
    path = [(0, 0), (0, 1), (0, 2), (0, 3)]

    assert strip_self_touches(board, path) == path


def test_evaluate_rejects_sealing_a_connector():
    board = Board.from_string(TRAP_BOARD)
    through_the_corridor = [(0, 0), (1, 0), (1, 1), (1, 2), (1, 3), (2, 3)]

    score = evaluate_path(
        board, through_the_corridor, 1, [(2, 1)], PathfinderConfig()
    )

    assert score == INFEASIBLE


def test_evaluate_accepts_route_that_preserves_access():
    board = Board.from_string(TRAP_BOARD)
    around_the_top = [(0, 0), (0, 1), (0, 2), (0, 3), (1, 3), (2, 3)]

    score = evaluate_path(board, around_the_top, 1, [(2, 1)], PathfinderConfig())

    assert score < INFEASIBLE


def test_choose_path_avoids_sealing_a_connector():
    board = Board.from_string(TRAP_BOARD)

    path = choose_path(
        board, (0, 0), (2, 3), connection_id=1, future_connectors=[(2, 1)]
    )

    assert path is not None
    assert (1, 1) not in path, "el camino elegido dejó a C3 sin accesos"

    board.mark_path(path, 1)
    assert free_neighbors(board, (2, 1)), "C3 quedó incomunicado"


def test_choose_path_result_is_always_rule_valid():
    """Todo camino entregado debe pasar la validación que aplica el servidor."""
    board = Board.from_string(
        "C1,N,N,N,N,N|N,N,L,N,N,N|N,N,N,N,L,N|N,C3,N,N,N,N|N,N,N,L,N,C2"
    )
    connectors = board.find_connectors()

    for seed in range(10):
        path = choose_path(
            board,
            connectors[1],
            connectors[2],
            connection_id=1,
            future_connectors=[connectors[3]],
            rng=random.Random(seed),
        )
        assert path is not None
        # No debe levantar: replica la comprobación del servidor.
        order_path(board, path[1:-1], start=connectors[1], end=connectors[2])


def test_choose_path_returns_none_when_no_route_exists():
    board = Board.from_string("C1,L,C2|L,L,L|N,N,N")

    assert choose_path(board, (0, 0), (0, 2), 1, future_connectors=[]) is None


def test_choose_path_respects_time_budget():
    board = Board.from_string("C1,N,N,N|N,N,N,N|N,N,N,C2")
    config = PathfinderConfig(time_budget=0.0, random_candidates=50)

    import time

    started = time.monotonic()
    path = choose_path(board, (0, 0), (2, 3), 1, [], config=config)
    elapsed = time.monotonic() - started

    assert path is not None  # siempre entrega una jugada
    assert elapsed < 0.5
