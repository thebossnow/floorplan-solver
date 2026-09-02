"""
Phase 2 validation: orchestrate.py's solve_program()/render_svg() and
validators.validate(), plus the two non-CP-SAT rules from the 2026-09-02
plan review that landed alongside them (ProgramSpec.has_entry,
place_openings()'s hallway-door filter).

Run: .venv/bin/python3 test_orchestrate.py
"""

from layout import place_openings, room_kind
from orchestrate import ProgramSpec, solve_program, render_svg
from validators import validate


def check_solve_validate_render():
    """The basic chain: solve -> validate -> render, all succeed and
    circulation holds, matching the plan's stated solve->validate->render
    ordering (vs. v1's render-before-circulation_ok in _run_solve(),
    deliberately left alone there)."""
    spec = ProgramSpec(total_area=1500, beds=3, baths=2, style="traditional", width=46)
    result = solve_program(spec)
    assert result.plan is not None, f"solve_program failed: {result.status}"
    assert result.entry_room == "Entry"

    v = validate(result)
    assert v["circulation_ok"], f"circulation broken: {v['unreachable']}"
    assert v["findings"] == [], "Phase 2: findings should always be empty (Phase 5 adds validators)"

    svg = render_svg(result, title_block=dict(title="TEST", lines=["1,500 SF"]))
    assert "<svg" in svg and "</svg>" in svg
    print(f"check_solve_validate_render: OK -- status={result.status!r}, "
          f"{len(result.rooms)} rooms, circulation ok, svg rendered "
          f"({len(svg)} chars)")


def check_has_entry_false():
    """has_entry=False drops the separate Entry room; the arrival room
    (Living, for traditional) picks up Entry's old edges=["S"] boundary
    requirement, and circulation still holds from the new entry_room."""
    spec = ProgramSpec(total_area=1500, beds=3, baths=2, style="traditional",
                        width=46, has_entry=False)
    result = solve_program(spec)
    assert result.plan is not None, f"solve_program failed: {result.status}"
    assert "Entry" not in result.plan, "Entry room should not exist when has_entry=False"
    assert result.entry_room == "Living", f"expected Living as entry_room, got {result.entry_room}"

    living_spec = next(r for r in result.rooms if r.name == "Living")
    assert living_spec.edges == ["S"], (
        f"Living should have inherited Entry's edges=['S'], got {living_spec.edges}")

    v = validate(result)
    assert v["circulation_ok"], f"circulation broken from Living: {v['unreachable']}"
    print(f"check_has_entry_false: OK -- no Entry room, Living forced to S edge, "
          f"circulation ok from 'Living' ({len(result.rooms)} rooms)")


def check_has_entry_false_open_concept():
    """Same check, open_concept style -- arrival room should be Great, not
    Living (Living doesn't exist in this style; Great absorbs it)."""
    spec = ProgramSpec(total_area=1800, beds=3, baths=2, style="open_concept",
                        width=48, has_entry=False)
    result = solve_program(spec)
    assert result.plan is not None, f"solve_program failed: {result.status}"
    assert "Entry" not in result.plan
    assert result.entry_room == "Great", f"expected Great as entry_room, got {result.entry_room}"
    great_spec = next(r for r in result.rooms if r.name == "Great")
    assert great_spec.edges == ["S"]
    v = validate(result)
    assert v["circulation_ok"], f"circulation broken from Great: {v['unreachable']}"
    print("check_has_entry_false_open_concept: OK -- Great takes over the arrival role")


def check_hallway_door_filter():
    """place_openings() should draw no door for a Hall<->public-room
    adjacency (open threshold), but still draw one for Hall<->bedroom
    (needs an actual door)."""
    spec = ProgramSpec(total_area=1500, beds=3, baths=2, style="traditional", width=46)
    result = solve_program(spec)
    assert result.plan is not None, f"solve_program failed: {result.status}"

    openings = place_openings(result.plan, result.footprint, result.adjacencies, result.rooms)
    door_pairs = [set(o["rooms"]) for o in openings if o["kind"] == "door"]

    hall_public = [ad for ad in result.adjacencies
                   if {room_kind(ad.a), room_kind(ad.b)} == {"hall", "living"}]
    assert hall_public, "test setup: expected at least one Hall<->public adjacency (Living-Hall)"
    for ad in hall_public:
        assert {ad.a, ad.b} not in door_pairs, (
            f"{ad.a}-{ad.b}: hall<->public adjacency should have NO door, but one was placed")
    hall_bedroom = [ad for ad in result.adjacencies if "Hall" in (ad.a, ad.b)
                     and any(room_kind(n) == "sleep" for n in (ad.a, ad.b))]
    assert hall_bedroom, "test setup: expected at least one Hall<->bedroom adjacency"
    for ad in hall_bedroom:
        assert {ad.a, ad.b} in door_pairs, (
            f"{ad.a}-{ad.b}: hall<->bedroom adjacency should have a door, but none was placed")
    print(f"check_hallway_door_filter: OK -- {len(hall_public)} hall<->public adjacency(ies) "
          f"got no door, {len(hall_bedroom)} hall<->bedroom adjacency(ies) did")


if __name__ == "__main__":
    check_solve_validate_render()
    check_has_entry_false()
    check_has_entry_false_open_concept()
    check_hallway_door_filter()
    print("\nAll Phase 2 checks passed.")
