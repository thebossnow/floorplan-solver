"""
Constraint-solver floor plan generator.

Models a floor plan as an exact rectangular partition of a footprint.
No training data, no model weights. OR-Tools CP-SAT does the search.

A room is normally a single rectangle. Setting Room.parts > 1 lets a
room be built from several rectangles glued edge-to-edge (parts=2 is
the common L-shape case), each part sized/placed independently by the
solver subject to a mandatory shared-wall constraint between
consecutive parts.

Units are integer feet on a 1ft grid. Change GRID to use 6in units.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from ortools.sat.python import cp_model


# ----------------------------------------------------------------------
# Drawing palette (vellum sheet, not the CAD-dump cream/black default)
# ----------------------------------------------------------------------

SHEET = "#EDE6D6"     # vellum background
INK = "#1C1915"       # graphite, not pure black
GRID = "#D9D0BE"      # faint 1ft graph under the plan
LIVING = "#E4C9A0"    # oak -- entry/living/dining/kitchen/great room
SLEEP = "#C9D4C2"     # linen -- bedrooms
WET = "#B7C9C8"       # tile -- baths/utility
ACCENT = "#9A3B2F"    # iron-oxide -- windows, used sparingly

FONT_SANS = "'IBM Plex Sans', Arial, sans-serif"
FONT_MONO = "'IBM Plex Mono', 'Courier New', monospace"


def room_kind(name: str) -> str:
    """Classifies a room name for fill color. Hall is left neutral (reads
    as circulation, not a room); everything else buckets into the
    living/sleep/wet groups an architectural plan conventionally uses."""
    if name.endswith("Closet"):
        return "closet"
    if name == "Hall":
        return "hall"
    if name == "Primary" or name.startswith("Bed"):
        return "sleep"
    if name == "PrimBath" or name.startswith("Bath") or name == "Utility":
        return "wet"
    return "living"


def display_name(name: str) -> str:
    """"PrimBath" -> "PRIM BATH", "Bed2Closet" -> "BED 2 CLOSET" -- splits
    camelCase and letter/digit boundaries so generated room names read as
    architectural labels instead of Python identifiers."""
    s = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
    s = re.sub(r"(?<=[a-zA-Z])(?=\d)", " ", s)
    return s.upper()


# ----------------------------------------------------------------------
# Program definition
# ----------------------------------------------------------------------

@dataclass
class Room:
    name: str
    target_area: int              # sq ft
    min_area: Optional[int] = None
    max_area: Optional[int] = None
    min_dim: int = 8              # shortest allowed wall (per part)
    max_aspect: float = 2.0       # long side / short side (per part)
    needs_exterior: bool = True   # needs a window
    edges: List[str] = field(default_factory=list)  # forced: N/S/E/W, applies to part 0
    parts: int = 1                # rectangles making up the room; 2+ = L/T/U-shaped
    must_cover: Optional[Tuple[str, int, int]] = None  # (axis, lo, hi): part 0 must
        # span at least [lo, hi] on "x" or "y" -- pins *where* along an edge a room
        # sits, which Room.edges alone doesn't (it only pins *which* edge). Used by
        # zoning.solve_zoned() to force two independently-solved zones' connector
        # rooms to overlap at a shared coordinate, not just share a wall somewhere.

    def bounds(self):
        lo = self.min_area if self.min_area is not None else int(self.target_area * 0.85)
        hi = self.max_area if self.max_area is not None else int(self.target_area * 1.15)
        return lo, hi


@dataclass
class Adj:
    a: str
    b: str
    min_shared: int = 3           # ft of shared wall, enough for a door


@dataclass
class Footprint:
    width: int
    height: int
    voids: List[Tuple[int, int, int, int]] = field(default_factory=list)  # x1,y1,x2,y2

    def area(self):
        a = self.width * self.height
        for x1, y1, x2, y2 in self.voids:
            a -= (x2 - x1) * (y2 - y1)
        return a


def add_closets(rooms, adjacencies, bedrooms, area=20, min_dim=3, max_aspect=3.0, min_shared=3):
    """Attach a mandatory closet to each named bedroom.

    A closet is just a small interior room plus a forced Adj to its
    bedroom -- Adj is already a hard constraint, so the solver cannot
    return a plan where that wall doesn't exist. Steal the closet's
    area from the bedroom's own target_area beforehand if the two are
    meant to share the same reserved footprint; this helper doesn't
    touch the bedroom's target.

    Returns new (rooms, adjacencies) lists. Closet names are
    f"{bedroom}Closet"; include them in circulation_ok's `private` set
    so they're never treated as a hallway pass-through.
    """
    rooms = list(rooms)
    adjacencies = list(adjacencies)
    for b in bedrooms:
        closet = f"{b}Closet"
        rooms.append(Room(closet, area, min_dim=min_dim, max_aspect=max_aspect,
                           needs_exterior=False))
        adjacencies.append(Adj(b, closet, min_shared=min_shared))
    return rooms, adjacencies


# ----------------------------------------------------------------------
# Solver
# ----------------------------------------------------------------------

def validate_program(footprint: Footprint, rooms: List[Room], adjacencies: List[Adj]):
    """Raise ValueError with a specific, actionable message for a
    self-inconsistent program, instead of letting OR-Tools fail deep inside
    model-building on an invalid variable domain, or burning the full solve
    time_limit on a program that can never fit the footprint."""
    names = {r.name for r in rooms}
    if len(names) != len(rooms):
        raise ValueError("room names must be unique")

    for r in rooms:
        if r.min_dim <= 0:
            raise ValueError(f"{r.name}: min_dim must be positive, got {r.min_dim}")
        if r.max_aspect < 1:
            raise ValueError(f"{r.name}: max_aspect must be >= 1, got {r.max_aspect}")
        if r.parts < 1:
            raise ValueError(f"{r.name}: parts must be >= 1, got {r.parts}")
        if r.must_cover is not None:
            axis, mlo, mhi = r.must_cover
            if axis not in ("x", "y"):
                raise ValueError(f"{r.name}: must_cover axis must be 'x' or 'y', got {axis!r}")
            if mlo >= mhi:
                raise ValueError(f"{r.name}: must_cover range is empty ({mlo}, {mhi})")
        lo, hi = r.bounds()
        if lo > hi:
            raise ValueError(f"{r.name}: min_area ({lo}) exceeds max_area ({hi})")
        part_lo = r.min_dim * r.min_dim
        if part_lo > hi:
            raise ValueError(
                f"{r.name}: min_dim={r.min_dim} forces at least {part_lo} sf per part, "
                f"but this room's area only ranges up to {hi} sf (target_area={r.target_area}) "
                "-- raise target_area/max_area or lower min_dim"
            )

    for ad in adjacencies:
        if ad.a not in names:
            raise ValueError(f"adjacency references unknown room {ad.a!r}")
        if ad.b not in names:
            raise ValueError(f"adjacency references unknown room {ad.b!r}")
        if ad.min_shared <= 0:
            raise ValueError(f"adjacency {ad.a}-{ad.b}: min_shared must be positive")

    total_lo = sum(r.bounds()[0] for r in rooms)
    total_hi = sum(r.bounds()[1] for r in rooms)
    fa = footprint.area()
    if total_lo > fa:
        raise ValueError(
            f"program needs at least {total_lo} sf across all rooms, "
            f"but the footprint is only {fa} sf"
        )
    if total_hi < fa:
        raise ValueError(
            f"program's rooms max out at {total_hi} sf combined, "
            f"but the footprint is {fa} sf -- add rooms or raise max_area/target_area"
        )


def _touch_cases(m, W, H, x1, x2, y1, y2, a, b, min_len, tag):
    """Boolean literals, one per side (a-right-of-b, etc), true when a and
    b share a wall segment >= min_len long. Caller enforces with add_bool_or
    (optional adjacency) or a bare disjunction (mandatory glue)."""
    ovx = m.new_int_var(-max(W, H), max(W, H), f"ovx_{tag}")
    ovy = m.new_int_var(-max(W, H), max(W, H), f"ovy_{tag}")
    lox = m.new_int_var(0, W, f"lox_{tag}")
    hix = m.new_int_var(0, W, f"hix_{tag}")
    loy = m.new_int_var(0, H, f"loy_{tag}")
    hiy = m.new_int_var(0, H, f"hiy_{tag}")
    m.add_max_equality(lox, [x1[a], x1[b]])
    m.add_min_equality(hix, [x2[a], x2[b]])
    m.add_max_equality(loy, [y1[a], y1[b]])
    m.add_min_equality(hiy, [y2[a], y2[b]])
    m.add(ovx == hix - lox)
    m.add(ovy == hiy - loy)

    cases = []
    for side, (u, v, ov) in {
        "aRb": (x2[a], x1[b], ovy),
        "bRa": (x2[b], x1[a], ovy),
        "aTb": (y2[a], y1[b], ovx),
        "bTa": (y2[b], y1[a], ovx),
    }.items():
        lit = m.new_bool_var(f"t_{tag}_{side}")
        m.add(u == v).only_enforce_if(lit)
        m.add(ov >= min_len).only_enforce_if(lit)
        cases.append(lit)
    return cases


def solve(footprint: Footprint,
          rooms: List[Room],
          adjacencies: List[Adj],
          time_limit: float = 30.0,
          seed: int = 0,
          workers: int = 8,
          hint: Optional[Dict[str, Tuple[int, int, int, int]]] = None):
    """hint: optional {part_key: (x1, y1, x2, y2)} warm start, in the same
    part-key namespace as Room.parts (the room name itself for a single-part
    room, f"{name}#{i}" for part i of a multi-part room). Doesn't need to be
    a feasible layout -- it's just a starting point for CP-SAT's search, so a
    fast approximate packer (see generator.shelf_pack_hint) is enough. Parts
    missing from the dict are left for CP-SAT to place unassisted."""

    validate_program(footprint, rooms, adjacencies)

    W, H = footprint.width, footprint.height
    m = cp_model.CpModel()

    x1, x2, y1, y2, w, h, area = {}, {}, {}, {}, {}, {}, {}
    xiv, yiv = {}, {}
    part_keys: Dict[str, List[str]] = {}
    tarea = {}

    for r in rooms:
        n = r.name
        lo, hi = r.bounds()
        part_lo = r.min_dim * r.min_dim
        pks = [n if r.parts == 1 else f"{n}#{i}" for i in range(r.parts)]
        part_keys[n] = pks

        for i, pk in enumerate(pks):
            x1[pk] = m.new_int_var(0, W, f"{pk}_x1")
            x2[pk] = m.new_int_var(0, W, f"{pk}_x2")
            y1[pk] = m.new_int_var(0, H, f"{pk}_y1")
            y2[pk] = m.new_int_var(0, H, f"{pk}_y2")
            w[pk] = m.new_int_var(r.min_dim, W, f"{pk}_w")
            h[pk] = m.new_int_var(r.min_dim, H, f"{pk}_h")
            area[pk] = m.new_int_var(part_lo, hi, f"{pk}_a")

            xiv[pk] = m.new_interval_var(x1[pk], w[pk], x2[pk], f"{pk}_xi")
            yiv[pk] = m.new_interval_var(y1[pk], h[pk], y2[pk], f"{pk}_yi")

            m.add_multiplication_equality(area[pk], [w[pk], h[pk]])

            if hint and pk in hint:
                hx1, hy1, hx2, hy2 = hint[pk]
                m.add_hint(x1[pk], hx1)
                m.add_hint(y1[pk], hy1)
                m.add_hint(x2[pk], hx2)
                m.add_hint(y2[pk], hy2)
                m.add_hint(w[pk], hx2 - hx1)
                m.add_hint(h[pk], hy2 - hy1)
                m.add_hint(area[pk], (hx2 - hx1) * (hy2 - hy1))

            # aspect ratio, expressed with integer math
            num = int(round(r.max_aspect * 100))
            m.add(100 * w[pk] <= num * h[pk])
            m.add(100 * h[pk] <= num * w[pk])

            if i == 0:
                for e in r.edges:
                    if e == "W": m.add(x1[pk] == 0)
                    if e == "E": m.add(x2[pk] == W)
                    if e == "S": m.add(y1[pk] == 0)
                    if e == "N": m.add(y2[pk] == H)
                if r.must_cover:
                    axis, mlo, mhi = r.must_cover
                    lo1, hi1 = (x1, x2) if axis == "x" else (y1, y2)
                    m.add(lo1[pk] <= mlo)
                    m.add(hi1[pk] >= mhi)

        tarea[n] = m.new_int_var(lo, hi, f"{n}_totarea")
        m.add(tarea[n] == sum(area[pk] for pk in pks))

        # glue: consecutive parts of a multi-part room must physically join
        for i in range(len(pks) - 1):
            pa, pb = pks[i], pks[i + 1]
            cases = _touch_cases(m, W, H, x1, x2, y1, y2, pa, pb, r.min_dim, f"glue_{pa}_{pb}")
            m.add_bool_or(cases)

    all_pks = [pk for pks in part_keys.values() for pk in pks]

    # fixed voids (porch cutouts, garage bump-outs)
    void_ivs_x, void_ivs_y = [], []
    for i, (vx1, vy1, vx2, vy2) in enumerate(footprint.voids):
        void_ivs_x.append(m.new_interval_var(vx1, vx2 - vx1, vx2, f"void{i}_x"))
        void_ivs_y.append(m.new_interval_var(vy1, vy2 - vy1, vy2, f"void{i}_y"))

    m.add_no_overlap_2d([xiv[pk] for pk in all_pks] + void_ivs_x,
                        [yiv[pk] for pk in all_pks] + void_ivs_y)

    # exact partition: disjoint + inside + total area equal => full cover
    m.add(sum(tarea.values()) == footprint.area())

    # ------------------------------------------------------------------
    # adjacency: rooms must share a wall segment long enough for a door.
    # a multi-part room is adjacent if ANY of its parts qualifies.
    # ------------------------------------------------------------------
    for ad in adjacencies:
        a, b, L = ad.a, ad.b, ad.min_shared
        cases = []
        for pa in part_keys[a]:
            for pb in part_keys[b]:
                cases += _touch_cases(m, W, H, x1, x2, y1, y2, pa, pb, L, f"adj_{pa}_{pb}")
        m.add_bool_or(cases)

    # ------------------------------------------------------------------
    # daylight: room must touch the outer boundary (any one part suffices)
    # ------------------------------------------------------------------
    for r in rooms:
        if not r.needs_exterior or r.edges:
            continue
        lits = []
        for pk in part_keys[r.name]:
            for tag, (var, val) in {
                "W": (x1[pk], 0), "E": (x2[pk], W),
                "S": (y1[pk], 0), "N": (y2[pk], H),
            }.items():
                lit = m.new_bool_var(f"ext_{pk}_{tag}")
                m.add(var == val).only_enforce_if(lit)
                lits.append(lit)
        m.add_bool_or(lits)

    # ------------------------------------------------------------------
    # objective: hit the area program as closely as possible
    # ------------------------------------------------------------------
    devs = []
    for r in rooms:
        d = m.new_int_var(0, W * H, f"dev_{r.name}")
        m.add(d >= tarea[r.name] - r.target_area)
        m.add(d >= r.target_area - tarea[r.name])
        devs.append(d)
    m.minimize(sum(devs))

    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit
    s.parameters.num_workers = workers
    s.parameters.random_seed = seed
    status = s.solve(m)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, s.status_name(status)

    plan = {}
    for r in rooms:
        n = r.name
        parts_out = [
            dict(x1=s.value(x1[pk]), y1=s.value(y1[pk]),
                 x2=s.value(x2[pk]), y2=s.value(y2[pk]),
                 area=s.value(area[pk]))
            for pk in part_keys[n]
        ]
        plan[n] = dict(parts=parts_out, area=s.value(tarea[n]), target=r.target_area)
    return plan, s.status_name(status)


# ----------------------------------------------------------------------
# Post-processing
# ----------------------------------------------------------------------

def shared_walls(plan, min_len=3):
    """Recover the real adjacency graph from the solved geometry.

    Each room may be several rectangular parts (L-shaped rooms); two
    rooms are adjacent if any pair of their parts share a long-enough
    wall. Returns the longest qualifying segment per room pair.
    """
    out = []
    names = list(plan)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            best = None
            for A in plan[a]["parts"]:
                for B in plan[b]["parts"]:
                    ovy = min(A["y2"], B["y2"]) - max(A["y1"], B["y1"])
                    ovx = min(A["x2"], B["x2"]) - max(A["x1"], B["x1"])
                    if (A["x2"] == B["x1"] or B["x2"] == A["x1"]) and ovy >= min_len:
                        if best is None or ovy > best[1]:
                            best = ("V", ovy)
                    elif (A["y2"] == B["y1"] or B["y2"] == A["y1"]) and ovx >= min_len:
                        if best is None or ovx > best[1]:
                            best = ("H", ovx)
            if best:
                out.append((a, b, best[0], best[1]))
    return out


def circulation_ok(plan, entry, private=()):
    """BFS from the entry. Private rooms may not be pass-through."""
    edges = {}
    for a, b, _, _ in shared_walls(plan):
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set()).add(a)
    seen, stack = {entry}, [entry]
    while stack:
        cur = stack.pop()
        if cur in private and cur != entry:
            continue
        for nb in edges.get(cur, ()):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return seen == set(plan), sorted(set(plan) - seen)


def place_openings(plan, fp: Footprint, adjacencies: List[Adj], rooms: List[Room],
                    door_width: float = 3.0, window_width: float = 4.0):
    """Best-effort door/window placement from the solved geometry alone --
    no solver change. One door per adjacency, centered on the longest wall
    segment shared by any pair of parts from the two rooms (same overlap
    test as shared_walls(), but this needs the segment's actual position,
    not just its length). One window per daylight-required room, centered
    on its longest exterior-touching segment.

    Returns a list of dicts: kind ("door"/"window"), orient ("V"/"H"), and
    an (x1, y1, x2, y2) box in grid units -- a thin rect along the wall,
    ready for to_svg to draw. An adjacency the solver satisfied on a
    segment shorter than door_width (allowed, since Adj.min_shared can be
    less) is skipped rather than drawing an oversized door.
    """
    def best_shared_segment(a_parts, b_parts, min_len):
        best = None
        for A in a_parts:
            for B in b_parts:
                if A["x2"] == B["x1"] or B["x2"] == A["x1"]:
                    x = A["x2"] if A["x2"] == B["x1"] else A["x1"]
                    lo, hi = max(A["y1"], B["y1"]), min(A["y2"], B["y2"])
                    if hi - lo >= min_len and (best is None or hi - lo > best[2]):
                        best = ("V", x, hi - lo, lo, hi)
                elif A["y2"] == B["y1"] or B["y2"] == A["y1"]:
                    y = A["y2"] if A["y2"] == B["y1"] else A["y1"]
                    lo, hi = max(A["x1"], B["x1"]), min(A["x2"], B["x2"])
                    if hi - lo >= min_len and (best is None or hi - lo > best[2]):
                        best = ("H", y, hi - lo, lo, hi)
        return best

    def centered_box(orient, fixed, lo, hi, width):
        mid = (lo + hi) / 2
        c1, c2 = mid - width / 2, mid + width / 2
        if orient == "V":
            return dict(orient="V", x1=fixed, x2=fixed, y1=c1, y2=c2)
        return dict(orient="H", y1=fixed, y2=fixed, x1=c1, x2=c2)

    openings = []
    for ad in adjacencies:
        seg = best_shared_segment(plan[ad.a]["parts"], plan[ad.b]["parts"], door_width)
        if not seg:
            continue
        orient, fixed, _, lo, hi = seg
        openings.append(dict(kind="door", rooms=(ad.a, ad.b),
                              **centered_box(orient, fixed, lo, hi, door_width)))

    W, H = fp.width, fp.height
    for r in rooms:
        if not r.needs_exterior:
            continue
        best = None
        for part in plan[r.name]["parts"]:
            for is_vert, val, bound in (
                (True, part["x1"], 0), (True, part["x2"], W),
                (False, part["y1"], 0), (False, part["y2"], H),
            ):
                if val != bound:
                    continue
                lo, hi = (part["y1"], part["y2"]) if is_vert else (part["x1"], part["x2"])
                seg_len = hi - lo
                if seg_len >= window_width and (best is None or seg_len > best[0]):
                    best = (seg_len, is_vert, bound, lo, hi)
        if not best:
            continue
        _, is_vert, bound, lo, hi = best
        orient = "V" if is_vert else "H"
        openings.append(dict(kind="window", rooms=(r.name,),
                              **centered_box(orient, bound, lo, hi, window_width)))
    return openings


def _room_polygon(parts):
    """Merge a multi-part room's rectangles (glued edge-to-edge by solve())
    into one boundary polygon, so it renders as a single L/T/U outline
    instead of separate rectangles with a visible seam. Works via edge
    cancellation: an edge shared by two parts is interior and drops out;
    whatever's left is the union's boundary. Since solve()'s glue
    constraint guarantees the parts are edge-connected with no holes, that
    boundary is always one simple loop, so tracing it is a plain walk.
    Callers should only call this for len(parts) > 1 -- a single part is
    just its own rectangle."""
    v_edges, h_edges = [], []  # (coord, lo, hi, sign)
    for p in parts:
        v_edges.append((p["x2"], p["y1"], p["y2"], +1))   # right edge: material to -x side
        v_edges.append((p["x1"], p["y1"], p["y2"], -1))   # left edge: material to +x side
        h_edges.append((p["y2"], p["x1"], p["x2"], +1))   # top edge: material to -y side
        h_edges.append((p["y1"], p["x1"], p["x2"], -1))   # bottom edge: material to +y side

    def cancel_and_merge(edges):
        by_coord = {}
        for coord, lo, hi, sign in edges:
            by_coord.setdefault(coord, []).append((lo, hi, sign))
        out = []
        for coord, items in by_coord.items():
            points = sorted({v for lo, hi, _ in items for v in (lo, hi)})
            kept = []
            for i in range(len(points) - 1):
                a, b = points[i], points[i + 1]
                mid = (a + b) / 2
                net = sum(sign for lo, hi, sign in items if lo <= mid <= hi)
                if net != 0:
                    kept.append([a, b])
            merged = []
            for lo, hi in kept:
                if merged and merged[-1][1] == lo:
                    merged[-1][1] = hi
                else:
                    merged.append([lo, hi])
            out.extend((coord, lo, hi) for lo, hi in merged)
        return out

    v_out = cancel_and_merge(v_edges)  # (x, ylo, yhi)
    h_out = cancel_and_merge(h_edges)  # (y, xlo, xhi)

    adj = {}
    for x, lo, hi in v_out:
        p1, p2 = (x, lo), (x, hi)
        adj.setdefault(p1, []).append(p2)
        adj.setdefault(p2, []).append(p1)
    for y, lo, hi in h_out:
        p1, p2 = (lo, y), (hi, y)
        adj.setdefault(p1, []).append(p2)
        adj.setdefault(p2, []).append(p1)

    start = next(iter(adj))
    loop, prev, cur = [start], None, start
    while True:
        nxts = [n for n in adj[cur] if n != prev]
        nxt = nxts[0] if nxts else adj[cur][0]
        if nxt == start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
    return loop


def _door_svg(o, scale, H):
    """Door leaf + quarter-circle swing arc (the standard plan symbol),
    radius/width equal to the opening -- drawn in place of a flat rect so
    a door actually reads as a door."""
    def sx(gx): return gx * scale
    def sy(gy): return (H - gy) * scale
    if o["orient"] == "V":
        hinge = (sx(o["x1"]), sy(o["y1"]))
        jamb = (sx(o["x1"]), sy(o["y2"]))
        d = hinge[1] - jamb[1]
        tip = (hinge[0] + d, hinge[1])
    else:
        hinge = (sx(o["x1"]), sy(o["y1"]))
        jamb = (sx(o["x2"]), sy(o["y1"]))
        d = jamb[0] - hinge[0]
        tip = (hinge[0], hinge[1] + d)
    path = (f'M {hinge[0]:.1f},{hinge[1]:.1f} L {tip[0]:.1f},{tip[1]:.1f} '
            f'A {d:.1f},{d:.1f} 0 0,1 {jamb[0]:.1f},{jamb[1]:.1f}')
    return f'<path d="{path}" fill="none" stroke="{INK}" stroke-width="1.25"/>'


def _window_svg(o, scale, H):
    """Double line across the wall opening -- the standard window symbol."""
    def sx(gx): return gx * scale
    def sy(gy): return (H - gy) * scale
    off = 2.5
    if o["orient"] == "V":
        x, y0, y1 = sx(o["x1"]), sy(o["y1"]), sy(o["y2"])
        coords = [(x - off, y1, x - off, y0), (x + off, y1, x + off, y0)]
    else:
        y, x0, x1 = sy(o["y1"]), sx(o["x1"]), sx(o["x2"])
        coords = [(x0, y - off, x1, y - off), (x0, y + off, x1, y + off)]
    return [f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d:.1f}" '
            f'stroke="{ACCENT}" stroke-width="1.5"/>' for a, b, c, d in coords]


FILL_BY_KIND = {"living": LIVING, "sleep": SLEEP, "wet": WET, "hall": SHEET}


def _room_fill(name):
    kind = room_kind(name)
    return "url(#closetHatch)" if kind == "closet" else FILL_BY_KIND[kind]


def _north_arrow(x, y):
    """North-pointing triangle + label, (x,y) is the base center."""
    return (f'<path d="M {x},{y-22} L {x+6},{y} L {x},{y-4} L {x-6},{y} Z" fill="{INK}"/>'
            f'<text x="{x}" y="{y+11}" font-family="{FONT_SANS}" font-size="9" '
            f'font-weight="600" text-anchor="middle" fill="{INK}">N</text>')


def _scale_bar(x0, y, scale):
    length = 10 * scale
    return (f'<line x1="{x0}" y1="{y}" x2="{x0+length}" y2="{y}" stroke="{INK}" stroke-width="1.5"/>'
            f'<line x1="{x0}" y1="{y-4}" x2="{x0}" y2="{y+4}" stroke="{INK}" stroke-width="1.5"/>'
            f'<line x1="{x0+length}" y1="{y-4}" x2="{x0+length}" y2="{y+4}" stroke="{INK}" stroke-width="1.5"/>'
            f'<text x="{x0+length/2}" y="{y+16}" font-family="{FONT_MONO}" font-size="9" '
            f'text-anchor="middle" fill="{INK}">10 FT</text>')


def _title_block_svg(x0, y0, width, title, lines):
    p = [f'<text x="{x0}" y="{y0+10}" font-family="{FONT_SANS}" font-size="12" '
         f'font-weight="600" letter-spacing="0.5" fill="{INK}">{title}</text>',
         f'<line x1="{x0}" y1="{y0+16}" x2="{x0+width}" y2="{y0+16}" '
         f'stroke="{ACCENT}" stroke-width="2"/>']
    for i, line in enumerate(lines):
        p.append(f'<text x="{x0}" y="{y0+30+i*13}" font-family="{FONT_MONO}" '
                  f'font-size="9" fill="{INK}" opacity="0.75">{line}</text>')
    return p


def _exterior_dims(W, H, scale):
    """Overall width (top) and depth (left) dimension strings with
    extension ticks -- no per-room span callouts, just the two overalls."""
    p = []
    y = -9
    p.append(f'<line x1="0" y1="{y}" x2="{W*scale}" y2="{y}" stroke="{INK}" stroke-width="1"/>')
    for x in (0, W * scale):
        p.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{y}" stroke="{INK}" stroke-width="0.75" opacity="0.6"/>')
    p.append(f'<text x="{W*scale/2}" y="{y-3}" font-family="{FONT_MONO}" font-size="9.5" '
             f'text-anchor="middle" fill="{INK}">{W}\'-0"</text>')
    x = -24
    p.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H*scale}" stroke="{INK}" stroke-width="1"/>')
    for gy in (0, H * scale):
        p.append(f'<line x1="{x}" y1="{gy}" x2="0" y2="{gy}" stroke="{INK}" stroke-width="0.75" opacity="0.6"/>')
    p.append(f'<text x="{x-4}" y="{H*scale/2}" font-family="{FONT_MONO}" font-size="9.5" '
             f'text-anchor="middle" fill="{INK}" transform="rotate(-90 {x-4} {H*scale/2})">'
             f'{H}\'-0"</text>')
    return p


def to_svg(plan, fp: Footprint, scale=14, path="plan.svg", openings=None,
           interior_thickness=0.3, exterior_thickness=0.5, title_block=None):
    """Renders the plan to SVG. If path is None, returns the markup string
    directly instead of writing to disk -- use this from a server handling
    concurrent requests, since writing every request to the same path is a
    race. openings is the optional list of dicts from place_openings().

    Room rectangles are drawn inset from their solved (centerline)
    coordinates by half a wall thickness, using exterior_thickness on a
    footprint-boundary edge and interior_thickness/2 on an edge shared with
    another room (each side of a shared wall contributes half). Multi-part
    (L/T/U) rooms are drawn as one merged outline via _room_polygon() and
    skip the thickness inset -- doing both at once would mean insetting
    per polygon edge based on what's across it, which isn't worth the
    complexity while the seam-merge itself is still new.

    Rooms are filled by use (living/sleep/wet/closet-hatch, Hall left
    neutral) on a 1ft graph-paper grid, doors draw as a swing arc and
    windows as a double line rather than a flat colored rect. The sheet
    carries overall width/depth dimension strings, a north arrow, and a
    10ft scale bar; title_block, if given, is
    {"title": str, "lines": [str, ...]} drawn as a small block in the
    bottom-right margin (e.g. area/beds-baths/style/date). Each room's
    shapes and labels sit inside <g data-room="{name}"> (the raw plan
    key, not display_name()'s formatted label) so a page embedding this
    markup can hook up hover-sync with a schedule table keyed the same
    way."""
    W, H = fp.width, fp.height
    margin_left, margin_right, margin_top = 34, 20, 48
    n_tb_lines = len(title_block.get("lines", [])) if title_block else 0
    margin_bottom = max(40, 14 + 30 + max(0, n_tb_lines - 1) * 13 + 10) if title_block else 40
    canvas_w = W * scale + margin_left + margin_right
    canvas_h = H * scale + margin_top + margin_bottom
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" '
         f'viewBox="-{margin_left} -{margin_top} {canvas_w} {canvas_h}">',
         f'<defs><pattern id="closetHatch" width="6" height="6" '
         f'patternTransform="rotate(45)" patternUnits="userSpaceOnUse">'
         f'<rect width="6" height="6" fill="{SLEEP}"/>'
         f'<line x1="0" y1="0" x2="0" y2="6" stroke="{INK}" stroke-width="1" opacity="0.25"/>'
         f'</pattern></defs>',
         f'<rect x="-{margin_left}" y="-{margin_top}" width="100%" height="100%" fill="{SHEET}"/>']
    for gx in range(W + 1):
        heavy = gx % 5 == 0
        p.append(f'<line x1="{gx*scale}" y1="0" x2="{gx*scale}" y2="{H*scale}" '
                  f'stroke="{GRID}" stroke-width="{1 if heavy else 0.5}" '
                  f'opacity="{0.55 if heavy else 0.3}"/>')
    for gy in range(H + 1):
        heavy = gy % 5 == 0
        p.append(f'<line x1="0" y1="{gy*scale}" x2="{W*scale}" y2="{gy*scale}" '
                  f'stroke="{GRID}" stroke-width="{1 if heavy else 0.5}" '
                  f'opacity="{0.55 if heavy else 0.3}"/>')
    for x1, y1, x2, y2 in fp.voids:
        p.append(f'<rect x="{x1*scale}" y="{(H-y2)*scale}" width="{(x2-x1)*scale}" '
                 f'height="{(y2-y1)*scale}" fill="{GRID}"/>')
    for n, room in plan.items():
        p.append(f'<g data-room="{n}">')
        parts = room["parts"]
        fill = _room_fill(n)
        biggest = max(parts, key=lambda pt: pt["area"])
        if len(parts) > 1:
            poly = _room_polygon(parts)
            pts = " ".join(f"{gx*scale},{(H-gy)*scale}" for gx, gy in poly)
            p.append(f'<polygon points="{pts}" fill="{fill}" stroke="{INK}" stroke-width="2"/>')
        for part in parts:
            if len(parts) > 1:
                ix1, ix2, iy1, iy2 = part["x1"], part["x2"], part["y1"], part["y2"]
            else:
                ix1 = part["x1"] + (exterior_thickness if part["x1"] == 0 else interior_thickness / 2)
                ix2 = part["x2"] - (exterior_thickness if part["x2"] == W else interior_thickness / 2)
                iy1 = part["y1"] + (exterior_thickness if part["y1"] == 0 else interior_thickness / 2)
                iy2 = part["y2"] - (exterior_thickness if part["y2"] == H else interior_thickness / 2)
                px, py = ix1 * scale, (H - iy2) * scale
                pw, ph = (ix2 - ix1) * scale, (iy2 - iy1) * scale
                p.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" '
                         f'fill="{fill}" stroke="{INK}" stroke-width="2"/>')
            cx, cy = (ix1 + ix2) / 2 * scale, (H - (iy1 + iy2) / 2) * scale
            pxw, pxh = (ix2 - ix1) * scale, (iy2 - iy1) * scale
            w, h = part["x2"] - part["x1"], part["y2"] - part["y1"]
            dims = f'{w}\'-0" × {h}\'-0"'
            if part is biggest:
                name_label = display_name(n)
                area_label = f'{room["area"]} SF'
                if pxw >= 70 and pxh >= 40:
                    # room's big enough for the full three-line label
                    max_chars = max(4, int(pxw / 6.8))
                    if len(name_label) > max_chars:
                        name_label = name_label[:max_chars - 1].rstrip() + "…"
                    p.append(f'<text x="{cx}" y="{cy-6}" font-family="{FONT_SANS}" '
                             f'font-size="11" font-weight="600" letter-spacing="0.5" '
                             f'text-anchor="middle" fill="{INK}">{name_label}</text>')
                    p.append(f'<text x="{cx}" y="{cy+8}" font-family="{FONT_MONO}" '
                             f'font-size="9" text-anchor="middle" fill="{INK}" opacity="0.65">'
                             f'{dims}</text>')
                    p.append(f'<text x="{cx}" y="{cy+19}" font-family="{FONT_MONO}" '
                             f'font-size="9" text-anchor="middle" fill="{INK}" opacity="0.65">'
                             f'{area_label}</text>')
                elif pxw >= 38 and pxh >= 24:
                    # too tight for dims too -- name (truncated if needed) + area only
                    max_chars = max(3, int(pxw / 5.2))
                    if len(name_label) > max_chars:
                        name_label = name_label[:max_chars - 1].rstrip() + "…"
                    p.append(f'<text x="{cx}" y="{cy-2}" font-family="{FONT_SANS}" '
                             f'font-size="8" font-weight="600" text-anchor="middle" '
                             f'fill="{INK}">{name_label}</text>')
                    p.append(f'<text x="{cx}" y="{cy+8}" font-family="{FONT_MONO}" '
                             f'font-size="7" text-anchor="middle" fill="{INK}" opacity="0.65">'
                             f'{area_label}</text>')
                else:
                    # too small for a name to ever fit without colliding with
                    # the neighbors -- area only, full name is in the schedule table
                    p.append(f'<text x="{cx}" y="{cy+3}" font-family="{FONT_MONO}" '
                             f'font-size="6.5" text-anchor="middle" fill="{INK}" opacity="0.7">'
                             f'{area_label}</text>')
            elif pxw >= 34 and pxh >= 20:
                p.append(f'<text x="{cx}" y="{cy+4}" font-family="{FONT_MONO}" '
                         f'font-size="8" text-anchor="middle" fill="{INK}" opacity="0.55">'
                         f'{dims}</text>')
        p.append('</g>')
    p.append(f'<rect x="0" y="0" width="{W*scale}" height="{H*scale}" '
             f'fill="none" stroke="{INK}" stroke-width="2"/>')
    for o in openings or []:
        if o["kind"] == "door":
            p.append(_door_svg(o, scale, H))
        else:
            p.extend(_window_svg(o, scale, H))
    p.extend(_exterior_dims(W, H, scale))
    p.append(_north_arrow(W * scale - 6, -24))
    p.append(_scale_bar(0, H * scale + 18, scale))
    if title_block:
        tb_w = min(190, max(100, W * scale - 160))
        tb_x = W * scale - tb_w
        p.extend(_title_block_svg(tb_x, H * scale + 14, tb_w,
                                   title_block.get("title", "FLOOR PLAN"),
                                   title_block.get("lines", [])))
    p.append("</svg>")
    markup = "\n".join(p)
    if path is None:
        return markup
    open(path, "w").write(markup)
    return path
