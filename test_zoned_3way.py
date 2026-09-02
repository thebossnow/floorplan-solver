from layout import Room, Adj, Footprint, circulation_ok, place_openings, to_svg
from zoning import solve_zoned
import time

# 3-zone program (garage / private / public) -- exercises solve_zoned()'s
# N-way slab split (zone order is alphabetical: "garage" < "private" <
# "public", so the layout is garage | private | public west-to-east) and
# per-wall cross-adjacency anchoring, generalized from the 2-zone case in
# test_zoned.py.
fp = Footprint(width=160, height=64)  # 2560 sf (grid-units: 1 unit = 6in)

# single-room garage zone -- keeps the zone's own internal packing trivial
# so this test stays focused on exercising solve_zoned()'s N-way split and
# per-wall cross-adjacency anchoring, not garage-specific room-fit tuning
garage_rooms = [
    Room("Garage", 380, min_dim=20, max_aspect=2.5),
]
private_rooms = [
    Room("Primary",   230, min_dim=24, max_aspect=1.6),
    Room("PrimBath",   90, min_dim=12, max_aspect=2.5),
    Room("Bed2",      160, min_dim=20, max_aspect=1.6),
    Room("Bath2",      60, min_dim=10, max_aspect=2.5),
    Room("PLHall",    110, min_dim=8,  max_aspect=8.0, needs_exterior=False),
]
public_rooms = [
    Room("Entry",      60,  min_dim=12, max_aspect=2.5, edges=["S"]),
    Room("Living",    280,  min_dim=24, max_aspect=1.8),
    Room("Kitchen",   190,  min_dim=20, max_aspect=2.0),
    Room("Dining",    150,  min_dim=20, max_aspect=1.8),
    Room("Hall",      100,  min_dim=8,  max_aspect=8.0, needs_exterior=False),
    Room("Utility",    70,  min_dim=12, max_aspect=2.5, needs_exterior=False),
]

rooms = garage_rooms + private_rooms + public_rooms
target_sum = sum(r.target_area for r in rooms)
scale = fp.area() / target_sum
for r in rooms:
    r.target_area = round(r.target_area * scale)

zone_of = {r.name: "garage" for r in garage_rooms}
zone_of.update({r.name: "private" for r in private_rooms})
zone_of.update({r.name: "public" for r in public_rooms})

adj = [
    # private wing's own internal hall
    Adj("PLHall", "Primary"), Adj("Primary", "PrimBath"),
    Adj("PLHall", "Bed2"), Adj("Bed2", "Bath2"),
    # public zone's own internal adjacencies
    Adj("Entry", "Living"), Adj("Living", "Dining"), Adj("Dining", "Kitchen"),
    Adj("Living", "Hall"), Adj("Kitchen", "Utility"),
    # cross-zone connectors -- both between NEIGHBORING zones in the
    # alphabetical split order (garage | private | public)
    Adj("Garage", "PLHall"),       # garage <-> private
    Adj("Hall", "PLHall"),         # private <-> public
]

t = time.time()
plan, status, cross, zone_metrics = solve_zoned(fp, rooms, adj, zone_of, split_axis="x", time_limit=20)
print(f"status={status}  {time.time()-t:.1f}s")
if zone_metrics:
    for zname, (obj, bound, wt, st) in zone_metrics.items():
        print(f"  zone {zname}: status={st}  objective={obj}  bound={bound}  solve_wall_time={wt:.1f}s")

if plan:
    tot = 0
    for n, r in sorted(plan.items()):
        print(f"  {n:11s} {r['area']:4d} gu^2  (target {r['target']}, delta {r['area']-r['target']:+d})")
        tot += r["area"]
    print(f"  total {tot} / footprint {fp.area()}")

    print(f"\ncross-zone adjacencies satisfied: {cross['satisfied']}")
    print(f"cross-zone adjacencies FAILED:    {cross['failed']}")

    ok, unreachable = circulation_ok(plan, "Entry", private=("PrimBath", "Bath2"))
    print(f"\ncirculation ok: {ok}  unreachable: {unreachable}")

    openings = place_openings(plan, fp, adj, rooms)
    print(f"openings placed: {len(openings)}")
    print(to_svg(plan, fp, path="plan_zoned_3way.svg", openings=openings))

# sanity check: a cross-zone adjacency between non-neighboring zones
# (garage <-> public, skipping private) must be rejected up front, not
# silently mis-anchored.
try:
    solve_zoned(fp, rooms, adj + [Adj("Garage", "Entry")], zone_of, split_axis="x", time_limit=5)
    print("\nFAIL: expected ValueError for a non-adjacent-zone cross adjacency")
except ValueError as e:
    print(f"\nOK: non-adjacent-zone adjacency correctly rejected: {e}")
