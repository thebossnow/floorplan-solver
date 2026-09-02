"""
Machine-facing solve/render entry point, decoupled from the human-form
Flask route (app.py's _run_solve()). Mirrors _run_solve()'s solve-only
portion (generate_program() + default_proximity() + the zoned/unzoned
branch calling layout.solve()/zoning.solve_zoned()) minus
place_openings()/to_svg()/circulation_ok(), which render_svg() below and
validators.validate() handle separately -- see V2-ALPHA-PLAN.md's
"solve_program() / validate() / render_svg()" section.

Phase 2 scope only: this wraps solve()/solve_zoned() exactly as they work
today (v1 rule content unchanged) -- no assumption-literal diagnosis, no
ruleset, no new hard rules. Those are Phase 3/4.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from generator import (HALLWAYS, PRODUCTION_WEIGHTS, STYLES, ZONE_ROOM_THRESHOLD,
                        default_proximity, generate_program, shelf_pack_hint,
                        zone_of_program)
from layout import Adj, Footprint, Room, place_openings, solve, to_svg
from zoning import solve_zoned

# Independent copies of app.py's own tuning constants, not an import from
# it -- orchestrate.py is meant to work standalone (e.g. from a future
# API route or a script), and app.py is the Flask entry point, not a
# library other modules should depend on. Keep these in sync with
# app.py's TIME_LIMIT/NO_IMPROVEMENT_TIMEOUT/SOLVE_TIME_BUDGET by hand
# (same convention test_production_shapes.py already uses for its own
# copies of these).
TIME_LIMIT = 25.0
NO_IMPROVEMENT_TIMEOUT = 15.0
SOLVE_TIME_BUDGET = 60.0


@dataclass
class ProgramSpec:
    """Public, JSON-serializable-shaped description of what to solve --
    the orchestrate-layer counterpart to generate_program()'s own
    positional args. width replaces shape as the primary footprint
    control (matching v1's width slider); has_entry=False drops the
    separate Entry room (2026-09-02 plan review)."""
    total_area: int
    beds: int = 3
    baths: int = 2
    style: str = "traditional"
    width: Optional[int] = None
    has_entry: bool = True


@dataclass
class SolveResult:
    """Everything render_svg()/validators.validate() need, without
    re-deriving it from a ProgramSpec -- the program actually solved
    (footprint/rooms/adjacencies/private/entry_room) travels with the
    result rather than being reconstructed."""
    plan: Optional[dict]
    status: str
    footprint: Footprint
    rooms: List[Room]
    adjacencies: List[Adj]
    private: Tuple[str, ...]
    entry_room: str
    zoned: bool
    wall_time: Optional[float] = None
    objective_value: Optional[float] = None
    best_objective_bound: Optional[float] = None
    cross: Optional[Dict] = None
    zone_metrics: Optional[Dict] = None


def _entry_room_name(rooms: List[Room]) -> str:
    """The room circulation_ok() should BFS from -- "Entry" normally, or
    whichever room took over its arrival-point role when
    ProgramSpec.has_entry=False (see generate_program()'s docstring).
    Derived from the room list itself rather than duplicating style-name
    logic here, so this stays correct if a future style's own arrival
    room isn't Living/Great."""
    names = {r.name for r in rooms}
    if "Entry" in names:
        return "Entry"
    return "Great" if "Great" in names else "Living"


def solve_program(spec: ProgramSpec, seed: int = 0, workers: int = 8) -> SolveResult:
    """Runs ProgramSpec -> generate_program() -> solve()/solve_zoned(),
    no rendering. Raises ValueError for an invalid spec (unknown style,
    out-of-range area/beds/baths -- same validation generate_program()/
    layout.validate_program() already do)."""
    fp, rooms, adj, private = generate_program(
        spec.total_area, spec.beds, spec.baths, style=spec.style,
        width=spec.width, has_entry=spec.has_entry)
    entry_room = _entry_room_name(rooms)
    proximity = default_proximity(rooms)
    zoned = len(rooms) > ZONE_ROOM_THRESHOLD

    t0 = time.time()
    if zoned:
        zone_of = zone_of_program(rooms)
        plan, status, cross, zone_metrics = solve_zoned(
            fp, rooms, adj, zone_of, time_budget=SOLVE_TIME_BUDGET, seed=seed,
            workers=workers, hallways=HALLWAYS, private=private,
            weights=PRODUCTION_WEIGHTS, proximity=proximity)
        wall_time = time.time() - t0
        return SolveResult(plan=plan, status=status, footprint=fp, rooms=rooms,
                            adjacencies=adj, private=private, entry_room=entry_room,
                            zoned=True, wall_time=wall_time, cross=cross,
                            zone_metrics=zone_metrics)

    hint = shelf_pack_hint(fp, rooms)
    plan, status, objective_value, best_objective_bound, wall_time = solve(
        fp, rooms, adj, time_limit=TIME_LIMIT, seed=seed, workers=workers, hint=hint,
        hallways=HALLWAYS, private=private, no_improvement_timeout=NO_IMPROVEMENT_TIMEOUT,
        proximity=proximity, **PRODUCTION_WEIGHTS)
    return SolveResult(plan=plan, status=status, footprint=fp, rooms=rooms,
                        adjacencies=adj, private=private, entry_room=entry_room,
                        zoned=False, wall_time=wall_time, objective_value=objective_value,
                        best_objective_bound=best_objective_bound)


def render_svg(result: SolveResult, title_block: Optional[dict] = None) -> str:
    """Thin place_openings() + to_svg() wrapper, for API symmetry with
    solve_program()/validate() -- no new rendering logic. Raises
    ValueError if result.plan is None (nothing to render)."""
    if not result.plan:
        raise ValueError("cannot render: solve_program() found no plan "
                          f"(status={result.status!r})")
    openings = place_openings(result.plan, result.footprint, result.adjacencies, result.rooms)
    return to_svg(result.plan, result.footprint, path=None, openings=openings,
                  title_block=title_block)
