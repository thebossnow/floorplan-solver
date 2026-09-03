"""
Post-solve checks on a solved plan, decoupled from rendering -- the
validate() aggregator API routes (Phase 7) will call independently of
solve_program(), given a prior result's plan/footprint/rooms/adjacencies
back (see V2-ALPHA-PLAN.md's API surface: POST /api/validate takes those
directly, no re-solve).

Phase 5: the four new validators, all pure functions on a solved `plan`
dict (the layout.place_openings()/circulation_ok() pattern) plus, for
door swings, the `openings` list place_openings() produces. Each returns
a list of finding dicts ({rule, room(s), message}); validate() below
concatenates all four into one findings list.

- check_egress: bedroom exterior openings vs. a minimum clear width --
  width-only by necessity, since this data model has no sill-height or
  net-clear-area data to check the rest of a real egress-window code
  requirement (IRC R310.2.1) against.
- check_door_swings: door-vs-door swing-arc overlap, and swing-vs-both-
  adjoining-rooms boundary -- both computable from place_openings()'s
  existing output, no new geometry. Door-vs-furniture swing is deferred
  (needs FURNITURE_CATALOG's data, but placed furniture isn't modeled,
  only a room-level clearance envelope -- there's no furniture position
  to swing-check against yet).
- check_fixture_clearance / check_furniture_fit: a room's solved
  dimensions/area vs. fixtures.py's coarse envelopes -- not real 2D
  fixture/furniture placement (see fixtures.py's own docstring for why).
"""

from typing import Dict, List, Optional

from fixtures import FIXTURE_CLEARANCE, FURNITURE_CATALOG
from layout import circulation_ok, place_openings, room_kind
from orchestrate import SolveResult


def _opening_width(o: dict) -> int:
    """An opening's own width -- the extent along the wall it's centered
    on (V-orient: the y-span; H-orient: the x-span). Both door and window
    openings share this shape (see layout.place_openings())."""
    return (o["y2"] - o["y1"]) if o["orient"] == "V" else (o["x2"] - o["x1"])


def _bbox(room: dict):
    """A room's overall (x1, y1, x2, y2) bounding box across all its
    parts -- exact for a single-part room, a coarse over-approximation
    for a multi-part (L/T/U) one (matches this whole module's "coarse,
    not exact placement" spirit -- room["area"], used separately below,
    stays exact regardless of part count)."""
    parts = room["parts"]
    x1 = min(p["x1"] for p in parts)
    x2 = max(p["x2"] for p in parts)
    y1 = min(p["y1"] for p in parts)
    y2 = max(p["y2"] for p in parts)
    return x1, y1, x2, y2


def _bbox_dims(room: dict):
    x1, y1, x2, y2 = _bbox(room)
    return x2 - x1, y2 - y1


def _door_swing_box(o: dict):
    """Reconstructs layout._door_svg()'s swing quadrant in raw grid-unit
    space (no scale/y-flip -- those are rendering-only transforms, not
    part of the underlying geometry): a door always swings into the
    +x,+y quadrant from its own hinge point (o's x1,y1), matching
    _door_svg()'s own hinge/jamb/tip construction exactly, with radius
    equal to the opening's own width. Returns (x1, y1, x2, y2) for the
    swing's bounding box (the quadrant's own bounding box, since a
    quarter-circle's bounding box is exactly its two radii)."""
    d = _opening_width(o)
    hx, hy = o["x1"], o["y1"]
    return hx, hy, hx + d, hy + d


def check_egress(plan: dict, openings: List[dict], min_width: int = 4) -> List[dict]:
    """min_width default: 4 grid-units (2ft) -- above IRC R310.2.1's real
    20in clear-width minimum (~3.33 grid-units), rounded up to a whole
    grid-unit rather than down, so this stays a meaningful (if width-
    only) proxy rather than one that's impossible to ever trip. Well
    below place_openings()'s own window_width default (8 grid-units), so
    a normal solve at default settings passes trivially -- this mainly
    catches a bedroom with NO window at all, or one rendered with an
    unusually narrow custom window_width."""
    windows_by_room: Dict[str, List[dict]] = {}
    for o in openings:
        if o["kind"] != "window":
            continue
        for rn in o["rooms"]:
            windows_by_room.setdefault(rn, []).append(o)

    findings = []
    for name in plan:
        if room_kind(name) != "sleep":
            continue
        wins = windows_by_room.get(name, [])
        if not wins:
            findings.append(dict(rule="egress", room=name,
                                  message=f"{name}: no exterior window found -- no egress opening"))
            continue
        best = max(_opening_width(w) for w in wins)
        if best < min_width:
            findings.append(dict(rule="egress", room=name,
                                  message=f"{name}: widest window is {best} grid-units wide, "
                                          f"below the {min_width} grid-unit egress minimum"))
    return findings


def check_door_swings(plan: dict, openings: List[dict]) -> List[dict]:
    doors = [o for o in openings if o["kind"] == "door"]
    boxes = [(_door_swing_box(o), o) for o in doors]

    findings = []
    for i in range(len(boxes)):
        a, oa = boxes[i]
        for b, ob in boxes[i + 1:]:
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                findings.append(dict(
                    rule="door_swing", rooms=tuple(oa["rooms"]) + tuple(ob["rooms"]),
                    message=f"door swings for {oa['rooms']} and {ob['rooms']} overlap"))

    room_bbox = {name: _bbox(room) for name, room in plan.items()}
    for box, o in boxes:
        if any(_box_contains(room_bbox[rn], box) for rn in o["rooms"] if rn in room_bbox):
            continue
        findings.append(dict(rule="door_swing", rooms=tuple(o["rooms"]),
                              message=f"door swing for {o['rooms']} extends outside both adjoining rooms"))
    return findings


def _box_contains(outer, inner) -> bool:
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    return ox1 <= ix1 and oy1 <= iy1 and ix2 <= ox2 and iy2 <= oy2


def check_fixture_clearance(plan: dict) -> List[dict]:
    findings = []
    for name, room in plan.items():
        if room_kind(name) != "wet" or name == "Utility":
            continue  # room_kind() lumps Utility into "wet" for rendering, but it isn't a bathroom
        spec = FIXTURE_CLEARANCE["bath"]
        w, h = _bbox_dims(room)
        if w < spec.min_width or h < spec.min_depth or room["area"] < spec.min_area:
            findings.append(dict(
                rule="fixture_clearance", room=name,
                message=f"{name}: {w}x{h} ({room['area']} grid-units^2) is below the "
                        f"{spec.description} minimum ({spec.min_width}x{spec.min_depth}, "
                        f"{spec.min_area} grid-units^2)"))
    return findings


def check_furniture_fit(plan: dict) -> List[dict]:
    findings = []
    for name, room in plan.items():
        if room_kind(name) != "sleep":
            continue
        spec = FURNITURE_CATALOG["primary_bedroom" if name == "Primary" else "secondary_bedroom"]
        w, h = _bbox_dims(room)
        if w < spec.min_width or h < spec.min_depth or room["area"] < spec.min_area:
            findings.append(dict(
                rule="furniture_fit", room=name,
                message=f"{name}: {w}x{h} ({room['area']} grid-units^2) is below the "
                        f"{spec.description} minimum ({spec.min_width}x{spec.min_depth}, "
                        f"{spec.min_area} grid-units^2)"))
    return findings


def validate(result: SolveResult, openings: Optional[list] = None) -> dict:
    """result: a SolveResult with a plan already solved (raises ValueError
    otherwise, same as render_svg()). openings, if not already computed by
    a caller that also rendered, is derived here via place_openings().

    Returns {circulation_ok, unreachable, findings} -- findings is the
    concatenation of all four Phase 5 validators, run unconditionally
    (not opt-in/gated the way solve()'s new rules are -- these are post-
    solve checks, not CP-SAT constraints, so there's no solve-time cost
    to running them)."""
    if not result.plan:
        raise ValueError("cannot validate: solve_program() found no plan "
                          f"(status={result.status!r})")
    ok, unreachable = circulation_ok(result.plan, result.entry_room, private=result.private)
    if openings is None:
        openings = place_openings(result.plan, result.footprint, result.adjacencies, result.rooms)

    findings = []
    findings += check_egress(result.plan, openings)
    findings += check_door_swings(result.plan, openings)
    findings += check_fixture_clearance(result.plan)
    findings += check_furniture_fit(result.plan)

    return dict(circulation_ok=ok, unreachable=unreachable, findings=findings)
