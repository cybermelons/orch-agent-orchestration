# Dashboard information architecture

The stated information architecture for `widget.tpl.html`, written before the
code that implements it. Issue #88 asks for this document first, because a
second trimming pass gets the same result as the first one: #87 removed 20 of
528 muted elements at 390px and left the pill count and the key-value row count
unchanged. The page does not have too many words. The page has no primary.

This document decides the primary. Everything else here follows from it.

## 1. The primary job

**The page's primary job is: what needs me right now.**

Secondary, in order:

1. **Work on a specific issue** — drill in, read the brief, act on a review.
2. **What is the machine doing** — liveness, throughput, whether the loop is
   healthy.

### Why this primary and not the other two

The argument is not taste. It is what the feed actually contains.

`status.json` at 2026-09-14T11:00 carries 3 repos, 5 tracked issues, 2 units
awaiting review, 3 alerts, 45 agent sessions and **189 unattached sessions**.
The volume is in the machine-state material, and the volume is dominated by
rows that do not need the operator at all. If "what is the machine doing" were
the primary, the first screen would be 189 idle session rows and the two units
that are actually blocked would be below them.

The live feed also contains the decisive example. One of the 3 alerts reads:

```
error  deployment is 3 commit(s) behind origin/main (running b61de67 on main)
       — this page is NOT running current code; pull and restart
```

That is the page telling the operator that the page cannot be trusted. It is
not machine trivia and it is not issue detail. It is the clearest possible
instance of *what needs me right now*, and any architecture that does not put
that class of content first is wrong about what the page is for.

"Work on a specific issue" is real but it is **reached**, not landed on. The
operator arrives with an issue in mind, or arrives from a blocked thing on the
first screen. Either way the issue view is a destination, so it is secondary
and it is behind a drill-in.

"What is the machine doing" is a diagnostic. The operator asks it when
something looks wrong, which means it is answered by a **summary that is
always visible** plus **detail that is always collapsed**. It never competes
for the first screen.

### The rule this primary produces

**Attention is the sort key.** A row's position and weight come from whether
it needs a human, never from which section of the feed it arrived in.

This is the structural fix. #87 trimmed labels inside a layout that gave every
row equal weight, so the layout regenerated the problem. Under this rule a new
feature cannot land at equal weight with a blocked review, because there is no
equal weight left to land at — there are 3 named zones and a new thing must be
assigned to one.

## 2. The three zones

The page is 3 zones, top to bottom, in strict attention order.

### Zone A — NEEDS YOU

Everything that is waiting on a human decision, in one list, regardless of
which feed key it came from. Always first, always expanded, never collapsed.

Sources that merge into Zone A:

- `alerts` where `level` is `error` or `warn`
- unaddressed `escalated` rows in a repo journal — an agent asking a human to
  decide. They arrive through that same `alerts` key at `error` level, keyed
  `escalation` and carrying the row's `at`, so they need no zone rule of their
  own; what is particular to them is that they carry two controls rather than
  being cleared by the world changing: **file as issue**, the durable act
  that turns the row into tracked work, and **dismiss**, a per-browser
  display preference that clears nothing on the server
- `awaiting` — units awaiting review, with their review items and controls
- `repos[].issues[]` where `work_state` is `BLOCKED`, or `contended` is true
- `repos[].labels_missing` — a repo that cannot be driven

When Zone A is empty it says so in one line: `nothing needs you`. That line is
information, and it is the one case where the page should read as calm.

### Zone B — IN FLIGHT

The work orch is doing without help. One row per tracked issue, grouped by
repo. Collapsed detail per issue holds the brief, the key-value facts, the
sessions and the per-issue controls.

Zone B rows carry **one** state pill, not two. See section 4.

### Zone C — THE MACHINE

Orch itself, and the session ledgers. Summary line always visible; every
detail collapsed by default.

This zone is where #73 is answered. See section 5.

## 3. The first screen at 390px

The target is that at 390x844 the operator sees Zone A complete, or as much of
Zone A as exists, plus the start of Zone B.

**On the first screen:**

- The identity and trust line: `orch`, tick freshness, deploy commit and
  whether it is behind. One line. This is 12 words of text, and it is the
  line that tells the operator whether to believe the rest of the page.
- Zone A in full, with its controls.
- The Zone B header and the first issue rows.

**Behind a drill-in (tap to expand), not on the first screen:**

- Any issue's detail: brief, labels, ready, branch, commits, idle, PR,
  issue-orch liveness, sessions, workers, resume strings.
- Zone C in full: the orch panel, agent sessions, unattached sessions.
- `info`-level alerts. They are collapsed under a count in Zone C, because an
  `info` alert by definition does not need the operator right now. The 2 live
  `info` alerts today both say "waiting on you" about issues that are already
  Zone A rows, so at `info` level they are duplicates of Zone A content.
- The repo management controls: `+ ask`, `+ issue`, `+ track`, `unwatch`,
  `+ watch repo`.

### The controls question — #66 is not weakened

Collapsing a control behind a drill-in is **not** conditional rendering, and
this architecture does not reintroduce conditional rendering of controls.

The distinction that must hold:

- **Forbidden (#66):** a control that does not exist in the DOM because the
  feed said it was unavailable. The operator cannot tell that from a build
  with no such button.
- **Allowed:** a control that exists in the DOM, always, for every row, inside
  a disclosure the operator opens. The operator opens it and finds every
  control, with the unavailable ones disabled and carrying their reason.

Every control the page has today is still emitted for every row it applies to,
always, with `actAttrs()` / `actAttrsIf()` deciding only `disabled` and the
reason text. A disclosure is operator state. A capability gate is not. That is
already the existing distinction in the code — `display:none` on the `+ ask`
box is a disclosure and #66 deliberately left it alone.

## 4. The 24 render functions — survive, merge, go

The count of state pills per issue row goes from 2 to 1, and this is the
structural half of #87.

`state` and `work_state` rendered as two equal pills on every row, and
`work_state` rendered a third time inside the detail key-value grid. They are
not independent for the operator: they are two resolutions of one fact. One
pill on the row; `state` moves into the collapsed detail as a key-value row,
and the duplicate `work_state` key-value row is removed.

**Which fact the single pill carries is not "always `work_state`".** This
document said that in its first revision and it was wrong. `work_state` is the
finer axis and wins by default, but it is derived from the branch and the PR
alone, so there are 2 values it cannot express. `UNCLAIMED` is the one that
matters: with no commits and no PR, `work_state` returns `CLAIMED`
(`core.py:1312`) whether or not anyone holds the issue. A pill that preferred
`work_state` blindly would paint an unpicked issue exactly like one an agent
is mid-work on — which erases the "needs you" fact this architecture makes
primary.

So the rule is: `state` wins for `UNCLAIMED` and `ABANDONED`; `work_state`
wins otherwise. #87 landed this in PR #89 while this branch was open, and it
is kept verbatim across the rebase rather than re-derived.

**The rule has exactly one home: `pillState(i)`.** Zone A and Zone B both
render a pill for the same issue, so a rule written inline in one of them is a
rule the other cannot reach. The review of this PR caught that: Zone A's
`BLOCKED` filter and pill used the raw `work_state`, so an issue with a red PR
(`work_state` = `BLOCKED`) and an `agent-stuck` label (`state` = `ABANDONED`)
rendered `BLOCKED` in Zone A and `ABANDONED` in Zone B, on one screen, for one
issue. That pairing is not exotic — giving up on an issue labels it and leaves
the red PR open, so it is the *expected* combination.

Two zones disagreeing about one issue is the same equal-weight,
two-renderings-of-one-fact failure this architecture exists to remove. Any
future zone that shows a state pill calls `pillState`; none re-derives it.

### Survive unchanged

| function | zone | note |
|---|---|---|
| `esc`, `idsafe`, `mins`, `secs`, `clock`, `dot`, `$` | all | helpers |
| `act`, `actAttrs`, `actAttrsIf`, `inputAttrs` | all | the #66 control machinery, untouched |
| `briefLine` | B detail | renders the feed's brief, computes nothing |
| `worker` | B detail | |
| `paintTrack`, `trackFetch` | B controls | |
| `fetchCandidates`, `candidateRow`, `paintCandidates` | B controls | |
| `wire`, `disarm`, `poll`, `repaint`, `paintAge`, `mins_from_ts` | all | |

### Merge

| becomes | from | why |
|---|---|---|
| `needsYou(d)` | `awaitingSection` + the error/warn half of the inline `alerts` map + the BLOCKED/contended filter over `repos[].issues` | Zone A is one list from 3 feed keys. This is the merge the architecture exists to make. |
| `machineSection(d)` | `ledgerSection` + `unattachedSection` + the tick/host header material + the new orch panel | Zone C is one collapsed zone, not 3 sibling sections at page level. |
| `workers` into `session` | `workers` | a 3-line wrapper whose only caller is `session` |

### Go

| function | why |
|---|---|
| the inline `alerts` map in `render` | split: error/warn to Zone A, info to a Zone C count |
| the second `work_state` in `issueDetail`'s kv grid | the third rendering of one fact |

### New

| function | zone | why |
|---|---|---|
| `orchPanel(d)` | C | #73 — see section 5 |
| `repoHead(r)` | B | carries the forge — see section 6 |

`render` and `issueDetail` survive but are rewritten to the zone structure.
Net function count falls; the page stays one self-contained file with no
framework, and stays under the 256 KiB cap `build_widget` enforces (the
current build is a small fraction of it, and this change removes markup on
the balance).

## 5. The orch-level view — #73

Issue #73 folds in here: the page has repo-level and issue-level detail and no
view of orch itself. The facts exist in the feed and are today scattered
across the header, spread across the alerts, or not rendered at all.

`orchPanel(d)` is a collapsed panel in Zone C. Its summary line is always
visible. Expanded it renders, from the feed:

| fact | feed source | rendered today? |
|---|---|---|
| tick freshness, last run | `tick.minutes_since_last`, `tick.last_run` | yes, in the header |
| deploy commit, branch, behind count | `host.deploy` | yes, in the header |
| live orch count | `host.live_orchs` | yes, in the header |
| load, disk | `host.load`, `host.disk` | yes, in the header |
| dashboard-op liveness | `dashboard_op.alive`, `.prior_runs` | **no** |
| dashboard-op last event and time | `dashboard_op.last_event`, `.last` | **no** |
| the ack lease | `dashboard_op.key`, `.activity` | **no** |
| the digest | `dashboard_op.entries` | **no** |
| feed generated-at | `generated` | as an age only |

The trust line stays in the header, always visible, because it is the answer
to "should I believe this page". The rest moves into `orchPanel`. This is a
rendering change only: no new feed key, and `status.json` is not trimmed.

## 6. The three-repo, two-forge case

Today all 3 repos render identically and the backend only appears in a link.
The live set is:

| slug | repo | forge | URL host |
|---|---|---|---|
| `orch` | `cybermelons/orch` | GitHub | `github.com` |
| `openclaw-src` | `cybermelons/openclaw` | GitHub | `github.com` |
| `gita-lectures` | `cybermelon/gita-lectures` | Gitea | `gitea.local:3000` |

Note `cybermelons` against `cybermelon`: the owner differs by one character
across forges. An operator who reads only the owner/name string can misread
which forge a row belongs to, and the two forges have different consequences —
different auth, different reachability, and the `pull` versus `pulls` path
difference that #68 fixed and that fails silently when it is wrong.

**Decision: the forge is named on the repo row, as text, derived from the
URLs the feed already carries.**

`repoHead(r)` renders the forge label next to the repo name. It is derived
from the host of `issues[].url` / `awaiting[].url`, which is the same source
the links already use. No new feed key, and no abstract forge concept — the
label says `github` or `gitea.local:3000`, the concrete thing.

Consequence for the `pull` versus `pulls` difference: it stays exactly where
#68 put it. The page continues to use `pr_url` from the feed verbatim and
never builds a PR path itself. Naming the forge on the row makes a wrong PR
link **visible** instead of silent, which is the only page-side defence
available against a failure mode that produces a working-looking link.

## 7. What does not change

- No new capability. Every action the page can perform today it still
  performs: nudge, ask, new issue, track, assign, kill, unwatch, watch,
  tail, review, re-review, reply, approve item, reject item, merge, reload.
- `feed.py` and the JSON shape are untouched.
- No framework. One self-contained page, one `<style>`, one `<script>`.
- No page-side refresh cadence. The tick is the origin; `poll` keeps its
  existing behaviour and the reload button stays a plain page reload (#83).
- No authentication and no permission layer. The tailnet is the boundary.
- `public/widget.html` stays generated and gitignored; only the template is
  tracked (#76). The build stays pinned to its own checkout (#57).

## 8. How to judge the result

Measured at 390x844, against the same counts #87 was measured against:

1. Zone A is complete above the fold whenever Zone A has 3 or fewer rows.
2. The `error` deploy alert is in the first screen, always.
3. State pills per issue row: exactly 1, down from 2.
4. `work_state` appears once per issue, down from 3 times.
5. Every control that exists today still exists in the DOM for every row it
   applies to, with `disabled` and a reason as the only variation.
6. All 3 repos show their forge on the repo row.
7. The orch panel renders `dashboard_op` liveness, last event, ack lease and
   digest — 4 facts the page does not render today.
8. `build_widget.py` succeeds and reports under the cap.
