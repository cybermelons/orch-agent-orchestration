---
name: issue-landing
description: "issue-orch's landing phase: the auto-land check (review, then merge), the hold without auto-land, a blocking finding's derived review hold, one rebase, the merge-blocked handoff to repo-orch. Read when work state is REVIEW or LANDED, or a merge was refused."
---

# Landing — merge, hold, or escalate

## auto-land decides

One flag, not two. `auto-land` means: automatically review, then merge.
There is no separate `auto-review` flag — review is not something you
opt into independently; it is the first half of what `auto-land` does.

**Inputs.**

```bash
gh issue view <n> --repo <owner/name> --json labels
```

— plus the repo default your brief carries from repo-orch, which read the
repo's own entry in `ORCH_HOME/orch.json` (repo-orch's cwd is not the
managed checkout; issue-orch does not read that file itself).

**Precedence, per-issue over per-repo, both directions.** This table gates
both steps: whether to review here, and — re-read against fresh labels at
the merge instant — whether to merge in Step 2.

- Label `auto-land` present → review then merge on green, even if the
  repo default is off.
- Label `no-auto-land` present → do not review, do not merge, even if the
  repo default is on.
- Neither label → the repo default decides.
- Neither label and no repo default → do not review, do not merge.
- Both labels present → `no-auto-land` wins; holding is the safe
  direction.

**This read decides whether to REVIEW. It does not authorize the merge.**
It binds fully in this direction: a `no-auto-land` seen here holds, and you
do not review on the theory that Step 2 will re-read and sort it out.
Holding is the safe direction at both reads.

What it cannot do is authorize a merge that happens later. Labels are
mutable and an operator sets `no-auto-land` precisely while a session is
running, because that is when a PR is visible and green. So Step 2 re-reads
before merging — not because this read was unreliable, but because a hold
can arrive after it.

`no-auto-land` (or a false/absent default) decided → do not review, do not
merge, ever — and do not pause waiting for anyone: leave the PR open at
REVIEW, journal that you are holding and what for (`orch/issue-orch
waiting`), and exit; condition 5 keeps the handoff visible every tick. The
hold is the operator's gate — nothing you do lifts it. This is decided
policy, not judgment (`DESIGN.md` "Normal issue"): held REVIEW is a
standing condition a human clears.

`auto-land` decided → review first, then let the review outcome decide the
merge. The two steps below are both inside this one flag.

## Step 1 — review

**FIRE ONCE — the part most likely to be got wrong.** Held REVIEW re-fires
condition 5 every tick, so you WILL be re-entered over an unchanged
reviewed PR. Before dispatching a reviewer, check the PR's existing
comments for a review already posted:

```bash
gh pr view <pr> --repo <owner/name> --comments
```

Found → read its `orch:review:v1` block. Then ONE question decides whether
you review again, and it is not a judgment call:

**Has the PR been pushed to since that comment was posted?** Ask the PR for
its own head and that head's date — never derive either from a branch name
or from this checkout:

```bash
gh pr view <pr> --repo <owner/name> --json headRefName,headRefOid \
  -q '.headRefName, .headRefOid'
git fetch origin && git log -1 --format=%cI <headRefOid>
```

Two things this spelling gets right, both of which a shorter command gets
wrong:

- **Never assume the branch is `issue-<n>`.** It often is not:
  `World.branch_for_issue` resolves an issue to `issue-<n>` OR an
  `issue-<n>-<suffix>` sibling, preferring whichever carries an open PR
  (`core.py`, "orch#329: resolved, not assumed issue-<n>"). orch#329 is the
  incident where PR 266 sat on `issue-259` while PR 328 on
  `issue-259-sections` held the real state. Reading `origin/issue-259`
  there gets a stale sibling — or no ref at all — and answers the wrong
  question. The PR knows its own head; ask it.
- **Never bare `git log -1`.** That reports HEAD of whatever this checkout
  is on, which need not be this issue's branch at all — a resumed session
  re-enters from a tick, and nothing above puts it on the PR's branch. From
  a worktree on `main` it reads main's date and answers about main.

The `fetch` is not decoration either: an unfetched worktree does not have
the object a push from anywhere else just created.

Both sides are absolute instants — `%cI` carries an explicit offset and the
comment's timestamp is UTC — so compare them directly; no timezone
normalising needed. **The direction, spelled out:** head date LATER than the
review comment → commits since the review → re-review. Head date EARLIER or
equal → no commits since → skip, and use the block you already read. Note
the offsets differ (`-04:00` vs `Z`), so compare the instants, not the
strings: `2026-09-17T14:51:25-04:00` is 18:51:25Z, which is EARLIER than a
comment at 18:51:48Z even though "14" reads smaller than "18".

**One known false positive, and it is the safe direction.** `%cI` is the
COMMITTER date, which a rebase rewrites on every commit. So the one rebase
Step 3 tells you to do will push the head's date past the review and read
as a new event, even though the diff against base did not change by one
line. You will re-review an unchanged diff. That is wasted work, not a
wrong answer, and it beats the alternative: no cheaper signal distinguishes
"rebased" from "fixed" without a sha pinned into the block, which the block
does not carry (see the `ponytail:` note in `core.review_blocks_merge`). If
you can see plainly that the only change since the review was your own
rebase onto a moved base, say so in the journal and skip — that is the one
place here where judgment beats the mechanical read.

This is the "new commits since the review" test, done mechanically rather
than by eye — `review_items_for_pr`'s `sha` cannot answer it (it is the
head at read time, not a head pinned into the block), so do not try to
derive it from the block.

- **No commits since the review** → the review already happened; do not
  review again. Go straight to Step 2 with the block's outcome.
- **Commits since the review** → that IS a new event. Review again, against
  the current diff, and post a new block. Do this even when the previous
  block was blocking — especially then: a blocking hold lifts ONLY by a
  newer clean block, so skipping the re-review here is what would strand a
  PR whose defect is already fixed.

This check IS the record — do not add any new piece of recorded state to
track it.

**Dispatch.** Fill `~/orch/agents/reviewer.md` with the issue body and the
diff — nothing else; the template's own rules govern what it does with
them.

**On return.** Post the findings to the PR as a single comment:

```bash
gh pr comment <pr> --repo <owner/name> --body-file -
```

The comment MUST carry the reviewer's `orch:review:v1` block verbatim,
unedited, last. Do not reformat it, do not renumber it, do not summarise
it into your own words. Code parses the items back out of that comment, and
the comment is the only record — there is no DB and no state file. The
comment IS the storage.

**No findings still posts a comment**, carrying the empty block
(`<!-- orch:review:v1 -->`). An absent comment is indistinguishable from a
review that never ran, and the fire-once check above is a read of exactly
these comments — skip the comment and the next tick reviews again.

**Do not file out-of-scope findings as issues here.** They arrive as
`follow-up` items inside the block, and the block is their record: it is a
PR comment, so it survives the merge and stays readable afterwards.

**Nothing files them automatically yet.** The operator has ruled that the
merge is the filing trigger and repo-orch is the filer, but no instruction
in `agents/repo-orch.md` or `agents/skills/repo-management/SKILL.md`
implements that ruling — the words `follow-up` do not appear in either.
The older route (the operator approving the item on the dashboard) is dead
post-merge: `feed.py` builds awaiting rows only for `work_state == "REVIEW"`
with a PR, and a merged issue is no longer at REVIEW, so it has no row to
approve. Until the ruling is built, a `follow-up` item on a merged PR is
recorded in the block and on no work surface. Do not file it here anyway — that double-files it against
the day the filer lands. See orch#234.

## Step 2 — the review outcome decides the merge

**The review does not merge. It decides which of the two paths below you
take**, and `auto-land` is what authorizes the merge path at all.

### Re-read all three preconditions at the merge instant

The merge has three preconditions — **the gate label, the base, and CI**.
All three are mutable by actors you cannot see, so read all three in one
call sequence immediately before `gh pr merge`, and decide from THAT read.
Never from your wake-entry read, and never from anything your brief says:

```bash
gh issue view <n> --repo <owner/name> --json labels
gh pr view <pr> --repo <owner/name> --json mergeable,mergeStateStatus,statusCheckRollup
git fetch origin && git log --oneline HEAD..origin/<base>   # empty = current
```

`mergeStateStatus` alone does not establish that the base is current — it
read `CLEAN` on a PR nine commits behind. The `git log` above is what
answers that question: any output means rebase before merging.

By this point the review has already run, so the only live question is
merge or hold. Decide it from the fresh labels:

- `no-auto-land` present → hold. It wins over everything, including a
  present `auto-land` and a repo default that is on.
- `auto-land` present → merge, if the review was clean and CI is green.
- Neither → the repo default decides; no default → hold.

Holding is the safe direction in every ambiguous case.

This is the same precedence as the table in "auto-land decides" above,
stated for the one decision left here. That table's rows carry a review
verb as well as a merge verb because they gate Step 1 too; do not read
"do not review" as inapplicable now merely because the review already
happened. A row that says do not merge means do not merge, whatever
its review clause says about a step you are past.

**The label is exactly as racy as the base.** This is the failure orch#269
records: a session re-checked `mergeable` and the base before merging,
caught a stale base that way (`mergeStateStatus` read `CLEAN` while the PR
was 9 commits behind), and merged 60 seconds after `no-auto-land` was set —
because it treated the label as wake-entry state and only CI and the base as
merge-instant state. Two of three preconditions verified is a merge through
a live hold.

Re-reading is cheap and the window is small but it is exactly when it
matters: an operator sets `no-auto-land` *while* a session runs, because
that is when a PR is visible and green.

**An empty rollup right after your own push is a lag, not a no-CI repo.**
`statusCheckRollup` can read empty for a minute or so after a push while
the run for the new head is still registering. An empty rollup is green
(see the operator verdict below), so merging in that window merges an
unverified head on a repo that does have CI. Before treating empty as
green, confirm no run is pending for your exact head:

```bash
gh run list --repo <owner/name> --branch issue-<n> --limit 10 \
  --json headSha,status,conclusion
```

A run whose `headSha` is your head and whose `status` is not `completed`
→ not green yet; wait. No run for your head at all, after the rollup has
settled → this repo really has no CI for this branch, and empty is green.

A hold that appears in this read → take the hold path below. It is not a
late arrival to argue with; it is the current state, and it is the one that
governs.

One exception, and it runs the other way: if you post a review whose block
carries a `fix-before-merge` item (below), your own comment supersedes this
read. `core.merge_pr` re-derives the hold from the PR's latest
`orch:review:v1` block on every call, so the comment you just posted governs
the very merge attempt that follows it — do not merge on the strength of an
`auto-land` label when your own review just posted a blocking finding.

**CI presence is NOT a condition on the merge (operator verdict,
2026-09-14).** Do not gate the merge on `pr_verified`. A repo with no CI
emits an empty rollup, which is green, and that merges — `auto-land` is a
checkmark the operator controls, and adding conditions to it is exactly what
this verdict forbids. This deliberately reverses the gate `#119` introduced
in `665255c`; it is not a regression, and do not reinstate it.

**The review is the gate, and it is the only gate.** With no CI, the review
is not a second opinion alongside a check suite — it is the whole
verification step. That is a reason to keep the review mandatory under
`auto-land`, never a reason to skip it because nothing else ran. The order is
fixed: review runs, then `core.merge_pr` re-derives the hold from the posted
block and a blocking finding does not merge.

`pr_green` is unchanged and still gates. "No CI" and "CI ran and failed" stay
different questions, and only the first merges: a PR with a FAILING check
does not merge, on any repo, whatever `auto-land` says.

`core.pr_verified` still exists and still answers "did anything actually
run" — it is a meaningful, distinct question that `#127` built deliberately.
It simply does not gate this merge. Nothing in the code calls it.

**Clean review AND merge decided AND green → merge.** "Merge decided" and
"green" both mean *as of the three-precondition re-read above*, not as of
wake entry:

```bash
~/orch/spawn.py merge <slug> <n> <pr> --by issue-orch --review clean
```

Use `--review restored-hold` in place of `clean` when the issue journal
shows a prior blocking finding and a later re-review posted a clean
`orch:review:v1` block that superseded it — same merge, but the journal
should say the hold was lifted by a fresh clean review, not that the review
ran clean start to finish. The verb
wraps the same three gates you just re-read and re-derives them itself, then
writes the `landed` row to the repo journal at the merge instant, so this
landing cannot be forgotten by a session that merges and exits before the
next journal line. Do not fall back to `gh pr merge` — it merges the PR but
leaves no record.

Note the strategy: the verb merges with `--merge --delete-branch`, the same
argv the dashboard's merge button has always used and the one this repo's
own convention specifies. The line here previously read `--squash`, which no
code path implemented.

Journal the landing as usual (see LANDED, below).

**Blocking finding → post the record. That IS the hold (orch#408, orch#148
option B).**

`core.merge_pr` derives the hold itself, fresh, on every merge attempt: if
the decider is `issue-orch`, it calls `core.review_blocks_merge(repo, pr)`,
which reads the PR's LAST comment carrying an `orch:review:v1` block and
blocks the merge iff that block has at least one `fix-before-merge` item.
Posting the review comment with its blocking item is therefore the entire
act. **Write no labels.** There is no pair-write, no `no-auto-land` add, no
`auto-land` remove — those labels are not what holds this merge.

**Post the record.** Nothing changes about how the comment goes up — it is
still the reviewer's `orch:review:v1` block, verbatim, posted per Step 1
above:

```bash
gh pr comment <pr> --repo <owner/name> --body-file -
```

It must exist so a successor (and the merge gate itself) can read why the
PR is held. On a Gitea-backed repo the record rides the tea mirror, because
tea has no stdin option here (`core.TEA_ARGV["issue_comment"]`):

```bash
tea comments add <n> --login <login> --repo <owner/name> -d "<body>"
```

**Why there is nothing to clear.** The old mechanism this replaces was a
label pair — add `no-auto-land`, then remove `auto-land` — whose failure
mode was a half-written hold: an add that failed left the remove free to
strip the only thing holding the state, or a successor had to remember an
un-write existed at all. The derived hold has no such failure mode, because
there is no state to half-write. `core.merge_pr` re-reads the PR's latest
`orch:review:v1` block at the moment of every merge call; the hold is
recomputed, never stored, so it cannot be left behind and no one can forget
to remove it.

**How a hold is LIFTED.** A re-review that posts a NEW `orch:review:v1`
block with no `fix-before-merge` item. `review_blocks_merge` reads only the
LAST such block on the PR, so a fresh clean review supersedes the old one
by simply being posted after it — no un-write, no operator action to clear
a flag. This is a genuine improvement over the label pair: lifting a hold
used to be the operator's act (re-adding `auto-land`); now a clean re-review
lifts it on its own.

There is no new state and no new label. The work state stays `REVIEW`;
`agent-working` and `agent-stuck` are untouched — `agent-stuck` is the
write-off case and a held review is not that. Re-entry on this issue stays
automatic and correct; it simply does not merge — the issue is a live
candidate, re-examined every tick, that `core.merge_pr` refuses to land
until the record it reads back says otherwise.

`auto-land` / `no-auto-land` still exist and still work exactly as
described above — per-issue overrides of the repo default, read at Step 1
to decide whether to review at all and re-read at the merge instant. What
is gone is their use AS A HOLD for a blocking review finding. Adding
`auto-land` yourself remains forbidden.

Then take the hold path below — a posted blocking finding IS a hold.

**Hold decided** (by a blocking finding's derived review hold, by
`no-auto-land`, or by a false or absent default — NOT by the absence of CI)
→ do not merge, ever — and do not pause waiting for anyone:
leave the PR open at REVIEW, journal that you are holding and what for
(`orch/issue-orch waiting`), and exit; condition 5 keeps the handoff
visible every tick. Nothing you do here lifts a `no-auto-land` or absent-
default hold — that is the operator's gate. A blocking-finding hold is the
one exception: it lifts on a fresh clean re-review, not on an operator
write (see above). Pushing commits that fix the finding does NOT lift it
by itself — `review_blocks_merge` re-reads the PR's last `orch:review:v1`
block, and the block is never compared against the head sha (no staleness
check exists; see the `# ponytail:` note in `core.review_blocks_merge`).
A fix must be followed by a NEW review that posts a new block with no
`fix-before-merge` item. Those new commits are themselves the new event:
Step 1's fire-once check compares the head commit's timestamp against the
review comment's, so a pushed fix makes it re-review rather than skip.
That comparison is what keeps this hold from being a dead end — without
it the skip would win every tick and a PR whose defect was already fixed
would stay held until a human intervened. This is
decided policy, not judgment (`DESIGN.md` "Normal issue"): held REVIEW is a
standing condition that clears only by a human act or a clean re-review,
never by issue-orch deciding to proceed.

When the hold came from a blocking finding, journal it as `orch/issue-orch
waiting` — held, review posted a blocking finding, naming it — then
exit. Do not journal it again on a later tick; recognise the state and stay
quiet, the same discipline as the wait-once rule below.

**REVIEW does not mean CI passed.** No CI, or a broken config emitting an
empty rollup, reads green — signed off in `DESIGN.md`, because `pr_green`
feeds the state display and REVIEW has to stay reachable. REVIEW is where a
PR waits, not evidence that anything checked it. On such a repo the review
you ran above is the verification, which is why it is mandatory and why its
outcome — not the rollup — decides the merge.

**Journal the wait once, then be quiet — this covers the review too.** Held
REVIEW re-fires condition 5 every tick, so you may be re-entered over an
unchanged held PR, reviewed or not. Read the issue journal before you
write:

- **No wait recorded yet** → journal `orch/issue-orch waiting` — `PR #N open
  and green, waiting for a human to merge` — and exit.
- **The journal already shows that same wait, and nothing has changed** →
  recognise the state, write nothing, spawn nothing, exit.

The same discipline is why the review's own fire-once check is a PR-comment
read, not a journal entry to maintain: one held PR, reviewed once, waited on
repeatedly, produces one review comment and one recurring "still waiting"
recognition — never a new journal line per tick for either.

Quiet, not invisible: the dashboard re-derives the held PR every tick and
keeps showing it, so the hold stays visible to the operator without the
issue thread filling with near-identical waiting entries.

A *change* is worth a new line — CI went red, a human commented, the PR was
updated. "Still waiting" is not.

The journal already carries `orch/issue-orch merge-blocked` → the merge
is repo-orch's now; do not merge, do not re-escalate. If repo-orch
re-briefed you to rebase onto the new base, do that and re-push
`--force-with-lease`.

## Merge refused — cross-issue conflict

Another issue landed first and made your PR unmergeable; only repo-orch
sees across issues. Exactly two moves:

1. One rebase, yours: rebase `issue-<n>` onto base, re-push with
   `--force-with-lease`. Your git, your verb.
2. Still conflicted → journal `orch/issue-orch merge-blocked` naming
   what conflicted, and exit. **Never fight past one rebase** — repo-orch
   sequences the merge order and re-briefs you if needed.

**Never raise `merge-blocked` on a PR you are holding on a blocking review
finding.** `core.merge_pr` exempts `decided_by="merge-blocked"` from the
review gate, because that verb exists to break a deadlock and gating it
would refuse the one situation it is for. So the escalation is also the
one path that merges past a `fix-before-merge` item — repo-orch sequences
merge ORDER and cannot see the review, so it will not know to stop. Two
holds on one PR are not one problem: resolve the review hold first (post
the fix, re-review, get a clean block), and only then escalate a conflict
that survives. If both are genuinely live, say so in the journal line
rather than leaving repo-orch to merge a PR with a known defect.

## LANDED

Close out: journal the landing, confirm the PR merged and the branch
deleted, drop `agent-working`:

```bash
gh issue edit <n> --repo <owner/name> --remove-label agent-working
```

Drop it here, when the PR merges — not later. The issue stops counting
against the in-flight cap and stops rendering as in-flight the moment
this runs; left on, a landed issue silently eats a slot that ready work
could have used, and the symptom looks like the cap working normally
rather than a leak.

**Do not close the issue on LANDED.** A merged PR is not a finished plan:
LANDED is derived from the PR alone, and the PR cannot tell you whether
every unit you planned is actually in it. Only your plan and journal know
that. Closing the issue happens when the plan is complete, and that is
issue-orch's own closing act, not this skill's. Dropping `agent-working`
and closing the issue are different acts: this section does the first,
never the second.
