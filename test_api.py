"""
Phase 7 validation: the JSON API (api_routes.py), exercised entirely
through Flask's test client -- no live server needed, per
V2-ALPHA-PLAN.md's own stated Phase 7 verification approach.

Covers POST /api/solve (success, INFEASIBLE-with-diagnosis, and several
400 input-validation cases), POST /api/validate called independently
using a prior /api/solve response's own echoed `program` (not a fresh
solve), and GET /api/jurisdictions.

Run: .venv/bin/python3 test_api.py
"""

import app

client = app.app.test_client()


def check_solve_success():
    r = client.post("/api/solve", json=dict(total_area=1500, beds=3, baths=2,
                                              width=46, style="traditional"))
    j = r.get_json()
    assert r.status_code == 200, (r.status_code, j)
    assert j["ok"] is True, j
    assert j["status"] in ("OPTIMAL", "FEASIBLE"), j["status"]
    assert j["plan"] and isinstance(j["plan"], dict)
    assert j["zoned"] is False
    assert set(j["footprint"]) == {"width", "height", "voids"}
    program = j["program"]
    assert set(program) == {"footprint", "rooms", "adjacencies", "private"}
    assert len(program["rooms"]) >= 1
    print(f"check_solve_success: OK -- status={j['status']!r}, "
          f"{len(program['rooms'])} rooms, program echoed")


def check_solve_infeasible_with_diagnosis():
    # 700 sf for a 3bed/2bath traditional program (14 rooms) is
    # genuinely too cramped -- found empirically to reliably prove
    # INFEASIBLE (not just time out to UNKNOWN) within a few seconds.
    r = client.post("/api/solve", json=dict(total_area=700, beds=3, baths=2,
                                             style="traditional",
                                             diagnose_infeasibility=True, time_limit=8))
    j = r.get_json()
    assert r.status_code == 200, (r.status_code, j)
    assert j["ok"] is False, j
    assert j["status"] == "INFEASIBLE", j["status"]
    assert j["diagnosis"] is not None, "expected a diagnosis for a proven-INFEASIBLE solve"
    assert j["diagnosis"]["conflicting_rules"], "expected a non-empty conflicting_rules list"
    assert isinstance(j["diagnosis"]["message"], str) and j["diagnosis"]["message"]
    print(f"check_solve_infeasible_with_diagnosis: OK -- status=INFEASIBLE, "
          f"{len(j['diagnosis']['conflicting_rules'])} conflicting rule(s)")


def check_solve_input_validation():
    cases = [
        (dict(beds=3, baths=2), "missing total_area"),
        (dict(total_area=99999, beds=3, baths=2), "total_area out of range"),
        (dict(total_area=1500, beds=3, baths=2, style="brutalist"), "unknown style"),
        (dict(total_area=1500, beds=3, baths=2, ruleset="made-up-jurisdiction"), "unknown ruleset"),
    ]
    for body, label in cases:
        r = client.post("/api/solve", json=body)
        j = r.get_json()
        assert r.status_code == 400, f"{label}: expected 400, got {r.status_code} ({j})"
        assert j["ok"] is False and j.get("error"), f"{label}: expected an error message, got {j}"
    print(f"check_solve_input_validation: OK -- all {len(cases)} bad-input cases correctly 400")


def check_solve_with_known_ruleset():
    # time_limit=60, not app.py's production default (25s) -- the
    # ruleset's closet-alignment rules are a real, non-trivial constraint
    # across every bedroom at once, found empirically (test_hard_rules.py)
    # to need more search time than a normal solve. 2200 sf, not 1500:
    # 1500/46 was already a marginal config even with no ruleset active
    # (seen elsewhere this session) -- picking a config that's already
    # borderline would conflate this test with that pre-existing
    # reliability gap instead of cleanly testing ruleset plumbing.
    r = client.post("/api/solve", json=dict(total_area=2200, beds=3, baths=2,
                                              style="traditional",
                                              ruleset="generic-residential", time_limit=60))
    j = r.get_json()
    assert r.status_code == 200 and j["ok"], (r.status_code, j)
    print("check_solve_with_known_ruleset: OK -- 'generic-residential' accepted and solved")


def check_validate_using_echoed_program():
    solve_r = client.post("/api/solve", json=dict(total_area=1500, beds=3, baths=2,
                                                    width=46, style="traditional"))
    solve_j = solve_r.get_json()
    assert solve_j["ok"], solve_j

    # /api/validate independently -- no re-solve, just the prior response's
    # own plan + echoed program handed straight back in
    body = dict(plan=solve_j["plan"], **solve_j["program"])
    r = client.post("/api/validate", json=body)
    j = r.get_json()
    assert r.status_code == 200, (r.status_code, j)
    assert j["ok"] is True, j
    assert j["circulation_ok"] is True, j["unreachable"]
    assert isinstance(j["findings"], list)
    print(f"check_validate_using_echoed_program: OK -- circulation ok, "
          f"{len(j['findings'])} finding(s) via the echoed program, no re-solve")


def check_validate_input_validation():
    r = client.post("/api/validate", json=dict(plan={}))  # missing footprint/rooms/adjacencies
    j = r.get_json()
    assert r.status_code == 400 and j["ok"] is False, (r.status_code, j)
    print("check_validate_input_validation: OK -- missing program fields correctly 400")


def check_jurisdictions():
    r = client.get("/api/jurisdictions")
    j = r.get_json()
    assert r.status_code == 200, (r.status_code, j)
    assert j["jurisdictions"] == [{"id": "generic-residential", "name": "Generic Residential (stub)"}]
    print("check_jurisdictions: OK -- matches the plan's documented shape exactly")


if __name__ == "__main__":
    check_solve_success()
    check_solve_infeasible_with_diagnosis()
    check_solve_input_validation()
    check_solve_with_known_ruleset()
    check_validate_using_echoed_program()
    check_validate_input_validation()
    check_jurisdictions()
    print("\nAll Phase 7 API checks passed.")
