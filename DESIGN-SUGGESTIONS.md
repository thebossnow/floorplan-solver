# Make MetaWhaleAlerts / Floor Plan Solver feel like a real drawing

This is a **design recommendation**, not an implementation plan. The live site at [metawhalealerts.com](https://www.metawhalealerts.com/) is the Flask floor-plan solver in `~/workspace/floorplan-solver`. It works. It also currently looks like an internal demo: a cream form, a 22px system-font title, and a wireframe of white boxes.

The product is a **generated architectural drawing**. The page should look like the drawing, not like a generic SaaS form.

---

## What is wrong today

I generated a 1,500 sf / 3-bed / 2-bath plan on the live site. The layout is a valid, gap-free house. The presentation is what makes it feel cheap.

| Surface | Current | Why it feels boring |
| --- | --- | --- |
| First screen | "Floor Plan Solver" + CP-SAT subtitle + 4-column form | Reads as a lab tool, not a house |
| Type | `-apple-system, Helvetica, Arial` | No character, no architectural voice |
| Color | `#fbfaf7` paper, white rooms, `#222` strokes | Cream + black boxes is the default CAD dump |
| Plan drawing | White rects, Helvetica labels, no color, no scale, no north, no title block | The product itself looks unfinished |
| Doors / windows | Tiny cream or blue gaps (local `to_svg` already has these; live SVG I got was still mostly blank boxes) | Openings do not read as openings |
| Wait | Full-page POST, ~25 seconds, no progress | Feels broken, then dumps a result |
| Stats | `Status: FEASIBLE`, `Solve time: 25.0s`, `Circulation: OK` | Engineer jargon, not homeowner language |
| Room table | Raw schedule, no link to the drawing | Two artifacts instead of one |
| Brand | Domain is `metawhalealerts.com`; product is Floor Plan Solver | Name and page do not match |
| Mobile | 4-column form on a 780px column | Cramped on phones |

The live page is also slightly behind local source: local `templates/index.html` already has a **Room style** select (`traditional` / `open_concept`) that production does not show.

Do **not** paper over this with purple gradients, blob backgrounds, or a marketing hero above the tool. The drawing is the hero.

---

## Recommended direction: presentation sheet on vellum

**Purpose.** Let someone enter a foundation and get a drawing they would actually show a contractor or spouse.

**Audience.** Homeowners, builders, and agents scanning rooms, sizes, and flow — not OR-Tools users.

**Tone.** Drafting-room, precise, a little warm. Like a plotted sheet pinned to a wall, not a dashboard.

**One memorable thing.** The generated SVG should look like a **titled architectural sheet**: colored rooms by use, dimension ticks on the exterior, a north arrow, a scale bar, and a title block in the corner with project name, area, beds/baths, and date.

Avoid the three AI-default looks: cream + terracotta serif, black + neon, and newspaper grid. The current site is already the cream default. Avoid the navy-cyan "blueprint" cliché too — every architecture toy does that.

### Palette (named tokens)

- `Sheet` `#EDE6D6` — slightly warm vellum, not the current near-white cream
- `Ink` `#1C1915` — graphite, not pure black
- `Grid` `#D9D0BE` — faint 1-foot graph under the plan
- `Living` `#E4C9A0` — oak / sunlit public rooms
- `Sleep` `#C9D4C2` — linen bedrooms
- `Wet` `#B7C9C8` — tile baths, kitchen, utility
- `Accent` `#9A3B2F` — iron-oxide stamp for the generate button, north arrow, and title-block rule (one loud color, used sparingly)

### Type

- Display / title block: **IBM Plex Serif** (or Fraunces if you want more bite) — used only for the product name and sheet title
- UI and room labels: **IBM Plex Sans**
- Dimensions, areas, schedule numbers: **IBM Plex Mono**, `font-variant-numeric: tabular-nums`

That pairing is architectural without looking like a resume template.

### Layout

Desktop is a **sheet, not a blog post**:

```
┌─────────────┬──────────────────────────────────────────┐
│ SPEC        │  DRAWING                                 │
│ Area slider │  [graph paper]                           │
│ Beds  ooo   │     ┌─────────────────────────────┐      │
│ Baths oo    │     │  colored rooms + dims       │      │
│ Shape [▭][■]│     │                             │      │
│ Style chips │     └─────────────────────────────┘      │
│ [ Generate ]│  title block · north · scale             │
│ Room legend │  hover a room ↔ highlight schedule row   │
└─────────────┴──────────────────────────────────────────┘
```

Mobile stacks the drawing first (that is the product), then the spec form, then the schedule.

Shape should be **two plan thumbnails**, not a dropdown. Style should be chips: Traditional / Open concept.

---

## Highest-impact changes (do these first)

These are the things that would make the current page stop feeling ugly, even before a full restyle.

### 1. Make the drawing look like a drawing

Change `to_svg()` in `layout.py`. This is the product.

- Fill rooms by type: living/dining/kitchen public, bedrooms sleep, baths/utility wet, closets a lighter hatch
- Keep walls as ink; drop the heavy 6px outer box or turn it into a true exterior wall
- Draw doors as a swing arc + opening, windows as a double line in the wall (you already compute `place_openings()`)
- Exterior dimension string on two sides (overall width/depth, plus a few room spans)
- North arrow and a 10-ft scale bar
- Title block: project title, footprint, beds/baths, style, date
- Room labels in a small all-caps architectural style (`PRIMARY`, `12'-0" × 15'-0"`, `210 SF`) instead of `PrimBath` / `PrimaryCloset`
- Faint graph-paper grid at 1 ft, stronger every 5 ft

A colored, dimensioned sheet will do more for "attractive" than any CSS on the form.

### 2. Stop dumping solver internals

Replace the stats strip with human copy:

- `46 × 33 ft · 1,518 sf · 3 bed / 2 bath`
- `Every room reachable from the entry`
- Hide `FEASIBLE`, `zoned`, and solve time behind a small "How this was built" disclosure

The schedule table stays, but:

- Group public / private / service
- Color-swatch next to each room name
- Click/hover syncs with the SVG
- Delta as a quiet `+6 sf` in mono, red only when it is a real miss

### 3. Kill the 25-second blank wait

The POST currently sits on a spinner-less form for up to 25s. That is the second-biggest "this site is broken" feeling.

Minimum: disable the button, show a drafting-progress state ("searching layouts…"), keep the previous plan on screen if any.

Better: generate with `fetch()`, stream or poll, so the page never unloads.

### 4. Give the empty state something to look at

On first visit there is no drawing. Show a **sample sheet** (the default 1500/3/2 solve, cached as static SVG) with a caption: "Example · 1,500 sf ranch". People need to see the product before they wait 25 seconds.

Add 3 preset chips: `1,200 sf square`, `1,500 sf ranch`, `2,000 sf open concept`.

### 5. Spec panel instead of a cramped grid

- Area as a labeled slider with the number (`1,500 sf`) in mono
- Beds/baths as large tap targets, not native `<select>`
- Shape as two mini footprints
- One primary button in `Accent`, full width of the spec column
- Copy: "Draw this house" not "Generate floor plan"
- Subtitle: "Give it a foundation. It tiles every room to the inch." Drop "OR-Tools CP-SAT" from the first screen

### 6. Name and domain

`metawhalealerts.com` does not match "Floor Plan Solver". That mismatch makes the page feel unfinished even if the UI is pretty.

Pick one:

- Keep the domain, rebrand the product to something that can live on it (weak fit)
- Keep Floor Plan Solver, point a real name at it (`floorplansolver.com` or similar)
- Or a short drawing-studio name: **Sheet**, **Vellum**, **Plotted**, **Footprint**

Until the title, `<title>`, and domain agree, polish will only go so far.

---

## Polish that makes it feel finished

Once the sheet and layout are right, these details (from ordinary UI craft, not decoration) compound:

- Concentric radii on the spec card vs inputs
- 44px hit areas on bed/bath steppers
- `text-wrap: balance` on the title; tabular nums on every sf figure
- Button press `scale(0.98)`, 150ms on color/transform only — no `transition: all`
- Keyboard focus rings in `Ink`, not browser default
- SVG download button ("Download sheet") and a copy-link for the current inputs
- Empty/error states as instructions: "Try 1,800 sf or drop to 2 bedrooms" — you already have a decent error string
- Respect `prefers-reduced-motion`

---

## What not to do

- Do not put a marketing landing page in front of the tool
- Do not restyle only the form and leave white-box SVG
- Do not go full navy blueprint unless you really want the cliché
- Do not add stock photos of living rooms; the generated plan is the image
- Do not expose more solver knobs on the first screen
- Do not animate rooms flying in on every generate — one entrance is enough

---

## Two alternatives if you hate vellum

1. **Chipboard model shop.** Gray board background, rooms as basswood-colored blocks with drop shadows, labels engraved. More object, less document. Good if you want "toy you can hold."
2. **Surveyor's field book.** Bound notebook, pencil lines, rubber-stamp title, no color fills. More character, worse scannability for room types.

I would still ship vellum-plus-fills. Color-by-use is the fastest way for a non-architect to read the plan.

---

## Suggested sequence if you later want this built

1. Restyle `to_svg()` (fills, labels, title block, north, scale, doors/windows)
2. Restyle `templates/index.html` into spec column + drawing (CSS only, same Flask POST)
3. Cached example SVG on GET, loading state on POST
4. Hover sync + presets + download
5. Branding/name pass (`<title>`, wordmark, domain decision)

Steps 1–3 would already change the feel of the live site. Local source already has style selection, wall-thickness inset, merged L-room polygons, and openings — production just does not present them.

No code changes in this pass. If you want, the next step is implementing the vellum sheet (drawing first, then the page chrome).
