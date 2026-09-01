from layout import Room, Adj, Footprint, solve, shared_walls, circulation_ok, to_svg, place_openings
import time

fp = Footprint(width=24, height=20)   # 480 sf

rooms = [
    Room("Entry",    40,  min_dim=5, max_aspect=2.5, edges=["S"]),
    Room("Living",  200,  min_dim=8, max_aspect=2.5, parts=2),   # L-shaped
    Room("Kitchen", 120,  min_dim=8, max_aspect=2.0),
    Room("Bath",     60,  min_dim=5, max_aspect=2.5, needs_exterior=False),
    Room("Closet",   60,  min_dim=4, max_aspect=3.0, needs_exterior=False),
]

adj = [
    Adj("Entry", "Living"),
    Adj("Living", "Kitchen"),
    Adj("Living", "Bath"),
    Adj("Kitchen", "Closet"),
]

t = time.time()
plan, status, objective_value, best_objective_bound, wall_time = solve(fp, rooms, adj, time_limit=60)
print(f"status={status}  {time.time()-t:.1f}s  "
      f"objective={objective_value}  bound={best_objective_bound}  solve_wall_time={wall_time:.1f}s")

if plan:
    tot = 0
    for n, r in sorted(plan.items()):
        dims = " + ".join(f"{p['x2']-p['x1']}x{p['y2']-p['y1']}" for p in r["parts"])
        print(f"  {n:9s} {len(r['parts'])} part(s)  {dims:16s} = {r['area']:4d} sf  "
              f"(target {r['target']}, delta {r['area']-r['target']:+d})")
        tot += r["area"]
    print(f"  total {tot} / footprint {fp.area()}")

    a, b = plan["Living"]["parts"]
    glued = (a["x2"] == b["x1"] or b["x2"] == a["x1"] or
             a["y2"] == b["y1"] or b["y2"] == a["y1"])
    print(f"\nLiving parts glued into one L: {glued}")

    ok, unreachable = circulation_ok(plan, "Entry", private=("Bath",))
    print(f"circulation ok: {ok}  unreachable: {unreachable}")
    print(f"shared walls: {len(shared_walls(plan))}")
    openings = place_openings(plan, fp, adj, rooms)
    print(f"openings placed: {len(openings)}")
    print(to_svg(plan, fp, path="plan_lshape.svg", openings=openings))
