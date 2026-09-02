# floorplan-solver "v2 alpha" — rules engine, infeasibility diagnosis, JSON API

## Context

v1 (this repo, live at metawhalealerts.com on Vercel) generates a single feasible layout and returns either a drawing or a bare "No layout found." The goal for "v2 alpha" is to answer *why not* — e.g. "4 beds/3 baths won't fit in 1,200 sf with 42" halls, because X and Y conflict" — so an eventual AI agent (Eve, Vercel's open-source TypeScript agent framework) can drive it without ever emitting a dimension itself.

This came out of a design review: splitting the solver into `solve_program()`/`validate()`/`render_svg()`, encoding hard rules as CP-SAT constraints, soft preferences as objective terms, and geometry-dependent checks as post-solve validators, plus using CP-SAT's assumption-literal mechanism (`SufficientAssumptionsForInfeasibility`) to turn INFEASIBLE into a rule-conflict explanation. That design holds up — this plan implements it.

**Decisions locked in:**
- New branch (`v2-alpha`) off `master`, same repo — v1 keeps running untouched on `master`/production.
- **Python-only scope.** Eve integration and real per-jurisdiction building-code data are explicit future work, not this plan.
- Jurisdictions: one stub ruleset, `generic-residential`.
- Units: integer inches quantized to a 6" grid (not literal 1"), modeled as "grid-units of 6 inches" — v1's feet-resolution model already has a documented, separately-tracked search-reliability gap at large footprints; full 1" domains would make that worse.
- Rule content: hard rules = min room dimension, hall clear width, setback envelope, closet alignment (plus v1's existing per-entity hard rules, once wired for diagnosis); soft = an adjacency-matrix term as a guideline-covering objective (reusing v1's `alignment_weight` reformulation, not naive pairwise sums); validators = egress opening, door swing conflicts, fixture clearance, furniture fit. **Garage separation is dropped from this pass** (see sign-off #3/#4 below) — `generator.py` doesn't produce a Garage room at all yet, so there's nothing for the rule to constrain; revisit once/if garage generation exists.
- **Three more rules added 2026-09-02** (user review, before implementation started — see "Additional rules" section below for full detail): an optional-entry program variant, a closet-alignment hard rule, and a hallway-door-placement fix to `place_openings()`. The latter two land as originally planned; optional-entry is a `generator.py`/`ProgramSpec` option, not a CP-SAT rule.
- **v1's width slider has since landed**: the shape enum was replaced with a continuous width slider (`WIDTH_ASPECT_MAX`, `WIDTH_MIN_SIDE`, `width_bounds()`, `generate_program(..., width=...)`), committed to `master`, and is now live in production — `metawhalealerts.com`/`www.metawhalealerts.com` were cut over from the old VPS to this same Vercel project on 2026-09-02 (VPS left running, untouched, as a rollback fallback). v2-alpha builds on this: the new `ProgramSpec` takes a `width`, not a shape choice.

## Additional rules (added 2026-09-02, user review before implementation started)

Three rules came out of a plan review, after the original "Decisions locked in" above. Closet alignment is folded into "Net-new hard rules" below (it's genuine `rules.py` content). The other two aren't CP-SAT rules at all — noted here since they don't fit neatly under `rules.py`:

- **Optional entry**: today `generator.py` always includes a separate `Entry` room in both styles (`STYLES["traditional"|"open_concept"]["pcts"]["Entry"]`). New `ProgramSpec` field `has_entry: bool = True`; when `False`, `Entry`'s `pcts`/`floors` allocation is dropped from the style mix entirely, and the daylight/door-access requirements that would've attached to `Entry` move to whichever room the style treats as the arrival space (`Living` for `traditional`, `Great` for `open_concept`) instead — the front door opens directly into it. This is a program-generation choice, not a solvability constraint, so it lands in `generator.py` (modified in place, same treatment as `layout.py` — see Architecture below) and threads through `orchestrate.ProgramSpec` alongside `width`. Natural home: Phase 2, alongside `ProgramSpec`'s definition.
- **Hallway door placement**: `layout.place_openings()` currently draws exactly one door per `Adj`, uniformly — including e.g. `Adj("Living", "Hall")`. The correct rule: a door belongs where a hallway meets a **private** room (bedroom, bath), not where it meets a **public** room (Living/Great) — the latter is an open threshold, no door drawn. Fix is a filter in `place_openings()`'s adjacency loop, keyed off `room_kind()`'s existing living/hall/sleep/wet/closet classification (already imported by `app.py` today) — skip door placement when one side is `"hall"` and the other is `"living"`. **Not a CP-SAT rule** — doors aren't solve-time variables, so this is pure post-solve geometry, same category as `circulation_ok()`. **Consequence worth flagging**: `place_openings()` lives in `layout.py`, shared by both v1's live `_run_solve()` and v2's `render_svg()` wrapper. Since `v2-alpha` isn't merging to `master` on any defined timeline (see Branch/workflow below), production keeps drawing the extra hallway-threshold door until v2-alpha ships — decided anyway (folded into v2 rather than cherry-picked into `master` now), noted here so it's not a surprise later. Natural home: Phase 2, alongside the `render_svg()` wrapper.

## Architecture

New modules on `v2-alpha`, kept as leaf-acyclic w.r.t. `layout.py` (today `layout.py` has zero project-internal imports — `generator.py`/`zoning.py` import *from* it, never the reverse; new modules preserve this):

- **`rules.py`** — `RuleSpec`, `Ruleset` (id, hall_clear_width, setbacks, garage_separation, validator params), the `GENERIC_RESIDENTIAL` stub instance, `InfeasibilityDiagnosis` (conflicting_rules, message), `list_jurisdictions()`.
- **`validators.py`** — the four post-solve validators + a `validate()` aggregator, following the existing `layout.place_openings()`/`layout.circulation_ok()` pattern (pure functions on a solved `plan` dict).
- **`fixtures.py`** — new static data (`FIXTURE_CLEARANCE`, `FURNITURE_CATALOG`) that doesn't exist anywhere today.
- **`orchestrate.py`** — `ProgramSpec`/`SolveResult` dataclasses + `solve_program()` (wraps `layout.solve()`/`zoning.solve_zoned()`, no rendering) + a thin `render_svg()` wrapper.
- **`serialize.py`** — `Room`/`Adj`/`Footprint` ↔ JSON, needed for the API.
- **`api_routes.py`** — a Flask Blueprint (`/api/solve`, `/api/validate`, `/api/jurisdictions`), registered from `app.py`, keeping the machine-facing surface separate from the human-form file.

`layout.py` (currently 1109 lines) is **modified in place, not replaced**: the min-room-dimension restructuring, new opt-in `solve()` params, and the adjacency-objective reuse land there; everything else (`validate_program()`, `place_openings()`, `circulation_ok()`, rendering) is untouched.

## `solve_program()` / `validate()` / `render_svg()`

`solve_program()` in `orchestrate.py` mirrors `app.py:_run_solve()`'s solve-only portion (`generate_program()` + `default_proximity()` + the zoned/unzoned branch calling `layout.solve()`/`zoning.solve_zoned()`), minus `place_openings`/`to_svg`/`circulation_ok`.

**Return-shape decision:** `layout.solve()`'s existing 5-tuple return is unpacked positionally at 5+ call sites (`app.py`, `zoning.py` twice, every `test_*.py`). Rather than widen it to 6 and touch every call site, `solve()` gains two new **opt-in out-parameters**: `diagnose_infeasibility: bool = False`, `diagnosis_out: Optional[list] = None`. When requested and the solve comes back INFEASIBLE, `solve()` appends an `InfeasibilityDiagnosis` to the caller-provided list. Existing callers never pass these — **zero existing call sites need to change**. `orchestrate.solve_program()` passes a fresh `[]` and reads it back after. `zoning.solve_zoned()`'s 4-tuple return also stays unchanged; **zoned-path infeasibility diagnosis is explicitly out of scope for this pass** (zoning's anchor/retry logic already has its own, different failure-attribution story — don't conflate the two).

`validate(plan, fp, rooms, adjacencies, ruleset)` in `validators.py` returns `{circulation_ok, unreachable, findings}`; it calls the existing `layout.circulation_ok()`/`layout.place_openings()` plus the four new checks, and is directly callable by `/api/validate` without solving first.

`render_svg()` is a thin `orchestrate.py` wrapper (`place_openings` + `to_svg`) for API symmetry — no new rendering logic.

New API code sequences **solve → validate → render** (correcting v1's current render-before-circulation_ok order in `_run_solve()`, which stays untouched since it's the live browser path — not worth the risk of touching it here).

**Housekeeping:** rename `test_production_shapes.py`'s local `solve_program` helper (a same-named, unrelated function) to avoid grep-confusion with the new `orchestrate.solve_program`.

## Assumption-literal infeasibility diagnosis

New `solve()` params: `ruleset: Optional[Ruleset] = None`, `diagnose_infeasibility`, `diagnosis_out` (all opt-in, default = today's exact behavior). Stable rule-id scheme: `"{kind}:{entity}"`.

**Already per-entity in `solve()`, low restructuring cost** — wrap each existing `add_bool_or(...)` in `.only_enforce_if(lit)`, register via `add_assumption(lit)`:
- Adjacency (`rule_literals["adjacency:{a}:{b}"]`)
- Exterior daylight (`"daylight:{room}"`)
- Door-access/hallway (`"door_access:{room}"`)

**Min room dimension — needs restructuring, and it's embedded in *two* places, not one:** `w[pk]`/`h[pk]`'s variable domains AND `area[pk]`'s domain floor (`r.min_dim ** 2`). Widen all three to `(1, W)`/`(1, H)`/`(1, hi)`, then add explicit `m.add(w[pk] >= r.min_dim).only_enforce_if(lit)` (and same for `h[pk]`) — area's own floor follows algebraically through the existing `add_multiplication_equality`, so it doesn't need its own literal.

**Net-new hard rules** (gated on `ruleset is not None`, in `rules.py`):
- **Hall clear width**: reuses the widened w/h domains from the min-dim fix; per hallway-room part, `m.add(w[pk] >= ruleset.hall_clear_width).only_enforce_if(lit)`.
- **Setback envelope**: `(room, edge, distance)` triples — per-edge inequality on `x1`/`x2`/`y1`/`y2`. **Open design point**: modeled as an explicit per-room/per-edge list (e.g. "Garage sets back from its front edge"), not a uniform inward margin — a uniform margin would conflict with the existing daylight rule's "must touch the boundary" semantics.
- **Closet alignment** (added 2026-09-02, user review): a bedroom's closet should (a) match its parent bedroom's own width, and (b) sit on the wall opposite the bedroom's door. Both are real CP-SAT constraints, no post-solve validator needed:
  - *Width match*: `add_closets()` already glues the closet to its bedroom as a separate part sharing an edge (see `layout.py`'s multi-part-room machinery) — add `m.add(w[closet_pk] == w[bedroom_pk]).only_enforce_if(lit)` (or the `h` pair, whichever axis the shared glue-edge runs along), rule id `"closet_align_width:{bedroom}"`.
  - *Away from the door*: door position itself isn't a solve-time variable (`place_openings()` runs post-solve — see the hallway-door item below), so "the door entry wall" is modeled as **the wall shared with the bedroom's own Hall adjacency** — the wall a door will actually get drawn on. Constrain the closet's glue-edge side to be a *different* side than the Hall-shared side, using the existing shared-wall-side (`_touch_cases`-style) literals. Rule id `"closet_align_position:{bedroom}"`. Needs the bedroom to actually have a Hall adjacency to anchor against — for a bedroom with no Hall adjacency (shouldn't happen given `HALLWAYS`/door-access wiring, but worth an assertion) this rule has nothing to compare against and should no-op rather than error.
- ~~**Garage separation**~~ — **dropped from this pass** (sign-off #3/#4, decided before implementation started): `generator.py` has no Garage room at all today, so there's no real program to constrain yet. The exact-partition-model problem (every square inch belongs to some room, so a true minimum *gap* is unrepresentable without buffer rooms) is real and would still apply whenever this is picked back up — a non-adjacency constraint (`_touch_cases` literals forced false) rather than true distance, same disclosed limit as before, just not built now against a nonexistent room type.

**Deliberately scoped OUT of diagnosis** (stay real hard constraints, just outside the mechanism): `add_no_overlap_2d` and the exact-partition constraint — both are single global constraints across all rooms, and in practice are essentially never the actual cause of infeasibility once `validate_program()`'s pre-solve area check has passed.

**Extraction:** on INFEASIBLE, always re-solve once more with `num_workers=1` against the same model/assumptions specifically to call `sufficient_assumptions_for_infeasibility()` — cheap insurance paid only on the failure path (no confirmed single-worker requirement was found in OR-Tools docs, and an empirical toy-model test worked fine at `num_workers=8`, but this is untested at production complexity). Map the returned literal indices back to rule ids via a `{lit.index: rule_id}` dict built at model-construction time. **The returned core is *sufficient*, not *minimal*** (confirmed empirically — a 2-literal conflicting toy model returned only 1 literal back) — shrinking it (drop-one-and-retest) is real future work, explicitly deferred here since each shrink attempt costs a full re-solve against Vercel's time ceiling.

## Adjacency-matrix soft objective

Reuses `layout.py`'s existing `_guideline_usage()` (the function `alignment_weight` already uses) and its reformulation rationale, applied per preferred-adjacency pair instead of globally: new `AdjPref(a, b)` dataclass (parallel to `Proximity`), new `adjacency_weight: int = 0` param. For each pair, call `_guideline_usage` restricted to just that pair's own x/y edges — minimizing distinct coordinate lines used is minimized precisely when the two rooms' walls coincide, i.e. when they touch. Additive alongside the existing `Proximity`/`proximity_weight` mechanism, not a replacement.

## Post-solve validators

All four in `validators.py`, pure functions on the solved `plan` dict (the `place_openings()`/`circulation_ok()` pattern):
- **Egress**: checks window width at bedroom exterior openings ≥ a minimum — **width-only by necessity**, since this data model has no sill-height or net-clear-area data.
- **Door swing conflicts**: door-vs-door swing-arc overlap and swing-vs-own-room-boundary, computable from `place_openings()`'s existing output. Door-vs-furniture swing is deferred (needs the furniture data below).
- **Fixture clearance**: needs a new fixture catalog (`fixtures.py`) — necessarily coarse (room dimensions vs. required clearance envelope, not real fixture placement, since individual fixtures aren't CP-SAT variables).
- **Furniture fit**: same category, needs a new furniture-dimensions catalog, same coarse area/min-dim check rather than true 2D placement.

Both data catalogs are new content, not just code — real data-modeling work, not a one-line addition.

## Units migration (feet → 6"-grid-units)

Rule: **linear × 2, area × 4**, dimensionless (percentages, aspect ratios, time) unchanged. Boundary: `generator.make_footprint()` still takes `total_area` in **public-facing square feet** and converts internally as its first step; everything inside `layout.py` is unit-agnostic already, same as today.

| File | Item | Feet/sqft | Grid-units |
|---|---|---|---|
| generator.py | `ROOM_SPECS[*].min_dim` | 5,10,8,8,14,3,5,11,5 | ×2 |
| generator.py | `STYLES[*].floors` | 30,140,90,70,30,30,260 | ×4 |
| generator.py | `CLOSET_AREA` | 20 | 80 |
| generator.py | `PRIMARY_FLOOR`/`BED_FLOOR`/`BATH_FLOOR` | 110,75,35 | 440,300,140 |
| generator.py | inline `min_dim=9`/`5` (secondary bed/bath) | 9,5 | 18,10 |
| generator.py | `make_footprint()`'s `max(round(...),15)` floor | 15 | 30 |
| generator.py | `WIDTH_MIN_SIDE` (concurrent width-slider work) | 20 | 40 |
| generator.py | `WIDTH_ASPECT_MAX` | — | unchanged (dimensionless ratio) |
| generator.py | `MIN_AREA`/`MAX_AREA` | 400/10000 | **unchanged** — public sqft bound, not internal |
| layout.py | `Room.min_dim` default | 8 | 16 |
| layout.py | `add_closets()` `area`/`min_dim`/`min_shared` | 20,3,2 | 80,6,4 |
| layout.py | `_min_dim_floor()` | 4,2,5 | 8,4,10 |
| layout.py | `Adj.min_shared` default | 3 | 6 |
| layout.py | `place_openings()` `door_width`/`window_width` | 3.0,4.0 | 6.0,8.0 |

Rendering fixes needed (`layout.to_svg()`): halve `scale=14` to ~7 (px-per-grid-unit); the grid-paper loop needs every-2-units for the light line and every-10 for heavy (was every-1/every-5); `_exterior_dims()`/room-label dimension strings need a feet-and-inches formatter (`divmod(units, 2)` → whole feet + 0-or-6 inches) instead of assuming whole feet; `_scale_bar()`'s `10 * scale` needs to become `20 * scale` (or its label changed) to stay physically accurate. `zoning.py` needs no change (operates on `Footprint`/`Room` generically; its own constants are time-based, not spatial).

**Validation approach**: do this in complete isolation as Phase 1 (before any feature work), and check it structurally — take `test_house.py`'s hand-built program, produce a ×2/×4 converted version, solve both, and assert identical topology with coordinates exactly ×2. A structural diff catches a missed literal far more reliably than eyeballing the conversion.

## API surface

No collision with v1's existing `/` or `/solve` (browser-fetch, HTML-in-JSON) routes; `vercel.json`'s catch-all rewrite already covers any new path.

- **`POST /api/solve`** — `{total_area, beds, baths, width, has_entry, style, ruleset, time_limit, diagnose_infeasibility}` in (width replaces shape as the primary footprint control, per v1's width-slider work; `has_entry` defaults `true`, matching today's always-Entry behavior). Success: `{ok, status, plan, footprint, objective_value, best_objective_bound, wall_time, zoned, program}` — `program` is the serialized spec, included so `/api/validate` can be called independently without the caller reconstructing it. Infeasible: `{ok: false, status, diagnosis: {conflicting_rules, message}}`.
- **`POST /api/validate`** — `{plan, footprint, rooms, adjacencies, ruleset}` in → `{ok, circulation_ok, unreachable, findings}` out.
- **`GET /api/jurisdictions`** — `{jurisdictions: [{id: "generic-residential", name: "Generic Residential (stub)"}]}`.

## Phased sequencing (each independently testable, plain `assert`/`print` style matching existing `test_*.py`)

1. **Units migration** — generator.py + layout.py defaults + the 4 rendering fixes. Structural-diff test against pre-migration output.
2. **Three-stage split, v1 rule content unchanged** — `orchestrate.py`, `validators.py` skeleton (existing checks only). Rename the `test_production_shapes.py` helper here. New `test_orchestrate.py`. Also carries the two non-CP-SAT rules from the 2026-09-02 review: `ProgramSpec.has_entry` in `generator.py`/`orchestrate.py`, and the hallway-door-placement filter in `place_openings()`.
3. **Assumption-literal wiring on existing per-entity rules + min-dim restructuring** — `diagnose_infeasibility`/`diagnosis_out`, single-worker retry, rule-id mapping. New `test_diagnosis.py` with a deliberately-conflicting hand-built program.
4. **Three new hard rules** (hall clear width, setback envelope, closet alignment — garage separation dropped, see sign-off #3/#4) in the `generic-residential` stub. New `test_hard_rules.py` (one infeasible + one feasible case per rule).
5. **Four post-solve validators** + `fixtures.py`. New `test_validators.py`, reusing `test_house.py`'s program.
6. **Adjacency-matrix soft objective**. New `test_adjacency_objective.py` (weight=0 no-op + weight>0 measurable effect).
7. **JSON API routes** — `serialize.py`, `api_routes.py`. New `test_api.py` via Flask's test client, including a full INFEASIBLE→diagnosis round-trip through JSON.

Validators (5) are independent of diagnosis/hard-rules (3/4) and could be built in parallel if useful; API (7) depends on everything else existing.

## Branch/workflow

`git checkout -b v2-alpha` off `master`. Commit as normal; push to `origin/v2-alpha` for backup/review whenever useful. **Do not merge to `master`** — this plan doesn't touch the live Vercel deploy, and merge/deploy strategy for v2-alpha is explicitly undecided (not assumed here). If Vercel auto-creates preview deployments per branch, pushing will trigger a harmless preview build at an auto-generated URL, not `metawhalealerts.com`.

## Open design choices flagged for sign-off (not silently picked where genuinely close)

Signed off 2026-09-02, before implementation started. Items 1-2 and 5-7 accepted as recommended (no discussion needed); 3-4 changed from the original recommendation after review:

1. Diagnosis via out-parameter vs. 6-tuple + updating all call sites — **accepted: out-parameter.**
2. Setback as per-room/edge list vs. uniform margin — **accepted: per-room/edge** (uniform conflicts with the daylight rule).
3. Garage separation as "no shared wall" vs. true minimum distance — **superseded: dropped from this pass entirely**, not just weakened to no-shared-wall. `generator.py` doesn't produce a Garage room at all yet, so neither semantics has a real program to apply to. Revisit both this choice and garage generation together, whenever garage support is actually added.
4. Should `generator.py` gain a Garage-producing option now — **decided: no** — consistent with #3, garage support (generation *and* the separation rule) is deferred as a unit, not split across passes.
5. Minimal-core shrinking — **accepted: defer** as documented follow-up.
6. `AdjPref` global-weight-only vs. per-pair weight — **accepted: global** (parity with `Proximity`).
7. New routes as a Blueprint (`api_routes.py`) vs. directly in `app.py` — **accepted: Blueprint.**

## Verification (when implementation starts)

- Run the full existing `test_*.py` suite (`.venv/bin/python3 test_X.py` for each) after Phase 1 to confirm the units migration didn't silently break v1's own hand-built test programs, then after each subsequent phase for its own new test file.
- Phase 3's `test_diagnosis.py` is the key correctness gate: build a hand-crafted program where two named hard rules provably conflict (e.g. a hall too narrow to satisfy both `hall_clear_width` and a `min_dim` floor in a too-small footprint), confirm `status == INFEASIBLE` and `diagnosis.conflicting_rules` names exactly the expected rule ids.
- Phase 7's `test_api.py`: exercise `/api/solve` (both success and INFEASIBLE-with-diagnosis), `/api/validate` called independently using a prior `/api/solve` response's echoed `program`, and `/api/jurisdictions`, all via Flask's test client — no live server needed.
- Before pushing `v2-alpha`, confirm whether Vercel is configured for per-branch preview deployments (harmless either way, but good to know).
