# Dashboard Design — issue #92

Implementable spec. Derived from #88 IA (three zones, attention-first), #66
(never hide a control), and the #92 operator verdicts. A builder implements
this literally; anything not stated here is out of scope, not an invitation.

Rendering rule, absolute: the page **renders** status.json. It computes no
state of its own. Every value below is either a field in the feed or a pure
string function of feed fields. No fetching, no timers, no derived
persistence beyond the two localStorage keys named in §9.

---

## 1. The three-line row

Every row on every page — dashboard and repo page — is the same three lines.
One shape, everywhere. No row type has four lines, none has two.

```
<work>      what this unit of work IS          (identity)
<status>    where it stands right now          (fact)
<next>      what happens next, and whose move  (the only actionable line)
```

### 1.1 Sources

`brief` in the per-issue feed already carries the prose. It is the preferred
filler for all three lines when present. Treat `brief` as either:

- an object with keys `work` / `status` / `next` → use directly, one per line;
- a plain string → put it on `<work>`, derive `<status>` and `<next>` from the
  table in §1.3.

The builder must handle both. `brief` is agent-written prose and is never
trusted as markup: escape it, then clamp each line to 140 characters with a
trailing ellipsis. A clamped line keeps its full text in `title=`.

### 1.2 `<work>` line

Always: `#<issue> <title>`, with `title` clamped. If `title` is empty, use
`#<issue> (untitled)`. Never blank — a row with no identity is unfindable.

On the dashboard, prefix with the repo slug: `<slug> #<issue> <title>`. On the
repo page the slug is redundant and is omitted.

### 1.3 `<status>` and `<next>` by state

The distinguishing rule the earlier design got wrong: `work_state` returns
`CLAIMED` for an unpicked issue, so it cannot alone separate unclaimed from
mid-work. **Resolve state first from `state`, then refine with `work_state`.**

Precedence, evaluated top to bottom, first match wins:

| # | Condition | `<status>` | `<next>` |
|---|---|---|---|
| 1 | `state == UNCLAIMED` and `startable` | Unclaimed, ready to start | Next tick will claim it |
| 2 | `state == UNCLAIMED` and not `startable` | Unclaimed, not startable | **Needs you** — <blocker> |
| 3 | `state == ABANDONED` | Abandoned after <prior_runs> run(s) | **Needs you** — decide: retry or drop |
| 4 | `work_state == BLOCKED` | Blocked <idle_min>m | **Needs you** — answer below |
| 5 | `work_state == REVIEW` and `pr` | PR #<pr> in review | **Needs you** — review and merge |
| 6 | `work_state == LANDED` | Landed via PR #<pr> | Closing next tick |
| 7 | `contended` | Contended — <sessions> sessions on one issue | **Needs you** — pick one owner |
| 8 | `work_state == ACTIVE` and `orch_alive` | Working, <commits> commit(s), <idle_min>m idle | Agent continues |
| 9 | `work_state == ACTIVE` and not `orch_alive` | Stalled <idle_min>m, no live orch | Next tick will respawn |
| 10 | `work_state == CLAIMED` and `orch_alive` | Claimed, starting up | Agent begins this tick |
| 11 | `work_state == CLAIMED` and not `orch_alive` | Claimed, nothing running | Next tick will respawn |
| 12 | anything else | State unknown (`<state>`/`<work_state>`) | **Needs you** — inspect on the forge |

`<blocker>` for row 2 is, in order of availability: the first entry in
`labels_missing` that applies, else `"no label match"`. Row 2 exists because a
non-startable unclaimed issue is a configuration fault, not a queue item.

Rows whose `<next>` begins with **Needs you** are exactly the rows that sort
into NEEDS YOU (§4). This is the single definition of attention. There is no
second list, no flag, no override.

### 1.4 When `brief` is absent

Not an error, not an empty row. Fall back to §1.2 + §1.3 verbatim — those are
computed from structural fields that always exist. Then append to `<status>`
the marker `· no brief`, so the operator can tell a quiet agent from a broken
one. Never render the word "undefined", "null", or an empty line.

### 1.5 Below the three lines

Anything else is `<details>`, collapsed by default (`<details>` with no `open`
attribute). One per row, summary reads `more`. Contents, in order, each line
omitted if its field is absent: branch, pr_url, url, activity, prior_runs,
sessions, labels. Nothing in here is actionable; actionable belongs on `<next>`.

---

## 2. Chip taxonomy

A chip encodes state **in its shape**, not in a label/value text pair, and not
in color alone. Form is `<kind>=<id>` plus a shape class.

### 2.1 The chips that exist

| Chip | Text | Meaning |
|---|---|---|
| `pr` | `pr=45` | a pull request |
| `iss` | `iss=45` | a related issue |
| `run` | `run=3` | prior_runs count |
| `sess` | `sess=2` | concurrent sessions |
| `idle` | `idle=12m` | idle_min |
| `forge` | `gh` / `gitea` | which forge, from repo config |

That is the complete list. A builder adding a seventh chip kind is adding a
feature; it needs its own issue.

### 2.2 States a chip may carry

`open` · `merged` · `closed` · `draft` · `related` · `contended` · `stale`

### 2.3 Visual distinction — no color-only encoding

Each state is distinguished by **two** channels, one of which is never color:

| State | Border | Glyph prefix | Color (third channel only) |
|---|---|---|---|
| open | solid 1px | `○` | neutral |
| merged | solid 2px | `●` | positive |
| closed | dashed | `×` | muted |
| draft | dotted | `◌` | muted |
| related | solid 1px | `→` | neutral |
| contended | double | `‼` | warn |
| stale | dashed | `⋯` | warn |

The glyph is real text inside the chip, so the chip survives a monochrome
screen, a screenshot, and a colorblind reader. Remove the stylesheet entirely
and the chips still read correctly — that is the test.

Chips are never the only place a fact appears. Every chip restates something
the three lines already said. A chip is a scanning aid, not a data channel.

---

## 3. The wizard

Lives at the very top of the page, inside NEEDS YOU, inside NEEDS YOU's 8KB.

### 3.1 Structure

```
NEEDED   one line: what the agent needs from you
DID      one line: what it already tried
RESULT   one line: what happened / why it stopped

  (A) <option text>
  (B) <option text>
  (C) <option text>
  ( ) other: [____________________]

  [ Confirm ]
```

Lettered options are radio inputs. The free-text "other" is always rendered
and always enabled — the agent's options are frequently incomplete and the
operator must never be trapped in a closed set.

### 3.2 Where options come from

From `brief` on the blocked issue. Expected shape: `brief.options`, an array
of strings. Rules:

- More than three options: render the first three, and the rest fold into a
  collapsed `<details>` labelled `more options`. Letters continue D, E, …
- Zero options, or `brief.options` absent: render the free-text field alone,
  with the three A/B/C radios **present but disabled** and their label reading
  `agent offered no options`. Per #66 the controls appear regardless.
- Options are escaped prose. Never HTML, never markdown.

`NEEDED` / `DID` / `RESULT` come from `brief.needed` / `brief.did` /
`brief.result`. Any missing one renders as `—` rather than collapsing the
slot; a wizard that changes height between ticks is a wizard the operator
misreads.

### 3.3 What Confirm writes

Confirm never acts and never polls. It composes the text the next tick will
consume, and hands it to the operator to place. For the per-issue case the
destination is the **issue journal**, via the existing `a_reply` mechanism:
operator prose into a GitHub issue comment / `tea comments add`, carrying a
marker line in the `orch/<actor> <event>` namespace so a later reader can tell
an operator answer from an agent journal row.

Composed body:

```
orch/operator answer
issue: <slug>#<issue>
choice: <letter> <option text>       (or: choice: other)
<free text, if any>
```

A fresh issue-orch reads the issue journal on re-entry, so the answer is
picked up next tick. No new transport, no new file, no new endpoint.

The page is static and cannot POST. Confirm therefore:

1. renders the composed body into a readonly `<textarea>` beneath the wizard;
2. renders the exact one-line CLI next to it, selectable, per forge —
   `gh issue comment <n> --repo <owner/name> --body-file -` or
   `tea comments add <n> --login <login> --repo <owner/name>`;
3. copies the body to the clipboard if `navigator.clipboard` is available.

The copy button is rendered always. Where the clipboard API is unavailable it
is **disabled with the visible reason** `clipboard unavailable — select the
text above` — it is never hidden (#66).

### 3.4 After pressing Confirm

The wizard does not vanish and does not clear. It gains a banner:

```
Answer composed — paste it, then it lands next tick (<tick+1>).
```

The radios stay selected and stay enabled, so a mistaken choice is corrected
by choosing again and pressing Confirm again. The wizard for that issue
disappears only when a later status.json no longer reports it BLOCKED. This
is deliberate: the page has no way to know the paste happened, so it must not
pretend it did.

### 3.5 Multi-question — queue, not stack

**Verdict: queue. One wizard visible at a time.**

Two blocked units means two questions; showing both halves the attention each
gets and doubles the 8KB pressure. The wizard renders the first blocked issue
in NEEDS YOU sort order, with a header counter:

```
Question 1 of 3          [ Skip → ]
```

`Skip →` advances to the next question without answering and wraps around.
Confirm also advances. The other blocked issues still appear as ordinary
three-line rows in NEEDS YOU beneath the wizard — nothing is hidden, only the
interactive form is singular.

`Skip →` is rendered always; with one question it is disabled with the reason
`only one question`.

---

## 4. Sort order — everywhere

No list anywhere is sorted by repo name. Repo-alphabetical is the invention
this section exists to forbid.

### 4.1 NEEDS YOU

Sort key, descending urgency:

1. `contended` (two agents fighting corrupts work — always first)
2. `state == ABANDONED`
3. `work_state == BLOCKED`
4. `work_state == REVIEW`
5. `state == UNCLAIMED and not startable`
6. everything else that matched a **Needs you** `<next>`

Ties inside a band: larger `idle_min` first — the thing that has been waiting
on you longest is the thing you have failed longest. Final tiebreak, and only
as a determinism guarantee: `slug`, then `issue` ascending.

### 4.2 IN FLIGHT

Descending recency of movement: smaller `idle_min` first. What moved most
recently is what you are most able to reason about. Ties: `commits`
descending, then `slug`/`issue`.

### 4.3 THE MACHINE

Repo blocks ordered by `ok` false first (a repo with a problem outranks a
healthy one), then by live work descending (`counts` of active issues), then
`slug`. Inside a block, live orchs before dead ones, each by `idle_min`
ascending.

### 4.4 Repo page

Active list: §4.2 rule. Inactive list: most recently changed first, using
`activity` where present, else `idle_min` ascending.

Sorting is stable across ticks given identical data. Never sort by insertion
order of feed keys — the feed's key order is not a contract.

---

## 5. Empty states

Three, each a single rendered line, each stating the fact and the consequence.
Never an empty zone; an empty zone is indistinguishable from a broken one.

| Zone | Condition | Text |
|---|---|---|
| NEEDS YOU | no row matched a **Needs you** `<next>` | `Nothing needs you. <n> issues in flight, next tick <hh:mm>.` |
| IN FLIGHT | no ACTIVE/CLAIMED/REVIEW rows | `Nothing in flight. <n> unclaimed issues are startable.` |
| THE MACHINE | no repo reported history this tick | `No repo activity since <generated of previous tick>.` |
| Repo page inactive list | no merged/closed/dropped rows | `No finished work yet in this repo.` |

If both NEEDS YOU and IN FLIGHT are empty and every repo is `ok`, NEEDS YOU
additionally renders `All clear.` as its first line. That is the only
celebratory string on the page.

Where a count is unavailable, the number renders as `?`, never as blank and
never as `0`. A false zero is worse than an admitted unknown.

---

## 6. Navigation

Two pages. No third, no modal, no tab bar.

### 6.1 Dashboard → repo page

Every row carries a link on its repo slug (dashboard) or on a `repo ↗` chip
(when the slug is omitted). The link target is the repo page for that repo.

Implementation: the repo page is the **same HTML file** with a hash route,
`#/repo/<slug>`. One file, one CSP, one size budget to defend. No second
document to keep in sync.

### 6.2 Back

A `← Back` control at the top-left of the repo page, always rendered. It
returns to the dashboard (`#/`) and restores the dashboard's previous scroll
position. It is **not** a repo picker, and no repo dropdown appears anywhere.
The operator arrives at a repo page from a row, having a reason; browsing
repos is not a supported motion.

Browser back must also work — use `location.hash` and a `hashchange`
listener, never `history.pushState` with synthetic state.

### 6.3 Repo page contents, in order

1. **Header** — slug, forge chip, `ok` state, path, `← Back`.
2. **Repo alerts** — `labels_missing`, `live_orchs` above expectation, `ok`
   false reason. Each one line. Empty → `No repo alerts.`
3. **Active issues** — three-line rows, §4.2 order. This is the bulk.
4. **`repohistorysummary`** — the single "since you last looked" line
   (per the #92 cut of the added/changed/removed triple), plus the three
   events retained from the timeline cut: opened / last orch action /
   current wait. This section lives **here and only here**; promoting it to
   the dashboard breaks #88's primary and is forbidden.
5. **Inactive issues** — merged / closed / dropped, in a collapsed
   `<details>`, with the filter controls.
6. **Orch runtime** — `orch{alive,key,prior_runs,recent}`, collapsed.

### 6.4 Filters

Filters (merged / closed / dropped) exist on the repo page's **inactive list
only**. They are checkboxes, all rendered always, all on by default. They
never touch the active list and never hide a row the attention sort placed —
filtering an attention row would defeat the one guarantee the dashboard makes.

---

## 7. Size budgets

| Surface | Cap | Note |
|---|---|---|
| NEEDS YOU zone | 8 KB | includes the wizard, entirely |
| IN FLIGHT zone | 25 KB | |
| THE MACHINE zone | 36 KB | |
| **Dashboard total** | **69 KB** | sum of zones; shared chrome/CSS/JS counted separately below |
| Repo page (`#/repo/*`) | **30 KB** | one repo's worth of rows |
| Shared chrome — CSS + JS + shell | 25 KB | |
| **Page size** | no cap | single file, gzip-agnostic, measured on the generated artifact. The former 124 KB budget (and the build's 256 KiB warning) were removed by operator verdict 2026-09-16: neither had a recorded justification, and the page is served from loopback to one operator. The build still prints the size. |

The repo page gets 30KB precisely so it does not become the place features
escape #88's caps to. It is subordinate; it is smaller than IN FLIGHT.

When a zone would exceed its cap, it **truncates from the bottom** — the sort
order guarantees the bottom is the least urgent — and renders a final line:

```
+<n> more not shown (zone at cap)
```

on the dashboard, and for the repo page a link to the forge's own issue list.
Truncation is always disclosed. Silently dropping rows is the one failure the
size caps must not produce.

---

## 8. Non-issue wizard answers

The per-issue case is settled: issue journal via `a_reply`. Repo-level and
machine-level questions have no issue to comment on.

**Verdict: the same journal mechanism, aimed at a designated journal issue.**

- **Repo-level** question → the repo's `orch` meta-issue, an issue labelled
  `orch-journal` in that repo. The composed body carries `repo: <slug>` and no
  `issue:` line. repo-orch reads its own journal issue on entry, exactly as
  issue-orch reads the issue's.
- **Machine-level** question → the `orch-journal` issue in the orch repo
  itself. Body carries `scope: machine`.

Rationale in one line: inventing a second transport for two rare cases doubles
the number of things that can silently fail to be read.

If no `orch-journal` issue is discoverable for the needed scope, the wizard
still renders in full, Confirm still composes the body, and the copy control
is **disabled with the reason** `no orch-journal issue in <slug> — create one
labelled orch-journal`. The operator learns the exact repair. Per #66, nothing
is hidden.

---

## 9. Client-side state

Exactly two `localStorage` keys, both per-viewer conveniences that the page
renders correctly without:

- `orch.dash.lastSeen` — the `generated` timestamp of the last tick viewed,
  used solely for the "since you last looked" line. Absent → that line reads
  `First look.`
- `orch.dash.filters` — inactive-list filter checkbox states.

Both reads and both writes are wrapped in try/catch. Nothing else persists.
Wizard selections do **not** persist — a stale answer to a resolved question
is a trap.

---

## 10. What a builder must NOT do

1. **Do not sort anything by repo name.** §4 names a key for every list.
2. **Do not hide a control.** Ever, for any reason. Disable it and render the
   reason in visible text (#66). A vanished control is indistinguishable from
   one never built.
3. **Do not distinguish anything by color alone.** Every chip state carries a
   glyph and a border treatment (§2.3).
4. **Do not use `work_state` alone to decide claimed-vs-unclaimed.** It
   returns `CLAIMED` for an unpicked issue. Read `state` first (§1.3).
5. **Do not make Confirm act.** It composes text for the journal. No fetch, no
   POST, no polling, no optimistic UI, no "saved!" without a paste.
6. **Do not show two wizards.** Queue, one at a time (§3.5).
7. **Do not promote `repohistorysummary` to the dashboard.** Repo page only.
8. **Do not add a repo picker, dropdown, or tab bar.** Rows link down; Back
   comes up (§6.2).
9. **Do not let filters touch the active list** or any row NEEDS YOU placed.
10. **Do not add a fourth zone or a third page.** Both are features and need
    their own issue against the caps.
11. **Do not silently truncate.** Every capped zone discloses its remainder.
12. **Do not compute state on the client.** If a value is not in status.json
    or a pure string function of it, it does not render.
13. **Do not load anything external.** No fonts, no CDN, no icons. CSP allows
    no external scripts; inline everything.
14. **Do not add a fourth line to a row.** Below the three lines is
    `<details>`, collapsed, or it is nothing.
15. **Do not render `undefined`, `null`, `NaN`, or an empty required line.**
    Missing count → `?`. Missing prose → the §1.3 fallback.

---

## 11. Out of scope (moved or cut in #92)

Gantt turn graph (own issue) · full issue timeline (cut to 3 events) ·
added/changed/removed triple (cut to one line) · dirlisting (undefined) ·
any live action from the page · any notion of "unattached sessions" on the
dashboard — the 189 of them need nobody and are THE MACHINE's business at
summary level only, never as rows.
