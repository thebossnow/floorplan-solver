"""
Turns a simple program (total area, bed count, bath count, footprint
shape, style) into a Room/Adj list that layout.solve() can consume.

STYLES holds a couple of named public-room mixes on top of a shared
bedroom/bathroom wing -- still not a full design system, but a first step
away from the single fixed proportional layout this started as. For
anything bespoke, build the Room/Adj list by hand instead.
"""

from typing import Dict, List, Tuple

from layout import Room, Adj, Footprint, add_closets

MIN_AREA = 400
MAX_AREA = 10000
MAX_BEDS = 5
MAX_BATHS = 4

# static per-room shape constraints, independent of style or program size
ROOM_SPECS = {
    "Entry":    dict(min_dim=5,  max_aspect=2.5, edges=["S"]),
    "Living":   dict(min_dim=10, max_aspect=1.9),
    "Kitchen":  dict(min_dim=8,  max_aspect=2.0),
    "Dining":   dict(min_dim=8,  max_aspect=1.8),
    "Great":    dict(min_dim=14, max_aspect=2.2),
    "Hall":     dict(min_dim=3,  max_aspect=8.0, needs_exterior=False),
    "Utility":  dict(min_dim=5,  max_aspect=2.5),
    "Primary":  dict(min_dim=11, max_aspect=1.7),
    "PrimBath": dict(min_dim=5,  max_aspect=2.5),
}

# each style is just a different public-room mix (pcts/floors/adjacency);
# the bedroom/bathroom wing below is shared across all styles
STYLES = {
    "traditional": dict(
        pcts={"Entry": 0.040, "Living": 0.205, "Kitchen": 0.133, "Dining": 0.100,
              "Hall": 0.050, "Utility": 0.050},
        floors={"Entry": 30, "Living": 140, "Kitchen": 90, "Dining": 70,
                "Hall": 30, "Utility": 30},
        adj=[("Entry", "Living"), ("Living", "Dining"), ("Dining", "Kitchen"),
             ("Living", "Hall"), ("Kitchen", "Utility")],
    ),
    "open_concept": dict(
        # Living+Kitchen+Dining collapsed into one big "Great" room
        pcts={"Entry": 0.035, "Great": 0.360, "Hall": 0.045, "Utility": 0.045},
        floors={"Entry": 30, "Great": 260, "Hall": 30, "Utility": 30},
        adj=[("Entry", "Great"), ("Great", "Hall"), ("Great", "Utility")],
    ),
}

PRIMARY_PCT = 0.167
PRIMARY_BATH_PCT = 0.060
BED_PCT = 0.117       # each secondary bedroom
BATH_PCT = 0.042      # each secondary bathroom
CLOSET_AREA = 20      # sf, carved out of each bedroom's own target

PRIMARY_FLOOR = 110
BED_FLOOR = 75
BATH_FLOOR = 35


def _fit_targets(pcts: Dict[str, float], floors: Dict[str, int], F: int) -> Dict[str, int]:
    """Turn area percentages into integer sf targets that sum to exactly F,
    respecting each room's floor. Floor-clamped rooms are held fixed; every
    other room absorbs the remainder proportionally to its own share, so
    solve()'s default +/-15% area bounds always bracket F regardless of
    style, bed/bath count, or how hard the floors bite -- no separate
    feasibility check needed at generation time (validate_program() in
    layout.py still catches the rare case where the floors alone exceed F,
    e.g. a tiny total_area with many bedrooms)."""
    total_pct = sum(pcts.values())
    raw = {n: pct / total_pct * F for n, pct in pcts.items()}
    targets = {n: max(round(v), floors.get(n, 0)) for n, v in raw.items()}
    floor_hit = {n for n in pcts if targets[n] > raw[n]}
    free = [n for n in pcts if n not in floor_hit]
    remainder = F - sum(targets[n] for n in floor_hit)
    free_pct_total = sum(pcts[n] for n in free)
    if free and free_pct_total > 0:
        for n in free:
            targets[n] = max(round(pcts[n] / free_pct_total * remainder), floors.get(n, 0))
        drift = F - sum(targets.values())
        if drift:
            biggest = max(free, key=lambda n: targets[n])
            targets[biggest] += drift
    return targets


def shelf_pack_hint(footprint: Footprint, rooms: List[Room]) -> Dict[str, Tuple[int, int, int, int]]:
    """Rough shelf-packing layout used only as a CP-SAT warm-start hint for
    solve() -- it doesn't need to be feasible (rooms may run past the
    footprint's height; those are simply left unhinted), just a starting
    point closer to a real solution than CP-SAT's own default assignment.
    Only covers single-part rooms (multi-part rooms are keyed as
    f"{name}#{i}" in solve()'s part-key namespace, which this doesn't
    populate -- CP-SAT places those unassisted).

    NOTE: benchmarked against CP-SAT's default 8-worker search on 18-room
    programs, this did not reduce solve time or improve solution quality
    (see README) -- kept as opt-in infrastructure, not a proven fix for
    the documented >15-room slowdown."""
    W, H = footprint.width, footprint.height
    hint = {}
    x, y, shelf_h = 0, 0, 0
    for r in sorted(rooms, key=lambda r: -r.target_area):
        if r.parts != 1:
            continue
        side = max(r.min_dim, round(r.target_area ** 0.5))
        w = min(side, W)
        h = max(r.min_dim, round(r.target_area / w))
        if x + w > W:
            x, y, shelf_h = 0, y + shelf_h, 0
        if y >= H:
            continue
        hint[r.name] = (x, y, min(x + w, W), min(y + h, H))
        x += w
        shelf_h = max(shelf_h, h)
    return hint


def make_footprint(total_area: int, shape: str = "rectangular", aspect: float = 1.4) -> Footprint:
    if shape == "square":
        side = max(round(total_area ** 0.5), 15)
        return Footprint(width=side, height=side)
    W = max(round((total_area * aspect) ** 0.5), 15)
    H = max(round(total_area / W), 15)
    return Footprint(width=W, height=H)


def generate_program(total_area: int, beds: int = 3, baths: int = 2,
                      shape: str = "rectangular", style: str = "traditional"):
    """Returns (footprint, rooms, adjacencies, private_room_names)."""
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}; choose from {sorted(STYLES)}")
    beds = max(1, min(beds, MAX_BEDS))
    baths = max(1, min(baths, MAX_BATHS))
    style_cfg = STYLES[style]

    fp = make_footprint(total_area, shape)
    F = fp.area()

    pcts = dict(style_cfg["pcts"])
    floors = dict(style_cfg["floors"])
    pcts["Primary"] = PRIMARY_PCT
    floors["Primary"] = PRIMARY_FLOOR
    pcts["PrimBath"] = PRIMARY_BATH_PCT
    floors["PrimBath"] = BATH_FLOOR
    for i in range(2, beds + 1):
        pcts[f"Bed{i}"] = BED_PCT
        floors[f"Bed{i}"] = BED_FLOOR
    for i in range(2, baths + 1):
        pcts[f"Bath{i}"] = BATH_PCT
        floors[f"Bath{i}"] = BATH_FLOOR

    targets = _fit_targets(pcts, floors, F)

    bedroom_names = ["Primary"] + [f"Bed{i}" for i in range(2, beds + 1)]
    bathroom_names = ["PrimBath"] + [f"Bath{i}" for i in range(2, baths + 1)]

    for b in bedroom_names:
        targets[b] = max(targets[b] - CLOSET_AREA, floors.get(b, BED_FLOOR))

    rooms = [Room(name, targets[name], **ROOM_SPECS[name]) for name in style_cfg["pcts"]]
    rooms.append(Room("Primary", targets["Primary"], **ROOM_SPECS["Primary"]))
    rooms.append(Room("PrimBath", targets["PrimBath"], **ROOM_SPECS["PrimBath"]))
    for i in range(2, beds + 1):
        rooms.append(Room(f"Bed{i}", targets[f"Bed{i}"], min_dim=9, max_aspect=1.7))
    for i in range(2, baths + 1):
        rooms.append(Room(f"Bath{i}", targets[f"Bath{i}"], min_dim=5, max_aspect=2.5))

    adj = [Adj(a, b) for a, b in style_cfg["adj"]]
    adj.append(Adj("Hall", "Primary"))
    adj.append(Adj("Primary", "PrimBath"))
    for i in range(2, beds + 1):
        adj.append(Adj("Hall", f"Bed{i}"))
    for i in range(2, baths + 1):
        adj.append(Adj("Hall", f"Bath{i}"))

    rooms, adj = add_closets(rooms, adj, bedroom_names, area=CLOSET_AREA, min_dim=3, max_aspect=3.0)

    # only leaf rooms are private (can't be a hallway to somewhere else) --
    # bedrooms must stay non-private so BFS can relay through them to reach
    # their own ensuite bath/closet
    private = tuple(bathroom_names) + tuple(f"{b}Closet" for b in bedroom_names)
    return fp, rooms, adj, private


HALLWAYS = ("Hall",)  # solve()'s door-access hard constraint: every room not
    # in `private` (see above) must reach the exterior or one of these rooms
    # through a door-width wall segment. generate_program()'s Hall touches
    # every bedroom/secondary-bath in every style, so it's the right anchor.


def zone_of_program(rooms: List[Room]) -> Dict[str, str]:
    """Classifies a generate_program() room list into the "public"/"private"
    zones zoning.solve_zoned() needs, for when a program is too large to
    hand to solve() as one model. Hall touches every bedroom (not the
    public rooms) in every style above, so it's part of the private wing,
    not a public hub -- putting it in "public" instead turns one cross-zone
    connector into one per bedroom/bathroom, which is exactly the
    "too many cross-zone adjacencies on one room" case solve_zoned's
    docstring warns tends to fail. Not meaningful for a hand-built room
    list that doesn't follow this naming convention."""
    zone_of = {}
    for r in rooms:
        n = r.name
        if n == "Hall" or n in ("Primary", "PrimBath") or \
                n.startswith(("Bed", "Bath")) or n.endswith("Closet"):
            zone_of[n] = "private"
        else:
            zone_of[n] = "public"
    return zone_of
