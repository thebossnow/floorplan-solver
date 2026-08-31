"""
Turns a simple program (total area, bed count, bath count, footprint
shape) into a Room/Adj list that layout.solve() can consume.

This is a fixed proportional room mix (roughly what test_house.py
uses), not a design system -- it exists to drive the web form in
app.py. For anything bespoke, build the Room/Adj list by hand instead.
"""

from layout import Room, Adj, Footprint, add_closets

MIN_AREA = 400
MAX_AREA = 10000
MAX_BEDS = 5
MAX_BATHS = 4

# fraction of the footprint each fixed room gets; normalized against
# whatever the actual bed/bath count adds up to, not required to sum to 1
BASE_PCTS = {
    "Entry": 0.040,
    "Living": 0.205,
    "Kitchen": 0.133,
    "Dining": 0.100,
    "Hall": 0.050,
    "Utility": 0.050,
}
PRIMARY_PCT = 0.167
PRIMARY_BATH_PCT = 0.060
BED_PCT = 0.117       # each secondary bedroom
BATH_PCT = 0.042      # each secondary bathroom
CLOSET_AREA = 20      # sf, carved out of each bedroom's own target

# sanity floors so a small requested area doesn't hand the solver an
# unsolvable room (e.g. a 12 sf "Hall")
FLOORS = {
    "Entry": 30, "Living": 140, "Kitchen": 90, "Dining": 70,
    "Hall": 30, "Utility": 30, "Primary": 110, "PrimBath": 35,
}
BED_FLOOR = 75
BATH_FLOOR = 35


def make_footprint(total_area: int, shape: str = "rectangular", aspect: float = 1.4) -> Footprint:
    if shape == "square":
        side = max(round(total_area ** 0.5), 15)
        return Footprint(width=side, height=side)
    W = max(round((total_area * aspect) ** 0.5), 15)
    H = max(round(total_area / W), 15)
    return Footprint(width=W, height=H)


def generate_program(total_area: int, beds: int = 3, baths: int = 2, shape: str = "rectangular"):
    """Returns (footprint, rooms, adjacencies, private_room_names)."""
    beds = max(1, min(beds, MAX_BEDS))
    baths = max(1, min(baths, MAX_BATHS))

    fp = make_footprint(total_area, shape)
    F = fp.area()

    pcts = dict(BASE_PCTS)
    pcts["Primary"] = PRIMARY_PCT
    pcts["PrimBath"] = PRIMARY_BATH_PCT
    for i in range(2, beds + 1):
        pcts[f"Bed{i}"] = BED_PCT
    for i in range(2, baths + 1):
        pcts[f"Bath{i}"] = BATH_PCT
    total_pct = sum(pcts.values())

    targets = {}
    for name, pct in pcts.items():
        floor = FLOORS.get(name, BED_FLOOR if name.startswith("Bed") else BATH_FLOOR)
        targets[name] = max(round(pct / total_pct * F), floor)

    bedroom_names = ["Primary"] + [f"Bed{i}" for i in range(2, beds + 1)]
    bathroom_names = ["PrimBath"] + [f"Bath{i}" for i in range(2, baths + 1)]

    for b in bedroom_names:
        floor = FLOORS.get(b, BED_FLOOR)
        targets[b] = max(targets[b] - CLOSET_AREA, floor)

    rooms = [
        Room("Entry", targets["Entry"], min_dim=5, max_aspect=2.5, edges=["S"]),
        Room("Living", targets["Living"], min_dim=10, max_aspect=1.9),
        Room("Kitchen", targets["Kitchen"], min_dim=8, max_aspect=2.0),
        Room("Dining", targets["Dining"], min_dim=8, max_aspect=1.8),
        Room("Hall", targets["Hall"], min_dim=3, max_aspect=8.0, needs_exterior=False),
        Room("Utility", targets["Utility"], min_dim=5, max_aspect=2.5, needs_exterior=False),
        Room("Primary", targets["Primary"], min_dim=11, max_aspect=1.7),
        Room("PrimBath", targets["PrimBath"], min_dim=5, max_aspect=2.5),
    ]
    for i in range(2, beds + 1):
        rooms.append(Room(f"Bed{i}", targets[f"Bed{i}"], min_dim=9, max_aspect=1.7))
    for i in range(2, baths + 1):
        rooms.append(Room(f"Bath{i}", targets[f"Bath{i}"], min_dim=5, max_aspect=2.5))

    adj = [
        Adj("Entry", "Living"),
        Adj("Living", "Dining"),
        Adj("Dining", "Kitchen"),
        Adj("Living", "Hall"),
        Adj("Kitchen", "Utility"),
        Adj("Hall", "Primary"),
        Adj("Primary", "PrimBath"),
    ]
    for i in range(2, beds + 1):
        adj.append(Adj("Hall", f"Bed{i}"))
    for i in range(2, baths + 1):
        adj.append(Adj("Hall", f"Bath{i}"))

    rooms, adj = add_closets(rooms, adj, bedroom_names, area=CLOSET_AREA, min_dim=3, max_aspect=3.0)

    # only leaf rooms are private (can't be a hallway to somewhere else) --
    # bedrooms must stay non-private so BFS can relay through them to reach
    # their own ensuite bath/closet
    private = tuple(bathroom_names) + tuple(f"{b}Closet" for b in bedroom_names)
    return fp, rooms, adj, private
