# floorplan-solver

A constraint-solver floor plan layout generator. No trained model, no
training data. OR-Tools CP-SAT does the search.

## How it works

The plan is modeled as an exact rectangular partition of the footprint.
Three constraints together force a gap-free tiling with no explicit
tiling logic:

1. every room is an axis-aligned rectangle inside the footprint
2. no two rooms overlap
3. the room areas sum to exactly the footprint area

On top of that: area targets, minimum room dimensions, aspect ratio
caps, adjacency (rooms must share a wall segment long enough for a
door), daylight (a room must touch the outer boundary unless marked
interior), and edge anchors (pin a room to a specific side).

A room is normally one rectangle. Setting `Room(..., parts=2)` builds
it from two rectangles instead, each sized and placed independently
by the solver, with a mandatory constraint that they share a wall —
the standard way to get an L-shaped room (a chain of 3+ parts gives
T/U/Z shapes, if the solver can still make them fit). Adjacency and
daylight checks pass for a multi-part room if any one of its parts
qualifies. See `test_lshape.py`.

The objective minimizes total deviation from the target area program.

`Adj` is a hard constraint, not a preference — the solver has no
feasible way to skip it. So "every bedroom must have a closet" is just
a program-authoring pattern: give the closet its own small interior
`Room` and force it onto the bedroom with `Adj`. The `add_closets()`
helper does this for a list of bedroom names in one call. See how
`test_house.py` uses it for `Primary` and `Bed2`.

## Install

```
pip install -r requirements.txt
```

## Usage

See `test_house.py` for a full example: a 10-room, 1200 sq ft program
with adjacency and circulation checks. See `test_lshape.py` for the
same, with an L-shaped room.

```
python3 test_house.py
python3 test_lshape.py
```

## Web app

`app.py` is a small Flask front end: enter a foundation area (sq ft),
bed/bath counts, square vs. rectangular, and a room style, and it solves
and renders a floor plan inline. `generator.py` turns those inputs into a
Room/Adj program (closets included via `add_closets()`) and hands it to
`solve()`. The room style (`generator.STYLES`) picks the public-room mix
-- `traditional` (separate Living/Kitchen/Dining) or `open_concept` (one
larger `Great` room) -- while the bedroom/bathroom wing stays the same
either way; `generator._fit_targets()` scales whichever mix is chosen so
room area targets always sum to exactly the footprint area, regardless of
style, bed/bath count, or how hard its per-room area floors bite.

```
python3 app.py
```

then open http://localhost:5000. Each request runs a real solve
(capped at 25s server-side — a capped solve may come back FEASIBLE
rather than OPTIMAL, still a valid layout) so it isn't instant; this
runs the dev server only; put it behind gunicorn/nginx (or similar)
for anything but local use.

## Robustness &amp; rendering extras

- `solve()` calls `validate_program()` first, so a self-inconsistent room
  (e.g. `min_dim` too large for its own area bounds) or an over/under
  -programmed footprint raises a specific `ValueError` immediately, instead
  of a low-level OR-Tools domain error or a full `time_limit` spent on a
  solve that could never succeed.
- `place_openings()` picks a door per adjacency and a window per
  daylight-required room from the solved geometry, using the same wall
  segments `shared_walls()` already finds; pass the result into `to_svg`'s
  `openings=` argument.
- `to_svg` renders room rectangles inset by a wall thickness
  (`interior_thickness` / `exterior_thickness`, still centerline in the
  solver itself) and merges a multi-part room's rectangles into one L/T/U
  outline instead of drawing a visible seam between parts.
- `to_svg(..., path=None)` returns the markup string directly instead of
  writing to disk — use this from a server, since writing every request to
  the same path is a race (this is what `app.py` does now).
- `solve()` takes an optional `hint` (a `{part_key: (x1,y1,x2,y2)}` warm
  start); `generator.shelf_pack_hint()` produces one from a rough packing
  heuristic. **Empirically this did not reduce solve time or improve
  solution quality** in spot checks against CP-SAT's default 8-worker
  portfolio search on 18-room programs — it's left in as opt-in
  infrastructure (e.g. for a single-worker config or a better heuristic
  later), not a fix for the scaling limit below.

## Zoning: splitting large programs

`zoning.solve_zoned()` is the real fix for the >15-room slowdown: split
the room program into exactly two zones (e.g. a public wing and a
bedroom wing), and it divides the footprint into two adjacent
sub-footprints (proportioned to each zone's room-area total) and solves
each with the ordinary `solve()` -- so within a zone every hard
constraint is still exact, just over a smaller, faster problem.

```python
zone_of = {"Entry": "public", "Living": "public", ..., "Primary": "private", ...}
plan, status, cross = solve_zoned(footprint, rooms, adjacencies, zone_of,
                                   split_axis="x", time_limit=20)
```

Adjacencies that cross the zone boundary can't be a hard guarantee the
way they are within one zone -- two independently-solved zones have no
way to coordinate where along their shared wall a room ends up.
`solve_zoned()` does its best (anchors each cross-zone room to the wall
that faces the other zone, and pins it to a shared coordinate band along
that wall so the two rooms' extents actually have to overlap) and then
tells you the truth: `cross["satisfied"]` / `cross["failed"]` name which
cross-zone adjacencies actually ended up touching. If the anchor pins
would have made an otherwise-solvable zone infeasible (seen with a
hallway anchored to the boundary while also required to touch six
bedrooms), that zone is retried without them rather than failing outright
-- see `test_zoned.py` for both outcomes. Keep cross-zone adjacencies to
a small number of connector rooms for the best odds of a real doorway.

```
python3 test_zoned.py
```

## Status

Prototype. Verified on a single test program:

- Feasible solution in 2 to 5 seconds
- Optimal in under a minute
- All requested adjacencies satisfied
- Valid circulation from entry to every room

Known limitations:

- Room area targets must sum exactly to the footprint area (now a fast,
  clear error via `validate_program()` rather than a silent timeout)
- Slows down past roughly 15 rooms in a single `solve()` call; the
  warm-start hint above didn't fix this in testing, so use
  `zoning.solve_zoned()` for larger programs instead (see below)
- `generator.py` now offers two room-mix styles (`STYLES`, selectable in
  the web form: `traditional`, `open_concept`) instead of one fixed
  layout, but it's still a couple of hand-authored proportional mixes,
  not a design system; large bed/bath counts on a small area can still
  produce no feasible layout within the time cap (though you'll now find
  out immediately rather than after a full timeout)
- `solve_zoned()` only splits into exactly 2 zones along one axis; a
  program that needs 3+ zones (e.g. public / private / garage wings) has
  to be split pairwise by hand, one `solve_zoned()` call at a time

## License

MIT
