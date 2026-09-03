"""
Phase 5 validation: the four new validators.py checks (egress, door
swings, fixture clearance, furniture fit).

Most cases use hand-built `plan`/`openings` dicts directly -- these are
pure functions on already-solved data, so there's no need to invoke
CP-SAT at all to test their own logic quickly and deterministically. One
integration case reuses a real generator.py-produced, orchestrate.py-
solved program (in the spirit of V2-ALPHA-PLAN.md's stated "reusing
test_house.py's program" -- test_house.py itself is a standalone script
with top-level side effects, not importable, so this rebuilds an
equivalent realistic case through the production generate_program() path
instead) to confirm the whole validate() pipeline runs end-to-end without
crashing on a normal house.

Run: .venv/bin/python3 test_validators.py
"""

from fixtures import FIXTURE_CLEARANCE, FURNITURE_CATALOG
from orchestrate import ProgramSpec, solve_program
from validators import check_door_swings, check_egress, check_fixture_clearance, check_furniture_fit, validate


def _room(x1, y1, x2, y2, target=None):
    area = (x2 - x1) * (y2 - y1)
    return dict(parts=[dict(x1=x1, y1=y1, x2=x2, y2=y2, area=area)],
                area=area, target=target if target is not None else area)


def _window(room, x1, y1, x2, y2):
    orient = "V" if x1 == x2 else "H"
    return dict(kind="window", orient=orient, rooms=(room,), x1=x1, y1=y1, x2=x2, y2=y2)


def _door(rooms, x1, y1, x2, y2):
    orient = "V" if x1 == x2 else "H"
    return dict(kind="door", orient=orient, rooms=rooms, x1=x1, y1=y1, x2=x2, y2=y2)


def check_egress_cases():
    plan = {
        "Primary": _room(0, 0, 20, 20),
        "Bed2": _room(20, 0, 34, 16),
        "Hall": _room(0, 20, 34, 26),
    }
    # Primary: wide window (10 gu) -- passes. Bed2: narrow window (2 gu) --
    # fails width. Hall: not a bedroom (room_kind != "sleep") -- never
    # checked regardless of having no window at all.
    openings = [
        _window("Primary", 0, 5, 0, 15),
        _window("Bed2", 34, 5, 34, 7),
    ]
    findings = check_egress(plan, openings)
    rooms_flagged = {f["room"] for f in findings}
    assert "Primary" not in rooms_flagged, f"Primary shouldn't be flagged: {findings}"
    assert "Bed2" in rooms_flagged, f"Bed2 (narrow window) should be flagged: {findings}"
    assert "Hall" not in rooms_flagged, "Hall isn't a bedroom, shouldn't be checked at all"
    bed2_msg = next(f["message"] for f in findings if f["room"] == "Bed2")
    assert "widest window" in bed2_msg

    # a bedroom with NO window at all is a separate, distinct finding
    plan2 = {"Primary": _room(0, 0, 20, 20)}
    findings2 = check_egress(plan2, [])
    assert len(findings2) == 1 and "no exterior window" in findings2[0]["message"]
    print("check_egress_cases: OK -- narrow window, missing window, and a passing "
          "window all handled correctly; non-bedrooms never checked")


def check_door_swing_cases():
    # two doors hinged close enough that their swing quadrants overlap
    plan = {
        "A": _room(0, 0, 20, 20),
        "B": _room(20, 0, 40, 20),
        "C": _room(0, 20, 20, 40),
    }
    d1 = _door(("A", "B"), 10, 0, 10, 6)   # hinge (10,0) -> swing box (10,0)-(16,6)
    d2 = _door(("A", "C"), 12, 0, 12, 5)   # hinge (12,0) -> swing box (12,0)-(17,5)
    findings = check_door_swings(plan, [d1, d2])
    assert any(f["rule"] == "door_swing" and "overlap" in f["message"] for f in findings), (
        f"expected an overlap finding, got {findings}")

    # a door whose swing extends outside both adjoining rooms (room B is
    # only 6 wide, but this door's swing radius is 15)
    plan2 = {"A": _room(0, 0, 20, 20), "B": _room(20, 0, 26, 20)}
    d3 = _door(("A", "B"), 20, 0, 20, 15)  # hinge (20,0) -> swing box (20,0)-(35,15)
    findings2 = check_door_swings(plan2, [d3])
    assert any("extends outside" in f["message"] for f in findings2), (
        f"expected a swing-outside-room finding, got {findings2}")

    # a normal, well-clear door: no findings
    plan3 = {"A": _room(0, 0, 20, 20), "B": _room(20, 0, 40, 20)}
    d4 = _door(("A", "B"), 20, 5, 20, 11)  # radius 6, fits easily in either 20x20 room
    findings3 = check_door_swings(plan3, [d4])
    assert findings3 == [], f"expected no findings for a normal door, got {findings3}"
    print("check_door_swing_cases: OK -- overlap, swing-outside-room, and a "
          "passing door all handled correctly")


def check_fixture_clearance_cases():
    plan = {
        "PrimBath": _room(0, 0, 6, 6),     # 6x6, 36 gu^2 -- below the 10x10/100 minimum
        "Bath2": _room(0, 0, 12, 12),       # 12x12, 144 gu^2 -- clears it
        "Utility": _room(0, 0, 4, 4),        # room_kind()=="wet" too, but not a bathroom -- never checked
    }
    findings = check_fixture_clearance(plan)
    rooms_flagged = {f["room"] for f in findings}
    assert rooms_flagged == {"PrimBath"}, f"expected only PrimBath flagged, got {rooms_flagged}"
    spec = FIXTURE_CLEARANCE["bath"]
    assert spec.description in findings[0]["message"]
    print(f"check_fixture_clearance_cases: OK -- undersized bath flagged, adequate bath "
          f"and Utility (not a real bathroom) both correctly skipped")


def check_furniture_fit_cases():
    plan = {
        "Primary": _room(0, 0, 18, 18),      # 18x18 -- below the primary spec's 22x22/440
        "Bed2": _room(0, 0, 14, 14),          # 14x14 -- below the secondary spec's 18x18/300
        "Bed3": _room(0, 0, 20, 20),          # 20x20, 400 gu^2 -- clears the secondary spec
    }
    findings = check_furniture_fit(plan)
    rooms_flagged = {f["room"] for f in findings}
    assert rooms_flagged == {"Primary", "Bed2"}, f"expected Primary+Bed2 flagged, got {rooms_flagged}"
    primary_msg = next(f["message"] for f in findings if f["room"] == "Primary")
    bed2_msg = next(f["message"] for f in findings if f["room"] == "Bed2")
    assert FURNITURE_CATALOG["primary_bedroom"].description in primary_msg
    assert FURNITURE_CATALOG["secondary_bedroom"].description in bed2_msg
    print("check_furniture_fit_cases: OK -- undersized Primary (own, larger spec) and "
          "Bed2 flagged, adequately-sized Bed3 correctly passes the secondary spec")


def check_validate_integration():
    """A real generate_program()-produced, orchestrate.solve_program()-
    solved house -- confirms validate() runs all four validators
    end-to-end against a genuinely solved plan without crashing, and that
    circulation_ok/findings both come back in the expected shape."""
    spec = ProgramSpec(total_area=1500, beds=3, baths=2, style="traditional", width=46)
    result = solve_program(spec)
    assert result.plan is not None, f"solve_program failed: {result.status}"
    v = validate(result)
    assert v["circulation_ok"], f"circulation broken: {v['unreachable']}"
    assert isinstance(v["findings"], list)
    for f in v["findings"]:
        assert set(f) >= {"rule", "message"}, f"malformed finding: {f}"
    print(f"check_validate_integration: OK -- real {len(result.rooms)}-room house, "
          f"circulation ok, {len(v['findings'])} finding(s): "
          f"{[f['rule'] for f in v['findings']]}")


if __name__ == "__main__":
    check_egress_cases()
    check_door_swing_cases()
    check_fixture_clearance_cases()
    check_furniture_fit_cases()
    check_validate_integration()
    print("\nAll Phase 5 validator checks passed.")
