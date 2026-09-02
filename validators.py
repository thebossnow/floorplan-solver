"""
Post-solve checks on a solved plan, decoupled from rendering -- the
validate() aggregator API routes (Phase 7) will call independently of
solve_program(), given a prior result's plan/footprint/rooms/adjacencies
back (see V2-ALPHA-PLAN.md's API surface: POST /api/validate takes those
directly, no re-solve).

Phase 2 scope only: wires the two checks that already exist in layout.py
(circulation_ok(), and door/window placement via place_openings(), needed
for door-swing checks later) through this new validate() entry point,
with an empty findings list. The four new hard-rule-adjacent validators
(egress opening width, door-swing conflicts, fixture clearance, furniture
fit) -- and fixtures.py, the static data they need -- land in Phase 5.
"""

from typing import Optional

from layout import circulation_ok, place_openings
from orchestrate import SolveResult


def validate(result: SolveResult, openings: Optional[list] = None) -> dict:
    """result: a SolveResult with a plan already solved (raises ValueError
    otherwise, same as render_svg()). openings, if not already computed by
    a caller that also rendered, is derived here via place_openings() --
    Phase 5's door-swing validator will need it; circulation_ok() doesn't.

    Returns {circulation_ok, unreachable, findings} -- findings is always
    [] until Phase 5."""
    if not result.plan:
        raise ValueError("cannot validate: solve_program() found no plan "
                          f"(status={result.status!r})")
    ok, unreachable = circulation_ok(result.plan, result.entry_room, private=result.private)
    if openings is None:
        openings = place_openings(result.plan, result.footprint, result.adjacencies, result.rooms)
    return dict(circulation_ok=ok, unreachable=unreachable, findings=[])
