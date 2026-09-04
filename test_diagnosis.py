"""
Phase 3 validation: solve(diagnose_infeasibility=True, diagnosis_out=[...])
-- the assumption-literal wiring on the four existing per-entity/per-part
hard rules (adjacency, exterior daylight, door access, min room
dimension) plus the min-dim domain restructuring.

Two deliberately-conflicting hand-built programs, each isolating exactly
ONE of the four rule types as the actual cause of infeasibility (every
other active rule stays trivially satisfiable), so the returned
diagnosis can be checked against a known-correct answer:

- check_min_dim_conflict: a single room whose exact-partition-forced
  shape (footprint area == room's only possible shape, given aspect
  headroom) is narrower than its own min_dim requirement.
- check_adjacency_conflict: two rooms with an Adj.min_shared bigger than
  the footprint's own max dimension -- physically unsatisfiable
  regardless of layout, independent of where either room ends up.

daylight and door_access aren't separately covered here -- they use the
identical .only_enforce_if()/add_assumption()/rule-id-dict pattern as
adjacency (verified by direct code review, not contrived into their own
INFEASIBLE case), and a clean, layout-independent conflict construction
for either one is meaningfully harder to build than for min_dim/
adjacency without adding rooms/edges whose OWN geometry could become a
second, confounding cause -- not worth it for what's meant to be a
focused correctness gate.

Both cases assert conflicting_rules CONTAINS the expected rule id, not
equals it exactly: sufficient_assumptions_for_infeasibility() returns a
*sufficient*, not *minimal*, core (V2-ALPHA-PLAN.md's own documented
caveat) -- it may legitimately include an assumption that isn't strictly
necessary (e.g. daylight:Studio alongside min_dim:Studio below, even
though only the latter is truly load-bearing), so an exact-equality
assertion would be over-fitting to one solver run's specific (valid but
non-minimal) answer.

Run: .venv/bin/python3 test_diagnosis.py
"""

from layout import Room, Adj, Footprint, solve


def check_min_dim_conflict():
    """A single room, alone in its footprint: exact partition + area cap
    forces it to exactly fill the 10x50 footprint (500 sf, aspect loose
    enough not to bind), but min_dim=20 requires its short side >= 20 --
    impossible once the shape is pinned to 10x50. Only min_dim's
    assumption can actually be blamed: daylight is trivially satisfied
    (a room filling the whole footprint touches every edge regardless),
    and there's no adjacency/hallway in play at all."""
    fp = Footprint(width=10, height=50)  # 500 gu^2, alone forces the room to fill it exactly
    rooms = [Room("Studio", 500, min_dim=20, max_aspect=10.0)]
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, [], time_limit=15, workers=8,
                                   diagnose_infeasibility=True, diagnosis_out=diagnosis_out)
    assert plan is None, f"expected INFEASIBLE, got a plan (status={status})"
    assert status == "INFEASIBLE", f"expected status INFEASIBLE, got {status!r}"
    assert len(diagnosis_out) == 1, f"expected exactly one diagnosis, got {len(diagnosis_out)}"
    diagnosis = diagnosis_out[0]
    assert "min_dim:Studio" in diagnosis.conflicting_rules, (
        f"expected 'min_dim:Studio' in conflicting_rules, got {diagnosis.conflicting_rules}")
    print(f"check_min_dim_conflict: OK -- status={status}, "
          f"conflicting_rules={diagnosis.conflicting_rules}, message={diagnosis.message!r}")


def check_adjacency_conflict():
    """Two ordinary rooms, but Adj.min_shared=100 in a 20x20 footprint --
    no shared wall segment can ever reach length 100 (the footprint's own
    max dimension is 20), regardless of how the rooms are arranged. min_dim
    and daylight are both loose/trivial here (5ft min_dim in a 20x20
    footprint, needs_exterior default on two rooms that between them must
    cover the whole footprint area -- easily satisfiable)."""
    fp = Footprint(width=20, height=20)  # 400 gu^2
    rooms = [
        Room("A", 200, min_dim=5, max_aspect=5.0),
        Room("B", 200, min_dim=5, max_aspect=5.0),
    ]
    adj = [Adj("A", "B", min_shared=100)]
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=15, workers=8,
                                   diagnose_infeasibility=True, diagnosis_out=diagnosis_out)
    assert plan is None, f"expected INFEASIBLE, got a plan (status={status})"
    assert status == "INFEASIBLE", f"expected status INFEASIBLE, got {status!r}"
    assert len(diagnosis_out) == 1, f"expected exactly one diagnosis, got {len(diagnosis_out)}"
    diagnosis = diagnosis_out[0]
    assert "adjacency:A:B" in diagnosis.conflicting_rules, (
        f"expected 'adjacency:A:B' in conflicting_rules, got {diagnosis.conflicting_rules}")
    print(f"check_adjacency_conflict: OK -- status={status}, "
          f"conflicting_rules={diagnosis.conflicting_rules}, message={diagnosis.message!r}")


def check_diagnosis_doesnt_break_feasible():
    """diagnose_infeasibility=True on a program that DOES solve -- the
    widened domains + enforce_if-wrapped constraints must still produce a
    fully valid plan (every hard rule actually held), not just skip them
    because they're now conditional. No diagnosis should be recorded."""
    fp = Footprint(width=24, height=20)  # 480 gu^2 (matches Living+Bed target sum exactly)
    rooms = [
        Room("Living", 300, min_dim=12, max_aspect=2.2, edges=["S"]),
        Room("Bed", 180, min_dim=9, max_aspect=2.0),
    ]
    adj = [Adj("Living", "Bed", min_shared=4)]
    diagnosis_out = []
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=15, workers=8,
                                   diagnose_infeasibility=True, diagnosis_out=diagnosis_out)
    assert plan is not None, f"expected a plan, got status={status}"
    assert diagnosis_out == [], f"expected no diagnosis for a feasible solve, got {diagnosis_out}"
    for name, r in ((n, room) for n, room in [("Living", rooms[0]), ("Bed", rooms[1])]):
        part = plan[name]["parts"][0]
        w, h = part["x2"] - part["x1"], part["y2"] - part["y1"]
        assert w >= r.min_dim and h >= r.min_dim, f"{name}: {w}x{h} violates min_dim={r.min_dim}"
    print(f"check_diagnosis_doesnt_break_feasible: OK -- status={status}, "
          "plan valid, min_dim actually held for both rooms, no spurious diagnosis")


def check_default_params_unaffected():
    """Omitting diagnose_infeasibility/diagnosis_out entirely (today's
    call signature) must still work -- backward compatibility for every
    existing caller."""
    fp = Footprint(width=24, height=20)  # 480 gu^2
    rooms = [
        Room("Living", 300, min_dim=12, max_aspect=2.2, edges=["S"]),
        Room("Bed", 180, min_dim=9, max_aspect=2.0),
    ]
    adj = [Adj("Living", "Bed", min_shared=4)]
    plan, status, _, _, _ = solve(fp, rooms, adj, time_limit=15, workers=8)
    assert plan is not None, f"expected a plan, got status={status}"
    print(f"check_default_params_unaffected: OK -- status={status}, plan produced with no new args")


if __name__ == "__main__":
    check_min_dim_conflict()
    check_adjacency_conflict()
    check_diagnosis_doesnt_break_feasible()
    check_default_params_unaffected()
    print("\nAll Phase 3 diagnosis checks passed.")
