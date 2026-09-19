# SC-INTERFACE — retired as an interface (operator, 2026-09)

orch no longer invokes `/sc` (ship-change). The issue pipeline is
orch's own, written into `agents/issue-orch.md` for an agent with no
human at the stops — zero mismatches by construction. This file
replaces the former seven-mismatch list; consult git history if you
need it.

`~/.claude/skills/ship-change/SKILL.md` remains the **operator's manual
skill** for interactive work: never invoked by orch, never edited by
orch. The `pre-merge` stop added to it during the shared era stays —
harmless, opt-in, possibly useful to the operator; orch no longer
depends on it.

## What orch borrowed from /sc — the structural reference

- **The stage shape**: plan → implement → gate → PR → merge.
- **Derive stage position from disk, never record it**: plan file
  exists → planning done; commits ahead of base → implementing; PR
  open → at review; PR merged → done. This is what makes a dead
  issue-orch resume at the right stage.
- **The role split**: one orchestrator, many implementers, one
  committer — issue-orch is the orchestrator and sole committer;
  workers are the implementers.
- **The plan file as single source of truth** (`tmp_plan-*.md`,
  force-added, committed on the issue branch).
- **Gates are the repo's own checks only** — build, tests, lint; none
  defined → say so, never invent.

## What orch deliberately does differently

- **No human stops.** /sc pauses and waits for a person; issue-orch IS
  the decider at every joint. The `auto-land`-absent case opens the PR
  and leaves it at REVIEW — journal, exit — rather than pausing for
  nobody (condition 5 keeps it visible).
- **WIP commits by pathspec throughout**, force-with-lease on squash —
  the crash-survival mandates /sc never needed.
- **Merge conflicts are handed off, not fought**: one rebase, then
  `orch/issue-orch merge-blocked` to repo-orch, which alone sees
  across issues.
- **One fix round, then `agent-stuck`** — /sc's fix round-trips are
  open-ended under a human; orch's are bounded by the operator's rule.
- **Heavy reads go to tools** (failure-reader, ad-hoc readers) so the
  long-lived session's context survives the issue.
