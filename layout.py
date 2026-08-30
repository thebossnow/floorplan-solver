"""
Constraint-solver floor plan generator.

Models a floor plan as an exact rectangular partition of a footprint.
No training data, no model weights. OR-Tools CP-SAT does the search.

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
    min_dim: int = 8              # shortest allowed wall
    max_aspect: float = 2.0       # long side / short side
    needs_exterior: bool = True   # needs a window
    edges: List[str] = field(default_factory=list)  # forced: N/S/E/W

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


# ----------------------------------------------------------------------
# Solver
# ----------------------------------------------------------------------

def solve(footprint: Footprint,
          rooms: List[Room],
          adjacencies: List[Adj],
          time_limit: float = 30.0,
          seed: int = 0,
          workers: int = 8):

    W, H = footprint.width, footprint.height
    m = cp_model.CpModel()
    R = {r.name: r for r in rooms}

    x1, x2, y1, y2, w, h, area = {}, {}, {}, {}, {}, {}, {}
    xiv, yiv = {}, {}

    for r in rooms:
        n = r.name
        lo, hi = r.bounds()
        x1[n] = m.new_int_var(0, W, f"{n}_x1")
        x2[n] = m.new_int_var(0, W, f"{n}_x2")
        y1[n] = m.new_int_var(0, H, f"{n}_y1")
        y2[n] = m.new_int_var(0, H, f"{n}_y2")
        w[n] = m.new_int_var(r.min_dim, W, f"{n}_w")
        h[n] = m.new_int_var(r.min_dim, H, f"{n}_h")
        area[n] = m.new_int_var(lo, hi, f"{n}_a")

        xiv[n] = m.new_interval_var(x1[n], w[n], x2[n], f"{n}_xi")
        yiv[n] = m.new_interval_var(y1[n], h[n], y2[n], f"{n}_yi")

        m.add_multiplication_equality(area[n], [w[n], h[n]])

        # aspect ratio, expressed with integer math
        num = int(round(r.max_aspect * 100))
        m.add(100 * w[n] <= num * h[n])
        m.add(100 * h[n] <= num * w[n])

        for e in r.edges:
            if e == "W": m.add(x1[n] == 0)
            if e == "E": m.add(x2[n] == W)
            if e == "S": m.add(y1[n] == 0)
            if e == "N": m.add(y2[n] == H)

    # fixed voids (porch cutouts, L-shape notches, garage bump-outs)
    void_ivs_x, void_ivs_y = [], []
    for i, (vx1, vy1, vx2, vy2) in enumerate(footprint.voids):
        void_ivs_x.append(m.new_interval_var(vx1, vx2 - vx1, vx2, f"void{i}_x"))
        void_ivs_y.append(m.new_interval_var(vy1, vy2 - vy1, vy2, f"void{i}_y"))

    m.add_no_overlap_2d([xiv[n] for n in x1] + void_ivs_x,
                        [yiv[n] for n in y1] + void_ivs_y)

    # exact partition: disjoint + inside + total area equal => full cover
    m.add(sum(area.values()) == footprint.area())

    # ------------------------------------------------------------------
    # adjacency: rooms must share a wall segment long enough for a door
    # ------------------------------------------------------------------
    for ad in adjacencies:
        a, b, L = ad.a, ad.b, ad.min_shared

        ovx = m.new_int_var(-max(W, H), max(W, H), f"ovx_{a}_{b}")
        ovy = m.new_int_var(-max(W, H), max(W, H), f"ovy_{a}_{b}")
        lox = m.new_int_var(0, W, f"lox_{a}_{b}")
        hix = m.new_int_var(0, W, f"hix_{a}_{b}")
        loy = m.new_int_var(0, H, f"loy_{a}_{b}")
        hiy = m.new_int_var(0, H, f"hiy_{a}_{b}")
        m.add_max_equality(lox, [x1[a], x1[b]])
        m.add_min_equality(hix, [x2[a], x2[b]])
        m.add_max_equality(loy, [y1[a], y1[b]])
        m.add_min_equality(hiy, [y2[a], y2[b]])
        m.add(ovx == hix - lox)
        m.add(ovy == hiy - loy)

        cases = []
        for tag, (u, v, ov) in {
            "aRb": (x2[a], x1[b], ovy),
            "bRa": (x2[b], x1[a], ovy),
            "aTb": (y2[a], y1[b], ovx),
            "bTa": (y2[b], y1[a], ovx),
        }.items():
            lit = m.new_bool_var(f"adj_{a}_{b}_{tag}")
            m.add(u == v).only_enforce_if(lit)
            m.add(ov >= L).only_enforce_if(lit)
            cases.append(lit)
        m.add_bool_or(cases)

    # ------------------------------------------------------------------
    # daylight: room must touch the outer boundary
    # ------------------------------------------------------------------
    for r in rooms:
        if not r.needs_exterior or r.edges:
            continue
        n = r.name
        lits = []
        for tag, (var, val) in {
            "W": (x1[n], 0), "E": (x2[n], W),
            "S": (y1[n], 0), "N": (y2[n], H),
        }.items():
            lit = m.new_bool_var(f"ext_{n}_{tag}")
            m.add(var == val).only_enforce_if(lit)
            lits.append(lit)
        m.add_bool_or(lits)

    # ------------------------------------------------------------------
    # objective: hit the area program as closely as possible
    # ------------------------------------------------------------------
    devs = []
    for r in rooms:
        d = m.new_int_var(0, W * H, f"dev_{r.name}")
        m.add(d >= area[r.name] - r.target_area)
        m.add(d >= r.target_area - area[r.name])
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
        plan[n] = dict(x1=s.value(x1[n]), y1=s.value(y1[n]),
                       x2=s.value(x2[n]), y2=s.value(y2[n]),
                       area=s.value(area[n]), target=r.target_area)
    return plan, s.status_name(status)


# ----------------------------------------------------------------------
# Post-processing
# ----------------------------------------------------------------------

def shared_walls(plan, min_len=3):
    """Recover the real adjacency graph from the solved geometry."""
    out = []
    names = list(plan)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            A, B = plan[a], plan[b]
            ovy = min(A["y2"], B["y2"]) - max(A["y1"], B["y1"])
            ovx = min(A["x2"], B["x2"]) - max(A["x1"], B["x1"])
            if (A["x2"] == B["x1"] or B["x2"] == A["x1"]) and ovy >= min_len:
                out.append((a, b, "V", ovy))
            elif (A["y2"] == B["y1"] or B["y2"] == A["y1"]) and ovx >= min_len:
                out.append((a, b, "H", ovx))
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
    for n, r in plan.items():
        px, py = r["x1"] * scale, (H - r["y2"]) * scale
        pw, ph = (r["x2"] - r["x1"]) * scale, (r["y2"] - r["y1"]) * scale
        p.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" '
                 f'fill="#fff" stroke="#222" stroke-width="3"/>')
        p.append(f'<text x="{px+pw/2}" y="{py+ph/2-3}" font-family="Helvetica" '
                 f'font-size="12" text-anchor="middle" fill="#222">{n}</text>')
        p.append(f'<text x="{px+pw/2}" y="{py+ph/2+11}" font-family="Helvetica" '
                 f'font-size="10" text-anchor="middle" fill="#888">'
                 f'{r["x2"]-r["x1"]}x{r["y2"]-r["y1"]} / {r["area"]}sf</text>')
    p.append(f'<rect x="0" y="0" width="{W*scale}" height="{H*scale}" '
             f'fill="none" stroke="#222" stroke-width="6"/>')
    p.append("</svg>")
    open(path, "w").write("\n".join(p))
    return path
