from layout import Room, Adj, Footprint, circulation_ok, place_openings, to_svg
from zoning import solve_zoned
import time

# 16-room program split into a public zone and a private (bedroom) zone --
# this is the case that (per README) slows solve() down badly as one model;
# solving each zone separately is the documented fix.
fp = Footprint(width=56, height=40)  # 2240 sf

public_rooms = [
    Room("Entry",   60,  min_dim=6,  max_aspect=2.5, edges=["S"]),
    Room("Living", 280,  min_dim=12, max_aspect=1.8),
    Room("Kitchen",190,  min_dim=10, max_aspect=2.0),
    Room("Dining", 150,  min_dim=10, max_aspect=1.8),
    Room("Hall",   100,  min_dim=4,  max_aspect=8.0, needs_exterior=False),
    Room("Family", 210,  min_dim=12, max_aspect=1.8),
    Room("Utility", 70,  min_dim=6,  max_aspect=2.5, needs_exterior=False),
]
private_rooms = [
    Room("Primary",   230, min_dim=12, max_aspect=1.6),
    Room("PrimBath",   90, min_dim=6,  max_aspect=2.5),
    Room("Bed2",       160, min_dim=10, max_aspect=1.6),
    Room("Bed3",       150, min_dim=10, max_aspect=1.6),
    Room("Bed4",       140, min_dim=10, max_aspect=1.6),
    Room("Bath2",       60, min_dim=5,  max_aspect=2.5),
    Room("Bath3",       60, min_dim=5,  max_aspect=2.5),
    Room("PLHall",     100, min_dim=4,  max_aspect=8.0, needs_exterior=False),
]

rooms = public_rooms + private_rooms
target_sum = sum(r.target_area for r in rooms)
scale = fp.area() / target_sum
for r in rooms:
    r.target_area = round(r.target_area * scale)

zone_of = {r.name: "public" for r in public_rooms}
zone_of.update({r.name: "private" for r in private_rooms})

adj = [
    Adj("Entry", "Living"), Adj("Living", "Dining"), Adj("Dining", "Kitchen"),
    Adj("Living", "Hall"), Adj("Living", "Family"), Adj("Kitchen", "Utility"),
    # private wing's own internal hall -- kept to a modest degree (4 rooms,
    # not 6+) since the cross-zone anchor below is one more hard constraint
    # competing for PLHall's perimeter; overload it and solve_zoned's
    # fallback will still solve the zone, just without the doorway (see
    # zoning.py's docstring)
    Adj("PLHall", "Primary"), Adj("Primary", "PrimBath"),
    Adj("PLHall", "Bed2"), Adj("Bed2", "Bath2"),
    Adj("PLHall", "Bed3"), Adj("Bed3", "Bath3"),
    Adj("PLHall", "Bed4"),
    # the one connector across the zone boundary
    Adj("Hall", "PLHall"),
]

t = time.time()
plan, status, cross = solve_zoned(fp, rooms, adj, zone_of, split_axis="x", time_limit=20)
print(f"status={status}  {time.time()-t:.1f}s")

if plan:
    tot = 0
    for n, r in sorted(plan.items()):
        print(f"  {n:9s} {r['area']:4d} sf  (target {r['target']}, delta {r['area']-r['target']:+d})")
        tot += r["area"]
    print(f"  total {tot} / footprint {fp.area()}")

    print(f"\ncross-zone adjacencies satisfied: {cross['satisfied']}")
    print(f"cross-zone adjacencies FAILED:    {cross['failed']}")

    ok, unreachable = circulation_ok(plan, "Entry", private=("PrimBath", "Bath2", "Bath3"))
    print(f"\ncirculation ok: {ok}  unreachable: {unreachable}")

    openings = place_openings(plan, fp, adj, rooms)
    print(f"openings placed: {len(openings)}")
    print(to_svg(plan, fp, path="plan_zoned.svg", openings=openings))
