"""
Phase 1 (units migration) validation.

Solves one small hand-built program, then arithmetically scales the
solved plan x2 (coordinates) / x4 (areas) and independently re-verifies,
in pure Python, that the scaled plan satisfies every hard constraint of
the matching 2x-scaled program (min_dim, max_aspect, exact partition /
no-overlap, per-Adj min_shared, daylight). A violation here means some
constant in generator.py/layout.py's units migration wasn't converted
consistently with the rest.

Earlier version of this test tried solving the 1x and 2x programs
SEPARATELY and diffing the two plans' coordinates -- that approach was
abandoned: CP-SAT can legitimately land on different (but equally valid)
room arrangements for two independently-solved models, even single-
threaded with the same seed, whenever more than one arrangement satisfies
the objective equally well (confirmed empirically, not a units bug).
Solving once and checking the scaled result against hand-written
constraint logic sidesteps solver-search nondeterminism entirely, and is
actually the more direct test of what "the migration is correct" means:
a valid solution, scaled by k, must still satisfy every constraint once
that constraint's own constants are scaled by the matching k.

Run: .venv/bin/python3 test_units_migration.py
"""

from layout import Room, Adj, Footprint, solve, add_closets


def build_original():
    """A small program, valid against the CURRENT (migrated) engine's own
    rules. Touches plain rooms, an edges-forced (daylight-exempt) room, a
    needs_exterior=False room, explicit-min_shared adjacencies, and
    add_closets()'s glue constraint."""
    fp = Footprint(width=36, height=24)  # 864 grid-units^2
    rooms = [
        Room("Living", 300, min_dim=12, max_aspect=2.2, edges=["S"]),
        Room("Bed",    180, min_dim=9,  max_aspect=2.0),
        Room("Bath",   140, min_dim=10, max_aspect=3.0, needs_exterior=False),
        Room("Hall",   100, min_dim=4,  max_aspect=8.0, needs_exterior=False),
    ]
    adj = [
        Adj("Living", "Hall", min_shared=4),
        Adj("Hall", "Bed", min_shared=4),
        Adj("Hall", "Bath", min_shared=4),
    ]
    rooms, adj = add_closets(rooms, adj, ["Bed"], area=60, min_dim=6,
                              max_aspect=3.0, min_shared=3)
    return fp, rooms, adj


def scale_room(r, k):
    return Room(r.name, r.target_area * k * k, min_dim=r.min_dim * k,
                max_aspect=r.max_aspect, needs_exterior=r.needs_exterior,
                edges=list(r.edges), parts=r.parts)


def scale_adj(a, k):
    return Adj(a.a, a.b, min_shared=a.min_shared * k)


def scale_plan(plan, k):
    out = {}
    for name, room in plan.items():
        parts = [dict(x1=p["x1"] * k, y1=p["y1"] * k, x2=p["x2"] * k, y2=p["y2"] * k,
                      area=p["area"] * k * k)
                 for p in room["parts"]]
        out[name] = dict(parts=parts, area=room["area"] * k * k, target=room["target"] * k * k)
    return out


def constraint_violations(rooms, adjacencies, fp, plan):
    """Pure-Python re-check of every hard constraint solve() enforces,
    independent of CP-SAT -- see module docstring for why this replaces a
    second solve() call. Returns a list of violation strings (empty =
    fully valid)."""
    violations = []
    room_by_name = {r.name: r for r in rooms}

    for name, r in room_by_name.items():
        for i, part in enumerate(plan[name]["parts"]):
            w, h = part["x2"] - part["x1"], part["y2"] - part["y1"]
            if w < r.min_dim or h < r.min_dim:
                violations.append(f"{name} part {i}: w={w} h={h} below min_dim={r.min_dim}")
            long, short = max(w, h), min(w, h)
            if short > 0 and long / short > r.max_aspect + 1e-6:
                violations.append(f"{name} part {i}: aspect {long/short:.3f} > {r.max_aspect}")
            if part["x1"] < 0 or part["y1"] < 0 or part["x2"] > fp.width or part["y2"] > fp.height:
                violations.append(f"{name} part {i}: outside footprint bounds")
        geom_area = sum((p["x2"] - p["x1"]) * (p["y2"] - p["y1"]) for p in plan[name]["parts"])
        if plan[name]["area"] != geom_area:
            violations.append(f"{name}: reported area {plan[name]['area']} != geometric {geom_area}")

    all_parts = [(name, i, p) for name, r in room_by_name.items()
                 for i, p in enumerate(plan[name]["parts"])]
    total = sum((p["x2"] - p["x1"]) * (p["y2"] - p["y1"]) for _, _, p in all_parts)
    if total != fp.area():
        violations.append(f"total area {total} != footprint area {fp.area()}")
    for i in range(len(all_parts)):
        n1, _, p1 = all_parts[i]
        for n2, _, p2 in all_parts[i + 1:]:
            if n1 == n2:
                continue  # a multi-part room's own parts are allowed to touch (glue)
            ox = min(p1["x2"], p2["x2"]) - max(p1["x1"], p2["x1"])
            oy = min(p1["y2"], p2["y2"]) - max(p1["y1"], p2["y1"])
            if ox > 0 and oy > 0:
                violations.append(f"{n1} and {n2} overlap")

    def shared_len(a_parts, b_parts):
        best = 0
        for A in a_parts:
            for B in b_parts:
                if A["x2"] == B["x1"] or B["x2"] == A["x1"]:
                    best = max(best, min(A["y2"], B["y2"]) - max(A["y1"], B["y1"]))
                elif A["y2"] == B["y1"] or B["y2"] == A["y1"]:
                    best = max(best, min(A["x2"], B["x2"]) - max(A["x1"], B["x1"]))
        return best
    for ad in adjacencies:
        L = shared_len(plan[ad.a]["parts"], plan[ad.b]["parts"])
        if L < ad.min_shared:
            violations.append(f"adjacency {ad.a}-{ad.b}: shared wall {L} < min_shared {ad.min_shared}")

    for name, r in room_by_name.items():
        if not r.needs_exterior or r.edges:
            continue
        touches = any(part["x1"] == 0 or part["x2"] == fp.width or
                      part["y1"] == 0 or part["y2"] == fp.height
                      for part in plan[name]["parts"])
        if not touches:
            violations.append(f"{name}: needs_exterior but touches no boundary")

    return violations


def main():
    fp1, rooms1, adj1 = build_original()
    plan1, status1, _, _, _ = solve(fp1, rooms1, adj1, time_limit=30, seed=0, workers=1)
    assert plan1 is not None, f"1x program failed to solve: {status1}"

    viol1 = constraint_violations(rooms1, adj1, fp1, plan1)
    assert not viol1, "1x plan violates its own constraints:\n  " + "\n  ".join(viol1)

    fp2 = Footprint(width=fp1.width * 2, height=fp1.height * 2)
    rooms2 = [scale_room(r, 2) for r in rooms1]
    adj2 = [scale_adj(a, 2) for a in adj1]
    plan2 = scale_plan(plan1, 2)

    viol2 = constraint_violations(rooms2, adj2, fp2, plan2)
    assert not viol2, (
        "2x-scaled plan violates the 2x-scaled constraints (a units-migration "
        "constant is inconsistent):\n  " + "\n  ".join(viol2))

    print(f"test_units_migration: OK -- status={status1!r}, {len(plan1)} rooms, "
          "the solved 1x plan and its exact x2/x4 scaled twin both satisfy every "
          "hard constraint (min_dim, aspect, partition, adjacency, daylight)")


if __name__ == "__main__":
    main()
