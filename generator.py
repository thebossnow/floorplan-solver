"""
Turns a simple program (total area, bed count, bath count, footprint
shape, style) into a Room/Adj list that layout.solve() can consume.

STYLES holds a couple of named public-room mixes on top of a shared
bedroom/bathroom wing -- still not a full design system, but a first step
away from the single fixed proportional layout this started as. For
anything bespoke, build the Room/Adj list by hand instead.
"""

from typing import Dict, List, Tuple

from layout import Room, Adj, Footprint, Proximity, add_closets

MIN_AREA = 400
MAX_AREA = 10000
MAX_BEDS = 5
MAX_BATHS = 4

# static per-room shape constraints, independent of style or program size.
# min_dim is in grid-units (6in each -- see V2-ALPHA-PLAN.md's units
# migration); comments give the real-feet equivalent.
ROOM_SPECS = {
    "Entry":    dict(min_dim=10, max_aspect=2.5, edges=["S"]),  # 5ft
    "Living":   dict(min_dim=20, max_aspect=1.9),                # 10ft
    "Kitchen":  dict(min_dim=16, max_aspect=2.0),                 # 8ft
    "Dining":   dict(min_dim=16, max_aspect=1.8),                 # 8ft
    "Great":    dict(min_dim=28, max_aspect=2.2),                 # 14ft
    "Hall":     dict(min_dim=6,  max_aspect=8.0, needs_exterior=False),  # 3ft
    "Utility":  dict(min_dim=10, max_aspect=2.5),                 # 5ft
    "Primary":  dict(min_dim=22, max_aspect=1.7),                 # 11ft
    "PrimBath": dict(min_dim=10, max_aspect=2.5),                 # 5ft
}

# each style is just a different public-room mix (pcts/floors/adjacency);
# the bedroom/bathroom wing below is shared across all styles.
# floors are in grid-units^2 (1 grid-unit = 6in, so 1 sf = 4 grid-units^2);
# comments give the real-sf equivalent.
STYLES = {
    "traditional": dict(
        pcts={"Entry": 0.040, "Living": 0.205, "Kitchen": 0.133, "Dining": 0.100,
              "Hall": 0.050, "Utility": 0.050},
        floors={"Entry": 120, "Living": 560, "Kitchen": 360, "Dining": 280,
                "Hall": 120, "Utility": 120},  # 30,140,90,70,30,30 sf
        adj=[("Entry", "Living"), ("Living", "Dining"), ("Dining", "Kitchen"),
             ("Living", "Hall"), ("Kitchen", "Utility")],
    ),
    "open_concept": dict(
        # Living+Kitchen+Dining collapsed into one big "Great" room
        pcts={"Entry": 0.035, "Great": 0.360, "Hall": 0.045, "Utility": 0.045},
        floors={"Entry": 120, "Great": 1040, "Hall": 120, "Utility": 120},  # 30,260,30,30 sf
        adj=[("Entry", "Great"), ("Great", "Hall"), ("Great", "Utility")],
    ),
}

PRIMARY_PCT = 0.167
PRIMARY_BATH_PCT = 0.060
BED_PCT = 0.117       # each secondary bedroom
BATH_PCT = 0.042      # each secondary bathroom

# Each bedroom's closet is a percentage of that SAME bedroom's own target
# area, carved out before the closet is subtracted -- not a flat constant
# (that was the original design, CLOSET_AREA=80 regardless of bedroom
# size) -- changed 2026-09-02 after Phase 7 integration testing found a
# flat closet size geometrically incompatible with layout.solve()'s
# closet_align_width rule for any real bedroom (a fixed 20sf closet can
# never reach even 30% of an 11ft-min_dim Primary bedroom's width). Per
# 2026-09-02 user review: "the closet theoretically is a part of the
# bedroom, not an afterthought" -- CLOSET_PCT makes a bigger bedroom get
# a bigger closet, same as every other proportional room share here.
CLOSET_PCT = 0.15
CLOSET_MIN_AREA = 60  # grid-units^2 floor, so a small secondary bedroom's
                      # closet doesn't shrink toward nothing

PRIMARY_FLOOR = 440   # 110 sf
BED_FLOOR = 300       # 75 sf
BATH_FLOOR = 140      # 35 sf


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


# v1 prototype guard for the width slider (see templates/index.html) --
# not a real feasibility check (that's the "v2 alpha" rules-engine's job,
# see [[floorplan-solver-v2-alpha]]) -- just keeps the slider inside a
# range CP-SAT is actually likely to solve within TIME_LIMIT, instead of
# shipping a control that silently times out at its own extremes (e.g. a
# 250x10 footprint: almost every room's min_dim is too wide to fit across
# a 10ft strip).
WIDTH_ASPECT_MAX = 2.5   # long side : short side
WIDTH_MIN_SIDE = 20      # ft -- comfortably above the widest room min_dim
                         # (Great, 14ft) plus margin for halls/walls
                         #
                         # Stays in FEET, deliberately not grid-units, despite
                         # V2-ALPHA-PLAN.md's units-migration table listing this
                         # as ft->grid-units (20->40): width_bounds() is a public
                         # boundary function, same category as MIN_AREA/MAX_AREA
                         # (which that same table marks unchanged) -- it's called
                         # directly by templates/index.html's JS to size the HTML
                         # slider, and by make_footprint() to clamp the public
                         # `width` param, both still in feet. Converting it would
                         # silently break the live v1 form (still feet-only) on
                         # this branch. See make_footprint()'s docstring.


def width_bounds(total_area: int) -> Tuple[int, int]:
    """Usable (lo, hi) range for the width slider at a given area -- both
    footprint dimensions stay >= WIDTH_MIN_SIDE and the aspect ratio stays
    <= WIDTH_ASPECT_MAX, whichever is tighter. Mirrored in
    templates/index.html's JS so the slider's min/max can update live as
    the area slider moves, without a server round-trip."""
    lo = max(round((total_area / WIDTH_ASPECT_MAX) ** 0.5), WIDTH_MIN_SIDE)
    hi = min(round((total_area * WIDTH_ASPECT_MAX) ** 0.5),
             max(total_area // WIDTH_MIN_SIDE, WIDTH_MIN_SIDE))
    if lo > hi:
        mid = round(total_area ** 0.5)
        return mid, mid
    return lo, hi


def make_footprint(total_area: int, shape: str = "rectangular", aspect: float = 1.4,
                    width: int = None) -> Footprint:
    """total_area is public-facing square feet; width (when given) is public-
    facing feet, same boundary treatment -- both convert to grid-units
    (1 grid-unit = 6in) here, as the very first step, before any Footprint
    math. width_bounds()/WIDTH_MIN_SIDE/WIDTH_ASPECT_MAX stay in feet too
    (they're also public boundary -- used directly by the HTML slider),
    so `width` is clamped in feet, then converted."""
    area_gu = total_area * 4  # sf -> grid-units^2
    if width is not None:
        lo, hi = width_bounds(total_area)      # feet, public boundary
        width_ft = max(lo, min(width, hi))
        width_gu = width_ft * 2
        height_gu = max(round(area_gu / width_gu), 30)  # 15ft floor -> 30gu
        return Footprint(width=width_gu, height=height_gu)
    if shape == "square":
        side_gu = max(round(area_gu ** 0.5), 30)
        return Footprint(width=side_gu, height=side_gu)
    W_gu = max(round((area_gu * aspect) ** 0.5), 30)
    H_gu = max(round(area_gu / W_gu), 30)
    return Footprint(width=W_gu, height=H_gu)


def generate_program(total_area: int, beds: int = 3, baths: int = 2,
                      shape: str = "rectangular", style: str = "traditional",
                      width: int = None, has_entry: bool = True):
    """Returns (footprint, rooms, adjacencies, private_room_names). `width`
    (ft), when given, overrides `shape`/`aspect` entirely -- see
    make_footprint(). has_entry=False drops the separate Entry room --
    the style's arrival room (Living for traditional, Great for
    open_concept) takes over Entry's boundary-edge requirement instead,
    for a front door that opens directly into it."""
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}; choose from {sorted(STYLES)}")
    beds = max(1, min(beds, MAX_BEDS))
    baths = max(1, min(baths, MAX_BATHS))
    style_cfg = STYLES[style]

    fp = make_footprint(total_area, shape, width=width)
    F = fp.area()

    # public_names drives which rooms get built from ROOM_SPECS below --
    # kept as its own list (not read back off style_cfg["pcts"], which
    # stays the original, un-mutated style dict) so has_entry=False can
    # drop "Entry" from it without needing a second, differently-scoped
    # dict just for the room-construction loop.
    public_names = list(style_cfg["pcts"])
    pcts = dict(style_cfg["pcts"])
    floors = dict(style_cfg["floors"])
    style_adj = list(style_cfg["adj"])
    arrival = None
    if not has_entry:
        public_names.remove("Entry")
        del pcts["Entry"]
        del floors["Entry"]
        style_adj = [(a, b) for a, b in style_adj if "Entry" not in (a, b)]
        arrival = "Great" if "Great" in pcts else "Living"

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

    closet_areas = {b: max(round(targets[b] * CLOSET_PCT), CLOSET_MIN_AREA) for b in bedroom_names}
    for b in bedroom_names:
        targets[b] = max(targets[b] - closet_areas[b], floors.get(b, BED_FLOOR))

    rooms = []
    for name in public_names:
        specs = dict(ROOM_SPECS[name])
        if name == arrival:
            specs["edges"] = ["S"]
        rooms.append(Room(name, targets[name], **specs))
    rooms.append(Room("Primary", targets["Primary"], **ROOM_SPECS["Primary"]))
    rooms.append(Room("PrimBath", targets["PrimBath"], **ROOM_SPECS["PrimBath"]))
    for i in range(2, beds + 1):
        rooms.append(Room(f"Bed{i}", targets[f"Bed{i}"], min_dim=18, max_aspect=1.7))  # 9ft
    for i in range(2, baths + 1):
        rooms.append(Room(f"Bath{i}", targets[f"Bath{i}"], min_dim=10, max_aspect=2.5))  # 5ft

    adj = [Adj(a, b) for a, b in style_adj]
    adj.append(Adj("Hall", "Primary"))
    adj.append(Adj("Primary", "PrimBath"))
    for i in range(2, beds + 1):
        adj.append(Adj("Hall", f"Bed{i}"))
    for i in range(2, baths + 1):
        adj.append(Adj("Hall", f"Bath{i}"))

    rooms, adj = add_closets(rooms, adj, bedroom_names, area=closet_areas, min_dim=6, max_aspect=3.0)  # 3ft

    # only leaf rooms are private (can't be a hallway to somewhere else) --
    # bedrooms must stay non-private so BFS can relay through them to reach
    # their own ensuite bath/closet
    private = tuple(bathroom_names) + tuple(f"{b}Closet" for b in bedroom_names)
    return fp, rooms, adj, private


HALLWAYS = ("Hall",)  # solve()'s door-access hard constraint: every room not
    # in `private` (see above) must reach the exterior or one of these rooms
    # through a door-width wall segment. generate_program()'s Hall touches
    # every bedroom/secondary-bath in every style, so it's the right anchor.


PRODUCTION_WEIGHTS = dict(
    aspect_penalty_weight=1,
    compactness_weight=1,
    proximity_weight=1,
    # left off in production: at weight 1, ~150s to reach true OPTIMAL on a
    # 12-room house program (see HANDOFF), and the candidate-guideline-line
    # count that costs scales with footprint dimension in feet, not room
    # count -- a real risk on larger footprints. Revisit once that scaling
    # is addressed (see layout._guideline_usage).
    alignment_weight=0,
)

DEFAULT_PROXIMITY_PAIRS = (
    ("Kitchen", "Living"),
    ("Kitchen", "Dining"),
    ("Kitchen", "Great"),
    ("Primary", "PrimBath"),
)


def default_proximity(rooms: List[Room]) -> List[Proximity]:
    """Soft-proximity pairs for solve()'s proximity_weight, filtered to
    just the room names actually present in `rooms` -- covers both STYLES
    (Kitchen/Living/Dining vs. the merged Great room in open_concept) and
    a single zone's room subset (zoning.solve_zoned() only wants pairs
    where both rooms landed in the same zone)."""
    names = {r.name for r in rooms}
    return [Proximity(a, b) for a, b in DEFAULT_PROXIMITY_PAIRS if a in names and b in names]


ZONE_ROOM_THRESHOLD = 14   # more rooms than this: split into zones instead --
                           # matches README's documented ">15 rooms slows down"
                           # (a 15-room 4-bed/3-bath open_concept program was the
                           # one found genuinely INFEASIBLE unzoned in testing);
                           # the default 3-bed/2-bath program is 14 rooms and
                           # solves fine unzoned, so this shouldn't fire for typical inputs


def zone_of_program(rooms: List[Room]) -> Dict[str, str]:
    """Classifies a generate_program() room list into zones for
    zoning.solve_zoned(), for when a program is too large to hand to
    solve() as one model. Normally a 2-way "public"/"private" split: Hall
    touches every bedroom (not the public rooms) in every style above, so
    it's part of the private wing, not a public hub -- putting it in
    "public" instead turns one cross-zone connector into one per
    bedroom/bathroom, which is exactly the "too many cross-zone
    adjacencies on one room" case solve_zoned's docstring warns tends to
    fail.

    At the top of the beds/baths range (5 beds/4 baths -- MAX_BEDS/
    MAX_BATHS above), "private" alone comes out to 15 rooms, over
    ZONE_ROOM_THRESHOLD, which used to fail solve_zoned outright (each
    zone must itself stay under the threshold). In that case the private
    wing is split further into "suite" (Hall, the primary bedroom's own
    rooms, and -- see below -- the first secondary bedroom) and "wing"
    (every other secondary bedroom/bathroom/closet). Zone names are
    chosen so they sort alphabetically as public < suite < wing, matching
    the physical chain a style's own adjacencies imply (public's
    Living/Great touches Hall; Hall then needs to sit next to wing so the
    Hall-Bed{i}/Hall-Bath{i} cross-zone adjacencies have a shared wall to
    anchor to) -- solve_zoned() raises ValueError if this ordering doesn't
    hold, so don't rename these zones without re-checking it.

    Why "suite" also gets one secondary bedroom, not just Hall/Primary/
    PrimBath/PrimaryCloset: solve_zoned() sizes each zone's slab
    proportionally to its own room-area total, and Hall+Primary+PrimBath+
    PrimaryCloset alone is a small enough share of the whole footprint
    (~20% for the 5-bed/4-bath case) that the resulting slab comes out
    narrower than Primary's own max_aspect requires (Primary needs a
    ~16ft short side at max_aspect=1.7; a bare 4-room suite's slab came
    out ~15ft in testing, and the zone failed INFEASIBLE outright).
    Folding one secondary bedroom in widens suite's area share enough to
    clear that floor -- confirmed empirically for the 5-bed/4-bath case,
    not derived from a general formula, so re-check this if MAX_BEDS/
    MAX_BATHS ever change.

    Secondary bedrooms/baths in "wing" have no hallway room in their own
    zone, so solve()'s door-access constraint falls back to requiring
    them to touch the wing's own exterior directly instead of reaching
    Hall -- stricter than before, but physically reasonable (bedrooms
    plausibly want a window anyway). Some of the Hall-anchored cross-zone
    adjacencies (into both suite and wing) may still end up in
    solve_zoned's cross_report["failed"] at this room count -- expected
    given how many connectors compete for anchors on the same two walls;
    circulation_ok() still holds (every room reachable from Entry via
    *some* shared wall, not necessarily the originally-intended one),
    which is the guarantee that actually matters.

    Not meaningful for a hand-built room list that doesn't follow this
    naming convention."""
    zone_of = {}
    private_names = []
    for r in rooms:
        n = r.name
        if n == "Hall" or n in ("Primary", "PrimBath") or \
                n.startswith(("Bed", "Bath")) or n.endswith("Closet"):
            zone_of[n] = "private"
            private_names.append(n)
        else:
            zone_of[n] = "public"

    if len(private_names) > ZONE_ROOM_THRESHOLD:
        secondary_beds = sorted(
            (n for n in private_names if n.startswith("Bed") and not n.endswith("Closet")),
            key=lambda n: int(n[len("Bed"):]))
        suite_extra = {secondary_beds[0], f"{secondary_beds[0]}Closet"} if secondary_beds else set()
        for n in private_names:
            if n == "Hall" or n in ("Primary", "PrimBath", "PrimaryCloset") or n in suite_extra:
                zone_of[n] = "suite"
            else:
                zone_of[n] = "wing"

    return zone_of
