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

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from ortools.sat.python import cp_model


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
          workers: int = 8):

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


def to_svg(plan, fp: Footprint, scale=14, path="plan.svg"):
    W, H = fp.width, fp.height
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W*scale+40}" '
         f'height="{H*scale+40}" viewBox="-20 -20 {W*scale+40} {H*scale+40}">',
         '<rect x="-20" y="-20" width="100%" height="100%" fill="#fbfaf7"/>']
    for x1, y1, x2, y2 in fp.voids:
        p.append(f'<rect x="{x1*scale}" y="{(H-y2)*scale}" width="{(x2-x1)*scale}" '
                 f'height="{(y2-y1)*scale}" fill="#e8e4dc"/>')
    for n, room in plan.items():
        parts = room["parts"]
        biggest = max(parts, key=lambda pt: pt["area"])
        for part in parts:
            px, py = part["x1"] * scale, (H - part["y2"]) * scale
            pw, ph = (part["x2"] - part["x1"]) * scale, (part["y2"] - part["y1"]) * scale
            p.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" '
                     f'fill="#fff" stroke="#222" stroke-width="3"/>')
            dims = f'{part["x2"]-part["x1"]}x{part["y2"]-part["y1"]}'
            if part is biggest:
                p.append(f'<text x="{px+pw/2}" y="{py+ph/2-3}" font-family="Helvetica" '
                         f'font-size="12" text-anchor="middle" fill="#222">{n}</text>')
                p.append(f'<text x="{px+pw/2}" y="{py+ph/2+11}" font-family="Helvetica" '
                         f'font-size="10" text-anchor="middle" fill="#888">'
                         f'{dims} / {room["area"]}sf</text>')
            else:
                p.append(f'<text x="{px+pw/2}" y="{py+ph/2+4}" font-family="Helvetica" '
                         f'font-size="9" text-anchor="middle" fill="#aaa">{dims}</text>')
    p.append(f'<rect x="0" y="0" width="{W*scale}" height="{H*scale}" '
             f'fill="none" stroke="#222" stroke-width="6"/>')
    p.append("</svg>")
    open(path, "w").write("\n".join(p))
    return path
