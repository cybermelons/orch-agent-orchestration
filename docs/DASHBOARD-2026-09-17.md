# The dashboard — one spec, 2026-09-17

Fable. Report and spec only: no code changed, no PR, no issue edited.
Written after reading `docs/RESTRUCTURE-2026-09-16.md` (§10–§12 are
rulings), the operator's own statements of 2026-09-16, PR #361 (#358), the
23 open `2.0-ui` issues and their threads, `docs/UX-REDESIGN.md` v6 with
its supersession note, `docs/artboards/README.md`, `widget.tpl.html`
(2,135 lines), `orch/feed.py` (1,158), `orch/server.py` `ACTIONS` and
`/events`, and the live `public/status.json` on devhost at 22:2x EDT.

Every "ships today" claim below cites a line in today's tree; every number
is measured. Anything not checked is marked **unverified**. The canvas URL
was not read (#237: 403); the vendored PDF's page table in
`docs/artboards/README.md` was.

This file supersedes the 23 issues listed in §6 as the description of the
dashboard. `docs/UX-REDESIGN.md` remains the shape system (§3) and the
page skeletons (§4.1–4.3 wireframes); where this file and that one differ,
this file wins.

---

## 0. The answer

**The dashboard the operator described is ~85 % built.** The live push
path exists, the three pages exist, the verb set is already the ruled
three (issue · PR · note), and the feed already derives an activity line
at every altitude. What is missing is small and specific:

1. The activity log the pages render is the **tick heartbeat**, not
   activity: `orch.recent` is the last 8 journal rows, and 99 of 104 rows
   in the live feed are `observed` (measured, §2.3). The repo page's
   JOURNAL section therefore shows eight lines of "tick observed". One
   filter fixes it and unlocks a machine-wide tape.
2. The repo row says **"N in flight"** by counting issue rows
   (`widget.tpl.html:1241`, `:1484`) — exactly the "11 in flight, why only
   2" misread #386 recorded. The feed already carries `live_orchs`; the
   widget ignores it.
3. Nothing marks **what changed** between one paint and the next, so a
   page that repaints every tick looks static.
4. Issue rows on the **machine page** carry no activity line; the repo
   page's rows do (#289 part 4). Inspecting a row in place is specified
   (`UX-REDESIGN.md` §3.1) and not built.
5. The deploy commit/behind readout is derived (`feed.py:708`) and
   rendered nowhere (#141).

Seven widget-side changes and one feed-side filter. No new server action,
no new state, no new timer, no rewrite. Of the 23 issues: 10 supersede
into this file, 7 are contradicted by a ruling, 2 are already done, 4
survive as independent issues (§6).

---

## 1. The operator's ask, and what it means mechanically

Stated 2026-09-16:

> *"A dashboard I'd live monitor for activity, like a trade window."*
> *"I want to see the latest activity log when inspecting issue rows, repo rows, session rows."*
> *"The API is for the agent. I, the operator, only want few actions."* (#358)
> *"I only ever want to see the issue, PR, or send a note."*
> *"The goal is auto-land, then I check later."*

A trade window has three parts, and each maps onto something the feed
already derives:

| trade window | dashboard | source in the feed today |
|---|---|---|
| the tape — a time-ordered stream of fills | a **TAPE** of journal rows across every repo | `repos[*].orch.recent` (`feed.py:317`, `journal_tail("repo", slug, n=8)`) |
| positions — rows with live state and P&L | TRACKED repos with nested issue rows, state pill, age | `repos[*].issues[*]` — `state`, `work_state`, `orch_alive`, `activity`, `pr` |
| inspect a position — its recent fills | tap a row → its latest activity, in place | issue: `brief` (`core.issue_brief`), `sessions[*]`; repo: `orch.recent`; session: `a_log` / `a_tail` |
| the blotter's few buttons | issue ↗ · PR ↗ · note | shipped by PR #361 |

"Live" is already answered: the widget opens `EventSource("/events")`
(`widget.tpl.html:2126`), the server pushes the whole feed whenever
`status.json`'s mtime moves (`server.py:63-70`, 1 s poll, 15 s heartbeat,
8 streams max), and the page repaints from it. Granularity is the tick —
fires on any tracked session exit or the 600 s clock; median gap 56 s
over the last 200 ticks (RESTRUCTURE §2). That is the right cadence: the
tick is the origin of every fact on the page, so nothing between ticks
could change what it shows. **Do not add a second timer**
(`widget.tpl.html:2113-2115`).

"Check later" is answered by the tape too: on returning, the operator
reads the newest rows down to where they left off. That replaces the
WHAT MOVED digest (§4.7).

---

## 2. What ships today — grounded

### 2.1 Pages and routes (`widget.tpl.html`)

- `#/` machine page (`machinePage`, `:1083`): nav with tick age; slug-less
  alert strip; TRACKED repos (`repoRow` `:1185`) with nested issue rows
  (`repoIssueRows` `:1292`: automerge dot, `#n`, pill, title, age, PR
  verb); AVAILABLE = paused repos with their `note` (`:1138-1143`, #257
  screen half **is** rendered — the #257 thread's "not done" comment
  predates this); other sessions collapsed; footer (`machineFooter`
  `:1332`: tick now, dop dot, disk).
- `#/<slug>` repo page (`repoPage`, `:1471`): nav with path, "N in flight",
  read-only repo automerge default, nudge; WHAT MOVED furniture (disabled
  generate); ACTIVE rows with `display_name` + `brief` sub-line (#289
  parts 3–4, PR #334); INACTIVE = `unconsidered` numbers; JOURNAL =
  `orch.recent` with synthetic gap rows (`journalBody` `:1600`); the note
  box posting `{action:"ask"}` (PR #315, #292).
- `#/<slug>/<n>` issue page (`issuePage`, `:1682`): title + both state
  axes; condition line from alerts; STATE deflist (branch, PR, labels,
  automerge dot, issue-orch liveness, brief); ACTIONS = merge (with
  blocking findings) + issue ↗ + resume line; SESSIONS with `tail`; RUN
  LOG on demand via `a_log`; steering link to the thread.

Verbs on the page after PR #361: issue ↗, PR ↗, note (send), merge
(issue page, arm-then-confirm), nudge (repo header), unwatch (repo row,
arm-then-confirm), tick now (footer), automerge dot (per issue). No
kill, assign, unclaim, set-inactive.

### 2.2 The feed (`orch/feed.py`, live `status.json` = 263,682 bytes)

Top keys: `awaiting alerts generated nudge_idle_mins tick host
dashboard_op repos agent_sessions unattached_sessions`.

Per repo (`repo_json` `:321`): `repo path slug ok counts labels_missing
live_orchs in_flight_cap auto_land issues unconsidered orphan_prs orch
stalled stalled_since state`. `orch` = `{key, alive, prior_runs,
recent}`. `live_orchs` is counted from the session ledger
(`core.live_issue_orchs`, orch#279) — the one honest concurrency number.
`stalled` is #301's derivation (`:525-556`).

Per issue (`issue_json` `:62`): `issue state work_state title
display_name name_stale labels ready auto_land auto_land_source branch
commits idle_min activity url brief pr pr_url sessions contended
orch_alive prior_runs startable owner_starting considered`. `brief` is
`{text, stale}` from the newest agent journal comment on the thread
(`core.issue_brief` `:5024`, no forge call). Per session: `id file turns
idle_sec bytes current live resume workers`.

Machine: `tick.minutes_since_last`, `host.deploy = {commit, branch, base,
behind, is_behind, pull_*}`, `host.disk`, `host.live_orchs`,
`agent_sessions` (200 ledger rows: `key role scope alive log_mtime
prior_runs` — rendered nowhere, cut by UX-REDESIGN §7), `unattached_sessions`
(983 rows; the bake ships 5 + `unattached_total`, `build_widget.py:63-66`).

### 2.3 Measured on the live feed, 22:2x EDT

| fact | value |
|---|---|
| `orch.recent` rows across 13 repos | 104 — `observed` 99 · `spawn` 3 · `consolidated` 2 |
| repo journals, 24 h, by event | `observed` 5,678 · `spawn` 436 · `consolidated` 184 · `deferred` 112 · `started` 94 · `escalated` 18 · `retracted` 16 · `landed` 15 · `spawn-cold-retry` 14 · `filed` 14 · `nudged` 9 · `note` 6 · `asked` 3 |
| issues in feed / with a `brief` | 20 / 6 |
| `orch` repo: `live_orchs` / issue rows | 5 / 15 — the widget prints "15 in flight" |
| `orch` issue rows by (state, work_state, alive) | UNCLAIMED·CLAIMED·dead 7 · CLAIMED·REVIEW·alive 3 · CLAIMED·ACTIVE·alive 2 · UNCLAIMED·LANDED·dead 1 · CLAIMED·CLAIMED·dead 1 · UNCLAIMED·ACTIVE·dead 1 |
| `_tail_rows` (`core.py:4765`) | `read_text().splitlines()[-n:]` — reads the whole file regardless of `n`; raising `n` costs nothing |

The first row is the finding that matters. An `observed` row is the
tick's per-issue snapshot (`{issues:[{number,state,work_state}…]}`); the
widget renders it as the bare word "observed" (`journalBody`, `:1621`).
So the JOURNAL section that #211 asked for and PR #315 shipped is,
today, eight heartbeat lines. Nothing the operator would call activity
survives an 8-row tail when the heartbeat writes 5,678 rows a day.

---

## 3. What the dashboard is

**One live page, three altitudes, three verbs.** The machine page is the
window; the repo and issue pages are the same rows opened wider. Every
row is an entity with a state and an age; every altitude can be inspected
in place for its latest activity; the top of the window is a tape of
what the machine did, newest first, across all repos.

### 3.1 Machine page `#/`

    ┌──────────────────────────────────────────────────────────────┐
    │ orch   tick 2m ago                                       ⟳    │
    │ (machine alerts, only when present)                          │
    ├─ TAPE ───────────────────────────────────────────────────────┤
    │ 22:23  orch         repo-orch   consolidated — THE OPERATOR… │
    │ 22:07  orch         repo-orch   started — STARTED #184…      │
    │ 21:49  orch         repo-orch   deferred — DEFERRED #387 ON… │
    │ 21:14  orch         issue-orch  landed — #372 PR 383         │
    │ 19:46  orch         operator    asked — note box smoke test  │
    │ 18:38  ops-console  operator    nudged                       │
    │ …                                              (20 rows max) │
    ├─ TRACKED 13 ─────────────────────────────────────────────────┤
    │ ▾ ● orch ›   2 working · 3 awaiting · 1 orphaned · 8 stale   │
    │     ◉ #387  REVIEW   PR #394 open, CI green            9m  PR│
    │       ↳ latest: opened PR 394; awaiting CI                   │
    │     ◉ #343  ACTIVE   3 commits, no PR                  2m    │
    │       ↳ latest: deleting wake_gate; tests next               │
    │     …                                                        │
    │   ● ops-console ›  0 working · 1 awaiting                    │
    ├─ AVAILABLE ──────────────────────────────────────────────────┤
    │ ▸ other sessions on this machine 983                         │
    │ tick now         commit 4c959dd main · 0 behind · disk 61%   │
    └──────────────────────────────────────────────────────────────┘

Changes from today, each one section below: TAPE (§4.1), the repo row's
count (§4.2), the issue row's activity sub-line and in-place inspect
(§4.3), the changed-row flash (§4.4), the footer readout (§4.5).
Everything else is as shipped.

### 3.2 Repo page `#/<slug>`

As shipped, minus WHAT MOVED (§4.7 deletes it), with the JOURNAL section
showing acts rather than heartbeat (§4.1's filter does this for free,
because JOURNAL reads the same `recent`). The header's "N in flight"
becomes the §4.2 count. Nothing else changes.

### 3.3 Issue page `#/<slug>/<n>`

As shipped, with RUN LOG auto-fetched on each render while the session is
live (§4.6). Nothing else changes.

### 3.4 Verbs — closed, and why closed

| verb | where | ruling |
|---|---|---|
| issue ↗ | every issue row, issue page | #358 |
| PR ↗ | every issue row with a PR, issue page | #358 |
| note (send) | repo page, bottom of JOURNAL | #358; backend is `ask` permanently (#296 ruling) |
| merge | issue page only, arm-then-confirm, blocked by findings | PR #361 kept it "where it applies"; the operator posture is auto-land, so this is the manual override for a `no-auto-land` issue |
| automerge dot | per issue | #163; the per-issue label is the ruled hold mechanism (§10) |
| nudge | repo header | UX-REDESIGN §5's one launch-now verb |
| unwatch ⊘ / watch ▶ | repo row | membership (job 3); ▶ is the one addition, §4.8 |
| tick now | footer | furniture |

Nothing else. A server action that has no button here is for agents and
the CLI; that is the #358 shape and it is not a gap.

---

## 4. What to build

All widget-side unless stated. Sizes are estimates against today's file;
`public/widget.html` is 199 KB and there is no page cap (verdict 2026-09-16,
`DASHBOARD-DESIGN-92.md:370`); the repo-page zone cap is 30 KB.

### 4.1 TAPE — the one feed change, then one widget section

**Feed (`orch/feed.py:313-320`, `repo_orch_json`).** `recent` becomes the
last 8 rows whose `event` is not in `{"observed", "spawn",
"spawn-cold-retry"}`: read a wider tail (`journal_tail("repo", slug,
n=200)`), filter, keep the last 8. Cost: none — `_tail_rows` already reads
the whole file (§2.3). The three excluded events are wake bookkeeping:
`observed` is the tick's snapshot (its information is the state pill on
every row and the tick age in the nav), `spawn` is "repo-orch woke" (the
repo row's live dot). Everything else — `started deferred consolidated
landed escalated retracted filed nudged asked note addressed correction`
and any event added later — is an act, and passes through unfiltered (the
filter is a deny-list of three, never an allow-list, so a new event needs
no edit; same stance `orch/usage.py` took, #298).

One test: a journal of 50 `observed` rows and 3 `started` rows yields
`recent == the 3 started rows`. Mutation-check by removing the filter.

**Widget: `tapeBody(d)` + a TAPE section at the top of `machinePage`, under
the alert strip.** Merge every `repos[*].orch.recent` (each row tagged
with its `slug`), sort by `at` descending, cap 20. Render with the
existing log-row shape (`logRow`, `:877`) plus a slug cell linking to the
repo page: `[time] [slug ›] [actor] [event — first line of note, clamped
140]`. Reuse the 140-char slice-then-escape clamp PR #334 established. No
gap rows on the tape (a cross-repo gap means nothing). Quiet state: one
furniture line, "no activity recorded yet". Section header shows the
count; the section is a disclosure (`sectionHead` with `tid`) so the
operator can fold it, persisted like repo folds (`FOLD_KEY`, #160).

Why this and not a per-tick digest: the tape is derived from rows already
written by the agents that did the work, at zero model cost. WHAT MOVED
(#209) would spend a session per press to summarise the same rows into
three columns. §12's finding is that sessions are 91 % of spend;
a button that starts one to restate the tape is the wrong direction, and
the operator's "check later" is served by reading down the tape.

~60 lines JS + ~10 CSS. This is the shortest path to the trade window.

### 4.2 Row counts in #386's vocabulary — replaces "N in flight"

`widget.tpl.html:1241` (repo row) and `:1484` (repo page header) print
`issues.length + " in flight"`. Replace with four counts derived
client-side from fields every issue row already carries, using #386's
words exactly:

| bucket | predicate on the issue row |
|---|---|
| **working** | `orch_alive` |
| **awaiting** | `!orch_alive && pr && work_state ∈ {REVIEW, CHECKING, BLOCKED}` |
| **orphaned** | `!orch_alive && work_state === "LANDED"` (row present ⇒ issue still open) |
| **stale** | `!orch_alive && !pr && work_state ∈ {CLAIMED, ACTIVE}` |

Render `N working · N awaiting · N orphaned · N stale`, omitting zero
buckets; `idle` when all four are zero. **`working` must equal
`r.live_orchs`** on every row — assert it in the widget test and render
`r.live_orchs` for that cell, so the ledger count is what the operator
reads (PR #389 ruled the ledger the numerator). `stalled` (#301) stays as
the red dot it already is (`:1213-1229`); it is a repo-level fact and
does not become a fifth bucket.

Cost: a per-row reduce; no feed change. ~25 lines. One derivation with
one home (`bucketOf(i)`), called from both sites — the `pillState` lesson
(`:933-941`).

### 4.3 Inspect in place — the issue row's activity line and expansion

**Sub-line on the machine page.** `repoIssueRows` (`:1292-1310`) passes
no `sub`; the repo page does (`:1548`). Pass `brief.text` through the same
clamp. `entityRow` already supports `sub` (`:861-874`); this is one
argument. Rows with no brief render exactly as today (honest blank, no
invented sentence). 6 of 20 issues carry a brief on the live feed; that
number rises as issue-orchs journal.

**Tap-to-expand.** `UX-REDESIGN.md` §3.1 specifies it and the artboard
draws it (page 3: `dead mid-flight — 3 commits, no PR. tick re-enters
~13m.` under `#42`). The fold mechanism exists (`data-t` + `.det`,
`wire()` `:1901-1932`); reuse it per issue row, keyed `is-<slug>-<n>`,
default folded, not persisted (inspection is an act, not a preference).
The expanded body, in order, from fields the row already has:

1. the condition what-line — the `alerts` entry for this `(slug, issue)`
   with `needs_you`, as `kind · what` (same text `needsYouRow` builds,
   `:1418-1440`), absent when there is none;
2. the brief with its age (`brief.stale` marker as on the issue page);
3. the current session: `turns · idle Ns` from `sessions[current]`
   (`issuePage` `:1804-1810` builds this string already);
4. the verbs: issue ↗ · PR ↗ · `open ›` (the issue page).

No fetch on expand. The expanded issue is what the row already knows; the
issue page is where a read (`log`, `tail`) is spent. ~50 lines.

**Repo row.** Its fold already opens the nested issues. Append the repo's
own last 3 `recent` rows (post-§4.1, so acts) under the issue rows inside
the same `.det`, via `journalBody(recent.slice(-3))` — the operator asked
for the activity log when inspecting repo rows, and this is it without a
second mechanism. Duplicates the tape by at most 3 rows per repo; the
tape is "across repos, newest", the fold is "this repo, latest", and the
repo page holds the 8. ~6 lines.

**Session rows.** They exist on the issue page (SESSIONS, `tail` per row)
and as "other sessions" (unattached, reference only). The issue page's
`tail` is the inspect; nothing to add. `agent_sessions` (the ledger)
stays unrendered — UX-REDESIGN §7 cut the ledger section because the
pages show liveness already, and that holds.

### 4.4 The changed-row flash

`render()` holds `last_data` (`:1856`). Before overwriting it, compute per
`(slug, issue)` whether `pillState(i)`, `orch_alive`, `pr` or `activity`
differ from the previous paint, and per tape row whether its `at` is newer
than the previous `generated`. Add class `moved` to those rows; CSS gives
`.moved` a background that transitions to normal over ~4 s; under
`@media (prefers-reduced-motion: reduce)` it is a static highlight that
clears on the next paint. New rows (an issue absent last paint) and
vanished rows are not animated — a row leaving the set is the merge case
#289 noted, and the tape's `landed` row is its record.

This is #289 part 2's honest form. The page rebuilds `innerHTML` from a
string (`:1875`); animating a row *moving* between sections means a
retained-DOM diff layer, which is a rewrite the file's own design refuses.
A flash on the row that changed, in the section it now belongs to, gives
the operator the same fact — something moved, here — at ~30 lines.

### 4.5 Footer readout — deploy commit/behind (#141)

`machineFooter` (`:1332`) renders disk. Add, beside it, `host.deploy`:
`commit <sha> <branch> · <behind> behind` when `behind` is a
non-negative int, `commit ?` otherwise. The `deploy-behind` alert still
fires at `behind > 0` (`feed.py:1118`); this is the quiet readout the
alert escalates from. #141's 2026-09-16 comment records a live case where
two commits of drift silently changed what the CLI could do. ~8 lines.

### 4.6 RUN LOG rides the push while the session is live (#189)

On the issue page, when `i.orch_alive`, call the existing run-log fetch
(`[data-run-log]` handler, `:1998-2009`, action `log`) on every `render()`
of that page, painting into the same `run_log_text[sink]` store. When the
session is dead, leave it button-only as today (#189: "do not poll a file
for a session that is not alive"). No timer: the fetch is triggered by
the SSE repaint, so it happens once per tick, which is the cadence the
log's own writer (`orch/runlog.py`, #138) is meaningful at. `a_log` takes
`repo`/`issue` only and derives the path itself (`server.py:618-628`), so
the key-validation acceptance in #189 is met by construction. Bounded:
`runlog.parse(text, limit=400)` (`:648`).

This settles #189's open question: **one surface, the issue page**, one
tap from any issue row. The repo page's rows carry the brief sub-line
instead of an embedded pane. ~15 lines.

### 4.7 Deletions

- **WHAT MOVED** — the section furniture in `repoPage` (`:1504-1506`), the
  `stageTwoAttrs("generate …")` button, and the `.sh.sub` CSS (`:341`)
  that exists only for its three outcome columns. Reason: §4.1. Nothing
  else references it (grep today). Closes #209 and #152 by removal.
- `dashboard-op-hung` alert (`feed.py:1108-1111`, its guard just above) and the footer `dop` dot
  — these go with #343's cut (RESTRUCTURE §3 item 1 names the alert; the
  feed dict `dashboard_op` is kept). Do it in #343's PR, not here; listed
  so this spec does not describe a dot that will not exist.
- `public/dashboard.html`, `public/orch-canvas.html` — not served
  (`server.py` serves `public/widget.html`); RESTRUCTURE §1.3 says move
  them under `docs/`. Not a dashboard change; noted because #289 C.4 and
  #241 C.4 ask "which rendering do the artboards govern" — the answer is
  `widget.tpl.html`, only.

### 4.8 One membership control: ▶ on a paused repo (#257's last piece)

The feed carries `state: "off"` rows, the widget renders them under
AVAILABLE with their `note` (`:1138-1143`), and `unwatch` writes
`state: "off"` rather than deleting (`core.unwatch_repo`,
`core.py:3154-3166`). What is missing is the way back from the page: a
paused row's verbs are `nudge` + `unwatch` (`:1140` → `repoRow` `:1257`).
Add `verbWatch(r)` mirroring `verbUnwatch` (`:1062-1077`) — glyph `▶`,
posts `{action:"watch", path:r.path}` (`server.py:662`, exists), same
`SLUG_OK` guard, no arm-then-confirm (tracking is cheap and reversible).
Emit ▶ on `state === "off"` rows and ⊘ on tracked rows, never both.

This closes #259's deferred glyph question by not using `❙❙` at all: the
shipped pair is `⊘` / `▶`, bound to `state`, and automerge keeps its own
control (the dot). If the operator wants `❙❙` back, it is a one-character
change in one function. ~15 lines. **Recommendation, not a ruling** — the
operator said they would decide by testing.

### 4.9 What this dashboard does NOT do — and the ruling for each

| not built | ruling |
|---|---|
| controls rendered from the `/act` verb table | #358: "a control earns its place by being used, not by existing in the table" |
| CRUD on repos / issues / keys | #358; #11's own verdict left only the "which verbs" question, and #358 answered it |
| review approve / reject / re-review surface | #358; the landing skill runs review agent-side; the issue page shows blocking findings as text and gates `merge` on them (`:1024-1060`) |
| repo-level automerge toggle | RESTRUCTURE §10: "do not flip the repo defaults until [the ops-console case] is understood"; the per-issue dot is the ruled hold; the repo default renders read-only |
| cross-repo noticing, unprompted | #343 (ruled do, in flight): dashboard-op wakes only when asked |
| a READY state, `agent-ready` toggles, "set inactive" | #346 (ruled collapse): the axis is dead; `startable`/`owner_starting`/`ready` feed fields go with it |
| turn graph / burndown / activity graph | #347 deleted `transitions`; UX-REDESIGN §7 cut the graph; the tape + flash is the time view |
| WHAT MOVED digest | §4.1, §4.7 |
| `load earlier` journal paging | the repo page shows 8 acts (post-filter) and the tape 20; the file is `state/repos/<slug>/orch.jsonl`, one `cat` away; build when 8 acts is measured to be too few, not before |
| row-movement animation | §4.4: the page rebuilds from a string by design |
| a second refresh timer | `:2113-2115`; SSE is the live path |
| a structured-question wizard | #295 — kept as its own issue, sequenced after this (§6) |
| new stored state | none; `names.json` is the one cache and it predates this |
| a new server action | none; §4.6 and §4.8 use `log` and `watch`, both in `ACTIONS` today |

---

## 5. Build order — shortest path to the trade window first

Each step is one PR, widget-only unless noted, independently shippable
and reviewable against one measurable check. `python3 -m orch.test_core`
gate throughout (passed=1621 at the audit; **not re-run for this report**).

| # | step | files | size | check |
|---|---|---|---|---|
| 1 | `recent` excludes `observed`/`spawn`/`spawn-cold-retry` (§4.1 feed) | `orch/feed.py`, `orch/test_core.py` | ~5 + test | repo page JOURNAL shows acts; live feed `recent` has 0 `observed` |
| 2 | TAPE section on the machine page (§4.1 widget) | `widget.tpl.html` | ~70 | 20 newest acts across repos, newest first, slug links resolve |
| 3 | #386 counts replace "N in flight" (§4.2) | `widget.tpl.html`, test | ~25 | `working` cell == `live_orchs` on every row; no "in flight" string left |
| 4 | brief sub-line on machine-page issue rows + tap-to-expand + repo fold tail (§4.3) | `widget.tpl.html` | ~60 | expand shows condition/brief/session/verbs with no fetch |
| 5 | changed-row flash (§4.4) | `widget.tpl.html` | ~30 | a state change between two pushes highlights that row; reduced-motion static |
| 6 | footer deploy readout (§4.5) | `widget.tpl.html` | ~8 | `commit <sha> · N behind` renders from `host.deploy` |
| 7 | RUN LOG auto-fetch while live (§4.6) | `widget.tpl.html` | ~15 | live issue page refreshes RUN LOG per push; dead session unchanged |
| 8 | delete WHAT MOVED (§4.7) | `widget.tpl.html` | −20 | no `what moved` / `.sh.sub` in the file |
| 9 | ▶ watch on paused rows (§4.8) | `widget.tpl.html`, test | ~15 | pause → AVAILABLE with ⊘ absent and ▶ present; ▶ → TRACKED |

Steps 1–3 are the trade window. Steps 4–7 are "inspect a row". 8–9 are
tidy-ups that can ride any of the others. Steps 2–9 each touch only
`widget.tpl.html` plus tests, so they serialise trivially; do not batch
them into one PR — each has one review question.

Sequenced against the restructure: none of 1–9 depends on #343/#346
landing, and none touches what they cut, except that step 8 should not
race #343's feed edit (different function, same file — rebase, not
conflict). #295 (wizard) comes after all nine.

---

## 6. Disposition of the 23 issues

Labels: **supersede** — absorbed into this file, section cited, close
the issue; **keep** — independent, survives as its own issue, reason
given; **close-no** — contradicted by a ruling or premised on something
deleted; **close-done** — already landed, nothing left. Every row has a
reason; the operator's session does the closing.

| issue | title (short) | disposition | reason |
|---|---|---|---|
| #298 | usage inspection timeline | **keep** | The query layer landed (PR #306, `orch/usage.py`); what is left is a UI for historical inspection — a report page, not the live window. Independent of this spec. Re-scope the body to "UI over `usage.py`, separate page, on demand", or close as done if the CLI is enough. Do not put it on the machine page. |
| #295 | structured questions → wizard | **keep** | Agent→operator, discrete answers, `orch:question:v1` on the review-block precedent. Different direction from the note box (#296 ruling) and not obsoleted by it. Consistent with "few actions" — it is answering, not commanding. Sequence after §5 step 9; open questions 3–5 in its body still need the operator. |
| #289 | live repo page: WHAT CHANGED live, animate, names, tails | **supersede** → §4.1, §4.4 | Parts 3–4 landed (PR #334). Part 1 (WHAT CHANGED on every tick) is a per-tick model call — contradicts UX-REDESIGN "generated by a button, never per-tick" and §12; the tape replaces it. Part 2 (row movement) becomes the changed-row flash, §4.4, for the reason stated there. |
| #257 | pause/play per repo | **supersede** → §4.8 | Feed half landed (PR #304); the screen half the thread calls "not done" is done (`widget.tpl.html:1138-1143` renders paused repos under AVAILABLE with `note`). What remains is one ▶ button. |
| #241 | artifact-vs-code index | **supersede** → this table | The index's rows: 1.1 → §4.8; 1.2 built (`:1150`); 1.3 → §4.5; 3.1 → deleted §4.7; 3.2/3.3/3.6 built (`journalBody`, note box); 3.4 built; 3.5 → §4.3; 4.1 is #233 (closed, unverified here); 4.2 close-no (see #212); 4.3 landed; 4.4/4.5 built (`:1804`, `:1793`); 4.6 cut by #358; C.1–C.3 done (see #237); C.4 answered in §4.7. |
| #237 | artifact link 403 | **close-done** | Premise inverted: the PDF is vendored; `UX-REDESIGN.md:24-27` cites the in-repo path as authority with the URL as provenance; `docs/artboards/README.md` carries the refresh rule. All three "remaining work" items are in-tree. Which of two URLs is canonical is unknowable from here and no longer load-bearing. |
| #231 | landing updates the architecture map | **keep** | Not a dashboard issue; a landing-skill convention. Wrongly in `2.0-ui`. Move milestone; otherwise untouched by this spec. |
| #212 | repo-level automerge toggle | **close-no** | RESTRUCTURE §10 holds repo-default flips until the ops-console auto-land failure is understood; #358 limits the widget to issue/PR/note; the per-issue dot (#163) is the ruled hold mechanism; the repo default already renders read-only (`:1247-1251`). A config edit is `orch.json` by hand. |
| #211 | repo page journal + input box | **supersede** → §4.1 | Journal with gap rows and the note box shipped (PR #315). The journal was showing heartbeat; §4.1's filter is what makes it the view #211 described. `load earlier` explicitly not built (§4.9). |
| #209 | WHAT MOVED summary verb | **supersede** → §4.1, §4.7 | The tape's `landed`/`started`/`deferred` rows are landed/moved/not-started, derived at zero model cost; the section is deleted. |
| #208 | dashboard does not show activity | **supersede** → §4.1, §4.2, §4.4 | This spec is the research answer it asked for: tape, honest counts, flash. |
| #189 | live tail of the running session on the repo page | **supersede** → §4.6, §4.3 | One surface (issue page), rides the push, no timer, `a_log` derives the path. Repo page rows carry the brief sub-line instead. |
| #164 | dashboard-op notices cross-repo relationships unprompted | **close-no** | #343 (ruled do, in flight) deletes the automatic wake; RESTRUCTURE §10 accepts "cross-repo patterns must now be asked for" knowingly. The issue is that wake. |
| #159 | steer a repo-orch: `ask` wrong and inert | **close-done** | `core.ask_repo` is a journal append (`core.py:4160-4174`), the box renders and posts (`:1573-1583`), `nudge` stays the launch verb; #296 ruled `ask` permanent. All five acceptance items hold today. |
| #154 | tracking: six discrepancies | **supersede** → this table | #150, #151, #145 landed; #152 close-no, #140 close-no, #141 → §4.5. The index is replaced by this table. |
| #152 | WHAT CHANGED vs WHAT MOVED naming | **close-no** | Premised on the section existing; §4.7 deletes it. |
| #141 | deploy commit/behind readout dropped | **supersede** → §4.5 | Real and cheap; the feed has it; built as step 6. |
| #140 | review surface removed, four actions orphaned | **close-no** | #358: the operator never used approve/reject; a surface is earned by use. The four actions stay live for agents (that is #358's shape, not a gap). Blocking findings still render on the issue page and gate `merge`. |
| #101 | repo turn graph | **close-no** | #347 deleted `transitions` (ruled, "nothing curls it"); the session end time it needs is unrecorded; UX-REDESIGN §7 cut the activity graph. The tape and flash are the time view. |
| #100 | burndown / throughput chart | **keep** | Not contradicted: an on-demand forge read on a separate page, independent of the window; `history.jsonl` exists (150 KB, `~/orch/history.jsonl`). But it is a report, not what the operator described on 2026-09-16, and nobody re-asked since 2026-09-14. Keep, lowest priority, outside this build order; close if not asked for again. |
| #92 | mobile sketch: three-line rows, chips, wizard, repo page | **supersede** → UX-REDESIGN §3, #295 | Everything alive in it has a home: repo page and drill-down shipped; chips are the state pills; the three-line row was overturned by UX-REDESIGN §3.1's one-line rule; the Gantt stays cut; the wizard is #295; `DASHBOARD-DESIGN-92.md`'s caps were removed by verdict except the 30 KB repo zone. Nothing left to build under this number. |
| #13 | render controls from the `/act` verb table | **close-no** | #358 inverts it: the widget is a curated subset, a new verb must not grow a button. Its own 2026-09-17 comment says so. |
| #11 | CRUD on the web control surface | **close-no** | #358. Its 2026-09-13 verdict settled auth; the remaining "which verbs" question is answered: issue, PR, note. |

Tally: supersede 10 · keep 4 · close-no 7 · close-done 2.

---

## 7. Unverified — do not act on without checking

- `python3 -m orch.test_core` was **not run** for this report; the
  passed=1621 figure is the audit's.
- Whether `/events` survives Tailscale Serve for a phone viewer
  (`server.py`'s stream sets `Connection: close` and a 15 s heartbeat; the
  load-time `poll()` is the fallback either way).
- #343's PR is not open yet (issue OPEN, `agent-working`, live session in
  `wt/orch/issue-343` at 22:2x) — "in flight" is the session, not a diff.
  #346 is OPEN and unlabelled (held), as stated in the brief.
- #233's state (referenced by #241 row 4.1) — not read.
- The `brief` coverage (6 of 20) is a point measurement; whether
  issue-orchs journal often enough for the sub-line to be useful on most
  rows is a thing to look at after step 4 lands, not to assume.
- Widget line numbers are today's `main` (`4c959dd`); PR #361 landed at
  23:36Z and shifted them, so re-grep before editing.

---

## 11. Correction — 2026-09-17, after the operator saw steps 1-3

**The TAPE was the wrong thing.** It was built from a misreading of the
operator's phrase *"like a trade window"*, which this document turned into a
ticker-tape of events. Their actual meaning, stated plainly when they saw it:

> "No I didn't want to see them as notifications. I meant that the items
> underneath the listing are updated in realtime with status. Like a realtime
> status window."
>
> "I want the latest update of each issue shown as well as the latest update
> of each repo under the repo row."

So: **not a feed. Each row carries its own latest update, inline, and that
line stays current.** A trade window's rows tick in place; it is not a
scrolling log of trades.

### What that changes

| element | disposition |
|---|---|
| §4.4 TAPE (step 2, shipped in PR #414) | **remove.** A cross-repo event feed is not what was asked for and it occupies the top of the machine page, the most valuable space on it. |
| §4.1 step 1 (`recent` filter) | **keep, unchanged.** Still correct and now load-bearing for a different reason: the per-repo latest-act line reads from `recent`, so it must contain acts rather than tick heartbeat. Measured before: 99 of 104 rows were `observed`; after: 0. |
| §4.2 step 3 (working/awaiting/orphaned/stale counts) | **keep, unchanged.** Independent fix for the #386 misread. |
| §4.5 step 5 (changed-row flash) | **keep, and it is now secondary** — the flash marks that a live line moved. Useful, but it is not the feature; the inline line is. |

### The actual requirement

Every row shows its own most recent update, at every altitude:

- **Issue row** → that issue's latest update. The data already exists as
  `brief.text` in the feed and is already rendered as a sub-line — but **only
  on the repo page** (`widget.tpl.html:1645`). The machine page, which is the
  page an operator watches, does not render it.
- **Repo row** → that repo's latest act, from `orch.recent[-1]` (event +
  clipped note + age). **Not rendered anywhere today.**
- **Session row** → its latest activity line. Per the operator: *"issue rows,
  repo rows, session rows."* Unverified whether the feed carries a per-session
  equivalent; check before specifying.

All of it repaints already: `EventSource("/events")` (`widget.tpl.html:2222`)
pushes the whole feed on every tick and the page re-renders. **No new
transport, no new timer, no new server action.** The lines are missing, not
the liveness.

### Build order, replacing §5 steps 2 and 5

1. **Repo row gets a latest-act line** — `orch.recent[-1]`, event + clipped
   note + relative age, under the repo row. Reuses the existing `logMsg()` /
   `clip()` helpers from PR #414.
2. **Machine-page issue rows get the `brief` sub-line** the repo page already
   has. Same data, same clamp, one call site.
3. **Remove the TAPE** and its CSS.
4. *(then, optional)* changed-row flash, so a line that just moved is visible
   without staring.

Steps 1 and 2 are the feature. They are small because the data is already
derived and already pushed; only the render is missing.

### Note on how this was gotten wrong

The chain was: the operator said *"like a trade window"* → this document
named a section TAPE → an event feed got built. Each hop was a reasonable
reading of the previous one, and the result was a feature the operator did not
ask for, occupying the top of the page. The operator's own words named rows
("the items underneath the listing"), not a feed, and that detail survived
none of the hops. Recorded because the #395 measurement compares paths that
both contain this failure mode.

### 11.1 What is already implemented (measured 2026-09-17)

Most of the live-row feature exists. Only render sites are missing. Verified
against the live `status.json`:

| piece | state |
|---|---|
| `display_name` — the short description per issue (e.g. "derived hold unwired") | **built**, rendered |
| `brief.text` — the agent's own latest word per issue | **built**, rendered on the repo page only (`widget.tpl.html:1645`) |
| `brief.at` + `brief.stale` — currency of that line | **built**; #280's brief is a day old and correctly flagged `stale: true` |
| per-session `turns` / `idle_sec` / `live` | **in the feed**; render site unverified |
| `orch.recent[-1]` — a repo's newest act | **in the feed, rendered nowhere** |
| whole-feed push on every tick (`EventSource("/events")`, `:2222`) | **built** |

So the remaining work is three render sites, not a feature:

1. repo row → its latest act from `orch.recent[-1]`
2. machine-page issue row → the `brief` sub-line the repo page already draws
3. session row → its latest activity (confirm the field first)

**Degrade gracefully:** not every issue has a brief. orch#227 currently has
`brief: null`, so a missing line must collapse rather than render an empty
sub-line. The `stale` flag should be visible in some form — an old brief is
not the same as a current one, and the operator watching for progress needs
to tell them apart.
