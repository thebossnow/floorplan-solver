"""
Zone-split/stitch: the real fix for the >15-room slowdown documented in
README.md (a CP-SAT warm-start hint was tried first -- see
generator.shelf_pack_hint -- and benchmarked as no help).

Splits a room program into exactly two zones (e.g. public/private wing),
divides the footprint into two adjacent sub-footprints along one axis
sized proportionally to each zone's room-area total, and solves each zone
independently with the same exact solve() used everywhere else in this
project -- so within a zone, every hard constraint (no-overlap, exact
area, adjacency, daylight, aspect ratio) is still a real guarantee, not a
heuristic.

What's NOT a hard guarantee: adjacencies that cross the zone boundary.
Since the two zones are solved independently, there's no way to force a
room in zone A to land at the exact height along the dividing wall where
its zone-B neighbor ends up. The best this module can do is anchor both
rooms in a cross-zone adjacency to the shared wall (via Room.edges) and
then report, after the fact, which cross-zone adjacencies actually ended
up touching -- the same "solve exactly, then report what didn't work"
pattern circulation_ok() already uses for reachability. Keep cross-zone
adjacencies to a small number of "connector" rooms (a Hall linking to a
bedroom wing, for example) for the best odds of a real doorway.

That anchor is a hard constraint on top of whatever else the connector
room already owes (its other adjacencies, aspect ratio, min_dim), and
those can conflict badly enough to make an otherwise-solvable zone
INFEASIBLE outright (seen with a hallway anchored to the dividing wall
while also required to touch 6 bedrooms in a narrow zone). So each zone
is tried once with its cross-zone anchors applied, and -- only if that
comes back infeasible -- retried without them. A zone that only fails
because of its own anchors still solves; it just can't promise the
cross-zone doorway that anchor was trying to win.
"""

from dataclasses import replace
from typing import Dict, List, Tuple

from layout import Room, Adj, Footprint, solve, shared_walls


def solve_zoned(footprint: Footprint,
                 rooms: List[Room],
                 adjacencies: List[Adj],
                 zone_of: Dict[str, str],
                 split_axis: str = "x",
                 time_limit: float = 30.0,
                 seed: int = 0,
                 workers: int = 8):
    """zone_of: {room_name: zone_name}, exactly two distinct zone names,
    covering every room in `rooms`. split_axis: "x" splits the footprint
    into a west zone (alphabetically first zone name) and an east zone;
    "y" splits into south/north the same way.

    Returns (plan, status, cross_report). plan/status match solve()'s
    return shape (plan is None on failure, status names which zone failed
    and why). cross_report is None on failure, else
    {"satisfied": [(a,b), ...], "failed": [(a,b), ...]} for the
    cross-zone adjacencies -- check "failed" before assuming the plan
    matches the requested program.

    Doesn't support footprint voids yet -- raises ValueError if
    footprint.voids is non-empty."""

    if footprint.voids:
        raise ValueError("solve_zoned doesn't support footprint voids yet")
    if split_axis not in ("x", "y"):
        raise ValueError(f"split_axis must be 'x' or 'y', got {split_axis!r}")

    names = {r.name for r in rooms}
    missing = names - set(zone_of)
    if missing:
        raise ValueError(f"zone_of is missing rooms: {sorted(missing)}")
    zones = sorted(set(zone_of[n] for n in names))
    if len(zones) != 2:
        raise ValueError(f"solve_zoned needs exactly 2 zones, got {zones}")
    zone_a, zone_b = zones

    rooms_by_zone = {zone_a: [], zone_b: []}
    for r in rooms:
        rooms_by_zone[zone_of[r.name]].append(r)
    if not rooms_by_zone[zone_a] or not rooms_by_zone[zone_b]:
        raise ValueError(f"both zones need at least one room ({zone_a}, {zone_b})")

    intra = [ad for ad in adjacencies if zone_of[ad.a] == zone_of[ad.b]]
    cross = [ad for ad in adjacencies if zone_of[ad.a] != zone_of[ad.b]]

    # anchor every room that participates in a cross-zone adjacency to the
    # side of its own zone that faces the dividing wall (which wall) *and*
    # pin it to a shared coordinate band on that wall (where on it) -- an
    # edge anchor alone only guarantees the two rooms are somewhere on the
    # same line, not that their extents actually overlap, since each zone
    # is solved with no visibility into where the other placed its room.
    # Each cross adjacency gets its own non-overlapping band, sized to its
    # own min_shared, spread evenly along the shared wall so two different
    # connector pairs on the same side don't get pinned on top of each other.
    perp_dim = footprint.height if split_axis == "x" else footprint.width
    anchor_edge, must_cover = {}, {}
    n = len(cross)
    for i, ad in enumerate(cross):
        slot = perp_dim / n
        width = max(ad.min_shared, min(ad.min_shared + 2, slot - 2))
        center = slot * (i + 0.5)
        band_lo = max(0, round(center - width / 2))
        band_hi = min(perp_dim, band_lo + round(width))
        for name in (ad.a, ad.b):
            z = zone_of[name]
            if split_axis == "x":
                anchor_edge[name] = "E" if z == zone_a else "W"
            else:
                anchor_edge[name] = "N" if z == zone_a else "S"
            must_cover[name] = band_lo, band_hi

    cover_axis = "y" if split_axis == "x" else "x"

    def anchored(zone_rooms):
        out = []
        for r in zone_rooms:
            edge = anchor_edge.get(r.name)
            if edge and edge not in r.edges:
                r = replace(r, edges=list(r.edges) + [edge])
            if r.name in must_cover and r.must_cover is None:
                r = replace(r, must_cover=(cover_axis, *must_cover[r.name]))
            out.append(r)
        return out

    dim = footprint.width if split_axis == "x" else footprint.height
    sum_a = sum(r.target_area for r in rooms_by_zone[zone_a])
    sum_b = sum(r.target_area for r in rooms_by_zone[zone_b])
    split_at = max(1, min(round(dim * sum_a / (sum_a + sum_b)), dim - 1))

    if split_axis == "x":
        fp_a = Footprint(width=split_at, height=footprint.height)
        fp_b = Footprint(width=footprint.width - split_at, height=footprint.height)
        origin_a, origin_b = (0, 0), (split_at, 0)
    else:
        fp_a = Footprint(width=footprint.width, height=split_at)
        fp_b = Footprint(width=footprint.width, height=footprint.height - split_at)
        origin_a, origin_b = (0, 0), (0, split_at)

    intra_a = [ad for ad in intra if zone_of[ad.a] == zone_a]
    intra_b = [ad for ad in intra if zone_of[ad.a] == zone_b]

    def solve_zone(fp_zone, zone_rooms, zone_adj):
        plan, status = solve(fp_zone, anchored(zone_rooms), zone_adj,
                              time_limit=time_limit, seed=seed, workers=workers)
        if plan:
            return plan, status
        # the cross-zone anchor(s) may be what made this infeasible -- retry
        # without them rather than failing a zone that's solvable on its own
        return solve(fp_zone, zone_rooms, zone_adj,
                      time_limit=time_limit, seed=seed, workers=workers)

    plan_a, status_a = solve_zone(fp_a, rooms_by_zone[zone_a], intra_a)
    if not plan_a:
        return None, f"zone {zone_a!r} failed: {status_a}", None

    plan_b, status_b = solve_zone(fp_b, rooms_by_zone[zone_b], intra_b)
    if not plan_b:
        return None, f"zone {zone_b!r} failed: {status_b}", None

    def translate(plan, dx, dy):
        out = {}
        for name, room in plan.items():
            parts = [dict(x1=p["x1"] + dx, y1=p["y1"] + dy,
                          x2=p["x2"] + dx, y2=p["y2"] + dy, area=p["area"])
                     for p in room["parts"]]
            out[name] = dict(parts=parts, area=room["area"], target=room["target"])
        return out

    merged = {**translate(plan_a, *origin_a), **translate(plan_b, *origin_b)}

    wall_len = {frozenset((a, b)): length for a, b, _, length in shared_walls(merged, min_len=1)}
    satisfied, failed = [], []
    for ad in cross:
        if wall_len.get(frozenset((ad.a, ad.b)), 0) >= ad.min_shared:
            satisfied.append((ad.a, ad.b))
        else:
            failed.append((ad.a, ad.b))

    status = f"zone {zone_a!r}: {status_a}, zone {zone_b!r}: {status_b}"
    return merged, status, dict(satisfied=satisfied, failed=failed)
