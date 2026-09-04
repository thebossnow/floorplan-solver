"""
Machine-facing JSON API -- a Flask Blueprint (Phase 7, sign-off #7: kept
separate from app.py's human-form routes, registered from app.py rather
than merged into it). No collision with v1's existing "/" or "/solve"
routes (those return HTML-in-JSON for the browser fetch(), a completely
different shape) -- url_prefix="/api" keeps every route here on its own
path. See V2-ALPHA-PLAN.md's API surface section for the exact request/
response shapes below.
"""

from flask import Blueprint, jsonify, request

from generator import MAX_AREA, MAX_BATHS, MAX_BEDS, MIN_AREA, STYLES
from orchestrate import ProgramSpec, SolveResult, _entry_room_name, _private_room_names, solve_program
from rules import GENERIC_RESIDENTIAL, list_jurisdictions
from serialize import program_from_dict, program_to_dict
from validators import validate as validate_result

api = Blueprint("api", __name__, url_prefix="/api")

# Upper bound on a caller-supplied time_limit -- unlike total_area/beds/
# baths, nothing about solve_program()'s own signature caps this, so an
# unvalidated request could tie up a CP-SAT worker (of 8) for as long as
# the caller likes. 120s is double app.py's own production TIME_LIMIT
# (25s) and matches SOLVE_TIME_BUDGET's own doubling-for-Vercel headroom
# elsewhere in this codebase -- generous for a deliberately slow ruleset
# solve, not unlimited.
MAX_TIME_LIMIT = 120.0


def _resolve_ruleset(ruleset_id):
    """None (the field omitted or explicitly null) -> no ruleset, today's
    behavior. Any other value must name a known jurisdiction id -- fails
    loudly (ValueError, caught by the route as a 400) rather than
    silently ignoring a typo'd id."""
    if ruleset_id is None:
        return None
    if ruleset_id == GENERIC_RESIDENTIAL.id:
        return GENERIC_RESIDENTIAL
    raise ValueError(f"unknown ruleset id {ruleset_id!r}; choose from "
                      f"{[j['id'] for j in list_jurisdictions()]}")


@api.route("/solve", methods=["POST"])
def solve_route():
    body = request.get_json(force=True, silent=True) or {}
    try:
        total_area = int(body["total_area"])
        beds = int(body.get("beds", 3))
        baths = int(body.get("baths", 2))
        style = body.get("style", "traditional")
        width = body.get("width")
        width = int(width) if width is not None else None
        has_entry = bool(body.get("has_entry", True))
        ruleset = _resolve_ruleset(body.get("ruleset"))
        diagnose = bool(body.get("diagnose_infeasibility", False))
        time_limit = body.get("time_limit")
        time_limit = float(time_limit) if time_limit is not None else None

        if time_limit is not None and not (0 < time_limit <= MAX_TIME_LIMIT):
            raise ValueError(f"time_limit must be between 0 and {MAX_TIME_LIMIT}")
        if not (MIN_AREA <= total_area <= MAX_AREA):
            raise ValueError(f"total_area must be between {MIN_AREA} and {MAX_AREA}")
        if not (1 <= beds <= MAX_BEDS):
            raise ValueError(f"beds must be between 1 and {MAX_BEDS}")
        if not (1 <= baths <= MAX_BATHS):
            raise ValueError(f"baths must be between 1 and {MAX_BATHS}")
        if style not in STYLES:
            raise ValueError(f"style must be one of {sorted(STYLES)}")
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(ok=False, error=str(e)), 400

    spec = ProgramSpec(total_area=total_area, beds=beds, baths=baths, style=style,
                        width=width, has_entry=has_entry, ruleset=ruleset)
    try:
        result = solve_program(spec, diagnose_infeasibility=diagnose, time_limit=time_limit)
    except ValueError as e:
        # validate_program()'s own pre-flight rejection (e.g. floors alone
        # exceed the footprint) -- a genuine bad-input case, not a solver
        # INFEASIBLE, so it gets the same 400 treatment as the parameter
        # checks above rather than a {ok:false, status, diagnosis} shape.
        return jsonify(ok=False, error=str(e)), 400

    if not result.plan:
        diagnosis = None
        if result.diagnosis is not None:
            diagnosis = dict(conflicting_rules=result.diagnosis.conflicting_rules,
                              message=result.diagnosis.message)
        return jsonify(ok=False, status=result.status, diagnosis=diagnosis)

    program = program_to_dict(result.footprint, result.rooms, result.adjacencies, result.private)
    return jsonify(ok=True, status=result.status, plan=result.plan,
                   footprint=program["footprint"], objective_value=result.objective_value,
                   best_objective_bound=result.best_objective_bound, wall_time=result.wall_time,
                   zoned=result.zoned, program=program)


@api.route("/validate", methods=["POST"])
def validate_route():
    body = request.get_json(force=True, silent=True) or {}
    try:
        plan = body["plan"]
        if not isinstance(plan, dict):
            raise ValueError("plan must be an object keyed by room name")
        footprint, rooms, adjacencies, private = program_from_dict(body)
        # "private" absent from the request at all -> infer it from
        # room_kind(). An explicit "private": [] is a deliberate caller
        # choice (e.g. "treat every room as public for this check") and
        # must NOT be overridden -- checked against the raw body, not the
        # already-tupled `private` above, since program_from_dict() (by
        # design) can't distinguish an empty list from an omitted key by
        # the time it returns.
        if "private" not in body:
            private = _private_room_names(rooms)
        # ruleset is accepted (matching the documented request shape) but
        # not yet consumed -- none of Phase 5's four validators are
        # ruleset-aware (they check fixed fixtures.py envelopes / a fixed
        # egress width, not a jurisdiction-varying one). Still resolved
        # and validated here so a bad/unknown id fails loudly now rather
        # than silently doing nothing forever.
        _resolve_ruleset(body.get("ruleset"))
        # Raises if none of Entry/Great/Living (generate_program()'s own
        # arrival-room names) appear in a caller-submitted room list --
        # /api/solve's own programs always have one, but /api/validate
        # accepts an arbitrary program, where silently guessing "Living"
        # would produce a bogus circulation_ok=False instead of a clear
        # error (see this function's own docstring).
        entry_room = _entry_room_name(rooms)
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(ok=False, error=str(e)), 400

    result = SolveResult(plan=plan, status="OK", footprint=footprint, rooms=rooms,
                          adjacencies=adjacencies, private=private,
                          entry_room=entry_room, zoned=False)
    try:
        v = validate_result(result)
    except (KeyError, TypeError, ValueError) as e:
        # a plan/rooms mismatch (e.g. a room in `rooms` missing from
        # `plan`, or a part's coordinates sent as strings instead of
        # ints) surfaces from deep inside circulation_ok()/
        # place_openings() as a bare KeyError/TypeError -- normalize to
        # the same {ok:false, error} shape rather than a bare 500.
        return jsonify(ok=False, error=f"plan/program mismatch: {e}"), 400
    return jsonify(ok=True, **v)


@api.route("/jurisdictions", methods=["GET"])
def jurisdictions_route():
    return jsonify(jurisdictions=list_jurisdictions())
