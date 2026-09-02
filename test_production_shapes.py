"""
Exercises generator.generate_program()/zone_of_program() and layout.solve()/
zoning.solve_zoned() the same way app.py actually calls them -- with
PRODUCTION_WEIGHTS on and real generated programs, not the hand-built room
lists and weight=0 defaults the rest of the test_*.py suite uses.

Added after two bugs went undetected because nothing exercised this exact
call shape: zone_of_program()'s old 2-way-only split failed outright at
5 beds/4 baths (15 rooms crammed into one "private" zone, over
ZONE_ROOM_THRESHOLD), and the app's own default/most-common preset already
burns its full time budget without anyone noticing. Run:
.venv/bin/python3 test_production_shapes.py
"""

import time
from collections import Counter

from generator import (HALLWAYS, MAX_BATHS, MAX_BEDS, PRODUCTION_WEIGHTS,
                        ZONE_ROOM_THRESHOLD, default_proximity, generate_program,
                        shelf_pack_hint, zone_of_program)
from layout import circulation_ok, solve
from zoning import solve_zoned

SOLVE_TIME_BUDGET = 45.0  # must match app.py's SOLVE_TIME_BUDGET
TIME_LIMIT = 25.0         # must match app.py's TIME_LIMIT


def check_zone_sizes():
    """Structural-only regression guard for the N-zone split: every
    beds/baths/style combination in the app's real range must produce
    zones that each stay under ZONE_ROOM_THRESHOLD. This is cheap (no
    CP-SAT solve) and is exactly the property that silently broke before
    zone_of_program() learned to fall back to a 3-way split."""
    for style in ("traditional", "open_concept"):
        for beds in range(1, MAX_BEDS + 1):
            for baths in range(1, MAX_BATHS + 1):
                _, rooms, _, _ = generate_program(4000, beds, baths, "rectangular", style)
                zone_of = zone_of_program(rooms)
                counts = Counter(zone_of.values())
                for zone, count in counts.items():
                    assert count <= ZONE_ROOM_THRESHOLD, (
                        f"{style} beds={beds} baths={baths}: zone {zone!r} has "
                        f"{count} rooms, over ZONE_ROOM_THRESHOLD={ZONE_ROOM_THRESHOLD}")
    print("check_zone_sizes: OK -- every beds/baths/style combo stays under "
          "threshold per zone")


def solve_program(area, beds, baths, shape, style, label):
    """Runs generate_program() -> solve()/solve_zoned() exactly the way
    app.py's index() route does (same weights, same time budgets, same
    zoning threshold), then asserts a plan was found and circulation
    actually holds -- not just that solve_zoned returned something."""
    fp, rooms, adj, private = generate_program(area, beds, baths, shape, style)
    proximity = default_proximity(rooms)
    zoned = len(rooms) > ZONE_ROOM_THRESHOLD
    cross = None

    t0 = time.time()
    if zoned:
        zone_of = zone_of_program(rooms)
        plan, status, cross, zone_metrics = solve_zoned(
            fp, rooms, adj, zone_of, time_budget=SOLVE_TIME_BUDGET, workers=8,
            hallways=HALLWAYS, private=private,
            weights=PRODUCTION_WEIGHTS, proximity=proximity)
    else:
        hint = shelf_pack_hint(fp, rooms)
        plan, status, objective_value, best_objective_bound, wall_time = solve(
            fp, rooms, adj, time_limit=TIME_LIMIT, workers=8, hint=hint,
            hallways=HALLWAYS, private=private,
            proximity=proximity, **PRODUCTION_WEIGHTS)
    elapsed = time.time() - t0

    print(f"{label}: rooms={len(rooms)} zoned={zoned} status={status!r} "
          f"elapsed={elapsed:.1f}s")
    assert plan is not None, f"{label}: no plan found (status={status!r})"

    ok, unreachable = circulation_ok(plan, "Entry", private=private)
    assert ok, f"{label}: circulation broken, unreachable={unreachable}"
    if cross:
        print(f"  cross-zone adjacencies failed: {cross['failed']}")

    return plan, status


if __name__ == "__main__":
    check_zone_sizes()
    print()

    # The app's own default/most-common config -- also the one already
    # flagged as burning its full TIME_LIMIT and landing on FEASIBLE, not
    # OPTIMAL. Not a failure here (FEASIBLE is a valid plan), just worth
    # watching if the elapsed time starts creeping past TIME_LIMIT.
    solve_program(1500, 3, 2, "rectangular", "traditional", "default preset")

    # The smallest preset, and the only one using shape="square".
    solve_program(1200, 2, 2, "square", "traditional", "1,200 sf square preset")

    # NOTE: deliberately not testing the 14/15-room unzoned/zoned boundary at
    # a large area (e.g. 4000 sf) here -- that's covered structurally by
    # check_zone_sizes() above. A *solved* large-footprint/low-room-count
    # case (e.g. 4000 sf, 3 bed/2 bath, still 14 rooms) turned out to be
    # separately, pre-existingly flaky: reproduced at weight=0 too (so not a
    # PRODUCTION_WEIGHTS regression), CP-SAT's own search log shows the
    # *first* feasible incumbent often isn't found until ~21s into a 25s
    # budget, and repeated trials split roughly 1/3 FEASIBLE, 2/3 UNKNOWN
    # (no plan at all) for the exact same input. This is a genuine
    # reliability gap -- larger footprints make the search space harder
    # independent of room count -- but fixing CP-SAT search reliability
    # across the app's full 400-10,000 sf area range is a much bigger,
    # open-ended effort than this pass's scope (the zoning fix above, plus
    # the default-preset perf ceiling). Flagged here, not attempted, same
    # spirit as generator.PRODUCTION_WEIGHTS's own deferred alignment_weight
    # note -- don't add a large-area/low-room-count case to this suite
    # without first addressing that flakiness, or it'll make CI flaky too.

    # The bug-triggering case: 5 beds/4 baths, 3-way zoned, both styles.
    solve_program(4000, 5, 4, "rectangular", "traditional",
                  "5bed/4bath max (3-way zoned, traditional)")
    solve_program(4000, 5, 4, "rectangular", "open_concept",
                  "5bed/4bath max (3-way zoned, open_concept)")

    print("\nAll production-shape checks passed.")
