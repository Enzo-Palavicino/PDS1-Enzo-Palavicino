"""Corre un lote de competencia bajo presupuesto de tiempo global.

    ./.venv/bin/python tournament.py --tournament 3
    ./.venv/bin/python tournament.py --tournament 3 --budget 400 --no-improve
    ./.venv/bin/python tournament.py --games 20 21 22 --dry-run

A diferencia de `benchmark.py`, esto es la herramienta de *competencia*: respeta
el límite de 10 partidas en 10 minutos, aísla fallos por partida y deja un log en
`results/logs/` para el post-mortem. Ver `gridlink/tournament_runner.py`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gridlink.api_client import GridLinkClient
from gridlink.tournament_runner import TournamentConfig, run_batch

ROOT = Path(__file__).resolve().parent
PLAN = ROOT / "results" / "consolidation_plan.json"


def load_preferred() -> dict[int, str]:
    """Estrategia ya conocida como ganadora de cada partida, si existe el plan."""
    if not PLAN.exists():
        return {}
    return {
        int(game_id): info["strategy"]
        for game_id, info in json.loads(PLAN.read_text()).items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tournament", type=int, default=None)
    parser.add_argument("--games", nargs="*", type=int, default=None)
    parser.add_argument(
        "--budget", type=float, default=None, help="segundos de pared para todo el lote"
    )
    parser.add_argument(
        "--no-improve", action="store_true", help="no reinvertir el tiempo sobrante"
    )
    parser.add_argument(
        "--no-plan",
        action="store_true",
        help="ignorar el plan de consolidación (simula partidas nuevas)",
    )
    args = parser.parse_args()

    config = TournamentConfig(improve_with_leftover=not args.no_improve)
    if args.budget is not None:
        config.total_budget = args.budget

    client = GridLinkClient()
    preferred = {} if args.no_plan else load_preferred()

    print(f"presupuesto base: {config.total_budget:.0f}s para 10 partidas "
          f"(se escala al tamaño del lote)   plan: {len(preferred)} partidas conocidas")
    result = run_batch(
        client,
        game_ids=args.games,
        tournament_id=args.tournament,
        config=config,
        preferred=preferred,
        log_dir=ROOT / "results" / "logs",
    )

    print(f"\npuntaje del lote: {result.total_score}")
    print(f"partidas jugadas: {result.played}   sin jugar: {result.skipped or 'ninguna'}")
    print(f"tiempo total: {result.elapsed:.1f}s de {result.budget:.0f}s permitidos "
          f"para {result.played} partidas")
    if result.elapsed > result.budget:
        print("  ATENCIÓN: se pasó del presupuesto")
    errores = [o for o in result.outcomes if o.error]
    if errores:
        print(f"partidas con error: {[(o.game_id, o.error) for o in errores]}")


if __name__ == "__main__":
    main()
