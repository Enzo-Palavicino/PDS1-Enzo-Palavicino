"""Corrida de un lote de partidas bajo un presupuesto de tiempo global.

**No es un benchmark.** `benchmark.py` mide estrategias y tarda lo que haga
falta; esto juega en competencia, donde la restricción dura es *10 partidas en
10 minutos de punta a punta* y quedarse sin tiempo con partidas sin jugar cuesta
entre 100 y 300 puntos por partida.

Las cuatro decisiones de diseño y su porqué:

1. **Presupuesto global, no por partida.** Un tope por partida no respeta la
   restricción: 10 × 55 s ya son 9 minutos sin contar red ni arranque. Acá el
   presupuesto se reparte sobre la marcha (`tiempo restante / partidas
   restantes`), así que **cada partida que termina antes le dona su sobrante a
   las siguientes**. Las chicas terminan en segundos y financian a las grandes.

2. **Degradación elegante.** Si a una partida le toca menos tiempo del que
   `retry` necesita, se juega con una estrategia rápida. `retry` rinde 1657 y
   `shortest` 1582: la diferencia es chica al lado de dejar una partida en 0 por
   quedarse sin reloj.

3. **Aislamiento de fallos.** Cada partida va en su propio `try`; un error de red
   en la cuarta no puede impedir que se jueguen la quinta a la décima.

4. **Orden ascendente por cantidad de solicitudes.** Las partidas chicas se
   juegan primero: son rápidas, casi siempre las ganamos completas, y aseguran
   puntos temprano mientras donan tiempo a las caras. Si el reloj se acaba, lo
   que queda sin jugar son las partidas donde igual rendíamos 25-40%.

Y una regla que sale de lo verificado el 2026-08-10: la API no permite saber si
el torneo cuenta la mejor sesión o la última, así que **toda repetición de una
partida ya jugada debe terminar dejando vigente la mejor**. Lo aplica la fase de
mejora al final.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .api_client import RemoteGame
from .game_runner import play_game
from .retry_player import RetryConfig, play_with_retries
from .strategies import DISCARDED, STRATEGIES

PLAYERS = STRATEGIES | DISCARDED

# Jugador por omisión en partidas nunca vistas: el mejor individual medido.
DEFAULT_PLAYER = "retry"
# Jugador de emergencia cuando queda poco tiempo: determinista y de segundos.
FAST_PLAYER = "shortest"


@dataclass
class TournamentConfig:
    """Presupuestos del lote. Los valores por omisión son para 10 partidas."""

    # Presupuesto total de pared. El límite del enunciado es 600 s para 10
    # partidas. Una partida puede excederse de su cuota (`retry` chequea el reloj
    # al inicio de cada intento y después envía una línea entera, decenas de
    # llamadas HTTP en tableros grandes; medido: 7.8 s de exceso en id 25). Ese
    # exceso **no se acumula**: descuenta de `remaining` y por lo tanto achica la
    # cuota de las siguientes, así que el total está acotado por este presupuesto
    # más el exceso de una sola partida. Los 90 s de margen cubren eso incluso
    # con la red bastante más lenta que la medida (253 ms por llamada).
    total_budget: float = 510.0
    # Por debajo de esto una partida se juega con `FAST_PLAYER` en vez de `retry`:
    # `retry` necesita decenas de segundos para que sus reintentos sirvan de algo.
    fast_player_threshold: float = 18.0
    # Techo por partida. `retry` se estanca alrededor de los 40 s: medido en vivo,
    # id 23 recibió 119 s y sacó los mismos 42 puntos que saca en 1.4 s. Sin este
    # tope, las partidas que terminan rápido inflan la cuota de las últimas y el
    # lote quema el reloj sin ganar nada; con él, el sobrante llega entero a la
    # fase de mejora, que sí lo aprovecha.
    max_game_budget: float = 45.0
    # Reserva intocable: si el reloj baja de acá, se juega sólo con el rápido.
    reserve: float = 45.0
    # Con tiempo sobrante al final, reintentar las partidas peor puntuadas.
    improve_with_leftover: bool = True


@dataclass
class GameOutcome:
    game_id: int
    player: str
    score: int = 0
    completed: int = 0
    total_requests: int = 0
    elapsed: float = 0.0
    budget: float = 0.0
    error: str | None = None
    restored: bool = False
    # Jugador que falló o se abstuvo y obligó a caer al jugador simple.
    fallback_from: str | None = None


@dataclass
class BatchResult:
    started_at: str = ""
    elapsed: float = 0.0
    total_score: int = 0
    budget: float = 0.0
    played: int = 0
    skipped: list[int] = field(default_factory=list)
    outcomes: list[GameOutcome] = field(default_factory=list)


def _play(client, game_id: int, player: str, budget: float):
    """Juega una partida con el jugador indicado, dentro de su presupuesto."""
    client.reset_game(game_id)
    game = RemoteGame(client, game_id)
    if player == "retry":
        return play_with_retries(
            game, game_id=game_id, config=RetryConfig(time_budget=budget)
        )
    return play_game(game, game_id=game_id, strategy=PLAYERS[player])


def run_batch(
    client,
    game_ids: list[int] | None = None,
    tournament_id: int | None = None,
    config: TournamentConfig | None = None,
    preferred: dict[int, str] | None = None,
    log_dir: Path | None = None,
    verbose: bool = True,
) -> BatchResult:
    """Juega un lote completo respetando el presupuesto global.

    `preferred` mapea partida -> estrategia conocida como ganadora (lo que produce
    `results/consolidation_plan.json`). Sirve para los torneos ya explorados; en
    partidas nuevas no hay plan y se usa `DEFAULT_PLAYER`.
    """
    config = config or TournamentConfig()
    preferred = preferred or {}
    started = time.monotonic()
    result = BatchResult(started_at=datetime.now(timezone.utc).isoformat())

    catalogue = {game["id"]: game for game in client.list_games(tournament_id=tournament_id)}
    targets = [gid for gid in (game_ids or sorted(catalogue)) if gid in catalogue]

    # El límite del enunciado es una tasa (10 min por 10 partidas), no un total
    # fijo, y la entrega parcial se juega en **lotes de 8**. Se escala para no
    # asumir 10 partidas cuando hay menos.
    budget = min(config.total_budget, len(targets) * config.total_budget / 10)
    deadline = started + budget
    result.budget = round(budget, 1)
    # Las chicas primero: aseguran puntos temprano y donan tiempo a las caras.
    targets.sort(key=lambda gid: catalogue[gid].get("request_count", 0))

    for position, game_id in enumerate(targets):
        remaining = deadline - time.monotonic()
        pending = len(targets) - position
        if remaining <= 0:
            result.skipped.append(game_id)
            continue

        share = min(remaining / pending, config.max_game_budget)
        if share < config.fast_player_threshold or remaining < config.reserve:
            player = FAST_PLAYER
        else:
            player = preferred.get(game_id, DEFAULT_PLAYER)
            if player not in PLAYERS and player != "retry":
                player = DEFAULT_PLAYER

        outcome = GameOutcome(game_id=game_id, player=player, budget=round(share, 1))
        began = time.monotonic()
        try:
            played = _play(client, game_id, player, min(share, remaining))
            outcome.score = played.score or 0
            outcome.completed = played.completed_requests
            outcome.total_requests = played.total_requests
            outcome.error = played.error
        except Exception as error:  # noqa: BLE001 - una partida rota no puede voltear el lote
            outcome.error = f"{type(error).__name__}: {error}"

        # Red de seguridad: si el jugador falló o se abstuvo (por ejemplo `retry`
        # ante una partida sin resets permitidos, o con puentes potenciales), la
        # partida NO puede quedar en 0 — se rejuega con el jugador simple, que no
        # depende de ningún parámetro. Sin esto, un lote con parámetros distintos
        # a los explorados dejaría todas las partidas en cero.
        if (outcome.error or outcome.score == 0) and player != FAST_PLAYER:
            left = deadline - time.monotonic()
            if left > config.fast_player_threshold:
                try:
                    rescued = _play(client, game_id, FAST_PLAYER, min(left, config.max_game_budget))
                    if (rescued.score or 0) >= outcome.score:
                        outcome.score = rescued.score or 0
                        outcome.completed = rescued.completed_requests
                        outcome.total_requests = rescued.total_requests
                        outcome.fallback_from = player
                        outcome.player = FAST_PLAYER
                        outcome.error = rescued.error
                except Exception as error:  # noqa: BLE001
                    outcome.error = f"{outcome.error} | rescate falló: {error}"

        outcome.elapsed = round(time.monotonic() - began, 2)

        result.outcomes.append(outcome)
        result.total_score += outcome.score
        result.played += 1
        if verbose:
            flag = f"  ERROR: {outcome.error}" if outcome.error else ""
            print(
                f"  id={game_id:>3} {player:<14} {outcome.completed:>3}/"
                f"{outcome.total_requests:<3} score={outcome.score:>4} "
                f"({outcome.elapsed:>5.1f}s de {outcome.budget:>5.1f}s){flag}",
                flush=True,
            )

    if config.improve_with_leftover:
        _improve(client, result, deadline, config, preferred, verbose)

    result.elapsed = round(time.monotonic() - started, 2)
    if log_dir is not None:
        _write_log(log_dir, tournament_id, result)
    return result


def _improve(
    client, result: BatchResult, deadline: float, config: TournamentConfig,
    preferred: dict[int, str], verbose: bool,
) -> None:
    """Reinvierte el tiempo sobrante en las partidas peor puntuadas.

    Con la disciplina de consolidación: si el reintento saca menos, se vuelve a
    jugar con el jugador original para que la sesión vigente no quede peor que la
    que ya teníamos. Por eso sólo se intenta cuando alcanza el tiempo para ambas
    cosas.
    """
    candidates = sorted(
        (o for o in result.outcomes if not o.error and o.total_requests),
        key=lambda o: o.score / max(o.total_requests, 1),
    )
    for outcome in candidates:
        remaining = deadline - time.monotonic()
        # Hace falta espacio para el reintento y para restaurar si sale peor.
        if remaining < outcome.elapsed * 2 + config.fast_player_threshold:
            return

        alternative = FAST_PLAYER if outcome.player == "retry" else "retry"
        try:
            retried = _play(client, outcome.game_id, alternative, min(remaining / 2, 40.0))
        except Exception:  # noqa: BLE001
            continue

        score = retried.score or 0
        if score > outcome.score:
            result.total_score += score - outcome.score
            outcome.score, outcome.player = score, alternative
            outcome.completed = retried.completed_requests
            if verbose:
                print(f"  id={outcome.game_id:>3} mejorada con {alternative}: {score}", flush=True)
        elif score < outcome.score:
            # Restaurar: la sesión vigente no puede quedar peor que antes.
            try:
                _play(client, outcome.game_id, outcome.player, min(remaining / 2, 40.0))
                outcome.restored = True
            except Exception:  # noqa: BLE001
                pass


def _write_log(log_dir: Path, tournament_id: int | None, result: BatchResult) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.started_at.replace(":", "-")
    name = f"torneo{tournament_id or 'x'}_{stamp}.json"
    payload = asdict(result)
    (log_dir / name).write_text(json.dumps(payload, indent=1, ensure_ascii=False))
