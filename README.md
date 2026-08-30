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

The objective minimizes total deviation from the target area program.

## Install

```
pip install -r requirements.txt
```

## Usage

See `test_house.py` for a full example: a 10-room, 1200 sq ft program
with adjacency and circulation checks.

```
python3 test_house.py
```

## Status

Prototype. Verified on a single test program:

- Feasible solution in 2 to 5 seconds
- Optimal in under a minute
- All requested adjacencies satisfied
- Valid circulation from entry to every room

Known limitations:

- Every room is a single rectangle (no L-shapes)
- Zero wall thickness (dimensions are centerline)
- No door or window placement (though `shared_walls()` returns every
  usable wall segment, so this is a short pass away)
- Room area targets must sum exactly to the footprint area
- Slows down past roughly 15 rooms in one solve; larger programs need
  to be split by zone and stitched together

## License

Not yet decided.
