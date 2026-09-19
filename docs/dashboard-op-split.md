# The dashboard-op split: mechanical routing in the tick, coordination in the session

Status: design, for orch#48. Written before implementation, on the operator's
instruction, so the role is restructured deliberately and not redefined inline
inside a changeset.

Governs: `orch/tick.py`, `agents/dashboard-op.md`, and any later work that
changes who routes and who coordinates. Later work builds on this document
rather than re-deriving the split.

---

## 1. The claim, and the one number that does not prove it

dashboard-op is woken by the tick, evaluates a six-row condition-to-action
table, spawns `repo-orch <slug>` for each repo that needs one, and acks if it
spawned nothing. `agents/dashboard-op.md` opens with "You do not look at
issues or code."

The observed cost: one dashboard-op wake was recorded ~197 times against a
single frozen issue (gita-lectures#31), and journalled `Spawned nothing.`
every time. A Claude session per tick to evaluate a lookup table.

**That number does not, by itself, prove the role is mis-shaped, and this
document does not rest on it.** orch#96 is open at review right now (PR #105)
and it fixes precisely those wakes. The cause it found is not "the table is
too dumb for an agent" — it is that the defer the agent needed was
*unsatisfiable*. `agents/dashboard-op.md` licenses a defer once the chain has
already run "for this same digest", but `digest_of(build_thin(data))` in
`orch/tick.py` is global: it hashes every repo's every issue plus the
error-alert set. With several repos live, some repo always moves, so a repo
frozen for hours never sees a repeat digest. #96 measured 64 distinct global
digests over a repo whose own state changed 11 times. The agent could never
justify the defer, so it took the one defer its brief forbids instead.

So the honest accounting is:

| the ~197 wakes | cause | fixed by |
|---|---|---|
| the repeat wakes on one frozen repo | global digest made the defer unsatisfiable | #96 / PR #105 |
| one session spent per wake to read a 6-row table | the role's shape | this issue |

When #96 lands, the wake *count* collapses. **The argument here must rest on
the residual cost, and it does:** even a correct, well-acked dashboard-op
spends one Claude session to evaluate a predicate over facts `orch/tick.py`
has already computed, in the same process, one function earlier. That cost is
per-wake and it does not go to zero when the loop is fixed. It is the cost of
asking a language model to do bookkeeping.

The second residual is correctness, not cost. The conditional ack — the lease,
`ACK_TTL_MINS`, the two failure directions, "a mangled or paraphrased digest
counts as no ack" — exists only to stop the wake loop that the routing itself
creates. It places a bookkeeping step on a language model and then spends
design effort bounding how badly that step can fail. `wake_gate()` already
holds the real logic.

---

## 2. What moves into the tick

**Only the condition-to-action table**, and only its mechanical rows.

The tick already computes every fact those rows read. `compute_conditions()`
returns the conditions; `build_thin()` supplies `state`, `work_state`,
`orch_alive`, `idle_over`, `contended`, `startable`. Nothing new must be
gathered. The move is: evaluate the predicate where the facts already are,
and call `core.spawn("repo-orch", (slug,), reason)` directly.

| # | condition | action | mechanical? |
|---|---|---|---|
| 1 | ready or abandoned | spawn repo-orch | yes |
| 2 | blocked | alive → skip; dead → spawn | yes — `orch_alive` is a fact |
| 3 | contended | **never spawn** | yes — a constant |
| 4 | oracle failure | **never spawn**, never ack | yes — a constant |
| 5 | unowned work | spawn repo-orch | yes |
| 6 | wedged | lease holds → skip; expired → escalate, defer | **no — see section 3** |
| 7 | landed but still claimed | spawn repo-orch | yes |

Conditions 3 and 4 stay as they are, with their reasoning carried into the
tick as comments, not dropped. They are not spawn-suppression details; they
are the two cases where spawning is actively wrong. Condition 3: repo-orch
holds no kill and no labels, so it cannot clear contention. Condition 4: the
facts a repo-orch would reason over did not load, so anything it concluded
would be built on an unread world. `orch/tick.py` already carries do-not-
simplify comments of exactly this kind (conditions 1, 5, 7); these join them.

**The ack becomes the tick's own record.** The tick spawned, so the tick
records that it spawned, per repo, keyed to that repo's per-repo digest from
#96. No agent is asked to echo a hash back verbatim. The lease semantics
survive — a standing human-owed condition must still go quiet without going
invisible — but the bookkeeping stops being an agent's mandated final act.
`ACK_TTL_MINS` keeps its meaning: a suppression that expires, so a wrong
suppression is bounded rather than self-sealing.

### The caution the operator raised: the tick is the origin and must stay cheap

Every wake in this system descends from the tick, so work added there is paid
on every tick forever. This split does **not** add work to the tick. It moves
an evaluation onto facts the tick has already built, in memory, in the same
function call chain. No new `gh` read, no new file scan, no new subprocess.
The one genuinely new read is the journal tail for the agentic handoff of
section 3 — and that read happens only on the path that hands off, not on
every tick.

**Nothing here changes what any level is ALLOWED to do.** `DENY_BY_ROLE` in
`orch/core.py` is untouched. dashboard-op still holds no labels, no merge, no
kill. The red lines are separate from the shape, and only the shape moves.

---

## 3. What stays agentic, and the seam between the two

**The unproductive-repo defer stays, and it stays a judgment.** The operator
decided this on 2026-09-14 and the reasoning is load-bearing: "defer a repo
whose recent repo-orchs accomplished nothing" reads a journal tail and
concludes whether anything actually moved. A mechanical version ("N
consecutive wakes with no `started` row") would defer the repo whose last
repo-orch began long-running work — which is precisely the repo that should
be left alone. By row count the two are identical. Only reading the tail
tells them apart.

Condition 6, the wedged case, is the same shape. Judging whether a live
session with an expired lease is wedged or merely slow is a judgment about a
journal, not a predicate over a field.

**So the mechanical path must be able to hand off to the agentic one.** This
is the seam, and it is designed, not incidental:

- The tick evaluates the table. For the common path it spawns and records.
- When a repo's per-repo digest has not moved across a spawn the tick already
  made — the repo was fed and nothing changed — the tick does **not** keep
  spawning into it. It has reached the limit of what a predicate can settle:
  "fed, unchanged" is ambiguous between long-running work and a repo spinning
  in place, and that ambiguity is exactly what #96 showed the agent must
  resolve by reading the tail.
- That is the handoff. The tick wakes dashboard-op **for that specific
  question**, naming the repo and why the mechanical path could not dispose
  of it.

This is the inverse of today. Today dashboard-op is woken for everything and
the interesting case is buried among the routine ones. After the split it is
woken only when a session is the right instrument.

Note the seam depends on #96's per-repo digest. Without it the tick cannot
tell "this repo was fed and did not move" from "some other repo moved", which
is the same defect #96 found. **This issue must not land before #96.**

---

## 4. What the session is FOR

This is the section that decides whether the level survives, and it is written
in that knowledge.

`docs/` precedent: repo-orch's own document opens by saying repo-orch is NOT a
router, because a thin "which issues have owners" description is what once got
that level cut. The same risk applies here, upward. **If mechanical routing
moves into the tick and nothing explicit is named in its place, dashboard-op
becomes a level with nothing to do, and it gets cut for the same reason.** So
what remains is named concretely.

A dashboard-op session exists to **answer a question that spans repos, using
its children**. Three things it does that no other level can:

**a. Judge progress across a repo's history.** Section 3's defer, and
condition 6's wedged judgment. Both read a journal and conclude whether
anything moved. Neither is a predicate.

**b. Decompose an operator question across repos and dispatch children for
it.** The concrete test the operator asked for — a question dashboard-op can
answer that **no single repo-orch could**:

> "Three repos have had an issue sitting in REVIEW for over a day. Is that
> one cause or three?"

A repo-orch sees one repo. It can report that its own issue is in REVIEW
awaiting a human merge because `auto-land` is absent. It cannot see that the
other two are also in REVIEW, nor that one of them is red on CI for an
unrelated reason. Only dashboard-op holds the cross-repo view *and* the verb
to send a child into each repo to find out why. The answer — "two are the
same missing-`auto-land` case, the third is a real CI break" — is a
conclusion no child could reach and the tick could never compute, because it
requires reading three journals and judging what they have in common.

Other questions of the same shape: "which repo should get attention first,
and why"; "has this class of failure appeared in more than one repo this
week"; "is this contention the same root cause as the one yesterday".

**c. Escalate what it concludes.** Per the operator's 2026-09-14 verdict,
escalation delivery is settled and must not be redesigned here: #93 landed,
escalations surface in the dashboard's NEEDS YOU zone, and a control files one
as an issue. dashboard-op journals the escalation; that surface carries it.

### The execution model does not change

**dashboard-op fires and exits. It never waits on a child.** The operator
settled this on 2026-09-13 and it is restated here because section 4b makes
the temptation obvious: "dispatch children, read results, answer" reads like a
join. It is not one.

Spawn-and-exit is the model at every level. Do not add blocking, polling, or a
join on a child session. "Coordinate its children to answer" means: dispatch
the children, journal the question being asked, exit. The answers arrive the
way every answer in this design arrives — through the journals, on a later
wake. An answer that spans two ticks is correct. A session held open waiting
for one is not.

This is why the journal entry naming the question matters more here than
anywhere else in the system. It is the only thing that connects the wake that
asked to the wake that answers.

---

## 5. Acceptance, restated against this split

From the issue, with what each now means:

- **The common path runs with no Claude session.** The tick spawns repo-orch
  for conditions 1, 2, 5 and 7 directly.
- **`wake_gate` has tests before its behaviour moves.** `orch/test_core.py`
  presently says "wake_gate has no tests yet". Tests come first, in their own
  unit, and characterise current behaviour before anything is rewritten.
- **An operator can put a cross-repo question to dashboard-op and get an
  answer derived from its children.** Section 4b, delivered across ticks.
- **Conditions 3 and 4 still produce no spawn**, with their reasoning
  preserved as comments wherever the table lands.
- **Sessions per tick with nothing to do falls to zero.** No wake without a
  question.

## 6. Ordering

1. #96 / PR #105 lands. The per-repo digest is a prerequisite for the seam.
2. `wake_gate` tests, characterising today's behaviour.
3. The table moves into the tick, conditions 3 and 4 included as constants
   with their reasoning.
4. The tick's own spawn record replaces the agent-performed ack.
5. `agents/dashboard-op.md` is rewritten around sections 3 and 4 — the
   judgment, the question, the escalation — and loses the routing table.
