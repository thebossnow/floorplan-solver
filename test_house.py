from layout import Room, Adj, Footprint, solve, shared_walls, circulation_ok, to_svg
import time

fp = Footprint(width=40, height=30)   # 1200 sf

rooms = [
    Room("Entry",     48,  min_dim=6, max_aspect=2.5, edges=["S"]),
    Room("Living",   260,  min_dim=12, max_aspect=1.8),
    Room("Kitchen",  170,  min_dim=10, max_aspect=2.0),
    Room("Dining",   130,  min_dim=10, max_aspect=1.8),
    Room("Hall",      70,  min_dim=4,  max_aspect=8.0, needs_exterior=False),
    Room("Primary",  200,  min_dim=12, max_aspect=1.6),
    Room("PrimBath",  72,  min_dim=6,  max_aspect=2.5),
    Room("Bed2",     140,  min_dim=10, max_aspect=1.6),
    Room("Bath2",     50,  min_dim=5,  max_aspect=2.5),
    Room("Utility",   60,  min_dim=6,  max_aspect=2.5, needs_exterior=False),
]

adj = [
    Adj("Entry", "Living"),
    Adj("Living", "Dining"),
    Adj("Dining", "Kitchen"),
    Adj("Living", "Hall"),
    Adj("Hall", "Primary"),
    Adj("Hall", "Bed2"),
    Adj("Hall", "Bath2"),
    Adj("Primary", "PrimBath"),
    Adj("Kitchen", "Utility"),
]

t = time.time()
plan, status = solve(fp, rooms, adj, time_limit=60)
print(f"status={status}  {time.time()-t:.1f}s")

if plan:
    tot = 0
    for n, r in sorted(plan.items()):
        dims = "+".join(f"{p['x2']-p['x1']}x{p['y2']-p['y1']}" for p in r["parts"])
        print(f"  {n:9s} {dims:9s} = {r['area']:4d} sf   (target {r['target']}, "
              f"delta {r['area']-r['target']:+d})")
        tot += r["area"]
    print(f"  total {tot} / footprint {fp.area()}")

    ok, unreachable = circulation_ok(plan, "Entry",
                                     private=("Primary", "Bed2", "PrimBath", "Bath2"))
    print(f"\ncirculation ok: {ok}  unreachable: {unreachable}")
    print(f"shared walls: {len(shared_walls(plan))}")
    print(to_svg(plan, fp, path="plan.svg"))
