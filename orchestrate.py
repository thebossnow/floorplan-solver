"""
Machine-facing solve/render entry point, decoupled from the human-form
Flask route (app.py's _run_solve()). Mirrors _run_solve()'s solve-only
portion (generate_program() + default_proximity() + the zoned/unzoned
branch calling layout.solve()/zoning.solve_zoned()) minus
place_openings()/to_svg()/circulation_ok(), which render_svg() below and
validators.validate() handle separately -- see V2-ALPHA-PLAN.md's
"solve_program() / validate() / render_svg()" section.

Phase 7 update: solve_program() now exposes ruleset/diagnose_infeasibility/
time_limit, needed for POST /api/solve (Phase 2-6 only wired these into
layout.solve() itself, not this wrapper). ruleset/diagnose_infeasibility
stay unzoned-only, matching layout.solve()'s own documented scope --
zoning.solve_zoned() doesn't accept either, and threading a ruleset
through its own per-zone solve() calls (technically possible via its
`weights` dict, since that's just **kwargs) is a separate, not-yet-
scoped extension, not silently bolted on here.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from generator import (HALLWAYS, PRODUCTION_WEIGHTS, STYLES, ZONE_ROOM_THRESHOLD,
                        default_proximity, generate_program, shelf_pack_hint,
                        zone_of_program)
from layout import Adj, Footprint, InfeasibilityDiagnosis, Room, place_openings, room_kind, solve, to_svg
from rules import Ruleset
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
    separate Entry room (2026-09-02 plan review). ruleset is a real
    rules.Ruleset object here (not a JSON-shaped id string) -- api_routes.py
    resolves an incoming ruleset id to one before constructing a spec."""
    total_area: int
    beds: int = 3
    baths: int = 2
    style: str = "traditional"
    width: Optional[int] = None
    has_entry: bool = True
    ruleset: Optional[Ruleset] = None


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
    diagnosis: Optional[InfeasibilityDiagnosis] = None


def _entry_room_name(rooms: List[Room]) -> str:
    """The room circulation_ok() should BFS from -- "Entry" normally, or
    whichever room took over its arrival-point role when
    ProgramSpec.has_entry=False (see generate_program()'s docstring).
    Derived from the room list itself rather than duplicating style-name
    logic here, so this stays correct if a future style's own arrival
    room isn't Living/Great.

    Raises ValueError if none of Entry/Great/Living appear at all --
    always safe for solve_program()'s own generate_program()-produced
    rooms (one of the three is always present), but POST /api/validate
    accepts an arbitrary caller-submitted room list, where silently
    guessing "Living" (when it isn't even in the list) would produce a
    bogus circulation_ok=False instead of a clear error."""
    names = {r.name for r in rooms}
    if "Entry" in names:
        return "Entry"
    if "Great" in names:
        return "Great"
    if "Living" in names:
        return "Living"
    raise ValueError(
        "cannot determine the entry room: none of 'Entry', 'Great', or 'Living' "
        "appear in the submitted rooms")


def _private_room_names(rooms: List[Room]) -> Tuple[str, ...]:
    """Rooms circulation_ok() should treat as private (closets, ensuite
    baths -- never a hallway pass-through), inferred from room_kind() the
    same way generate_program() builds its own `private` tuple. Needed
    for POST /api/validate (Phase 7), whose request shape has no explicit
    `private` field -- see V2-ALPHA-PLAN.md's API surface."""
    return tuple(r.name for r in rooms
                 if room_kind(r.name) == "closet" or
                 (room_kind(r.name) == "wet" and r.name != "Utility"))


def solve_program(spec: ProgramSpec, seed: int = 0, workers: int = 8,
                   diagnose_infeasibility: bool = False,
                   time_limit: Optional[float] = None) -> SolveResult:
    """Runs ProgramSpec -> generate_program() -> solve()/solve_zoned(),
    no rendering. Raises ValueError for an invalid spec (unknown style,
    out-of-range area/beds/baths -- same validation generate_program()/
    layout.validate_program() already do).

    time_limit overrides this module's own TIME_LIMIT/SOLVE_TIME_BUDGET
    when given (SOLVE_TIME_BUDGET on the zoned path is a *total* budget
    across every zone, not a per-attempt cap like TIME_LIMIT -- a
    different semantic than the unzoned path, but both are "the module's
    own timing constant," so one override param covers both).

    diagnose_infeasibility/spec.ruleset only apply on the unzoned path --
    see this module's own docstring for why zoning doesn't support
    either yet. SolveResult.diagnosis is always None on the zoned path or
    when the unzoned solve doesn't come back INFEASIBLE.

    Raises ValueError up front if the program needs zoning (>
    ZONE_ROOM_THRESHOLD rooms -- routinely true for beds=5/baths=4, well
    within ProgramSpec's own valid range) while either is requested,
    rather than silently solving without them: a caller who explicitly
    asked for ruleset enforcement getting back {ok:true, ...} with the
    ruleset quietly never applied is a worse failure mode than a clear
    rejection."""
    fp, rooms, adj, private = generate_program(
        spec.total_area, spec.beds, spec.baths, style=spec.style,
        width=spec.width, has_entry=spec.has_entry)
    entry_room = _entry_room_name(rooms)
    proximity = default_proximity(rooms)
    zoned = len(rooms) > ZONE_ROOM_THRESHOLD

    if zoned and (spec.ruleset is not None or diagnose_infeasibility):
        raise ValueError(
            f"this program needs {len(rooms)} rooms, over the {ZONE_ROOM_THRESHOLD}-room "
            "threshold that requires zoning -- ruleset enforcement and infeasibility "
            "diagnosis aren't supported on the zoned path yet, so this request can't "
            "honor what was asked for. Retry with fewer beds/baths, or without "
            "ruleset/diagnose_infeasibility.")

    t0 = time.time()
    if zoned:
        zone_of = zone_of_program(rooms)
        plan, status, cross, zone_metrics = solve_zoned(
            fp, rooms, adj, zone_of,
            time_budget=time_limit if time_limit is not None else SOLVE_TIME_BUDGET,
            seed=seed, workers=workers, hallways=HALLWAYS, private=private,
            weights=PRODUCTION_WEIGHTS, proximity=proximity)
        wall_time = time.time() - t0
        return SolveResult(plan=plan, status=status, footprint=fp, rooms=rooms,
                            adjacencies=adj, private=private, entry_room=entry_room,
                            zoned=True, wall_time=wall_time, cross=cross,
                            zone_metrics=zone_metrics)

    hint = shelf_pack_hint(fp, rooms)
    diagnosis_out = []
    plan, status, objective_value, best_objective_bound, wall_time = solve(
        fp, rooms, adj, time_limit=time_limit if time_limit is not None else TIME_LIMIT,
        seed=seed, workers=workers, hint=hint, hallways=HALLWAYS, private=private,
        no_improvement_timeout=NO_IMPROVEMENT_TIMEOUT, proximity=proximity,
        ruleset=spec.ruleset, diagnose_infeasibility=diagnose_infeasibility,
        diagnosis_out=diagnosis_out, **PRODUCTION_WEIGHTS)
    return SolveResult(plan=plan, status=status, footprint=fp, rooms=rooms,
                        adjacencies=adj, private=private, entry_room=entry_room,
                        zoned=False, wall_time=wall_time, objective_value=objective_value,
                        best_objective_bound=best_objective_bound,
                        diagnosis=diagnosis_out[0] if diagnosis_out else None)


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
