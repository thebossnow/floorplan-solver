"""
Static fixture/furniture data for validators.py's coarse post-solve
checks (Phase 5): a room's actual solved dimensions/area vs. a required
clearance envelope for what it's supposed to hold -- not real fixture or
furniture placement, since individual fixtures/furniture pieces aren't
CP-SAT variables (nothing in layout.solve() models where a toilet or a
bed actually sits). This is real data-modeling work, not a one-line
addition -- the numbers below are rough, defensible minimums for a
typical fixture/furniture set to physically fit with code-typical
clearance around it, not a specific product catalog or a claim of
code compliance.

Units: grid-units (1 grid-unit = 6in), matching everything else post-
Phase-1. min_width/min_depth are independent of orientation (checked
against a room's solved bounding-box dimensions either way, whichever is
smaller/larger doesn't matter since a bathroom or bedroom isn't
direction-sensitive the way e.g. a hallway is).
"""

from typing import Dict, NamedTuple


class ClearanceSpec(NamedTuple):
    min_width: int    # grid-units
    min_depth: int     # grid-units
    min_area: int       # grid-units^2
    description: str


# fixture clearance: bathrooms. generator.py never produces a powder-room
# (2-piece) case -- every PrimBath/Bath{i} is assumed 3-piece (toilet +
# sink + tub/shower) -- so one spec covers all of them, keyed "bath"
# rather than by room name.
FIXTURE_CLEARANCE: Dict[str, ClearanceSpec] = {
    "bath": ClearanceSpec(
        min_width=10, min_depth=10, min_area=100,  # 5ft x 5ft, ~25sf
        description="toilet + sink + tub/shower (3-piece bath)"),
}

# furniture fit: bedrooms, keyed by generator.py's Primary/secondary
# distinction (STYLES/ROOM_SPECS already treat these as different room
# types) -- a primary suite is assumed to want a queen/king bed plus two
# nightstands; a secondary bedroom, a full/twin plus one nightstand.
FURNITURE_CATALOG: Dict[str, ClearanceSpec] = {
    "primary_bedroom": ClearanceSpec(
        min_width=22, min_depth=22, min_area=440,  # 11ft x 11ft, ~110sf
        description="king/queen bed + two nightstands + walk space"),
    "secondary_bedroom": ClearanceSpec(
        min_width=18, min_depth=18, min_area=300,  # 9ft x 9ft, ~75sf
        description="full/twin bed + one nightstand + walk space"),
}
