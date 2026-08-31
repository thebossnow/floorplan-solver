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

## Status

Prototype. Verified on a single test program:

- Feasible solution in 2 to 5 seconds
- Optimal in under a minute
- All requested adjacencies satisfied
- Valid circulation from entry to every room

Known limitations:

- L/T/U-shaped rooms (`parts>1`) render as their constituent rectangles
  in the SVG, with a visible seam — no merged polygon outline yet
- Zero wall thickness (dimensions are centerline)
- No door or window placement (though `shared_walls()` returns every
  usable wall segment, so this is a short pass away)
- Room area targets must sum exactly to the footprint area
- Slows down past roughly 15 rooms in one solve; larger programs need
  to be split by zone and stitched together

## License

MIT
