---
name: issue-giveup
description: "issue-orch's give-up path: the one-fix-round rule, what agent-stuck requires, the write-off assessment. Read when work state is BLOCKED, or when your spawn brief says assess-and-give-up."
---

# Giving up — one fix round, then stuck

The rule is decided (operator, 2026-09): **a BLOCKED PR gets exactly one
fix round. Fail fast.** Your fix-round count lives in the issue journal
and nowhere else — read the thread before counting.

## The fix round

Journal shows no prior round → run one, in this order:

1. Dispatch the **failure-reader** tool (template:
   `~/orch/agents/failure-reader.md`) on the failed checks. It reads the
   logs so you never hold them; it returns the residual-delta digest —
   what is failing, the decisive lines, hypothesis, files implicated,
   what not to retry.
2. Journal `orch/issue-orch fix-round` with that digest.
3. Re-enter the Implement stage from the existing commits: dispatch a
   fresh worker with the digest as its brief, run the gates, re-push
   `--force-with-lease`.

## Still BLOCKED after the round

Journal what BOTH rounds tried FIRST — a human removing `agent-stuck`
otherwise hands the next issue-orch the same dead end. Then:

```bash
gh issue edit <n> --repo <owner/name> --add-label agent-stuck
```

The label is loud (condition 1, every tick) by design. Exit.

## Spawned as the write-off assessment

repo-orch judged this issue hopeless but cannot label; its write-off IS
you, briefed assess-and-give-up (`DESIGN.md` "Giving up"). Honor it
honestly in both directions:

- continuable → continue (your caller could not read the code; you can —
  go read the `issue-units` skill and work it).
- hopeless → journal the assessment, mark `agent-stuck`.

Either way the issue must leave the ambiguous CLAIMED-with-no-owner
state. Exiting with neither progress nor the label strands it: condition
5 fires every tick forever, and nothing downstream can conclude give-up
for you.
