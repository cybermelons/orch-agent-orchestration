---
name: repo-management
description: "repo-orch's per-wake pipeline: widen triage to the unlabelled backlog, gate, order, nominate+spawn, record the standing order. Read on every wake after the consolidate stage."
---

# Repo management — widen, gate, order, start, record

## Inputs, by absolute path

`<checkout>` is the repo's checkout path, from the feed row's `path`. Your
cwd is `wt/<slug>/`, not this checkout, so none of these load natively —
read every one with the Read tool, by absolute path.

- `<checkout>/CLAUDE.md`
- `<checkout>/AGENTS.md`
- `<checkout>/CONTRIBUTING.md`
- `<checkout>/docs/ARCHITECTURE-MAP.md`, if present
- This repo's own entry in `ORCH_HOME/orch.json` (orch#297)

Extract only what a brief needs: a stated priority order that overrides the
defaults below, conventions a brief must restate (the test/gate command,
pnpm-not-npm or its equivalent, `tmp_*` one-time-script naming, branch
rules), any "do not automate" zone, and the architecture map's file/module
names so a brief points at the right place.

**The cat/grep refusal is real, not a permissions bug.** Bash in this
sandbox is cwd-only; `cat`/`grep` against a path outside `wt/<slug>/` is
REFUSED, and that refusal is not evidence the file is unreadable — it means
you used the wrong tool. The Read tool reaches absolute paths and is what
you have been granted for exactly this. If Read itself comes back refused
or errors on one of these paths, escalate it once, naming the path and the
exact error, and proceed on the issue text alone for this wake. Do not
re-escalate the same missing file on a later wake.

## Load the standing order

Read the **full journal file**, `state/repos/<slug>/orch.jsonl`, by absolute
path — not the tail your brief was handed. `compose_brief` (`core.py`,
`compose_brief`, `n=20`) hands you the last 20 rows unfiltered, including
tick `observed` rows, so a standing order written even a few wakes ago has
already scrolled out of it. Relying on the tail means re-deciding work
someone already parked, or re-parking work someone already lifted.

Find the newest `deferred` row whose note contains `STANDING ORDER`
("newest" by string-max on `at`, the same rule `consolidated_coverage`
uses). That row is your starting point:

- For each issue it lists as PARKED, re-check **only its named lift
  condition** — mechanically: did the named PR merge, is the named label
  now present, was the named comment answered. Do not re-judge a parked
  issue from scratch; that repeats work the standing order already did.
- Judge fresh only issues **created after that row's `at`**, or updated
  since — a new comment, a new commit, a label change.

No `STANDING ORDER` row found (first wake on this repo, or none ever
changed) → there is nothing to load; triage the full set fresh.

## Triage — widened to the whole open backlog

This is the fix for the #239 defect: triage that only ever looks at
issues already carrying `agent-ready` never reaches the issues that need
the label created, so the backlog past the label never gets seen.

Your input is the full open issue set (already loaded by `World.load`),
minus:

- CLAIMED issues (`agent-working`) — someone already owns these.
- ABANDONED issues (`agent-stuck`) — **never re-nominate over this
  label**, per the red line below.
- Anything carrying `owner_starting` — a spawn is already mid-flight for
  it.
- Anything you filed this wake — you cannot triage your own new filing in
  the same pass; it waits for the next wake.

What remains — issues that are UNCLAIMED **and unlabelled**, plus
UNCLAIMED issues already carrying `agent-ready` — goes through the gate
below.

**Operator-nominated issues skip part of the gate.** An issue a human
already labelled `agent-ready` has had G1 and G3 decided by a person; run
G2 only. Everything else runs all three.

## The gate — three questions, all must pass

**G1 — Decided.** The issue body says what to build; no open question
still changes *what* gets built. Fails when: the title is question-shaped,
an "Open questions" or "operator decision" section is unanswered, the last
human comment is an unanswered question, or the "Done when" criterion names
a decision still to be made rather than a code change to verify.

**G2 — Clearable.** Every precondition is liftable by a session doing
work. Fails when the issue needs an artifact, credential, or value nobody
has delivered; needs an operator act; or depends on a parked issue.

**G3 — Briefable.** You can fill, right now: (i) the change, in one
sentence; (ii) the file(s) or component it touches; (iii) a done-criterion
issue-orch can check itself. Fails when any of the three is missing.

Pass all three → **queued**, into the ordering step below.

Fail any → **parked**. Park by naming the specific failing question and
its lift condition — never park silently; a parked issue with no named
lift condition cannot be re-checked mechanically next wake and gets
re-judged from scratch, which is the cost this whole section exists to
avoid.

## Order among queued issues

An explicit order on the issue itself wins over everything below (orch#256).
Two labels carry it, and both are read straight off the feed's `labels` list:

- **`blocked-by:<n>`** — a hard edge. This issue does not start before `<n>`
  lands, whatever its tier and whatever the considerations below say. orch
  neither creates nor validates these labels, so an edge can be stale:
  before you hold work for one, check that `<n>` is actually still open. An
  edge you cannot resolve is ABSENT, not a block — a stale edge that
  silently parks a ready issue is worse than no edge at all. Say in the
  journal that you checked.
- **`p0` / `p1` / `p2`** — coarse tiers, most urgent first. Ties inside a
  tier are undefined by design; the considerations below break them.

**Do not confuse these labels with the `P0`–`P5` considerations below.** The
lowercase `p0`/`p1`/`p2` are LABELS a human or a repo-orch wrote on an issue
to record an order. The uppercase `P0`–`P5` (and `P1B`) are the named steps
of THIS ladder, which is how you order issues that carry no label. They are
different things that happen to share letters.

Where no label decides it, the ordering is not a formula to compute — it is
a sequence of considerations, in this order, that narrows the set:

- **P0 — continuity before novelty.** Re-entries (a CLAIMED issue whose
  issue-orch died) first, then operator-nominated `agent-ready` issues,
  then issues you are nominating yourself this wake.
- **P1 — breakage of what runs now**, laddered: corrupts or loses records
  outranks silent wrongness, which outranks a loud failure, which outranks
  a cosmetic issue. A defect in the system's own observability (its logs,
  its journal, its dashboard) outranks a defect of the same shape in its
  output — you cannot fix what you cannot see. When several observability
  defects tie here — and they will, because a system that cannot see
  itself tends to fail that way in more than one place at once — run the
  same corrupts/silent/loud/cosmetic ladder *again* among them, and break
  a remaining tie by what the defect hides: one that conceals a decision
  point (a review finding, an operator pass) outranks one that merely
  makes a state harder to read, because the first loses an act and the
  second costs a look.
- **P1B — an explicit priority label.** Named as its own step, not folded
  into P3: a human attaching a priority label is a deliberate act, stronger
  evidence than the inferred signals below it (a comment could be idle, a
  reaction could be noise; a label was chosen). It outranks P2 fan-out and
  P3-P5, but not P0 continuity — a session already in flight, or already
  re-entering, still goes first. Example: orch#250 (deploy pipeline dead)
  and orch#230 both carry `priority`; ranking #250 highly was correct here.

  **Which labels carry priority is not this step's to define.** This step
  only fixes *where* a deliberate priority signal enters the ladder; the
  label vocabulary above it is authoritative, and orch#256 settled it: the
  `p0`/`p1`/`p2` tiers rank here, and a `blocked-by:<n>` edge is not a rung
  at all — it outranks this whole ladder, per the top of this section. The
  bare `priority` label predates those tiers and still appears on older
  issues; treat it as "some deliberate signal, tier unstated" and let the
  steps below break the tie, rather than guessing a tier for it.

  Expect this step to sit vacant much of the time, and do not read that as
  a missed label: the operator tends to attach priority to work already
  running, so on 2026-09-15 all five labelled issues were in flight and it
  decided nothing among the 42 that were not.
- **P2 — fan-out.** Root before leaves: rank a root issue by how many
  others depend on it. Do not nominate a leaf of a root that is still
  open — courier the dependency into the root's own brief instead of
  spawning the leaf separately. When you conclude a dependency here, RECORD
  it as `blocked-by:<root>` on the leaf: a dependency that lives only in
  this wake's reasoning has to be re-derived by every later session, which
  is the gap orch#256 closed.
- **P3 — operator signal** — anything a human flagged, commented on, or
  reacted to, short of an explicit priority label (that is P1B).
- **P4 — smallness and certainty** — prefer the issue you are more sure
  of and can finish smaller.
- **P5 — age** — oldest first. Weakest signal; only a tiebreaker.

Two considerations narrow the set further, before scheduling:

**Consolidation — one piece of work wearing two issue numbers.** P2
fan-out covers dependency (root before leaves) but not two issues being
the *same* work under different names. Before ordering, check whether a
queued issue names the same file, config key, or design-doc section as
another open issue — a shared *artifact name* is the reliable tell,
because it is mechanically checkable, unlike "same topic," which is not.
If two collide this way, order them adjacently or fold them into one
brief, and the journal says which was done and why. **Fold** when one
brief would do both jobs — same artifact, same cause, and nothing in
either issue the other does not cover; supersession counts, where a newer
issue indexes the same ground as an older one. **Order adjacently**, and
do not fold, when they share a symptom but name different causes: two
issues can read identically and still need separate fixes, and folding
them loses the second. Same topic is not same work; the fold bar is one
brief, not one subject.

A fold verdict is the one conclusion that acts on the issue itself: close
the superseded issue with a comment naming the survivor and the shared
artifact that triggered the fold, then journal the same `consolidated` row
with the direction — `--folds <superseded>:<survivor>` alongside the
existing `--covered`. Record the direction, not just the fact of a fold:
a later wake that cannot tell which issue survived cannot tell a fold
from a mistake. The exact command spellings (gh vs tea, comment-then-close
ordering) live in `agents/repo-orch.md`, not here — this file is for the
judgment of which verdict applies, and a second copy of a command spelling
is a second copy that drifts.

**Fold is rare; order adjacently is the common answer.** A close fires
once and cannot be un-fired by anyone who did not read the journal, so it
must clear a high bar before it runs. The night of 2026-09-16 measured
this directly: roughly 60 consolidate passes produced about a dozen
recorded relationships, and not one was a fold — every one was order
adjacently. The trap case from that night was orch#283 and orch#284, both
editing `widget.tpl.html`, filed minutes apart by the same operator: every
cheap signal — same file, same author, same minute — says fold. It is
not one. #283 adds tooltips to 12 existing buttons; #284 adds three
controls that do not exist yet. Shared artifact, different causes: the
right call was order adjacently, not fold. (orch#277 and orch#280, the
same night, were cause-and-finding — again related, again not duplicates.)
Whenever the cheap signals line up this cleanly, that is exactly when to
re-apply the actual test: **the fold bar is one brief, not one subject.**

This happened for real on 2026-09-15: orch#230
proposed a JSON config format in ignorance
of orch#212, which had already named `orch.json` as the target in
`UX-REDESIGN.md` section 6 — and the format actually implemented turned
out to be a third thing, `.orch.toml`. Three answers to one question,
filed hours apart, none aware of the others; the fallout fanned into
orch#244 and orch#250. Catching the shared artifact name before starting
either #230 or #212 would have caught this at triage instead of at
cleanup.

The same night produced a second instance, caught in time: orch#252 (this
strategy) and orch#256 (ordinal priority labels) were both in flight and
both editing *this file*, one adding a priority step to the ladder while
the other replaced what a priority label means. Neither body mentioned the
other. The shared artifact name — this path — is what surfaced it, which is
the cue working as described. Note what the collision rule below cannot do:
both were already started, so there was no later one to skip. Once two
issues are concurrent, the tell has to be used at **landing**, by whoever
merges second reading the other's diff first.

That is what happened, and it is the cheap outcome: #256 merged first, #252
rebased onto it, and the only cost was one rebase and a few lines of
rewording. The expensive version is #230/#212 above, where nobody looked
until three incompatible answers had shipped. Neither issue was wrong to
exist — they were genuinely different work that happened to share a file.
Consolidation does not mean one of them should not have been started; it
means whoever lands second reads the other's diff before merging.

**Blocked-by is deferred, never started.** G2 already gates issues whose
precondition cannot be lifted by a session, but say the ordering
consequence explicitly: an issue whose blocker is still open is
`deferred`, carrying that blocker as its named lift condition — never
`started`, no matter how high P0-P5 would otherwise rank it. Rank does
not override a blocker. Worked example: orch#237's remainder needs the
operator to say whether two artifact URLs are the same artifact, a newer
version, or two artifacts — nobody else holds that answer, so no session
can lift it however small the eventual edit is. A standing order that
cannot express "deferred, lifts when the operator reconciles the two
URLs" either loses it silently or re-judges it from scratch every wake.

Note what this rung does *not* cover. orch#229 is merged work (commit
a476fa3) still one manual `git apply` from taking effect — but it was
**closed**, and a closed issue never enters triage at all, so no rung
here can hold it and no lift condition re-surfaces it. Deferring is how
you retain an *open* issue you are not starting; it is not a remedy for
work closed while an operator act is still outstanding. That is a
separate defect, and the only thing that catches it is not closing such
an issue in the first place.

Then apply one scheduling constraint, which is not a rank: **do not start
two issues that touch the same file or component concurrently.** When two
queued issues collide this way, skip the later one and park it: `deferred:
collides with #N on <file>; lift = #N lands`.

## Start loop

Spawn **one issue at a time**, not as a batch — label, spawn, journal,
then move to the next:

```bash
gh issue edit <n> --repo <owner/name> --add-label agent-ready
```

```bash
echo "<reason>" | ~/orch/spawn.py issue-orch <slug> <n>
```

```bash
~/orch/spawn.py journal repo <slug> started "<why>"
```

Use the identical strings above — these are the fences `repo-orch.md`
already carries, and only fences in that file are harvested into the
permission envelope; a fence written only here grants nothing.

The `in_flight_cap` is a **ceiling, not a target**: stop the loop the
moment the ordered queue is empty, even with slots still free, and say so
in the journal rather than padding the wake with marginal nominations.

## Record — one row per wake, only when something changed

Write a single `deferred` journal row carrying the standing order, and
only when the standing order actually changed this wake:

```bash
~/orch/spawn.py journal repo <slug> deferred "STANDING ORDER as of <at>. IN FLIGHT: <issues and what they're doing>. QUEUED (start when a slot frees): <ordered list>. PARKED: <n> — G<k>, lift = <condition>; <n> — G<k>, lift = <condition>; ... UNCHANGED since <prev at> except: <what changed>."
```

"Changed" means: a new issue was judged (queued or parked), a named lift
condition was observed lifted, an issue closed or got claimed, or the
order itself changed. Nothing in any of those categories moved → write
nothing this wake.

The `consolidated --covered` row from the consolidate stage is separate
and stays unchanged by this — it still carries every issue this pass
considered, whether or not the standing order moved.

## Blocked versus hard

The test is **who can lift it**, never how big the work looks.

- **Blocked**: the lifter is not a session. An operator decision that
  changes *what* to build, an operator act, another PR that must merge
  first, an external system, a value the level cannot read itself.
- **Hard**: a large diff, many files, an unfamiliar subsystem, an unclear
  root cause, "needs investigation." None of these is a reason to park —
  investigation is work a session can do. Queue it.

Two sharpening rules:

1. A choice that affects only *how* something is built, not *what*, is
   not a blocker. Courier it instead: "decide X within the repo's own
   conventions and journal the choice" — issue-orch resolves it, not you.
2. Before you conclude "needs information" and park on G2 or G3, read the
   repo's own docs and its architecture map. Most "missing" information is
   sitting in `CLAUDE.md`, `AGENTS.md`, or the architecture map you were
   told to read above.

**File one issue per decision, not per issue.** Only when the decision
gates the head of the order or the root of a cluster. Cluster every parked
issue that shares one lifting decision, and list every issue number in the
filed body — do not file the same decision twice under different issue
numbers. There is no escalation object and no retraction verb (`repo-orch.md`,
"Filing" — same discipline applies here): a filed issue is closed by a human,
not un-filed by you, and a decision already visible as a dashboard state
(a parked issue's own named lift condition, already carried in the standing
order) is not something to also file — file only the decision itself, once.

## Red lines

- **`agent-ready` only on an issue you are spawning this same wake.** A
  label with no spawn behind it in the same wake is a condition-1 spin you
  caused. (The ordering labels `p0` / `p1` / `p2` and `blocked-by:<n>` are
  also yours, per orch#256 — those record an order and need no spawn behind
  them.)
- **Never** write `agent-working` or `agent-stuck` — those belong to
  issue-orch exclusively — and **never** ADD `auto-land`. Adding it would
  authorize a merge, the one direction you must not move a gate.
  The single exception is the hold verb: you MAY write the `no-auto-land` /
  `auto-land` pair (add `no-auto-land`, then remove `auto-land`) to hold an
  issue you judge entangled with another (orch#254, orch#318; see
  `repo-orch.md`, "Your hold verb", which governs). Revoke only, and only
  on a relationship you can name in one sentence — name it in the row.
  Journal it BOTH ways: a `gh issue comment` on the issue you revoked,
  which is what the resumed issue-orch reads, and a repo row, which is what
  your own next wake reads.
- **Never** remove `agent-ready` from an issue you did not add it to.
- **Never** re-nominate over `agent-stuck` — that label means a human
  needs to look, not that the issue is available again.
- Parking is a journal row, never an act on the issue: never close,
  relabel, or comment a verdict onto a parked issue.
- Spawn `issue-orch` only. Never kill. Merge only on a journaled
  `merge-blocked`, per `repo-orch.md`.
- No formula lives in `agents/repo-orch.md` — the ordering strategy is
  this file, and stays this file.
