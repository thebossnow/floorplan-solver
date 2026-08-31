"""
Minimal web front end for the floor plan solver.

Form -> generator.generate_program() -> layout.solve() -> SVG, embedded
back into the same page. Solve is capped at TIME_LIMIT seconds so a
request can't hang indefinitely; a capped solve may return FEASIBLE
instead of OPTIMAL, which is still a valid layout.
"""

import time

from flask import Flask, render_template, request

from generator import MAX_AREA, MAX_BATHS, MAX_BEDS, MIN_AREA, STYLES, generate_program, shelf_pack_hint
from layout import circulation_ok, place_openings, solve, to_svg

TIME_LIMIT = 25.0

app = Flask(__name__)


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

            t0 = time.time()
            hint = shelf_pack_hint(fp, rooms)
            plan, status = solve(fp, rooms, adj, time_limit=TIME_LIMIT, workers=8, hint=hint)
            elapsed = time.time() - t0

            if not plan:
                error = (f"No layout found ({status}) within {TIME_LIMIT:.0f}s. "
                         "Try a larger area, fewer bedrooms/bathrooms, or a different shape.")
            else:
                openings = place_openings(plan, fp, adj, rooms)
                svg_markup = to_svg(plan, fp, path=None, openings=openings)
                ok, unreachable = circulation_ok(plan, "Entry", private=private)
                result = dict(
                    svg=svg_markup,
                    status=status,
                    elapsed=round(elapsed, 1),
                    footprint=f'{fp.width} x {fp.height} ft ({fp.area()} sf)',
                    total=sum(r["area"] for r in plan.values()),
                    circulation_ok=ok,
                    unreachable=unreachable,
                    rooms=sorted((n, r["area"], r["target"]) for n, r in plan.items()),
                )
        except ValueError as e:
            error = str(e)

    return render_template(
        "index.html", form=form, result=result, error=error,
        max_beds=MAX_BEDS, max_baths=MAX_BATHS, min_area=MIN_AREA, max_area=MAX_AREA,
        styles=list(STYLES),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
