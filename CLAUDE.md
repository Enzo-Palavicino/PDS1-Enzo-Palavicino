# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Read `ESTADO_PROYECTO.md` first.** It holds the current state, measured scores against the real
> server, the diagnosis of where points are being lost, experiments already tried (so they are not
> repeated), and the prioritized next steps.

## Project status

Python implementation in progress, targeting the partial delivery (survival mode, request
window 1, no potential bridges). **Never create git commits in this repo** — the user handles
all commits.

Environment and commands:
```bash
python3 -m venv .venv && ./.venv/bin/python -m pip install pytest   # one-time setup
./.venv/bin/python -m pytest -q                                     # all tests
./.venv/bin/python -m pytest tests/test_rules.py -q                 # one file
./.venv/bin/python -m pytest tests/test_rules.py::test_order_path_returns_ordered_path  # one test
```

Modules in `gridlink/` (built in this order; later ones not written yet):
- `board.py` — `Cell`/`Board`: parses and serializes the board string, tracks which connection(s)
  occupy each cell, exposes neighbors/connectors, applies a computed path via `mark_path`.
- `rules.py` — **single source of truth for legality**, deliberately shared by the simulator and
  (later) the pre-submit validator so the two cannot diverge. Holds `extract_added_cells`
  (survival-only: forbids touching prior connections), `check_capacity`, `order_path`
  (backtracking check that marked cells form exactly one simple path), bridge side rules, and
  `find_any_path` (BFS baseline, also used for survival end-of-game detection).
- `scoring.py` — the normalized scoring from `Proyecto_1_Ajustes_calculo_puntaje.md`.
- `simulator.py` — local stand-in for the server. `GameGenerator` builds boards that satisfy every
  constraint below and guarantees solvability by carving disjoint paths first and only then
  placing blocks around them; `GameSimulator` exposes `start`/`get_status`/`move`/`reset` with the
  same payload shapes as the real API, so the real client can be swapped in later.
- Still to build: `pathfinder.py` (strategy layer above `find_any_path`), `move_validator.py`
  (thin wrapper over `rules.py`), `api_client.py`, `game_runner.py`, `tournament_runner.py`.

### Verified behavior of the real API
Confirmed empirically against the live server on 2026-08-10 (game 12). Do not re-derive these from
the docs — the docs' examples are illustrative and mutually inconsistent.

1. **`current_turn` is a lagging counter equal to `completed_requests`**, not the round you are
   about to play. It is `0` before the first move. The connection id to submit is
   `completed_requests + 1`, so the first connection is `K1`. Submitting `K<current_turn>` directly
   would send `K0` and lose the game.
2. **Paths may not touch themselves.** Two non-consecutive cells of a path cannot be orthogonal
   neighbors, even though no cell is reused. The server rejects these with the misleading message
   "La conexión debe comenzar y terminar en sus conectores" (its path reconstruction is
   degree-based and becomes ambiguous). `order_path` enforces this via a degree check. Note a plain
   BFS shortest path can never self-touch — an adjacency between non-consecutive cells would imply
   a shortcut — but a *weighted* path can, so the pathfinder must check explicitly.
3. **Invalid moves return HTTP 400** with `{valid: false, error, session_status: "failed"}` in the
   body, not HTTP 200. `api_client._parse` returns that body instead of raising, since it is game
   information rather than a transport error.
4. **End by blockage** arrives as `status: "completed"` with `blocked: true` plus `score`.
5. **Scoring matches `scoring.py` exactly** — 3 of 5 requests returned `score: 60` (100 × 3/5).
6. **The request sequence is fixed and deterministic per game**: after a reset the same game replays
   the identical sequence, so games can be replayed and optimized offline.
7. **`has_verified_solution: false`** on the real games — unlike `simulator.py`'s generator, the
   server does *not* guarantee every request is solvable, so local scores are optimistic.
8. **Undocumented response fields**: `is_best_score` on a completed game (the server keeps the best
   score per game, so retries are allowed and worth doing) and `completed_request_indices` on
   status.
9. **`request_count` is usually *less* than `connector_count / 2`** — measured across all 16
   partial-delivery games, it holds with equality in only 4 of them (ids 12, 17, 20, 24). In id 18,
   40 of the 160 connectors are never requested; in id 26, 24 of 80. **Many connectors are
   expendable, and there is no way to know which** until the requests arrive. This invalidates the
   premise stated at the top of `pathfinder.py`, which is why every connector-protection heuristic
   there measures *worse* than a plain BFS.
10. **Games are not guaranteed solvable, and some are provably not.** Game 14's initial board has a
    27-cell free region disconnected from the main 120-cell one; 4 connectors live only in the
    island and 16 only in the mainland, so any request pairing the two groups is impossible from
    turn zero. 16 structurally distinct round-1 paths (7 to 21 steps) were submitted to the server
    and every one ended the game immediately. **15/200 is game 14's ceiling, not a bug.**
11. **Most games do have bridges** (`bridge_count` between 6% and 20% of cells in 12 of 16 games;
    only ids 12, 19, 22, 24 have none). Bridge count does not correlate with our score — id 20 is
    20% bridges and scores 100%. The **bridge side rule** is still unverified (interpreted
    conservatively in `rules.py` as "two connections sharing a bridge must use disjoint sides");
    no move has yet been rejected because of it, but that is weak evidence. Re-check before the
    final delivery.

## What this project is

ICC4201 (Proyecto Desarrollo de Software) Proyecto 1: implement a bot that plays **GridLink**, a
path-connection puzzle (similar to Numberlink/Flow Free) against a remote game server, over a
provided HTTP API. The bot runs in batches over many games ("tournament mode") and must
maximize cumulative score. Full rules are in `enunciado/proyecto1v2.pdf`; the API contract is in
`enunciado/GridLink API - Docs.md`; a scoring amendment is in
`enunciado/Proyecto_1_Ajustes_calculo_puntaje.md`.

Key deadlines (see PDF §4 for full grading breakdown):
- Partial delivery: Wed Aug 12 — Survival mode only, request window always 1, no potential bridges.
- Final delivery: Wed Aug 26 — full competition, all modes/parameters, evaluated live at the university.
- Post-mortem report: Wed Sep 2.

**Hard operational constraint: a batch of 10 games must complete in ≤10 minutes end-to-end.**
Keep the decision algorithm fast enough to respect this under real network latency to the API.

## Game rules (GridLink)

### Board format
The board is a string: cells separated by `,`, rows separated by `|`. Cell codes:
- `N` — normal cell
- `L` — blocked cell (impassable, never usable by any connection)
- `P` — potential bridge (behaves like `N` until transformed)
- `B` — free bridge (can carry up to 2 connections simultaneously, each must enter/exit through different sides)
- `C1`, `C2`, ... — connectors (endpoints revealed at game start; which pairs must connect is NOT known upfront)
- `K1`, `K2`, ... — a normal cell currently used by the path for connection/round `1`, `2`, ...
- `B:1`, `B:1+2` — a bridge cell currently used by connection 1, or by both 1 and 2

### Board & connectors
- Rectangular board, 8×8 to 30×40, all cells visible from the start.
- Connector pairing is unknown until requests are revealed round by round.
- Each connector participates in exactly one connection for the whole game; connections cannot cross through a connector.
- Every connector has at least 3 free orthogonal neighbors (no blocks/other connectors) — guaranteed by generation, useful as a sanity check.

### Blocks & bridges
- Blocks: ≤10% of cells, never usable.
- Bridges: ≤20% of cells, hold up to 2 connections, each entering/exiting via distinct sides; never on the board edge; never orthogonally adjacent to a block.
- Potential bridges (`P`): ≤10% of cells, same placement constraints as bridges, act as normal cells until transformed. After each completed round, at most one potential bridge may randomly transform into a real bridge (probability is a per-game parameter). If the transforming cell was already part of a connection, that connection is untouched and the cell gains capacity for a second connection.

### Connections (paths)
- Connects exactly two connectors from the same request.
- Must be continuous, orthogonal moves only (no diagonals), no branching.
- Cannot pass through blocks or other connectors.
- A normal cell belongs to at most one connection; a bridge cell can belong to two (entering/exiting on different sides for each).

### Requests
- Each game has a fixed request sequence; total requests ≤ half the number of connectors.
- Request window size is 1, 2, or 3 (fixed per game). The player may pick any visible request in the window each round; completing one slides in the next pending request.

### Game modes
- **Menos celdas (fewest cells)**: complete all requests minimizing final cost. Before building a new connection, the player may freely redraw any existing connections, but previously completed requests must remain validly connected at the end of every round.
  - `costo = celdas_finales + N × celdas_eliminadas`, where `N ∈ {0.5, 1, 1.5, 2}` is a per-game penalty parameter. A bridge cell used by two connections counts once toward `celdas_finales`. Cells removed in a round count as removed even if reused later.
  - Ends when all requests are completed.
- **Supervivencia (survival)**: maximize number of completed requests. Connections are permanent once built (no redraws). Ends when none of the currently visible requests can be validly connected.

### Scoring (current formulas — see `enunciado/Proyecto_1_Ajustes_calculo_puntaje.md`, which supersedes the PDF's per-game formulas)
Max points per game, based on total requests in that game:
- ≤10 requests → 100
- 11–30 requests → 200
- \>30 requests → 300

- **Survival**: `puntaje = máximo × solicitudes_completadas / solicitudes_totales`, rounded to an integer.
- **Menos celdas**: compare against the game's stored verified reference solution's cost:
  `puntaje = máximo × costo_referencia / costo_jugador`, rounded, capped at `máximo` (matching or beating the reference solution yields the max).

An invalid move, or failing to move within the time limit, ends the game immediately with 0 points.

## API contract

Base URL: `https://gridlink-app-imzah.ondigitalocean.app/`. Full docs in
`enunciado/GridLink API - Docs.md`.

- Auth: every endpoint except `GET /api/health/` requires header `X-API-Key: <key>`.
- `GET /api/health/` — liveness check, public.
- `GET /api/games/` (optional `?tournament_id=<id>`) — list available games (id, mode, rows, cols, request_count).
- `GET /api/tournaments/` — list tournaments available to the authenticated player.
- `POST /api/games/<game_id>/start/` — create a session for this game (one active session per player per game). Returns initial `board`, `params` (mode, dims, connector/request/block/bridge/potential counts, bridge_probability, request_window_size, fewest_cells_penalty, allow_player_resets, has_verified_solution), and `requests` (visible connector-index pairs).
- `GET /api/games/<game_id>/status/` — recover current session state (board, params, requests, current_turn, completed_requests, removed_cells_total, timestamps; includes `score` once completed). Useful if local state is lost.
- `POST /api/games/<game_id>/moves/` — submit body `{"board": "<new board string>"}` representing the full board after building/editing a connection (paths use `K<request_round_number>`). Response includes `valid`, `completed`, `blocked`, updated `board`/`requests`/`current_turn`/`completed_requests`/`removed_cells_count`/`removed_cells_total`/`status`, and `score` once `status == "completed"`. On an invalid move: `{"valid": false, "error": "...", "session_status": "failed"}`.
- `POST /api/games/<game_id>/reset/` — clears the player's session for that game; only works if the game allows resets (`allow_player_resets`).

Each move submits the *entire* board string reflecting the desired new state, not a diff — the
client is responsible for serializing the full board (including all `K`/`B:` markers) after
computing a path.

## Working on this repo

Since there is no code yet, when implementation begins:
- Choose and document the language/stack, then add real build/test/lint commands to this file.
- Isolate the board (de)serialization, the game-rules/validity checker, the pathfinding/strategy
  logic, and the HTTP client into separate, independently testable units — the scoring formulas
  and validity rules above are exactly the kind of thing that should have unit tests, since a
  single invalid move zeroes out a game's score.
- Keep an eye on the 10-minutes-per-10-games budget when choosing algorithms (e.g., pathfinding
  approach, how much lookahead/backtracking is done per move).
