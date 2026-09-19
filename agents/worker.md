# worker — subagent brief TEMPLATE, not a standing agent doc

Workers are **Agent-tool subagents of issue-orch**, not orch processes.
There is no `spawn.py worker`, no worker ledger key, no worker cwd, and no
worker permission envelope — a worker runs inside issue-orch's session
(same process, same pgid, transcript under `<session-id>/subagents/`) and
inherits its envelope. This file is the template issue-orch fills in per
unit and passes as the subagent's prompt. Nothing below is read by a
worker from this path — it is copied, filled, and sent.

Every worker edits inside the shared issue worktree (issue-orch's cwd, a
real checkout on branch `issue-<n>`). The repo's own `CLAUDE.md` context
is the parent session's; restate anything the worker must know in the
brief.

## Template

```
You are working unit <unit> of issue #<n> in <owner/name>, inside a
shared checkout. Other units may be in flight in this same tree —
touch ONLY your target files.

Target: <the exact files/components this unit may touch — exclusive>
Delta: <the new state or behavior this unit produces>

<any other steering: constraints, repo conventions, prior-attempt notes>

Rules, non-negotiable:
- NEVER run git. No add, no commit, no push, no status, nothing. The
  orchestrator owns all git state; your edits are files on disk and
  that is all they need to be.
- NEVER run gh, and never touch files outside your target list.
- Running a build or any tool that writes repo files? Do not assume it
  targets this worktree — set ORCH_HOME explicitly to this worktree's
  path first. Several tools fall back to a hardcoded default when
  ORCH_HOME is unset, and that default is the LIVE checkout, not
  wherever you happen to be running from.
- NEVER dispatch other agents. You are a tool; the caller composes.
- When done, report: the files you changed, what you did, anything you
  could not do and why. Your report is how your work gets committed.
- If you cannot proceed, say `blocked:` and the reason, and stop. Do
  not work around it, do not widen your target list, do not wait. The
  orchestrator decides what happens next.
```

The **target** list is load-bearing twice: it is the worker's containment
(parallel units are safe only because targets are disjoint), and it is the
pathspec issue-orch commits by. A worker that strays off-target puts a
sibling unit's half-edits at risk of being swept into the wrong commit.

The **delta** must be concrete enough to act on with no other context: a
worker cannot read the issue thread, cannot ask questions, and reports
once. Anything not in the brief does not exist for it.

## What moved out of this template, and where

The old worker mandates — WIP commits, best-effort push, force-adding the
plan file, `--force-with-lease`, the merge decision, running the
pipeline — are **issue-orch's own now** (`agents/issue-orch.md`).
Workers never run git and never run the pipeline; the issue pipeline is
issue-orch's, and workers are the implementation subagents inside its
Implement stage.

## Model

The dispatcher passes `model: sonnet` in the Agent tool call. The brief
already names exact target files and a concrete delta, so the task does not
need judgment beyond following it. issue-orch reads every worker report, so
a miss is caught one level up. Workers are 189 dispatches at roughly 384k
weighted tokens each, 45.1% of issue-orch's own spend — the largest single
lever in the tree.

The bare alias `sonnet` is correct HERE and in `orch/core.py`. Both the
Agent tool and the `--model` spawn flag take a short alias on this host.

An `anthropic/`-prefixed string does NOT work and must never be used: it
does not fail the spawn, it fails INSIDE the run and takes the session down
with "There's an issue with the selected model". Combined with orch#138 —
a live session's log holds only a spawn banner until it exits — a session
killed that way looks exactly like one that is working. That is orch#165;
it took the whole tree down for 22 minutes on 2026-09-14.

## Why workers are always cold

The Agent tool starts every subagent fresh — the same property the old
cold-spawn rule bought: no poisoned conversation is ever inherited. A
failed unit is retried as a new subagent with a brief written to the
residual delta (what is still wrong, what the last attempt tried, what
not to try again), never by resuming the failed one.
