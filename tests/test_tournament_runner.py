import json
import time

from gridlink.tournament_runner import (
    DEFAULT_PLAYER,
    FAST_PLAYER,
    TournamentConfig,
    run_batch,
)


class FakeGameResult:
    def __init__(self, score, completed=1, total=4, error=None):
        self.score = score
        self.completed_requests = completed
        self.total_requests = total
        self.error = error


class FakeClient:
    """Cliente falso: registra qué se jugó y cuánto presupuesto se le dio."""

    def __init__(self, games, scores=None, delay=0.0, explode=()):
        self._games = games
        self._scores = scores or {}
        self._delay = delay
        self._explode = set(explode)
        self.calls = []
        self.resets = []

    def list_games(self, tournament_id=None):
        return self._games

    def reset_game(self, game_id):
        self.resets.append(game_id)


def _patch(monkeypatch, client):
    """Sustituye la jugada real por una que sólo registra e inventa puntaje."""
    from gridlink import tournament_runner

    def fake_play(_client, game_id, player, budget):
        client.calls.append((game_id, player, round(budget, 1)))
        if game_id in client._explode:
            raise RuntimeError("caída de red simulada")
        if client._delay:
            time.sleep(client._delay)
        return FakeGameResult(client._scores.get((game_id, player), 10))

    monkeypatch.setattr(tournament_runner, "_play", fake_play)


GAMES = [
    {"id": 1, "rows": 8, "cols": 8, "request_count": 5},
    {"id": 2, "rows": 30, "cols": 40, "request_count": 90},
    {"id": 3, "rows": 16, "cols": 8, "request_count": 9},
]


def test_plays_smallest_games_first(monkeypatch):
    client = FakeClient(GAMES)
    _patch(monkeypatch, client)

    run_batch(client, config=TournamentConfig(improve_with_leftover=False), verbose=False)

    assert [call[0] for call in client.calls] == [1, 3, 2]


def test_budget_is_shared_and_grows_as_games_finish(monkeypatch):
    client = FakeClient(GAMES, delay=0.05)
    _patch(monkeypatch, client)

    run_batch(
        client,
        config=TournamentConfig(
            total_budget=300.0, max_game_budget=1000.0, improve_with_leftover=False
        ),
        verbose=False,
    )

    budgets = [call[2] for call in client.calls]
    # Cada partida que termina antes le dona su sobrante a las siguientes.
    assert budgets == sorted(budgets), f"el presupuesto debería crecer, fue {budgets}"
    assert budgets[-1] > budgets[0], "la última debería recibir más que la primera"


def test_no_game_gets_more_than_the_cap(monkeypatch):
    """Sin techo, las partidas rápidas inflan la cuota de las últimas y se quema
    reloj: `retry` se estanca cerca de los 40 s (medido en vivo en id 23)."""
    client = FakeClient(GAMES)
    _patch(monkeypatch, client)

    run_batch(
        client,
        config=TournamentConfig(
            total_budget=3000.0, max_game_budget=45.0, improve_with_leftover=False
        ),
        verbose=False,
    )

    assert all(call[2] <= 45.0 for call in client.calls), client.calls


def test_falls_back_to_the_fast_player_when_time_is_short(monkeypatch):
    client = FakeClient(GAMES)
    _patch(monkeypatch, client)

    run_batch(
        client,
        config=TournamentConfig(total_budget=20.0, improve_with_leftover=False),
        verbose=False,
    )

    assert {call[1] for call in client.calls} == {FAST_PLAYER}


def test_uses_the_preferred_player_when_the_game_is_known(monkeypatch):
    client = FakeClient(GAMES)
    _patch(monkeypatch, client)

    run_batch(
        client,
        preferred={3: "shortest_turns"},
        config=TournamentConfig(improve_with_leftover=False),
        verbose=False,
    )

    chosen = {call[0]: call[1] for call in client.calls}
    assert chosen[3] == "shortest_turns"
    assert chosen[1] == DEFAULT_PLAYER


def test_a_broken_game_does_not_stop_the_batch(monkeypatch):
    client = FakeClient(GAMES, explode={3})
    _patch(monkeypatch, client)

    result = run_batch(
        client, config=TournamentConfig(improve_with_leftover=False), verbose=False
    )

    assert result.played == 3
    broken = [o for o in result.outcomes if o.game_id == 3][0]
    assert "caída de red simulada" in broken.error
    assert all(o.error is None for o in result.outcomes if o.game_id != 3)


def test_never_exceeds_the_global_budget(monkeypatch):
    client = FakeClient(GAMES, delay=0.2)
    _patch(monkeypatch, client)

    started = time.monotonic()
    result = run_batch(
        client,
        config=TournamentConfig(total_budget=1.0, improve_with_leftover=False),
        verbose=False,
    )

    assert time.monotonic() - started < 3.0
    assert result.elapsed < 3.0


def test_improvement_restores_the_best_session_when_the_retry_is_worse(monkeypatch):
    """Si el reintento saca menos, hay que volver a dejar vigente el mejor."""
    client = FakeClient(
        GAMES,
        scores={(1, DEFAULT_PLAYER): 90, (1, FAST_PLAYER): 10,
                (2, DEFAULT_PLAYER): 90, (3, DEFAULT_PLAYER): 90},
    )
    _patch(monkeypatch, client)

    # El presupuesto se escala por cantidad de partidas, así que hay que pedir
    # suficiente para que la fase de mejora alcance a correr.
    result = run_batch(client, config=TournamentConfig(total_budget=600.0), verbose=False)

    worst = [o for o in result.outcomes if o.game_id == 1][0]
    assert worst.score == 90, "no puede quedar registrado el puntaje peor"
    assert worst.restored, "debía re-jugarse el jugador bueno para dejarlo vigente"
    # La partida 1 se juega, se reintenta con el peor, y se restaura el bueno.
    players = [call[1] for call in client.calls if call[0] == 1]
    assert players == [DEFAULT_PLAYER, FAST_PLAYER, DEFAULT_PLAYER]


def test_writes_a_log_for_the_post_mortem(monkeypatch, tmp_path):
    client = FakeClient(GAMES)
    _patch(monkeypatch, client)

    run_batch(
        client,
        tournament_id=3,
        config=TournamentConfig(improve_with_leftover=False),
        log_dir=tmp_path,
        verbose=False,
    )

    logs = list(tmp_path.glob("torneo3_*.json"))
    assert len(logs) == 1
    payload = json.loads(logs[0].read_text())
    assert payload["played"] == 3
    assert len(payload["outcomes"]) == 3
    assert payload["outcomes"][0]["player"]


def test_a_game_the_player_abstains_from_is_rescued(monkeypatch):
    """`retry` se abstiene si la partida no permite resets. Esa partida NO puede
    quedar en 0: el runner debe rejugarla con el jugador simple."""
    client = FakeClient(GAMES, scores={(1, DEFAULT_PLAYER): 0, (1, FAST_PLAYER): 70})
    _patch(monkeypatch, client)

    result = run_batch(
        client, config=TournamentConfig(improve_with_leftover=False), verbose=False
    )

    rescued = [o for o in result.outcomes if o.game_id == 1][0]
    assert rescued.score == 70, "la partida abstenida debía rescatarse"
    assert rescued.player == FAST_PLAYER
    assert rescued.fallback_from == DEFAULT_PLAYER


def test_a_crashing_player_is_rescued_too(monkeypatch):
    client = FakeClient(GAMES, explode={3})
    _patch(monkeypatch, client)
    from gridlink import tournament_runner

    original = tournament_runner._play

    def fake(cli, gid, player, budget):
        if gid == 3 and player == DEFAULT_PLAYER:
            raise RuntimeError("caída de red simulada")
        cli.calls.append((gid, player, round(budget, 1)))
        return FakeGameResult(50)

    monkeypatch.setattr(tournament_runner, "_play", fake)
    result = run_batch(
        client, config=TournamentConfig(improve_with_leftover=False), verbose=False
    )

    crashed = [o for o in result.outcomes if o.game_id == 3][0]
    assert crashed.score == 50, "un jugador que revienta debe caer al simple, no quedar en 0"
    assert crashed.player == FAST_PLAYER


def test_budget_scales_with_the_number_of_games(monkeypatch):
    """El límite es una tasa (10 min por 10 partidas) y la entrega parcial se
    juega en lotes de 8, así que un lote chico no puede quedarse con el total."""
    client = FakeClient(GAMES)
    _patch(monkeypatch, client)

    result = run_batch(
        client,
        config=TournamentConfig(total_budget=600.0, improve_with_leftover=False),
        verbose=False,
    )

    assert result.budget == 180.0, "3 partidas deberían recibir 3/10 del presupuesto"
