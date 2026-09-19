# orch operator UX — re-derived

Fable, 2026-09-14, v6. This replaces five accreted revision passes. The
owner's diagnosis of those passes was correct: each was internally
consistent and the whole was architecturally accreted — patches onto a
structure (attention-first inbox + four-route drill-down) decided before
the facts that now matter existed. This document re-derives the surface
from the operator's jobs, keeps every *decision* the rounds settled, and
discards the *structure* that only inheritance was holding up.

Grounded in: README.md, STATES.md, widget.tpl.html, orch/server.py,
orch/feed.py, orch/core.py (ask/nudge/journal functions read directly),
orch/tick.py, DESIGN.md (tick, six conditions, surfaces, security, cut
list), docs/DECISIONS.md, and the owner's canvas
edits.

Philosophy constraints, unchanged and load-bearing throughout: state is
derived fresh every tick, never stored; no thresholds in the tick;
conclusions belong to whoever holds context — the UI surfaces, it never
judges; `/act` is a closed, argument-validated verb set; one vanilla
HTML/CSS/JS file, no build, no dependencies.

**Visual reference:** eight artboards, four desktop and their four 390px
phone counterparts — `Main.dc.html`/`PhoneMain`, `Repo.dc.html`/`PhoneRepo`,
`Issue.dc.html`/`PhoneIssue`, `Flat.dc.html`/`PhoneFlat`. The in-repo authority is `docs/artboards/orch_Operator_UX.pdf` read with `docs/artboards/README.md`'s page table (page 2 is CUT); the canvas
<https://claude.ai/code/artifact/32d8b13a-5a82-45d2-9e8e-0fff7372fa52>
is provenance only and is not publicly readable (#237).

`Main` and `Flat` are two designs for the same route, `#/` (the machine
page): `Main` nests each repo's issues beneath it, `Flat` lifts a NEEDS YOU
slice of issues to the top and reduces repos below it to counts. §4.1 states
which one this document specifies and §7 records how the other was cut.

**Superseded in part, 2026-09-16:** `docs/RESTRUCTURE-2026-09-16.md` §10 rules that the `agent-ready` axis is collapsed (#346), dashboard-op's automatic wake is deleted (#343), and the `auto-land`/`no-auto-land` label pair is kept with `no-auto-land` winning over the repo default. Until re-derived, do not build from: §1 job 3's issue half, §4.1 "Automerge — the settled model", §4.2 ACTIVE/INACTIVE, §4.3 items 3-4, §5 "Working-set edits", §8 stage 2. Layout (§3, §3.6, §4.1/§4.2 wireframes, WHAT CHANGED) stands.

---

## 1. The operator's jobs

Derived from what the system actually is: an unattended tree the tick
runs. The tick re-enters dead work, wakes the chain, and turns intent
into processes. The operator is not a scheduler and the page is not a
console — the operator *visits*, desk or phone, and their questions come
in a fixed order:

1. **"What happened since I last looked?"** — a *digest*, not an alarm:
   what landed, what moved, what stopped and why. Every visit, first
   thing. The journals answer the "why" (the escalation rule guarantees a
   written reason at every stop); WHAT CHANGED answers the "what."
2. **"Is anything waiting on me?"** — the small subset a human must act
   on: a green PR with automerge off, an abandoned issue, a wedged
   session, contention. A *sort and a mark* within the digest — not a
   separate surface, because on any given visit this set is usually
   empty, and a dedicated inbox for a usually-empty set is chrome.
3. **"Adjust what the machine works on."** — membership: track/untrack
   repos, activate/deactivate issues, automerge. A primary, every-visit
   job — it belongs on the rows themselves, where the things are, not in
   a settings area.
4. **"Dig into one thing / steer it."** — read one story (journal), tail
   or log a session, kill a wedged one, leave a note for the next wake.

The old structure put job 2 first (triage inbox as the landing view).
That was v1's call, made before two facts existed: the journal is the
event log (job 1's "why" already written, per level, at every stop), and
WHAT CHANGED renders job 1's "what" better than anomaly cards render
anything. Jobs 1–3 happen at the same glance, every visit; job 2 is job
1's top slice. **Attention-first was wrong as a page; it survives as a
sort order.** The six-conditions philosophy ("the conditions ARE the
report") is intact — the report is rows in the story of what happened,
loud and sorted first, not a modal inbox in front of it.

---

## 2. The surface: three pages

Everything in this system renders as one of a small family of shapes
(§3) — entities with states, a time-ordered log, label→value pairs,
headers, dividers. That observation collapses the *view* structure (it
does not mean one grid: §3 corrects v6's over-claim there). Two altitudes exist in the data (machine → repo →
issue), so:

    #/                the machine page (landing)
    #/<slug>          one repo
    #/<slug>/<n>      one issue

Three hash routes — kept not by inheritance but because they earn it in
~10 lines: deep-linking (a row anywhere can point at the exact issue
page), reload-keeps-place, free back/forward. Navigation chrome is a
back-link plus title; at two clicks of maximum depth a breadcrumb system
is decoration.

**Discarded from my own previous passes, by name:**

- **The triage inbox as landing view and `#/` route of its own** — the
  needs-you set becomes the top section of the machine page. Inherited
  from v1's spine; does not survive the digest facts.
- **The triage badge** (notification-bell convention) — the top of the
  landing page IS the needs-you list; a counter pointing at the thing you
  are already looking at is the inbox metaphor's ghost.
- **Cards as a component** — an anomaly is a row whose status cell is
  loud. The card taxonomy survives intact as row vocabulary (§4.1); the
  bespoke card layout dies into the entity-row shape (§3.1).
- **The four-route model and the breadcrumb system** — three routes, a
  back link.
- **"Nothing else gets first-class pixels"** (v1's spine sentence) — the
  operator's first question is *what happened*; attention is a mark and a
  sort within that answer.

**Read-only / remote, carried forward:** two tiers set by the existing
probe, no new machinery. `/act` present → everything renders; the verb
set is intent-shaped by construction (§5), so "the phone edits the
working set, the tick launches" holds without gating. `/act` absent (the
baked snapshot — physically cannot POST) → pure viewer; the verb is the
link, and the links are the steering that still works (issue thread, PR).

**Small screen, carried forward as a first-class constraint:** every page
holds at 390px under per-shape fold rules defined once with each shape
(§3). Not a mobile design — the same page, honestly narrow.

---

## 3. The shapes — derived from the content, not from repos

**Correction on the record: v6's "one table contract" was a repo row
generalized into a universal one.** It fit repos and issues and nothing
else; journal entries, WHAT CHANGED groups, the issue page's state block,
section headers, and gap markers all had to be forced through one grid —
which is exactly what broke three times at phone width. The fix is not
better CSS, it is admitting the content has more than one structure.

Inventory of everything the three pages display, grouped by actual
structure, yields **four shapes plus one piece of furniture**:

### 3.1 Entity row — a thing with a state you can act on

    [mark] [identity] [status/what ……] [age] [verbs 0–2]

Instances: tracked/available repos, active/inactive issues, needs-you
rows (an issue whose status cell is a condition and whose mark is loud),
sessions, other-sessions, WHAT CHANGED rows (the lightest variant:
identity + what-happened + age; no mark, no verbs — the outcome group
supplies the status, see Section header). Declared variance, not
per-view exceptions: mark optional, status optional, verbs 0–2.

**The mark cell is the status dot and the automerge toggle, at once.**
Colour is status — red needs you, amber waiting, grey idle, green
healthy. A rounded-square outline drawn around the dot means automerge
is on; tapping the mark flips it. One element carries both facts, so
there is no separate automerge control competing for row width. Visible
on the artboard (page 1): the dots beside `orch`, `#42`, `#7` and `#9`
carry the outline; `ops-console` and its `#42` do not. The artboard also draws a rival —
`.st`/`.st.auto`, a bordered box around the status word instead of an
outline on the dot. orch#163 chose the outline-on-the-dot and #282
deliberately declined to ship the box: a 1px border on `.st` would
reflow the flex row, and two mechanisms recording one fact is the
exact duplication this codebase already fought once. The box is a
rejected rival, on the record, not an open question.

**The verb slot is glyphs, not words.** `❙❙` (pause) for
untrack/deactivate, `▶` (play) for track/activate, plus icon-only
buttons for tick, nudge, log, regenerate — visible on the artboard
(pages 1 and 3). Controls are 24px (§3.6). What the pause/play pair
*binds to* — untrack vs. deactivate, per instance — is an open question
the operator has explicitly deferred to orch#257; this document fixes
the glyph vocabulary and the 24px box, not the binding.

**The state chip is a control: tapping it expands the row in place.**
An issue row's status chip, tapped, opens that issue's summary inline,
directly beneath the row, with the actions that resolve it — check,
act, tap out, move to the next row, without navigating away. This is
the loop the design is for. Visible on the artboard (page 3): under
`#42 fix tick drift`, an expanded body reads `dead mid-flight — 3
commits, no PR. tick re-enters ~13m.` with an action line `log tail
kill issue ↗` beneath it.

**One line is the default at every width; the fold is a last resort,
not a rule.** An earlier version folded to a fixed three-line shape
below 390px by default, and that default is what destroyed the scan —
folding is now reserved for content that genuinely cannot fit on one
line at the width available, never applied on principle. Where a fold
is unavoidable its shape is unchanged from before: line 1 = mark +
identity + age (age stays top-right — the scan column survives the
fold); line 2 = status/what, wrapping freely; line 3, only when verbs
exist = verbs right-aligned at the 24px control box (§3.6). Most rows,
at most widths, stay on one line.

### 3.2 Log row — a time-ordered record

    [time] [actor] [message ……]

Instances: journal entries, and nothing else. **Time is the identity**,
so it leads — a log reads time-first; v6 putting age last was the repo
row showing through. No mark, no verbs, no state. The **gap marker**
(`—— 4d 2h gap — no ticks recorded ——`) belongs to this shape as its
divider: full-width, no cells, cannot break.

**390px fold:** line 1 = time + actor; message wraps below at full
width. Nothing else changes.

### 3.3 Definition pair — a label naming a value

    [label] [value ……]

Instances: the issue page's state block (branch, commits, idle, PR,
labels, automerge, issue-orch + re-entry countdown) — a definition list,
not a list of entities; rendering it as rows made "a form pretending to
be a table." Values may carry a link or one inline control (the
automerge toggle, greyed when the repo overrides).

**390px fold: none.** The label column is short and fixed; values wrap
in place. A value's control wraps to its own line if it must. This shape
is fold-free by construction — the media query does not touch it.

### 3.4 Section header — a title governing rows below

    [title (count)] [controls: chips · + add · load earlier]

Instances: TRACKED, AVAILABLE, ACTIVE, INACTIVE, JOURNAL,
WHAT CHANGED (its outcome sub-headings — Landed / Moved / Not started —
are this shape at reduced weight; the group is the structure, so it gets
a real header, not a label jammed into an identity cell), SESSIONS.
Headers own the controls that govern their rows: filter chips, `+ add
path` / `+ add URL` / `+ add issue`, `load earlier` / `load all`.

**390px fold:** controls wrap to a second line under the title; chips
wrap, never scroll horizontally.

### 3.5 Furniture — the full-width line

One-cell lines with no grid at all: quiet-state lines ("✓ nothing needs
you"), the RECENTLY DONE struck aside, the resume-command code line, the
steering link, the footer status line, empty-state hints. Cannot break
at any width because there is nothing to align.

### 3.6 The grid — one unit, fixed rows, aligned columns

This is the part most likely to be lost in implementation, because it is
arithmetic rather than a component, and it is what stopped the
alignment drift that broke the layout three times. It is settled, not a
default: PR #282 (commit 4936819) transcribed these values from the
artboard, and they are not open for re-derivation.

**One 4px base unit.** Every spacing value in the stylesheet is a
multiple of it — padding, gaps, indents, all of it. The tokens live on
`#orch`, not `:root`: every rule in this file is `#orch`-scoped, and
putting the base unit anywhere else would break that discipline for the
sake of one variable.

**Rows are exactly 32px. Fixed, not a minimum.** A verb appearing or
disappearing on a row must not reflow the rows around it — that is the
artboard's own stated reason for fixing the height rather than letting
content set it. The same reasoning fixes controls at exactly 24px.

The 32px is a real trap, recorded because #282 hit it: 7px of padding
above and below a 20px line-height renders 34px, not 32 — a 2px error
per row that is invisible on one row and visibly off-rhythm down a long
list. At the 20px line-height the row uses, 6px above and below lands it on 32 exactly. The 32px is the constraint; the padding is derived from it — (32 − line-height) / 2 — and must be re-derived if the line-height ever changes. It is not a rounding choice a later tidy-up can "simplify."

**Age gets tabular numerals and a min-width**, so the column cannot
jitter sideways as its digits change between visits — `47m` and `9m`
occupy the same width.

**Five-column grid**, per entity row: mark, identity, status/what, age,
verbs.

**The governing rule for nesting: indent only the identity cell.**
Status, age and verb columns keep their tracks regardless of nesting
depth. Padding the whole row — the bug #282 fixed — pushed age and
verbs out of their columns on every nested row, because the indent
compounded across every cell instead of one. In the shipped template
the indent lands on `.er-id` alone; nothing else moves.

### What is genuinely shared

The type scale (13px base, up to 14px under 390px — text scales up on
narrow, never down); the mark vocabulary (state pills, condition levels,
liveness dots — one visual system across all shapes); the verb slot
(same button style, 24px, max two per row, §3.6); vertical rhythm and
section spacing; the principle *wrap, never truncate* — rows still wrap
rather than truncate when they must break, even though one line is now
the default rather than folding (§3.1). **Not shared: the grid.** Each
shape owns its own layout and its own fold rule, stated above. One
universal media query was the wrong abstraction; four small ones that
each match their shape's structure are the fix — and the definition pair
needs none at all.

**Page width is shared too: `max-width: 880px` on `#orch`, left-aligned
(orch#186).** The page was fluid from 390px to whatever the monitor is;
on a wide screen the rows stretched the full viewport. A cap belongs at
the container, once, not per shape. The artboards are all drawn at
560px, and page 1 (`Main.dc.html`) is confirmed current per
docs/artboards/README.md, but 560px was not taken as the answer: the
artboards carry three issues per repo and a live repo carries more. The
binding constraint is the entity row (§3.1) — identity, state pill, age,
and up to two verb buttons on one line — which already folds at 390px
because it runs out of horizontal room; capping at 560px would push
every screen toward that crowded regime, making the fix a regression for
the densest content. 880px is the top of the 640-880px range: it caps
the wide-screen stretch while leaving the entity row uncrowded. The
390px folds are unaffected — a max-width binds the upper end only.

One container, one cap: `#orch` wraps the whole app and routing is
client-side hash (`#/`, `#/<slug>`, `#/<slug>/<n>`), so all three pages
share it. The issue page does not want a narrower bound of its own — its
definition pairs (§3.3) are fold-free by construction and no media query
touches them, so an upper limit only bounds them, never fights them.

Left-aligned, not centred: the artboards are fixed-width canvases with
no viewport, so they do not answer this one. This is a polling dashboard
the operator watches continuously; centring would move the content
horizontally on every window resize, and left keeps the page anchored
where the eye already is.

---

## 4. The pages

### 4.1 `#/` — the machine page

    ┌────────────────────────────────────────────────────┐
    │ orch ●  16:04 · tick 2m · 2 orchs · disk 61%     ⟳  │
    ├─ TRACKED 2 ─────────────────────────────────────────┤
    │ (◉) orch 3                    ~/Documents/GitHub/orch│
    │   (◉) #42 fix tick drift      ACTIVE    47m     ❙❙  │
    │   (◉) #7  product table       REVIEW    22m     ❙❙  │
    │   (◉) #9  toml migration      CHECKING  38m     ❙❙  │
    │  ●  ops-console 1          ~/Documents/GitHub/ops-.. │
    │    ●  #42 session died        ACTIVE    47m      ≡  │
    ├─ AVAILABLE 4 ───────────────────────────────────  + ┤
    │    endgame-content                       2d      ▶  │
    │    claude-code-repo                      9d      ▶  │
    ├─ ▸ other sessions 3 ─────────────────────────────────┤
    └────────────────────────────────────────────────────┘

This section specifies `Main`: repos listed with their issues nested
beneath each repo, the operator seeing the work itself at the top level.
`Flat` — the artifact's other machine-page artboard, a NEEDS YOU slice of
individual issues on top with repos below reduced to counts — is not what
this document describes. No head-to-head comparison of the two is on
record; the convergence on `Main`'s shape is incidental, the net effect of
the #150 and #160 verdicts below, each of which settled a narrower
question. See §7 for `Flat`'s disposition.

**Header.** The nav header carries the machine's vitals at rest: a live
dot, the clock, tick age, how many orch sessions are alive, and disk —
`16:04 · tick 2m · 2 orchs · disk 61%` (artboard, page 1) — with a
refresh glyph right-aligned. These facts used to split across a footer
(`dop ● · disk 61%`) and a bare `tick ✓ 2m ago` up top; the artboard
merges them into one line at the top of the page, and the footer is
gone. It shortens at phone width the same way the repo row's directory
column does. The alert strip (§ "Alert placement" below) renders above
or into this same header, but only when a slug-less alert is pending —
at rest the header is only ever these vitals, never an empty strip.

**Hierarchy.** Design the quiet state first — it is what the operator
sees most. At rest: no header strip renders at all (§ "Alert
placement" below), then **TRACKED as the primary section** at full
weight — the working set is the page's subject. AVAILABLE is
secondary: visible (the transfer box needs both lists on screen) but
visually lighter — no marks, dimmed, capped at 5 with "+N more".
Other-sessions is reference: collapsed behind its count. Order = jobs:
my working set → what I could add → the edge cases.

**NEEDS YOU is cut as a section — operator verdict, 2026-09-14, on
issue #150:** "We do not need the needs you section. It is adding
complexity." This settles the open question §4.1 previously left to the
operator, and the answer is recorded here because implementing it is not
the same as recording it (#145). The old reason for a top slice was "the
needs-you set is the machine page's top slice" — a counter naming what
elsewhere needs attention. The reason the operator's cut is right: the
machine page already shows the work itself, each
repo with its issues nested under it, so a counter pointing at that same
work adds a layer without adding a fact. An operator scanning TRACKED
already sees every issue and its state chip; a second list restating
"these need you" is derived from cells the operator is already reading.

Per-issue attention now lives on the issue row itself, in its state chip
(ACTIVE / REVIEW / CHECKING) and its escalation, both nested under the
repo that owns them (see "Alert placement" and the TRACKED entry below).
The row vocabulary that used to distinguish NEEDS YOU cases survives as
row content, not as a separate section:

| row | cell / condition | what-line carries | verbs |
|---|---|---|---|
| dead mid-flight | ACTIVE ∧ ¬orch_alive | commits, "tick re-enters ~Nm" | log |
| died before starting | CLAIMED ∧ ¬orch_alive | prior runs, re-entry countdown | log |
| contended | >1 live session | session ids | kill |
| waiting on merge | REVIEW ∧ no automerge | PR # | PR ↗ |
| still running after PR | REVIEW ∧ orch_alive | session (info) | tail |
| blocked | BLOCKED | PR # | PR ↗ |
| checking Nm | CHECKING ∧ idle > nudge_idle_mins | "waiting on CI, PR #N" + liveness dot | PR ↗ |
| wedged | orch_alive ∧ idle_over | idle | kill |
| abandoned | agent-stuck | — (journal holds why) | issue ↗ |
| oracle / tick stale | cond 4 | which repo / minutes | tick now |

The ACTIVE/CLAIMED in those two cells is **`work_state`**, not the
label-only `issue_state`. Both rows additionally require the issue to be
label-CLAIMED and the session dead, but what separates them is
`work_state()`'s `ACTIVE if work_mtime(...) is not None else CLAIMED`
branch — the only thing in the system that knows whether branch work
happened. Joining liveness against `issue_state` alone reads "carries
`agent-working`" as "never started": orch#196 fixed exactly that, where
orch#183 rendered "died before starting (4 prior runs)" holding one
commit and an OPEN MERGEABLE PR. The other work_states (REVIEW, BLOCKED,
CHECKING, LANDED) fire **neither** row — they carry their own rows above,
and an else-branch here would swallow them.

Settled treatments carried forward unchanged: CHECKING stays a state
(load-bearing — the PENDING cell of the deliberately non-complementary
`pr_green`/`pr_red`; deleting it misreads pending as landable or failed)
and gets exactly one row with a stated cause and a clock — the human is
the judge. Dead rows carry no start button: the tick is the launcher
(condition 5 re-enters — instantly for REVIEW, after `idle_over`
otherwise); the row states the countdown and the issue page shows the
`claude --resume` line for a local operator who won't wait — terminal
friction is right for overriding the system's own recovery.

**TRACKED / AVAILABLE at repo altitude, ACTIVE / INACTIVE at issue
altitude — two axes, not one.** orch#259's design artifact (the phone
artboards) rules out the single unified vocabulary this section
previously described: a repo is **tracked** or **merely available**, an
issue is **active** or **inactive**, and the two pairs do not collapse
into one surface word. The prior text here said repo membership was
"surfaced as active / inactive" with TRACKED/AVAILABLE only as internal
naming — that is backwards. TRACKED and AVAILABLE ARE the surface words
at repo altitude; ACTIVE and INACTIVE are the surface words at issue
altitude (§4.2, the `enable`/`disable` → `agent-ready` toggle, a
different key entirely — issue enablement, GitHub-truth, not stored in
`orch.json`). Two axes, two scopes, two vocabularies; do not conflate
them and do not merge them into one pair of words that reads as covering
both.

Job 3 at repo altitude is the two-list transfer box (owner's model:
"like in windows programs"). Tracking a repo is a row-level verb on the
machine page itself: AVAILABLE rows carry `▶` right on the row
(artboard, page 1), **no selection checkboxes**. `+ add path`
(filesystem autofill, shows the resolved path and whether it is a git
repo before confirming); `+ add URL` (validated git URL, cloned under
the first scan root, then tracked).

**Untracking does not have a row-level control on this page.** The
machine page's TRACKED repo row carries no `[make inactive]` / `[make
available]` button — the artboard draws no verb on either repo row, consistent with it (#225 gap 6). The verb (`unwatch`,
§5's underneath, name kept as historical wiring — see §5) lives on the
repo page's own header instead, beside `nudge` (§4.2). This is
deliberate, not a stage gap: untracking is destructive enough to want
one navigation step of separation, so it sits one level down from the
list an operator scans every visit, while tracking — cheap, reversible
by discovery — stays on the row where the AVAILABLE list already is.
The asymmetry is the point: adding a repo is a row click on the
machine page, removing one costs a visit to the repo page first.

**Available is not removal**: the repo stays listed, known, never
forgotten; full removal is a rare hand edit. AVAILABLE = discovery ∪
off-entries: `unwatch` writes `"state": "off"` in the config, so a repo
is never forgotten — including one outside every scan root. "Off" is
plumbing, never a visible third list. Discovery is on-demand (`GET
/repos.json`): one-level glob of `<root>/*/.git` per scan root, stat for
recency, drop tracked, sort, cap ~20 — never in the tick, never in
`status.json` (config-time data, not attention data). Layout for this
axis is specified in "TRACKED / AVAILABLE: two sections" below.

**TRACKED / AVAILABLE: two sections, not two halves of an axis
(artboard, orch#259).** The center-divider layout this section
previously specified is OVERRULED by the design artifact. There is no
divider, no single panel split in two, and no flip control that sits
between two regions. The artifact's actual structure is two independent
sections, each with its own header, stacked top to bottom:

    TRACKED                                   ← section header
      repo row      .e.repo    heavier, its own ground
        issue       .e.sub     indented
        issue       .e.sub
    AVAILABLE                                 ← section header
      repo row      .e         plain — no children, no .repo weight

TRACKED is a real section: its repo rows carry the `.e.repo` weight
(heavier, its own background — see widget.tpl.html's shape system, §3.1)
and each repo's issues nest under it at one indent (`.e.sub`), the same
fold mechanism `other sessions` already uses. AVAILABLE is a second,
separate section below it: plain rows (`.e`, no `.repo` weight, no
nested children — an available repo has no issues rendered because it
is not being worked), visually lighter per §"Hierarchy" above. The
transfer control for a row lives on that row, right-aligned inside
whichever section header governs it (§3.4's section-header shape:
`[title (count)] [controls]`, controls `margin-left: auto`) — never
centered on a divider, because there is no divider to center it on.

`kill` is explicitly not on this axis. It acts on a live session, not on
membership — it is not a state toggle, and DESIGN.md already holds
killing as the operator's own act, distinct from anything the tick or a
toggle does automatically. `kill` must not render close enough to a
transfer control that the two are mis-clickable for each other.

**Dependency: AVAILABLE has nothing to render yet.** The axis visualizes
per-repo `state`, and today nothing reads that key back out — orch#257
is the issue building the reader. Until #257 lands, the AVAILABLE
section has no population source: a repo turned off does not currently
reappear anywhere as a distinct row, so building the full two-section
layout now would render a distinction the system cannot yet compute.
Recorded here so a later reader does not build it against a key nothing
populates and wonder why the second section is always empty.

**Repo rows** carry a live dot, the repo name, and the checkout path.
The needs-you count is cut from this row (#150): it duplicated the
nested issue and escalation rows directly below the repo, so the row
and its own children could show two different numbers for one repo —
the `pillState` failure with a different noun. Aggregation is
counting; judgment is concluding; no health words, no scores.

The artboard (page 1) nonetheless draws a bare number beside the repo
name — `orch 3`, `ops-console 1` — sitting in the identity cell, not a
separate column. **What that number counts is an open question, and
this document does not answer it.** Page 1 labels it with nothing. Two current sources point different ways: artboard page 3 (current) titles the same repo `3 in flight`, and its ACTIVE 3 are exactly page 1's three nested rows, so numerically the digit is the nested/in-flight count (the cut `Flat` page's needs-you figure for the same repo is 2, not 3); but #225 gap 3, in the operator's words, calls it "the needs-you count", and #150 cut a needs-you count from this row. The sources conflict; this paragraph does not resolve them.

Do not resolve it by reading the `Flat` artboard. That page pairs two
labelled counts on the repo row, which makes it look like the
authority on this exact question — but `docs/artboards/README.md`
marks it CUT by the operator's 2026-09-14 verdict on #150, and the
README exists because the PDF carries rejected pages alongside current
ones with nothing marking the difference. A cut page is not evidence
for how to read a surviving one, however precisely it seems to answer
the question.

So the interaction with #150 stays genuinely unsettled. #150 cut a
*needs-you* count from this row for a stated reason — it restated a
judgment the nested rows' own state chips were already making, and the
two could disagree. Whether page 1's unlabelled digit is that same
count returning, or a different one the verdict never reached, is not
derivable from any current artboard. **Ask the operator before
implementing this cell**; do not cite this paragraph as the decision,
because it is explicitly not one.

The dot is not a plain liveness indicator — it is the mark cell
(§3.1): a rounded-square outline means automerge is on for the repo,
exactly as it does on an issue row.

The **directory** is a real column of its own, right-aligned on the
row — `~/Documents/GitHub/orch` — and this is #116's ask, answered on
the canvas. At phone width it shortens, e.g. `~/…/GitHub/orch`. The
automerge control does not ride the row as a separate chip and does
not collapse to one at 390px — that control is gone; automerge is the
outline on the row's own mark cell (§3.1), not a competing element.

**Repo rows fold (#160), default EXPANDED.** The row is also the
disclosure control for its own nested issue rows below it — the same
toggle mechanism `other sessions` already uses, not a parallel one. The
operator's ruling on #160 settles the default: expanded, because the
artboards throughout this document show every repo's issues rendered
open (§4.1's own sketch above draws `orch`'s two issue rows in place,
unfolded); folding is the operator's own act on a row they chose to
collapse, not a posture the page starts in. This is the same direction
as every other quiet-state rule in this design — the page shows the
work by default, and hiding it is something a visit does, not something
the page decides for the operator. Fold state is a browser display
preference, not working-set data: it persists per-viewer in
localStorage, keyed separately from the escalation-dismissal store
(#106) so the two preferences cannot collide, and a missing or corrupt
value degrades to "nothing folded," i.e. the expanded default (#66) —
never to a page that opens looking emptier than the work actually is.
The repo's own count lives on the row itself, so it stays visible
whether the repo is folded or not; folding hides only the nested issue
rows, never the fact that they exist. (What that count counts is the
open question recorded under "Repo rows" above — this paragraph fixes
only where it sits, not what it means.)

AVAILABLE and INACTIVE rows do not fold, and that is a stage gap, not a
design choice: both sections render only a header plus a stage-2 hint
line today, because their row lists need `GET /repos.json` and `GET
/issues.json?repo=` respectively, which are not built yet — there is
nothing to fold until those rows exist. The fold mechanism itself is
shape-agnostic (it is the entity row's own toggle, §3.1), so wiring it
onto those sections is expected to be a small follow-up once their data
lands, not a redesign.

**Nested under each repo row: its issues.** Every issue the repo carries
renders as an entity row at one indent under its repo, in the same shape
as any entity row (§3.1) — identity (`#N title`, linked to the issue
page), the state chip (ACTIVE / REVIEW / CHECKING / …), age, and the
issue's own verbs (kill, and whatever else the issue page's verb set
carries). A repo's needs-you alerts — every kind that carries its
`slug`, not escalations alone (§ "Alert placement" below) — nest above
its issue rows, in the same indent, so the operator reads one repo's
full attention picture — what needs a decision, then what is running —
in one place, without a second section elsewhere on the page.

The "N escalations hidden in this browser" note is page-scope: it counts
every dismissed escalation on the page and renders once, under the
header, never once per repo. Its count and its `show` button are both
page-wide, so a per-repo copy would state a partial count under wording
that means the whole page.

### Alert placement — why the split is on `slug`

Every alert in `feed.alerts` either names a repo (`slug` is set) or does
not. This is a mechanical split, not a judgment call, and it decides
where an alert renders:

- **No `slug` → machine-scope.** These alerts (`tick stale`,
  `deploy-behind`) describe orch itself, not any one
  repo's work, so no repo row could ever be the right home for them.
  They render in a strip directly under the nav header, which already
  carries machine-scope facts (tick age, the reload button). The strip
  is scoped to slug-less alerts only — never a general alerts feed — so
  it stays what it is today: rare, short, at most 3 rows, normally 0.
  When no slug-less alert is pending, the strip renders nothing: no
  header, no empty block, matching the quiet-state rule design-wide.
- **Has `slug` → repo-scope.** Eight needs-you kinds carry a `slug`:
  `escalation`, `contended`, `wedged`, `waiting-on-merge`, `abandoned`,
  `died-before-starting`, `dead-mid-flight` and `still-running-after-pr`.
  All eight nest
  under that repo's row, above the repo's issue rows — the test is the
  `slug`, never the `kind`. Nothing about them is machine-scope, and
  nesting them under their repo is what lets the operator read one
  repo's whole attention picture without visiting a second section.
  Their row vocabulary (the taxonomy table above) renders unchanged.

  Narrowing this to escalations alone drops the other six off the page
  with their what-line and their verb, which is the regression #150's
  own review caught before merge. If a needs-you alert carries a slug,
  it renders here.

`feed.needs_you` (§117's membership flag) is unchanged by this split —
it still decides whether an alert counts as attention-worthy at all. The
split above only decides where a `needs_you` alert renders, once it has
already qualified.

**Automerge — the settled model, stated once.** One control per repo:
**on / off**. On overrides every issue in the repo. Off defers to each
issue's own `automerge` label. Renamed from `auto-land` (label migrates
mechanically); there is **no separate auto-review** — landing reviews
first, the agent skips review on small changes. Applies only to ACTIVE
issues — inactive means not queued, nothing runs, nothing merges — stated
in the UI at each altitude. Repo setting lives in `orch.json` (§6); issue
setting is the label, derived fresh like all label state.

**Other sessions** — the unattached-transcript scan, collapsed, capped at
5 shown ("+N more"): still the answer to "where work goes missing."

### 4.2 `#/<slug>` — the repo page

    ┌────────────────────────────────────────────────────┐
    │ ‹ orch   3 in flight                    (◉)  ⚡  ❙❙ │
    ├─ WHAT CHANGED  16:04 ──────────────────────────── ⟳ ┤
    │  LANDED            MOVED           NOT STARTED     │
    │  #31 tick clamp    #42 3 commits   #15 deferred    │
    │  #28 rollup hoist  #9  PR open     #16 untouched   │
    │  #27 state audit                   #18 untouched   │
    │  #25 feed fields                                   │
    ├─ ACTIVE 3 ─────────────────────────────────────────┤
    │ (◉) #42 fix tick drift        ACTIVE    47m     ❙❙ │
    │     dead mid-flight — 3 commits, no PR. tick       │
    │     re-enters ~13m.                                │
    │     log  tail  kill  issue ↗                       │
    │ (◉) #7  product table         REVIEW    22m     ❙❙ │
    │ (◉) #9  toml migration        CHECKING  38m     ❙❙ │
    ├─ INACTIVE 3 ─────────────────────────────── open + ┤
    │     #15 activity graph                   —      ▶  │
    │     #16 server uptime                    —      ▶  │
    │     #̶3̶1̶ ̶t̶i̶c̶k̶ ̶c̶l̶a̶m̶p̶                 LANDED   3h     │
    ├─ JOURNAL ───────────────────────────────────────  ↑┤
    │ 16:02  repo-orch      deferred #15 — blocked on    │
    │                       history row                  │
    │ 15:48  issue-orch.9   opened PR #14, awaiting CI   │
    │ 15:31  operator       prioritise toml over graph   │
    │ 15:17  issue-orch.42  session died — no PR         │
    │ ──────────── 4d 2h — no ticks ───────────────────  │
    │ Sep 10 repo-orch      started #42                  │
    │ [ journal…                                    ]  → │
    └────────────────────────────────────────────────────┘

**Hierarchy.** Order: WHAT CHANGED → ACTIVE → INACTIVE → JOURNAL; live
work first, then membership, then history. **ACTIVE is primary.** WHAT
CHANGED sits above it but is furniture at rest — a header, a refresh
glyph, and the cached table only if one exists; it never renders as an
empty block. INACTIVE is secondary, capped with its filters. JOURNAL is
reference at rest: the last 3 entries plus `load earlier`; it expands
when the operator is tracing, which is exactly when they ask for more.
Quiet state: nothing in flight → ACTIVE collapses to the furniture line
"nothing active — activate an issue below," and INACTIVE effectively
becomes the primary section, because membership is then the job the
visit is for.

**WHAT CHANGED is three columns, side by side — never a vertical
stack.** Landed / Moved / Not started (artboard, page 3) render as three
reduced-weight section headers (§3.4) in a row, each governing a column
of verbless entity rows (§3.1: identity, what happened, no age, no
verbs — an issue number plus one or two words, never a sentence: `#42 3
commits`, not "3 commits, tests green"). **The block does not collapse
to one column at phone width; it narrows instead.** The three-way
comparison — what landed against what merely moved against what never
started — is the content; stacking the columns into three sequential
lists trades one glance for three reads, which is the exact failure job
1 (§1) exists to avoid. The block carries its own vertical scroll,
capped height, so a busy repo's table cannot push ACTIVE and JOURNAL
down the page as it grows. Generated **by a button, never per-tick**,
cached with its timestamp (`16:04` on the artboard header). Flagged
plainly: **the one place the design spends a model call and renders
judgment as text** — admissible because it is deliberate, attributed,
and gates nothing; the tick and the needs-you rows ignore it.

**The state chip expands in place, same mechanism as §3.1.** `#42`'s
chip, tapped, opens its summary inline beneath the row: the condition
sentence `dead mid-flight — 3 commits, no PR. tick re-enters ~13m.` and
the actions that resolve it, `log tail kill issue ↗` (artboard, page 3).
This is where §3.1's tap-to-expand rule is actually visible on a page —
the machine page nests the row one level down; the repo page is where an
operator opens it.

**The header carries the untrack verb, beside nudge — the other half of
§4.1's asymmetry.** §4.1 established that the machine page's TRACKED row
carries no row-level untrack control, and named this page's header as
where the verb lives instead. The artboard confirms it: `❙❙`
right-aligned in the header, beside the automerge mark and `⚡` (nudge).
Deliberate, not an oversight — untracking is destructive enough to want
one navigation step of separation from the list the operator scans every
visit. What the `❙❙`/`▶` pair binds to at this altitude is still
orch#257's open question (§3.1); this page does not settle it.

**Automerge in the header is the mark, not a dropdown.** The artboard
draws the same dot-with-outline as everywhere else (§3.1) — a
rounded-square outline around the status dot means automerge is on for
the repo — not a text control. An earlier sketch here (`automerge: off
▾`) predates the canvas; the header renders the mark instead. The on/off
semantics are unchanged and stated once, at §4.1's "Automerge — the
settled model, stated once"; this section only fixes how the control
renders here.

**ACTIVE / INACTIVE** — job 3 at issue altitude, mirroring
TRACKED/AVAILABLE one level down, same on-row transfer gesture
(`activate`/`deactivate` = the `enable`/`disable` verbs — the
`agent-ready` label, which already IS the enable bit; the tick's
condition 1 fires only on it). Not stored in the config: issue enablement
is GitHub truth, derived fresh, per derived-versus-recorded. INACTIVE is
fetched on demand (`GET /issues.json?repo=`, capped), filter chips on the
header, `+ add issue`, `load all open issues`, RECENTLY DONE greyed and
struck at the bottom. Deactivate prevents pickup and never stops in-flight
work — stopping is `kill`; the UI says so.

**JOURNAL — the event log.** This carries what the cut activity graph was
for: "why did it go quiet" is a written reason at the moment work stopped
(the escalation rule guarantees one per level). The feed already ships
the last 8 entries (`orch.recent` — shipped today, rendered nowhere).
Two cheap gaps closed: **synthetic gap rows** derived at render time from
deltas between consecutive entries (downtime and idleness become
distinguishable — the one thing git could not show); **`load earlier`**
pages past the 8-entry cap (on-demand file read, not in the tick).

The input box at the bottom is **`ask`, redefined** (§5): it appends an
operator entry to this journal and spawns nothing — the next repo-orch
reads the tail on wake, exactly how levels already steer each other. The
contract stated under the box: *"read on the next wake — nudge to wake
one now."* Intent and immediacy are different buttons; only the second
launches, and it is the named exception. The dashboard journal stays
unrendered (its unique content is dashboard-op's reasoning; one `cat`
away locally; surface it the first time a deferral actually confuses —
not before).

### 4.3 `#/<slug>/<n>` — the issue page

A **detail page, not a list** — v6 gave it list structure by default and
that was the repo row showing through again. Six blocks, top to bottom:

    ‹ orch   #42  fix tick drift            CLAIMED · ACTIVE
    ● dead mid-flight — tick re-enters ~13m            (only when true)
    ── state ───────────────────────────────
    branch      issue-42 · 3 commits · idle 47m
    PR          none                          [issue ↗]
    automerge   n/a (repo is ON — change there ›)
    issue-orch  ○ dead · 2 prior runs
    ── actions ─────────────────────────────
    [kill]  [deactivate]
    resume by hand: cd ~/orch/wt/orch/42 && claude --resume a1b2…
    ── sessions ────────────────────────────
    ● a1b2…  14 turns · idle 3m               [tail]
    RUN LOG   all runs of this key                              ☰
      15:02  spawn pgid=48812
      15:08  worker u1 commit a3f91c2 clamp tick interval
      15:17  session ended — no PR opened
    steer this issue: comment on the thread ↗

1. **Title block** — identity + state pills: "what is this."
2. **Condition line** — furniture, exceptional state only: the same
   needs-you vocabulary as the machine page, one loud line under the
   title. Absent when nothing is wrong — the common state has no banner.
3. **STATE** — a definition list (§3.3), primary weight: branch/commits/
   idle, the PR (`pr_url`/`pr_number` — new feed fields; for
   REVIEW/BLOCKED/CHECKING the real click target is the PR, and today
   the feed carries only the issue URL), automerge (third altitude —
   live toggle when the repo is off; greyed with a "set by repo" link
   when the repo is on, so nothing inert is ever toggled), issue-orch
   liveness + prior runs.
4. **ACTIONS** — the verbs grouped in one place, secondary: `kill`,
   `deactivate`, and the resume command as a furniture code line. Verb
   output (`tail`/`log`) renders inline here — the global page-bottom
   `#out` pre is dead.
5. **SESSIONS** — entity rows (§3.1) with `tail` only. `log` is not a
   per-session verb: a run log is "all runs of this key" (issue-scoped),
   so one button per session row would repeat an identical request with
   no per-session meaning — it lives in the RUN LOG block below instead.
6. **RUN LOG** — all runs of this key, fetched on demand from the
   section header's `log` verb. SESSIONS above is scoped to one run at a
   time; RUN LOG is the whole key's history across every run that ever
   held it — they answer different questions (run log = what the
   process DID; SESSIONS/tail = what it SAID). Then the steering
   footer: the journal is a **labeled steering link**, not an embed and
   not a verb — it is GitHub comments (D2), human replies pass into the
   next brief unfiltered as steering input (`core.journal_tail`), and
   the phone writes GitHub threads natively; a second write path
   through `/act` would duplicate a better surface.

---

## 5. The verbs

Closed set, named, argument-validated, no shell. Four kinds; the split is
the owner's model — *remote surfaces originate intent, never processes;
the tick launches*:

**Working-set edits** (intent — what remote surfaces are for):
- `watch` (path) → `"state": "tracked"` in orch.json; `unwatch` (path) →
  `state="off"` (known, never forgotten; full removal is a rare hand
  edit). **These are action-layer names only — orch#259 settles the
  repo-altitude surface words as TRACKED / AVAILABLE; it does not rename
  this pair.** server.py already ships `watch`/`unwatch` under these
  exact names, so a later pass must not "helpfully" rename them to match
  the surface word: the UI says tracked/available, the wire says
  watch/unwatch, and the two never need to match (§4.1's "TRACKED /
  AVAILABLE: two sections").
- `enable` / `disable` (repo, issue) → add/remove `agent-ready`. Fixed
  label constant, never client-supplied. Not a general label editor —
  `agent-working` stays issue-orch's exclusive property, `agent-stuck`
  the escalation record.
- `automerge` (repo, value) → config write; (repo, issue, value) → the
  fixed `automerge` label. Values from a closed set.
- `clone` (url) → `+ add URL`: pattern-validated git URL, cloned under
  the first scan root, tracked. The one action that fetches remote
  content.

**Steering** (a journal write, not a process):
- `ask` (repo, text) — **redefined**: today `core.ask_repo` spawns a
  repo-orch (a launcher wearing a steering costume — verified,
  core.py:963); it becomes `journal_append("repo", …)`, spawning
  nothing. Honest limit in the UI: journal entries are not digest inputs
  and wake nobody — they ride the next wake.
- `summary` (repo) — WHAT CHANGED's button: asks an agent for the
  three-section table, caches it, gates nothing. The one deliberate
  model call.
- Issue altitude gets no steering verb: the thread is the journal and
  the better surface; the UI links it.

**Process control** (local-operator affordances by posture, not gating):
- `kill` (repo,issue | key) — generalized to any ledger key (today the
  widget kills only issue-orch keys while the CLI kills any — parity gap,
  A14). Killing is the operator's act, full stop.
- `nudge` (repo) — spawn a repo-orch now. **The one launch-now verb**,
  kept as a local convenience, named as the exception. Mechanical gating
  (client-address check) deferred until the posture ever fails.
- `tick` — one pulse now; the action exists, it finally gets a button.

**Reads:** `probe` (capability detection — the two-tier rule hangs on
it), `tail` (file), `log` (key — ledger-validated, path never
client-supplied). GET endpoints, not actions: `/repos.json`,
`/issues.json?repo=` (inactive candidates + journal paging).

**Cut, and staying cut:** `start` (the tick is the start verb; a button
would duplicate the system's launcher minus its resume-vs-fresh
judgment); `merge` (one-tap merge on a tailnet page is the exact
escalation the security model exists to prevent — GitHub is the merge
surface); `recheck` (PR page covers it); general label editing; any
free-form action; `/history.json` (died with the graph).

---

## 6. Config — `orch.json`

    {
      "scan_roots": ["~/Documents/GitHub", "~/orch"],
      "tick_secs": 600,
      "repos": [
        {"path": "~/Documents/GitHub/orch",     "state": "tracked", "automerge": true},
        {"path": "~/Documents/GitHub/openclaw", "state": "off"}
      ]
    }

- `scan_roots`: `~/Documents/GitHub` plus explicit custom paths like
  `~/orch`, globbed one level. **Discovery only, never membership** — a
  tracked repo outside every root stays tracked.
- Per-repo keys: `path`, `state` (`tracked` | `off`), `automerge` (bool,
  on overrides every issue in the repo). `automerge` is the only policy
  key and the first one something actually reads; the bar on speculative
  keys stays for everything else. An optional `note` string is allowed
  per repo — it is where an annotation like "paused, CI broken" lives,
  since JSON has no comments.
- **Read and write are both stdlib `json`.** No emitter to author, no
  round-trip check to justify, no dependency, no Python floor. The format
  documentation lives in the README rather than a file header, where it
  is more discoverable anyway.
- **Why not TOML** (decided 2026-09-14, owner): TOML reads nicer by hand,
  but `tomllib` is read-only by design (PEP 680) and the browser is this
  file's primary editor — hand-editing is the rare case. Writing TOML
  meant either a hand-rolled emitter with a round-trip check, or a
  dependency (`tomli-w` loses comments anyway; `tomlkit` keeps them but
  spends orch's zero-install property). JSON costs one thing — comments —
  and the `note` field covers the only real use for them.
- Migration, never-delete-before-convert: first run with `repos.txt`
  present and no `orch.json` → each uncommented git path becomes
  `"state": "tracked"`; `repos.txt` left inert on disk. Touch points in
  code: `core.repo_path_for`, `core.watch_repo`, `core.unwatch_repo`,
  `feed.build()`'s repos loop.

---

## 7. What is cut — from the current widget, and from this design's own passes

From the shipped widget: the tree (one nested scroll at every altitude —
the data-shape mistake rendered); the general alerts strip (needs-you
rows were its superset, and #150 confirms the cut stands — the machine
page's new header strip is NOT a return of the general alerts strip; it
renders only slug-less, machine-scope alerts, at most 3, normally 0 —
see §4.1's "Alert placement"); the ledger section (duplicates liveness
the pages show; dashboard-op becomes a footer dot); the global `#out`
pre (output renders at its cause); host `load` (disk stays); the
hardcoded client-side 25-min threshold (the feed exports the one real
number); `watch`/`unwatch` as path-typing CRUD (subsumed by the transfer
box); `repos.txt` (migrated, left inert).

From this design's own earlier passes, cut by re-derivation or by owner
call: the triage inbox view, the `#/` inbox route, the triage badge, the
breadcrumb system, cards as a component (taxonomy survives as row
vocabulary); `start` (cut before birth); the tick-replay view; the
activity graph, `#/activity`, `GET /history.json`, and the widened
history row — git log answers the commit layer retroactively for free,
and the three git-blind facts (downtime vs idleness, spin, non-commit
state) are one-line text facts the journal carries, not density values.
Overturned framings, on the record: v1's "attention-first page" (survives
only as a sort), v3's "the view is cheap, the record is the work" (half
the view could have rendered from git on day one), v1's "verb is the
link" read-only rule (wrong in one direction — config writes are exactly
what remote surfaces are for), v1's blanket label-edit rejection (wrong
in scope for `agent-ready` and `automerge`, right for the agent-owned
labels).

The `Flat` artboard (§4.1) — the machine page with a NEEDS YOU slice of
issues on top and repos reduced to counts — was never chosen, and is not
in this list because someone cut it. Nobody did. No head-to-head
rejection of `Flat` in favor of `Main` is recorded anywhere; the outcome
follows from #150 and #160 each settling a narrower question, not from a
deliberate comparison of the two artboards. What removed `Flat` from
contention in practice: #150's verdict against a needs-you section takes
out its distinguishing feature, and §4.1 as specified is `Main`'s shape
throughout. Cite this entry as "`Flat` was never chosen," never as
"`Flat` was cut."

One operational note, not a design item: a stale scratchpad `server.py`
(orchtest, port 18811) has been serving a frozen snapshot since Sep 10
while looking healthy. Deployment fact; the fix is running the repo's
server.

---

## 8. Staging — two stages

**Stage 1 — the three pages, from the existing feed (ship alone).**
- widget.tpl.html rebuilt on the shape system (§3): three routes, machine
  page (needs-you + tracked rows), repo page (active rows + journal from
  the already-shipped `orch.recent`, with gap rows), issue page, inline
  verb output, 390px rule, two-tier read-only rule.
- feed.py: `pr_url`/`pr_number`; the two liveness-join warn alerts;
  export `nudge_idle_mins`; alert entries carry the row fields. All
  read-side; digest untouched.
- No new server actions. Worth shipping alone: the six invisible
  product-table cells become visible rows, the journal renders at last,
  navigation becomes conventional.

**Stage 2 — config, membership, and verbs.**
- `orch.json` (read/write via stdlib json, migration, `automerge`
  key); label rename `auto-land` → `automerge` through briefs and skills.
- server.py + core.py: `GET /repos.json`, `GET /issues.json?repo=`;
  `watch`/`unwatch` onto the config; `enable`/`disable`, `automerge`,
  `clone`, `summary`; `ask` respawned as a journal append; `kill`
  generalized. CLI forms land in the same commits (parity, A14).
  (`log` shipped ahead of the rest of this list, orch#151: `a_log` in
  server.py plus the RUN LOG section in §4.3.)
- widget: both transfer boxes, the automerge mark on machine-page rows and the repo-page header (§3.1) plus the issue page's definition-pair toggle (§3.3, §4.3),
  WHAT CHANGED, INACTIVE filters, the ask box.

Stage 2 is the owner's loudest ask and lands right behind 1; the only
reason it is second is that its sections are rows inside stage 1's pages
— structure before the room. If appetite is one stage, ship both
together.

---

## 9. Open questions — none

**Config format: DECIDED — JSON (`orch.json`).** (Owner, 2026-09-14.)
Stdlib reads and writes it, so the hand-rolled TOML emitter, its
round-trip check, and the Python 3.11 floor all drop out of stage 2.
Cost: no comments; the per-repo `note` field covers the one real use.
See §6 for why TOML was set aside.

**PR is a row attribute, not a third level: DECIDED.** (orch#161.) The
issue is the unit of work. The PR is an ATTRIBUTE of the issue, not a
separate row, section, or entity of its own. The hierarchy stays exactly
the two altitudes §2 already names — machine page → repo row → issue row
— a PR is not a third. On a **list row** (§3.1) the PR rides the state
chip already carrying it (REVIEW = PR open and green, CHECKING = open and
undecided, LANDED = merged, BLOCKED = open and red) and is reached by a
PR link verb, same shape as any other verb. This governs list rows only;
the **issue page** keeps its `PR` definition pair (§3.3, §4.3) exactly as
is — a definition list is where attributes belong on a detail page.

**An alert is not an object: DECIDED.** (Owner, 2026-09-14; orch#166.)
The page renders **repos and issues. Nothing else.** An alert is a derived
statement ABOUT an entity, never an entity of its own, so it never takes a
row of its own. This is the same rule as the PR decision above, applied to
the other thing that was competing for row status.

It follows from the derivation itself. `feed.py` computes
`waiting-on-merge` from `work_state == "REVIEW"` and a dead orch, and
`died-before-starting` from a label-CLAIMED issue whose `work_state` is
also CLAIMED with no live session — both are
re-readings of fields the ISSUE already carries and the state chip already
renders. A second row for the same fact is one fact in two shapes, free to
disagree: the `pillState` failure this codebase already fought once,
promoted to a row. What the alert adds is the *reason*, which belongs in
the issue row's `what` cell or its expanded body, derived at render time.

Two cases are NOT covered by this rule and keep their rows:

- **Slug-less alerts** — tick stale, deploy behind.
  These are facts about the MACHINE, with no repo or issue to attach to.
  They stay in the header strip (`machineAlerts`), which is where the
  artboard already carries machine-scope facts. Do not widen that filter.
- **Escalations** — a recorded journal row an agent wrote, carrying a note
  and verbs, with no `issue` number. It names no issue, so it duplicates
  nothing. It nests under its repo as a `loud` row, visually distinct from
  an issue row, until the separate change converting escalations to filed
  issues lands. Removing it earlier would delete the operator's only route
  to the note (#93, #66).

**This governs RENDERING ONLY.** `feed.alerts`, `msg` and `level` keep
their exact shape: `tick.py` folds the count of `error` alerts into
`needs_attention`, which decides whether a wake happens at all. Removing a
feed key to satisfy this rule would silently change when orch wakes, and
the change would be invisible until a wake that should have happened did
not. Check every consumer before removing one.

Everything else raised across the rounds is resolved and recorded above
so it stays closed: remote = intent only (`start` cut, `nudge` the named
exception); scan roots; Python floor; CHECKING (one row, cause + clock,
human judges); the temporal views (cut twice; journal + git log cover
them); automerge (on/off per repo, overrides when on, ACTIVE issues
only).
