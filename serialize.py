"""
Room/Adj/Footprint <-> JSON, needed for the API (Phase 7): POST
/api/solve echoes the solved program back as `program` so POST
/api/validate can be called independently without the caller
reconstructing Room/Adj/Footprint objects by hand (see V2-ALPHA-PLAN.md's
API surface); POST /api/validate deserializes an incoming program back
into those objects for validators.validate() to consume.

`plan` (the solved layout dict place_openings()/to_svg()/validate() all
take) needs no serialization of its own -- it's already built entirely
from plain str/int/list/dict values, so it round-trips through JSON as-is.
"""

from typing import Any, Dict, List, Tuple

from layout import Adj, Footprint, Room


def room_to_dict(r: Room) -> Dict[str, Any]:
    return dict(name=r.name, target_area=r.target_area, min_area=r.min_area,
                max_area=r.max_area, min_dim=r.min_dim, max_aspect=r.max_aspect,
                needs_exterior=r.needs_exterior, edges=list(r.edges), parts=r.parts,
                must_cover=list(r.must_cover) if r.must_cover is not None else None)


def room_from_dict(d: Dict[str, Any]) -> Room:
    must_cover = d.get("must_cover")
    return Room(name=d["name"], target_area=d["target_area"],
                min_area=d.get("min_area"), max_area=d.get("max_area"),
                min_dim=d.get("min_dim", 16), max_aspect=d.get("max_aspect", 2.0),
                needs_exterior=d.get("needs_exterior", True), edges=list(d.get("edges", [])),
                parts=d.get("parts", 1),
                must_cover=tuple(must_cover) if must_cover is not None else None)


def adj_to_dict(a: Adj) -> Dict[str, Any]:
    return dict(a=a.a, b=a.b, min_shared=a.min_shared)


def adj_from_dict(d: Dict[str, Any]) -> Adj:
    return Adj(a=d["a"], b=d["b"], min_shared=d.get("min_shared", 6))


def footprint_to_dict(fp: Footprint) -> Dict[str, Any]:
    return dict(width=fp.width, height=fp.height, voids=[list(v) for v in fp.voids])


def footprint_from_dict(d: Dict[str, Any]) -> Footprint:
    return Footprint(width=d["width"], height=d["height"],
                      voids=[tuple(v) for v in d.get("voids", [])])


def program_to_dict(footprint: Footprint, rooms: List[Room], adjacencies: List[Adj],
                     private: Tuple[str, ...]) -> Dict[str, Any]:
    return dict(footprint=footprint_to_dict(footprint),
                rooms=[room_to_dict(r) for r in rooms],
                adjacencies=[adj_to_dict(a) for a in adjacencies],
                private=list(private))


def program_from_dict(d: Dict[str, Any]):
    """Returns (footprint, rooms, adjacencies, private). `private`, if
    the caller's JSON doesn't include it (POST /api/validate's own
    request shape doesn't -- see orchestrate._private_room_names()),
    comes back as an empty tuple rather than raising, since a caller
    that only has rooms/adjacencies (not a full echoed `program`) has no
    other way to supply it."""
    footprint = footprint_from_dict(d["footprint"])
    rooms = [room_from_dict(r) for r in d["rooms"]]
    adjacencies = [adj_from_dict(a) for a in d["adjacencies"]]
    private = tuple(d.get("private", ()))
    return footprint, rooms, adjacencies, private
