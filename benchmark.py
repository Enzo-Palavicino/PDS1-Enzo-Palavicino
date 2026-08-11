"""Compara estrategias entre sí y guarda los resultados en disco.

Uso:
    ./.venv/bin/python benchmark.py                  # todas las estrategias, torneos 2 y 3
    ./.venv/bin/python benchmark.py --games 14 18 25 # sólo esas partidas
    ./.venv/bin/python benchmark.py --strategies shortest bidirectional
    ./.venv/bin/python benchmark.py --report         # sólo re-imprime lo ya guardado

Los resultados se acumulan en `results/benchmark.json`, así que cada corrida
suma evidencia en vez de reemplazarla. El objetivo es ir descartando estrategias
que no rinden y quedarse con las mejores para iterar sobre ellas.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from gridlink.api_client import GridLinkClient, RemoteGame
from gridlink.game_runner import play_game
from gridlink.retry_player import RetryConfig, play_with_retries
from gridlink.scoring import max_score
from gridlink.strategies import DISCARDED, STRATEGIES

# Por defecto se corre sólo el banco activo, pero las descartadas siguen siendo
# invocables por nombre para poder re-medirlas.
AVAILABLE = STRATEGIES | DISCARDED

# "retry" no es una estrategia de camino sino un jugador completo, con su propio
# ciclo de reintentos; se corre por defecto porque es el mejor individual (1657).
PLAYERS = [*STRATEGIES, "retry"]

RESULTS = Path(__file__).resolve().parent / "results" / "benchmark.json"


def load_results() -> list[dict]:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text())
    return []


def save_results(rows: list[dict]) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(rows, indent=1))


def run(strategy_names: list[str], game_ids: list[int] | None) -> list[dict]:
    client = GridLinkClient()
    catalogue = {game["id"]: game for game in client.list_games()}
    targets = game_ids or sorted(catalogue)
    rows = load_results()
    stamp = datetime.now(timezone.utc).isoformat()

    for name in strategy_names:
        print(f"\n=== {name} ===", flush=True)
        for game_id in targets:
            game = catalogue[game_id]
            client.reset_game(game_id)
            started = time.time()
            if name == "retry":
                # El jugador de reintentos no es una estrategia de camino sino un
                # ciclo completo de partida, así que tiene su propia entrada.
                result = play_with_retries(
                    RemoteGame(client, game_id),
                    game_id=game_id,
                    config=RetryConfig(time_budget=40.0),
                )
            else:
                result = play_game(
                    RemoteGame(client, game_id),
                    game_id=game_id,
                    strategy=AVAILABLE[name],
                )
            ceiling = max_score(game["request_count"])
            rows.append(
                {
                    "timestamp": stamp,
                    "strategy": name,
                    "game_id": game_id,
                    "rows": game["rows"],
                    "cols": game["cols"],
                    "request_count": game["request_count"],
                    "completed": result.completed_requests,
                    "score": result.score or 0,
                    "ceiling": ceiling,
                    "elapsed": round(time.time() - started, 2),
                    "error": result.error,
                }
            )
            flag = f"  ERROR: {result.error}" if result.error else ""
            print(
                f"  id={game_id:>3} {result.completed_requests:>3}/{game['request_count']:<3}"
                f"  score={str(result.score or 0):>4}/{ceiling}{flag}",
                flush=True,
            )
    save_results(rows)
    return rows


def report(rows: list[dict]) -> None:
    """Mejor puntaje por (estrategia, partida) sobre todo el historial acumulado."""
    best: dict[tuple[str, int], dict] = {}
    for row in rows:
        key = (row["strategy"], row["game_id"])
        if key not in best or row["score"] > best[key]["score"]:
            best[key] = row

    strategies = sorted({key[0] for key in best})
    games = sorted({key[1] for key in best})
    ceilings = {row["game_id"]: row["ceiling"] for row in rows}

    print("\n" + "=" * 78)
    print("MEJOR PUNTAJE POR ESTRATEGIA Y PARTIDA (histórico acumulado)")
    print("=" * 78)
    header = "estrategia    " + "".join(f"{gid:>6}" for gid in games) + "   TOTAL"
    print(header)
    print(f"{'techo':<14}" + "".join(f"{ceilings.get(g, 0):>6}" for g in games)
          + f"{sum(ceilings.get(g, 0) for g in games):>8}")
    print("-" * len(header))

    totals = []
    for name in strategies:
        scores = [best.get((name, gid), {}).get("score", 0) for gid in games]
        total = sum(scores)
        totals.append((total, name))
        print(f"{name:<14}" + "".join(f"{s:>6}" for s in scores) + f"{total:>8}")

    print("-" * len(header))
    combined = sum(
        max(best.get((name, gid), {}).get("score", 0) for name in strategies)
        for gid in games
    )
    print(f"{'MEJOR DE TODAS':<14}" + "".join(
        f"{max(best.get((n, g), {}).get('score', 0) for n in strategies):>6}" for g in games
    ) + f"{combined:>8}")

    print("\nRanking por total propio:")
    for total, name in sorted(totals, reverse=True):
        print(f"  {name:<14} {total}")
    print(
        f"\nCombinando lo mejor de cada estrategia por partida: {combined} puntos "
        f"(el servidor guarda el mejor puntaje por partida, así que esto es lo que "
        f"realmente queda registrado)."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="*", default=PLAYERS)
    parser.add_argument("--games", nargs="*", type=int, default=None)
    parser.add_argument("--report", action="store_true", help="sólo mostrar lo guardado")
    args = parser.parse_args()

    rows = load_results() if args.report else run(args.strategies, args.games)
    report(rows)


if __name__ == "__main__":
    main()
