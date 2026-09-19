# orch — design

A tracker for Claude Code sessions doing work across repos, with controls to
stop, start, and restart any of them from any surface. It is not an automator:
it does not decide what work is worth doing. It shows you everything and lets
you act. Judgment lives in agents that hold context; orch supplies mechanics —
spawn, lease, wake, record — and never learns what a repo means.

This document is complete: an implementer builds the `orch/` package from it
without reading the old root-level `.py` files. Where an old file holds a
subtlety worth preserving, this document says which file and what to look for.

Two rules govern everything here: minimum code that solves the problem,
nothing speculative; and deletion over addition. Every mechanism below earned
its place by surviving a cut attempt. The cut list at the end records what
did not, and why, so it stays cut.

## The levels

    tick             dumb. derive the world, rebuild the dashboard, gate the wake.
      dashboard-op   machine-wide. which repos have conditions. spawns repo-orch.
        repo-orch    one per repo. where repo understanding lives. spawns issue-orch.
          issue-orch one per issue. owns the issue worktree and its git. runs the issue pipeline.
            worker   Agent-tool subagent inside issue-orch. edits files. never git.

Workers are not an orch level: they are Agent-tool subagents dispatched by
issue-orch inside its own session — same process, same pgid, transcripts
under the parent key. orch spawns three roles and addresses nothing below
issue-orch.

Each level supervises the one below it. The tick supervises nothing — it
knocks on the top of the tree and exits. That makes it the unmoved mover:
mechanical, restartable, incapable of being wrong about the work.

**Do not make the tick smarter.** When a level misbehaves, the fix is
supervision inside the tree, not intelligence in the tick.

The governing principle for where a boundary sits:

> **Orchestrators move things along. They do not understand repos.**
> The test is not "could this decision be made elsewhere" but "does making it
> here force this level to understand something outside its scope."

### What each level knows

| level | knows | does not know |
|---|---|---|
| dashboard-op | which repos have conditions | anything about any repo |
| `repo-orch.<slug>` | this repo: conventions, what matters, what is worth driving | other repos; how to do the work |
| `issue-orch.<slug>.<n>` | this issue: units, briefs, git, landing; runs the issue pipeline | the repo's broader picture |
| worker (subagent) | this unit's target files | everything else — including git |

"Which repos have conditions" does not smuggle repo understanding into
dashboard-op: condition counts are repo-agnostic mechanical facts.

### Each level's mission — the real one

**dashboard-op** reads the dashboard the tick hands it and decides, per repo
with a condition, whether to spawn that repo's repo-orch, and nothing deeper.
It applies the long-running-lease rule before judging a wedged-looking session
stuck. Its last act is either an acknowledgment or a deliberate non-ack (see
the wake gate). One per machine.

**repo-orch** is not a router. It is where repo understanding lives, so
nothing above it has to. It runs the repo-management skill the way
issue-orch runs its issue pipeline: the skill holds the repo
understanding; repo-orch is the session that holds the skill's context. Its mission: which UNCLAIMED `agent-ready`
issues are worth driving, in what order; what to defer; what to
escalate to the operator. It holds up to **five issues in flight at once**
(operator-set bound: live issue-orchs plus CLAIMED issues it is driving; a
sixth ready issue is deferred and journaled). It has two verbs: spawning
issue-orchs, and — only on a journaled merge escalation — merging an
issue's PR (see "Merge blocked" below). It journals one line per
decision and exits. It writes one label — `agent-ready`, to nominate work
(orch#153) — and never `agent-working`, `agent-stuck`, `auto-land` or
`no-auto-land`. There is no worker role
for it to spawn. One per repo; keeping repo context for N repos in N
separate sessions instead of piling it into the one machine-wide agent is
why the level exists.

**issue-orch** owns one issue end to end: claims it (the `agent-working`
label), decomposes it into units (its recorded work breakdown, not orch
state), runs the issue pipeline in the issue worktree, dispatches workers as Agent-tool
subagents — in parallel only on disjoint target files — judges results,
commits and pushes, merges (or escalates a merge it cannot do cleanly), and
marks `agent-stuck` when it gives up. Its cwd IS the issue's git worktree
on branch `issue-<n>`, and it is the **sole owner of git state** there: one
owner, many editors. It owns the issue's labels exclusively. One per issue.

**worker** is an Agent-tool subagent of issue-orch: one unit's target
files, one brief, edits only — it never runs git, never runs the pipeline, and
reports its edits back to issue-orch, which commits them. It decides
nothing about the issue. N per issue — how many, and whether in parallel,
is issue-orch's judgment, bounded by one rule: parallel workers get
disjoint target files.

Cardinality is enforced for free: all three roles are singletons
*because the key is the scope*, and the flock on the ledger key is the guard.
Two attempts to spawn `issue-orch.foo.42` cannot both win — the second takes
the flock, sees a live pgid, and refuses. Singleton-ness is not a feature to
build.

### No level decides its own fate

The rule that unifies every "what do I do when I am stuck" answer in this
design:

> **A blocked level journals why and stops. The level above finds out
> through the tick and chooses.**

Not "escalates and waits" — there is nothing to wait in. Each level:

| level | when it cannot proceed | who chooses next |
|---|---|---|
| worker (subagent) | returns `blocked` with the reason | issue-orch |
| issue-orch | journals to the issue, exits | repo-orch, on re-entry |
| repo-orch | journals to the repo, exits | dashboard-op |
| dashboard-op | journals, leaves state visible, exits | the operator |

**Dying IS the deferral.** This is not a mechanism that had to be built — it
is already true structurally, because every level is a stateless session that
exits anyway. The parent learns of it through the tick (condition 5, unowned
work), not through a message, because there are no messages.

**Nothing can sleep.** No level may wait, poll, or hold itself open for a
condition to change. The tick is the only clock in the system. So the real
choice a blocked level faces is not *defer versus continue* — it is *exit*
versus *journal why, then exit*, and the journal is precisely what lets the
parent choose well instead of guessing. A level that exits silently hands its
parent an ambiguity; a level that journals hands it a decision.

The corollary is the one that keeps judgment where the context is: a parent
chooses **whether to re-enter**, never **whether the work is hopeless**. Only
the level that can see the work can conclude that, which is why repo-orch's
write-off is itself a spawn (see "Giving up") rather than a label.

## Derived versus recorded

**Derived — cannot lie.** Re-read every tick from the world itself:

| fact | source |
|---|---|
| is this issue claimed / abandoned | GitHub labels |
| is there work | commits on `issue-<n>` ahead of base |
| is it proposed / landed | PR state |
| is CI green / red / pending | check rollup, fetched once per open PR per tick |
| is an actor alive | `os.killpg(pgid, 0)` on the ledger row's pgid, at read time |
| what has it been doing | `~/.claude/projects/<mangled-cwd>/*.jsonl` transcripts |
| is anything orphaned | transcript dirs matching no key (`unattached`) |

**Recorded — may lie, so nothing gates on it** (one exception, bounded
below)**:**

| record | serves |
|---|---|
| issue journal (GitHub issue comments) | the handoff a fresh issue-orch or human continues from |
| repo journal (`state/repos/<slug>/orch.jsonl`) | repo-orch's memory across sessions |
| dashboard journal (`state/dashboard-op.jsonl`) | dashboard-op's memory; carries the ack |
| ledger rows, rolled rows, run logs | what orch spawned; what every past run did |
| plan files (`tmp_plan-*.md`, committed on the issue branch) | the issue pipeline's single source of truth |

**The one recorded fact that gates: the ack.** Attention is not derivable —
"an agent attended to this situation and chose to defer" moves nothing in
git, GitHub, or the ledger, by design — so the ack must be recorded, and it
must gate the wake, or every standing human-owed condition wakes forever.
What makes that safe is the same rule that protects every other trusted
record here: the long-running-lease rule. An ack is a **lease on the
machine's silence**, never a permanent fact; it expires after
`ACK_TTL_MINS`, and an expired ack over a standing digest re-wakes. A wrong
ack costs bounded silence, never permanent silence — corrected by time where
it cannot be corrected by derivation. (See "The wake gate and the
conditional ack".)

A recorded row that mirrors world state is a cache. Verify liveness before
acting on it. The derived/recorded split is not "GitHub versus local" — it is
"what the state functions read versus everything else." Issue-journal
comments live on GitHub and are still recorded: the state functions' input
set is labels, PRs, commits, rollups, and nothing more.

> A reset clears conversation. A reset does not clear the record.

This is why restart is safe at every level: durable state never lives in a
session. Journals are the handoff, not an audit log.

## The state machine

Two independent state functions, plus liveness as a deliberately orthogonal
axis. State is re-derived every tick and never stored; there are no illegal
transitions to validate, only anomalies to surface. Backwards edges are legal
and self-correcting (a force-push moves ACTIVE back to CLAIMED; a new commit
on an open PR moves REVIEW back to CHECKING).

### Issue

Pure over GitHub labels, first match wins:

    agent-stuck        → ABANDONED
    ¬agent-working     → UNCLAIMED
    else               → CLAIMED

`ABANDONED` is terminal by convention, not construction: it is a label a human
or agent can remove. `auto-land` and `no-auto-land` are a fourth and fifth
label, read by issue-orch at the Land stage, not by the state functions.
One flag, not two: `auto-land` means *review, then merge* — there is no
separate `auto-review` label. A repo-wide default for `auto-land` lives
outside GitHub entirely, nested in the repo's own entry in
`ORCH_HOME/orch.json` (orch#297) — see "Merge
blocked" and `agents/skills/issue-landing/SKILL.md` for the precedence
between the labels and the repo default.

`agent-stuck` means only the write-off case in "Giving up" below — gave up,
one label, ABANDONED. A blocking review finding under `auto-land` is not
that case and does not touch `agent-stuck`: it is recorded in the PR's
`orch:review:v1` comment, and `core.merge_pr` re-derives the hold from that
block on every merge attempt (`core.review_blocks_merge`, orch#408) — no
label written, and the work state stays at `REVIEW`. That is not a new
state and not a new label — a `REVIEW` issue with a blocking review finding
on its PR is the hold this design already has, the same one a repo default
of off produces, re-examined every tick by condition 5 and merged by nobody
because the merge gate refuses it. See "Review before landing" below for
the mechanics and why this is recorded by the posted review comment rather
than a new store.

### Work — per issue, from the issue branch and its PR

One branch (`issue-<n>`) and one PR per issue. Units are recorded, not
derived — there is nothing smaller than the issue for the state functions
to see. First match wins:

    PR MERGED               → LANDED
    PR OPEN ∧ green         → REVIEW
    PR OPEN ∧ red           → BLOCKED
    PR OPEN ∧ ¬green ∧ ¬red → CHECKING
    ¬PR ∧ commits ahead     → ACTIVE
    ¬PR ∧ ¬commits          → CLAIMED

The forward path, as documentation only:

    UNCLAIMED → CLAIMED → ACTIVE → CHECKING → REVIEW → LANDED
                                       ↕
                                    BLOCKED

### `pr_green` and `pr_red` are not complements — load-bearing

| rollup | green | red | state |
|---|---|---|---|
| `[]` / `None` (no CI) | true | false | REVIEW |
| all SUCCESS/SKIPPED/NEUTRAL | true | false | REVIEW |
| any FAILURE/ERROR/TIMED_OUT/CANCELLED | false | true | BLOCKED |
| PENDING only | false | false | CHECKING |

Delete either asymmetry and CHECKING becomes unreachable. Port
`pr_green`/`pr_red` from the old `core.py` (lines ~174–188) verbatim, and
port the tests pinning all four cases (`no_ci_is_green`, `null_is_green`,
`pending_notgreen`, `pending_notred`).

Signed-off consequence: a repo with no CI — or broken CI config emitting an
empty rollup — reads as green, hence REVIEW, hence landable. Absent CI and
passing CI are indistinguishable by construction. Accepted; the tests pinning
it stay.

Also port from old `core.py`: the rollup is fetched once in `World.load` for
open PRs only and stored keyed by branch; `_rollup` is a pure dict lookup.
This keeps `pr_green` and `pr_red` reading the same snapshot — they can never
disagree about one PR within a tick. `work_mtime` is still read live at
evaluation time; one snapshot plus one live read, self-correcting next tick.
`pr_for` collapses multi-PR branches deterministically: OPEN beats MERGED
beats first-seen.

### Liveness — the orthogonal axis

`alive(key)` = ledger row exists ∧ `killpg(pgid, 0)` succeeds. It never feeds
`work_state`: state is about the work, liveness is about the actor. Workers
are threads inside issue-orch's process, so issue-orch's liveness IS the
issue's liveness — there is no separate worker axis, and "issue-orch dead
but a worker alive" is impossible by construction. The product:

| work state | issue-orch alive | reading |
|---|---|---|
| CLAIMED | true | staked, pre-first-commit — normal briefly |
| CLAIMED | false | died before producing anything (dirty files may survive) |
| ACTIVE | true | healthy |
| ACTIVE | false | **dead mid-flight** — the busted-session case; commits and the dirty tree survive on disk |
| CHECKING | any | waiting on CI |
| REVIEW | true | deciding the merge, or holding without `auto-land` |
| BLOCKED | false | needs the one fix round |
| any | 2+ live sessions | **contended** |

Every one of these rows is reachable by the wake conditions below — the old
design computed this product nowhere, which is why a dead-mid-flight session
was invisible for eight days.

There is deliberately no STALE state: staleness is a conclusion, and
conclusions belong to whoever holds context. Keep that comment in the code.

## Addressing

One scheme. The key names the actor; the key determines the cwd; the cwd
determines the transcript directory; therefore resume identity is correct by
construction.

| key | cardinality | cwd (under ORCH_HOME) | resume | cwd is |
|---|---|---|---|---|
| `dashboard-op` | one, machine-wide | `state/dashboard-op/` | yes | plain dir |
| `repo-orch.<slug>` | one per repo | `wt/<slug>/` | yes | plain dir |
| `issue-orch.<slug>.<n>` | one per issue | `wt/<slug>/issue-<n>/` | yes | **git worktree** on `issue-<n>` |

Workers have no row: an Agent-tool subagent has no cwd of its own and no
resume identity to protect — its transcript lands under
`<session-id>/subagents/` beneath issue-orch's transcript dir, which the
flat/recursive glob split below already handles. Two OS sessions sharing
the issue worktree would be exactly the one-key-one-cwd violation; that is
why workers are subagents and not spawned processes.

**The invariant: one key = one exclusive cwd.** No key shares a cwd with any
other key, with the operator's interactive sessions (dashboard-op's cwd is
NOT ORCH_HOME — the operator uses that), or with a repo checkout. Claude
keys transcripts by mangling the exact cwd string — `/`, `.`, `_` all become
`-` (the translate table is in old `core.py` `session_dir()`). Distinct
strings give distinct transcript dirs; parent/child nesting is irrelevant to
the bijection. So:

- resume id = newest `.jsonl` stem in the key's own transcript dir, full stop.
  No mtime-versus-started filtering; the exclusivity makes it unnecessary.
- contended = more than one live session in one key's transcript dir.
- the filesystem mirrors the org chart: repo-orch's cwd contains its
  issue-orchs' worktrees.
- `wt/` in every orch cwd preserves the `-orch-wt-` substring the orphan
  scan excludes (see old `feed.py` `unattached_json()`).

Naming caveat, inherent to the mangling: a repo literally named `foo-issue-7`
collides with repo `foo`'s issue-7 orch. Do not name repos to collide with
the `<slug>-issue-<n>` pattern.

The issue worktree: `wt/<slug>/issue-<n>/` is a git worktree on branch
`issue-<n>`, created by spawn before launching issue-orch
(`git worktree add -b issue-<n> <path>` from the repo checkout; plain
`git worktree add` when the branch exists — e.g. re-entering a dead
issue). Every worker edits inside it; only issue-orch runs git in it.

Units are issue-orch's work breakdown, **recorded, never derived**: they
live in the plan file and the issue journal, and orch neither addresses
nor tracks them — the state functions see one branch and one PR per issue
and nothing smaller. A commit is a step *inside* a unit; git is that
layer. Nothing in orch addresses, tracks, or recovers at commit
granularity.

`<slug>` is the repo basename; the GitHub `owner/name` is derived from the
checkout's origin remote (port `gh_repo` and `base_ref` from old `core.py` —
base is `origin/HEAD`, falling back to `origin/main` then `origin/master`).

## The ledger

`state/sessions/`, one key per actor. It is the ownership lease and the spawn
record. The row cannot outlive its owner: `killpg(pgid, 0)` at read time
invalidates it the moment the process dies, which a recorded claim never
could.

- `<key>.json` — current row:
  `{"role","scope","workdir","pgid","started","log"}`
- `<key>.log` — append-only, never truncated. Each run starts with a header
  line `=== <iso> pgid <n> ===` so runs are separable. Raw `claude -p` stdout
  and stderr append here. The run you most want to debug — the busted one —
  survives respawn.
- On respawn over a dead row, the old row is **rolled aside** to
  `<key>.<started-ts>.json`, then the fresh row is written. Nothing is
  overwritten in place; nothing is ever destroyed. No pruning, no rotation,
  no retention policy, no archive subdirectory. Add pruning only when size
  actually hurts.

**The flock protocol.** All spawn-time mutation happens under an exclusive
`fcntl.flock` on `<key>.json`, held across the whole sequence:
read row → `killpg` check → refuse if alive → roll dead row aside →
`Popen(start_new_session=True)` → write the new row with the child's pgid →
release. The parent knows the pgid immediately, so there is no window where
the row exists without a pgid and no grace period is needed. This one lock
is every idempotency guarantee at once: concurrent POSTs, respawn of a live
key, double-wake — any double-spawn of any key loses the race and refuses.
(The tick has its own separate `.tick.lock` flock, LOCK_EX|LOCK_NB,
skip-if-held — port the pattern from old `tick.py` `main()`.)

Rolled rows and logs are recorded, not derived: nothing gates on them. They
exist for the operator and post-hoc debugging, surfaced in the feed as a
`prior_runs` count per key. Dead rolled rows are never presented as current.

Ledger rows carry pids that are meaningless on another machine — and that is
handled by design, not cleanup: nothing ever trusts a recorded pid, so on a
new machine `killpg` reports every inherited row dead and respawn rolls them
aside normally. Stale ledgers are self-neutralizing.

**Long-running leases are journal entries, not files.** An actor running
something long journals `long-running until <T>`. Its supervisor does not
judge it stalled while the lease holds; past T with no progress, it is stuck.
This applies at every level. It is the only honest way to tell a wedged
session from a slow one, and without it every supervisor eventually kills
healthy work.

Gitignored: all of `state/`. It is machine-local by design and does not
travel — the issue journal (the only record that must be portable) lives in
GitHub issue comments, and the repo and dashboard journals are *about this
machine's* orchestration, so a second machine legitimately starts its own.
The ledger is pids, meaningless elsewhere. Nothing in `state/` belongs in git.
`state/` as a whole is machine-local and does not travel — see cross-machine.

## spawn()

    spawn(role, scope, prompt, fresh=False) -> pgid

The one place a process ever starts. `role` ∈ {dashboard-op, repo-orch,
issue-orch}; `scope` is the slug / slug+issue that completes the key.
**Workdir is derived, never a parameter** — the addressing table above is the
whole mapping, and callers cannot get it wrong because they cannot express
it. Resume is derived too, but the parent may decline it (`fresh`, below).

Semantics, in order:

1. Derive key, cwd. Ensure the cwd exists (mkdir for dashboard-op and
   repo-orch; git worktree + `issue-<n>` branch creation for issue-orch —
   the only role-specific pre-spawn step).
2. Take the flock on `<key>.json`. Live pgid → refuse, report the live row.
   Dead row → roll aside.
3. Derive the resume id: newest transcript stem in the key's transcript
   dir, if any. Every role resumes. (Workers stay cold for free: the Agent
   tool starts every subagent fresh.) **Unless `fresh=True`, which skips
   derivation entirely and starts the key cold** — see below.
4. Launch: resolve `claude` via `shutil.which` (PATH, so `~/.local/bin/claude`
   — an rc alias like `claude`→`happy` never reaches `subprocess`; a wrapper
   must be a real executable). Argv is a fixed list:
   `claude -p --permission-mode acceptEdits --settings <key>.settings.json
   --append-system-prompt <caveman skill>` plus `--resume <id>` when one
   exists. Prompt (the brief) on stdin. `start_new_session=True`, cwd set,
   stdout/stderr appended to `<key>.log` under a fresh run header.
5. Write the row, release the flock, journal the spawn to the appropriate
   scope.

### The caveman skill — compression as a flag, not as brief text

Every spawned session loads the caveman compression skill. orch reads it from
`~/.claude/skills/caveman/SKILL.md` and passes it on `--append-system-prompt`.
The two alternatives both cost more. A line in `compose_brief` puts a fourth
part beside three that are each load-bearing — the agent doc is the actuation,
the journal tail is the only memory that crosses sessions, and the spawn reason
is why this session exists — so it risks a reorder or a displacement. Inlining
the skill text, about 5 KB, spends brief tokens on every single spawn, which
partly defeats the purpose. A flag costs no brief tokens and holds for the
whole session.

The level is `lite`, and it is the same level for all three roles. The skill's
own intensity table keeps articles and full sentences at `lite`, and drops
articles and permits fragments at `full`. Its Auto-Clarity section says to stop
compressing where compression creates technical ambiguity, and on multi-step
sequences where fragment order or an omitted conjunction risks a misread. orch
journals and escalations are exactly that content: ordered steps that carry
pgids, paths, issue numbers and counts. One level, not a level per role,
because a per-role level is a tunable with no evidence behind it. Measure
`lite` first. Raise one specific role later if its output stays too verbose.

### `fresh` — the parent picks resume versus cold

    spawn(role, scope, prompt, fresh=True)
    echo "<brief>" | ~/orch/spawn.py <role> <scope...> --fresh

Resume-vs-fresh is the **parent's** choice, not a derived fact. Facing a
stopped child, a parent can wake it (resume its transcript), re-enter the
same key with a fresh head, or brief it to assess and give up; only the
middle one lacked a mechanism, because `spawn()` derived a resume id
unconditionally whenever a transcript existed for the key.

That matters because **context exhaustion is the expected death mode of this
design** — every level is a stateless session that runs until it runs out —
and resuming an exhausted conversation reproduces the exhaustion. A parent
judging the prior conversation spent had no way to say "same key, fresh
head".

- **Default is `fresh=False`: resume.** Continuity is usually right, and
  this is exactly the prior behaviour, unchanged.
- **`fresh=True` skips resume-id derivation entirely** — argv carries no
  `--resume`, and differs from a default spawn in that alone.
- **The dead row still rolls aside**, either way. Abandoning a session never
  destroys its record; the rolled-aside row and the run log stay for
  forensics.
- The cold-retry path is untouched: it exists for a *bad* resume id, and a
  `fresh=True` launch has no resume id to be bad.

Which levels use it, and when, is agent judgment and lives in the agent docs
(`dashboard-op.md`, `repo-orch.md`): the parent judges the prior conversation
spent — context exhaustion, or it died confused.

### The permission envelope — part of the argv contract

`--permission-mode acceptEdits` permits file edits and **not Bash**. Every
agent doc hands its level literal Bash commands as its only verb, so for a
while the entire tree was specified to act through commands it was not
permitted to run. Observed, not theorized: a real dashboard-op read the
dashboard correctly, identified unowned work, and was denied `spawn.py`
twice, denied its own journal append, and denied reading that journal. The
chain reached level 1 and stopped. Its *logic* was right — it refused to ack
because it had not resolved the condition, exactly as the conditional-ack
rule requires. The envelope was wrong.

So `spawn()` writes `state/sessions/<key>.settings.json` and passes it with
`--settings`. Two measured facts about claude's permission layer fix the
shape, and both must be re-checked if this is ever revisited:

- **`--allowedTools` adds permission; it does not restrict.** Under a user
  `settings.json` with a broad allow list, a session launched with
  `--allowedTools 'Bash(echo:*)'` still ran `touch`. An allowlist alone
  enforces nothing, so it cannot carry a red line.
- **`deny` in a settings file enforces, and beats allow.** The same `touch`
  was refused, command never executed, including when allow and deny both
  named it.

Therefore: **allow makes a role able to do its job; deny is what makes a red
line real.** The envelope is per-role because the red lines are per-role.

**The allow list is derived from the agent docs, never maintained beside
them.** The docs already carry every literal command in ` ```bash ` fences —
that is what "load-bearing actuation" means — so the doc is the single
source: a doc that teaches a verb grants it, and a verb deleted from a doc is
revoked on the next spawn. This is the same one-representation discipline the
digest rule enforces, applied to permissions; a hand-maintained per-role list
would be exactly the drift surface this design refuses elsewhere. Only
` ```bash `-tagged fences are harvested — the docs also fence JSON and
English prose, and reading those as commands grants nonsense. `gh` and `git`
keep their subcommand (`Bash(gh issue edit:*)`, not `Bash(gh:*)`), because a
blanket rule would hand every level both red lines at once.

The settings file sits beside the ledger row deliberately: what a session was
permitted to do is part of the record of what it did, and it is inspectable
after the fact. It is rewritten every spawn, so an edited doc takes effect on
the next spawn with nothing to migrate. It is not derived state — nothing
gates on it — and `prior_runs` excludes it explicitly, since that glob is
issue-orch's only attempt counter and an off-by-one there is an off-by-one in
a give-up decision.

issue-orch is the deliberate exception: it runs the issue pipeline,
which dispatches subagents and writes arbitrary code, so no useful
narrow allow exists and
its envelope is broad. Its containment is structural rather than
enumerated — one issue worktree, one branch, one issue's labels — plus the
deny that matters at its level: it never runs `spawn.py` (its workers are
Agent-tool subagents, and killing is the operator's act). Worker subagents
inherit that same envelope; what keeps them off git is the brief's red
line plus issue-orch judging their reports, not a per-subagent settings
file.

**Open decision — skills versus the derivation rule (decide when we
build it; `role_settings` unchanged until then).** issue-orch's doc is
now thin core + phase skills (`agents/skills/*/SKILL.md`) + tool
templates, so some of its literal commands live outside its agent doc.
Mechanically nothing changes: issue-orch's allow is the hardcoded broad
list (`_ISSUE_ORCH_ALLOW`), and `_doc_commands` never reads its fences
anyway. But it deepens the existing inconsistency — "the allow list is
derived from the agent docs" is stated absolutely above, issue-orch was
already the exception, and now its commands are not even in one file.
Options:
  a. **Accept and scope the rule**: state that derivation applies to
     dashboard-op and repo-orch only. issue-orch's broad allow is forced
     by the pipeline — arbitrary code has no narrow allowlist — so derivation
     over its files could never narrow anything. One sentence of doc
     change, no code.
  b. **Harvest the skills too** (`_doc_commands` also globs
     `agents/skills/*/SKILL.md` and the tool templates) and derive
     issue-orch's allow from them. Dead end today: the derived list must
     still include bare `Bash` for the pipeline, so harvesting reproduces the
     broad allow with extra steps and adds a false sense of enumeration.
The tradeoff is the stated rule's honesty (a) versus a harvest that
cannot narrow anything (b). Nothing gates on this; the deny list — the
half that enforces — is untouched by the split either way.

**Spawns must not inherit a parent Claude session's environment.** orch is
routinely invoked from inside a Claude Code session — an agent running
`spawn.py` via its own Bash tool, or a tick started from one — and
`subprocess.Popen` inherits the parent environment by default. A nested
`claude -p` that inherits a live session's bridge/auth vars
(`CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_PID`, `CLAUDE_EFFORT`,
`ANTHROPIC_API_KEY`) hangs forever — it starts, loads MCP servers, and never
produces a transcript or output. `_launch` builds the child's environment
from a named strip-list instead of passing `os.environ` through unmodified.

**Cold retry.** When launched with `--resume` and the process exits nonzero
within a short grace window (~5s — resume rejections fail fast), retry once
without the session, journaling both attempts (`spawn`, then
`spawn-cold-retry`) so the fallback is visible, never silent. Honest limits,
to be stated in a comment at the retry site so nobody "improves" it into
vendor-output parsing: orch cannot distinguish "bad session id" from "agent
crashed for an unrelated reason" without parsing claude's output, which it
refuses to do. The retry is best-effort-once. If the cold attempt also dies,
the wake failed — it lands in the run log, and the next tick's conditions
surface it. A failure *after* the grace window is not retried; the tick
catches it. Spawn-and-exit means post-grace failures surface a tick late,
not in a captured error file. Accepted.

**The CLI is load-bearing.** Orchestrators are `claude -p` sessions acting
through Bash, and spawning downward is their normal verb, so:

    echo "<brief>" | ~/orch/spawn.py <role> <scope...>

    spawn.py repo-orch <slug>
    spawn.py issue-orch <slug> <n>

Brief on stdin, uniform across all three roles. There is no
`spawn.py worker`: workers are Agent-tool subagents dispatched by
issue-orch inside its own session, not orch processes. The CLI is a thin
wrapper over `spawn()`; it exposes nothing spawn() does not do.

Session death and completion detection: pgid death is the only signal. Orch
never parses claude's JSON output for session ids or results — transcripts
carry the ids, journals and the derived world carry the results. Resume-id
derivation is the one coupling to claude's transcript layout
(`~/.claude/projects/<mangled>`); if that layout changes, detection degrades
to pgid-only. Comment this at the `session_dir()` port.

### The model map — and why cheap is not the goal

`MODEL_BY_ROLE` in `orch/core.py` selects a model per spawned level, appended
as `--model` in `_spawn_argv`. An absent entry means the default, so the map
is additive: adding an entry can only change the role it names.

The assignment comes from an operator ruling (issue orch#155): `dashboard-op`
runs Sonnet, `repo-orch` and `issue-orch` run Opus. Subagents are not in this
map. A worker is an Agent-tool subagent inside issue-orch's session, not a
spawned process, so its model is a tool parameter the dispatcher passes; see
`agents/issue-orch.md`.

**The governing principle, in the operator's own terms: a rerun costs more
than the cheaper model saves.** The unit of cost is the work, not the call. A
cheap call that produces a wrong patch costs the call, plus a review
round-trip that finds the defect, plus a fix round-trip, plus the
orchestration around all three. Against that, the saving on one call is
small. So consistency outranks per-token price. A model that is right 95% of
the time at three times the cost beats one that is right 70% of the time,
because the 30% drags the whole pipeline with it.

This bites harder here than in a chat tool. A bad output does not stop at a
bad answer: it propagates into commits, a PR, a review, and a merge decision.

Two consequences a later reader must not re-litigate:

- **Haiku is assigned nowhere, and Sonnet is the floor** for anything that
  reads or writes code. Nobody has measured Haiku's consistency on these
  tasks, and the cost of finding out is a rerun.
- **Move a level DOWN only on measured evidence**: a consistency figure on
  that level's real task, and a count of reruns caused before and after. For
  the reviewer specifically, count blocking findings per PR before and after;
  a drop is a measurable regression, not a saving. "It is probably fine" is
  not evidence, and the failure it produces stays invisible until a bad patch
  lands.

This paragraph exists because the map alone cannot say it. A future agent
reading only the map sees cheap models as an obvious win, and does not see
the rerun cost — because the rerun happens to somebody else, later.

## The tick

Dumb on purpose. It holds exactly two numbers, and both are policy about
the tick's own attention, never about the work: `NUDGE_IDLE_MINS` — how idle
something must be before it is worth waking an agent — and `ACK_TTL_MINS` —
how long an ack may hold the machine silent over a standing condition (see
the wake gate). Everything else — is this stale, worth retrying,
should this land — is a judgment about the work, and belongs to the agent
that holds context. No other thresholds anywhere in the mechanics.

Steps, exactly:

1. Take `.tick.lock` (flock, non-blocking). Held → skip, exit 0.
2. Per watched repo (`repos.txt`, one path per line, `#` comments, `~`
   expansion; skip non-git paths): load the World — one `gh issue list`, one
   `gh pr list`, one rollup fetch per open PR. A failed read aborts the repo
   rather than deriving a false empty: an unreachable oracle must not read
   as "nothing is happening". Derive every fact.
3. Rebuild `public/status.json` and `public/widget.html`.
4. Append the observation to `history.jsonl`. Journal per-repo observations
   to the repo journals only when the digest changed — a journal of "still
   nothing" is noise the next session must read past.
5. Compute `needs_attention` (the wake conditions) and the digest.
6. The wake gate (below). If it opens: spawn dashboard-op and exit — knock
   once, spawn-and-exit, no blocking on the child.
7. Exit.

No claiming, no merging, no reclaiming, no label writes, no judgment.

### The wake conditions

Idle, defined once: an issue's activity is the newest of the issue
branch's last commit time (`work_mtime`), the newest transcript mtime
under issue-orch's cwd, and the issue's `updatedAt`. `idle_over` =
idle > `NUDGE_IDLE_MINS`. Booleans, so they flip exactly once at the
threshold — no repeat-wake spam.

**The transcript component is recursive — now load-bearing twice over.**
Workers ARE Agent-tool subagents: threads inside issue-orch's process
whose transcripts land under `<session-id>/subagents/` *beneath* the key's
own transcript dir. A flat `*.jsonl` glob misses them, so an issue-orch
whose workers are grinding reads as idle and condition 6 false-fires.
Glob recursively.

**Resume-id derivation does not.** The resume id is the newest *top-level*
stem; a recursive newest could hand `--resume` a subagent transcript's stem,
which is not a resumable id for that key. Two rules, two code paths, and a
comment at each saying why — they look like one helper and must never become
one.

1. **Ready or abandoned**: issue UNCLAIMED with `agent-ready`, or ABANDONED.
2. **Blocked**: issue work BLOCKED (CI failed on the issue PR).
3. **Contended**: >1 live session on one key. Resolved at dashboard scope
   or by the operator, never routed down the tree — see "Giving up" below.
4. **Oracle failure**: gh read failed for a repo, or the tick itself is stale
   — the error-alert set.
5. **Unowned work**: issue CLAIMED ∧ issue-orch not alive ∧ (**work in
   REVIEW** ∨ issue `idle_over`). The old "no worker alive" conjunct is
   gone by construction, not simplification: workers are threads in
   issue-orch's process and cannot outlive it. This one condition covers
   dead mid-flight, forward progress, and the merge-blocked handoff:
   work finishing cleanly (PR green, issue-orch exited) is unowned work,
   so auto-land, a blocked merge, and give-up assessment are all
   reachable.

   The REVIEW disjunct exists because finished work is not *idle*, it is
   *waiting*: the work is done and the PR is open, so there is nothing to
   infer about staleness. Requiring `idle_over` there would delay every
   auto-land by `NUDGE_IDLE_MINS` for no reason. REVIEW with no live owner
   is a fact; the other paths into this condition are inferences, and those
   keep the idle gate. This adds no threshold to the tick.

   **An orphaned claim** is a CLAIMED issue whose owner never existed: no
   session has ever run for that key. Condition 5 already covers this case,
   because `idle_over` reads the issue's `updatedAt`, and an orphaned claim
   is always idle. The tick adds no condition for it. A separate condition
   would double-fire on a state condition 5 already fires on and already
   routes, and DESIGN.md's own bar (see "What earns a condition" below)
   requires more than that to justify a new cell.

   **Where an orphaned claim comes from — settled, not open.** Three causes
   were on the table for the orphan state observed in #117/#119/#98: a
   spawn that failed after the claim, a label applied by hand, and a race
   between the ledger write and the session's own claim. The third is
   impossible by construction, so the cause is a claim no spawn ever made
   — in practice a label applied by hand, or one left behind by a claim
   whose spawn never completed.

   `spawn()` takes an exclusive flock on the key's lock file and holds it
   continuously across the read-check-launch-write sequence
   (`orch/core.py:3567-3598`): the row is read, a live pgid refuses the
   spawn, a dead row rolls aside, `_launch` returns a pgid, and the ledger
   row is written — all before the `finally` releases the lock. There is
   therefore no window in which a spawn-created session exists without its
   ledger row, and a claim carrying no row cannot have come from a spawn
   race. That is also why `void_claim` can treat `prior_runs(key) == 0` as
   proof that nothing was ever attempted rather than as a timing artifact.

   This rules out a reordering fix: there is no ordering hole to close. An
   orphaned claim is a label-level fact, and condition 5 routing it back to
   the pool is the whole remedy it needs.

   `never_owned` is the name for the distinction. It is `prior_runs == 0`
   AND NOT `orch_alive`, derived at read time from feed data already
   present, never a recorded state. The tick carries it through
   `build_thin` and into condition 5's note and structured `cond` entry, so
   an "owner died mid-flight" reads differently from "no owner ever
   existed" without changing when condition 5 fires or what it routes to.

   The tick still only reports. It never edits labels itself; label
   ownership stays with issue-orch, unchanged from condition 5's existing
   contract.

   `never_owned` cannot collide with a review-held issue, and #126 settled
   this without adding any state: there is no "deferred representation" to
   define. A blocking finding holds the issue at plain `REVIEW` — as of
   orch#408 (ruling orch#148 option B), `core.merge_pr` refuses the merge
   itself via `core.review_blocks_merge`, rather than revoking `auto-land`.
   An issue held that way has by definition already been reviewed, so at
   least one session ran for it and its `prior_runs` is
   greater than zero, while `never_owned` requires `prior_runs == 0`. A
   held issue can therefore never read as an orphan. Nothing here needs a
   second store, because nothing here needs a first one.
6. **Wedged**: issue-orch alive ∧ issue `idle_over`. Surfaced as
   information **only** — nothing in the tree ever kills a wedged session.
   Dashboard-op applies the long-running-lease rule to conclude stuck
   versus slow, journals the conclusion, and leaves the kill to the
   operator (widget or CLI). Killing is an operator act, full stop.

   **The same idiom covers dashboard-op itself**, one level up. The wake
   gate's second clause is *dashboard-op is not alive* — derived from
   `killpg`, so it cannot lie about whether a process exists. But alive is
   not attending: a dashboard-op that stalls keeps its process group alive
   indefinitely, every tick reads alive and skips the wake, and condition 6
   watches issue-orchs only. One hung session at the top of the tree
   silenced the whole machine for as long as its process lived, unbounded.
   So dashboard-op alive ∧ its own `transcript_activity` idle past
   `NUDGE_IDLE_MINS` raises the `dashboard-op-hung` error alert. Same
   threshold, same recursive derivation, no new number, and the same
   surfaced-never-killed rule.

   Traced, because the loop is not obvious: that alert is a digest input,
   so it changes the digest and satisfies the gate's first clause — but the
   second clause short-circuits on alive. **The alert surfaces to the
   operator; it does not spawn a second dashboard-op.** The alert carries a
   stable `key` and a fixed message for the digest rule below: an idle
   count inside the message would re-hash the digest every tick.

7. **Landed but still claimed**: issue work LANDED ∧ issue CLAIMED. It
   detects a PR that merged while the issue still carries `agent-working`,
   because issue-orch died between the merge and dropping its own label.
   This is not a cosmetic stale label: the issue keeps holding one of the
   repo's concurrency slots, and `agent-working` is exactly what makes it
   read as in-flight rather than ready — so it is invisible to pickup
   *while holding the slot*. The label that causes the leak also prevents
   the wake that would clear it. It is self-sealing, and left alone each
   instance costs a slot permanently.

   Deliberately not gated on `orch_alive` and not gated on `idle_over`: a
   merged PR is a derived fact and cannot lie, so it needs no threshold to
   be trusted — unlike condition 5's REVIEW disjunct, there is nothing here
   to wait out. Gating on liveness would reopen exactly the race this
   condition exists to close: the owner dying inside the window between
   merge and label-drop, with nothing left to notice it. This adds no
   threshold to the tick, the same claim condition 5's REVIEW disjunct
   makes for the same reason.

   The tick still only reports; it does not edit labels. issue-orch owns
   labels exclusively and clears its own on the wake this produces. A tick
   that strips the label itself was considered and rejected for #50: it
   breaks both "the tick derives, it does not edit" and "issue-orch owns
   labels" in one move.

8. **Unconsolidated issues**: a repo has issues filed since anything last
   considered the set as a whole (`r["unconsidered"]`). Unlike 1–7 this is a
   fact about the repo's issue *set*, not about any one issue, so it names no
   issue — the structured entry carries `"issue": None` and there is one entry
   per repo, not one per issue. It spawns: repo-orch's consolidation pass is
   exactly the verb that answers it.

   Deliberately not gated on liveness, and the reason is the same shape as
   condition 7's: a live issue-orch working some *other* issue in this repo
   does not consolidate the set, so there is no session whose liveness could
   answer for it. It reads `r["unconsidered"]` and not `r["issues"]` — the
   latter never fires on the deadlock this was built for (open issues, zero
   labels, `candidates()` empty). `unconsidered` already excludes claimed
   issues; see `feed.repo_json` for where the set comes from.

9. **Orphaned PR**: an open PR on an `issue-<n>` branch whose issue is no
   longer open. Set-level like condition 8 — `"issue": None`, one entry per
   repo, ungated on liveness for the same reason.

   Information only, and **deliberately not in `SPAWN_CONDS`**: the spawn verb
   is issue-orch, which is spawned per ISSUE against a live `issue-<n>`
   worktree, and an orphan's issue is closed — there is no issue to spawn
   against and nothing a spawned session could do with the PR. The decision an
   orphan needs (merge it, close it, or reopen the issue) is the operator's, so
   the condition raises `needs_attention` and a note, which is what wakes
   dashboard-op and puts the row in front of a human.

   It has to be a condition and not merely a digest input, because the wake
   gate returns False on `needs_attention <= 0` *before* it ever compares
   digests: a changed digest with no condition wakes nobody, and the orphan
   would reach no human (orch#279 consequence 3). Nothing else can raise it
   either — a closed issue is never re-entered, so its open PR has no owner, no
   label, and no other condition. It reads as handled because it exists and is
   green.

What earns a condition: conditions 1–3, 5 and 6 are the cells of one
predicate — *work exists that no single healthy owner is driving* — where
"single" excludes contention (two owners), "healthy" excludes death, and
"driving" excludes wedged. Condition 7 is a further cell of that same
predicate, not a new kind of thing: a landed-but-still-claimed issue is work
with no healthy owner too, and it is the one cell where the absence of an
owner is permanent rather than merely current — there is no liveness to
wait out, because the label that would signal an owner is precisely what
survived the owner's death. Condition 4 remains the one meta-condition: the
oracle failed, so no cell can be trusted.

Conditions 8 and 9 are the second axis, and naming it is the point: they are
predicates over a repo's **set** rather than over one issue's ownership. No
per-issue predicate can see either one — an issue nobody has consolidated is
invisible precisely because no session has ever looked at it, and an orphaned
PR's issue is closed, so the per-issue machinery has already let go of it.
That is why both carry `"issue": None`, both are ungated on liveness, and both
emit once per repo.

So the bar a new condition must clear is not a count, it is this: **name a
state that no existing cell already fires on, and say which axis it lives on —
one issue's ownership, or the repo's set.** A candidate that merely
re-describes a state an existing condition already routes on is a double-fire,
not a condition; that is the argument that kept the orphaned claim inside
condition 5 (above) and keeps review out of the list entirely (below), and it
is the test to apply to the next one, whatever its number turns out to be.

An earlier version of this paragraph was named for a count and argued the set
was closed at that count. The set has since grown twice, and both additions
cleared the bar above rather than contradicting it — the closed-set framing was
always the weaker reading of a sound argument. Stated here so the next addition
extends this section instead of having to overturn it, and so no future reader
mistakes the bar for a cap. Do not reintroduce a total, in this paragraph's
title or its body: a count here is what drifted four ways before, and
`docs_do_not_claim_a_wrong_condition_count` will fail on one.

Keep them enumerated in code and in the agent docs. The predicate is the
explanation; the named booleans are the implementation, and an agent that must
*check* them needs names it can verify one at a time, not an invariant it has
to re-derive. The live count belongs in code, where
`docs_do_not_claim_a_wrong_condition_count` derives it from the emitted
`"cond": <n>` sites (orch#339) — which is why this section states a bar and
never a total.

**Review adds no condition and no digest input.** Review is not a separate
flag with its own wake path — it is the first half of what `auto-land`
does. It fires at condition 5's REVIEW disjunct, exactly as the merge does,
and gets its re-entry from the same place — held REVIEW re-fires condition
5 every tick whether or not a review has already posted, and the
fire-once behaviour is enforced by checking the PR's own comments at
dispatch time, not by any new recorded fact the tick or the digest would
need to know about. A blocking finding holding the merge is likewise not a
new condition: as of orch#408 (ruling orch#148 option B),
`core.review_blocks_merge` derives the hold fresh from the PR's last
`orch:review:v1` comment on every merge attempt, and `core.merge_pr`
refuses accordingly — no label write, no stored state — while the work
state stays `REVIEW`, the same held state condition 5 already re-examines
every tick with no clean merge available. Nothing new enters `candidates()`
and nothing new is excluded from it. This is still deliberate, and the bar
it failed is the same bar condition 7 passed: a feature that only changes
what issue-orch does once it is already in the room does not need the tick
to see it coming, whereas condition 7 names a state no one is in the room
for at all — an issue-orch that already died. A successor may not cite
condition 7 as licence to add a condition for review, the merge hold, or
anything shaped like it.

### The digest rule

The digest hashes **exactly the facts the conditions read — nothing more,
nothing less.** Less re-creates the suppression bug (a condition that cannot
flip the digest can never cause a wake); more re-creates wake spam. A new
condition and its digest inputs land in the same commit, always.

Concretely, the thin shape: per repo → per issue `{number, state,
work_state, orch_alive, idle_over, contended, startable, never_owned}`,
plus the error-alert set. There is no unit layer: units are recorded, and
recorded facts never enter the digest. Sorted-key JSON, sha256, truncated.
(The serialize-thin-then-hash approach is in old `tick.py` `digest_of()`.)

**Journal scope for a spawn.** Two entries fire for one spawn action, to two
scopes, and they are not redundant: `spawn()` records *that a thing was
spawned* in the scope where the spawned thing lives (issue-orch → the
issue journal, repo-orch → the repo journal, dashboard-op → the dashboard
journal), while the spawning agent records *why it decided to* in its own
scope. A repo-orch starting issue 42 therefore writes its reasoning to the
repo journal and `spawn()` writes the spawn fact to issue 42's journal, where
whoever reads that issue can see it. Journal the spawn where the spawned thing
lives; journal the decision where the decider lives.

**The thin shape is emitted by the same function that evaluates the
conditions, from the same variables — one pass, two consumers.** Do not write
a separate walker that re-derives the world for hashing. Every digest bug in
this design's history was drift between a thin shape and the conditions it was
supposed to mirror, maintained as two code paths. Emitting both from one
evaluation makes drift impossible by construction, and turns "a condition's
inputs must be in the digest" from a rule someone has to remember into a
property of the code's shape.

### The wake gate and the conditional ack

Wake dashboard-op iff:

    needs_attention > 0
    AND dashboard-op is not alive (ledger check)
    AND (digest != the last digest dashboard-op ACKNOWLEDGED
         OR that ack is older than ACK_TTL_MINS)

The ack is a dashboard-journal entry `{"event":"handled","digest":"..."}`
written by dashboard-op itself; the tick reads the newest one. Gating on the
*acknowledged* digest — not the last digest the tick saw, not the last wake —
is what makes failure degrade to loud repetition instead of silence: a
dashboard-op that dies mid-decision over a static world gets re-woken every
tick until it finishes.

The ack is recorded, and it gates — the one sanctioned exception to
"nothing gates on recorded" (see Derived versus recorded). What earns the
exception is the expiry: the ack is a lease on silence, and the lease rule
("past T with no progress, it is stuck") applies to it exactly as to every
long-running actor. Without the expiry the ack is self-sealing, not
self-correcting — a wrong ack suppresses the very wake that would detect it,
and since the world has not moved, the digest never changes to break the
seal. That is the eight-day silence reachable through one malformed line.
With the expiry, the worst any ack can buy is `ACK_TTL_MINS` of quiet.

A malformed `handled` row fails loud, never quiet: a missing digest, or a
missing, unparseable, or future `at`, counts as expired — the tick wakes.
The `at` is stamped by the journal CLI, never typed by the agent.

**Ack only on a wake where you spawned nothing** — nothing needed doing,
everything needed is already alive or in flight, or you chose to defer. **If
you spawned anything, exit without acking**; the unchanged-unacked digest
brings you back next tick to verify the spawn took. Traced:

- chain succeeds → next tick the world moved (claim, commits) or conditions
  cleared → digest changed or attention zero → no repeat.
- chain dies before changing the world → conditions persist, digest ≠ acked
  → re-woken; sees the rolled ledger row and `prior_runs`, retries or
  escalates — loud, the right degradation.
- standing in-flight work (BLOCKED with its issue-orch alive on the fix
  round) → owner alive, spawns nothing, acks → correctly quiet until
  liveness or state flips the digest.
- confused ack (dashboard-op acks a digest it did not handle — the
  self-sealing case) → silence for at most ACK_TTL_MINS, then re-woken over
  the same digest; the fresh session's journal tail shows an ack with no
  reasoning behind it and treats the wake as fresh. Bounded, then loud.
- legitimate defer → quiet now; while the condition stands, one re-wake per
  ACK_TTL_MINS whose whole job is re-verifying the defer — still human-owed
  → re-ack, one line, lease renewed. That heartbeat is the price of bounded
  silence, and it is cheap: one machine-wide session per period, only while
  a condition stands.

The verify-loop other supervision designs build as machinery falls out of
withholding an ack. The ack asserts only "my own wake completed" — it never
asserts grandchildren succeeded. The tick's conditions, not the ack, are the
authority on whether work is moving.

The wake brief handed to dashboard-op: its agent doc (`agents/dashboard-op.md`),
its dashboard-journal tail (oldest first — it is probably a fresh session),
and the dashboard JSON (fact, derived this minute — trust it over memory).

## Giving up

**The give-up rule is decided (operator, 2026-09): one fix round, then
`agent-stuck`. Fail fast.** A BLOCKED issue gets exactly one fix
re-entry of the Implement stage from the existing commits; still
BLOCKED after it,
issue-orch journals what both attempts tried and adds the label. The
count lives in the issue journal — recorded, which is fine: give-up is
judgment, nothing gates on it.

Three design-fixed bounds on any give-up-shaped policy remain. They are
correctness constraints, not judgment — a policy that violates one strands
work or loops the machine, however sensible it reads.

**The visibility bound.** A give-up rule must leave the issue in a state
condition 1 or 5 can see: ABANDONED (`agent-stuck` set) or CLAIMED with no
live owner. Exiting without either respawning or labeling strands the work —
nothing anywhere will ever notice it again. Every give-up policy, at every
level, must terminate in a visible state.

**Write-off is a spawn, not a label.** Re-entering a CLAIMED issue whose
issue-orch died is automatic and needs no repo-orch judgment — condition 5
fires and it spawns a fresh issue-orch, which decides for itself whether the
work is hopeless once it has looked. repo-orch reaches write-off only through
the loop terminator (repeated identical deaths with no progress between them),
and even then it cannot act on "hopeless" directly: the judgment labels
(`agent-working`, `agent-stuck`) are issue-orch's exclusively — repo-orch
writes only the `agent-ready` nomination (orch#153) — and a CLAIMED
issue with no live owner fires condition 5 every tick — so "leave it and
move on" is a loop, not a write-off. repo-orch's write-off verb is its only
verb: spawn a fresh issue-orch whose brief says *assess; if hopeless,
journal why and mark `agent-stuck`*. This keeps issue judgment at the
issue's level and the label rule intact, and it converts CLAIMED —
ambiguous between "crashed, resumable" and "hopeless" — into ABANDONED,
which is self-describing: the label plus the journaled reasoning record
that the tree concluded give-up. Sometimes the assessor finds the issue
continuable after all and continues it — which repo-orch, holding no code
context, could not have known. Cost: one session per write-off. Accepted;
write-offs are rare, and the alternatives are a label with two writers or
a human on the critical path of every dead issue-orch.

**How a standing condition goes quiet.** Some conditions only a human can
clear: ABANDONED until the label is removed; work held in REVIEW because
`auto-land` is absent; a wedged session past its lease — the kill is the
operator's act, so once dashboard-op has journaled the wedge it defers and
acks the same way. These keep deriving every tick — that is the alarm
staying visible, and it is correct. What stops is the wake spam, the
designed way: once the chain has run for the current digest and journaled
who owes what, dashboard-op defers and acks. The ack silences wakes, never
the dashboard — and only for `ACK_TTL_MINS` at a time: the expiry re-wake
re-verifies the defer, and renewing it is a one-line re-ack (see the wake
gate). The asymmetry that makes this safe: deferring ABANDONED or
a held REVIEW is fine because the state itself records why it is waiting;
deferring condition 5 on a CLAIMED issue *before the chain has run for
this digest* hides a possibly-recoverable crash — the one silent failure
this machinery exists to prevent. That asymmetry is why write-off must
reach ABANDONED before anything upstream is allowed to go quiet on it.

One consequence for condition 3 (contended): contention is never routed
down the tree. repo-orch holds no kill, and its only label verb is
`agent-ready` — neither clears contention, so spawning it burns a session per
wake and resolves nothing.
The extra session on a key is usually a human's (an operator `--resume`, a
hand-run `claude` in the key's cwd — and the issue worktree is now the
natural place to poke at the code by hand), which is exactly why nothing
kills a contended key on a hunch. Contention is dashboard-op's to surface
and the operator's to resolve; the error-alert set keeps it loud until it
clears.

## Merge blocked

issue-orch merges its own PR — the normal path. The escalation exists for
the one thing only repo-orch can see: cross-issue conflict, where another
issue landing first makes this issue's PR unmergeable.

The path, mechanically:

1. issue-orch tries its merge. Refused (not mergeable) → it attempts one
   rebase of `issue-<n>` onto base and re-pushes with
   `--force-with-lease`. Its own git, its own verb.
2. Still failing → journal `orch/issue-orch merge-blocked` to the issue
   with what conflicted, and exit. That leaves work in REVIEW with no live
   owner — condition 5, no idle wait — so the tick re-derives the handoff
   even if the journal write failed.
3. The chain re-enters; repo-orch reads the issue journal, sees the
   escalation, and acts with the one verb this adds: `spawn.py merge
   ... --by merge-blocked`, in a
   merge order it chooses. What merges cleanly it merges; a branch still
   conflicted goes back down — spawn that issue's issue-orch with a
   rebase-onto-new-base brief. repo-orch never resolves conflicts in code;
   it sequences, merges the clean, and re-briefs the rest.

The red line moves accordingly: repo-orch **merges only on a journaled
escalation** — never speculatively, never to hurry a held REVIEW. The old
line ("repo-orch never merges") is deleted and its deny is lifted. Like
every argument-scope line, the "only on escalation" half is convention:
the deny layer sees verbs, not reasons.

## Review before landing

One flag, not two. `auto-land` (label, per issue) and the repo's own
`auto-land` key (per repo, nested in its entry in `ORCH_HOME/orch.json`,
read by repo-orch and passed into the issue-orch brief) together decide
whether issue-orch
reviews and merges. There is no separate `auto-review` label or key —
review is not an independent opt-in, it is the first half of what
`auto-land` does: automatically review, then merge. Precedence is
symmetric: the per-issue label wins over the repo default in both
directions — `auto-land` present reviews-then-merges even against a repo
default of off, `no-auto-land` present skips both even against a repo
default of on; with neither label the repo default decides, and with
neither label and no repo default, neither runs. Firing is condition 5's
REVIEW disjunct — see "What earns a condition" above for why this adds no
condition at all. Fire-once is enforced by reading the PR's own
comments for an existing review before dispatching another, not by any new
recorded state. The review posts findings as a PR comment; out-of-scope
findings arrive as `follow-up` items inside that same block for the
operator to file, not as issues created here.

**The on/on cell, settled (issue #98, filed against #39; re-settled by the
operator, 2026-09-14).** #98 had settled it as: both flags on means review
runs, posts its findings, and the merge proceeds on green regardless of
what it found — the second of three candidates #39 had tabled, chosen
because it followed from a no-gating rule that review, once posted, never
blocks the merge decision after it. The operator has now overturned that
no-gating rule itself: a blocking finding DOES gate the merge. `auto-land`
on means review runs first, and its outcome decides what follows —
clean → merge unattended; blocking finding → the merge is held and the
attempt stops. The work state stays `REVIEW` — there is no new state and
no new label. **Wired by orch#408 (ruling orch#148 option B, decided
2026-09-15):** the hold is derived, not a revoked label — `core.merge_pr`
calls `core.review_blocks_merge`, which reads the PR's latest
`orch:review:v1` block fresh on every call and refuses the merge iff it
carries a `fix-before-merge` item; no label is written, no state is
stored. Held `REVIEW` with a blocking finding on the PR already
re-examines every tick and merges nothing, because the merge gate itself
refuses it; a blocking finding just puts the issue into that same existing
hold instead of inventing a new one. The hold lifts on a fresh clean
re-review, because only the PR's last block is read — no longer only the
operator's act the way re-adding a revoked `auto-land` label once was.
Pushing commits that fix the finding does not lift it on its own: the
block carries no head sha to compare against (staleness is not derivable
today — see the `# ponytail:` note in `core.review_blocks_merge`), so a
fix only lifts the hold once a fresh re-review posts a new block over it.
Note what is deliberately NOT claimed here: re-entry still happens,
because a held issue is a live candidate. A resumed issue-orch reads the
PR's own comments, finds the blocking block, and holds — it does not call
merge to discover that. The gate is a backstop against a mistake, never
the way an agent is meant to learn the hold exists. The
review does not run twice either, but that is the fire-once PR-comment check
doing the work, not a ban on spawning. This is the third candidate #39 had tabled and #98 had
rejected; the operator's verdict supersedes both, and the 2026-09-14
correction above supersedes the add-`agent-stuck` mechanism this section
originally described. "Do not add conditions to `auto-land`" now cashes
out differently: the checkmark still fires exactly one path, but that path
now has a fork in it, decided by the review it already ran — and the fork's
"no" branch costs nothing new: no new state, no new store, no change to
the condition table, only a merge-time gate the machine already reads.

There is no revocation left to record. The `orch:review:v1` comment
already posted to the PR is not merely a record of the hold — it IS the
hold: `core.review_blocks_merge` reads it fresh on every merge attempt, so
there is nothing else for a successor to consult and no second store is
even possible, let alone needed.

Note the sharp edge this leaves: an absent or empty CI rollup reads as
green, so on a repo with no CI, a clean-but-unreviewed-by-CI PR still
merges once the review itself is clean. That hazard is orch#119's, not
this section's; the review step here checks code, not CI configuration.

**Where this is going.** `docs/UX-REDESIGN.md` §4.1 collapsing two flags
into one per-repo `automerge` control is superseded by the operator's
verdict above — orch already has one flag (`auto-land`), not two, as of
2026-09-14. What §4.1 still anticipates correctly: landing reviews first
as its own step, and the agent may skip review on small changes. That
skip-on-small-changes refinement is unimplemented as of 2026-09-14;
everything above it is what runs today.

**Who reviews, and why a same-level subagent rather than a new role.** The
reviewer is a `reviewer` subagent — one-shot, cold, dispatched by
issue-orch exactly like `worker` and `failure-reader` — not a fourth
spawnable role alongside `dashboard-op` / `repo-orch` / `issue-orch`. A new
role touches `ROLES`, `key_for`, `cwd_for`, `_split_key`, `ARITY`, `USAGE`,
`feed._KEY_ARITY`, `DENY_BY_ROLE`, `JOURNAL_PATH_BY_ROLE`,
`_journal_spawn`, and `_journal_scope_for` — a real cost this design has
been deliberate about not paying without a reason (see "Minimum code that
solves the problem" at the top of this document).

The independence the review needs does not come from a different level of
the tree; it comes from the subagent contract every tool in this design
already has. Every specialist here is one-shot and cold: it holds nothing
between calls, never saw the units get written, cannot read the issue
thread. The `reviewer` template enforces this for review specifically — it
receives the issue body and the diff, and deliberately never the plan
file, the journal, or issue-orch's account of what it did (a reviewer
given the writer's reasoning grades the reasoning, not the code). What
catches a bug the implementer missed is a cold read of the diff against
what the issue asked for; that is a property of statelessness, not of
which level in the tree issued the brief. A new orchestrator level spawned
by repo-orch would buy a different session key and nothing else — the
thing that actually catches bugs, the cold read, is available at
issue-orch's own level for the cost of one subagent template, so a new
level is not what did the work and is not worth its cost.

**Residual risk, stated plainly.** issue-orch still chooses when to
dispatch the reviewer and still judges its report — a systematically wrong
issue-orch (one that never dispatches it, or dismisses findings) can still
produce a weak review, and this design does not remove that. What it does
remove is the far likelier failure: a reviewer that rubber-stamps because
it remembers writing the code. Revisit if a review is observed passing a
PR a human later rejected.

## Journals

What makes a fresh session able to continue. A session is disposable; its
journal is not. Append-only; nothing reads a journal back for correctness, so
a lying journal costs context, never state.

One source per scope, split by where the acts happen:

| scope | home | why |
|---|---|---|
| issue | **GitHub issue comments** | about *the work*, which is portable and belongs with the work |
| repo | `state/repos/<slug>/orch.jsonl` | about *this machine's* orchestration |
| dashboard | `state/dashboard-op.jsonl` | machine-wide; two machines' dashboard-ops are not one conversation — syncing would be actively wrong |

**No mirror of the issue journal, ever.** A local mirror is authoritative
only when GitHub is unreachable — and in that state orch cannot read labels
or PRs either, so it is already the read-only tracker named under failure
modes. A cache that only matters when nothing works does not earn a
sync-and-reconcile question on every read and write.

The symmetry that justifies GitHub-first: the issue journal's author is a
session whose every consequential act — claim label, merge PR, edit issue —
is already a `gh` call on the same channel. If the journal write fails, the
acts it would record are failing too. The record rides the same channel as
the acts it records; they fail together. Code is local-first because the work
happens locally; the issue journal is GitHub-first because the acts happen on
GitHub. Each record sits where its act happens.

**Issue-journal format.** Write via
`gh issue comment <n> --repo <owner/name> --body-file -`. GitHub supplies the
envelope free (`createdAt`, permanence, ordering); it cannot supply actor
identity (comments post from the operator's `gh` account), so one visible
convention:

    orch/<actor> <event>
    <free prose — what was concluded and why; the handoff text>

First line `orch/<actor> <event>`; the rest is prose. Prose beats JSON — the
consumer is a fresh LLM session and a browser, not a parser. Parse rule: a
journal comment is one whose first line starts with `orch/`. That is the
whole spec.

**Human comments pass through unfiltered.** `journal_tail("issue", …)`
returns ALL comments — `orch/`-marked and human — each labeled with author
and time. An operator reply in the thread is exactly the steering input the
next issue-orch should read: the per-issue conversational surface, working
across machines and from a phone, with GitHub as transport. Nothing
structural is parsed from human comments. The marker identifies orch's own
entries; it excludes no one. Comments are recorded — may lie, nothing gates
on them.

**Read paths.** The tick never reads issue journals; its wake brief uses the
dashboard journal. Issue-journal reads happen in exactly two places:
`spawn.py` building an issue-orch brief (one
`gh issue view <n> --json comments,body` per spawn — rare), and a human or
fresh agent reading the handoff — which needs only `gh`, on any machine, with
orch not installed. Cost: a handful of writes per session plus one read per
spawn, against 5,000/hr; zero added calls per tick.

**Failure handling.** Retry the comment once; still failing → write the entry
text to stderr (captured in the append-only `<key>.log`) and exit. **Do not
fall back to a local journal file** — that is the mirror sneaking back in
through the failure path, recreating "which wins" on the next read. What is
lost, honestly: the reasoning of a session that could not act on the world
anyway — its label edits and merges were failing on the same dead channel,
the derived world did not move, and the next wake re-derives from unchanged
facts. The degraded state is already alarmed: gh-read failures are condition
4 and digest inputs.

**API.** `journal_append/tail/brief(scope, repo, issue, …)` keep one
signature; `scope == "issue"` branches to the `gh` implementation, other
scopes keep the file path (port the file implementation from old `core.py`).
There is no per-issue `journal_stats` — the feed carries the issue URL
instead. Local rows: one JSON object per line,
`{"at": iso, "actor": ..., "event": ..., ...extra}`. `journal_brief` renders
a tail human-readably for prompts.

### Giving up is a status change, not an object

**There is no escalation object.** There was one — an `escalated` journal row
closed by a `filed`/`addressed`/`retracted` marker naming its instant, plus a
browser-local dismiss store and a file-as-issue verb. It was removed
(operator, 2026-09-15; orch#198). None of it was derived: a row stayed visible
until someone appended a marker, so the pile only grew, and the markers were
deliberately unwritable by agents to stop them closing an alarm on the
operator's behalf. The result was rows nobody could clear — 23 of 41 machine
alerts at the end, which is not a channel anyone reads.

When an agent hits a wall, **the issue it is already working changes status,
and that status change IS the escalation.** Nothing is created, nothing
accumulates, nothing needs closing. The mechanism already existed:
`core.issue_state` is pure over labels, `agent-stuck` maps to ABANDONED, and
the dashboard already renders issue state.

**Comment first, then label. The order is load-bearing.** An agent giving up
posts one comment whose first line is `orch/<actor> stuck` and whose body says
what it measured, what it tried, what it ruled out, and what a human must
decide; then it adds `agent-stuck`. Both ride one channel, so the failure
cases pair off: comment fails → label fails → the issue stays CLAIMED and
loud, reason in the run log. Comment succeeds, label fails → CLAIMED, reason
on the issue. Label-first would permit ABANDONED with no reason recorded,
which is the one bad cell.

**A failed write cannot silence an alarm, because there is no closing write.**
The only transition out of ABANDONED is a human removing `agent-stuck`. This
is the old fail-loud property with the object it guarded removed: an
issue-level give-up cannot go silent, since acts and record share a channel
and a failed label write leaves the issue in a state conditions 1 and 5
already see.

**The one case with no issue behind it** is a repo-level finding — a repo
defect, a broken tool, a decision no issue holds. It files **once**: search
open issues on the repo it is about, comment if found, else
`gh issue create --label agent-stuck`. The label goes on *at creation*,
because an unlabelled new issue is invisible to orch — no label, no condition,
no dashboard row. Dedup is derived from the forge, not remembered. If the
create fails, the attempt goes to the repo journal and the next session
retries; nothing closes it but a successful create. Write-refusal is a machine
defect the operator fixes once, and while it stands every issue in that repo
churns condition 5 — already loud.

This makes repo-orch a writer of `agent-stuck`, which orch#153 reserved to
issue-orch. That rule's reason was two writers racing on one issue; a label
set in the same call that creates the issue has no second writer, ever.
**orch#153 is narrowed to "one writer per issue at a time", not "one role".**

Merge escalation kept its mechanism and lost the word: the journal event once
written `escalated-merge` is now `merge-blocked` (see "Merge blocked"
above). It was never the escalation
object — it is a journal handoff that condition 5 makes loud, which is the
shape everything else now takes. The rename stops `escalat` meaning two
different things.

## The issue pipeline

issue-orch runs orch's own pipeline in the issue worktree — workers never
do; they never run git at all. **`/sc` is retired for orch** (operator,
2026-09): `~/.claude/skills/ship-change/SKILL.md` remains the operator's
manual skill for interactive work — never invoked by orch, never edited
by orch — and serves only as the structural REFERENCE the pipeline was
borrowed from. The seven-mismatch interface era is over; an orch-owned
pipeline has zero mismatches by construction. `docs/SC-INTERFACE.md` now
records what was borrowed and what orch deliberately does differently.
(The `pre-merge` stop added to `/sc` during the shared era stays there —
harmless, opt-in, the operator may want it; orch no longer depends on
it.)

**Borrowed from the reference**: the stage shape (plan → implement →
gate → PR → merge); derive-stage-from-disk re-entrancy (plan file
exists → planning done; commits ahead of base → implementing; PR open →
at review; PR merged → done — a dead issue-orch resumes at the right
stage with no recorded pointer); the role split (one orchestrator, many
implementers, one committer); the plan file as single source of truth
(`tmp_plan-*.md`); gates are the repo's own checks only — none defined
→ say so, never invent.

**Deliberately different — no human at the stops.** The reference
pauses and waits for a person; issue-orch IS the decider at every
joint. The `auto-land`-absent case opens the PR and leaves it at
REVIEW — journal the wait, exit — rather than pausing for nobody;
condition 5 keeps the handoff visible. `auto-land` present → review, then
merge on a clean review or hold at `REVIEW` on a blocking one (the merge
gate itself refuses; see orch#408). The full
stage spine,
stage derivation table, and per-stage skill routing live in
`agents/issue-orch.md`.

**One owner, many editors.** Inside the pipeline, implementation
subagents (the workers) edit files and never run git; issue-orch does
all commits. Two rules make parallel editors safe in one checkout:

- **Disjoint targets.** Parallel workers get non-overlapping target file
  lists. Two units that need the same file run sequentially. Nothing
  enforces this; issue-orch's decomposition is the enforcement.
- **Commit by pathspec.** While any worker is in flight, commits name the
  finished unit's files explicitly (`git add <paths from that unit's
  brief>`), never `git add -A` — an all-add would sweep a sibling's
  half-done edits into the wrong commit.

**The mandates — issue-orch's own (formerly the worker brief's):**

- WIP commit before the first substantive dispatch and after each unit's
  edits are judged done. Commit is what survives process death.
- After each commit, best-effort `git push -u origin issue-<n>`; push
  failure non-fatal, retried next step. Push is what survives machine
  death; it must never wedge the session on a network outage.
- The first WIP commit **force-adds the plan file**
  (`git add -f tmp_plan-*.md`). Repos commonly gitignore `tmp_*`, and an
  ignored plan file silently does not travel.
- Squash-before-PR pushes with `--force-with-lease`, never plain force:
  the issue branch is single-writer by construction (the flock singleton;
  `contended` is an alarm state).
- **On every wake, `git status` first.** A dirty tree is a dead
  predecessor's uncommitted step — WIP-commit it before deciding
  anything. This is the pre-commit recovery path: worker edits are files
  on disk the moment they happen, and process death does not unlink them.

Derived state is untouched by pushing: `work_mtime` reads the local branch;
push changes what other machines can read, not what orch reads.

**Workers are always cold** — the Agent tool starts every subagent fresh,
which is the same property the old cold-spawn rule bought: no poisoned
conversation is ever inherited. The pipeline stays re-entrant for
issue-orch itself via the derived stage position above.

**A merge the pipeline cannot do cleanly is exited, not fought** —
issue-orch follows "Merge blocked" above instead of grinding on the
conflict.

## The repo-management skill

Exists, at `agents/skills/repo-management/SKILL.md`. It is load-bearing the
same way the agent docs are — the repo-orch level idles as a thin router
without it.

The skill holds the repo understanding: which `agent-ready` issues are worth
starting and in what order; what to defer; what to escalate. It directs repo-orch
to read the repo's own docs (`CLAUDE.md`, `AGENTS.md`, contributing docs) by
absolute path — repo-orch's cwd is not a checkout.

**What orch owes it** (mirroring what orch owes the issue pipeline):

- the repo's checkout path,
- the repo's slice of `status.json`,
- the repo journal tail,
- the literal command: `echo "<reason>" | ~/orch/spawn.py issue-orch <slug> <n>`.

Red lines repeated where the skill can see them: repo-orch's only label verb
is `agent-ready` (issue-orch owns claiming and `agent-stuck`), and it merges
only on a journaled escalation — never to hurry a held REVIEW.

## Agent contracts

The agent docs are load-bearing actuation, not documentation — "gives no
command and no session key" was the original 2026-09-01 failure mode, and the
chain does not fire without them. Every doc contains the **literal** commands
its level runs, pasteable into Bash.

`agents/dashboard-op.md` must contain:
- its mission (per-repo condition → spawn that repo's repo-orch, never deeper)
- the literal spawn: `echo "<one-line reason>" | ~/orch/spawn.py repo-orch <slug>`
- the conditional-ack rule verbatim: ack only when you spawned nothing; the
  ack's literal form:
  journal `{"event":"handled","digest":"<the digest from your brief>"}`
- the ack-lease rule: an ack silences wakes for at most `ACK_TTL_MINS`; a
  re-wake over a digest already acked means the lease expired — re-verify,
  and re-ack if the defer still stands
- the ack is about your own actions only; it never asserts the chain below
  succeeded — the tick's conditions are that authority
- the long-running-lease rule for judging wedged sessions
- the standing-condition rule from "Giving up": defer+ack once the chain
  has run for this digest and what remains is human-owed; never route
  contention (condition 3) down the tree
- red line: never spawn issue-orchs; never touch labels; **never kill** —
  condition 6 is surfaced and journaled, and the kill is the operator's

`agents/repo-orch.md` must contain:
- the mission (the real one, from the tree section — not "which issues have
  owners"; a thin description here is what once got the level cut)
- run the repo-management skill; where its inputs are
- the literal spawn: `echo "<reason>" | ~/orch/spawn.py issue-orch <slug> <n>`
- the write-off spawn from "Giving up": hopeless CLAIMED issue → same spawn,
  assess-and-give-up brief; never "leave it and move on"
- the merge-blocked verb: literal `spawn.py merge <slug> <n> <pr>
  --by merge-blocked`, bound to a journaled `merge-blocked` in the issue
  thread ("Merge blocked"). Not `gh pr merge`: the verb writes the
  `landed` journal row at the merge instant (orch#280)
- the in-flight bound: at most five issues driving at once
- journal one line per decision (started / deferred, with why),
  then exit
- red lines: never labels; merge only on a journaled escalation

`agents/issue-orch.md` is deliberately **thin core + on-demand skills +
one-shot subagents** — the one split doc. issue-orch is the
context-heavy session: `compose_brief` prepends the whole doc to every
spawn, it carries decomposition, the entire issue pipeline, and every
worker report in one context, and context exhaustion mid-issue is the
expected death mode (Known limits). Trimming its baseline and moving
heavy reads out of its context directly extend how far it gets.
dashboard-op and repo-orch stay plain docs: one decision per wake,
short-lived by design — a skill round trip there costs more than it
saves, and a skill loaded on every wake is a doc with extra steps.

**The dividing test (operator, 2026-09: subagents, not sessions —
"issue-orch uses all the other agents as its tools").** The question is
not "is this one thing?" but **"does it hold state between
invocations?"** issue-orch orchestrates the tools *and the things
between them*: the worktree's working state, git, labels, the journal,
the plan, the fix-round count, and routing — whose output becomes whose
input. Tools die after returning, so everything that persists lives in
issue-orch and nowhere else; that is why it stays ONE session
(splitting the between serializes it through a handoff at every step).
Decomposition, result-judging, and merging consume that state
continuously — they are issue-orch's own judgment, so they become
*skills*: guidance read into its own context, one per phase, never a
separate actor. A clean one-shot transformation — input in, a decision
or digest out, the return smaller than what it read — becomes an
*Agent-tool subagent* with a brief template: a call signature, a return
contract, and the tool red lines (never spawns, never git or labels,
never dispatches another specialist; knows nothing of the plan, labels,
or round count). The pipeline is orch's own (operator: "running a
standard pipeline, but has the agency to fix it" — and then: retire
`/sc` for orch; see "The issue pipeline"): the staged shape lives in
the agent doc itself, and issue-orch is every role in it — the
orchestrator, the dispatcher of the implementers, the sole committer,
and the decider at every joint where the reference pipeline would wait
for a human. It follows the stages so it never improvises the happy
path, and departs deliberately — re-brief, re-split, rebase, escalate,
give up — journaling each departure. Stage position is derived from the
world (plan file exists → planned; commits → implementing; PR open → at
review; PR merged → done), never recorded, so a dead issue-orch resumes
at the right stage with no pointer that can disagree with the world.
One pipeline, never a second beside it. Exactly two standing tool
templates exist:
`agents/worker.md` (one unit's edits → files changed, what done, what
blocked) and `agents/failure-reader.md` (failed CI/gate logs before a
fix round → the residual-delta digest — failing checks, decisive error
lines, hypothesis, files implicated, what not to retry — which the
fix round consumes; issue-orch never holds the raw
logs). Ad-hoc heavy reads (a huge issue body, a repo survey) use a
plain read-only subagent with no standing template.

The always-loaded core must contain:
- the mission: claim, decompose, run the pipeline, dispatch worker
  subagents, commit, land or escalate the merge, give up after one fix
  round
- label ownership and the literal claim command (`agent-working`)
- git ownership: sole git writer in the issue worktree; the mandates
  (WIP-commit by pathspec, best-effort push, force-add plan, lease-force,
  `git status` on wake)
- journal to the issue: literal
  `gh issue comment <n> --repo <owner/name> --body-file -` with the
  `orch/issue-orch <event>` first-line convention; journal entries are the
  handoff — write them as instructions to your successor
- the stage spine and derivation table; `auto-land` decides the Land
  stage — absent, the PR is left open at REVIEW, never merged
- read the full comment thread on wake — human comments are steering
- the heavy-read principle: bulky input goes to a subagent; only
  conclusions enter issue-orch's context
- the phase router: derived work state → which skill file to read

Phase judgment lives in three skills under `agents/skills/`, each read
on demand by literal path (`~/orch/agents/skills/<name>/SKILL.md`), at
most one per wake:
- `issue-units` — decompose, dispatch workers (template stays
  `agents/worker.md`), judge reports; owns the decomposition and
  worker-brief-acceptance `TODO(user)` markers
- `issue-landing` — auto-land check (review, then merge), the hold without
  `auto-land`, a blocking finding's derived review hold, one rebase, merge-blocked
- `issue-giveup` — the one-fix-round rule, what `agent-stuck` requires,
  the write-off assessment; routes the fix round through the
  failure-reader tool

Discovery is the doc's pointer table plus the broad envelope's
`cat`/Read — deliberately NOT the Skill tool: issue-orch's cwd is the
issue worktree, so Skill-tool discovery sees only `~/.claude/skills/`
and the *target repo's* `.claude/skills/`, and installing orch's skills
into `~/.claude/skills/` would move them out of the repo they version
with. The skills live where the agent docs live and change in the same
commit.

Tool briefs are generated per call from their templates
(`agents/worker.md` per unit, `agents/failure-reader.md` per fix
round); tools have no standing doc of their own and no orch address.

## Lifecycles

**Bootstrap from zero.** `./run.py` starts the server and the ticker as two
processes; the ticker schedules (interval measured from `.tick.lock`'s mtime
so a manual tick from the dashboard resets it across the process boundary,
tick subprocess under a timeout so a wedged tick cannot stall the schedule —
the loop shape lives in `ticker.py` `tick_loop`, including its
nothing-may-escape-this-body try). Two processes because ticking spends
tokens and serving does not: either must be stoppable alone (#442). First tick builds the dashboard from
`repos.txt`. An issue labeled `agent-ready` fires condition 1; the digest has
no ack yet; dashboard-op is spawned cold (empty journal — "(no history)" is a
fine brief). It spawns repo-orch for the repo with the condition; repo-orch
runs the repo skill, picks the issue, spawns issue-orch; issue-orch claims
(`agent-working`), plans units, journals the plan to the issue, and runs
the issue pipeline, dispatching workers as it goes.

**Normal issue.** issue-orch runs the pipeline: workers edit, issue-orch
WIP-commits per unit (by pathspec) and pushes; the PR opens on
`issue-<n>`; CI runs (CHECKING → REVIEW or BLOCKED). `auto-land` present:
issue-orch reviews, then — clean → merges and closes out; blocking finding
→ **`core.merge_pr` refuses the merge** (orch#408, ruling orch#148 option
B: `core.review_blocks_merge` derives the hold fresh from the PR's last
`orch:review:v1` block) and issue-orch stops. The work state stays
`REVIEW`; no new label, no new state, and `auto-land` itself is untouched.
Nothing merges it until a fresh review posts a clean block — a re-review
lifts the hold on its own, with no operator act required; the operator can
still remove `auto-land` to stop issue-orch from reviewing again at all,
but that is the label's ordinary surviving role, not what lifts this
hold. If it died before finishing either path, condition 5 fires (no idle
wait in REVIEW) and the re-entered chain picks up where the PR's own
comments say it left off — the same fire-once check that reads whether a
review already posted also carries that review's outcome, so a resumed
issue-orch holds on a blocking block rather than calling merge to find
out.
Absent: it journals the held REVIEW and exits; condition 5 keeps the
handoff visible. BLOCKED: one fix round — re-enter the Implement stage from
the commits; still BLOCKED →
journal and `agent-stuck` — the write-off case; see "The state machine"
above. `agent-stuck` is never written for a blocking review finding — that
case only holds the merge.

**Failure and recovery, every path:**

- *issue-orch dies mid-flight (workers die with it — same pgid)* — commits
  survive locally and (best-effort) on the remote; uncommitted worker
  edits survive as the dirty tree. Condition 5 fires after `idle_over`
  (immediately if in REVIEW). Chain re-enters; the fresh issue-orch runs
  `git status`, WIP-commits the dirty step, and the stage derivation
  imports the existing plan and commits.
- *issue-orch wedges (alive, silent)* — condition 6. Dashboard-op checks
  the issue journal for a long-running lease; holds → wait; expired or
  absent → it journals the wedge and defers. **Nothing in the tree kills
  it** — the operator kills from the widget or CLI (TERM, wait ~3s, KILL,
  roll the row — port the escalation from old `core.py` `worker_kill`),
  and condition 5 re-enters the chain afterward.
- *Cross-issue merge conflict* — issue-orch rebases once; failing that,
  journals `merge-blocked` and exits. Condition 5 re-enters the chain;
  repo-orch merges what is clean and re-briefs the conflicted issue's
  issue-orch (see "Merge blocked").
- *dashboard-op dies mid-decision* — digest unchanged and unacked → re-woken
  next tick, every tick, until it completes. Loud, not silent.
- *dashboard-op acks a wake it did not handle* — silence until the ack lease
  expires (`ACK_TTL_MINS`), then re-woken over the unchanged digest.
  Bounded, then loud — see the wake gate.
- *A spawn's cold retry also dies* — the wake failed; it is in `<key>.log`
  and the conditions re-fire next tick.
- *gh unreachable* — Worlds fail to load; repos report not-ok; condition 4;
  orch is a read-only tracker of its local facts until the channel returns.
  Issue-journal writes in flight follow the journal failure rule.
- *Operator intervention, any time* — the widget shows every session with a
  `resume` command; kill from the widget clears the lease via the ledger;
  a human `gh issue comment` steers the next issue-orch.
- *Two live sessions on one key* — `contended`, condition 3, error alert.
  Detected, never assumed away: before acting on an issue, an actor that
  finds a live lease it does not own stops and tells its supervisor.

**Cross-machine takeover** (machine A dead or stopped):

> Resumable from another machine means exactly: code to the last pushed
> step, reasoning to the last tick or comment, conversations not at all.

Machine B: clone the orch remote (tooling), clone the work repo, edit
`repos.txt` (committed, but holds machine-local absolute paths — one manual
edit), `git fetch` the issue branch, then either `./run.py` or
`echo "continue" | ./spawn.py issue-orch <slug> <n>`. The issue handoff is
already on GitHub — `gh issue view <n> --comments` reads it with orch not
even installed. Inherited ledger rows read dead and roll aside. Deliberately
not portable: transcripts (machine death costs conversations, only
conversations), repo and dashboard journals (machine-scoped; B legitimately
starts its own), uncommitted dirty files (window: one logical step, bounded
by the WIP mandate), unpushed commits (window: one push retry), `public/`
artifacts (regenerated by B's first tick).

**Single-host is a design assumption.** If A is not actually dead and both
machines run, B cannot see A's pgids — cross-machine contention is invisible
to the lease. `--force-with-lease` refusals are the only tripwire. The
supported model is takeover of a stopped machine, not two live hosts.

## How the acceptance test passes

The test: a session dying costs only conversation — never progress, never
the record, never the ability to resume issue X. And every dead run stays
inspectable forever.

What survives any death, untouched by it: commits on `issue-<n>`
(derived), the dirty tree in the issue worktree (worker edits are files on
disk the moment they happen — process death does not unlink them), the
pushed branch and force-added plan file, the issue journal on GitHub (the
handoff), the transcript under the key's mangled cwd, the ledger row plus
every rolled row plus the append-only run log.

Recovery path 1 — operator: the widget shows the issue `alive:false` (or
alive and idle 40 min) with the session's
`cd <cwd> && claude --resume <id>`. Kill via the widget (clears the lease),
then resume the transcript or tell a fresh agent "continue issue X" — it
gets the issue comments and the branch, and continues.

Recovery path 2 — the tree, independently: the next tick derives the same
facts; condition 5 or 6 fires; `idle_over` flips the digest past the last
ack; dashboard-op is spawned (skip-if-alive, cold-retry-once); it spawns
repo-orch; the chain clears the stale lease and re-enters the issue from
the same journal, commits, and dirty tree.

Orphans: a session whose worktree was removed, or that orch never spawned
(someone ran `claude` by hand), appears in the unattached-sessions surface
(transcript dirs matching no key, `-orch-wt-` dirs excluded, capped at
recent). A dead orch-spawned role's history survives as the rolled
`<key>.<ts>.json` plus its delimited block in `<key>.log`, surfaced as
`prior_runs`. "At least I can see it happened" holds for every class of
dead session.

## Surfaces

| surface | for |
|---|---|
| `public/widget.html` | full tree, controls; served by `server.py`, opened chromeless by `run.py` |
| `:18803/widget.html` | same page over the tailnet |
| `spawn.py` | the orch CLI — agents' and the operator's verbs: `spawn`, `kill`, `tick`, `status`, `tail`, `watch`, `unwatch`, `nudge`, `ask` |
| `history.jsonl` | append-only record of what each tick saw |
| GitHub issue threads | per-issue journal and steering, any device |
| transcripts + `<key>.log` | what an agent actually did |

Every control is stop / go / restart at some level of the tree. If one
surface breaks, the others still reach the same sessions.

**Parity: the CLI is the agent's operator surface.** Whatever the operator
can do through the widget, an agent should be able to do through Bash, and
where both surfaces exist **one implementation sits behind both**.
`server.py`'s handlers and the CLI verbs call the same function in
`core.py`; the handler only shapes the HTTP response.

The coverage is partial, and the claim that it is total was false: there are
11 CLI verbs against 20 `/act` actions, so 12 actions have no CLI form
(`approve_item`, `assign`, `candidates`, `create_issue`, `log`, `probe`,
`reject_item`, `reply`, `review_now`, `set_auto_land`,
`unclaim`, `untracked`), and 3 verbs have no action (`journal`, `names`,
`status`). Closing that gap is #223. `orch/test_conformance.py` asserts
that no doc reasserts total parity until the two sets actually match. Do not "simplify" one
of the two callers away, and do not let a second implementation grow on
either side — this is the same one-representation discipline the digest rule
enforces.

The parity runs one way only. Agents act through Bash and already hold
arbitrary shell, so the CLI grants no principal a capability it lacked;
`/act` stays a closed set with validated arguments and no shell, and must
never gain a general run-anything route to mirror the CLI. The asymmetry is
deliberate: the web surface is reachable over the tailnet, the CLI is not.

Two reads of a session look similar and are not: HTTP `tail` takes a
transcript path and renders conversation turns; CLI `tail` takes a key and
returns its run log. Different questions — what it said, versus what the
process did.

The feed (`status.json`) reports, per repo: the issues with their work
state, liveness, sessions, commits, idle; `agent_sessions` — the current ledger row
per key (role, scope, alive, log mtime) and `prior_runs` count; unattached
sessions; alerts (error: gh read failed, contended, tick stale,
dashboard-op hung; warn: abandoned; info: REVIEW waiting, wedged). Alerts
in the error set are condition 4 and therefore digest inputs. An error
alert that can persist across ticks carries a stable `key`, which is what
the digest hashes in place of the whole dict; without one, a message
holding a changing number re-hashes the digest every tick. No history browser over rolled
rows — a count plus files on disk is enough.

**Attention is derived state rendered, not a message sent.** Levels are
stateless and exit, so "reporting up the tree" is fiction — a child cannot
tell a parent that is not running; it can only write somewhere. The tick
already derives every fact a report would carry (idle session, unowned
work, blocked PR, contention), so the conditions ARE the report and the
widget renders them prominently, at the top. The one thing derivation
cannot carry is *why* an agent concluded what it did — reasoning is not
derivable — and that already lives in the issue journal, whose URL the
feed carries. The tick derives that you are needed; the journal says why;
the dashboard shows both, each alert linking the issue it is about. There
is no notification channel, no listener hook, no event emission — a
condition that fires is a row on the dashboard, and anything beyond that
is the operator's to build outside orch, reading `status.json` or
`history.jsonl` like any other consumer.

## Security model

`server.py` binds loopback only; tailscale fronts it. Actions are a closed
set with validated arguments and no shell — a page that can run arbitrary
commands is a remote shell, not a dashboard. Port the shape from old
`server.py`: fixed `ACTIONS` table mapping name → (handler, required args),
regex-validated `repo`/`issue`/`path` arguments, body-size cap,
argv always a fixed list, never a string, never `shell=True`.

The set: `probe`, `kill` (ledger-based now: TERM the row's pgid, escalate,
clear), `nudge` (→ `spawn("repo-orch", slug)`), `ask` (free text, capped,
delivered as prose *to an agent* — the one open-ended action, and it reaches
an agent, never a shell), `tick`, `watch`/`unwatch` (edits `repos.txt`,
git-repo paths only), `tail` (transcripts under `~/.claude/projects` only,
`.jsonl` only). `repo_path` resolves slugs only against repos already in
`repos.txt` — never an arbitrary path.

`spawn.py` does not breach this: it is not reachable from the web surface.
The page can only POST the closed set; `spawn.py` is invoked by agents
through their own Bash tool inside their own permission envelope, and by the
operator at a terminal — both already hold arbitrary shell. The CLI adds
capability to no principal that lacked it.

**That argument assumed a human-supervised session, and spawned agents are
not one.** "Agents already hold arbitrary shell" is true of an interactive
session where a human approves each command; it is false by choice for a
`claude -p` the tick started at 3am, and the argument does not extend to it.
An autonomous tree that can merge PRs is a principal whose capabilities orch
picks, so orch picks them: each spawned session gets the per-role settings
file described under spawn(). This does not make orch a sandbox — issue-orch
holds broad Bash by necessity — it makes the *shape of the tree* enforced
rather than merely described.

**Which red lines are now mechanical:**

| red line | status |
|---|---|
| dashboard-op never merges a PR | **mechanical** — `Bash(gh pr merge:*)` denied |
| dashboard-op never touches labels | **mechanical** — `Bash(gh issue edit:*)` denied |
| repo-orch writes only `agent-ready`, never `agent-working` / `agent-stuck` / the merge-gate labels | **convention** — the label deny was lifted for nomination (orch#153); a permission rule cannot scope below `gh issue edit`, so the doc and the journal row hold this line |
| repo-orch merges only on a journaled escalation | **convention** — the merge deny is lifted; the deny layer sees verbs, not reasons |
| repo-orch never writes an issue journal | **mechanical** — `gh issue comment` denied to dashboard-op; repo-orch journals to its own file |
| issue-orch never spawns orch processes | **mechanical** — `spawn.py` denied; workers are Agent-tool subagents |
| nothing in the tree kills a session | **mechanical for the direct form** — `spawn.py kill` denied to dashboard-op and repo-orch; the `python3 spawn.py` bypass stays convention. The operator holds kill at the terminal and the widget |
| workers never run git | **convention** — brief red line plus issue-orch judging reports; subagents share issue-orch's envelope |
| each role writes only its OWN journal file | **mechanical** — only that path is allowed |

**Which remain convention, and why.** The denies are per-verb, and several
red lines are about *scope*, which a verb-level rule cannot see:

- *dashboard-op never spawns issue-orchs; repo-orch spawns only
  issue-orchs.* Both roles legitimately run `spawn.py`, and the forbidden
  part is the argument, not the command. A rule could match `Bash(~/orch/spawn.py repo-orch:*)`,
  but the docs pipe through `echo ... | spawn.py ...`, so the matchable head
  is unreliable — and the real enforcement point is `spawn()` itself, which
  could reject a role/caller pair. Not built: it needs caller identity
  `spawn()` cannot verify without an env-var honor system, the same reason
  spawn-depth enforcement is on the cut list.
- *issue-orch owns its own issue's labels exclusively.* `Bash(gh issue edit:*)`
  is allowed to issue-orch as a verb; nothing stops it naming another issue's
  number. Per-issue enforcement would need argument-level rules keyed on `<n>`.
- *Tree depth, and "never spawn a second actor for a key".* Structural, and
  already guaranteed by the flock rather than by permissions.

The honest summary: the permission layer now enforces **which verbs a level
holds**, and convention still governs **which arguments it uses them with**.
That is a real narrowing — the two red lines with the worst blast radius
(merging and labeling from the wrong level) are now refusals, not advice —
and it is not a claim that the tree is contained.

Two servers cannot fight: the fixed-port bind fails for the second. Two
ticks cannot fight: `.tick.lock`. Two spawns cannot fight: the key flock.

### The dashboard `assign` verb and the label deny — not a hole

`DENY_BY_ROLE` denies `Bash(gh issue edit:*)` to dashboard-op and to
repo-orch (see the mechanical-red-lines table above). It is a per-agent-session
permission envelope: `role_settings` builds it into the settings file a
spawned agent runs under. The web server is not an agent session — it runs
under no role, so no envelope applies to it — and `a_assign` in `server.py`
calls `gh issue edit` directly, unmediated by any deny list.

This is not a hole the design forgot to close. It is the distinction the
whole envelope rests on. The deny answers one question — *which agent may
choose to write a label* — and that answer stays issue-orch alone; nothing
above changes it. Pressing `assign` on the page is the operator acting
through one fixed verb, not an agent choosing to label, so the agent-scoped
deny was never the control guarding it in the first place.

What keeps that honest is that `assign` is not a general label writer.
`a_assign` writes exactly one label, `core.L_READY`, on one issue, in a repo
already gated by `repo_path` to one already in `repos.txt`. It cannot clear
`agent-stuck`, cannot set `agent-working`, and cannot reach a repo absent
from `repos.txt`. Every other label verb issue-orch owns stays unreachable
from the page.

Access control on the surface itself: none, by operator decision, settled
2026-09-13. `server.py` binds loopback only, and Tailscale Serve fronts it —
the private tailnet is the boundary, not a login on the page. This is a
recorded decision, not an open risk: no login, token, password, or
per-verb permission layer is planned on top of it.

## Cut list — and why each stays cut

- **Timestamp comparison in place of the digest** (`wake if max(updatedAt,
  commit times, PR times) > last_ack_time`). Tempting — GitHub and git already
  carry change timestamps, so the digest looks like a hand-rolled
  change-detector over data that timestamps itself. It fails on both sides of
  the suppression class. *Silence:* process death has no timestamp anywhere —
  an issue-orch that opens a PR and then exits cleanly moves no `updatedAt`, so an
  ack taken while it was still alive suppresses condition 5 forever and
  auto-land never fires. You cannot synthesize a death time; the ledger
  records `started`, not died. *Spam:* a failed `gh` read has no timestamp
  either — call it "now" and every tick exceeds the ack, burning one session
  per tick until the oracle recovers. The ack must reference *situation
  identity*, not recency, and any encoding of "this situation" is the digest
  under another name. Idle crossings alone would have been repairable
  (`last_activity + NUDGE_IDLE_MINS` is synthesizable); the other two are not.
- **Deriving the ack** ("handled" means the world below moved). The defer
  wake changes nothing in the world *by design* — that wake exists to
  conclude "nothing needed" — so a derived ack either wakes forever on every
  standing human-owed condition or needs a recorded "this run was for digest
  D" to go quiet, which is the record sneaking back one layer down.
  Attention is a fact about what an agent did; it has to be recorded. The
  ack's lease expiry is what makes recording it safe.
- **Validating the digest at `journal handled` write time.** Narrows forgery
  to replay, but does nothing for the real failure — a confused dashboard-op
  acking the *current* digest it did not handle — and the ack expiry already
  bounds both. A check that cannot catch the actual case is machinery
  without a failure.
- **A separately-maintained digest thin shape** — every digest bug in this
  design's history was drift between the thin shape and the conditions. The
  evaluator emits both; see the digest rule.
- **Collapsing the wake conditions into one predicate in code** — the predicate
  is true as an explanation and empty as a reduction: the named booleans
  *are* its cells, so collapsing removes names, not work, and the agents that
  must check them need names they can verify one at a time. The unifying
  paragraph lives with the conditions; the code stays explicit.
- **`SessionStart`/`SessionEnd` hook registration of sessions.** Claude
  already registers every session on the machine — subagents included — in
  `~/.claude/projects/<mangled-cwd>/`, keyed by cwd exactly as orch's
  addressing needs, vendor-maintained, zero orch code. A hook row is a
  *recorded cache of a derived fact*, and the only thing it adds is a pid for
  a session orch will never resume, supervise, or kill — foreign sessions are
  surfaced (orphan list, contended alarm), never driven, and surfacing needs
  no pid. It also adds a writer to `<key>.json` that never took the flock and
  can race `spawn()` mid-sequence, and `SessionEnd` is unreliable (`kill -9`,
  crash, power loss) so `killpg` remains the truth anyway — the scheme reduces
  to "write on start, let killpg decide," which is the current design plus
  writers and minus guarantees. Filtering by cwd does not save it: the filter
  would reproduce the one-key-one-cwd table inside machine-global shell config,
  a second copy of the addressing scheme, drifting, outside the repo.
  *Reopens only if* transcript-mtime liveness for foreign sessions proves
  measurably flaky — and then as one JSON line in a separate
  `state/foreign.jsonl`, never a ledger key, never touching the flock.
- **Spawn-depth enforcement.** Both verbs are tracked by construction:
  `spawn()` writes a ledger row (flocked, killable, visible), and the Agent
  tool creates no process at all — same pgid, transcripts contained under the
  parent key. Untracked proliferation is therefore impossible, so a depth
  guard would police a failure that cannot occur, using caller identity
  `spawn()` cannot verify without an env-var honor system. Tree depth is a
  red line in the agent docs, where this system's conventions live.
- **openclaw, everywhere** — dead dependency; binary absent; the entire
  judgment layer routed through it was dead code. `claude` on PATH is the
  default and only runner.
- **`ORCH_AGENT` runner template (and `ORCH_SESSION_FILE`, `.session`
  files)** — speculative: zero second runners exist. The seam is "spawn() is
  the one place processes start"; a future adapter edits one function.
- **`.orch.pgid`, `.orch.brief`** — the ledger row is the lease; the brief
  goes on stdin and into the journal. Two lease schemes encoded a
  worker-versus-role distinction that zero code reads after the lease is
  read: liveness, kill, and next-action are identical for both.
- **`CLAIM_GRACE_S` and the empty-stake window, plus its two tests**
  (`staked_is_alive`, `stale_stake_dead`) — an artifact of the bash version
  staking an empty file before forking. Holding the flock across
  check → Popen → write-pgid closes the window entirely. Never built, never
  tested.
- **`notes/<slug>.md`** — per-repo knowledge inside orch's tree *is* orch
  understanding repos. Conventions live in the repo's own
  `CLAUDE.md`/`AGENTS.md`, loading natively in the issue worktree; the repo skill
  points repo-orch at them.
- **`STALE` as a state** — staleness is a conclusion; conclusions belong to
  whoever holds context. It survived once as dead vocabulary in README and
  widget CSS; do not reintroduce it.
- **The tick-pushes-`state/` transport** (union-merge gitattributes,
  un-ignoring `state/`, push-failure handling) — replaced by the scope split:
  the issue journal lives on GitHub where its acts happen; repo and
  dashboard journals are machine-scoped and syncing them would be wrong.
- **A local mirror / fallback file for the issue journal** — a cache that is
  authoritative only when nothing works, at the price of "which wins" on
  every read.
- **Per-key attempt counters in the digest** — the conditional ack does the
  verify loop with zero mechanism; counters would add digest inputs outside
  the digest rule and need a self-exclusion carve-out.
- **A commit-decomposition layer or skill** — units are the decomposition;
  git owns commit granularity.
- **Backoff schedules, `blockers.jsonl`, `blocked.json`** — thresholds in
  the tick. The error-alert set in the digest covers "a permanent blocker
  must not be silently suppressed" with no schedule.
- **Adapter registries, plugin systems, sibling session listings, vendor
  JSON output parsing, lease *files*, log rotation, retention policies,
  history browsers, completion detection beyond pgid death, config files** —
  each is machinery for a need that has not occurred.
- **`journal_stats` for issues** — display sugar; the issue URL is better.
- **A `spawn_worker()` separate from `spawn()`** — worktree creation is
  pre-spawn mechanics for one role (now issue-orch), not a second spawner.
  Two spawners was the original sin this design unified.
- **Per-unit worktrees, branches, and PRs — and the worker as an orch
  role** (operator decision, 2026-09). One worktree, one branch
  (`issue-<n>`), one PR per issue; workers became Agent-tool subagents of
  issue-orch. What the OS-session worker bought, and where each property
  went: cold starts — the Agent tool is always cold; crash survival —
  worker edits are files on disk plus issue-orch's WIP commits;
  independent kill — deliberately gone, killing is the operator's act and
  its unit is the issue; per-unit derived state — collapsed into per-issue
  work state, units are recorded. Do not reintroduce a worker ledger key:
  two OS sessions sharing the issue worktree is exactly the
  one-key-one-cwd violation, and two git owners in one checkout is the
  interleaved-index corruption the single-owner rule exists to prevent.
- **A reporting/notification channel for attention** (listener hooks,
  event emission, report-up messages). Levels exit; a report to a
  non-running parent is a write to a journal, which already exists — and
  the tick derives everything a report would carry. Attention is derived
  state rendered on the dashboard; the journal carries the why. Anything
  that pushes beyond the widget is the operator's, built outside orch on
  `status.json`/`history.jsonl`.

## Non-goals

- Deciding what work is worth doing.
- Running unattended for days without being looked at.
- Multi-host coordination. Single-host is a design assumption; cross-machine
  is takeover, not concurrency.
- Being correct when `gh` or `claude` is missing — it degrades to a
  read-only tracker, which is the honest failure mode for a tool whose job
  is visibility.

## Tunables — chosen by default, not by decision

Every number below was inherited or picked by a designer, never deliberately
chosen by the operator. They are recorded here together because a value that
nobody decided is a value nobody will remember to revisit, and each of these
changes behaviour the operator will feel.

| knob | value | what it costs either way |
|---|---|---|
| `NUDGE_IDLE_MINS` | 30 min | How long a wedged session sits before anything looks at it. Lower wakes the chain over healthy slow work; higher leaves real wedges unattended. Inherited from the bash original; never measured. |
| `ACK_TTL_MINS` | 360 min | How long a wrong ack can silence the machine — and the heartbeat period for standing human-owed conditions. Lower re-verifies defers more often, one dashboard-op session per period; higher extends the worst-case silence a confused ack buys. Picked as 12× `NUDGE_IDLE_MINS`; never measured. Paired with `SPAWN_FLOOR_SECS` below — see that row. |
| `SPAWN_FLOOR_SECS` | 600 s | orch#174. Lower bound on agentic spacing per slug in `should_spawn` — no spawn for a given slug happens more often than this, independent of `ORCH_TICK_SECS` (the derivation cadence, 60s default). Paired with `ACK_TTL_MINS`, which is the upper bound on wrong-suppression silence: `SPAWN_FLOOR_SECS` must stay `< ACK_TTL_MINS * 60` (asserted at import in `tick.py`) or a floor could swallow the TTL and let a wrong suppression seal itself shut, the exact failure the TTL expiry exists to bound. Both numbers are always stated in seconds — "N ticks" stops meaning anything once the derivation cadence and the agentic floor can move independently. |
| cold-retry grace | ~5 s | How long `spawn()` watches a `--resume` launch before deciding the session id was bad. Based on "resume rejections fail fast", which was reasoned, not measured. Too short retries healthy slow starts cold; too long delays every genuine bad-id recovery. |
| contention window | 120 s | A session counts as live if its transcript moved this recently. Long enough to catch a session pausing mid-turn; also means a dead session reads live for up to two minutes in the widget. Contention detection needs it; the per-session dot is what pays. |
| ledger retention | forever | Rolled rows and run logs are never pruned. Deliberate — the busted run is the one worth reading — but it is unbounded growth on a real disk, and nothing warns when it matters. |
| default decomposition | one unit | issue-orch splits only when an issue obviously partitions into disjoint-file units. Too eager multiplies briefs and pathspec bookkeeping; too reluctant makes one worker carry everything. |

Two numbers here WERE deliberately chosen, by the operator (2026-09), and
are recorded with the tunables so they get revisited in the same pass:
**issues in flight per repo = 5** (repo-orch's judgment enforces it, not
the tick) and **fix rounds before `agent-stuck` = 1** (fail fast).

None of these has been exercised under load. Revisit them the first time the
system runs against a real queue, not before — but revisit them deliberately,
because none of them was chosen.

## Known limits, plainly

- **Absent CI reads as passing CI.** Signed off; the tests pin it. A repo
  with broken CI config is landable. `auto-land` is the human gate that
  contains this: the Land stage merges only when it is present.
- **CHECKING is a trap only an agent exits.** If CI hangs forever, LANDED is
  unreachable without action — but everyone has exited, so unowned-work
  fires and the chain gets its chance. The machine's liveness still depends
  on the tree running.
- **A wrong ack buys silence, bounded.** A dashboard-op that acks a digest
  it did not handle silences wakes for up to `ACK_TTL_MINS`, not forever;
  the expiry wake is the correction. Permanent silence would require the
  ack to be re-written every period.
- **Cold retry is best-effort-once** and cannot tell a bad session id from
  an unrelated crash.
- **Post-grace spawn failures surface one tick late** — spawn-and-exit has
  no supervisor of its own.
- **Transcripts never leave the machine.** No session anywhere can resume
  another machine's conversation.
- **Cross-machine contention is invisible to the lease**; force-with-lease
  refusals are the only tripwire.
- **Agent reasoning is as public as the repo** — the issue journal is issue
  comments. Fine for private repos; a judgment call per public repo.
- **Non-GitHub remotes cannot journal at issue scope** (the gitea line in
  `repos.txt`) — but the whole issue layer is gh-based, so this narrows
  nothing. Locked issues block journal writes; closed issues do not.
- **Transcript layout is a vendor coupling, in two places.** Resume-id
  derivation depends on `~/.claude/projects/<mangled>`, and idle derivation
  additionally depends on subagent transcripts living under
  `<session-id>/subagents/` beneath it. If either scheme changes, resume
  degrades to cold starts and idle degrades to commit-time only — which
  silently re-opens the false-wedge on subagent-heavy workers.
- **`rm -rf state/` blinds all liveness and destroys the repo and dashboard
  journals.** The issue handoff (GitHub) and the work (git) survive it.
- **Append-only grows forever** — ledger logs, rolled rows, history.jsonl.
  Deliberate; prune only when size actually hurts.
- **Repo names that match `<slug>-issue-<n>` collide under cwd mangling.**
  Do not create them.
- **Parallel-editor collisions are convention-bounded.** Disjoint targets
  and commit-by-pathspec are brief text and issue-orch judgment; a worker
  that strays off-target puts a sibling's half-edits at risk of being
  swept into the wrong commit. Surfaces at review, not before.
- **A kill's blast radius is the whole issue.** Workers die with
  issue-orch (same pgid). Files on disk and commits survive; every
  in-flight worker's conversation is lost at once, and no individual
  worker can be killed alone.
- **issue-orch is the long-lived session now.** It carries the whole
  issue — decomposition, the whole pipeline, every worker report — in
  one context.
  Context exhaustion mid-issue is the expected death mode, and the
  dirty-tree + WIP-commit + journal recovery path is the normal path, not
  the rare one.
- **A spawned child gets an explicit env, not a passthrough of `os.environ`.**
  `_launch` strips the Claude/Anthropic vars a parent session would otherwise
  leak into the child (see spawn()). The strip-list is an explicit set plus
  one prefix rule, empirically derived — a var that merely starts with
  "CLAUDE" but isn't on the list is left alone, so a deliberately user-set
  var of that shape still reaches the child.

## Implementer's map of the old code

Consult, then delete:

| old file | keep the subtlety |
|---|---|
| `core.py` | `pr_green`/`pr_red` non-complementarity and its comment; `World.load` one-snapshot rollup; `pr_for` collapse order; `session_dir` translate table; `base_ref`/`work_mtime`/`gh_repo` derivations; `worker_kill` TERM→wait→KILL escalation; the no-STALE comment; journal file format |
| `tick.py` | `.tick.lock` flock pattern; `digest_of` thin-shape-then-hash; journal-only-on-change; abort-repo-on-failed-read stance |
| `feed.py` | `unattached_json` orphan scan and its `-orch-wt-` exclusion; alert levels; the feed tree shape |
| `server.py` | closed ACTIONS table, validation regexes, body cap |
| `ticker.py` | `tick_loop` shape with its nothing-escapes try and subprocess timeout; the interval read from `.tick.lock`'s mtime, never an in-process timer |
| `run.py` | app-mode window launch with plain-tab fallback — port as-is |
| `test_core.py` | no frameworks; real process groups via `Popen(start_new_session=True)` — never stub `killpg`; the four rollup-pinning checks. Drop the two grace-window tests; add the flock-stake assert: spawn twice for one key → one pgid; kill → respawn rolls the dead row aside |
| `~/.claude/skills/ship-change/SKILL.md` | the operator's manual skill and the pipeline's structural REFERENCE (stage shape, derive-stage-from-disk, role split) — never invoked or edited by orch |
