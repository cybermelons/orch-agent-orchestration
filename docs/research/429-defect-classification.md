# Is there a construct that closes the class?

Research answer to orch#429. Filed 2026-09-17.

**Verdict: hypothesis A, with one qualification that is worth more than the
count.** Of 47 real defects across the 2.0 open set and the issues closed on
2026-09-17, 19 (40%) would have been prevented by one of the four candidate
constructs and 28 (60%) are genuinely their own edge. No construct closes the
class. The patches are the system.

The qualification: the 40% is not evenly spread. It concentrates almost
entirely in **orch's own coordination machinery** — merge paths, recovery
predicates, backend selection — and is nearly absent from everything else.
That makes one construct worth building anyway, on a much narrower footing
than the issue proposed. See "What is worth building".

## Method

Every issue open in 2.0 and every issue closed on 2026-09-17 was read for its
**defect mechanism** — what existed, and what path missed it — not its title.
Each was placed in exactly one bucket by an independent classifier, with the
model cases from the issue body supplied as calibration.

Three buckets are excluded from the denominator:

- **F (39)** — features, new surfaces, research questions, tracking issues.
  Nothing was broken, so no construct could have prevented them. Most of the
  low-numbered closed set is this. Counting them as "genuine edges" would
  have inflated hypothesis A; counting them as preventable would have
  inflated B. They are neither.
- **D (3)** — deliberate, signed-off unsoundnesses. Absent CI ≡ passing CI
  (STATES.md finding 2) and `RepoAdapter`'s no-fallback are decisions, not
  defects, per the issue's own constraint.

Three issues (#227, #268, #280) appear in both the open and closed sets and
are counted once; each was classified identically in both passes. 89 unique
issues were classified in all.

The remaining **47 are real defects**, and they are the denominator.

## The count

| bucket | n | share of defects |
|---|---|---|
| C1 single chokepoint per concern | 7 | 15% |
| C2 claimed effect carries its measurement | 3 | 6% |
| C3 recovery is one predicate | 8 | 17% |
| C4 operator uses the agents' paths | 1 | 2% |
| **construct-preventable subtotal** | **19** | **40%** |
| A genuinely its own edge | 28 | 60% |
| **defect total** | **47** | |
| *excluded: F features* | *39* | |
| *excluded: D deliberate decisions* | *3* | |

**60% is the answer to the operator's question.** Three fifths of real
defects are not instances of a missing construct. They are authorization
boundaries, external API limits, forge capability gaps, host resource limits,
doc/code drift, and plain bugs.

## What the 28 edges actually are

They are not a residue of hard cases that a cleverer construct would absorb.
They are structurally different in kind, and the largest groups are:

- **Authorization and identity boundaries** (#413, #337, #405). orch#413 is
  the clearest: `agents/repo-orch.md` lines 182, 236 and 783 are three false
  sentences, and no agent may edit its own grant file. No model of merges,
  recovery or measurement touches this. It is a permissions fact.
- **External API and forge limits** (#404, #401, #390, #237). GitHub caps a
  label description at 100 characters. `tea` can list issue titles but not
  read bodies. `gh issue edit` applies `--remove-label` even when
  `--add-label` fails. These are properties of other people's software.
- **Host resource limits** (#197, #204, #184). OOM kills and account usage
  limits. This research session was itself killed mid-analysis at ~14:58 on
  2026-09-17, alongside two unrelated sessions (orch#427, orch#430) in the
  same minute, all three with `prior_runs=0` — the orch#204/#197 signature,
  and a reminder that the largest single category of genuine edge is the
  machine underneath, which no model of merges or predicates can express.
- **Doc/code drift** (#360, #172). Documentation outliving the code it
  describes — which, as §"The counterweight" shows, is the failure mode of
  impossibilities themselves. (#398 and #408 also read as drift but are
  classified C1 and C2 respectively, because in both a real mechanism was
  reached past rather than merely mis-described.)
- **Plain bugs with no bypass structure** (#340 CSS track priority, #374 a
  heredoc parser, #370 a test racing a live filesystem).

A construct that claimed to prevent these would be a construct that prevents
nothing.

## Where the 40% concentrates — the real finding

The construct-preventable defects are not scattered. Sorted by **subsystem**,
which cuts across the buckets — #427 is C3 and #318 is C4, but both live in
the merge and review path:

| subsystem | C-bucket defects |
|---|---|
| merge / review path | #421, #427, #372, #318, #366, #417, #146 |
| recovery / liveness predicates | #425, #362, #336, #365, #411, #228, #143 |
| backend selection | #423, #398 |
| everything else | #416, #382, #408 (3 of 19) |

**Sixteen of the nineteen land in three subsystems** (seven on the merge and
review path, seven in recovery predicates, two in backend selection). Those
three are exactly orch's coordination core — the parts where several
independent mechanisms must agree about one fact. Everywhere else in the
codebase, the constructs predict almost nothing.

That is why the answer is A rather than B, but also why it is not a pure
negative. The class is real; it is just much smaller than "all our issues",
and it is localised.

## The counterweight, verified

The operator's second comment identified the decisive risk: orch already
tried a make-it-impossible model, and it eroded. This was checked against the
tree rather than the docs.

`orch/core.py:8-10` states the impossibility verbatim:

> Derived facts (GitHub labels, PR state, CI rollups, pgid liveness) are
> re-read every tick and never stored — there is nothing to invalidate.
> Recorded facts (ledger rows, journals) may lie; nothing here gates on them.

The last clause is **false**, as the #262 audit found. Five recorded facts
are stored and read back: `state/tick-spawns.json`, `state/tick-dash-wake.json`,
`state/.last-observed-digest`, the `consolidated --covered` journal rows, and
the STANDING ORDER row.

The practiced rule is narrower than the stated one: a recorded fact may
**suppress or defer** for ≤TTL, and may never **authorize**. That narrower
rule is sound and is what the code obeys. But it is not what the code says,
and the gap between them has been sitting in a module docstring long enough
that #366 (`--covered` honoured on one event type, accepted on all) grew in
exactly that gap.

**An impossibility that gets weakened is worse than an honest exception**,
because the weakened version still reads as a guarantee and nobody re-checks
it. This is now measured, not asserted.

### The same erosion, inside the strongest candidate

Construct 1's best evidence is `RepoAdapter`, which the issue calls a working
instance. It largely is: `core.py:1878-1889` raises `AttributeError` rather
than falling back across backend tables, deliberately. 28 call sites go
through `adapter_for()`.

Two things qualify it.

1. **`adapter_for_entry` falls back to `gh` itself.** At `core.py:1992`,
   an unknown entry yields `backend = "gh"`. That is the #423 failure —
   silently choosing the GitHub backend for a repo whose backend matters —
   living *inside* the chokepoint, documented as deliberate.
2. **Backend selection has a second entrance.** `feed.py:208` and
   `feed.py:332` construct `RepoAdapter(...)` directly with an inline
   `"gh" if is_gh else "tea"` ternary, rather than calling `adapter_for()`.
   They go through the adapter for *dispatch* but decide the backend
   themselves.

So the chokepoint holds **by convention, not by construction**. Nothing
prevents a 29th caller from writing that ternary again. This is the single
most important datum in the research: the construct exists, works, is the
best instance in the codebase, and has already been reached past twice more
since #423 was fixed.

The operator's citation `core.py:1268` for the no-fallback has itself drifted
— `RepoAdapter` is now at `core.py:1848`, the guard at `core.py:1878`. A
citation in a comment decayed silently, which is the same failure one level
down.

### Construct 3, measured

The thresholds are **four**, not three, and all independently configurable:

| constant | file:line | default |
|---|---|---|
| `NUDGE_IDLE_MINS` | core.py:39 | 30 |
| `CLAIM_LEASE_MINS` | core.py:65 | 120 |
| `LEASE_TTL_MINS` | core.py:57 | 720 |
| `void_claim` | core.py:3175 | boolean predicate |

`void_claim` carries **eight conjunctive clauses**, five of which are
exemptions that cross-reference `lease_expired` with the phrase "same
reasoning as". The two predicates are not independent questions; they are one
question split in two and kept in sync by repeated prose. Each clause was
added to fix a prior issue — orch#363 is named in the docstring.

That is hypothesis A's mechanism (accretion by patching) producing construct
3's symptom (a hole between thresholds). The two hypotheses are not as
opposed as the issue framed them.

### Evidence from this level

Three defects were lived rather than read:

- **#425.** I declined to re-enter orch#408 fourteen-plus consecutive times.
  `void_claim` excluded it on three clauses at once (`prior_runs > 0`,
  commits exist, session reported) and `lease_expired` was hours away. A
  single predicate — *is there work with no live owner?* — has no seam to
  fall into. Strong C3.
- **#421.** Its own fix, PR #426, merged at 2026-09-17T18:52:23Z carrying
  **zero** `orch:review:v1` blocks. The PR that closed the review-bypass was
  itself landed through the bypass. Strong C1, and the sharpest available
  demonstration that a contract reachable from one place is not a contract.
- **#413.** Three false sentences in `agents/repo-orch.md` that no agent may
  fix. Unreachable by any construct here. Genuine A, and the reason this
  table is not strained: a classification finding no A rows would have been
  fitted to its conclusion.

## The shape no construct covers

The batch-B classifier surfaced a recurring shape that fits none of the four
candidates: **a flag grants permission but nothing schedules the action.**

- **#387** — `auto-land` authorises a merge; nothing drives it. Four green
  PRs sat at REVIEW because landing needed two session starts.
- **#318, #372** — the only granted revocation verb removes a per-issue label
  that is not there, so a repo-default `auto-land` cannot be revoked at all.

This is a permission/scheduling split, and it is worth naming because it is
the second-most-repeated shape in the corpus after the two above. It is not
an argument for a fifth construct — three instances is not a class — but it
predicts where the next defect lands.

### Two more, observed after this research began

Both arrived on 2026-09-17 after the classification was complete. Neither
fits C1–C4, and each extends the shape above rather than any construct.

- **The blocker was a doc, not code.** repo-orch declined to re-enter
  orch#408 fourteen-plus consecutive times because orch#413 was unanswered.
  Forced re-entry landed it in minutes — PR #426 merged at 18:52:23Z and
  PR #412 at 18:54:40Z — **while orch#413 remained open and unanswered.**
  The mechanism not reached was *re-entry itself*, and what withheld it was
  a rule written in prose, not a predicate in code. No construct here
  reaches this: constructs constrain what code can express, and this was a
  sentence no code consulted. It is the counterweight's thesis in its
  sharpest form — the doc outlived, and outranked, the model.
- **orch#432 — orphaned at birth.** PR #431 was opened 136 seconds after
  orch#408 closed. repo-orch's spawn verb is per-issue and needs a live
  `issue-<n>` worktree; a closed issue has neither, so nothing in the
  pipeline can attach a follow-up branch whose parent closed while it was
  in flight. This is a reachability gap, but not a bypass: no caller
  reached past a chokepoint, and the correct mechanism does not exist at
  all for this case. It is an **A** row.

Both reinforce the verdict rather than complicating it. The first shows
that a class of stall lives entirely outside code, where no construct can
reach; the second that new edges are still arriving at the same rate the
corpus predicts.

## Answering the operator's three questions

Only construct 1 survives all three. The others are recorded for completeness.

### Construct 1 — one chokepoint per concern

1. **Which defects does it make unwritable?** The seven C1 rows: #421, #423,
   #372, #366, #417, #398, #146. (#318 is C4 — the bypass is the operator's
   own config, not a caller. #427 is C3 — a hole between parse states, which
   no chokepoint closes.)
2. **What real requirement would erode it?** The one already eroding it:
   a caller that legitimately knows its backend and wants to skip a
   resolution cost. `feed.py` does this for a real reason — `adapter_for()`
   would re-resolve a web base it already has. The requirement is
   performance, and it is genuine.
3. **Does it fail loudly when eroded?** **No — and this is disqualifying for
   the strong form.** `RepoAdapter("gh" if is_gh else "tea", ...)` is a
   legal, silent, reviewable-looking line. Nothing fails.

So the strong form ("a model in which the bug is unwritable") is not
available in Python for this concern. The weak form — make the single entry
the only *convenient* path and make bypasses *loud* — is available, and is
the thing worth building.

### Construct 2 — every claimed effect carries its measurement

Predicts 3 of 47 (6%). Not worth a construct. The three cases (#416, #382,
#408) are better served by the existing conformance suite growing three
assertions. Test suite is green at 1790 passing.

### Construct 3 — recovery as one predicate

Predicts 8 of 47 (17%), and the measurement above shows the four thresholds
are already one question wearing four hats, kept coherent by prose. This is
real, but it is a **refactor of `void_claim`/`lease_expired` into one
predicate**, not a new model. It lands incrementally or not at all, per
#262.

### Construct 4 — operator uses the agents' paths

Predicts 1 of 47 (2%). The issue's own evidence for it — repo-orch's "the
queue in front of the human is stuck" — is a throughput observation, not a
defect class. Drop it.

## What is worth building

Not a model. Two narrow, incremental things, in this order:

1. **Make bypasses of `adapter_for()` loud.** One conformance test asserting
   that `RepoAdapter(...)` is constructed only inside `core.py` — the two
   `feed.py` sites either route through `adapter_for()` or are explicitly
   allowlisted with a reason. This converts a silent convention into a
   failing check, which is the honest version of the impossibility. Cheapest
   item here and it closes the largest C-bucket.
2. **Collapse `void_claim` and `lease_expired` into one predicate.** They
   already share five exemption clauses by copied prose. One question, one
   action. Closes #425's class rather than #425's instance.

And one correction, which is not optional:

3. **Fix `core.py:10`.** The sentence "nothing here gates on them" is false
   and has been since at least five recorded facts started gating. Replace it
   with the practiced rule: *a recorded fact may suppress or defer for ≤TTL,
   and may never authorize.* A false guarantee in the module docstring is the
   #421 shape one level up — the thing everyone reads and nobody re-checks.

What is explicitly **not** worth building: a new model, a fifth construct for
the permission/scheduling shape, or any change justified by "too many special
cases". The 60% are real edges and will keep arriving. The conformance suite
is the right backstop, and #262's finding stands.

## Negative result

The issue asked whether most defects map to a construct. **They do not** —
40% do, and only inside three subsystems. The patches are the system, and
this closes as a negative result, which the issue correctly anticipated is a
real result.

The three items above are worth doing on their own evidence, not as the
construct the question was looking for.

## Appendix: full classification

Every issue, one row. `F` = feature/not a defect, `D` = deliberate decision,
`A` = genuine edge, `C1`–`C4` = the candidate constructs. F and D rows are
excluded from the 47-defect denominator.

### Open set (2.0)

| n | bucket | defect mechanism | why this bucket |
|---|---|---|---|
| 28 | F | MCP surface design question | design question, no defect |
| 156 | F | can a role escalate model mid-run | open design question |
| 174 | F | tick cadence and coalescing floor rework | scoped feature |
| 187 | F | split one wake floor into per-role floors | scoped feature |
| 190 | F | noticing issue citing two filed root causes | adds no new defect |
| 227 | A | second repo never got orch's auto-pull timer | missing generalization, nothing bypassed |
| 231 | F | landing step keeps architecture map current | new process work |
| 268 | F | audit doc bloat and duplicated rules | research task |
| 280 | A | no journal event fires at merge instant | mechanism never existed to bypass |
| 286 | F | research prior art for orchestrator prompts | research question |
| 295 | F | structured question block and wizard UI | new capability |
| 298 | F | usage-inspection dashboard | new capability |
| 346 | F | collapse a two-minute-lived label axis | ruled design decision |
| 365 | C3 | work_state cannot separate no-op from report-only success | hole between liveness predicates |
| 366 | C1 | `--covered` accepted on any event, honoured on one | writer reached past reader's filter |
| 367 | A | triage buckets lived only in prose | lost data, nothing to bypass |
| 381 | A | `.gitignore` has no `tmp_*` pattern | unenforced convention |
| 382 | C2 | pull reports success while 10 commits behind | claim never checked against ground truth |
| 390 | A | `gh issue edit` removes label even when add fails | external CLI non-atomicity |
| 398 | C1 | tier labels created at watch time, never backfilled | one-time gate skipped for existing repos |
| 401 | A | tea lists issue titles but cannot read bodies | forge capability gap |
| 404 | A | gh caps label description at 100, text is 106 | external API limit |
| 405 | A | no granted invocation reaches the seed module | envelope gap, no path exists |
| 407 | F | tracking issue re-filing an unfinished ask | administrative |
| 408 | C2 | hold-from-review ruling decided, gate never wired | decided outcome undelivered |
| 411 | C3 | no label means "awaiting operator, not startable" | hole between ready/working/stuck |
| 413 | A | no agent may edit its own grant file | authorization boundary |
| 417 | C1 | repo-orch reads feed's slice, not the open set | path returns a slice, doc says full set |
| 421 | C1 | reviewer reachable only from issue-landing | single-chokepoint model case |
| 427 | C3 | `[]` conflates clean, unreviewed and truncated | hole between None/[]/truncated |

Open-set counts: C1=5, C2=2, C3=3, C4=0, A=10, F=13.

### Closed 2026-09-17, batch A

| n | bucket | defect mechanism | why this bucket |
|---|---|---|---|
| 11 | F | CRUD on the web control surface | feature |
| 13 | F | render controls from the verb table | feature |
| 92 | F | mobile rows, chips, wizard, repo page | design spec |
| 100 | F | burndown and throughput charts | feature |
| 101 | F | repo turn graph | feature |
| 140 | A | redesign dropped a live review surface | plain omission |
| 141 | A | redesign dropped the deploy readout | plain omission |
| 143 | C3 | credential fault reached no surface | fell between escalation and derivation |
| 144 | D | orphan-state root cause doc deferred | signed-off follow-up |
| 146 | C1 | `spawn.py` accepted `--ref` on any event | caller reached past the intended gate |
| 152 | D | WHAT CHANGED vs WHAT MOVED naming | documented stage-2 boundary |
| 154 | F | tracking six artifact/widget discrepancies | tracking issue |
| 159 | D | `ask` inert, redefinition deferred | explicit deferral |
| 164 | F | no wake reason for cross-repo reasoning | new capability |
| 172 | A | docs describe the superseded design | doc/code drift |
| 184 | A | usage limit killed five sessions undetected | external account limit |
| 185 | F | no per-tick spend record or policy | new capability |
| 189 | F | live session log tail | feature |
| 197 | A | OOM kills sessions on type-check gates | host resource limit |
| 204 | A | two sessions died silently at Land | unresolved external cause |
| 208 | F | no visual sense of realtime activity | feature |
| 209 | F | WHAT MOVED summary verb never landed | unbuilt feature |
| 211 | F | journal view and operator input box | unbuilt feature |
| 212 | F | repo automerge toggle | unbuilt feature |
| 219 | A | caveman hook fired and was ignored all session | model-compliance gap |
| 223 | F | should the CLI wrap gh/tea | research question |
| 227 | A | gita-lectures has no auto-pull, hook inert | missing infra |
| 228 | C3 | a dead cross-host claim looks live | pgid liveness meaningless off-host |
| 237 | A | artifact URL 403s unauthenticated | external auth boundary |
| 241 | F | index of artboard-vs-code divergence | tracking issue |
| 257 | F | pause/play state specified, nothing reads it | unbuilt feature |
| 262 | F | ponytail audit, report only | research task |

Batch-A counts: C1=1, C2=0, C3=2, C4=0, A=8, D=3, F=18.

### Closed 2026-09-17, batch B

| n | bucket | defect mechanism | why this bucket |
|---|---|---|---|
| 268 | F | doc rule-duplication audit | research task |
| 280 | A | no journal row at the merge instant | no mechanism to bypass |
| 289 | F | live feed, display names, journal tail | feature |
| 296 | F | remove the `ask` verb | deliberate redesign |
| 301 | F | derive and badge a stalled status | new derivation |
| 308 | A | dispatch never told to batch parallel calls | missing instruction |
| 314 | F | derive the in-flight cap from headroom | design improvement |
| 318 | C4 | operator's repo-default config bypasses the label path | operator config outside the agents' path |
| 321 | A | a no-commit issue can never reach DONE | terminal path never wired |
| 335 | F | cost audit, measurement only | measurement |
| 336 | C3 | `agent-working` is a lease that never expires | missing threshold among liveness questions |
| 337 | A | shared token erases label provenance | identity boundary |
| 340 | A | grid `auto` column outranks `1fr` | plain layout bug |
| 343 | F | delete dashboard-op's automatic wake | ruled cut |
| 352 | A | closing an issue orphans its open PR | derived-state gap |
| 360 | A | docs say seven wake conditions, code has nine | doc/code drift |
| 362 | C3 | void claim has no clearing predicate | hole between claim-write and lease TTL |
| 363 | F | PR body token policy question | policy question |
| 368 | F | rulings do not persist as rulings | new capability |
| 369 | A | repo-orch never nominates `agent-ready` | behavioural gap |
| 370 | A | test races the live projects tree | race against live filesystem |
| 372 | C1 | landing skill teaches the inert revocation spelling | caller reached past the correct revoke path |
| 374 | A | harvester parses past the heredoc terminator | parser bug |
| 386 | A | in-flight count sums four states | presentation conflation |
| 387 | A | `auto-land` grants permission, nothing schedules | permission/scheduling split |
| 395 | F | Fable-writes-code experiment | research question |
| 415 | F | live per-row status lines | feature |
| 416 | C2 | claimed trim, payload grew 12%, no test measured it | claimed effect unmeasured |
| 423 | C1 | lease and void-claim passes pass a bare slug | caller reached past `RepoAdapter` |
| 425 | C3 | dead owner with commits fits neither predicate | hole between two thresholds |

Batch-B counts: C1=2, C2=1, C3=3, C4=1, A=15, D=0, F=8.

### Totals

Deduplicated across both sets (#227, #268, #280 counted once):

C1=7, C2=3, C3=8, C4=1 → **19 construct-preventable**.
A=28 genuine edges. **Defect denominator 47.**
Excluded: F=39 features, D=3 deliberate decisions. 89 unique issues.
