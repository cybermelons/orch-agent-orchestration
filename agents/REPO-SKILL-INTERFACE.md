# REPO-SKILL-INTERFACE

The repo-management skill **exists**, at
`agents/skills/repo-management/SKILL.md`. This file specifies its interface
only — the skill body holds the repo understanding, the same way a repo's
own judgment cannot be guessed by orch. This skill is **load-bearing the
same way the agent docs are**: repo-orch idles as a thin router without it.

## What orch hands it

repo-orch runs this skill the way issue-orch runs its issue pipeline — the skill holds
the repo understanding, repo-orch holds the skill's context. orch hands
the skill:

- the repo's checkout path
- the repo's slice of `status.json` (its issues with work state, liveness,
  alerts)
- the repo journal tail (`state/repos/<slug>/orch.jsonl`, oldest first)
- the literal command to act with:

```bash
echo "<reason>" | ~/orch/spawn.py issue-orch <slug> <n>
echo "<reason>" | ~/orch/spawn.py issue-orch <slug> <n> --fresh
```

`--fresh` starts that key cold — no `--resume`, same key and worktree. The
skill chooses it when it judges the prior conversation spent; the default
resumes.

## What it must decide

The policies are decided and written in `agents/repo-orch.md` — that is the
same set of decisions, stated from the session's side. What this file fixes
is the **shape the skill's answer has to take**: repo-orch holds two verbs
(spawn; merge, on a journaled `merge-blocked` report only) and three journal
events, so every rule below must terminate in one of them — under the
decided bound of **`in_flight_cap` sessions in flight at once**.

What is left to the skill is not *whether* to defer or file — those are
settled below — but the one thing only repo understanding can supply: the
**prioritize** step's strategy.

Each rule the skill writes must resolve to exactly one of:

| outcome | what repo-orch actually does |
|---|---|
| start / re-enter / write off | `echo "<reason>" \| ~/orch/spawn.py issue-orch <slug> <n>` (add `--fresh` to start cold) + journal `started`. A write-off is this same spawn with an assess-and-give-up brief (see `DESIGN.md` "Giving up") |
| defer | journal `deferred` with the condition for lifting it — there is no timer |
| merge (`merge-blocked` only) | `spawn.py merge <slug> <n> <pr> --by merge-blocked` — only when an issue journal carries `orch/issue-orch merge-blocked`; still-conflicted branches go back down as a rebase-brief spawn. Not `gh pr merge`: the verb writes the `landed` journal row at the merge instant |
| file | search open issues, comment if found, else `gh issue create --label agent-stuck` with the full account in the body — there is no notification channel and no escalation object. Skip this outcome entirely when the thing needing a human is already a state the dashboard shows (ABANDONED, a held REVIEW, a dead owner, contention): report by that state, do not restate it in a filing |

There is no fourth outcome. A rule that concludes "leave it" is a rule that
concludes `deferred` and must be written as one, or the next repo-orch
re-decides it from scratch.

### Ordering — run repo-orch through three stages

The skill drives `triage → prioritize → start`, and returns per issue this
wake: **start** (one spawn) or **defer**.

| stage | what the skill supplies |
|---|---|
| triage | which UNCLAIMED issues, labelled or not — the skill's gate decides which get the label — are startable at all — the defer rule below removes the rest |
| **prioritize** | **the repo's ordering strategy. This is the skill's own judgment and the reason the skill exists; no formula belongs in the agent doc** |
| start | spawn down the order until `in_flight_cap` sessions are in flight; the remainder are `deferred` lines |

The ordering must survive as journal text or not at all — an issue ordered
last is not queued, it re-fires condition 1 next tick and the next repo-orch
starts from zero.

A note may be any length on disk, but the wake prompt built from it shows
only the first 300 chars, marked `… [+N chars]` where it cut. So whatever is
load-bearing must sit in the note's FIRST clause, or in a structured field
(`--digest` / `--covered` / `--folds`) — never in a trailing sentence, which
survives on disk but is invisible to every wake that reads the prompt.

### Re-entry — return "re-enter", and count the deaths

A CLAIMED issue whose issue-orch is dead resolves to **re-enter**: the same
spawn, with a continue brief. The skill does not decide whether the issue is
hopeless — the resumed issue-orch decides that once it has looked.

Two things the skill must return with the re-entry:

1. **Resume or cold.** Default resumes. When the prior conversation looks
   spent — context exhaustion, or it died confused — the spawn takes
   `--fresh` so the key starts with no `--resume`.
2. **The loop terminator.** After ~3 identical deaths with no progress
   between them, the brief becomes assess-and-give-up (*assess; if hopeless,
   journal why and mark `agent-stuck`* — `DESIGN.md` "Giving up"), so the
   issue reaches ABANDONED instead of retrying forever.

NOT defer — repo-orch cannot clear `agent-working`, so a deferred CLAIMED
issue fires condition 5 on every tick forever. File only when a session
cannot help (oracle down, repo broken).

### Defer — cap, or a plain blocker

The skill returns **defer** in exactly two cases: `in_flight_cap` sessions
already in flight, or the issue plainly cannot proceed (depends on an
unmerged PR, needs information nobody provided, references a branch that does
not exist). Not open-ended judgment.

Must resolve to a journal line **stating the condition that lifts the
deferral**, since nothing else re-reads it.

### File — early, on first real ambiguity

Anything the skill is not confident about goes to the operator, while it is
still small: search open issues, comment if found, else `gh issue create
--label agent-stuck`, with the repo's state left untouched so the condition
keeps it on the dashboard. Skip this when the ambiguity is already a state
the dashboard shows — report by that state, do not restate it in a filing.
The loop terminator above is what stops early filing from becoming
per-tick noise on a permanently broken issue.

## What it spawns

Issue-orchs, via the one command above, and nothing else. There is no
worker role anywhere — workers are issue-orch's Agent-tool subagents.

## What it journals

One line per decision to the repo journal
(`state/repos/<slug>/orch.jsonl`):

```json
{"at":"<iso>","actor":"repo-orch","event":"started|deferred|filed","note":"<why>"}
```

## Red lines — repeated here so the skill can see them

- repo-orch's own label is `agent-ready`, to nominate work (orch#153). It
  also holds one narrow grant on the merge-gate labels: it may write the
  `no-auto-land` / `auto-land` pair to hold an entangled issue (see
  `repo-orch.md`, "Your hold verb"), but it may never ADD `auto-land`.
  Issue-orch owns claiming (`agent-working`) and giving up (`agent-stuck`)
  exclusively. repo-orch may not remove `agent-ready` from an issue it did
  not nominate. This is **convention, not mechanism** — a permission rule
  cannot scope below `gh issue edit`, so the same grant that allows
  nominating would also allow claiming. The doc and the journal row each
  nomination leaves are what hold the line.
- repo-orch merges only on a journaled `merge-blocked` report, never to
  hurry a held REVIEW.
- at most `in_flight_cap` sessions in flight per repo.

## Absolute-path requirement

The skill must direct repo-orch to read the repo's own docs — `CLAUDE.md`,
`AGENTS.md`, contributing docs — **by absolute path against the checkout**.
repo-orch's cwd is `wt/<slug>/`, not a checkout of the repo it manages, so
these files do not load natively the way they do for issue-orch sitting
in the issue worktree.
