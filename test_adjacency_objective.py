"""
Phase 6 validation: the adjacency-matrix soft objective (AdjPref +
adjacency_weight) -- weight=0 is a no-op (matches solve() called with no
AdjPref at all), weight>0 has a measurable effect (pushes a named,
otherwise-unconstrained pair to actually touch).

Three equal rooms tiling a 30x10 strip with NO hard adjacencies at all --
left-to-right is the natural (and, empirically, the unique-up-to-symmetry
OPTIMAL) tiling with no preference active, landing A and C on opposite
ends, not touching. AdjPref("A", "C") with a positive weight measurably
changes that.

Run: .venv/bin/python3 test_adjacency_objective.py
"""

from layout import Room, AdjPref, Footprint, solve


def _rooms():
    return [
        Room("A", 100, min_dim=8, max_aspect=2.0),
        Room("B", 100, min_dim=8, max_aspect=2.0),
        Room("C", 100, min_dim=8, max_aspect=2.0),
    ]


def _touching(p1, p2):
    return (p1["x2"] == p2["x1"] or p2["x2"] == p1["x1"] or
            p1["y2"] == p2["y1"] or p2["y2"] == p1["y1"])


def check_weight_zero_is_noop():
    fp = Footprint(width=30, height=10)  # 300 gu^2
    plan0, status0, obj0, _, _ = solve(fp, _rooms(), [], time_limit=15, workers=1, seed=0)
    assert plan0 is not None, f"baseline failed to solve: {status0}"

    # same program, AdjPref given but weight=0 (the default) -- should be
    # byte-for-byte equivalent to not passing adjacency_preferences at all,
    # since the extra objective term is multiplied by 0
    plan1, status1, obj1, _, _ = solve(fp, _rooms(), [], time_limit=15, workers=1, seed=0,
                                        adjacency_preferences=[AdjPref("A", "C")])
    assert plan1 is not None, f"weight=0 run failed to solve: {status1}"
    assert obj1 == obj0, f"weight=0 should be a no-op: baseline obj={obj0}, weight=0 obj={obj1}"
    for n in ("A", "B", "C"):
        p0, p1 = plan0[n]["parts"][0], plan1[n]["parts"][0]
        assert (p0["x1"], p0["y1"], p0["x2"], p0["y2"]) == (p1["x1"], p1["y1"], p1["x2"], p1["y2"]), (
            f"{n}: weight=0 changed the solved geometry (should be identical)")
    print(f"check_weight_zero_is_noop: OK -- objective identical ({obj0}) and geometry "
          "identical with AdjPref present but weight=0")


def check_weight_positive_has_effect():
    fp = Footprint(width=30, height=10)

    plan0, status0, _, _, _ = solve(fp, _rooms(), [], time_limit=15, workers=1, seed=0)
    assert plan0 is not None, f"baseline failed to solve: {status0}"
    a0, c0 = plan0["A"]["parts"][0], plan0["C"]["parts"][0]
    assert not _touching(a0, c0), (
        "test setup assumption broken: A and C already touch with no preference active "
        f"(A={a0}, C={c0}) -- pick a different baseline that doesn't")

    plan1, status1, _, _, _ = solve(fp, _rooms(), [], time_limit=15, workers=1, seed=0,
                                     adjacency_preferences=[AdjPref("A", "C")], adjacency_weight=1)
    assert plan1 is not None, f"weight=1 run failed to solve: {status1}"
    a1, c1 = plan1["A"]["parts"][0], plan1["C"]["parts"][0]
    assert _touching(a1, c1), (
        f"expected A and C to end up touching with adjacency_weight=1, but they don't "
        f"(A={a1}, C={c1})")
    print(f"check_weight_positive_has_effect: OK -- A/C not touching with no preference "
          f"(A={a0['x1']}-{a0['x2']}, C={c0['x1']}-{c0['x2']}), touching with "
          f"adjacency_weight=1 (A={a1['x1']}-{a1['x2']}, C={c1['x1']}-{c1['x2']})")


if __name__ == "__main__":
    check_weight_zero_is_noop()
    check_weight_positive_has_effect()
    print("\nAll Phase 6 adjacency-objective checks passed.")
