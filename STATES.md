# orch — the state machine, and its correctness

Derived from `core.py` (`work_state`, `issue_state`, `alive`). Originally audited
at commit `e59dfa5`, updated for the fixes landed in #7 (findings 3, 4 and 8),
and updated again for #261 to track the function renames (`unit_state` →
`work_state`, `worker_alive` → `alive`) and to add the landing transition
(finding 11). This describes what the code actually computes, not what the
README says.

## The machine

Two independent state functions, plus a third axis that is deliberately not a
state.

### Issue

`issue_state(world, n)` — pure over GitHub labels, first match wins:

    agent-stuck             → ABANDONED
    ¬agent-working          → UNCLAIMED
    else                    → CLAIMED

### Unit

`work_state(world, repo, n)` — first match wins:

    PR MERGED               → LANDED
    PR OPEN ∧ green         → REVIEW
    PR OPEN ∧ red           → BLOCKED
    PR OPEN ∧ ¬green ∧ ¬red → CHECKING
    ¬PR ∧ commits ahead     → ACTIVE
    ¬PR ∧ ¬commits          → CLAIMED

There is no `unit` parameter: units are recorded, never derived, and there is
nothing smaller than the issue for the state functions to see (`core.py`
docstring on `work_state`).

### Liveness

`alive(key)` — reads the session ledger row for `key` and checks whether its
recorded `pgid` still exists (`_pgid_alive`).

Orthogonal. It never feeds `work_state`. This is the design's intent — state
is about the work, liveness is about the actor — but see finding 7.

### Landing (control flow, not state)

Two more labels exist beyond the three `issue_state` reads: `auto-land` and
`no-auto-land` (`L_AUTOLAND`, `L_NO_AUTOLAND`, `ORCH_LABELS` in `core.py`).
Neither feeds `issue_state` or `work_state`. They feed one function,
`auto_land_on(labels, repo_default)`, which resolves whether an issue at
REVIEW may be merged automatically:

    no-auto-land ∈ labels        → False
    auto-land ∈ labels           → True
    else                         → bool(repo_default)

`repo_default` comes from `repo_auto_land(repo_path)`, a total, non-raising
reader of the repo's own entry in `orch.json`'s `auto-land` key — false on
any absence, unreadable file, or parse failure. `auto_land_on` is the single
home for this precedence; see finding 11 for the transition it gates and how
`LANDED` is actually reached.

## Findings

Numbered independently of issue #7, which groups them differently: this
document's findings 3, 4 and 8 are that issue's items 1, 2 and 3 respectively,
and its item 4 (absent CI reads as passing CI) is discussed under finding 2.

### 1. Totality and determinism — holds

Both functions are total over their inputs and side-effect-free given a fixed
`World`. `pr_for` collapses multi-PR branches deterministically (OPEN > MERGED
> first), so branch → PR is a function, not a relation. No unreachable branch,
no fallthrough.

### 2. `pr_green` and `pr_red` are not complements — load-bearing

Both call `_rollup` independently:

| rollup | green | red | state |
|---|---|---|---|
| `[]` / `None` (no CI) | true | false | REVIEW |
| `ROLLUP_UNREADABLE` (read failed) | false | false | **CHECKING** (finding 10) |
| all SUCCESS/SKIPPED/NEUTRAL | true | false | REVIEW |
| any FAILURE/ERROR/TIMED_OUT/CANCELLED | false | true | BLOCKED |
| PENDING only | false | false | **CHECKING** |

Delete either asymmetry and `CHECKING` becomes unreachable. The test suite
pins all four cases (`no_ci_is_green`, `null_is_green`, `pending_notgreen`,
`pending_notred`). Best-specified part of the machine.

Consequence worth a conscious sign-off: a repo with *broken CI config* emits
an empty rollup, which reads as green, which reads as REVIEW — i.e. landable.
Absent CI and passing CI are indistinguishable by construction.

What is no longer indistinguishable, as of finding 10: a rollup that could not
be *read*. That used to collapse into the same `None` and buy a green light on
the strength of a failed subprocess. It now reads CHECKING.

### 3. FIXED — state was composed from three different moments

Originally `unit_state` mixed three views of the world, because `_rollup` shelled
out a fresh `gh pr view` on every call and `pr_green`/`pr_red` each called it
independently — two network calls per BLOCKED determination, able to observe two
different CI states.

The rollup is now fetched once in `World.load` (only for PRs whose `state` is
`OPEN` — a MERGED PR short-circuits to LANDED before any rollup is read) and
stored on the World as `self.rollups`, keyed by branch. `_rollup(branch)` is a
pure dict lookup that performs no I/O.

| sub-fact | source | when |
|---|---|---|
| `world.pr_for` | `World.load` snapshot | once per repo per tick |
| `world.pr_green` / `pr_red` | `self.rollups` lookup | **same `World.load` snapshot** |
| `work_mtime` | `git log` | **live, at evaluation time** |

What this resolved: `pr_green` and `pr_red` can no longer disagree about the same
PR, so CHECKING is never reported for a unit that is already green, and the
network cost of a state evaluation dropped from two `gh pr view` calls per open-PR
unit to one per open PR per tick.

What remains, accepted: `work_mtime` is still read live, so a PR opened after
`World.load` still reads as ACTIVE/CLAIMED for the remainder of that tick. This is
one snapshot plus one live read rather than three moments; every tick re-derives,
so it self-corrects. `pr_green`/`pr_red` semantics are unchanged — see finding 2.

### 4. FIXED — `STALE` was documented and styled but could not be produced

`core.py` says so deliberately, and that comment stays: *"There is deliberately no
STALE: staleness is a conclusion, and conclusions belong to whoever holds
context."*

It had survived as dead vocabulary in:

- `README.md` — documented as a state with a reclaim policy
  (`no commit in 30 min AND no live worker -> reclaim, 3 attempts, then abandon`)
  that the code never implemented
- `README.md` — a machine-task caveat about going STALE
- `widget.tpl.html`, `public/widget.html`, `widget.html`,
  `public/orch-canvas.html` — CSS styling it alongside BLOCKED/ABANDONED

All removed. The state table, the phantom reclaim policy, and the `.STALE`
selectors are gone; only the explanatory comment in `core.py` remains.

### 5. Transitions are unspecifiable as constraints

State is re-derived and never stored, so backwards edges are legal and
self-correcting:

    ACTIVE  → CLAIMED    branch force-pushed away
    REVIEW  → CHECKING   new commit on an open PR
    BLOCKED → CHECKING   checks re-run

There is no illegal transition to assert on — only anomalies worth surfacing.
A transition table is fine as *documentation*, wrong as *validation*.

The forward path, for reference:

    UNCLAIMED → CLAIMED → ACTIVE → CHECKING → REVIEW → LANDED
                                       ↕
                                    BLOCKED
    any issue → ABANDONED (label)

`REVIEW → LANDED` is drawn here as if it were one more derivation like the
rest, but it is not: it is the one arrow in this diagram that is an agent
merging a PR rather than a function re-reading the world. See finding 11.

### 6. `CHECKING` is a trap state

Nothing exits it if CI never reports. `LANDED` and `ABANDONED` are the only
terminals, and neither is reachable from a hung CHECKING without human or
agent action.

Consistent with "no thresholds in the tick" — but it means the machine's
liveness depends entirely on an external judge existing. Today no judge runs.

`ABANDONED` is terminal by *convention*, not construction: it is a label,
which a human or agent can remove.

### 7. DEFECT — `needs_attention` reads one axis only

`tick.py` counts issue-level UNCLAIMED/ABANDONED, plus unit-level BLOCKED and
`contended`. Nothing consults `alive`.

So the cell (ACTIVE, alive=false) — dead mid-flight, the case the whole
recovery scenario is about — contributes **zero** to the wake decision. This
is the digest/wake gap, seen from the state-machine side.

### 9. `compute_conditions`' condition 7 — landed but still claimed

`tick.compute_conditions` (`orch/tick.py`) has a seventh condition alongside
the six discussed above: `work_state == LANDED ∧ state == CLAIMED`. It is
deliberately not gated on `orch_alive` or `idle_over` — a merged PR is a
derived fact, not an inference, so it needs no threshold. This names the
(LANDED, CLAIMED) cell in the product table below, the one case #7's audit
found had no name: an issue-orch that died between merge and label-drop
leaves its issue holding `agent-working` with nothing left to notice. The
tick only reports it; issue-orch clears its own label on the wake this
produces.

### 8. FIXED — cosmetic

`wt` was assigned in `work_state` (then still named `unit_state`) and never
read. Removed. Hoisting the rollup
(finding 3) also orphaned `World._pr_number`, whose only caller had been the old
network `_rollup`; it was removed too.

### 10. A derivation that cannot resolve emits an explicit unknown

The rule: **when a derivation cannot produce a true answer it emits an explicit
unknown, never a plausible value.** A fallback that is indistinguishable from a
real answer is worse than a loud failure, because the consumer acts on it.

Why this is the convention and not a preference: six of the seven observed feed
defects were silent wrongness rather than loud failure. The feed reported
success while being wrong — `ok: true` while serving another repo's issues, a
digest going backwards, a ready issue simply absent with no condition firing,
links that looked right and 404ed. `DESIGN.md` treats a failed oracle as
condition 4 and designs it to be LOUD. These were the exact opposite: the
failure was absorbed at the derivation and reported as health.

An audit of the feed and state derivations found **20 sites** that emit an
indistinguishable fallback and **15 sites** that already do it right — they
emit falsy, `None` or `-1`, and the consumer reads "unavailable".

The convention already exists in this code. It is simply not enforced:

- `feed.py`'s `web_base`/`pr_url` (landed in #63/#68) emits no link rather than
  a guessed one that looks like a URL and 404s, and its comment records that
  both call sites read a falsy url/pr_url as "no link available".
- `tick_json`'s comment says "never fake a green light".

**Enforced at the derivation, not at the consumer.** The derivation is the only
place that knows it failed. Once the fallback is on the wire the information is
gone and the consumer cannot recover it.

This does not conflict with the standing guardrail, *"Never add a state field to
fix a bug; find why the derivation is wrong."* An unknown sentinel is not a new
state field. It is the derivation telling the truth about its own failure —
which is the derivation being made correct, not being worked around.

#### Fixed here

| site | was | now |
|---|---|---|
| `core.py` `_fetch_rollup` | `None` for all six failure modes, conflated with "no CI" | `ROLLUP_UNREADABLE` on the failure paths; a genuine empty result stays `None` |
| `core.py` `pr_green` / `pr_red` | unreadable rollup returned green, gating merges | both return `False` for the sentinel, so the unit reads CHECKING |
| `feed.py` `commits` | `0` for a failed or timed-out `rev-list`, identical to "produced nothing" | `-1`, following `idle`'s precedent in the same function; a real zero stays `0` |
| `feed.py` repo `ok` | `r.get("ok", True)` — a row that never stated `ok` read as healthy | `r.get("ok", False)` |
| `feed.py` subagent walk | exception yielded `workers: []`, identical to "no workers" | still `[]` (wire shape held) plus `workers_unreadable: True`, and the widget renders it |

A fix that stops at the wire is half a fix. Both unknowns added here are
carried through to a renderer: `commits` `-1` draws as an em dash at all three
call sites, and `workers_unreadable` draws as "workers unreadable" rather than
as the empty block an empty list already produced. Enforcing the unknown at the
derivation is necessary because only the derivation knows — but if no consumer
reads it, the operator still sees the plausible value, which is the whole
defect. Review of this change caught exactly that gap in the `workers` half.

No-CI-is-green is unchanged and still deliberate (finding 2). The four pinned
cases still hold: a literal JSON `null` rollup from a backend that *answered* is
"no CI", not "unreadable", so it stays green. The sentinel is produced only on a
failed read.

#### Remaining known sites

Filed, not fixed:

- `server.py` returns `ok: true` for a kill with no ledger row, and for a tick
  it has not proved started.
- `core.py` `issue_field` returns `""` and `issue_comments` returns `[]` for an
  unknown issue — indistinguishable from a real empty value.
- `core.py` ledger read does `row.get("started", str(now()))`, fabricating a
  start time of "now" for a row missing one, which makes an old session look
  freshly started.
- `tick.py` swallows a widget/status rebuild exception, leaving a stale
  `status.json` served as current.

### 11. The one transition that reaches `LANDED` is prose in a skill file, not a function here

Every state above is a pure derivation: re-read the world, get an answer,
never store it. `LANDED` breaks that pattern in one respect — it is the only
state whose entry is also an *effect performed on the world* (a merge), not
just an observation of it, and the code that decides whether to perform that
effect lives outside `core.py` entirely.

`work_state` already reports `LANDED` once a PR shows `MERGED` (`## The
machine`, above). What this finding adds is the transition that gets a PR
there: an issue sitting at `REVIEW`, with `auto_land_on(labels, repo_default)`
returning `True`, is reviewed and then merged by the issue-orch agent
following `agents/skills/issue-landing/SKILL.md`. That merge is what produces
the `MERGED` PR state that `work_state` then reads back as `LANDED`. Nothing
in `core.py` performs the merge; `core.py` only supplies `auto_land_on` and
the labels it reads.

The skill's five rules (`issue-landing/SKILL.md:23-33`), restated here so this
document does not require opening that file to understand the machine:

- `auto-land` present → review, then merge on green, even if the repo default
  is off.
- `no-auto-land` present → do not review, do not merge, even if the repo
  default is on.
- Neither label → the repo default decides.
- Neither label and no repo default → do not review, do not merge.
- Both labels present → `no-auto-land` wins; holding is the safe direction.

These five rules are exactly `auto_land_on`'s three-branch precedence,
spelled out over all label combinations — the function and the prose agree
because the function is what the skill's own bash reads at runtime.

**"Held" is not a state; it is REVIEW with a fact about it.** When
`auto_land_on` returns `False`, the issue-orch agent does not merge and does
not journal a wait-and-retry; it leaves the PR open and exits. `work_state`
still reports `REVIEW` — there is no fourth PR-open outcome and no "HELD"
value anywhere. A blocking review finding holds that issue at `REVIEW` too,
but by a different mechanism: as of orch#408 (ruling orch#148 option B),
`core.review_blocks_merge` derives the hold fresh from the PR's last
`orch:review:v1` block on every merge attempt, and `core.merge_pr` refuses
accordingly — no label is written or read for this, and `auto-land` itself
is untouched. Before and after a blocking finding, `work_state` reads
`REVIEW`, same as the `auto-land`-absent case above; only the reason a
merge won't happen differs, and neither reason is a state function.

**Why this cannot be folded into `work_state` or drawn as a state-to-state
arrow.** The merge is an effect an agent performs after reading markdown, not
a value a function returns. `core.py`'s own header says derived facts are
"re-read every tick and never stored"; a merge is the opposite of that — a
one-time side effect on GitHub's state, performed by a stateless agent
session that exists only for the duration of one tick's dispatch and holds no
memory between runs. There is no function to point at that takes
`(REVIEW, auto_land_on=True)` and returns `LANDED`; there is only an agent,
prompted by a tick, executing `gh pr merge`. Treating `REVIEW → LANDED` as a
checkable transition in the way finding 5 already rules out for the rest of
the machine would claim a guarantee — that the transition is total, pure, or
even reliably attempted — that nothing here provides. If the agent session
dies before merging, or before dropping `agent-working`, the issue simply
sits at `REVIEW` (or lands in the finding-9 leak cell) until the next tick
retries it.

## The product table

The prose design hides this by treating liveness as a footnote. State ×
liveness is where the interesting cells live:

| unit state | alive | reading |
|---|---|---|
| CLAIMED | true | staked, pre-first-commit — normal briefly |
| CLAIMED | false | died before producing anything |
| ACTIVE | true | healthy |
| ACTIVE | false | **dead mid-flight** — the busted-session case |
| CHECKING | any | waiting on CI; alive=true is mildly odd |
| REVIEW | true | still running after opening a PR — suspicious |
| LANDED | any, issue CLAIMED | **the leak cell** — PR merged, `agent-working` never dropped; issue-orch died between merge and label-drop and the label holds a slot with nothing left to notice it. Named by condition 7, not gated on alive: the merged PR is a fact, so a live owner about to drop the label fires it too, harmlessly (self-clears next tick) |
| BLOCKED | false | needs a fix round |
| any | 2+ live sessions | **contended** |

Six of eight rows are invisible to the current wake logic (finding 7).

## Verdict

The state functions are correct: total, deterministic, well-tested at the
boundary that matters (finding 2).

Every defect is at a seam, not in the machine:

- **3** — temporal inconsistency in composition. **Fixed:** rollup hoisted into
  `World.load`. The remaining seam (`work_mtime` read live) is accepted.
- **4** — dead vocabulary in README and widget. **Fixed:** deleted.
- **8** — dead `wt` local, plus the orphaned `_pr_number`. **Fixed:** deleted.
- **7** — the state × liveness product is computed nowhere. **Open**, tracked by
  #6. Blocks the recovery scenario.
- **11** — the `REVIEW → LANDED` transition is an agent effect, not a
  derivation, and previously undocumented here. **Documented, not fixed:**
  there is nothing to fix — it is honestly not a function, and this document
  now says so instead of implying otherwise.

Finding 4 in the issue (absent CI reads as passing CI) was signed off as
intended behavior and deliberately left alone; the tests pinning it stay.

None require a redesign.
