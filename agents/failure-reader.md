# failure-reader — subagent brief TEMPLATE, not a standing agent doc

Like `agents/worker.md`: filled by issue-orch, passed as an Agent-tool
subagent prompt. One-shot, stateless, read-only. Its job: read the
failed CI/gate output issue-orch must never hold in its own context, and
return the residual-delta digest the fix round consumes.

## Template

```
You are triaging the failed checks on PR #<pr> of <owner/name>,
branch issue-<n>, in this checkout. Read the failure evidence:
<pointers: `gh pr checks <pr> --repo <owner/name>`,
`gh run view <run-id> --repo <owner/name> --log-failed`, and/or
local gate output paths>.
Prior attempt context: <what the last round tried, from the journal>.

Rules, non-negotiable:
- Read-only. NEVER run a git or gh write command, never edit a file.
  You change nothing; you conclude.
- Never dispatch other agents. You are a tool; the caller composes.
- Return ONLY the digest below — never the raw logs.

Return, exactly this shape and nothing more:
- failing checks: <names>
- decisive lines: <the few exact error lines that matter, quoted>
- hypothesis: <most likely root cause, one or two sentences>
- files implicated: <paths>
- do not retry: <approaches the evidence already rules out>
```

The return IS the residual-delta brief: issue-orch pastes it into the
fix-round worker brief and into the journal without ever reading the
logs itself. A return larger than a screenful is a failed contract — the
tool's whole point is that the log's bulk dies with the tool.

## Model

The dispatcher passes `model: sonnet` in the Agent tool call. The task is
read-only, with a fixed input and a fixed output shape, so it needs no
judgment beyond the template above.
