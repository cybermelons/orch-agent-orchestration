# How much landed unattended, 2026-09-16 01:46Z–04:02Z

Report for orch#277. Answers the operator's question: of tonight's 20
merged PRs, how many landed with no human in the loop *at the merge
instant*.

**Headline: 18 of 20 were agent landings; 12 of those merged after the
last manual session went idle; the median unattended time from PR open
to merge was 10.6 minutes. But the window is not the clean "unattended"
span the question assumes, and the deploy that was supposed to receive
all 20 was broken throughout.**

All times UTC unless marked local (EDT, UTC-4). Method notes and the
corrections to this issue's own premises are in §6.

---

## 1. The idle boundary — two kinds of presence, not one

The question says "after the operator stopped driving manual sessions."
That boundary exists and is sharp, but it only bounds *one* kind of
presence. Separating the two is the difference between an honest number
and an inflated one.

**Session presence ended 2026-09-16T02:23:27Z** (22:23:27 local), the
last typed operator prompt: `Just do the changes yourself`. The
session's final assistant turn closed at 02:25:12Z. The next typed
prompt anywhere on the machine is 04:20:14Z — 18 minutes *after* the
window closes. So the unattended session gap inside the window is
**02:23:27Z → 04:02:16Z, 1h 38m**.

Method: enumerated every `*.jsonl` under `~/.claude/projects/*/`,
extracted non-meta `type:user` messages with real text (excluding slash
invocations and `tool_result` payloads), and classified per session by
first-prompt shape — a role prompt beginning `# issue-orch` /
`# repo-orch` / `# dashboard-op` marks an agent. Operator sessions are
conversational and typo-laden. Cross-checked against directory path.
The boundary is an inference from transcripts, not a logged fact.

Two refinements worth keeping:

- **Operator sessions live in two places.** The local terminal session
  (`-home-kiri-orch/245e5bfd-…`, 31 typed turns) *and* four
  `-home-kiri-orch--claude-worktrees-bridge-cse-*` sessions — Claude on
  web, bridged to devhost. The latter look orch-spawned from the
  directory name but carry typed operator prose. Missing them would
  have moved the boundary.
- **Shell history is dead evidence.** `~/.zsh_history` ends at epoch
  `1787089518` (2026-08-18); `~/.bash_history` mtime is Aug 18. Neither
  covers the window. No command-line corroboration was obtainable —
  an absence of evidence, not evidence of absence.

**Label and thread presence never stopped.** The operator kept steering
the queue through the whole session-idle gap:

| UTC | Issue | Act |
|---|---|---|
| 03:01:24Z | #267 | `Removed.` |
| 03:03:40Z | #254 | `labeled no-auto-land` — the hold PR 264 merged past |
| 03:18:33Z | #270 | `**Tooling: use the deep-research skill for this.**` |
| 03:37:47Z | #274 | `labeled priority` |
| 03:52:53Z | #230 | `**Operator ruling: decompose. Four atomic steps.**` |
| 03:53:14Z | #258 | `**Answer to this issue's open design question…DECOMPOSE**` |
| 04:01:40Z | #259 | `**CORRECTION: the design artifact already answers this…**` |
| 04:04:49Z | #259 | long design comment, `play/pause` left deliberately open |
| 04:06:04Z | #259 | `labeled agent-ready` — re-nomination |

So "unattended" is true of sessions and false of the queue. Autonomy
here means *the machine executed without a human driving a session*, not
*without a human deciding*. Several of the 20 answer directives typed
inside the window — `Repo orch should queue things up` (01:48:15Z) and
`Nudge and build rule.` (01:49:14Z) are the proximate cause of the
triage widening that PR 249 shipped.

**Discriminating operator label acts from agent ones.** Author login is
useless: every act in the window is `cybermelons`, because agents use
the operator's own `gh` credential. The usable test is *shape* — an
agent claim writes `agent-ready` off and `agent-working` on in one
paired act. A standalone label with no pairing is an operator hand. This
matters concretely: `no-auto-land` on #230 at 02:26:52Z is **paired**
with an `agent-ready` removal, so it is a session self-gating its own
PR; `no-auto-land` on #254 at 03:03:40Z stands alone, so it is the
operator. Reading both as operator acts would have invented an
intervention that did not happen.

---

## 2. Per-PR attribution

`gh pr view --json author,mergedBy` returns `cybermelons` for **all
20** — the shared credential again. That field carries zero attribution
signal and no figure in this report rests on it. Attribution comes from
journal rows (`state/repos/orch/orch.jsonl`, 1056 rows, plus the
`orch/issue-orch` rows in each issue thread), cross-checked against the
GitHub label timeline and systemd where a non-participant source
existed.

`unattended` = merged by an issue-orch session under the repo's
`auto-land = true` default, with no operator act at the merge.

| PR | issue | attended? | reviewer | filed→merged |
|---|---|---|---|---|
| 238 | #235 | **not an agent landing** | none | 6m03s |
| 240 | #236 | **not an agent landing** | none | 4m57s |
| 243 | #233 | unattended | clean | 37m31s |
| 246 | #244 | unattended | 1 round, no blocking | **11m03s** |
| 245 | #229 | unattended merge, **fix needed operator** | clean, 2 rounds | 5h22m |
| 249 | #232 | unattended | not recorded | 53m45s |
| 242 | #224 | unattended | **2 blocking, fixed** | 5h53m |
| 248 | #226 | unattended | clean | 5h52m |
| 251 | #220 | unattended | clean +1 non-blocking | 6h16m |
| 253 | — | unattended | not recorded | n/a |
| 255 | #250 | unattended | clean after 1 fix round | 22m59s |
| 263 | #256 | unattended | **3 blocking, all real** | 21m33s |
| 260 | #252 | unattended, **scope operator-amended** | **1 blocking, fixed** | 32m41s |
| 264 | #254 | **merged past a live operator hold** | clean, 2 non-blocking fixed | 36m01s |
| 266 | #259 | unattended | forced a correction | 36m35s |
| 265 | #261 | unattended | 2 wrong cells + mutant hole | 37m24s |
| 271 | #258 | unattended | **2 rounds; caught a reverted merge** | 44m31s |
| 272 | #269 | unattended | **2 blocking, both correct** | 28m34s |
| 275 | #273 | unattended | **2 blocking bugs, fixed** | 24m54s |
| 276 | #274 | unattended | not recorded | 24m30s |

**PRs 238 and 240 are not agent landings.** Their branches are
`docs/artboards` and `docs/artboard-rule`, not `issue-<n>`, and the
GitHub timelines for #235 and #236 contain **zero label events** —
verified directly, no `agent-ready`, no `agent-working`. They never
entered the agent loop. Both merged 5–7 seconds after opening. The
issue body's list of 20 does not distinguish them, and pooling them
inflates every autonomy figure.

So: **18 of 20 were agent landings. 17 of 18 merged clean under
auto-land. One (PR 264) merged past a live hold.**

The reviewer was not a rubber stamp. Blocking findings on 242, 260,
263, 271, 272, 275 — and PR 271's reviewer caught that the session's own
squash had **reverted merged PR 264**. That is the downstream catching
what every level above it missed, which is the argument for keeping the
reviewer on the expensive model.

---

## 3. Time from file to merge

Pooling all eight same-window pairs gives a median of ~24.7 min, but
that number is built on a mis-pairing in the issue body (see §6) and on
two non-agent PRs. Corrected:

**Unattended landings after the session-idle boundary (n=12), PR open →
merge:** median **10.6 min**, min 1.2 (PR 253), max 24.4 (PR 265).

**Unattended landings after the boundary with the issue filed in the
same window (n=10), filed → merge:** median **30.6 min**, min 21.6
(PR 263), max 44.5 (PR 271). Two of the twelve drop out: PR 251's #220
was filed at 20:07:29Z, before the window, and PR 253 answers no issue.

The full distribution is bimodal, and the mode nobody quotes is the slow
one. Four PRs — 242, 245, 248, 251 — answer issues filed around 20:07–
20:45 local and took **5h22m to 6h16m** from filing to merge. Those
issues sat through the evening unqueued; the fast 11-to-44-minute
figures all belong to issues filed *after* repo-orch's triage was
widened. The speed is real but it measures a queue that had just been
unblocked, not a steady state.

The best case in the issue body — "#244 filed → reviewed → merged in ~25
minutes" — is actually **11m03s** (filed 01:56:16Z, merged 02:07:19Z),
the fastest agent landing of the night.

---

## 4. What auto-land actually decided

`.orch.toml` carries `auto-land = true`, so the default is *review, then
merge*. Of the 18 agent landings, **17 merged on a clean or
fixed-to-clean review with no operator step**. The exceptions:

**PR 264 merged 60 seconds past a live `no-auto-land`.** The operator
set the label at 03:03:40Z; the merge went through at 03:04:40Z. This is
the sharpest datum in the set and it is neither cleanly autonomous nor
cleanly human-gated.

It was **not a race.** The session's own account, verbatim:

> **I did not re-read the labels immediately before the merge call.** I
> treated the label read as wake-entry state and the CI/base read as
> merge-instant state. The label is exactly as racy as the base — this
> wake proved that, because I caught a stale base by re-checking and
> missed a stale label by not re-checking.

It read the labels at wake entry, before 03:03:40Z, and never
refreshed — while in the same wake it *did* re-check the base and caught
itself nine commits behind despite `mergeStateStatus` reading CLEAN. It
then self-reported rather than hiding it:

> **I merged past a `no-auto-land` label. Reporting it rather than
> quietly clearing it.**

and deliberately left both labels in place, reasoning that whoever set
the hold may have had a reason it could not see and that clearing the
evidence was the wrong move. That is the behaviour the design wants
from a session that has just done the wrong thing.

**PR 245's merge was unattended but its fix was not.** The PR merged
clean under auto-land at 02:07:40Z, and the session then deliberately
left #229 open, because merging did not stop the false positives. The
remaining step was one `git apply` into `~/.claude` — a write-protected
path no agent can reach. It needed the operator, who did it; #229 closed
at 02:25:02Z with `Fix applied and verified in effect.`

**PR 260's scope was operator-amended.** repo-orch first refused to
spawn #252 as a duplicate of the already-closed #232, then reversed on
an operator amendment: `do NOT leave prioritize a stub`.

**The gate semantics moved mid-window.** PR 272 merged 03:34:49Z as
`d2f3ad3` and made sessions re-read labels at the merge instant. So 18
of the 20 merged under the *old* wake-entry gate and only **275 and 276
merged under the new one**. Two notes:

- PR 266 merged at 03:14:44Z, *before* 272, but voluntarily applied the
  discipline anyway: *"This is the orch#269 discipline, after PR 264
  merged 60 seconds past a `no-auto-land`."* The fix propagated by
  courier before it propagated by code.
- PR 276 is the clean test that the contract took. repo-orch
  deliberately withheld the stale-base warning to see whether the
  merged contract would carry itself. It did: PR 276 merged as
  `12e7717`, whose parents are `6f213dd b3e3b1b`, and `6f213dd` **is**
  an ancestor — confirmed with `git merge-base --is-ancestor`. So 276
  did not merge behind, even though its `baseRefOid` still records the
  older `d2f3ad3`.

**Method warning, load-bearing for any successor:** `baseRefOid` is the
base a PR *records*, not the base it *merged against*. On PR 276 the
field was stale while the merge was correct; on PR 264 the field read
CLEAN while the PR was nine commits behind. Either direction, the field
is not the fact. Use ancestry.

---

## 5. What did NOT land — the honest half

**Four issues no amount of autonomy could move, all terminating at an
operator act.** repo-orch's condition 5 fired on these for sixteen
consecutive wakes without a spawn that could help:

- **orch#230** — PR 247 is **OPEN, CI SUCCESS, mergeStateStatus
  CLEAN**, verified directly. It is green and held by nothing but a
  `no-auto-land` its own session set as its final act, saying *"the
  hold is the operator's to clear."* Note the label lives on **issue
  #230**, not on PR 247 — a successor grepping the PR's own labels will
  find nothing.
- **orch#257** — blocked on PR 247. Its session verified that
  `git show main:orch.json` returns `fatal: path orch.json does not
  exist in main`, committed a plan, and exited rather than building on
  a file that has not landed.
- **orch#262** and **orch#268** — both **finished**. Reports delivered
  to their threads. They stay open only because they carry stale
  `agent-working` labels that neither repo-orch nor any session may
  clear, so they fire condition 5 forever.

One hold on one PR is therefore blocking three issues, and two more are
pinned by label bookkeeping alone. A throughput figure that omits these
five overstates the result.

**Two escalations remain unanswered:**

- `2026-09-15T22:47:06-04:00` — *"PR 247 no-auto-land now blocks
  orch#230, orch#257, orch#259. Merge or clear the hold."*
- `2026-09-15T23:06:37-04:00` — *"orch#254 merged PR 264 60s past a
  no-auto-land set 03:03:40Z. Your call whether that hold mattered."*

The second is notable: the *defect* was fixed autonomously within 30
minutes (PR 272), but the *question* — was that hold deliberate? — only
the operator can answer, and it is still open.

**Four of the 20 repair the same night's own work.** Real throughput,
not net progress:

- **PR 272** (#269) fixes the gate defect PR 264 exposed 30 min earlier.
- **PR 253** fixes a literal that PR 249 reintroduced into
  `repo-management/SKILL.md:156`, which PR 251 had just removed —
  regression and repair inside one merge cycle.
- **PR 260** (#252) writes the `prioritize` strategy that PR 249
  shipped as a deliberate stub.
- **PR 255** (#250) repairs the deploy pipeline the night's own config
  edits wedged.

Borderline, not counted: 271 reconciles grants 264 had just changed;
265 had to absorb `p0/p1/p2` from 263. So of the 18 agent landings,
**14 are net new capability** and four are the loop repairing itself.

**The deploy was broken for the entire window.** This is the most
consequential correction in the report and it comes from systemd, a
non-participant source. `orch-pull` failed continuously:

```
Sep 15 20:33:20  orch-pull: REFUSED -- working tree at /home/user/orch is dirty, skipping fetch
  … 15 consecutive refusals …
Sep 15 22:08:50  orch-pull: REFUSED -- working tree at /home/user/orch is dirty, skipping fetch
Sep 15 23:14:14  orch-pull: FAILED -- fast-forward merge to origin/main did not apply
  … 12 consecutive failures …
Sep 16 00:09:31  orch-pull: FAILED -- fast-forward merge to origin/main did not apply
Sep 16 00:19:35  orch-pull: up to date with origin/main, nothing to do
```

Local time: broken from at least 20:33 through 00:19:35, recovering
only **17 minutes after the window closed**. Every one of the 20 PRs
merged to `origin/main` while the live checkout could not receive it.
The agents were landing correct work into a repository the running
instance was not reading — which also means repo-orch spent the window
executing a prompt that predated the skill it was supposedly following,
and said so: *"either the skill's survey-and-promote step is not yet in
effect because the deployment is stuck at 43f6f4a and I am still running
the old prompt, or it shipped without that step."*

---

## 6. Corrections to this issue's premises

The body was unusually complete, and verifying it surfaced five errors.
Recording them because the report's numbers depend on them.

1. **`#252→#249` is not a causal pair.** PR 249 merged 02:14:51Z; #252
   was filed 02:25:06Z, **ten minutes later**, and is a duplicate of the
   already-closed #232. PR 249 closes #232 and #239. #252 was actually
   answered by **PR 260** (branch `issue-252`). Computing filed→merged
   on the body's pairing yields **−10.2 minutes**, which is how the
   error surfaced.
2. **238 and 240 are not agent landings** — no label events on #235 or
   #236 at all. See §2.
3. **The stall is mis-dated, mis-described, and overlaps the window.**
   The body places it at "00:03–00:14" and reads it as *before* the
   window. Those are **local** times; in UTC the divergence failures are
   04:04:29Z and 04:09:31Z, *after* the window closed. The real outage
   spans essentially the whole window (§5).
4. **`.tick.lock` never went stale and never stopped ticks.** The
   message `orch-pull: SKIPPED -- .tick.lock held, a tick is running,
   will retry next interval` is a **success** path, exit 0, and fired
   eight times because a tick was legitimately running. No stale lock,
   no hand-clearing, zero lock-error rows in either journal. The genuine
   ~90-minute quiet period is in the *journal* — no repo-orch row
   between 23:50:04Z and 01:20:23Z — while ticks fired normally
   throughout. That is repo-orch declining to write, not a system stall.
5. **The "~25 minute" best case for #244 is 11m03s** (§3).

**On sourcing.** repo-orch drove most of this window and told this
session so, unprompted, flagging its own 1056 journal rows as
"testimony from a participant, not neutral evidence." That was correct
and it changed the method: every load-bearing claim here was
re-verified against a non-participant source where one existed —
systemd for the deploy and tick history, the GitHub label timeline for
claim events, `git merge-base` for ancestry, the raw transcripts for the
idle boundary. Three places where its rows overstate: it credits its own
couriered brief for PR 266's correct behaviour where the PR thread
credits orch#269 and PR 264 instead; it wrote "RESOLVED EXACTLY AS I
PREDICTED, WITH NO ACT FROM ME" about a non-intervention; and its
in-flight cap arithmetic rests on a "parked vs driving" distinction it
admits is load-bearing and that cannot be checked from outside. Against
that, it recorded its own missed relationship, a lost journal row, an
omission from its own `--covered` list, and a propagated-wrong gate
figure — self-criticism that corroborates rather than undermines.

**One data-quality note.** The gate figure `passed=1265` circulates
through many journal rows and is stale in most: it is what an unrebased
checkout measures. Sessions that rebased first reported 1307, 1336,
1356, 1380, 1400, 1408, 1443, 1449, 1459. Three sessions independently
hit 1265, recognised it from a couriered warning, and rebased before
measuring — one was 5 commits behind, another 58. This report's own
gate run is `passed=1465 failed=0` on worktree
`wt/orch/issue-277` at `12e7717`, which equals `origin/main`. Any
throughput claim keyed to the gate number should state which tree.

---

## 7. The three numbers

- **Total merged in the window: 20.** Agent landings: **18**.
- **Merged unattended — no operator act at the merge instant: 17 of
  18.** Of those, **12 merged after the session-idle boundary** of
  02:23:27Z. The one exception is PR 264, which merged past a live
  `no-auto-land`.
- **Median unattended time-to-merge: 10.6 min** (PR open → merge, n=12
  post-boundary). Filed → merged, same set with in-window filing:
  **30.6 min** (n=10).

Net new capability: **14 of the 18 agent landings**, the other four
being the loop repairing its own regressions.

---

## 8. What the loop could not do without a human

The machine was good at everything downstream of a decision and unable
to move anything that *was* a decision. It planned, decomposed,
dispatched, reviewed itself with real teeth — six PRs had blocking
findings, one reviewer caught a squash that had reverted an
already-merged PR — rebased when it found itself 58 commits behind,
caught its own stale-checkout gate figure from a couriered warning, and
fixed a defect in its own merge gate within 30 minutes of committing it.
Seventeen of eighteen landings needed nothing from the operator at the
merge. What it could not do was clear a hold it had set, clear a stale
`agent-working` label on a finished issue, answer whether a hold was
meant to stop a merge, write to a path outside the repo, or resolve a
design fork — and each of those, not capability, is what actually
stopped work: one unmerged green PR blocking three issues, two finished
issues pinned open by label bookkeeping, two escalations unanswered.
The binding constraint on this repo tonight was never throughput.

That suggests the next thing to build is not more autonomy but narrower
gates on the acts that only a human can perform: let a session clear a
label it set itself, let a finished report-only issue release its own
`agent-working`, and make an unanswered escalation visible as a blocker
with a count of what it is holding — because tonight one label held
three issues for sixteen wakes and nothing in the system said so at a
glance. And fix the thing that made all of it invisible: the deploy that
could not fast-forward for the entire window, so the operator was
watching a dashboard served from a checkout that had not moved since
`43f6f4a` while twenty PRs landed behind it.
