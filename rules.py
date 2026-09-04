"""
Ruleset definitions for the v2-alpha rules engine. Phase 4: the
generic-residential stub ruleset plus the three new hard rules it gates
in layout.solve() (hall clear width, setback envelope, closet alignment
-- see solve()'s own docstring for exactly what each does and how it's
modeled). Python-only scope: Eve integration and real per-jurisdiction
building-code data are explicit future work, not this pass -- one stub
ruleset, "generic-residential".

Garage separation (originally also planned for this phase) is dropped
entirely, not just weakened -- generator.py doesn't produce a Garage
room at all, so there's no real program for a garage-separation rule to
apply to yet. Revisit both this rule and garage generation together,
whenever garage support is actually added (see V2-ALPHA-PLAN.md's
sign-off #3/#4).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# InfeasibilityDiagnosis is defined in layout.py, not here, despite
# V2-ALPHA-PLAN.md's Architecture section listing it under rules.py:
# layout.solve() must construct instances of it directly, and stays
# import-free from every other project module (its own "leaf-acyclic"
# invariant -- generator.py/zoning.py import *from* it, never the
# reverse). Re-exported here, not redefined, so `from rules import
# InfeasibilityDiagnosis` still works for callers that only know about
# this module -- see layout.InfeasibilityDiagnosis's own docstring for
# the full explanation.
from layout import InfeasibilityDiagnosis  # noqa: F401 (re-export)


@dataclass
class Ruleset:
    """id: stable identifier (e.g. "generic-residential"), also what
    list_jurisdictions() reports. hall_clear_width: grid-units, applied
    to every part of every room solve() is told is a hallway (via its own
    `hallways` param) -- see solve()'s docstring for how this differs
    from Room.min_dim. setbacks: (room_name, edge, distance) triples,
    edge in "N"/"S"/"E"/"W" -- a room named in a program this ruleset is
    used with must sit at least `distance` grid-units in from the
    footprint boundary on that edge. A room name with no match in a given
    program is silently skipped (see solve()'s own setback loop), so one
    Ruleset can be reused across programs that don't all contain every
    named room."""
    id: str
    hall_clear_width: int
    setbacks: List[Tuple[str, str, int]] = field(default_factory=list)


GENERIC_RESIDENTIAL = Ruleset(
    id="generic-residential",
    hall_clear_width=6,  # 3ft -- matches solve()'s own door_width default
                         # (also 6 grid-units); a hallway shouldn't be
                         # narrower than the doors opening off it
    setbacks=[],  # no setback rules in the stub -- setbacks are project-
                  # /jurisdiction-specific (a real site plan), not
                  # something a "generic" ruleset can sensibly default.
                  # The garage-front-setback example that originally
                  # motivated this rule no longer applies either (see
                  # module docstring) -- the mechanism itself is still
                  # fully generic and solve()-tested (test_hard_rules.py),
                  # just unpopulated here.
)


def list_jurisdictions() -> List[Dict[str, str]]:
    """{jurisdictions: [{id, name}, ...]} -- matches the shape
    V2-ALPHA-PLAN.md's API surface documents for GET /api/jurisdictions
    (Phase 7), usable standalone before that route exists."""
    return [dict(id=GENERIC_RESIDENTIAL.id, name="Generic Residential (stub)")]
