import pytest

from gridlink.board import Board, CellKind
from gridlink.rules import find_any_path
from gridlink.simulator import (
    GameConfig,
    GameGenerator,
    GameSimulator,
    partial_delivery_config,
)


def _play_one_move(sim, payload):
    """Resuelve la primera solicitud visible por el camino más corto."""
    board = Board.from_string(payload["board"])
    connectors = board.find_connectors()
    request = payload["requests"][0]
    path = find_any_path(
        board,
        connectors[request[0]],
        connectors[request[1]],
        connection_id=payload.get("current_turn", 1),
    )
    assert path is not None, "el simulador ofreció una solicitud sin solución"
    board.mark_path(path, payload.get("current_turn", 1))
    return sim.move(board.to_string())


# ------------------------------------------------------------- generación


def test_generated_board_respects_dimensions():
    config = GameConfig(rows=10, cols=12, request_count=4, seed=1)
    game = GameGenerator(config).generate()

    assert game.board.rows == 10
    assert game.board.cols == 12


def test_generation_is_deterministic_with_seed():
    config = GameConfig(rows=10, cols=10, request_count=4, seed=42)

    first = GameGenerator(config).generate()
    second = GameGenerator(config).generate()

    assert first.board.to_string() == second.board.to_string()
    assert first.requests == second.requests


def test_generated_board_respects_block_ratio():
    config = GameConfig(rows=12, cols=12, request_count=4, block_ratio=0.10, seed=7)
    game = GameGenerator(config).generate()

    kinds = [cell.kind for row in game.board.grid for cell in row]
    assert kinds.count(CellKind.BLOCKED) <= int(12 * 12 * 0.10)


def test_every_connector_has_three_free_neighbors():
    for seed in range(15):
        config = GameConfig(rows=10, cols=10, request_count=4, block_ratio=0.10, seed=seed)
        game = GameGenerator(config).generate()

        for connector_id, coord in game.board.find_connectors().items():
            free = sum(
                1
                for neighbor in game.board.neighbors(*coord)
                if game.board.cell(*neighbor).kind
                not in (CellKind.BLOCKED, CellKind.CONNECTOR)
            )
            assert free >= 3, f"conector {connector_id} en {coord} (seed {seed})"


def test_bridges_are_not_on_edge_and_not_next_to_blocks():
    config = GameConfig(
        rows=12, cols=12, request_count=4, bridge_ratio=0.15, block_ratio=0.10, seed=3
    )
    game = GameGenerator(config).generate()
    board = game.board

    bridges = [
        (r, c)
        for r in range(board.rows)
        for c in range(board.cols)
        if board.cell(r, c).kind == CellKind.BRIDGE
    ]
    assert bridges, "la configuración pedía puentes y no se generó ninguno"

    for row, col in bridges:
        assert 0 < row < board.rows - 1 and 0 < col < board.cols - 1
        assert all(
            board.cell(*neighbor).kind != CellKind.BLOCKED
            for neighbor in board.neighbors(row, col)
        )


def test_requests_never_exceed_half_the_connectors():
    config = GameConfig(rows=10, cols=10, request_count=5, seed=11)
    game = GameGenerator(config).generate()

    assert len(game.requests) <= len(game.board.find_connectors()) / 2


def test_generated_requests_are_initially_solvable():
    config = GameConfig(rows=12, cols=12, request_count=5, block_ratio=0.10, seed=5)
    game = GameGenerator(config).generate()
    connectors = game.board.find_connectors()

    for request in game.requests:
        path = find_any_path(
            game.board, connectors[request[0]], connectors[request[1]], connection_id=1
        )
        assert path is not None, f"solicitud {request} sin solución en el tablero inicial"


# ----------------------------------------------------------- ciclo de juego


def test_start_returns_api_shaped_payload():
    sim = GameSimulator(partial_delivery_config(rows=8, cols=8, request_count=3, seed=2))
    payload = sim.start()

    assert payload["game_id"] == 1
    assert set(payload) == {"game_id", "board", "params", "requests"}
    assert payload["params"]["mode"] == "survival"
    assert payload["params"]["request_window_size"] == 1
    assert len(payload["requests"]) == 1  # ventana de tamaño 1


def test_window_size_limits_visible_requests():
    sim = GameSimulator(
        GameConfig(rows=12, cols=12, request_count=5, request_window_size=3, seed=4)
    )
    payload = sim.start()

    assert len(payload["requests"]) == 3


def test_valid_move_advances_the_game():
    sim = GameSimulator(partial_delivery_config(rows=10, cols=10, request_count=4, seed=6))
    payload = sim.start()

    result = _play_one_move(sim, payload)

    assert result["valid"] is True
    assert result["completed_requests"] == 1
    assert result["current_turn"] == 2


def test_full_game_reaches_completion_and_scores():
    sim = GameSimulator(partial_delivery_config(rows=12, cols=12, request_count=4, seed=8))
    payload = sim.start()

    result = None
    for _ in range(10):
        result = _play_one_move(sim, payload)
        assert result["valid"] is True, result.get("error")
        if result["completed"]:
            break
        payload = {"board": result["board"], "requests": result["requests"],
                   "current_turn": result["current_turn"]}

    assert result["completed"] is True
    assert result["status"] == "completed"
    assert "score" in result
    assert 0 < result["score"] <= 100  # 4 solicitudes -> máximo 100


def test_invalid_move_fails_the_session():
    sim = GameSimulator(partial_delivery_config(rows=8, cols=8, request_count=3, seed=9))
    payload = sim.start()
    board = Board.from_string(payload["board"])

    # Marcar una celda suelta que no conecta nada.
    free = next(
        (r, c)
        for r in range(board.rows)
        for c in range(board.cols)
        if board.cell(r, c).kind == CellKind.NORMAL
    )
    board.cell(*free).occupy(1)

    result = sim.move(board.to_string())

    assert result["valid"] is False
    assert result["session_status"] == "failed"
    assert sim.status == "failed"
    assert sim.score == 0


def test_move_rejects_modifying_a_previous_connection():
    sim = GameSimulator(partial_delivery_config(rows=10, cols=10, request_count=4, seed=6))
    payload = sim.start()
    first = _play_one_move(sim, payload)
    assert first["valid"] is True

    # Borrar la conexión ya construida es ilegal en Supervivencia.
    tampered = Board.from_string(first["board"])
    for row in tampered.grid:
        for cell in row:
            cell.vacate(1)

    result = sim.move(tampered.to_string())

    assert result["valid"] is False


def test_move_before_start_is_rejected():
    sim = GameSimulator(partial_delivery_config(rows=8, cols=8, request_count=2, seed=1))

    result = sim.move("N")

    assert result["valid"] is False


def test_status_reports_score_after_completion():
    sim = GameSimulator(partial_delivery_config(rows=12, cols=12, request_count=3, seed=8))
    payload = sim.start()

    for _ in range(10):
        result = _play_one_move(sim, payload)
        assert result["valid"] is True
        if result["completed"]:
            break
        payload = {"board": result["board"], "requests": result["requests"],
                   "current_turn": result["current_turn"]}

    status = sim.get_status()
    assert status["status"] == "completed"
    assert status["score"] == sim.score


def test_reset_restarts_the_session():
    sim = GameSimulator(partial_delivery_config(rows=10, cols=10, request_count=3, seed=6))
    payload = sim.start()
    _play_one_move(sim, payload)

    result = sim.reset()

    assert result["reset"] is True
    assert sim.status == "created"


def test_reset_is_refused_when_not_allowed():
    config = partial_delivery_config(
        rows=8, cols=8, request_count=2, seed=1, allow_player_resets=False
    )
    sim = GameSimulator(config)
    sim.start()

    assert sim.reset()["reset"] is False


def test_potential_bridges_transform_over_time():
    config = GameConfig(
        rows=14,
        cols=14,
        request_count=6,
        potential_ratio=0.08,
        bridge_probability=1.0,  # transformación garantizada cada ronda
        seed=13,
    )
    sim = GameSimulator(config)
    payload = sim.start()
    before = sim._params()["potential_count"]
    assert before > 0

    result = _play_one_move(sim, payload)
    assert result["valid"] is True

    assert sim._params()["potential_count"] == before - 1
