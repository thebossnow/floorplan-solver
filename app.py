"""
Minimal web front end for the floor plan solver.

Form -> generator.generate_program() -> layout.solve() -> SVG, embedded
back into the same page. Solve is capped at TIME_LIMIT seconds so a
request can't hang indefinitely; a capped solve may return FEASIBLE
instead of OPTIMAL, which is still a valid layout.

A program bigger than ZONE_ROOM_THRESHOLD rooms goes through
zoning.solve_zoned() instead of a single solve() call -- past that size
CP-SAT's single-model solve gets slow enough to time out, and in testing
(a 15-room 4-bed/3-bath open_concept program) can even come back
genuinely INFEASIBLE rather than just slow, where the same rooms split
into a public/private zone solve fine.
"""

import os
import time
from datetime import date

from flask import Flask, render_template, request

from generator import (HALLWAYS, MAX_AREA, MAX_BATHS, MAX_BEDS, MIN_AREA, PRODUCTION_WEIGHTS,
                        STYLES, default_proximity, generate_program, shelf_pack_hint,
                        zone_of_program)
from layout import FILL_BY_KIND, circulation_ok, display_name, place_openings, room_kind, solve, to_svg
from zoning import solve_zoned

# schedule-table grouping: closets ride along with the bedroom they belong
# to, everything else follows to_svg's own room_kind() buckets
GROUP_BY_KIND = {"living": "Public", "hall": "Public", "sleep": "Private",
                  "wet": "Service", "closet": "Private"}
GROUP_ORDER = ["Public", "Private", "Service"]

TIME_LIMIT = 25.0
ZONE_ROOM_THRESHOLD = 14   # more rooms than this: split into zones instead --
                           # matches README's documented ">15 rooms slows down"
                           # (a 15-room 4-bed/3-bath open_concept program was the
                           # one found genuinely INFEASIBLE unzoned in testing);
                           # the default 3-bed/2-bath program is 14 rooms and
                           # solves fine unzoned, so this shouldn't fire for typical inputs
ZONE_TIME_LIMIT = 15.0     # per zone, so a worst-case zoned solve is 2x this

# empty-state sample: a pre-solved SVG cached to disk (regenerate via the
# one-off script this file's git history/HANDOFF notes, or by hand: run
# generate_program(1500, 3, 2, "rectangular", "traditional") through solve()
# the same way index() below does, then to_svg(..., path="static/sample-plan.svg"))
# rather than re-solved on every empty-state page load
with open(os.path.join(os.path.dirname(__file__), "static", "sample-plan.svg")) as f:
    SAMPLE_SVG = f.read()
SAMPLE_CAPTION = "Example · 1,500 sf ranch"

# quick-start presets shown on the empty state -- (area, beds, baths, shape, style)
PRESETS = [
    dict(label="1,200 sf square", area=1200, beds=2, baths=2, shape="square", style="traditional"),
    dict(label="1,500 sf ranch", area=1500, beds=3, baths=2, shape="rectangular", style="traditional"),
    dict(label="2,000 sf open concept", area=2000, beds=3, baths=2, shape="rectangular", style="open_concept"),
]

app = Flask(__name__)


def _room_rows(plan):
    rows = []
    for name, r in plan.items():
        kind = room_kind(name)
        swatch = FILL_BY_KIND["sleep"] if kind == "closet" else FILL_BY_KIND[kind]
        rows.append(dict(
            name=display_name(name), raw_name=name, group=GROUP_BY_KIND[kind], swatch=swatch,
            area=r["area"], target=r["target"], delta=r["area"] - r["target"],
        ))
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    rows.sort(key=lambda row: (order[row["group"]], row["name"]))
    return rows


@app.route("/", methods=["GET", "POST"])
def index():
    form = dict(area=1500, beds=3, baths=2, shape="rectangular", style="traditional")
    result = None
    error = None

    # a GET with query params is a "copy link" visit reproducing a past
    # result (see templates/index.html's Copy link button, which builds
    # this same area/beds/baths/shape/style querystring via url_for) --
    # solve immediately rather than just prefilling the form, so the link
    # is a true "see this exact result" link, not just a starting point
    source = request.form if request.method == "POST" else request.args
    if request.method == "POST" or source:
        try:
            form["area"] = int(source.get("area", ""))
            form["beds"] = int(source.get("beds", ""))
            form["baths"] = int(source.get("baths", ""))
            form["shape"] = source.get("shape", "rectangular")
            form["style"] = source.get("style", "traditional")

            if not (MIN_AREA <= form["area"] <= MAX_AREA):
                raise ValueError(f"Area must be between {MIN_AREA} and {MAX_AREA} sq ft.")
            if form["shape"] not in ("square", "rectangular"):
                raise ValueError("Shape must be square or rectangular.")
            if form["style"] not in STYLES:
                raise ValueError(f"Style must be one of {', '.join(STYLES)}.")

            fp, rooms, adj, private = generate_program(
                form["area"], form["beds"], form["baths"], form["shape"], form["style"])
            proximity = default_proximity(rooms)

            zoned = len(rooms) > ZONE_ROOM_THRESHOLD
            cross = None
            zone_metrics = None
            # objective_value/best_objective_bound stay None on the zoned path --
            # each zone scores its own independent objective (see zone_metrics
            # below for the per-zone numbers), so there's no single meaningful
            # value to report here the way there is for a single solve() call.
            objective_value = best_objective_bound = None
            t0 = time.time()
            if zoned:
                zone_of = zone_of_program(rooms)
                plan, status, cross, zone_metrics = solve_zoned(
                    fp, rooms, adj, zone_of, time_limit=ZONE_TIME_LIMIT, workers=8,
                    hallways=HALLWAYS, private=private,
                    weights=PRODUCTION_WEIGHTS, proximity=proximity)
            else:
                hint = shelf_pack_hint(fp, rooms)
                plan, status, objective_value, best_objective_bound, solver_wall_time = solve(
                    fp, rooms, adj, time_limit=TIME_LIMIT, workers=8, hint=hint,
                    hallways=HALLWAYS, private=private,
                    proximity=proximity, **PRODUCTION_WEIGHTS)
            elapsed = time.time() - t0

            if not plan:
                budget = f"{2*ZONE_TIME_LIMIT:.0f}s" if zoned else f"{TIME_LIMIT:.0f}s"
                # solve()'s own wall_time (vs. this request's outer elapsed)
                # tells the difference between "burned the whole time_limit
                # with no incumbent" and "failed fast" (e.g. a quick proof of
                # INFEASIBLE) -- only available on the unzoned path today.
                timing = f"{solver_wall_time:.1f}s" if not zoned else f"{elapsed:.1f}s"
                error = (f"No layout found ({status}) after {timing}, within a {budget} budget. "
                         "Try a larger area, fewer bedrooms/bathrooms, or a different shape.")
            else:
                openings = place_openings(plan, fp, adj, rooms)
                title_block = dict(
                    title="FLOOR PLAN",
                    lines=[
                        f'{fp.area():,} SF',
                        f'{form["beds"]} BED / {form["baths"]} BATH',
                        form["style"].replace("_", " ").upper(),
                        date.today().strftime("%b %d %Y").upper(),
                    ],
                )
                svg_markup = to_svg(plan, fp, path=None, openings=openings, title_block=title_block)
                ok, unreachable = circulation_ok(plan, "Entry", private=private)
                result = dict(
                    svg=svg_markup,
                    headline=(f'{fp.width} × {fp.height} ft · {fp.area():,} sf · '
                               f'{form["beds"]} bed / {form["baths"]} bath'),
                    status=status,
                    zoned=zoned,
                    cross=cross,
                    elapsed=round(elapsed, 1),
                    objective_value=objective_value,
                    best_objective_bound=best_objective_bound,
                    zone_metrics=zone_metrics,
                    footprint=f'{fp.width} x {fp.height} ft ({fp.area()} sf)',
                    total=sum(r["area"] for r in plan.values()),
                    circulation_ok=ok,
                    unreachable=unreachable,
                    rooms=_room_rows(plan),
                )
        except ValueError as e:
            error = str(e)

    return render_template(
        "index.html", form=form, result=result, error=error,
        max_beds=MAX_BEDS, max_baths=MAX_BATHS, min_area=MIN_AREA, max_area=MAX_AREA,
        styles=list(STYLES),
        living=FILL_BY_KIND["living"], sleep=FILL_BY_KIND["sleep"], wet=FILL_BY_KIND["wet"],
        sample_svg=SAMPLE_SVG, sample_caption=SAMPLE_CAPTION, presets=PRESETS,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
