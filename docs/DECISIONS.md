# orch — the record of deciding

DESIGN.md is the spec; this is the record of how it got decided, and — the
question that motivated this file — **who** decided each piece. Much of this
system was designed by delegated designer agents (Fable for design, Opus for
implementation orchestration, Sonnet for code). Decisions an AI made on the
operator's behalf are marked as such, so the operator can revisit them knowing
they never actually chose.

Sources: issue #8 and its five comments (the primary record; later comments
reverse earlier ones), issue #6 (the superseded design plus six edit comments),
issue #2 (the older actuation design, mostly cut), issues #3 and #7, DESIGN.md,
STATES.md, and the commit messages. Timestamps are UTC as GitHub and git record
them; the decisive session ran 2026-09-10 into the early UTC hours of 09-11.

Attribution tags:

- **[operator]** — the user decided it, personally, with stated reasoning.
- **[operator sign-off]** — a designer surfaced it as a question; the user
  explicitly accepted.
- **[delegated: Fable]** — decided by the design agent, not the operator.
- **[delegated: implementer]** — decided during the build (Opus/Sonnet),
  reported afterward.
- **[unattributed]** — the record does not say who decided; noted honestly
  rather than guessed.

---

## A. Operator decisions

These are load-bearing. Do not silently revisit them.

### A1. Four levels, and why — [operator]

`tick → dashboard-op → repo-orch → issue-orch → worker`. Two reasons, both
the operator's: **context isolation** (repo context for N repos lives in N
separate sessions instead of piling into one machine-wide agent — the problem
that motivated levels originally), and **each level is a conversational
surface** the operator can talk to at its own scope (the issue thread is the
per-issue surface; the dashboard journal is the machine surface). Record:
issue #8 comment 2026-09-10T23:25Z.

### A2. "Orchestrators move things along. They do not understand repos." — [operator]

The governing principle for where a boundary sits — not "could this decision
be made elsewhere" but "does making it here force this level to understand
something outside its scope." Stated by the operator in the repo-orch
reversal (#8, 23:25Z); now the epigraph of DESIGN.md's tree section. Several
downstream cuts follow from it mechanically (see `notes/` deletion, C-list).

### A3. repo-orch kept, after being cut — [operator]

The fresh design review cut repo-orch (#8 body, "Settled: three levels"): its
routing content was something dashboard-op already had the facts for. The
operator reversed it (#8, 23:25Z): the cut was "mechanically right and
philosophically wrong" — routing was never the point; repo-orch is **where
repo understanding lives so nothing above it has to**. Fable conceded (23:29Z)
and diagnosed its own error: *"I cut the router because a router is all that
was ever written down"* — the level's real mission had never been documented,
so any fresh reviewer would re-derive the same cut from the same thin
description. That is why DESIGN.md now carries the mission in full, and why
`agents/repo-orch.md` warns that a thin description is what once got the level
cut. (The cut recurred across review passes in the design conversation; the
durable record shows the one explicit cut-and-restore.)

### A4. Repo management is a skill — [operator]

repo-orch runs a repo-management skill the way a worker runs `/sc`: the skill
holds the repo understanding, repo-orch is the session that holds the skill's
context, and orch itself stays mechanics-only — spawn, lease, wake — never
learning what a repo means. Record: #8, 23:25Z. The skill body does not exist
yet (section F).

### A5. One lease scheme — [operator-prompted; reversal executed by Fable]

The design briefly had two: `.orch.pgid` in the worktree for workers, the
ledger for orchestrator roles (#2 revision comment, 22:10Z). The operator's
reframing — *"a worker is an agent spawned from an orchestrator, either in an
issue or repo"* — prompted the reversal (#6 fifth edit, 22:25Z): under that
definition a worker is a role that happens to own a worktree, and **zero code
branches on worker-vs-role after reading the lease** (liveness, kill, and
next-action are identical). Two lease schemes encoded a distinction no reader
consumed. Consequence: `.orch.pgid`, `.orch.brief`, `CLAIM_GRACE_S` and its
two tests were never built (see D3).

### A6. Disk as state; derived versus recorded — [operator]

Durable state never lives in a session: derived facts (labels, commits, PR
state, rollups, `killpg`, transcripts) are re-read from the world every tick
and cannot lie; records (journals, ledger rows, logs) may lie so nothing gates
on them. "A reset clears conversation, not the ledger." This is the oldest
standing decision — it shaped the 2026-09-01 bash original (commit 11aff76:
"the tick observes, the agents decide") and survived every rewrite. The #6
acceptance test is its restatement: a session dying costs only conversation.

### A7. Journal on the issue — one source, no mirror — [operator]

The issue journal lives in GitHub issue comments, and **only** there. The
operator's "one source" lean plus a scope split reversed Fable's own
durability objection (see D2 for the full reversal). No local mirror, ever: a
cache that is authoritative only when nothing works does not earn a
sync-and-reconcile question on every read and write. Repo and dashboard
journals stay local files — they are about *this machine's* orchestration, and
two machines' dashboard-ops are not one conversation. Record: #8, 00:21Z
comment ("SUPERSEDES"). DESIGN.md § Journals.

### A8. Push-per-commit — [operator-accepted design]

The worker brief mandates a WIP commit per logical step (#3 Option A,
recommended by the #3 design and folded into the brief) and, per the
cross-machine requirement the operator drove, `git push -u` after each commit,
best-effort — commit survives process death, push survives machine death, and
push failure must never wedge a worker on an outage. First WIP commit
force-adds the plan file (`git add -f tmp_plan-*.md`) so gitignored plans
travel. Squash-before-PR implies `--force-with-lease`. Record: #8, 00:19Z;
DESIGN.md § The worker and /sc.

### A9. Auto-land with no CI — accepted — [operator sign-off]

An empty or absent check rollup reads as green, so a repo with no CI reaches
REVIEW and is landable — meaning an `auto-land` issue on a no-CI repo can
self-merge having never been checked. Surfaced twice as a sign-off question
(#7 item 4; #6). Operator: *"No-CI PRs are the common case here, and
self-merge-unchecked is fine until it actually causes a problem"* (#7 comment,
2026-09-10T22:01Z). The four tests pinning the asymmetry stay. Revisit only if
an unchecked auto-land bites. Per #39, `README.md` now states this
consequence plainly where the `auto-land` checkbox is documented.

### A10. `claude`, not `happy` — [operator sign-off]

`subprocess` resolves `~/.local/bin/claude` via PATH, bypassing the
interactive shell's `claude`→`happy` alias. Accepted as-is: spawned agents run
plain Claude Code (#6 comment, 22:01Z). The `ORCH_AGENT` seam that would have
made the runner configurable was later cut as speculative (zero second
runners); the seam is now "spawn() is the one place processes start."

### A11. Keep-forever, append-only records — [operator]

Nothing about a dead run is ever destroyed: rolled ledger rows, append-only
run logs, no pruning, no rotation, no retention policy. "The run you most
want to debug — the busted one — survives respawn." Stated as a requirement in
the #6 body's acceptance test ("abandoned, runaway, and orphan sessions must
remain inspectable after the fact") and listed in #8's do-not-re-litigate
block. Note: the *retention-forever tunable* in DESIGN.md was extrapolated
from this decision by a designer, not separately chosen (see B1).

### A12. DESIGN-V2 replaces DESIGN — [operator]

The old DESIGN.md described a system that no longer existed (five-level tree,
`.orch.pgid` lease, openclaw surfaces, a STALE state the code never produced).
Keeping both would leave a future reader to guess which wins; V2 — the spec
the package was built from — took the name. Commit b8e5a83, 2026-09-10.

### A13. The S1 live-window tradeoff — accepted — [operator sign-off]

Per-session "live" is transcript-mtime recency within a 120 s window (the only
signal that can tell two concurrently-written transcripts apart — the ledger's
pgid is per-key and cannot). Cost, accepted: a dead session reads live in the
widget for up to two minutes. The fix that introduced it is commit 6e26df6
("detect contention instead of asserting it away"); the tradeoff is named in
DESIGN.md's tunables table (contention window). The "S1" label and the
acceptance itself are from the design session; the durable record carries the
tradeoff but not the label.

### A14. Agent surface = operator surface — [operator]

*"whatever i can do i want agents to be able to do."* The operator had eight
actions through `/act`; agents had one verb. spawn.py became the orch CLI —
`kill`, `tick`, `status`, `tail`, `watch`/`unwatch` — with **one
implementation behind both callers** (CLI verbs and HTTP handlers call the
same core functions). The parity runs one way only: `/act` stays a closed set
and never gains a run-anything route, because the web surface is reachable
over the tailnet and the CLI is not. Commit 22e19d7; DESIGN.md § Surfaces.

### A15. `auto-review`: per-repo `.orch.toml`, per-issue labels, a subagent not a role — [operator]

Resolved on the issue-26 thread before planning, four pieces:

**Where the per-repo default lives.** `<checkout>/.orch.toml` at the
watched repo's own root — not `repos.txt`, not a sibling file in orch's own
tree. `repos.txt` is a bare path list and machine-local by construction
(A6-adjacent: it says where a repo lives on *this* machine, and a second
machine legitimately starts its own). The `auto-review` default is a
property of the repo's own workflow, so it belongs versioned with the
repo's own history and travels with the clone — the opposite shape from
`repos.txt` for exactly the reason `repos.txt` is machine-local. Shape:

```toml
[orch]
auto-review = true
```

A bare `.orch/auto-review` marker file was rejected: it cannot carry a
second key without becoming a directory of markers, and a second key
(`auto-land` is the obvious next one) should not require reopening the
question. Do not add an `auto-land` key to it now — nothing asks for it
yet.

**Superseded by issue #39.** Issue #39 asked for the `auto-land` key. The
key was added as a line in the existing `[orch]` table, with no new file
and no new loader — exactly the shape this decision chose to allow.

**Storage location superseded by orch#297, 2026-09-16.** The per-repo
`auto-land` (and `in-flight-cap`) default no longer lives in a per-repo
`.orch.toml`; it moved into that repo's own entry in the single
`ORCH_HOME/orch.json`, keeping the kebab-case key names. This reverses the
"versioned with the repo's own history, travels with the clone" shape this
decision chose above — the setting is now machine-local, like `repos.txt`,
not repo-local. A one-time migration reads an existing `.orch.toml` into
`orch.json` on first load; the old file is never deleted
(no-delete-before-convert). The key name itself is unchanged by this —
still `auto-land`, not `automerge`; that separate rename is orch#212 and
remains unimplemented.

**Who reviews.** A `reviewer` subagent dispatched by issue-orch — the same
shape as `worker` and `failure-reader` — not a fourth spawnable role
spawned by repo-orch. The rejected alternative's cost was named precisely:
a new role touches `ROLES`, `key_for`, `cwd_for`, `_split_key`, `ARITY`,
`USAGE`, `feed._KEY_ARITY`, `DENY_BY_ROLE`, `JOURNAL_PATH_BY_ROLE`,
`_journal_spawn`, and `_journal_scope_for`, and this design has been
deliberate elsewhere about not paying that cost without a reason (A3, B5).

**The independence argument, and its residual risk.** The objection to a
same-level reviewer assumes it shares the writer's context; it does not.
Every specialist in this design is one-shot and cold — it never saw the
units get written, cannot read the issue thread. What catches a bug missed
at write time is a cold read of the diff against the issue, which is a
property of statelessness, not of which tree level issued the brief; a new
orchestrator level spawned by repo-orch would buy a different session key
and nothing else — a different key is not what did the work. The
`reviewer` template (`agents/reviewer.md`) makes this concrete: it receives
the issue body and the diff, never the plan file, the journal, or
issue-orch's account of what it did, because a reviewer given the writer's
reasoning grades the reasoning, not the code; its return has no
"looks good" field, only findings with concrete failure scenarios, and an
empty list is a valid result of looking, never a default. Residual risk,
stated and accepted: issue-orch still chooses when to dispatch it and
still judges its report, so a systematically wrong issue-orch can still
produce a weak review — this does not remove that, only the likelier
failure of a reviewer that agrees because it remembers writing the code.
Revisit if a review is observed passing a PR a human later rejected.

**No seventh condition.** `auto-review` fires at condition 5's REVIEW
disjunct, exactly as `auto-land` does, and re-enters from the same place.
Fire-once is enforced by checking the PR's own comments for an existing
review before dispatching another — no new recorded state, no digest
input. "Why these six and no seventh" (DESIGN.md § The six conditions)
stands unamended; a successor should not add a condition 7 for review.

**The one point left for the operator, flagged rather than guessed.** The
stated precedence ("per-issue wins over per-repo") assumes the repo
default is off; with a repo default of `true`, turning review off for one
issue needs an explicit opt-out, because label absence cannot mean both
"no opinion" and "no" at once. `no-auto-review` is the minimum label that
satisfies this, mirroring how presence/absence already works for
`auto-land`. This is the one piece of the `auto-review` design the
resolved decisions did not fully determine on their own — recorded here so
it reads as a flagged call, not an invented default.

DESIGN.md § Review before landing; `agents/reviewer.md`;
`agents/skills/issue-landing/SKILL.md` § auto-review.

**Superseded by the operator, 2026-09-14 — one flag, not two.** Everything
above this note is the record of what was decided and why at the time; it
was true and it ran. The operator has now overturned the two-flag shape
itself: there is one flag, `auto-land`, meaning "automatically review, then
merge." There is no separate `auto-review` flag, on this repo or any other
— the per-repo default, the per-issue `auto-review` / `no-auto-review`
labels, and the "No seventh condition" analysis above are all superseded by
this. The no-gating rule ("review posts findings and never gates the
merge") is also overturned: a blocking finding now gates the merge — by
**revoking `auto-land`** on the issue (the per-issue label only, never the
repo default), not by any new label or state. The work state stays
`REVIEW`; automation does not merge it again (no re-review, no retry, no
merge) until the operator re-adds `auto-land`, which is the only thing
that resumes the automated path. See DESIGN.md "Review before landing"
(the on/on cell) and `agents/skills/issue-landing/SKILL.md` for the
current, authoritative model. This entry stays as the record of the design
this replaced, not as a live description of current behavior.

**Corrected by the operator, 2026-09-14 — no new label, revoke instead of
`agent-stuck`.** The paragraph immediately above this one, written earlier
on 2026-09-14, itself described the blocking-finding case as "deferring the
issue — add `agent-stuck`, remove `agent-working`, one call." That
overloaded `agent-stuck` to mean both "gave up" and "deferred by review,"
and the operator has withdrawn it same-day: `agent-stuck` means only the
write-off case again. A blocking finding revokes `auto-land` and leaves the
work state at `REVIEW` — the same held-`REVIEW`-without-`auto-land`
condition this design already has for a repo default of off, re-examined
every tick by condition 5 and merged by nobody because the flag is absent.
No new label, no new work state, no new store, no change to the condition
table — the only new behaviour is the revocation itself, recorded by the
`orch:review:v1` comment already posted to the PR. See DESIGN.md "Review
before landing" (the on/on cell, re-settled) and
`agents/skills/issue-landing/SKILL.md` for the current, authoritative
model.

**Wired by orch#408, 2026-09-17 — the hold is derived, not revoked.** The
two notes above describe the blocking-finding hold as revoking `auto-land`.
That mechanism is now replaced. Ruling orch#148 option B, decided
2026-09-15 on a now-closed issue, chose instead: a PR whose latest
`orch:review:v1` block carries a `fix-before-merge` item does not merge —
re-derived fresh from the PR comment on every merge attempt, no label
written, no state stored. Until orch#408 that ruling was decided but
unwired: the parser (`review_items_for_pr`) existed, but its only non-test
caller rendered a display row, so the real merge gate was still the label
pair the ruling had already cut. It now lives in
`core.review_blocks_merge`, which gates `core.merge_pr` directly. This is a
genuine behavioural improvement over revocation, not just a rename: a
clean re-review lifts the hold on its own, because only the last block on
the PR is read, where under the label the lift was the operator's act
(re-adding `auto-land`). `auto-land` / `no-auto-land` survive unchanged as
per-issue overrides of the repo default — what is gone is their use as a
hold for a blocking review finding.

---

## B. Delegated decisions — made by a designer, not the operator

Each of these runs and looks right, and **the operator never chose it**.
Listed with what changing it would trade.

### B1. The five tunables — [delegated / inherited; recorded in DESIGN.md § Tunables]

DESIGN.md already carries the full table (commit 245f694: "name the tunables
nobody actually chose"). Summary of provenance:

| knob | value | who/where it came from |
|---|---|---|
| `NUDGE_IDLE_MINS` | 30 min | inherited from the 2026-09-01 bash original; never measured |
| cold-retry grace | ~5 s | Fable's reasoning ("resume rejections fail fast"), not measurement (#6 Q2) |
| contention window | 120 s | arrived with the bug fix 6e26df6, chosen "well under NUDGE_IDLE_MINS" |
| ledger retention | forever | extrapolated by a designer from the operator's keep-records decision (A11) |
| default decomposition | one unit, `main` | designer default; shapes every issue's work |

Revisit deliberately on the first run against a real queue — none has been
exercised under load.

### B2. Widget: port, not descope — [delegated: implementer]

tick bakes the feed into the widget every pass; the rewrite changed the feed
shape and the widget's JS still read the old one — the operator surface
rendered garbage and acceptance-recovery path 1 was dead. The implementer
chose to **port** the widget to the new tree rather than descope it, on the
grounds that the widget is the operator surface the acceptance test names and
the new shape was fully specified (commit c223ba3). Trade if reversed: the
acceptance test's operator recovery path (see the unit, copy its resume line,
kill from the widget) stops existing.

### B3. The `pre-merge` stop, named and edited into the personal skill — [delegated]

`/sc` mismatch 1 (Stage 3 merges unconditionally; combined with A9, a no-CI,
no-`auto-land` issue self-merges) was fixed by adding a named stop —
**pre-merge**, "after Stage 3 opens the PR, before it merges" — to
`~/.claude/skills/ship-change/SKILL.md`, the operator's personal skill used by
other work. The name, the synonym list, and the report format
(`merge: pr #N opened — holding for pre-merge stop`) were designer choices;
the worker brief (`agents/worker.md`) must *request* the stop for it to bind.
An interim containment (a fifth brief mandate overriding Stage 3) landed first
via commit f7290cc; `docs/SC-INTERFACE.md` records both and marks mismatch 1
RESOLVED. Trade if changed: rename or remove the stop and every worker brief
that names it goes back to unconditional merge.

### B4. Digest emitted by the evaluator — [delegated: Fable]

The thin shape the digest hashes is **emitted by the same function that
evaluates the conditions, from the same variables** — one pass, two consumers,
no separate walker. Rationale: every digest bug in the design's history was
drift between a thin shape and the conditions, maintained as two code paths;
one emission point makes drift impossible by construction. (#8 body, bug 4 →
the digest rule; enforced in the build when `build_thin` violated it — E7.)
Trade if changed: reintroduce the drift class that produced both the wake-spam
and the suppression bugs.

### B5. One key = one exclusive cwd — [delegated: Fable]

No key shares a cwd with any other key, with the operator's interactive
sessions, or with a repo checkout. Claude mangles the exact cwd string into
the transcript dir, so distinct strings give distinct dirs and resume identity
is **correct by construction** — the mtime filter deletes, contention becomes
per-key free. Found as bug 3 of the fresh review (#8 body); cwd table refined
in Fable's 23:29Z comment (`wt/<slug>/` over `state/repo-orch/<slug>/` — the
filesystem mirrors the org chart, and the `-orch-wt-` substring keeps the
orphan-scan exclusion working). Trade if changed: cross-resume between actors
returns, plus a second exclusion pattern in the orphan scan. Known cost,
recorded: repos named like `<slug>-issue-<n>` collide under mangling.

### B6. `spawn(role, scope, prompt)` — workdir and resume derived, never parameters — [delegated: Fable]

The sixth edit on #6 (22:28Z) collapsed `spawn()`/`spawn_worker()` into one
general spawn: cold retry self-selects (workers have no resume id), brief on
stdin is uniform, worktree creation is pre-spawn mechanics for one role. The
addressing table is the whole workdir mapping and callers cannot express a
wrong one. The same edit made `spawn.py` a load-bearing CLI (orchestrators are
`claude -p` sessions acting through Bash — spawning downward is their normal
verb). Trade if changed: a second spawner is "the original sin this design
unified" (cut list).

### B7. The condition set itself — [delegated: Fable]

The six wake conditions, the "unowned work" formulation of condition 5
(covering dead-mid-flight, between-units, and clean forward progress in one
condition), the REVIEW disjunct that keeps auto-land off the idle clock, and
the rule that conditions 1–3, 5, 6 are cells of one predicate — *work exists
that no single healthy owner is driving* — kept enumerated in code because
agents need names they can verify one at a time. #6 Q5 (conditions 5–6 new),
#8 body bug 2 (condition 5), DESIGN.md § The six conditions. Trade if
changed: each condition maps to a distinct failure the system historically
had; deleting one re-opens its failure (the section says which).

### B8. Other designer judgments worth knowing about — [delegated]

- **Conditional ack** ("ack only on a wake where you spawned nothing") —
  Fable's zero-mechanism fix for its own ack-hole bug (E4). The rejected
  alternative (per-key attempt counters in the digest) is in the cut list.
- **Journal scope for a spawn** — spec said "the appropriate scope" without a
  mapping; the implementer chose: spawn fact goes where the spawned thing
  lives, decision goes where the decider lives (PR #10 report; now DESIGN.md
  § digest rule). Flagged by the implementer as worth the operator's look.
- **`state/` gitignored wholesale** — broader than the spec's literal
  `state/sessions/*.log`; implementer judgment, left as-is (PR #10 report).
- **Agent docs as skeletons** — every judgment slot (ordering, give-up,
  escalation) is an explicit `TODO(user)` rather than a plausible invented
  table, on the stated ground that a guess that runs and looks right is worse
  than a gap (commit a46e6f7). The *decision to not decide* was the
  designer's; the decisions themselves are still owed (section F).

### B9. "Escalation addressed" is recorded, not derived — [delegated; #93]

#93's open question was what counts as an escalation being resolved and what
records it. A6 prefers derivation — a derived fact is re-read from the world
and cannot lie — so the burden was on recording, and recording won because
there was nothing in the world to derive from.

An `escalated` row is repo-scope and carries no issue number. The only existing
operator-answer path, `a_reply` in `server.py`, writes an `orch/operator reply`
comment to a GitHub *issue*: a different store, keyed by a number the
escalation does not have. No operator act touched the repo journal at all, so a
derivation would have had no fact to read. The third candidate — a successor
agent journalling that the condition lifted — is *also* a recorded row, and a
worse one: it makes the agent the judge of whether the operator was reached,
inverting the thing #93 is about. So the operator's durable act appends a
recorded row and `core.open_escalations` subtracts it.

**Superseded by #106.** The operator's durable act is now **file as issue**:
it appends a `filed` row (`core.escalation_filed`), carrying `ref` and the
filed issue number, and turns the escalation into tracked work. A plain
**dismiss** is browser-local `localStorage` only — no HTTP, no server state,
no journal row — reversible any time from the browser that set it and
invisible to every other viewer. #106 removed the single `addressed` button
this decision originally described, because silencing an escalation without
resolving it was itself the defect being fixed. `core.open_escalations`
honours BOTH `filed` and legacy `addressed` markers, so rows already written
under the old path stay honoured.

What corrects the recorded fact when it is wrong follows #34's move — a
recorded fact derivation cannot correct must be corrected by something else,
and #34 bounded the wake gate's one recorded fact for exactly that reason. Here
the correction is that the marker can only ever fail toward LOUD:

1. **Malformed reads as NOT addressed.** A marker with `ref` missing,
   non-string or empty, or with its own `at` missing, unparseable, or
   future-stamped, is ignored entirely — mirroring `tick.last_ack()` (deleted
   in #345; the same stance now lives in `tick.read_dash_wake()`). The
   marker is the only thing that can silence a live escalation, so a corrupt or
   forged one must be powerless; the failure that would recreate this bug is a
   marker that quietly hides a real escalation, so the code must be unable to
   produce one. Symmetrically, a malformed *escalation* row is returned as open
   rather than dropped: a human puzzling over a weird row beats a machine
   hiding one.
2. **Per-row, never a high-water mark.** The marker names its escalation by the
   exact `at`. A watermark would let one click clear rows the operator never
   saw.
3. **A later escalation is never covered by an earlier marker** — the direct
   consequence of (2), and the property that makes the button safe to press
   while a second escalation is open.

**No TTL, deliberately.** #34's bound is not a general rule for recorded facts;
it is the fix for a *lease*, and a lease is bounded because holding it blocks
progress. This marker blocks nothing — it silences one alert about one row
already written down — and expiring it would resurrect every addressed
escalation forever, which is the same flood the `ESCALATIONS_SINCE` epoch
exists to prevent. The absence of a TTL here is a decision, not an oversight.

**Amended by #130.** The original text rejects a successor agent journalling
that the condition lifted, on the ground that it makes the agent the judge of
whether the operator was reached. #130 implements exactly that row —
`retracted`, written by the level that raised the escalation — and the
rejection above is superseded, not deleted, because the two are answering
different questions. **The agent is still never the judge of whether the
operator was reached; #130 does not ask it to be.** A `retracted` row makes a
narrower claim: the specific, named condition that caused the escalation no
longer holds. The raising level can observe that fact directly, in the world,
the same way it observed the condition when it filed the escalation. Judging
"was the operator reached" stays forbidden and stays nobody's job but the
operator's; judging "does the condition I named still hold" is a different
question, and it is now recordable.

The ground also moved. When this decision was written, the operator's durable
clearing act was the `addressed` row this section originally described — a
real record that a human had been reached. #106 replaced it with **dismiss**,
which is browser-local `localStorage`: per-browser, invisible to every other
viewer, and no journal row at all. After #106, there is no durable record of
"the operator was reached" left for an agent retraction to usurp. The thing
the original rejection protected stopped existing.

The cost the original text did not price is the one #130 answers: with no
retraction verb, a resolved escalation outlives its cause indefinitely, and
the only remedy is the operator clicking dismiss in every browser they use.
Stale escalations accumulate on the dashboard with their conditions already
verified resolved.

What has NOT changed: no TTL, still deliberate (above); no digest-based
expiry; age is never evidence that a condition lifted. The malformed-marker
guards in point 1 apply to a `retracted` row unchanged — it is agent-written,
so it is MORE forgeable than an operator marker, not less, and must fail the
same way, toward LOUD. The journal is append-only, so a retraction never
erases the original `escalated` row. A recurrence of the same condition is a
new escalation, visibly the second one, not a reopening of the first.

`orch/core.py` (`escalation_filed`, `escalation_retracted`, `escalation_row`,
`open_escalations`); `orch/feed.py` (`ESCALATIONS_SINCE`); `orch/server.py`
(`a_file_escalation`).

---

## C. Rejected, and why it stays rejected

DESIGN.md's cut list is the authority; every entry there records its own
reasoning — read it before reintroducing anything. This section adds the
history the cut list does not carry: the big rejections that were live
proposals with real argument behind them, not just speculative machinery.

- **Timestamp comparison as the wake gate** (wake if newest
  `updatedAt`/commit/PR time > last ack time). Tempting because the data
  timestamps itself. Fails on both sides of the suppression class: process
  death has no timestamp anywhere (a worker exiting cleanly after opening a
  PR moves nothing — condition 5 suppressed forever, auto-land never fires),
  and a failed `gh` read has no timestamp either (call it "now" and every
  tick wakes until the oracle recovers). The ack must reference *situation
  identity*, not recency — and any encoding of "this situation" is the digest
  under another name. Cut list, first entry.
- **Hooks registering sessions** (`SessionStart`/`SessionEnd` writing ledger
  rows). Explored seriously in the #2 comments (2026-09-09) as the openclaw
  harness — registrar, PostToolUse binder, gate chains — where it was already
  demoted to safety net (blocked by the wrong-cwd problem, then obsoleted
  when orch launched `claude` itself). Rejected finally because Claude
  already registers every session by cwd exactly as orch's addressing needs;
  a hook row is a recorded cache of a derived fact, adds a writer to
  `<key>.json` that never took the flock, and `SessionEnd` is unreliable so
  `killpg` remains the truth anyway. Reopens only as `state/foreign.jsonl` if
  transcript-mtime liveness for foreign sessions proves measurably flaky.
- **Spawn-depth enforcement.** Both spawn verbs are tracked by construction
  (ledger row, or the Agent tool's in-process subagents under the parent
  key), so untracked proliferation cannot occur; a depth guard would need
  caller identity spawn() cannot verify. Tree depth is a red line in the
  agent docs instead.
- **Collapsing the six conditions into one predicate in code.** The predicate
  is true as an explanation and empty as a reduction — the six booleans are
  its cells, so collapsing removes names, not work, and checking agents need
  the names. The unifying paragraph lives beside the conditions; the code
  stays explicit.
- **Branch-as-record replacing the ledger.** Raised in the design session
  (the durable record does not carry the exchange; reasoning reconstructed
  from adjacent decisions): the ledger's job is liveness and spawn identity —
  pgids, which git cannot carry and `killpg` must invalidate at read time —
  and records must survive branch deletion, which unit branches do not.
  Related and recorded: git *is* the decomposition layer below units
  (commit-granularity tracking cut, #8 00:19Z), and the tick-pushes-`state/`
  transport was cut with the journal scope split (D2).
- **openclaw.** Dead dependency by 2026-09-10 — binary absent on this
  machine, the entire judgment layer routed through it dead code (#6 body).
  Its one real insight, transcript-scan recovery, survives as
  `unattached_json()`. Everything #2 built around it — the window/harness
  architecture, per-repo session keys, D5's backoff schedule, job units —
  was deleted or absorbed (the #2 revision comment, 22:10Z, is the item-by-
  item verdict table). `ORCH_AGENT`, the seam kept for a future adapter, was
  itself later cut as speculative.
- **The three-level tree.** The fresh review's cut of repo-orch, reversed by
  the operator on the layering principle — see A3 and D1. Stays rejected
  because the four-level tree is what keeps repo understanding out of
  dashboard-op and repo context isolated per repo.

Also rejected with reasoning worth keeping (cut list carries these):
`STALE` as a state (staleness is a conclusion; conclusions belong to whoever
holds context — README once promised a reclaim policy the code never had),
backoff schedules and `blockers.jsonl` (thresholds in the tick; the
error-alert-set-in-digest covers the live need), per-key attempt counters,
`notes/<slug>.md` (per-repo knowledge inside orch's tree *is* orch
understanding repos — killed by A2's principle, Fable 23:29Z).

## D. Reversals — decisions that changed, and what changed them

The most instructive section. Each of these was decided, then re-decided; the
argument that flipped it is the load-bearing part.

### D1. repo-orch: in → cut → restored

Five-level tree (2026-09-01 design) → cut by the fresh review (#8 body: its
decision content was routing dashboard-op already had facts for; one less
session to spawn, resume, wedge, recover) → **restored by the operator**
(23:25Z): routing was never the point; the level is where repo understanding
lives, and the cut would have made dashboard-op understand repos — precisely
what the layering exists to prevent. What made the cut *possible* was that
only routing had ever been written down; the fix is that the real mission is
now in DESIGN.md so the next fresh reviewer cannot re-derive the cut. Fable
retained one point: "which repos have conditions" is repo-agnostic, so the top
of the table stands.

### D2. The issue journal: local files → GitHub comments → rejected → restored on a scope split

Four positions in sequence:

1. **Local `.jsonl` files per scope** — the 2026-09-01 design (37b2c57).
2. **Journal-as-issue-comments proposed**, then **rejected by Fable**
   (#8, 00:19Z): "it inverts the durability ladder" — a journal write becomes
   a network call at decision time; network down means a lost entry or a
   local buffer-and-retry queue, which is the sync mechanism being avoided.
   Fable's pick instead: the tick commits and pushes `state/` (union-merge
   gitattributes, one-tick staleness window).
3. **Reversed two minutes later** (#8, 00:21Z, "SUPERSEDES"), after the
   operator's "one source" lean plus a scope split. The durability objection
   was overweighted: *the issue journal's author is a session whose every
   consequential act — claim label, merge PR — is already a `gh` call on the
   same channel. If the journal write fails, the acts it would record are
   failing too. The record rides the same channel as the acts it records;
   they fail together.* A symmetry, not a fragility.
4. **The resulting layering**: code is local-first because the work happens
   locally; the issue journal is GitHub-first because the acts happen on
   GitHub; repo and dashboard journals stay local because they are about this
   machine. Each record sits where its act happens. The tick-push transport,
   union merges, and the one-tick staleness window were all deleted; the
   failure path deliberately does **not** fall back to a local file (the
   mirror sneaking back in).

Bonus the choice bought: the handoff is readable with only `gh` on any
machine, orch not installed, and human comments in the thread become the
per-issue steering surface — from a phone.

### D3. Two lease schemes → one

#2's revision comment (22:10Z) deliberately established two leases with an
argument ("`.orch.pgid` is load-bearing across worker_alive, a_kill,
sessions_for, contended — the worktree is its natural scope"). The fifth edit
on #6 (22:25Z) reversed it, prompted by the operator's worker definition (A5):
the load-bearing argument "was inertia, not design" — every caller either
holds the key tuple or can derive it, and nothing distinguishes worker from
role at read time. With it died `CLAIM_GRACE_S` and the empty-stake window
(an artifact of the bash version staking an empty file before forking; holding
the flock across check → Popen → write-pgid closes the window), including its
two already-passing tests — deleted, not ported, per the addendum comment.
Side gain recorded: worker post-mortems now survive worktree removal.

### D4. The mtime filter → the one-key-one-cwd invariant

#6's fourth-edit comment found a defect: resume-id-from-newest-transcript
could `--resume` the **operator's own session** and run it as an orchestrator
when they shared a cwd. First fix: filter transcripts to mtime ≥ the ledger
row's `started`. The fresh review (#8 body, bug 3) identified that as a
symptom patch — every issue-orch in a repo still shared one cwd and could
cross-resume *each other*. The real invariant (B5) made the filter
unnecessary: exclusive cwds give correct resume identity by construction, and
the filter was deleted. Pattern worth remembering: the patch treated the
collision the operator had noticed; the invariant removed the whole class.

### D5. Smaller reversals, recorded

- **`spawn()`+`spawn_worker()` → one spawn** (D1 of #2 → #6 sixth edit) —
  folded into B6.
- **"No CLI for role spawn"** (#2 revision) → reversed same session (#6 sixth
  edit): repo-orch is a `claude -p` session acting through Bash; it needs a
  command line. Later widened into the full operator-parity CLI (A14).
- **`ORCH_AGENT` seam: kept → cut** — kept through every #6 edit as the
  adapter seam, cut by the fresh review as speculative (zero second runners).
- **`notes/<slug>.md`: in the design → deleted** (Fable, 23:29Z) — killed by
  the operator's A2 principle; conventions live in the repo's own
  CLAUDE.md/AGENTS.md, loading natively for workers.

### D6. Escalation retraction: rejected in B9 → allowed for the raising level

B9 explicitly considered a successor agent journalling that an escalation's
condition had lifted, and rejected it: that would make the agent the judge of
whether the operator was reached. #130 implements it anyway, as a
`retracted` row written by the level that raised the escalation. Not a
reversal of B9's reasoning — the operator-reached question stays off limits
to every agent. It is a reversal of what B9 assumed was the only question a
retraction row could answer. #106 also removed the durable `addressed`
record B9's rejection was protecting, replacing it with browser-local
dismiss; after that, there was nothing left for an agent retraction to
usurp. See the amendment inside B9 for the full argument, including what did
not change (no TTL, malformed-marker guards, append-only journal).

### D9. The escalation object: built, hardened, then removed entirely

**[operator]**, 2026-09-15, implemented on orch#198. This reverses the whole
line of decisions B9/#93/#106/#130 spent refining — not any single one of
their arguments, but the premise underneath all of them: that escalation
should be an object at all.

The object was an `escalated` repo-journal row, closed only by a `filed`,
`addressed`, or `retracted` marker naming its exact instant, plus a
browser-local dismiss store and a file-as-issue verb. Every refinement above
made it more correct and none made it fewer. The operator's verdict: **when an
agent hits a wall, the issue it is already working changes status, and that
status change IS the escalation.** Nothing is created, nothing accumulates,
nothing needs closing.

What made the cut, on measurement rather than taste:

- None of it was derived. A row stayed visible until an agent appended a
  marker, so the pile only grew.
- The markers were deliberately hard for agents to write — correctly, since
  an agent closing an alarm on the operator's behalf was the defect B9
  rejected. The consequence was rows nobody could clear at all.
- By 2026-09-16 the machine-wide alert array held 41 entries, 23 of them
  escalations. repo-orch spent a wake re-checking four open escalations for no
  result, recording *"Age is not evidence; none of these lift until the
  operator acts or I observe the condition false."* A permanently-red channel
  is not a channel anyone reads — which the issue body predicted before it
  happened.
- The one escalation that *did* clear that night cleared because the operator
  merged a PR, not because any marker verb was used. The state change did the
  work; the object only recorded it.

The mechanism replacing it already existed and needed nothing built:
`core.issue_state` is pure over labels and maps `agent-stuck` to ABANDONED.

Two parts of the design are load-bearing and were not free consequences of
the cut. **Comment first, then label** — both ride one channel, so a failed
comment means a failed label and an issue that stays CLAIMED and loud, whereas
label-first would permit ABANDONED with no reason recorded. And a **repo-level
finding**, which has no issue behind it, files once as a real issue carrying
`agent-stuck` *at creation*, because an unlabelled new issue is invisible to
orch — that is #93's "escalations never reached the dashboard" in new clothes.

**This narrows orch#153**, which reserved `agent-stuck` to issue-orch, and the
narrowing is deliberate rather than an unmarked exception: repo-orch now writes
that label when it files a finding. #153's reason was two writers racing on one
issue; a label set in the same call that creates the issue has no second
writer, ever, since no issue-orch exists for it. **#153 is restated as "one
writer per issue at a time", not "one role".**

Design by Fable, 2026-09-15; operator reaffirmed 2026-09-16. Not a reversal of
B9's core rule — whether the operator was reached is still not an agent's
judgement to make. It removes the object that rule was guarding.

Migration: none. The 44 `escalated` rows and 23 markers stay in the
append-only journals, unread. The three that were open and unanswered at the
cut were carried by hand to durable homes first (comments on orch#280 and
orch#284, and orch#305 filed for the third), on the ground that a design whose
point is that findings must not be lost cannot lose three operator questions
on its way out.

## E. Bugs found by review, not by testing

Five found by design review across the rounds — each a place where
one-at-a-time decisions composed into a system that was wrong as a whole —
then two found during the build, and one found only by running it.

| # | bug | found by | fix |
|---|---|---|---|
| E1 | **Wake gate rebuilt the eight-day silence.** Gating on "digest changed since last *spawn*" meant a dashboard-op dying mid-decision over a static world was suppressed forever — the third instance of the same suppression class (after `idle_over` and error-alerts), and nobody had noticed it applied to the wake itself. | fresh review (#8 body, bug 1) | gate on the digest dashboard-op *acknowledged*; failure degrades to loud repetition |
| E2 | **Forward progress woke nobody — auto-land unreachable.** Every condition counted pathology; a worker finishing cleanly (PR green, everyone exited) matched none, so every issue stalled at its first completed unit. | fresh review (#8 body, bug 2) | condition 5, "unowned work" |
| E3 | **Shared cwds broke resume identity** (see D4). | fresh review (#8 body, bug 3) | one-key-one-cwd |
| E4 | **Ack ≠ chain-took.** Dashboard-op acks digest D after spawning; the chain below dies before changing the world; the world regenerates exactly D, already acked — silence again. Present in the 3-level design too; tracing the restored 4-level chain surfaced it. | Fable, self-found while conceding the repo-orch reversal (23:29Z) | the conditional ack: ack only on a wake where you spawned nothing |
| E5 | **Auto-land gated behind a 30-minute idle wait.** Condition 5 with a bare `idle_over` gate delayed every auto-land by `NUDGE_IDLE_MINS` — a finished unit is *waiting*, not *idle*. | design round; confirmed applied at build (PR #10 report) | the REVIEW disjunct in condition 5 |
| E6 | *(companion, #8 body bug 4)* **The digest had no rule, so it accreted** — each prior digest bug had been patched ad hoc. | fresh review | the digest rule: hash exactly what the conditions read; condition and digest inputs land in one commit |
| E7 | **`build_thin` re-derived liveness** with a second `core.alive()` walk — the separate-walker pattern the digest rule forbids; it reported alive for dead keys. | build review (PR #10) | consume feed's single pass; B4 enforced |
| E8 | **feed/tick field mismatch.** The feed's wedged alert read commit-only `idle_min` while the tick's condition 6 read recursive `activity` (including subagent transcripts) — a worker busy in subagents read WEDGED in the exact status.json dashboard-op consumes, inviting a kill of healthy work. | build (commit f044125) | both read `u["activity"]`, same threshold, same None handling |
| E9 | **Nested `claude -p` inherited a live session's env and hung.** orch is routinely invoked from inside a Claude session; a child inheriting the parent's bridge/auth vars (`CLAUDE_CODE_*`, `CLAUDECODE`, `ANTHROPIC_API_KEY`, …) starts, loads MCP, and never produces output. | running it | `_launch` builds the child env from a named strip-list, never passes `os.environ` through |
| E10 | **A hung dashboard-op suppressed every wake, unbounded.** The gate's second clause, *dashboard-op is not alive*, is derived from `killpg`, so it cannot lie about a process existing — but alive is not attending. A stalled dashboard-op holds its pgid indefinitely; every tick read alive, skipped the wake, and condition 6 watches issue-orchs only. The fifth member of the suppression class, and the longest silence left: #34 bounded a bad ack to `ACK_TTL_MINS`, this path had no bound at all. | settling #34 (#35 body) | condition 6's idiom applied to dashboard-op's own key: alive ∧ own `transcript_activity` idle → `dashboard-op-hung` error alert. Surfaced, never killed. Traced: the alert changes the digest, but clause 2 still short-circuits on alive, so it does **not** spawn a second dashboard-op |
| E11 | **A persistent error alert with no stable `key` would flap the digest.** `build_thin` hashes `a.get("key", json.dumps(a))`; no alert carried a `key`, so the whole dict was hashed. An alert that survives across ticks with an idle count in its message re-hashes the digest every tick — the wake spam the digest rule forbids. Latent until E10 added the first cross-tick error alert. | tracing E10 before building it | error alerts that can persist carry a stable `key` and a fixed message |
| E12 | **Escalation had no receiver.** Both agent docs told an agent to journal an `escalated` row and leave it visible, and forbade inventing a notification channel because "the operator reads the widget and the journals" — but nothing promoted those rows to the widget, and the feed carried journal rows only in an eight-row tail, so an *unresolved* escalation expired from the dashboard. The orch repo's own journal held 29 such rows. No agent was misbehaving and nothing was malfunctioning: the protocol was followed exactly and had no other end. The sixth member of the suppression class, and the only one where the silence was written into the docs rather than the derivation. | #93 | one entry per open escalation into the existing `alerts` array at `error` level, key `escalation` — Zone A, not a second mechanism (#69) — cleared by a recorded per-row `addressed` marker (B9), with `ESCALATIONS_SINCE` treating pre-change rows as already seen rather than backfilling 29 alerts nobody can act on |

The lesson the five review bugs share, worth stating once: each condition,
gate, and address was individually defensible when decided; the failures lived
in the composition, and only a whole-design read (deliberately fresh, no
inherited context) could see them. E4 is the sharpest case — the designer
found the hole in its own just-restored chain by tracing it end to end.

Two more found by running the old code against reality, same family:
contention detection was unsatisfiable dead code inherited from the old feed
(6e26df6), and `spawn()` passed the stdin prompt through raw so the chain died
at level 2 with a one-line brief — the 2026-09-01 "gives no command" failure
rebuilt (99e11f5, fixed by `compose_brief`).

## F. Still open

Genuinely undecided, owed mostly by the operator.

### F1. The `TODO(user)` markers (all since answered — see `agents/repo-orch.md` and `agents/REPO-SKILL-INTERFACE.md`)

The agent docs are skeletons: mechanics filled, judgment explicitly absent
(B8). Each marker names the decision that belongs there and the tradeoff in
both directions. (README says 17 — stale; two mechanical items were filled by
commit 4609a48. Current count on disk: 15.)

| file | owes |
|---|---|
| `agents/dashboard-op.md` (2) | defer policy — when to hold a repo back on a wake you could have spawned into; wedge threshold — how much expired-lease silence, how many repeats, before killing |
| `agents/repo-orch.md` (4) | ordering rule for multiple agent-ready issues; re-entry rule for a CLAIMED issue with a dead issue-orch; defer rule and how long a deferral stands; escalation rule — what becomes the operator's problem |
| `agents/skills/*/SKILL.md` (3, moved from `agents/issue-orch.md` in the thin-core split: decomposition + worker-brief bar in `issue-units`, auto-land-absent in `issue-landing`) | decomposition rule — what makes a good unit boundary; give-up threshold — when `agent-stuck` beats another worker; auto-land-absent policy — a REVIEW unit with no `auto-land` re-fires condition 5 every tick, so decide what re-entry does |
| `agents/worker.md` (2) | prose voice/length for the brief's target+delta section; the acceptance bar a brief must clear before issue-orch spawns on it |
| `agents/REPO-SKILL-INTERFACE.md` (4) | the skill-side restatement of repo-orch's four judgments (ordering, re-enter-vs-escalate — note: NOT defer, a deferred CLAIMED issue loops condition 5 forever; defer with a lift condition; escalate with the decision a human owes) |

### F2. The repo-management skill body

Does not exist. Load-bearing the same way the agent docs are — repo-orch
idles as a thin router until it does. The interface orch owes it is specified
(`agents/REPO-SKILL-INTERFACE.md`); the body is the operator's to write.
Tracker item, not doc polish.

### F3. Everything else outstanding

- **Issue #6's body rewrite** — #8 item 4, still unchecked; #6's body and six
  edits are superseded and carry only a warning banner.
- **Closing #2, #3, #6** — all still open on GitHub. #3's Option A is in the
  worker brief; closing it "is yours to call" (PR #10 report). #2 is mostly
  superseded paperwork.
- **`repos.txt`** points at four paths that do not exist on this machine
  (commit 4609a48) — machine-local config edit owed.
- **The five tunables** (B1) — revisit on the first real-queue run.
- **Journal scope for spawns** — implementer's mapping flagged for the
  operator's look (B8); DESIGN.md now documents the two-scope rule, so this
  is closed unless the operator disagrees with it.

---

*Compiled 2026-09-10 from the sources listed at top. Where the durable record
does not name a decider, this file says so instead of guessing (A13, C's
branch-as-record entry, parts of A3). If a future decision changes anything in
section A, it belongs here as a dated reversal in section D — that is the
pattern this system's history rewards.*
