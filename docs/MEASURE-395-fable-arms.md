# orch#395: Fable writing its own code — what could and could not be measured

**Date:** 2026-09-16
**Status:** one arm measured, one arm unobtainable. The policy is unchanged.

**Snapshot: 2026-09-16T22:43:17.** The session logs under
`~/orch/state/sessions/` are live and still growing, so every count drawn from
them moves between runs. Each such figure below is quoted against that stamp
and is reproducible only with it. The merged-PR side is frozen into
`docs/measure-395-prs.json`, so the size axis does not drift at all; regenerate
the log-side figures with `python3 tmp_measure-395.py docs/measure-395-prs.json`,
which prints its own snapshot time.

orch#395 asked for one comparison:

- **Arm A** — current path: Fable designs → brief → Sonnet implements → PR.
- **Arm B** — Fable designs and writes the patch in the same session.

with the fence: *"Do not change the policy. Run one comparison and decide from
the number."*

This document reports Arm A from data that already existed, and explains why
Arm B could not be run without breaking that fence.

## 1. Arm B is unobtainable from here, and that is the first finding

**Fable has never run in orch.** Across every session log in
`~/orch/state/sessions/`, the `model=` field of an `init` line takes exactly
two values:

| model | init lines |
|---|---|
| `claude-opus-5` | 880 |
| `claude-sonnet-5` | 590 |

A third model string does appear in the logs, and it is worth recording
because it is not an init line: 13 occurrences of `claude-opus-4`, all inside
the failure text `There's an issue with the selected model
(anthropic/claude-opus-4-8)`, in `repo-orch.gita-lectures.log` and
`repo-orch.orch.log`. That is the known silent-spawn-death mode — a bad model
string that kills a session after a clean-looking spawn — not a Fable run, so
it does not disturb the conclusion here.

The string `fable` occurs in six logs: `issue-orch.orch.146.log`,
`issue-orch.orch.198.log`, `issue-orch.orch.395.log`, `dashboard-op.log`,
`repo-orch.garden-square.log`, `repo-orch.orch.log`. **None is a Fable
session.** Every one is an agent *citing* Fable as the designer — "The design
is fully settled by Fable's note", "by a Fable-level design review" — which is
Fable acting exactly as the current policy prescribes. The load-bearing
evidence for this section is the init-line table above plus `MODEL_BY_ROLE`
below, not the absence of the word.

`MODEL_BY_ROLE` (`orch/core.py:3659`) is the whole assignment:

```python
MODEL_BY_ROLE = {
    "dashboard-op": "sonnet",
    "repo-orch": "opus",
    "issue-orch": "opus",
}
```

There is no Fable role and no spawn path that produces one. Obtaining an Arm B
data point therefore requires adding a role or a model override to the spawn
path — a policy and plumbing change made in order to measure whether the policy
should change. That inverts the experiment, so it was not done.

**Arm A is reported below. Arm B is specified in §5 and left for the operator
to authorise.** One arm is not a comparison and nothing here is presented as
one.

## 2. What was measured, and at what granularity

Two facts already on disk, joined per issue:

- **cost** — every `total_cost_usd` the issue's sessions emitted, summed. This
  is the terminal `result` event rendered by `_translate_result`
  (`orch/runlog.py:174`) — the exact field orch#395 names. A session is resumed
  across wakes, so one issue accumulates several `result` lines; their sum is
  that issue's total Arm A cost, which is the unit comparable to one Arm B
  session.
- **size** — additions + deletions of that issue's merged PR.

Per-artifact attribution is **not** available: `total_cost_usd` is emitted once
per session, with no per-tool or per-message field. orch#395 records this limit
and accepts session-level totals as the right granularity; no attempt was made
to beat it.

Script: `tmp_measure-395.py`. At the snapshot above, 72 issues carry cost
records, 142 have merged-PR sizes, and **63 have both**. The 63 is the set
every figure in §3 is computed over.

**A known gap in the size axis.** Sizes are attributed by parsing the issue
number out of the PR's branch name, so 13 merged PRs on branches that encode
no issue (`fix/model-strings`, `rewrite/orch-package`, `feat/daemon-self-tick`
and others) are unattributable and contribute no lines. An issue whose work
landed on such a branch is therefore either dropped from the join or has its
lines undercounted while its full cost still counts — which would inflate its
$/line. The script prints this count on every run rather than dropping those
PRs silently. This matters most to the small bucket (n=6), which is the
thinnest figure in §3 and the one §5 offers as a baseline.

## 3. Arm A result

```
total Arm A spend (joined):    $458.50
median cost per issue:         $5.31
median lines per issue:        198

by unit size (lines changed):
  small   (<50)          n=  6  median $ 2.44  median $/line  0.070
  medium  (50-199)       n= 26  median $ 5.21  median $/line  0.042
  large   (200-499)      n= 19  median $ 4.89  median $/line  0.015
  xlarge  (>=500)        n= 12  median $10.38  median $/line  0.014

Spearman rho (cost vs lines):  +0.415  (n=63)
```

Two things follow, and only two.

**Arm A cost is not flat in unit size.** rho = +0.415 over 63 issues, and the
median rises from $2.44 (small) to $10.38 (xlarge). A size-threshold rule of
the kind orch#395 proposes therefore has a real axis to stand on — cost does
vary along the dimension the hypothesis picks out. This does **not** show the
threshold sits at 50 lines, or anywhere else.

**Marginal cost per line falls sharply with size** — 0.070 $/line small against
0.014 $/line xlarge, a 5× difference. Arm A carries a fixed overhead per issue
that large diffs amortise and small ones do not. That overhead is the handoff:
the brief, the dispatch, the judging of the report. It is the quantity Arm B
would remove, and it is largest, proportionally, exactly where orch#395
predicts Arm B wins.

**This is consistent with the hypothesis. It does not confirm it.** The
measured effect is Arm A's cost structure alone. Whether Fable-writing-directly
costs less than $2.44 on a small unit is precisely the unmeasured quantity, and
nothing above constrains it — Fable-class output could plausibly exceed the
handoff overhead it saves. The hypothesis was stated in advance in the issue
body and is **not** adjusted here.

## 4. The design-heavy regime is real and currently invisible to this join

At the snapshot above, **nine issues carry cost records but no merged PR,
totalling $54.93** — computed by the script, which lists the full set on every
run. The three expensive ones are all research or audit work that produced no
code:

| issue | cost | kind |
|---|---|---|
| #286 | $26.05 | Research: fold issue-management bot patterns into the skills |
| #262 | $14.32 | Ponytail audit: is there a smaller orch? |
| #270 | $5.45 | Research: is orch reimplementing Managed Agents? |

The remaining six (#184 $3.28, #387 $1.87, #146 $1.13, #296 $1.03, #268 $0.97,
#363 $0.83) are smaller and include issues still in flight at the snapshot, so
this set grows as the night's sessions land.

These sit squarely in the "design is the hard part" regime, and they are
excluded from §3 by construction — the join needs a diff size and they have
none. Their existence is worth recording: a non-trivial share of spend goes to
issues where the deliverable is a judgment, not a patch, and the Arm A/Arm B
distinction is least meaningful there because there is no handoff to collapse.

## 5. What Arm B would take

To obtain a genuine second arm, in the fence's spirit rather than around it:

1. A single Fable-model session, authorised explicitly, on one issue whose
   design is the hard part and whose diff is expected under ~50 lines.
2. Record the same three quantities: summed `total_cost_usd`, wall time, and
   PR outcome (merged clean / review findings / rework rounds).
3. Compare against the small-bucket Arm A median of **$2.44** — the number this
   document contributes.

One session, one issue. The comparison stays cheap, which was the point.

## 6. Which regime this data point sits in

Per orch#395's own caution: the hypothesis predicts the answer depends on unit
size, so a single comparison tests one point on a curve, not the curve.

**This work is not a point on that curve at all.** It is a characterisation of
Arm A's cost *across* all four size regimes (n=63, spanning <50 to ≥500 lines),
with no Arm B observation anywhere. It establishes the baseline against which a
future Arm B point would be read, and it identifies the small-unit bucket —
median $2.44, n=6 — as the thinnest part of that baseline and thus the place a
single Arm B run would be hardest to interpret. **A future Arm B run on a small
unit should expect to be compared against only six Arm A observations.**

## 7. Confound worth naming

Tonight's throughput came from many short per-issue sessions, and the sessions
that died repeatedly were those holding the most context — which reads as
support for orch#395's "case against" item 2. But the deaths correlate with
`ratelimit utilization` climbing 0.71 → 0.79 across the night (orch#184), not
observably with context size. **"Small sessions won" is not clean evidence
here**, and is not claimed as such.

## 8. What was not done

- The policy in `~/.claude/CLAUDE.md` is **unchanged**. No role prohibition was
  edited, no size threshold was introduced.
- No cost-tracking framework was built. The measurement is one throwaway
  script over logs that already existed.
- The comparison was not re-run across many issues for statistical power.
