# Prioritize dry run — 2026-09-15

## 1. Purpose and how to use this

This is round 0 of the ordering strategy in `agents/skills/repo-management/SKILL.md`,
applied by hand against this repo's own live backlog. It is acceptance item 3
of orch#252: produce an order whose reasoning is legible enough to argue
with. It is not a claim of correctness — the point is that every placement
carries a reason someone can name and dispute.

A later round diffs against this file. Use it like this: pick a rank you
disagree with, find its reason below, and argue with the reason, not the
rank. Where the ladder itself didn't decide a case, that's logged in section
7, not smoothed over — those are the highest-value lines in this document
for round 1, because they're places the strategy needs sharpening, not
places I need to re-guess.

## 2. Method

Applied `agents/skills/repo-management/SKILL.md` as of commit `3023794`
("feat: name priority, blocked-by, and consolidation in the ordering
ladder"), sections: "The gate — three questions" (G1/G2/G3), "Order among
queued issues" (P0 continuity, P1 breakage laddered, P1.5 the `priority`
label, P2 fan-out, P3 operator signal, P4 smallness, P5 age), the
Consolidation paragraph, the "Blocked-by is deferred, never started"
paragraph, and the same-file/component scheduling constraint. Date: 2026-09-15.

**Terminology note, added at land.** This document is a snapshot of round 0
and keeps the names the ladder used when it ran. The rung called `P1.5`
throughout was renamed **`P1B`** before this PR merged, to avoid colliding
with the lowercase `p0`/`p1`/`p2` *labels* that orch#256 introduced — those
labels and these uppercase rungs are different things. Read `P1.5` below as
`P1B`. The reasoning is unaffected; only the name moved.

**What the ladder gained after this run.** orch#256 merged (PR #263) while
this PR was at Land, adding `p0`/`p1`/`p2` tier labels and `blocked-by:<n>`
edges that sit *above* the whole ladder. Two of this document's open
questions are answered by it rather than by any later dry run: section 7's
"P1.5 has no live discriminating power" (tiers now give it something to
read) and the standing complaint that dependencies lived only in a wake's
reasoning (they are a label now). Round 1 should re-run against the tier
labels rather than re-arguing those two.

Inputs: the 47-issue list handed down in the task (open issues minus the 5
carrying `agent-working`), plus `gh issue view` on 18 of them where a gate
or cluster call was genuinely unclear: #237, #212, #152, #154, #225, #234,
#241, #172, #140, #141, #184, #185, #187, #197, #204, #256, #227, #143 (18,
not the "8-12" guideline — the artifact-drift cluster alone needed 7 reads
to tell apart cleanly, and it was worth it: #234 and #225 looked like
duplicates from titles alone and are not).

## 3. In flight — excluded from ordering

These carry `agent-working` and are already owned. Not judged, not ordered.

- **orch#230** — PR #247, at REVIEW, carries `no-auto-land`; a reviewer is
  holding it. Also carries `priority`.
- **orch#250** — orch-pull dirty-tree guard; deploy stuck at `43f6f4a`, five
  merged PRs not yet reached. Carries `priority`.
- **orch#252** — this work (the doc you're reading).
- **orch#254** — repo-orch merge-gating; carries `priority`; #256 names it
  as unblocked by #252.
- **orch#256** — ordinal-priority label mechanism; carries `priority`.

Note on `priority`: all five issues currently carrying the `priority` label
are these five in-flight issues. P1.5 has zero discriminating power over the
42 issues actually being ordered below — see section 7.

## 4. Consolidation findings

This section comes before the order because the config-drift failure
(orch#230 vs orch#212 vs the shipped `.orch.toml`) is the whole reason
orch#252 exists, and the same failure shape is visible again, live, in this
backlog.

### Cluster A — artifact-vs-code drift: #152, #154, #225, #234, #241, #172

All six say some version of "a design doc/artifact and the shipped code
disagree." The shared artifact name is the tell, but they are not all the
*same* artifact claim, and folding them all into one brief would lose real
distinctions:

- **#154** is the tracking index for six numbered discrepancies (#150 and
  others) found by diffing `docs/UX-REDESIGN.md` §7 against the artboard at
  `claude.ai/code/artifact/32d8b13a-...`. It is an index issue, not a leaf.
- **#241** is a *second*, newer consolidated index over the same vendored
  artifact (`docs/artboards/orch_Operator_UX.pdf`), and explicitly names
  #209 and #211 as the concrete leaves for its page-3 findings. #241 reads
  as #154's role, rebuilt after the PDF got vendored — same job, different
  artifact source (claude.ai URL vs vendored PDF), same output shape (a
  table of divergences with issue numbers).
- **#152** is one specific naming divergence (WHAT CHANGED vs WHAT MOVED) —
  a leaf of the same comparison #154 and #241 both run.
- **#225** is UX-REDESIGN.md specifically being stale against *canvas*
  decisions made after the doc was written (automerge-dot-is-the-toggle,
  etc.) — same symptom (doc lags reality) but the authority is the canvas,
  not the artboard PDF, and the content don't overlap with #154/#241's
  items.
- **#234** is the broadest: doc-vs-code drift found while *operating* orch
  on 2026-09-15 (CLI/`/act` verb parity, a stale TODO count, `spawn.py
  worker` not existing, the repo-management skill not existing when the
  doc claimed it did). Only its item 4 overlaps the artboard cluster; the
  rest is unrelated operating-doc drift (DESIGN.md/README, not
  UX-REDESIGN.md).
- **#172** is a *different* six things: operator rulings from 2026-09-14
  that changed the design and were never back-filled into DESIGN.md/
  DECISIONS.md. Same shape (doc says X, reality is Y) but the object is
  operator rulings, not artifact-vs-widget diffs. Shares no artifact name
  with #154/#241.

**Verdict:** fold #154 and #241 — same job (consolidated artifact-drift
index), same output shape, #241 is the more current one since it targets
the vendored PDF that #154 predates. One brief: retire #154's index role
into #241, keep #152 as a named leaf under #241 (do not fold #152 itself —
it is a real, small, independently-closeable fix). Order #225, #234, #172
*adjacent* to #241 but do not fold them in — each has a distinct authority
document and folding would blur which doc a fix should target. This is
exactly the #230/#212 shape: multiple issues answering "does the doc match
reality," filed at different times, each partially right, and mechanically
distinguishable only by which artifact/doc each one names.

### Cluster B — widget/#117 removals: #140, #141

Both say "PR #117's widget rebuild removed a surface the spec didn't
authorize," both point at `widget.tpl.html`, both came out of the same PR
#133 review (items 5 and 6). This is a real fold: one brief, "audit and
restore/formally-cut the surfaces #117 silently dropped," covering both the
review/approve/reject/reply actions (#140) and the deploy commit/behind
readout (#141). Folding loses nothing — same file, same cause, same
reviewer, filed minutes apart.

### Cluster C — account budget: #184, #185

Not a fold. #184 is the incident report (bug: five sessions died, 48
minutes idle, no detection/backoff/resume). #185 is the systemic capability
gap it exposes (enhancement: no per-tick spend record, no dashboard figure,
no policy). #184 is P2 root only in the sense that its postmortem facts are
what #185's design should be built against — order adjacently, #184 first,
courier its findings into #185's brief rather than treat them as
independent forks of the same fix.

### Cluster D — silent session death: #197, #204

Looks like a fold on symptom (both: log has only the spawn banner, no
journal row, no error) but #204's own body argues against folding: it
explicitly rules out #197's OOM/tsgo theory (different repo, different gate
command, no Node type-checker involved, and the death happens at Land,
after the gate already passed). Two issues, same *symptom*, provably
different *cause* — order adjacent (both are P1 "own observability" defects
of the same shape) but do not fold. Folding here would be exactly the
mistake the skill's artifact-name test exists to prevent: same topic is not
the same work.

### Cluster E — the config-drift issues themselves: #212

#212 (repo-level automerge toggle) names `orch.json` per `UX-REDESIGN.md`
§6 as where the setting lives. The shipped format is `.orch.toml` — this is
the identical shared-artifact-name failure the skill's own worked example
describes for #230, playing out a second time in #212. #212 cannot be
briefed correctly without first correcting which config file it targets;
see section 7's open question on this — it isn't safely queueable as
written.

## 5. The order

In-flight cap: 5, already full (section 3). Nothing below can start until a
slot frees. Per the skill's "ceiling, not a target" rule, only as many as
the cap allows would actually launch on a real wake — read the top ~5 as
"next to start," everything past that as `deferred` lines that exist so the
next wake doesn't re-derive them, not as a promise they'll run soon.

Rungs used: P0 continuity, P1 breakage (laddered: corrupts/loses records >
silent wrongness > loud failure > cosmetic; own-observability defects
outrank same-shape output defects), P1.5 priority label, P2 fan-out, P3
operator signal, P4 smallness/certainty, P5 age.

1. **orch#143** — P1, own-observability defect, silent-wrongness class. A
   stopped repo looked identical to a busy one; the operator only found the
   credential fault (#142, already fixed) by reading a session log by hand.
   This is "you cannot fix what you cannot see" named directly in the
   skill's P1 ladder. Small, well-scoped (surface a fault state on the
   dashboard), no blockers.

2. **orch#134** — P1, own-observability defect, silent-wrongness class. A
   live issue-orch hides its own PR from awaiting/fix-before-merge findings
   — the operator loses visibility into a PR that needs attention, in the
   same "the system's own observability" language the skill calls out
   explicitly as outranking a same-shape output defect. [bug]

3. **orch#125** — P1, own-observability, silent-wrongness. A nudge leaves
   no journal row; an operator pass on a repo becomes invisible in history.
   Journal integrity is close to "loses records" — ranks just under #134
   because a missing journal row is recoverable from other state (the
   nudge itself still happened), whereas #134 actively conceals a PR that
   needs a decision. [bug]

4. **orch#146** — P1, silent-wrongness. `spawn.py --ref` is accepted on any
   journal event, not just `retracted` — this means a `--ref` typo or
   misuse silently produces a wrong-shaped journal row rather than
   erroring, corrupting the one data structure (the journal) everything
   else in this backlog depends on reading correctly. Small, mechanical
   fix (validate event type before accepting `--ref`). [bug]

5. **orch#204** — P1, own-observability, loud-failure class (session dies,
   but leaves zero trace beyond a spawn banner) with fan-out: it is in the
   *orch* repo itself, so a fix here also de-risks every other repo's Land
   step, and it's got 5 comments already showing active operator
   engagement (P3 signal). Ranked above #197 despite #197 having more
   named instances (4 vs 2), because #204's proven cause (something at
   Land, after a passing gate) is closer to root in orch's own codebase,
   while #197 is host/environment-specific (OOM on devhost's Node
   type-checker) and narrower in blast radius.

--- cap falls here (5 slots, already full of in-flight work) ---

6. **orch#197** — P1, own-observability, loud-failure. Deferred: same
   cluster as #204 (order adjacent, not folded — see 4D). Real root cause
   already named by a surviving session (OOM, exit 137) but needs a memory
   fix or gate change on devhost specifically.

7. **orch#256** — P1.5-adjacent but already in flight; listed here only as
   the reason P1.5 doesn't move anything else (see section 7). Not
   re-ordered; already accounted for in section 3.

8. **orch#241** — P2 fan-out root (post-fold with #154; see cluster A).
   Names #209 and #211 as concrete leaves — don't nominate those leaves
   separately while #241's index work is open; courier findings into it.
   Deferred: high fan-out, but not P1 breakage, so it sits behind the
   observability defects above.

9. **orch#140+141** (fold, cluster B) — P2/P3: fan-out is small (two known
   dead server actions, one dropped readout) but there's direct operator
   signal (both filed from a PR review, not self-discovered). Deferred as
   one unit.

10. **orch#212** — P2, but blocked on the config-drift open question
    (section 7) before it's briefable as written — G3 arguably fails until
    someone names the real config file. Listed here rather than parked
    because the fix (point it at `.orch.toml`) is small once decided; see
    section 7 for why this is borderline park material.

11. **orch#144** — P1-adjacent (documents root cause of orphan state:
    spawn-failed vs manual label vs ledger) but is itself a documentation
    task, not a code fix, so it ranks behind the code-level observability
    fixes above it. P3: operator-relevant to understanding #229-shaped
    failures.

12. **orch#184** — P1, loud-failure (5 sessions killed) but already fully
    diagnosed and not recurring today; cluster C root, order ahead of #185.

13. **orch#185** — P2, cluster C leaf/successor to #184; enhancement, not
    bug, and needs #184's postmortem as input.

14. **orch#187** — P4/P3: concrete measurement already done (42% of spend
    on two levels), operator-framed ask, but is a tuning/efficiency
    improvement, not a breakage — ranks behind anything P1.

15. **orch#198** — P2: removing escalations as an object simplifies a
    chunk of the ordering/journal machinery this skill itself depends on
    (an agent that gives up re-statuses the issue instead of filing an
    escalation row) — touches the same subsystem as #256/#254, worth
    sequencing near them but not fan-out-root the way #241 is.

16. **orch#219** — P1, cosmetic/loud-failure edge: caveman mode hook fired
    every turn and was ignored for a session — annoying, not
    data-threatening, self-contained.

17. **orch#227** — P1, but scoped to gita-lectures, not orch itself — real
    breakage (deploy hook inert) but blast radius is one other repo's
    deploy path, not orch's own. [bug]

18. **orch#159** — P3: operator already specified an ask and got it
    implemented wrong — direct operator signal, concrete, correctable.

19. **orch#164** — P3: dashboard-op only reasons cross-repo when asked;
    operator-relevant workflow gap, no data risk.

20. **orch#225** — P2/documentation, cluster A (adjacent to #241, not
    folded). Real: a worker building from UX-REDESIGN.md alone builds the
    wrong automerge control.

21. **orch#234** — P2/documentation, cluster A (adjacent). Broadest but
    least urgent single item in the cluster — mostly stale-count and CLI-
    parity notes, only one line (#4) is the artboard-shaped drift.

22. **orch#172** — P2/documentation, cluster A (adjacent, distinct
    authority — operator rulings, not the artboard). Three comments show
    engagement (P3).

23. **orch#152** — P4: small, well-scoped naming fix, leaf under #241 per
    cluster A. Kept as its own issue (see cluster A verdict) but ranks low
    because it is cosmetic-only (a header label), the bottom of the P1
    breakage ladder.

24. **orch#154** — superseded by #241 per cluster A fold; kept in the order
    only until the fold is actually executed (closing #154 into #241 is
    itself the action, not a code change — courier it, don't spawn it).

25. **orch#237** — remainder only (the vendoring already fixed the
    immediate blocker per its own body); what's left is reconciling two
    different artifact URLs, which is an operator act, not a code change —
    this is nearly a park (see section 6), kept in the queued order only
    because a session *could* draft the reconciliation question for the
    operator, which is briefable even though the decision itself isn't.

26. **orch#116** — P4: small, well-scoped (surface checkout filepath, not
    just slug), no dependency.

27. **orch#209** — P2 leaf of #241 (WHAT MOVED section blank, summary verb
    never landed) — don't nominate separately while #241 is open; courier.

28. **orch#211** — P2 leaf of #241 (journal view + operator input box) —
    same as #209, courier into #241's brief, don't spawn standalone.

29. **orch#186** — P4: cosmetic (no max-width, rows stretch on wide
    screens), small, no dependency, bottom of P1 ladder's cosmetic tier.

30. **orch#189** — P4: render live tail of a running session's log, well-
    scoped, no known blocker.

31. **orch#190** — P3/G2-adjacent: dashboard-op can't confirm health of two
    specific repos because of sandboxing — worth checking whether this is
    actually clearable by a session or needs an operator sandbox change
    before queuing further; tentatively queued, flagged in section 7.

32. **orch#228** — P4: cross-host claims unreadable (dead claim on another
    machine) — a real but narrow defect, self-contained fix.

33. **orch#231** — P3: architecture-map-in-same-PR convention; process
    improvement, no urgency, no dependency.

34. **orch#156** — P4/P5: per-piece model selection for a level — a real
    ask but speculative-adjacent (no incident cited); low urgency.

35. **orch#174** — P4: tick-every-60s with agentic wakes rate-limited
    separately — tuning, not breakage, and possibly superseded in part by
    #187's per-level cadence framing; check for overlap before briefing
    both.

36. **orch#223** — P5/G1-soft: "can the orch CLI serve as the gh/tea
    wrapper" reads as still-open-question shaped; queued rather than
    parked only because #234 already answers part of it (CLI/`/act` verb
    gap) — courier that fact into whoever picks this up.

37. **orch#222** — P5: retire openclaw orchestration in favor of orch +
    Remote Control — large, cross-system, no incident forcing it now; oldest
    kind of "someday" item that's nonetheless decided (G1 passes, it's just
    big).

38. **orch#28** — P5: "would orch work as an MCP server" — speculative
    framing in the title itself; see section 7, this may not pass G1.

39. **orch#92** — P5: mobile three-line rows/chips/wizard/repo page — large
    surface, no incident, age-appropriate low rank.

40. **orch#100** — P5: burndown/throughput chart — net-new feature, no
    dependency, no urgency signal.

41. **orch#101** — P5: repo turn graph — same shape as #100, net-new
    visualization, no urgency.

42. **orch#11** — P5: CRUD on the web control surface — old (2026-09-11),
    broad, no incident; oldest-dated issue in the batch, which under pure
    P5 would rank it higher, but P1-P4 on everything above it dominates —
    age is the weakest signal per the skill and only breaks ties.

43. **orch#13** — P5: web UI render controls from `/act` verb table — old,
    related to #11 (same "web control surface" area) but no shared artifact
    name found between them, so not consolidated, just adjacent in age and
    topic.

44. **orch#208** — P4/`no-auto-land`: dashboard activity visualization,
    flagged no-auto-land so even if started it won't merge itself — low
    urgency, no incident.

**Deferred, not started, per the cap (5 slots, all currently full):**
everything from rank 6 down. This list exists so the next wake reads it
instead of re-deriving it — it is not a promise of imminent start.

## 6. Parked

Every park below fails a named gate and names its lift condition. No park
has an unnamed lift condition.

- **orch#28** ("Would orch work as an MCP server?") — **G1 fails.** Title
  is question-shaped and the body (per its framing in the task list) reads
  as exploratory, not a decided build. **Lift:** an operator or a design
  pass (Fable-level) answers yes/no and states what "yes" builds; then G1
  clears and it re-enters triage.

- **orch#237**, decision-remainder only — **G2 fails.** The immediate
  blocker (artifact not fetchable) is already mitigated by the vendored
  PDF per the issue's own "Already mitigated" section; what's left is
  reconciling two different artifact URLs/ids, which only the operator who
  owns the claude.ai account can resolve. **Lift:** operator confirms
  whether the two URLs are the same artifact, a newer version, or two
  artifacts; once stated, remove from park (it becomes a one-line doc
  correction).

- **orch#212** — **G3 borderline-fails**, held out of a firm queue slot in
  section 5 for this reason: the issue as written briefs against
  `orch.json`, but the shipped format is `.orch.toml` (confirmed by
  reading the file directly during this dry run — see section 7). A
  session could still discover this itself mid-brief, so this is not a
  clean park; flagged instead as the sharpest open question in section 7.
  Not parking it outright because the fix is small enough that "session
  discovers config file is `.orch.toml` and adjusts" is plausibly within
  G2's "liftable by a session doing work" — but it's exactly the kind of
  case the skill's own consolidation paragraph warns about, so a triager
  should not queue it on autopilot.

## 7. Open questions for round 1

- **P1.5 has no live discriminating power in this backlog.** Every issue
  currently carrying `priority` (#230, #250, #252, #254, #256) is also
  in-flight. Among the 42 judged issues, zero carry `priority`. The rung
  exists in the ladder between P1 and P2 but did nothing in this pass —
  either that's expected (priority gets assigned to what's already
  running, by design, and P1.5 will bite on a *future* dry run once
  #256 ships ordinality) or the ladder should say explicitly that P1.5 is
  currently vacant and why, so a reader doesn't wonder if this dry run
  missed labels.

- **The ladder doesn't say how to rank "own observability" defects against
  each other once you have more than one.** P1 says observability defects
  outrank same-shape output defects, but #134, #125, #146, #143, #197, #204
  are *six* observability-shaped defects and the ladder gives no way to
  order among them beyond the corrupts/silent-wrong/loud-failure/cosmetic
  sub-ladder. I used "closer to data corruption" and "conceals a decision
  point" as tiebreakers between #134 and #125 — that's my judgment call,
  not something the skill states, and it's exactly the kind of place round
  1 should either confirm or overrule.

- **Consolidation vs adjacency has no bright line.** The skill says "order
  them adjacently or fold them into one brief" but doesn't say which. I
  used "same file + same cause + same filer + filed minutes apart" as the
  fold bar (#140/#141) and "same symptom, different named cause" as the
  don't-fold bar (#197/#204), and "same job, different source artifact,
  one supersedes the other" as a third case I had to invent (#154 into
  #241) that the skill doesn't describe at all — it only names "same
  artifact = fold" and doesn't cover "two indexes of similar-but-not-
  identical drift, one newer." Round 1 should decide whether supersession
  is its own category or a subtype of fold.

- **#212 is a live instance of the exact failure the skill's example
  describes, and the skill doesn't say what to do when you catch it mid-
  flight rather than after the fact.** The worked example (#230 vs #212 vs
  `.orch.toml`) is written in the past tense, as a lesson already learned.
  But #212 *itself*, right now, still says `orch.json` in its body. Does
  triage silently correct the artifact name in the brief (courier-style,
  under the "affects only how, not what" rule), or does it fail G1 because
  the issue's stated target is now factually wrong? I treated it as
  briefable-with-a-caveat; a stricter reading would park it on G1 until
  someone edits the issue body. This is the single most consequential
  ambiguity I hit, because it's the same shape as the failure that
  motivated #252 in the first place.

- **#144 and #172 are process/documentation issues that don't cleanly map
  to the P1 breakage ladder**, which is written for code defects
  ("corrupts records," "silent wrongness," "loud failure," "cosmetic").
  Where does a stale-doc issue sit on a ladder built for runtime behavior?
  I ranked them by treating "agents read this file on every spawn, so a
  false statement is load-bearing" as a form of silent wrongness, but the
  skill never says documentation counts as P1 material at all.

- **orch#190 (sandbox blocks dashboard-op from confirming two repos'
  health)** — I could not tell from the title alone whether the blocker is
  liftable by a session (a sandbox config change is a plausible session-
  doable task) or needs an operator to grant an access change (G2 fail). I
  queued it tentatively; a reader with more context on the sandbox model
  should confirm or move it to Parked.

- **#174 and #187 likely overlap** (both are wake-cadence/tick-rate
  changes) but I did not read #174's full body against #187's per-level
  proposal closely enough to call a fold with confidence — flagging rather
  than guessing, per the instruction that an honest "don't know" beats a
  confident guess.

- **The skill has no explicit tiebreaker for "old but small" vs "new but
  breakage."** #11 is the oldest issue in the batch (2026-09-11) and would
  win outright under P5-only reasoning, but every P1-P4 rung above it
  overrides age long before P5 is reached. That's consistent with "P5 —
  weakest signal, only a tiebreaker" as written, but it means an issue can
  sit for a long time as long as *something* newer keeps outranking it on
  an earlier rung — worth naming explicitly as an accepted tradeoff rather
  than a starvation bug, or round 1 should say if it's the latter.
