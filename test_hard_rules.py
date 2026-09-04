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

from generator import HALLWAYS, generate_program
from layout import CLOSET_WIDTH_RATIO_MAX, CLOSET_WIDTH_RATIO_MIN, Room, Adj, Footprint, solve
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
    """A REAL generate_program()-produced house (not a hand-built
    program) with the ruleset active -- solves, and the resulting plan
    actually holds both closet_align sub-rules (verified independently,
    post-solve, not just trusted from the solve status) for every
    bedroom, not just Primary.

    Deliberately not a hand-tuned synthetic case (an earlier version of
    this test was): closet_align_width changed from exact equality to a
    CLOSET_WIDTH_RATIO_MIN/MAX proportion band on 2026-09-02, specifically
    because a hand-tuned case satisfying exact equality doesn't prove
    anything about the band, and generate_program()'s own closet sizing
    (CLOSET_PCT) changed in the same fix -- the real integration is what
    actually matters here, not a synthetic room list. time_limit=60 (not
    the app.py-production 25s): the ratio band is a real, non-trivial
    constraint across three bedrooms at once, found empirically to need
    more search time than a normal solve, which is an acceptable
    tradeoff for opting into a ruleset, not treated as a bug to chase."""
    ruleset = Ruleset(id="test", hall_clear_width=6)
    fp, rooms, adj, private = generate_program(2200, 3, 2, style="traditional")
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=60, workers=8,
                                   hallways=HALLWAYS, private=private, ruleset=ruleset)
    assert plan is not None, f"expected a plan, got status={status}"

    def touches(part, other, side_name):
        if side_name == "W":
            return part["x1"] == other["x2"]
        if side_name == "E":
            return part["x2"] == other["x1"]
        if side_name == "S":
            return part["y1"] == other["y2"]
        if side_name == "N":
            return part["y2"] == other["y1"]

    bedrooms = [r.name for r in rooms
                if (r.name == "Primary" or r.name.startswith("Bed")) and not r.name.endswith("Closet")]
    for bedroom in bedrooms:
        closet, hall = f"{bedroom}Closet", "Hall"
        bed = plan[bedroom]["parts"][0]
        cl = plan[closet]["parts"][0]
        hl = plan[hall]["parts"][0]

        vertical = bed["x2"] == cl["x1"] or cl["x2"] == bed["x1"]
        horizontal = bed["y2"] == cl["y1"] or cl["y2"] == bed["y1"]
        assert vertical or horizontal, f"{bedroom}/{closet} aren't actually touching"
        if vertical:
            bed_dim, closet_dim = bed["y2"] - bed["y1"], cl["y2"] - cl["y1"]
        else:
            bed_dim, closet_dim = bed["x2"] - bed["x1"], cl["x2"] - cl["x1"]
        ratio = 100 * closet_dim / bed_dim
        assert CLOSET_WIDTH_RATIO_MIN <= ratio <= CLOSET_WIDTH_RATIO_MAX, (
            f"{bedroom}: closet dim {closet_dim} is {ratio:.0f}% of bedroom dim {bed_dim}, "
            f"outside [{CLOSET_WIDTH_RATIO_MIN}, {CLOSET_WIDTH_RATIO_MAX}]%")

        for side_name in ("W", "E", "S", "N"):
            assert not (touches(bed, cl, side_name) and touches(bed, hl, side_name)), (
                f"{bedroom}: {closet} and {hall} both touch its {side_name} side")

    print(f"check_closet_align_feasible: OK -- {len(bedrooms)} bedrooms, each with its "
          f"closet proportioned (within [{CLOSET_WIDTH_RATIO_MIN},{CLOSET_WIDTH_RATIO_MAX}]%) "
          "to the shared axis and on a different side than the hall")


if __name__ == "__main__":
    check_hall_clear_width()
    check_setback()
    check_closet_align_width()
    check_closet_align_position()
    check_closet_align_feasible()
    print("\nAll Phase 4 hard-rule checks passed.")
