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

import time
from datetime import date

from flask import Flask, render_template, request

from generator import (MAX_AREA, MAX_BATHS, MAX_BEDS, MIN_AREA, STYLES,
                        generate_program, shelf_pack_hint, zone_of_program)
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

    if request.method == "POST":
        try:
            form["area"] = int(request.form.get("area", ""))
            form["beds"] = int(request.form.get("beds", ""))
            form["baths"] = int(request.form.get("baths", ""))
            form["shape"] = request.form.get("shape", "rectangular")
            form["style"] = request.form.get("style", "traditional")

            if not (MIN_AREA <= form["area"] <= MAX_AREA):
                raise ValueError(f"Area must be between {MIN_AREA} and {MAX_AREA} sq ft.")
            if form["shape"] not in ("square", "rectangular"):
                raise ValueError("Shape must be square or rectangular.")
            if form["style"] not in STYLES:
                raise ValueError(f"Style must be one of {', '.join(STYLES)}.")

            fp, rooms, adj, private = generate_program(
                form["area"], form["beds"], form["baths"], form["shape"], form["style"])

            zoned = len(rooms) > ZONE_ROOM_THRESHOLD
            cross = None
            t0 = time.time()
            if zoned:
                zone_of = zone_of_program(rooms)
                plan, status, cross = solve_zoned(
                    fp, rooms, adj, zone_of, time_limit=ZONE_TIME_LIMIT, workers=8)
            else:
                hint = shelf_pack_hint(fp, rooms)
                plan, status = solve(fp, rooms, adj, time_limit=TIME_LIMIT, workers=8, hint=hint)
            elapsed = time.time() - t0

            if not plan:
                budget = f"{2*ZONE_TIME_LIMIT:.0f}s" if zoned else f"{TIME_LIMIT:.0f}s"
                error = (f"No layout found ({status}) within {budget}. "
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
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
