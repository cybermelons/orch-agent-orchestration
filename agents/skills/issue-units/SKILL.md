---
name: issue-units
description: "issue-orch's work phase: decompose an issue into units, dispatch worker subagents, judge their reports. Read when work state is ACTIVE or CLAIMED — you are about to plan or continue the work."
---

# Units — decompose, dispatch, judge

## Decomposing

Units are your work breakdown, **recorded, never derived**: they exist in
your plan file and journal entries, nowhere else. orch sees one branch and
one PR for this issue and nothing smaller. Two units that must land
together are still one issue — there is nothing to group.

Default decomposition is ONE unit (`DESIGN.md` tunables): split only when
the issue obviously partitions into disjoint-file units.

The one hard rule: **parallel units get disjoint target files.** Two units
that touch the same file run sequentially. Nothing enforces this; your
decomposition is the enforcement, and a violation surfaces as one worker's
half-edits swept into another unit's commit.

**Disjoint file sets is the whole boundary rule.** Units exist for one
reason: so subagents can work in parallel inside one shared worktree. Split
where the file sets come apart cleanly; otherwise do not split.

There is no size heuristic, no taste test, no logical-coherence requirement.
One PR per issue means splitting buys parallelism and nothing else — a
"nicer" boundary that overlaps files is worse than no split at all, because
it corrupts commits (they go in by pathspec), not just merges.

Record the breakdown as you make it: your plan file and journal are the only
record, and a successor reconstructs it from them alone.

## Dispatching workers

Workers are Agent-tool subagents — never `spawn.py` (denied; there is no
worker process). Fill the template in `~/orch/agents/worker.md` per unit:
exclusive target files + concrete delta + the never-git red line. Restate
any repo convention the worker must know — it cannot read the thread and
cannot ask. Workers are always cold; a retry is a NEW subagent with a
brief written to the residual delta (what is still wrong, what the last
attempt tried, what not to try again) — never a resume.

**Check three things before you dispatch. A brief missing any one of them is
not dispatched — it is rewritten.**

1. **The exact files this unit owns.** Named, not described. Without them
   you cannot verify disjointness against the sibling units, which means the
   issue is not actually decomposed yet.
2. **A concrete delta** — what changes, not a goal. "Make auth work" is a
   goal; "add `verify_token()` to `auth.py`, call it from `handler()`" is a
   delta.
3. **A done-criterion the worker can check itself** — a test passing, a
   function existing, a behaviour working. It has no thread and cannot ask
   you.

A bad brief is not free: a wrong-direction worker leaves wrong-direction
edits in the shared tree that you must revert by pathspec before its sibling
units commit cleanly. Rewriting the brief is cheaper than the revert.

### Dispatch disjoint units in ONE message

Decomposing for parallelism does not produce it. The Agent tool accepts
several calls in a single message, and issuing them together is the only
thing that makes two units actually run at once — check 1 above already
proved their file sets are disjoint, so use it. Measured on orch#198
(2026-09-16): six units, correctly decomposed, dispatched one at a time;
two of them touched disjoint files with no ordering and still cost about
two minutes of dead serialization.

Dispatch sequentially when a real dependency exists, and only then:

- an earlier unit's output is a later unit's input (a survey pass that
  tells the next worker what to edit), or
- the file sets overlap — dispatching those together corrupts commits,
  because they go in by pathspec. This is the same failure the hard rule
  above guards.

So: dependency → sequential; disjoint and independent → one message.

**Cap concurrency at three workers, and dispatch none while a gate — the
repo's own build/test/lint run — is in flight.**
Sessions on this host are killed by the OOM killer with exit 137 and no
final message (`orch#197`), and concurrent subagents raise peak memory.
Two minutes of saved latency is not worth a session death that costs the
whole issue. More than three ready units → dispatch three, judge them,
dispatch the rest.

## Judging a report

The report is how the work gets committed. On each worker return:

1. Off-target files touched → revert them by pathspec before any sibling
   unit commits; journal it.
2. Delta met → commit by pathspec (`git add <that unit's files>`), push
   best-effort, journal the unit result.
3. Delta not met → retry as a fresh subagent with a residual-delta brief
   (`agents/worker.md`, "Why workers are always cold"). A unit failing
   the same way repeatedly is the issue failing: journal what each
   attempt tried and read the `issue-giveup` skill instead of burning
   more subagents.
