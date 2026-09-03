"""
Phase 4 validation: the three new ruleset-gated hard rules in
layout.solve() (hall clear width, setback envelope, closet alignment) --
one deliberately-infeasible and one feasible case per rule, per
V2-ALPHA-PLAN.md's own stated Phase 4 test approach. Each infeasible case
uses diagnose_infeasibility=True specifically so the returned diagnosis
can confirm the *intended* rule is the one actually blocking, not some
other constraint (min_dim, aspect, exact-partition) coincidentally also
failing.

Run: .venv/bin/python3 test_hard_rules.py
"""

from layout import Room, Adj, Footprint, solve
from rules import Ruleset


def check_hall_clear_width():
    ruleset = Ruleset(id="test", hall_clear_width=8)  # 4ft

    # infeasible: a single room named "Hall", alone in its footprint --
    # exact partition forces it to fill the 4x100 footprint exactly
    # (area/aspect loose enough not to bind on their own), but
    # hall_clear_width=8 needs both sides >= 8; the forced width (4) can't.
    fp = Footprint(width=4, height=100)  # 400 gu^2
    rooms = [Room("Hall", 400, min_dim=2, max_aspect=50.0, needs_exterior=False)]
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, [], time_limit=15, workers=8,
                                   hallways=("Hall",), ruleset=ruleset,
                                   diagnose_infeasibility=True, diagnosis_out=diagnosis_out)
    assert plan is None and status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"
    assert diagnosis_out and "hall_clear_width:Hall" in diagnosis_out[0].conflicting_rules, (
        f"expected 'hall_clear_width:Hall' in conflicting_rules, "
        f"got {diagnosis_out[0].conflicting_rules if diagnosis_out else None}")
    print(f"check_hall_clear_width (infeasible): OK -- "
          f"conflicting_rules={diagnosis_out[0].conflicting_rules}")

    # feasible: same rule active, but the hall has plenty of room to clear it
    fp2 = Footprint(width=30, height=20)  # 600 gu^2
    rooms2 = [
        Room("Hall", 200, min_dim=8, max_aspect=6.0, needs_exterior=False),
        Room("Room", 400, min_dim=10, max_aspect=2.0),
    ]
    adj2 = [Adj("Hall", "Room", min_shared=6)]
    plan2, status2, _, _, _ = solve(fp2, rooms2, adj2, time_limit=15, workers=8,
                                     hallways=("Hall",), ruleset=ruleset)
    assert plan2 is not None, f"expected a plan, got status={status2}"
    part = plan2["Hall"]["parts"][0]
    w, h = part["x2"] - part["x1"], part["y2"] - part["y1"]
    assert w >= ruleset.hall_clear_width and h >= ruleset.hall_clear_width, (
        f"Hall {w}x{h} violates hall_clear_width={ruleset.hall_clear_width}")
    print(f"check_hall_clear_width (feasible): OK -- Hall solved to {w}x{h}, "
          f"clears {ruleset.hall_clear_width}")


def check_setback():
    ruleset = Ruleset(id="test", hall_clear_width=6, setbacks=[("Garage", "S", 4)])

    # infeasible: Garage is forced to touch the south boundary (edges=["S"]
    # -> y1==0) while the setback requires y1 >= 4 -- direct contradiction,
    # independent of footprint size.
    fp = Footprint(width=20, height=16)  # 320 gu^2
    rooms = [Room("Garage", 320, min_dim=10, max_aspect=3.0, edges=["S"])]
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, [], time_limit=15, workers=8,
                                   ruleset=ruleset, diagnose_infeasibility=True,
                                   diagnosis_out=diagnosis_out)
    assert plan is None and status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"
    assert diagnosis_out and "setback:Garage:S" in diagnosis_out[0].conflicting_rules, (
        f"expected 'setback:Garage:S' in conflicting_rules, "
        f"got {diagnosis_out[0].conflicting_rules if diagnosis_out else None}")
    print(f"check_setback (infeasible): OK -- conflicting_rules={diagnosis_out[0].conflicting_rules}")

    # feasible: no edges forcing Garage to the boundary, and a second room
    # to share the footprint with (so Garage isn't forced to fill it
    # entirely, which would force y1=0 again via exact partition alone)
    fp2 = Footprint(width=30, height=20)  # 600 gu^2
    rooms2 = [
        Room("Garage", 300, min_dim=10, max_aspect=3.0),
        Room("Other", 300, min_dim=10, max_aspect=3.0),
    ]
    plan2, status2, _, _, _ = solve(fp2, rooms2, [], time_limit=15, workers=8, ruleset=ruleset)
    assert plan2 is not None, f"expected a plan, got status={status2}"
    part = plan2["Garage"]["parts"][0]
    assert part["y1"] >= 4, f"Garage y1={part['y1']} violates setback ('S', 4)"
    print(f"check_setback (feasible): OK -- Garage solved with y1={part['y1']} >= 4")


def _bedroom_closet_hall_program(bedroom_pin_edges):
    """Shared builder for the closet-alignment cases: a bedroom (optionally
    pinned to specific footprint edges), its closet, and a Hall it's
    adjacent to."""
    fp = Footprint(width=28, height=20)  # 560 gu^2
    rooms = [
        Room("Primary", 450, min_dim=20, max_aspect=1.5, edges=bedroom_pin_edges),
        Room("PrimaryCloset", 40, min_dim=6, max_aspect=3.0, needs_exterior=False),
        Room("Hall", 70, min_dim=6, max_aspect=8.0, needs_exterior=False),
    ]
    adj = [
        Adj("Primary", "PrimaryCloset", min_shared=4),
        Adj("Hall", "Primary", min_shared=4),
    ]
    return fp, rooms, adj


def check_closet_align_width():
    ruleset = Ruleset(id="test", hall_clear_width=6)

    # infeasible: Primary's own min_dim (20) puts both its dimensions well
    # above what PrimaryCloset's tiny area (40, min_dim 6) can ever reach
    # (closet's area cap bounds either side to well under 20 regardless of
    # orientation) -- so NEITHER the h-match nor w-match branch of
    # closet_align_width can be satisfied, whichever side they end up
    # sharing. No edges forced -- this isolates width, not position.
    fp, rooms, adj = _bedroom_closet_hall_program(bedroom_pin_edges=[])
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=20, workers=8,
                                   hallways=("Hall",), ruleset=ruleset,
                                   diagnose_infeasibility=True, diagnosis_out=diagnosis_out)
    assert plan is None and status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"
    assert diagnosis_out and "closet_align_width:Primary" in diagnosis_out[0].conflicting_rules, (
        f"expected 'closet_align_width:Primary' in conflicting_rules, "
        f"got {diagnosis_out[0].conflicting_rules if diagnosis_out else None}")
    print(f"check_closet_align_width (infeasible): OK -- "
          f"conflicting_rules={diagnosis_out[0].conflicting_rules}")


def check_closet_align_position():
    ruleset = Ruleset(id="test", hall_clear_width=6)

    # infeasible: Primary is pinned to W+E+S (spans the full footprint
    # width, sits at the very bottom) -- its only remaining touchable side
    # is north, so BOTH PrimaryCloset and Hall are forced to share that
    # same side, which closet_align_position forbids (closet can't be on
    # the same side as the hall/door wall). PrimaryCloset/Hall sized
    # bigger than check_closet_align_width's (120 each, not 40/70) so
    # width-match stays comfortably satisfiable and position is the sole
    # cause -- footprint enlarged to match (30x22=660, within
    # [total_lo, total_hi] for 450+120+120).
    fp = Footprint(width=30, height=22)  # 660 gu^2
    rooms = [
        Room("Primary", 450, min_dim=20, max_aspect=1.5, edges=["W", "E", "S"]),
        Room("PrimaryCloset", 120, min_dim=6, max_aspect=3.0, needs_exterior=False),
        Room("Hall", 120, min_dim=6, max_aspect=8.0, needs_exterior=False),
    ]
    adj = [
        Adj("Primary", "PrimaryCloset", min_shared=4),
        Adj("Hall", "Primary", min_shared=4),
    ]
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=20, workers=8,
                                   hallways=("Hall",), ruleset=ruleset,
                                   diagnose_infeasibility=True, diagnosis_out=diagnosis_out)
    assert plan is None and status == "INFEASIBLE", f"expected INFEASIBLE, got {status}"
    assert diagnosis_out and "closet_align_position:Primary" in diagnosis_out[0].conflicting_rules, (
        f"expected 'closet_align_position:Primary' in conflicting_rules, "
        f"got {diagnosis_out[0].conflicting_rules if diagnosis_out else None}")
    print(f"check_closet_align_position (infeasible): OK -- "
          f"conflicting_rules={diagnosis_out[0].conflicting_rules}")


def check_closet_align_feasible():
    """A normal bedroom/closet/hall program (generator.py-realistic
    proportions, not the deliberately-tight infeasible cases above) with
    the ruleset active -- solves, and the resulting plan actually holds
    both closet_align sub-rules (verified independently, post-solve, not
    just trusted from the solve status)."""
    ruleset = Ruleset(id="test", hall_clear_width=6)
    # Explicit (wide) min_area/max_area on all three, not just the default
    # +/-15% of target -- exactly matching a bedroom/closet's dimension on
    # whichever side they end up sharing, AND landing closet/hall on
    # different sides of the bedroom, AND filling the footprint exactly,
    # all simultaneously, is a much tighter exact-tiling problem than a
    # normal solve -- found empirically (several tighter attempts came
    # back genuinely INFEASIBLE, not just slow) that these three rules
    # together need real room to maneuver, not just a plausible-looking
    # area/footprint pairing.
    fp = Footprint(width=29, height=22)  # 638 gu^2
    rooms = [
        Room("Primary", 300, min_dim=12, max_aspect=3.0, min_area=250, max_area=400),
        Room("PrimaryCloset", 150, min_dim=6, max_aspect=6.0, needs_exterior=False,
             min_area=50, max_area=300),
        Room("Hall", 110, min_dim=6, max_aspect=8.0, needs_exterior=False,
             min_area=50, max_area=250),
    ]
    adj = [
        Adj("Primary", "PrimaryCloset", min_shared=4),
        Adj("Hall", "Primary", min_shared=4),
    ]
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=45, workers=8,
                                   hallways=("Hall",), private=("PrimaryCloset",),
                                   ruleset=ruleset)
    assert plan is not None, f"expected a plan, got status={status}"

    def side(a_part, b_part):
        if a_part["x2"] == b_part["x1"] or b_part["x2"] == a_part["x1"]:
            return "vertical"
        if a_part["y2"] == b_part["y1"] or b_part["y2"] == a_part["y1"]:
            return "horizontal"
        return None

    bed, closet, hall = plan["Primary"]["parts"][0], plan["PrimaryCloset"]["parts"][0], plan["Hall"]["parts"][0]
    bc_side = side(bed, closet)
    bh_side = side(bed, hall)
    assert bc_side is not None, "Primary/PrimaryCloset aren't actually touching"
    assert bh_side is not None, "Primary/Hall aren't actually touching"
    if bc_side == "vertical":
        bh = bed["y2"] - bed["y1"]
        ch = closet["y2"] - closet["y1"]
        assert bh == ch, f"width mismatch: Primary h={bh}, PrimaryCloset h={ch}"
    else:
        bw = bed["x2"] - bed["x1"]
        cw = closet["x2"] - closet["x1"]
        assert bw == cw, f"width mismatch: Primary w={bw}, PrimaryCloset w={cw}"

    # Same-side check: for each of the 4 concrete sides, closet and hall
    # must not BOTH be touching Primary on that exact side ("vertical"/
    # "horizontal" above is too coarse for this -- it doesn't distinguish
    # W from E or S from N).
    def touches(part, other, side_name):
        if side_name == "W":
            return part["x1"] == other["x2"]
        if side_name == "E":
            return part["x2"] == other["x1"]
        if side_name == "S":
            return part["y1"] == other["y2"]
        if side_name == "N":
            return part["y2"] == other["y1"]
    for side_name in ("W", "E", "S", "N"):
        assert not (touches(bed, closet, side_name) and touches(bed, hall, side_name)), (
            f"PrimaryCloset and Hall both touch Primary's {side_name} side")
    print("check_closet_align_feasible: OK -- width matched on the shared axis, "
          "closet and hall confirmed on different sides of Primary")


if __name__ == "__main__":
    check_hall_clear_width()
    check_setback()
    check_closet_align_width()
    check_closet_align_position()
    check_closet_align_feasible()
    print("\nAll Phase 4 hard-rule checks passed.")
