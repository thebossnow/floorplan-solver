"""
Zone-split/stitch: the real fix for the >15-room slowdown documented in
README.md (a CP-SAT warm-start hint was tried first -- see
generator.shelf_pack_hint -- and benchmarked as no help).

Splits a room program into 2+ zones (e.g. public/private wing, or public/
private/garage), divides the footprint into that many adjacent slabs along
one axis -- ordered alphabetically by zone name, each slab sized
proportionally to its zone's room-area total -- and solves each zone
independently with the same exact solve() used everywhere else in this
project -- so within a zone, every hard constraint (no-overlap, exact
area, adjacency, daylight, aspect ratio) is still a real guarantee, not a
heuristic.

What's NOT a hard guarantee: adjacencies that cross a zone boundary.
Since zones are solved independently, there's no way to force a room in
one zone to land at the exact height along the dividing wall where its
neighbor-zone room ends up. The best this module can do is anchor both
rooms in a cross-zone adjacency to their shared wall (via Room.edges) and
then report, after the fact, which cross-zone adjacencies actually ended
up touching -- the same "solve exactly, then report what didn't work"
pattern circulation_ok() already uses for reachability. Keep cross-zone
adjacencies to a small number of "connector" rooms (a Hall linking to a
bedroom wing, for example) for the best odds of a real doorway.

A cross-zone adjacency only makes physical sense between two zones that
end up next to each other in the slab ordering -- there's no shared wall
between the 1st and 3rd of three zones laid out in a row. solve_zoned
raises ValueError up front if a requested adjacency spans non-adjacent
zones, rather than silently producing a plan that can't honor it.

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
from typing import Dict, Iterable, List, Optional, Tuple

from layout import Room, Adj, Footprint, Proximity, solve, shared_walls


def solve_zoned(footprint: Footprint,
                 rooms: List[Room],
                 adjacencies: List[Adj],
                 zone_of: Dict[str, str],
                 split_axis: str = "x",
                 time_limit: float = 30.0,
                 seed: int = 0,
                 workers: int = 8,
                 hallways: Optional[Iterable[str]] = None,
                 private: Optional[Iterable[str]] = None,
                 weights: Optional[Dict[str, int]] = None,
                 proximity: Optional[List[Proximity]] = None):
    """zone_of: {room_name: zone_name}, 2 or more distinct zone names,
    covering every room in `rooms`. split_axis: "x" lays the zones out
    west-to-east in alphabetical order of zone name, one slab per zone;
    "y" lays them out south-to-north the same way.

    hallways/private: passed through to each zone's own solve() call (see
    its docstring), filtered down to just the names present in that zone --
    a zone with no hallway room in it falls back to requiring plain exterior
    touch for every non-private room in that zone, same as if hallways were
    omitted. A cross-zone hallway (the common case: the hallway lives in one
    zone, a room needing it lives in the other) can't be satisfied this way,
    same limitation as any other cross-zone adjacency -- keep hallway rooms
    and everything that needs them in the same zone.

    weights: solve()'s four *_weight kwargs, splatted into every zone's own
    solve() call as-is (same weights on every zone). proximity: pairs
    filtered down to just the ones where both rooms landed in the same
    zone -- a cross-zone proximity pair can't be scored by either zone's
    independent solve, so it's silently dropped (not an error, same
    "best-effort across the zone boundary" spirit as cross_report below).

    Returns (plan, status, cross_report, zone_metrics). plan/status match
    solve()'s return shape (plan is None on failure, status names which
    zone failed and why). cross_report is None on failure, else
    {"satisfied": [(a,b), ...], "failed": [(a,b), ...]} for the
    cross-zone adjacencies -- check "failed" before assuming the plan
    matches the requested program. zone_metrics is None on failure, else
    {zone_name: (objective_value, best_objective_bound, wall_time)} from
    whichever of that zone's solve() calls actually produced its plan (the
    anchored attempt, or the anchor-free retry if the anchored one failed)
    -- not aggregated across zones, since each zone's objective is scored
    independently and summing/comparing them isn't meaningful.

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
    if len(zones) < 2:
        raise ValueError(f"solve_zoned needs at least 2 zones, got {zones}")
    zone_index = {z: i for i, z in enumerate(zones)}

    rooms_by_zone = {z: [] for z in zones}
    for r in rooms:
        rooms_by_zone[zone_of[r.name]].append(r)
    empty = [z for z in zones if not rooms_by_zone[z]]
    if empty:
        raise ValueError(f"every zone needs at least one room (empty: {empty})")

    intra = [ad for ad in adjacencies if zone_of[ad.a] == zone_of[ad.b]]
    cross = [ad for ad in adjacencies if zone_of[ad.a] != zone_of[ad.b]]
    intra_by_zone = {z: [ad for ad in intra if zone_of[ad.a] == z] for z in zones}

    # group cross adjacencies by which dividing wall they'd need to share --
    # wall i sits between zones[i] and zones[i+1]. An adjacency between
    # non-neighboring zones has no wall to anchor to under this linear slab
    # layout, so fail fast instead of silently dropping it.
    cross_by_wall: Dict[int, List[Adj]] = {}
    for ad in cross:
        ia, ib = zone_index[zone_of[ad.a]], zone_index[zone_of[ad.b]]
        if abs(ia - ib) != 1:
            raise ValueError(
                f"cross-zone adjacency {ad.a!r}-{ad.b!r} spans non-adjacent "
                f"zones {zone_of[ad.a]!r}/{zone_of[ad.b]!r} (zone order: "
                f"{zones}); only neighboring zones in the split order share a wall")
        cross_by_wall.setdefault(min(ia, ib), []).append(ad)

    # anchor every room that participates in a cross-zone adjacency to the
    # side of its own zone that faces the relevant dividing wall (which
    # wall) *and* pin it to a shared coordinate band on that wall (where on
    # it) -- an edge anchor alone only guarantees the two rooms are
    # somewhere on the same line, not that their extents actually overlap,
    # since each zone is solved with no visibility into where the other
    # placed its room. Each cross adjacency on a given wall gets its own
    # non-overlapping band, sized to its own min_shared, spread evenly
    # along that wall so two different connector pairs on the same wall
    # don't get pinned on top of each other. A room with cross adjacencies
    # on two different walls (possible for a middle zone with 3+ zones)
    # only keeps the last-processed anchor -- same "best effort" spirit as
    # everything else here; if that costs it feasibility, the per-zone
    # anchor-free retry below still gives it a real plan.
    perp_dim = footprint.height if split_axis == "x" else footprint.width
    anchor_edge, must_cover = {}, {}
    for wall_i, group in cross_by_wall.items():
        n = len(group)
        for i, ad in enumerate(group):
            slot = perp_dim / n
            width = max(ad.min_shared, min(ad.min_shared + 2, slot - 2))
            center = slot * (i + 0.5)
            band_lo = max(0, round(center - width / 2))
            band_hi = min(perp_dim, band_lo + round(width))
            for name in (ad.a, ad.b):
                zi = zone_index[zone_of[name]]
                if split_axis == "x":
                    anchor_edge[name] = "E" if zi == wall_i else "W"
                else:
                    anchor_edge[name] = "N" if zi == wall_i else "S"
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

    # N-way slab split along the chosen axis, ordered by `zones`, each
    # slab sized proportionally to its zone's room-area total (the same
    # rounding pattern as generator._fit_targets: floor every slab at 1,
    # then dump the rounding remainder onto the biggest slab so widths
    # always sum to exactly `dim`).
    dim = footprint.width if split_axis == "x" else footprint.height
    if dim < len(zones):
        raise ValueError(
            f"footprint too small ({dim} along {split_axis!r}) to split "
            f"into {len(zones)} zones -- need at least 1 unit per zone")
    zone_sum = {z: sum(r.target_area for r in rooms_by_zone[z]) for z in zones}
    total_sum = sum(zone_sum.values())
    slab = {z: max(1, round(dim * zone_sum[z] / total_sum)) for z in zones}
    drift = dim - sum(slab.values())
    if drift:
        biggest = max(zones, key=lambda z: slab[z])
        slab[biggest] += drift
    if any(w < 1 for w in slab.values()):
        raise ValueError(
            f"footprint too small ({dim} along {split_axis!r}) to fit "
            f"{len(zones)} zones proportionally to their room areas")

    offset, cum = {}, 0
    for z in zones:
        offset[z] = cum
        cum += slab[z]

    fp_by_zone, origin_by_zone = {}, {}
    for z in zones:
        if split_axis == "x":
            fp_by_zone[z] = Footprint(width=slab[z], height=footprint.height)
            origin_by_zone[z] = (offset[z], 0)
        else:
            fp_by_zone[z] = Footprint(width=footprint.width, height=slab[z])
            origin_by_zone[z] = (0, offset[z])

    all_hallways = set(hallways or ())
    all_private = set(private or ())
    all_proximity = list(proximity or ())
    weights = weights or {}

    def solve_zone(fp_zone, zone_rooms, zone_adj):
        zone_names = {r.name for r in zone_rooms}
        zone_hallways = all_hallways & zone_names
        zone_private = all_private & zone_names
        zone_proximity = [pr for pr in all_proximity if pr.a in zone_names and pr.b in zone_names]
        plan, status, objective_value, best_objective_bound, wall_time = solve(
            fp_zone, anchored(zone_rooms), zone_adj,
            time_limit=time_limit, seed=seed, workers=workers,
            hallways=zone_hallways, private=zone_private,
            proximity=zone_proximity, **weights)
        if plan:
            return plan, status, (objective_value, best_objective_bound, wall_time)
        # the cross-zone anchor(s) may be what made this infeasible -- retry
        # without them rather than failing a zone that's solvable on its own
        plan, status, objective_value, best_objective_bound, wall_time = solve(
            fp_zone, zone_rooms, zone_adj,
            time_limit=time_limit, seed=seed, workers=workers,
            hallways=zone_hallways, private=zone_private,
            proximity=zone_proximity, **weights)
        return plan, status, (objective_value, best_objective_bound, wall_time)

    plans, statuses, zone_metrics = {}, {}, {}
    for z in zones:
        plan_z, status_z, metrics_z = solve_zone(fp_by_zone[z], rooms_by_zone[z], intra_by_zone[z])
        if not plan_z:
            return None, f"zone {z!r} failed: {status_z}", None, None
        plans[z], statuses[z], zone_metrics[z] = plan_z, status_z, metrics_z

    def translate(plan, dx, dy):
        out = {}
        for name, room in plan.items():
            parts = [dict(x1=p["x1"] + dx, y1=p["y1"] + dy,
                          x2=p["x2"] + dx, y2=p["y2"] + dy, area=p["area"])
                     for p in room["parts"]]
            out[name] = dict(parts=parts, area=room["area"], target=room["target"])
        return out

    merged = {}
    for z in zones:
        merged.update(translate(plans[z], *origin_by_zone[z]))

    wall_len = {frozenset((a, b)): length for a, b, _, length in shared_walls(merged, min_len=1)}
    satisfied, failed = [], []
    for ad in cross:
        if wall_len.get(frozenset((ad.a, ad.b)), 0) >= ad.min_shared:
            satisfied.append((ad.a, ad.b))
        else:
            failed.append((ad.a, ad.b))

    status = ", ".join(f"zone {z!r}: {statuses[z]}" for z in zones)
    return merged, status, dict(satisfied=satisfied, failed=failed), zone_metrics
