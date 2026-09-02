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

from flask import Flask, jsonify, render_template, request

from generator import (HALLWAYS, MAX_AREA, MAX_BATHS, MAX_BEDS, MIN_AREA, PRODUCTION_WEIGHTS,
                        STYLES, WIDTH_ASPECT_MAX, WIDTH_MIN_SIDE, ZONE_ROOM_THRESHOLD,
                        default_proximity, generate_program, shelf_pack_hint, width_bounds,
                        zone_of_program)
from layout import FILL_BY_KIND, circulation_ok, display_name, place_openings, room_kind, solve, to_svg
from zoning import solve_zoned

# schedule-table grouping: closets ride along with the bedroom they belong
# to, everything else follows to_svg's own room_kind() buckets
GROUP_BY_KIND = {"living": "Public", "hall": "Public", "sleep": "Private",
                  "wet": "Service", "closet": "Private"}
GROUP_ORDER = ["Public", "Private", "Service"]

TIME_LIMIT = 25.0
# The unzoned path's default/most-common config (the empty-state sample and
# first preset chip) was found to plateau on its primal incumbent within a
# few seconds while CP-SAT's bound spent the rest of TIME_LIMIT crawling
# toward a proof that never arrives (~25% gap left at the cap) -- profiled
# with CpSolver.parameters.log_search_progress per HANDOFF's documented
# technique. NO_IMPROVEMENT_TIMEOUT cuts that plateau short at negligible
# quality cost (roughly full-TIME_LIMIT solution quality, empirically).
#
# _ImprovementTracker.last_improvement (layout.py) starts its clock at
# search start, not at the first incumbent -- so this same timeout was also
# cutting off searches that hadn't found *any* solution yet, misreported as
# "plateaued" when it was really "still searching for a first incumbent".
# Raised 8.0 -> 15.0 (2026-09-02) after that showed up live: a 46x33/3bed/
# 2bath solve (known to succeed in 16-37s with a first incumbent sometimes
# landing late) came back UNKNOWN at exactly 8.0s on Vercel, then succeeded
# on retry with no code change -- i.e. the 8s stall cutoff, not the search
# itself, was the false negative.
NO_IMPROVEMENT_TIMEOUT = 15.0
# ZONE_ROOM_THRESHOLD lives in generator.py now -- zone_of_program() needs it
# too, to decide when to split "private" further into "suite"/"wing".
SOLVE_TIME_BUDGET = 60.0  # total wall-clock ceiling for a zoned solve, shared
                          # across however many zones the program has (see
                          # zoning.solve_zoned's time_budget param) -- leaves
                          # ~60s margin under Vercel's maxDuration (120s, set
                          # in vercel.json, needs a Pro plan above the Hobby
                          # tier's 60s ceiling) for cold start + render,
                          # regardless of zone count

# empty-state sample: a pre-solved SVG cached to disk (regenerate via the
# one-off script this file's git history/HANDOFF notes, or by hand: run
# generate_program(1500, 3, 2, style="traditional", width=46) through solve()
# the same way index() below does, then to_svg(..., path="static/sample-plan.svg"))
# rather than re-solved on every empty-state page load
with open(os.path.join(os.path.dirname(__file__), "static", "sample-plan.svg")) as f:
    SAMPLE_SVG = f.read()
SAMPLE_CAPTION = "Example · 1,500 sf ranch"

# quick-start presets shown on the empty state -- (area, beds, baths, width, style).
# width picked to roughly match the old shape="square"/"rectangular" presets
# (aspect 1.0 / 1.4) now that the form drives the footprint by width slider
# instead of a shape choice.
PRESETS = [
    dict(label="1,200 sf square", area=1200, beds=2, baths=2, width=35, style="traditional"),
    dict(label="1,500 sf ranch", area=1500, beds=3, baths=2, width=46, style="traditional"),
    dict(label="2,000 sf open concept", area=2000, beds=3, baths=2, width=53, style="open_concept"),
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


def _run_solve(source, attempt):
    """Runs one form submission through generate_program() -> solve()/
    solve_zoned() -> to_svg(), exactly the way both the plain POST/GET-with-
    querystring index() route and the fetch-based /solve route need it.
    attempt: whether to actually try (index()'s GET path only wants this
    when query params are present -- a bare GET just prefills the form --
    while both a real POST and /solve always attempt, even if `source`
    itself turns out empty, matching index()'s original
    `request.method == "POST" or source` condition). Returns (form, result,
    error) -- form is always populated (defaults if not attempted),
    result/error follow index()'s original meaning (exactly one of them
    non-None after a real attempt, both None otherwise)."""
    # width: None means "not specified" (old copy-link URLs from before this
    # field existed, or a bare GET) -- make_footprint() then falls back to
    # its old square-ish default rather than erroring.
    form = dict(area=1500, beds=3, baths=2, shape="rectangular", width=46, style="traditional")
    result = None
    error = None

    if attempt:
        try:
            form["area"] = int(source.get("area", ""))
            form["beds"] = int(source.get("beds", ""))
            form["baths"] = int(source.get("baths", ""))
            form["shape"] = source.get("shape", "rectangular")
            width_raw = source.get("width", "")
            form["width"] = int(width_raw) if width_raw not in ("", None) else None
            form["style"] = source.get("style", "traditional")

            if not (MIN_AREA <= form["area"] <= MAX_AREA):
                raise ValueError(f"Area must be between {MIN_AREA} and {MAX_AREA} sq ft.")
            if form["shape"] not in ("square", "rectangular"):
                raise ValueError("Shape must be square or rectangular.")
            if form["style"] not in STYLES:
                raise ValueError(f"Style must be one of {', '.join(STYLES)}.")

            fp, rooms, adj, private = generate_program(
                form["area"], form["beds"], form["baths"], form["shape"], form["style"],
                width=form["width"])
            form["width"] = fp.width  # reflect the resolved/clamped value (also
                                       # what the copy-link querystring encodes)
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
                    fp, rooms, adj, zone_of, time_budget=SOLVE_TIME_BUDGET, workers=8,
                    hallways=HALLWAYS, private=private,
                    weights=PRODUCTION_WEIGHTS, proximity=proximity)
            else:
                hint = shelf_pack_hint(fp, rooms)
                plan, status, objective_value, best_objective_bound, solver_wall_time = solve(
                    fp, rooms, adj, time_limit=TIME_LIMIT, workers=8, hint=hint,
                    hallways=HALLWAYS, private=private,
                    no_improvement_timeout=NO_IMPROVEMENT_TIMEOUT,
                    proximity=proximity, **PRODUCTION_WEIGHTS)
            elapsed = time.time() - t0

            if not plan:
                budget = f"{SOLVE_TIME_BUDGET:.0f}s" if zoned else f"{TIME_LIMIT:.0f}s"
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
                # A capped solve can return FEASIBLE instead of OPTIMAL -- still
                # a fully valid layout (every hard rule held), just not proven
                # to be the single best arrangement. Compute this here, not in
                # the template, since the zoned path's status is a concatenated
                # "zone 'x': OPTIMAL, zone 'y': FEASIBLE" string that's only
                # meaningful to parse once, against zone_metrics's own raw
                # per-zone status (its 4th element -- see zoning.solve_zoned).
                if zoned:
                    is_optimal = zone_metrics is not None and all(
                        st == "OPTIMAL" for (_, _, _, st) in zone_metrics.values())
                else:
                    is_optimal = status == "OPTIMAL"
                result = dict(
                    svg=svg_markup,
                    headline=(f'{fp.width} × {fp.height} ft · {fp.area():,} sf · '
                               f'{form["beds"]} bed / {form["baths"]} bath'),
                    status=status,
                    is_optimal=is_optimal,
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

    return form, result, error


@app.route("/", methods=["GET", "POST"])
def index():
    # a GET with query params is a "copy link" visit reproducing a past
    # result (see templates/index.html's Copy link button, which builds
    # this same area/beds/baths/width/style querystring via url_for) --
    # solve immediately rather than just prefilling the form, so the link
    # is a true "see this exact result" link, not just a starting point.
    # This route stays a plain full-page POST/GET -- it's the no-JS
    # fallback (see templates/index.html's submit handler, which normally
    # intercepts the form and posts to /solve instead) and the target for
    # copy-link visits, both of which want a real page load either way.
    source = request.form if request.method == "POST" else request.args
    attempt = request.method == "POST" or bool(source)
    form, result, error = _run_solve(source, attempt)

    return render_template(
        "index.html", form=form, result=result, error=error,
        max_beds=MAX_BEDS, max_baths=MAX_BATHS, min_area=MIN_AREA, max_area=MAX_AREA,
        width_bounds=width_bounds, width_aspect_max=WIDTH_ASPECT_MAX,
        width_min_side=WIDTH_MIN_SIDE, styles=list(STYLES),
        living=FILL_BY_KIND["living"], sleep=FILL_BY_KIND["sleep"], wet=FILL_BY_KIND["wet"],
        sample_svg=SAMPLE_SVG, sample_caption=SAMPLE_CAPTION, presets=PRESETS,
    )


@app.route("/solve", methods=["POST"])
def solve_route():
    """fetch()-based submit target (see templates/index.html) -- runs the
    exact same _run_solve() as index(), but returns just the drawing-col
    fragment's HTML (for a success) or the error string (for a failure)
    instead of a full page, so the browser tab never navigates away during
    the ~10-45s solve. Doesn't reduce total solve latency -- Vercel's
    Python functions don't keep state between invocations, so a real
    background-job-plus-polling design would need external infra (a
    datastore) this project doesn't have yet -- it only removes the blank/
    unloading-tab experience of a full-page POST."""
    form, result, error = _run_solve(request.form, True)
    if not result:
        return jsonify(ok=False, error=error, form=form)
    drawing_html = render_template("_drawing_result.html", result=result, form=form)
    return jsonify(ok=True, drawing_html=drawing_html, form=form)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
