# Restructure audit — 2026-09-16

Executes orch#262. Report only: no code changed, no PR, no issue edited.
Written after reading `orch/core.py` (4,346 lines today, not 3,586),
`tick.py`, `feed.py`, `spawn.py`, `server.py` (partial), every `agents/*.md`
and skill, the design docs, the 62 open issues, the prior issue-orch audit
posted on #262 (02:47–02:56Z today), and 24 h of ledger/journal data on devhost.
Gate at the time of writing: `python3 -m orch.test_core` → `passed=1621
failed=0`.

Every claim below cites a line in *today's* tree or a measured number. Anything
I could not verify is marked **unverified**. The prior audit's line numbers are
stale (core.py grew ~760 lines since); where I disagree with it, I say so.

---

## 0. The answer

**The architecture below dashboard-op is right. dashboard-op's automatic wake
is not earning itself, and two wake rules spend sessions on states no agent
can clear. None of it needs a rewrite.**

Three numbers carry the whole case (all from the last 24 h on devhost, ledger
`state/sessions/*.json` and `state/dashboard-op.jsonl`, measured 17:35 EDT):

| fact | value |
|---|---|
| session starts, 24 h | 725 — repo-orch 322 · dashboard-op 272 · issue-orch 131 |
| dashboard-op journal, 24 h | `spawn` 272 · `skipped` 259 · cold-retry 6 · **every other event: 0** |
| ticks, 24 h | 510 (394 session-exit-triggered, 110 clock; `journalctl --user -u orch-web`) — `needs_attention` never 0 (min 12, max 52) |

dashboard-op was woken on 53 % of ticks and in 272 sessions **took no action
it is designed to take**: no `spawned`, `asked`, `deferred`, `noticed`, or
`filed` row. Every verdict it reached ("progressing, standing verdicts hold")
is written to a journal that nothing mechanical reads (`tick.py` reads only
`handled`, which `agents/dashboard-op.md:271` forbids it to write). Its
acceptance test from `docs/dashboard-op-split.md` §5 — *"Sessions per tick
with nothing to do falls to zero"* — is failed 259 times a day, and §4 of that
same document pre-wrote the verdict: *"If mechanical routing moves into the
tick and nothing explicit is named in its place, dashboard-op becomes a level
with nothing to do, and it gets cut for the same reason."* The things named in
its place were exercised zero times.

The rest of the cost is mostly inherent to the design's chosen trade — every
transition re-derives judgment in a fresh session — and that trade delivered
17 merged PRs unattended (#335). What is *not* inherent: waking for states only
the operator can clear (§2 below), and handing every wake a 236 KB JSON of
which 71 % is the operator's own interactive transcript list (§4).

---

## 1. Corrections to the premises, before the ranking

The issue body and the prior audit are themselves agent input; these need
fixing so the ranking is not built on them.

1. **`core.py` is 4,346 lines, not 3,586.** Grew 21 % since #262 was filed
   (orphan PRs, sibling branches, orch.json migration, names cache).
2. **The repo-management skill exists.** `agents/skills/repo-management/SKILL.md`,
   377 lines, landed 2026-09-15 22:13 (`966531f`, revised `82a1e69` today).
   #252 is closed. The prior audit's #2 ("repo-orch is a corpse, 686 lines of
   unimplemented spec") was true when written and is **false now**. In 24 h on
   the `orch` repo the level wrote 62 `started`, 58 `consolidated`, 53
   `deferred`, 8 `filed`, 8 `escalated` rows — real per-repo decisions,
   including standing orders and fold/order-adjacently judgments the skill
   specifies. repo-orch's problem is wake *frequency*, not existence.
3. **`public/dashboard.html` and `public/orch-canvas.html` are not abandoned.**
   Both are git-tracked and were edited today (`3943b7d` 12:47, `166beb5`
   12:50, "style(canvas): …"). They are the design artboards #225/#237/#241
   argue about, kept as HTML. The prior audit's "cut both, ~23 KB" is wrong.
   Not served by `server.py` (which serves `public/widget.html`), so the only
   defensible move is relocating them under `docs/` so they stop looking like
   output. Not a code cut.
4. **`owner_starting` has a reader.** `agents/skills/repo-management/SKILL.md:70`
   tells repo-orch to skip anything carrying it. The prior audit called it
   dead. Keep.
5. **`ACK_TTL_MINS` is 45 on the live unit**
   (`~/.config/systemd/user/orch-web.service.d/ack-ttl.conf`), 360 in
   `core.py:43`. #299's text reasons from 360. Every "one wake per 6 h"
   estimate in the prior audit is off by 8×.
6. **`orch.json` is now the config** (`core.py:579-640`, `_load_config`),
   `.orch.toml` is migrated-from (`core.py:643-700`), `repos.txt` migrated-from
   (`core.py:711-756`). The "three config formats" finding is resolved; what
   remains is two one-shot migration functions (~115 lines) that can go once
   every host has migrated — a deletion with a date, not a design problem.
7. **The condition count is now four-way drifted.** *Six*: `README.md:242`,
   `docs/DECISIONS.md:247,383,389,539`, `docs/UX-REDESIGN.md:13`,
   `orch/feed.py:134,828`, `agents/dashboard-op.md:267`. *Seven*:
   `DESIGN.md:675,682,731,802,1041,1810`. *Eight*: `orch/tick.py:177` (the
   section header). *Nine*: the code (`tick.py:365-389`, condition 9, orphan
   PR, landed today in `f01e1b8`). `SPAWN_CONDS = (1, 5, 7, 8)` at
   `tick.py:415`. The header of the file that defines them is wrong.
8. **"Recorded facts … nothing here gates on them" (`core.py:10`) is false.**
   `consolidated_coverage()` (`core.py:3901`) reads journal rows and its
   output gates condition 8 (`tick.py:338`), which gates spawns.
   `tick-spawns.json` gates `should_spawn` (`tick.py:537`). The design already
   admits recorded state *as a bounded suppressor*. This matters for §8.

---

## 2. What the 24 h actually looked like (the evidence base)

- `tick_loop` (`server.py:156`) fires on **any tracked session exit** or the
  600 s clock. With ~725 exits/day, 78 % of ticks are exit-triggered; median
  gap 56 s over the last 200 ticks. The tick's own stderr (`spawned
  repo-orch …`, `woke dashboard-op`, `no wake …`) is swallowed —
  `server.py:802-806` runs it with `capture_output=True` and prints only on
  `rc != 0`. `journalctl` has **zero** of those lines in 24 h. The tick's
  routing decisions are unobservable after the fact (defect, one-line fix,
  §7).
- `repo-orch.orch`: 193 spawns/24 h (one per 7.5 min). Its journal:
  294 `observed`, 193 `spawn`, 62 `started`, 58 `consolidated`, 53 `deferred`.
  `repo-orch.ops-console`: 32 spawns → 3 `started`, 17 `deferred`.
- issue-orch: 132 spawns across 68 keys; `issue-orch.orch.259` alone 12×,
  `.298/.257/.291/.280` 6× each. Those are re-entries — each one preceded by a
  repo-orch wake that decided to re-enter it (`route()` spawns repo-orch, not
  issue-orch: `tick.py:862`).
- Standing human-owed fires: at 12:15 today 10 PRs sat at REVIEW, four ~24 h.
  Each is `CLAIMED ∧ ¬orch_alive ∧ work_state == REVIEW`, which is condition 5
  (`tick.py:251-252`) true on every tick until the human merges. Right now
  only `orch#225` remains (`status.json`: REVIEW, `auto_land: false`, dead,
  `prior_runs: 3`) — the day's merges drained the rest; it will refill.
- The wake payload: `public/status.json` is 235,865 bytes. Breakdown:
  `unattached_sessions` 167,007 (71 %) · `repos` 41,822 · `agent_sessions`
  22,005 · everything else < 3 KB. `tick.py:702` inlines the whole dict into
  every dashboard-op brief; `spawn.py status` (`spawn.py:151-159`) prints the
  whole file to every repo-orch that reads it (`agents/repo-orch.md:107`).
  ~59k tokens entering context per wake, before the cache-read multiplier #187
  measured (cache_read = 87 % of raw volume). **Cost figure unverified against
  billing**; the byte counts are measured.

---

## 3. Ranked: largest simplification first

Each entry: **cut · replaces · breaks · evidence**. "Needs ruling" marks an
entry that changes an operator decision on record.

### 1. Delete the automatic dashboard-op wake. Keep the role for operator-invoked cross-repo questions only.

**Cut.** `tick.py:568-692` (dash-wake record, `last_ack`, `wake_gate`),
`tick.py:695-739` (`wake_dashboard_op`, the handoff block), `tick.py:840-845,
869-880` (handoff collection, the wake call), `feed.py:1077-1098`
(dashboard-op-hung alert; keep the `dashboard_op` feed dict for the widget),
`state/tick-dash-wake.json`, and roughly 300 of `agents/dashboard-op.md`'s
418 lines (sections "What you are handed on wake", "Why you were woken", "The
judgment", "You no longer ack"). The tests keyed `_r48_*`, `_c8_*`, and the
`wake_gate` characterisation tests. Keep: the role name, key, cwd, `MODEL_BY_ROLE`
entry, `DENY_BY_ROLE` entry, `spawn.py dashboard-op` — so the operator can
still run `echo "<question>" | ~/orch/spawn.py dashboard-op` for the cross-repo
question the level was redesigned around.

**Replaces.** Nothing on the common path — the tick already routes
(`tick.py:418-488`, `docs/dashboard-op-split.md:218`). The "fed, unchanged"
ambiguity the session was kept to resolve is *already* bounded by
`ACK_TTL_MINS` (45 min live): a suppressed repo re-wakes at TTL whether or not
a dashboard-op judged it, and in 24 h the session never once shortened that
bound (0 `spawned` rows) or lengthened it (a `deferred` row has no mechanical
effect — nothing in `tick.py` reads it). If the operator wants a periodic
cross-repo "noticing" pass, that is a clock-shaped wake (once per N hours), not
a digest-shaped one; ~5 lines in `tick_loop`.

**Breaks.** (a) Automatic condition-6 wedged judgment — info-only by design,
the widget already renders it (`feed.py:975-989`) and the operator kills.
(b) Automatic escalation filing by dashboard-op — 0 uses in 24 h; repo-orch
holds the same verb with a per-repo view. (c) Unprompted cross-repo
"noticing" — 0 `noticed` rows in 24 h. (d) #338 (re-key the ack on per-repo
digests) and the dashboard half of #187 become moot — close, don't build.
(e) #302 (give dashboard-op authority to unstick) — recommend **no**: adding
blast radius to a level with zero measured acts is the wrong direction; the
un-clearable states it lists are better served by §2 below and by #336's
tick-side lease.

**Evidence.** `state/dashboard-op.jsonl` 24 h: 272 `spawn`, 259 `skipped`,
0 other. Sampled `skipped` notes (17:26–17:32 today): "all three unchanged …
standing verdicts hold", "replay — already journaled this exact wake". 272 of
510 ticks woke it. The split doc's own acceptance (§5) and self-cut clause
(§4). #187 measured dashboard-op at 25 % of weighted spend over a 7 h window
(then as one 800-turn session; now as 272 short ones, each re-reading
`agents/dashboard-op.md` + a 236 KB JSON).

Estimated saving: 37 % of session starts; #187's share suggests 25–35 % of
spend. **Unverified against billing.**

### 2. Stop waking on human-owed REVIEW: gate condition 5's REVIEW disjunct on `auto_land`.

**Cut.** Two lines. `tick.py:251-252` becomes
`state == CLAIMED ∧ ¬orch_alive ∧ ((work_state == REVIEW ∧ auto_land) ∨ idle_over)`,
and `auto_land` (already emitted per issue by `feed.py:198,238`) is added to
`build_thin` (`tick.py:127-152`) as a digest input — same commit, per the rule
at `tick.py:48-52`.

**Replaces.** The closed PR #325's condition-9-`awaiting_merge` approach, and
#301's badge for this case: the widget already carries "waiting on you" for
every REVIEW row (`feed.py:990-1000`, `kind: waiting-on-merge`). This is the
prior audit's "held boolean" (its item 7) derived from labels the system
already reads — no new record, no new threshold.

**Breaks.** Automatic re-entry of a held REVIEW issue. Today
`agents/repo-orch.md:631-640` says re-enter it "the same way"; the re-entered
issue-orch reads the labels, finds `auto-land` absent, journals the wait and
exits (`agents/issue-orch.md:147-148`). That round trip is two Opus sessions
per held PR per TTL to record a fact the widget already shows. After the
change: operator adds `auto-land` → `auto_land` flips the per-repo digest →
condition 5 fires → re-entry → merge. Operator merges by hand → `LANDED` →
condition 7 fires → label cleanup. A blocking review finding that revoked
`auto-land` holds silently until the operator re-adds it — exactly what
`issue-orch.md:143-146` specifies. CHECKING/BLOCKED/ACTIVE with a dead owner
still fire via `idle_over` (unchanged).

**Evidence.** 10 PRs at REVIEW at 12:15 today, four ~24 h;
`issue-orch.orch.259` respawned 12× in 24 h; `orch#225` is the standing
condition-5 fire in `status.json` right now. #299's own caution ("must not
reintroduce wake spam on a repo whose queue is … entirely parked") is
satisfied by construction: parked-for-human is the case this stops firing on.
This is what #299 should land as; see §9.

### 3. Two-level re-entry: let the tick spawn issue-orch directly on conditions 5 and 7 and on operator `assign`. **Needs ruling.**

**Cut.** repo-orch from the re-entry path. `route()` (`tick.py:418`) already
has the issue number on every condition dict except 8 and 9; `feed.py:516`
already has `live_orchs` for the cap; `prior_runs` and `commits` are in the
feed for the three-deaths rule (`repo-orch.md:655-662`). Roughly: `route()`
returns `(slug, issue)` pairs for 5/7 and bare slugs for 1/8; `main()` spawns
`issue-orch` for the pairs under the cap and `repo-orch` for the slugs.
`a_assign` (`server.py:309`) spawns `issue-orch` directly instead of writing
`agent-ready` and waiting for two wakes. ~60 lines net in `tick.py`/`server.py`,
~40 lines out of `repo-orch.md`'s "Re-entry" section.

**Replaces.** A repo-orch session (Opus, ~$1.5, re-reads a 715-line doc + a
377-line skill + the journal + `status.json`) in front of every re-entry, with
a predicate the tick already evaluates. repo-orch stays for what only it can do:
condition 8 (backlog triage/ordering/consolidation), condition 1 on a
nominated-but-unstarted issue, nudges, `merge-blocked` sequencing, and the
`auto-land` revocation. That is the level's actual mission per
`repo-orch.md:8-19`; re-entry is the one bullet on that list that is a lookup.

**Breaks.** (a) The `--fresh` judgment ("prior conversation spent") moves to a
rule: `--fresh` when `prior_runs ≥ 2` and no new commits since the last death
— crude, and this is the part that needs the ruling, because it sits next to
the 2026-09-14 verdict that progressing-vs-spinning "must not be made
mechanical" (`dashboard-op.md:79-82`). That verdict was about the repo-level
judgment; re-entry of a single dead session is a different question, and
issue-orch derives its own stage from disk regardless (`issue-orch.md:95-114`).
(b) The re-brief content ("how it died") — issue-orch reads its own run log on
wake anyway. (c) Batching: one repo-orch wake today can re-enter several
issues; the tick would spawn several issue-orchs in one tick — the cap and the
per-key flock (`core.py:2686-2699`) already bound that.

**Evidence.** 132 issue-orch spawns/24 h, 68 keys, each behind a repo-orch
wake; `repo-orch.orch` 193 spawns → 62 `started`. The split between re-entry
and fresh-start `started` rows is **unverified** (the tick's routing log is
dropped, §7 item 1); reading the journal notes suggests a majority are
re-entries of the same six keys. Measure before deciding: the fix in §7 item 1
makes it measurable in a day.

### 4. Trim the wake payloads. No structure change.

**Cut.** From the dashboard-op brief (`tick.py:702`, if the level survives):
`unattached_sessions` and `agent_sessions` — 189 KB of 236 KB. From
`spawn.py status` (`spawn.py:151-159`): add `status <slug>` printing one
repo's row (~10 lines) and point `agents/repo-orch.md:107` and the
repo-management skill at it. `unattached_sessions` is the operator's own
interactive `~/.claude/projects` dirs from the last 2 days (`feed.py:614-637`)
— a widget curiosity, never an agent input.

**Replaces.** Nothing; agents read the same facts from a 6 KB slice.

**Breaks.** Nothing. The widget still reads `status.json` in full.

**Evidence.** Byte breakdown in §2. 594 supervisor wakes/day × ~59k tokens
entering context. #268's doc-compression lever is the same class and composes.

### 5. Delete the legacy ack reader.

**Cut.** `last_ack()` (`tick.py:615-646`) and the OR-newer clause in
`wake_gate` (`tick.py:683-689`); `dashboard-op.md:269-294` (the "you no
longer ack" section shrinks to one sentence). ~45 lines + doc. Subsumed by
item 1; standalone if item 1 is refused.

**Replaces.** Nothing. `read_dash_wake()` is always newer.

**Breaks.** Nothing on a current system.

**Evidence.** Last `handled` row in `state/dashboard-op.jsonl` is
`2026-09-14T19:07:58` (150 rows total, none since; #48 landed that day). Every
`tick-dash-wake.json` write is newer, so the legacy branch can never win — yet
`last_ack()` reads the **whole** 550 KB, 1,352-row journal
(`journal_tail("dashboard", n=10**6)`, `tick.py:629`) on all 510 ticks/day to
learn nothing.

### 6. Collapse the `agent-ready` axis. **Needs ruling.**

**Cut.** `L_READY` and everything that reads it: `candidates()` label test
(`core.py:1721`), `startable`/`owner_starting`/`ready` (`feed.py:189,265-266`),
condition 1's ready half (`tick.py:214`), the claim's `--remove-label
agent-ready` (`issue-orch.md:222`), `a_assign`'s label write (`server.py:322`,
becomes a direct issue-orch spawn — item 3), the skill's "operator-nominated
skip G1/G3" rule (`repo-management/SKILL.md:80-82`, becomes: an operator spawn
*is* the nomination), and #321 (which cannot occur without the label). ~100
lines + three doc sections.

**Replaces.** The label's remaining function is a two-wake handoff: repo-orch
adds it *in the same wake it spawns* (`agents/skills/repo-management/SKILL.md` red line 1:
"agent-ready only on an issue you are spawning this same wake"), issue-orch
removes it minutes later on claim. It carries information for ~2 minutes.
`candidates()` becomes `agent-working ∪ issues with a live or rolled ledger
key`; the dashboard's "starting" state comes from `orch_alive` alone.

**Breaks.** A human nominating from a phone by adding a label (the side-channel
virtue the prior audit rightly kept for the *other* labels). Under this cut a
phone nomination is `p0` (already a label orch creates, `core.py:254`, ranked
at P1B in the skill) — the tier expresses "start this" without inventing a
second axis. Operator ruling needed because it changes what `assign` means on
the dashboard.

**Evidence.** #321 (label survives completion, nothing clears it); the
prior audit's finding that four contradictory label combinations are
representable and only one has a named tiebreak (`auto_land_on`,
`core.py:151`); `core.py:1717` documents `ready`/`working` exclusivity as a
comment, not an invariant.

Not recommended: collapsing `auto-land`/`no-auto-land` into one label. The
pair exists so an issue can *opt out* of a repo-wide default in both
directions (`core.py:151-165`, #148 ruling B, #163); one label cannot express
"repo says yes, this issue says no". The precedence rule has a single home
and a production bug behind it (#149). Keep.

### 7. `transitions` + `_obs_pairs` + `_at_epoch` + `a_transitions`.

**Cut.** `core.py:4131-4275` (~145 lines), `server.py:618-623,663`, ~227 test
lines. **Confirm with the operator first** (could be a hand `curl`).

**Replaces.** Nothing.

**Breaks.** Nothing shipped: zero consumers in `widget.tpl.html`,
`agents/`, `docs/` (grep today; only `server.py` references it). Built for
#102's turn graph (#101/#100 still open, unbuilt).

### 8. `orch/tui.py` + `tui_model.py` + `tui_act.py` — 1,209 lines. **Needs one answer.**

Second complete client; `tui_model.pill_state` duplicates
`widget.tpl.html`'s state vocabulary so every vocabulary change (#257/#259)
lands twice. Only reference outside the package: `README.md:94`. No shell
history hit for `orch.tui` on devhost. **Unverified whether the operator uses
it.** If not, it is the single largest deletable block. If yes, keep and
accept the double landing.

### 9. Pure deletions, nothing breaks.

- `docs/WIZARD-PROGRESS.md` (150 lines) — finished scratchpad; its eight
  decisions live in `repo-orch.md` and `REPO-SKILL-INTERFACE.md`.
- `docs/dashboard-op-split.md` — mark superseded by this document if item 1
  lands; its §3 handoff seam is what item 1 deletes.
- The 26 `tmp_plan-*.md` files at repo root are **git-tracked** (force-added
  by design, `issue-orch.md:240-241`). They are landed plan history, not
  junk; if the root is to be cleaned, `git mv` them under `docs/plans/` in
  one commit rather than deleting. (The prior audit's `README.md` `spawn.py
  worker` finding is already fixed — 0 hits today.)
- `_migrate_repos_txt` + `_migrate_orch_toml_keys` (`core.py:643-756`, ~115
  lines) once every host has run once; add a date to the docstring now.
- `ask` verb (`spawn.py:257`, `server.py:107`) — #296 is in flight to remove
  it; nothing here contradicts that.
- `_CAVEMAN_PREAMBLE`'s duplicated NEVER-COMPRESS block (`core.py:2564-2572`
  and `2582-2590` are the same nine lines twice; the `full`/`lite`
  contradiction the prior audit found is already fixed — both say `full`).
- `MODEL_BY_ROLE` comment: `core.py:2883-2893` and `2929-2939` are verbatim
  duplicates.

### 10. The wake-condition set: keep it, fix the bookkeeping.

Not a cut. Nine conditions, four spawn (`SPAWN_CONDS = (1, 5, 7, 8)`,
`tick.py:415`), and the four never-spawn conditions (3, 4, 6, 9) are display
rows that inflate `needs_attention`. The prior audit wanted 7 folded into 5;
7 is five lines and deliberately un-gated (`tick.py:281-293`), and folding it
re-opens the merge-to-label-drop race it names. Leave the predicates. What to
do: rename the header (`tick.py:177`), split `needs_attention` into
spawn-worthy vs notes if item 1 lands (then the number is only a widget
figure), and make the count a #339 assertion so it cannot drift a fifth time.

---

## 4. The stored-state question — argued, not assumed

The operator flagged that "state is derived, never stored" (DECISIONS A6,
`docs/DECISIONS.md:85-94`; `core.py:8-10`) may be the cost driver, against
its benefit: *a session dying costs only conversation* (DESIGN.md:1586).

**The prohibition as written is not what the tree does.** It already stores
and *gates on*: `state/tick-spawns.json` (`should_spawn`, `tick.py:537`),
`state/tick-dash-wake.json` (`wake_gate`), `state/.last-observed-digest`
(step-4 journaling), `consolidated --covered` journal rows
(`consolidated_coverage` → `unconsidered` → condition 8 → a spawn), and the
`STANDING ORDER` row the repo-management skill instructs every wake to load
first (`SKILL.md:36-52`). What the tree actually practices is narrower and
sound:

> A recorded fact may **suppress or defer** for a bounded time, and may never
> **authorize**. Every reader degrades unreadable → "no record" → wake
> (`read_spawn_records`, `read_dash_wake`, `consolidated_coverage` all say so
> in their docstrings), and every suppression expires (`ACK_TTL_MINS`).

That rule keeps the benefit intact: nothing an agent *did* is trusted for a
merge, a claim, or a state; a dead session still costs only conversation
because the records it left can only make the machine quieter for ≤ TTL, and
the world re-derives the rest. The `#339` check should assert this rule
rather than the false sentence at `core.py:10`.

**Under that rule the expensive thing — re-deriving the standing order on
every wake (#335: "the expensive part is re-deriving judgment") — is
admissible to record.** The standing-order row *is already* that record; it
is just prose only an agent can read. The minimal mechanical form is one
per-repo file `state/repos/<slug>/parked.json`: `{issue: {lift: <observable>,
at}}` where `lift` names a fact the feed already derives (`pr-merged:<n>`,
`label-present:auto-land`, `issue-closed:<n>`), written by repo-orch's
`journal repo <slug> deferred` path, read by `should_spawn` to *skip* a repo
whose only firing conditions are parked issues with un-lifted conditions,
and ignored past TTL. It suppresses; it never authorizes; it fails toward
waking. That is the shape #299 (option 2), #336 ("operator-owed is
indistinguishable from abandoned"), #301 (stalled badge), and #302 (unstick
authority) are all circling. Item 2 above is the zero-record version for the
one case that dominated today (held REVIEW); build the record only if
measurement after items 1–3 shows the residual re-derivation is still the
cost.

Explicitly against the "costs only conversation" benefit: a wrong `parked`
record silences a repo for at most TTL, the same bound a wrong
`tick-spawns.json` entry already has (`tick.py:670-677`'s expiry argument).
It does not touch the ledger, the labels, or a merge gate. The benefit is not
spent.

---

## 5. Deliberately kept — do not re-question

- **`ROLLUP_UNREADABLE`** (`core.py:1470-1485`) and the **`pr_green`/`pr_red`
  non-complement** (`core.py:1995-2025`). The gap between them *is*
  `CHECKING`; collapsing them buys a false green. **Absent CI ≡ green**
  (`core.py:2009-2010`) is signed off (STATES.md finding 2). Item 2 above does
  not widen it: `auto_land` gates a *wake*, not a merge.
- **`RepoAdapter` with no table fallback** (`core.py:1268-1272,1318-1329`);
  `pr_view_review` is tea-only by construction (`core.py:3674-3688`).
- **The `~`/absolute deny triplication** (`core.py:2795-2801,2838-2844,
  2871-2875`, #188). The matcher never expands `~`; each spelling is a
  separate rule.
- **`ACK_TTL_MINS` and expiry** (`tick.py:537-565,670-677`). A wrong
  suppression must expire or it seals itself. 45 on the live unit is a
  mitigation; leave it until items 1–2 are measured.
- **The tick never kills** (`tick.py:272-274,468-473`; `dashboard-op.md:232`).
- **`per_repo_digests` as the suppression key** (`tick.py:62-84`, #96). Keying
  on the global digest reproduces 64-distinct-digests-for-11-changes.
- **Labels as the side channel.** Visible in the forge UI, writable from a
  phone, survive `state/` loss (local, not in git). Item 6 removes one label,
  not the mechanism.
- **The comment mass** in `core.py`/`tick.py`/`feed.py`. It is the ledger of
  shipped-and-fixed bugs (`tick.py:174-185` double-spawn, `tick.py:323-337`
  the `r["issues"]` vs `r["unconsidered"]` seam, `core.py:1883-1897` the paged
  orphan check, `core.py:3479-3519` merge-despite-rc≠0). Any cut carries its
  comment to wherever the logic lands.
- **`spawn()`'s single flock** (`core.py:2686-2719`), **cold-retry-once**
  (`core.py:3275-3293`), **the detached runlog pump in its own session**
  (`core.py:3159-3239`, #138), **`_child_env` stripping** (`core.py:296-320`).
- **`orphan_prs`' two-step closed confirmation** (`core.py:1883-1925`): absence
  from a paged list is not closure.
- **The repo-orch level and the repo-management skill.** Exists, exercised,
  and holds the judgments no predicate can (fold vs order-adjacently,
  `SKILL.md:196-290`). Its wake count is the problem, and items 2–4 address
  that without touching the level.
- **Condition 8 and `--covered` bookkeeping.** It is the only trigger for
  "a new issue was filed" and the precedent for bounded recorded suppression.
- **`World.pr_verified`** (`core.py:2027-2054`). No non-test caller;
  `agents/skills/issue-landing/SKILL.md` cites it as the thing not to do. Keep the symbol.
- **`owner_starting`** — read by `repo-management/SKILL.md:70`.
- **The envelope derived from `bash` fences** (`core.py:2971-3004`). It is what
  keeps docs and grants from drifting.
- **The design docs as a category.** Judged by truth, not length. §1 item 7
  lists the specific false sentences; #339 is the mechanism.
- **`public/dashboard.html`, `public/orch-canvas.html`** — maintained
  artboards (§1 item 3).

---

## 6. Survey of the 62 open issues — structural vs incidental

- **Resolved or mooted by items 1–2 if landed:** #338, #299 (as item 2),
  #187 (dashboard half), #301 (this case), #325's approach (already closed),
  #302 (recommend close as "no, see audit").
- **Structural, still real after this audit:** #339 (§9 first), #336
  (dead `agent-working` lease — a tick-side TTL flipping to `agent-stuck`;
  one new threshold, argued acceptably in its own body), #321 (dies with
  item 6, else needs a clearing path), #223 (§9, with the envelope hazard
  below), #268 (doc compression — composes with item 4), #337 (token
  provenance — real, out of scope), #278 (tea cannot write `blocked-by:`
  edges — a `RepoAdapter` gap), #174 (mostly landed: ticks are ~60 s;
  the floor half is mooted by items 1–2), #313 (main red — **not reproduced**:
  gate passes today), #332 (**reproduced today**: `repo_auto_land('~/orch')`
  → `False`, `repo_auto_land('/home/user/orch')` → `True`. Root cause
  `core.py:89`: the *target* is `Path(repo_path).resolve()` with no
  `expanduser()`, so `~/orch` resolves to `<cwd>/~/orch`, while the config
  entries get `.expanduser()` at `core.py:96`. One-word fix; every caller
  that passes a tilde path — `spawn.py watch ~/x` after migration, a hand
  `orch.json` edit — silently reads auto-land off).
- **Incidental / UI feature requests, no bearing on structure:** #289, #298,
  #295, #289, #211, #212, #209, #208, #189, #152, #154, #141, #140, #101,
  #100, #92, #13, #11, #241, #237, #225, #172 (docs), #231, #286, #28 (MCP —
  question), #222, #219, #204/#197 (OOM — environmental), #190, #184/#185
  (budget — real, but a reporting feature), #164, #159, #156, #146, #144,
  #143, #227, #228, #257, #280, #296, #308, #314, #318.

---

## 7. Defects found on the way (not cuts; file separately)

1. **The tick's routing log is discarded.** `server.py:802-806` captures the
   tick's stderr and prints it only on non-zero exit. `journalctl` shows 0
   `spawned repo-orch` / `woke dashboard-op` / `no wake` lines in 24 h.
   Forward `p.stderr` always. This is also what makes item 3's re-entry vs
   fresh-start split measurable.
2. **`tick.py:177` header says eight; nine exist.** Plus the six/seven counts
   in §1 item 7. Nine-condition PR `f01e1b8` landed today without touching the
   header — the exact class #325 demonstrated.
3. **`~/.claude/skills/orch/SKILL.md:101`** still asserts "Every `/act` HTTP
   action has a CLI form"; `README.md:202` was corrected today to "13 of the
   21 … have no CLI form". The runtime skill contradicts the README it cites.
4. **`core.py:10`** "nothing here gates on them" is false (§1 item 8).
5. **#332 reproduces; root cause is one missing call.** `core.py:89`
   `_repo_entry_by_path` resolves the *target* without `expanduser()` while
   config entries get it at `:96`. `repo_auto_land('~/orch')` is `False`
   today; the absolute path is `True`. Fails toward auto-land off, so it is
   silent.
6. **#223 envelope hazard.** `_rule_for_command` (`core.py:3027-3039`) grants
   `Bash(~/orch/spawn.py:*)` and the absolute form wholesale to any role whose
   doc fences `spawn.py`. A new `spawn.py issue edit …` verb is therefore
   **allowed to dashboard-op by default** although `gh issue edit` is denied
   to it (`core.py:2784`). Every #223 verb must be added to `DENY_BY_ROLE` for
   the roles that hold the underlying `gh`/`tea` deny, and to the
   `SPAWNING_VERBS`/`NONSPAWNING_VERBS` partition (`core.py:368-372`) or
   `spawn.py:492` refuses to import.

---

## 8. Sequencing #339, #299, #223

Order chosen so nothing is built on structure that items 1–3 delete:

1. **#339 first, minimal.** One test module asserting the decidable set:
   condition numbers unique, each in `SPAWN_CONDS` or in an explicit
   `NEVER_SPAWN` tuple, the header count equals the condition count; the
   six/seven/eight strings absent from docs; `ACK_TTL_MINS`/`IN_FLIGHT_CAP`/
   `NUDGE_IDLE_MINS` literals in docs equal `core.py`; `STATES.md` names every
   `work_state`/`issue_state` value and no other; the label set in docs equals
   `ORCH_LABEL_NAMES`; `spawn.py` `VERBS` and `server.py` `ACTIONS` match what
   README/skill claim, or the parity claim is gone; cited paths exist. Check
   the *runtime* docs first (`agents/**`, `~/.claude/skills/orch/SKILL.md`) —
   they are the input. ~150 lines. It depends on nothing below and catches the
   doc stragglers every cut below leaves.
2. **#299 as item 2.** One commit: predicate + digest input + the
   `repo-orch.md:631-640` re-entry paragraph + a #339 assertion that
   `auto_land` is in `build_thin`. Close #325's path. Measure repo-orch
   spawns/day for 24 h (needs §7 item 1 landed first, same day).
3. **Item 1 (dashboard-op auto-wake)** after the measurement, so its saving is
   attributable. Close #338 and the dashboard half of #187 under it.
4. **Item 4 (payload trim / `status <slug>`)** any time; independent.
5. **Rulings: item 3 (two-level re-entry) and item 6 (`agent-ready`).** Then
   **#223**, last — its `assign`/`issue` verbs depend on whether `agent-ready`
   survives and on the deny partition (§7 item 5). `merge` stays excluded as
   ruled.
6. **#336** after items 2–3: measure what dead-`agent-working` cases remain
   once held REVIEW no longer re-enters; the tick-side TTL may then only need
   to cover "dead mid-flight, no PR, three deaths".
7. **#187/#174 floors, #268 compression**: re-measure after 1–4 before
   building either; both may be mostly paid for.

---

## 9. Unverified — do not act on without checking

- Cost figures per session/role (I have session counts and byte sizes; #335's
  $574 is the only billing number, and it predates today's changes).
- Whether `python3 -m orch.tui` is used (item 8).
- Whether anything `curl`s `transitions` (item 7).
- The re-entry vs fresh-start split of repo-orch `started` rows (item 3) —
  measurable after §7 item 1.

---

## 10. Operator rulings — 2026-09-16

Recorded in the review session that followed this report. Rulings only; no
code changed under this section. Where a ruling resolves an item's "needs
ruling" or answers a §9 unverified, that is noted.

### Ranked items

| item | ruling |
|---|---|
| 1. Delete dashboard-op's automatic wake | **do** — land third, after #339 and item 2, so the saving is attributable |
| 2. Gate condition 5's REVIEW on `auto_land` | **do** — this is what #299 lands as |
| 3. Two-level re-entry | **defer until measured** (§9: needs the swallowed-stderr defect fixed first) |
| 4. Trim wake payloads | **do** |
| 5. Delete `last_ack()` | **do** |
| 6. Collapse the `agent-ready` axis | **collapse** — resolves "needs ruling" |
| 7. `transitions` + `_obs_pairs` + `_at_epoch` + `a_transitions` | **delete** — operator confirms nothing curls it (resolves a §9 unverified) |
| 8. TUI (1,209 lines) | **keep** — "an issue, nice to have later, but ultimately just a wrapper for the CLI" (resolves a §9 unverified). Not deleted; does not constrain other items. Accept the double landing of vocabulary changes, or let the CLI-wrapper framing collapse it later. |
| 9. Pure deletions | **do** |
| 10. Conditions: keep nine, fix bookkeeping | **do** |

### On dashboard-op's role (bears on item 1)

The operator's own framing: dashboard-op is *"just the set of skills that
agent can do to manage orch — the agent I talk to about orch, who can run the
skills."* That is precisely what item 1 preserves (role, key, spawn path, the
`spawn.py dashboard-op` entry point) and is orthogonal to what item 1 deletes
(the automatic digest-shaped wake). The two are compatible; item 1 remains
open only because it has not been ruled, not because the framing conflicts.

### On auto-land — a standing intent, not an item in this report

*"The point of auto-land is that it doesn't pause on me. I'll check back and
stop/pause myself. I'll use orch to watch."*

This is a **posture**, and it sharpens item 2 rather than replacing it. Item 2
stops *waking* on human-owed REVIEW; the operator's intent is that far fewer
issues should be in human-owed REVIEW at all. Both hold at once: gate the wake,
and let auto-land be the normal case.

Three axes stay separate and per-repo, as they are today:

| axis | scope | meaning |
|---|---|---|
| `state: tracked` / `off` | repo | is orch watching at all |
| `auto-land` | repo | does work land unattended, or hold for the operator |
| `no-auto-land` label | issue | hold this one issue against the repo default |

The operator's rule for the third: **auto-land just lands; to hold something,
file it and set `no-auto-land`, and that is what waits for review.** The
existing precedence in `auto_land_on` (`core.py:151-165`) already implements
exactly this — `no-auto-land` wins both directions, then `auto-land`, then the
repo default. No code change is implied by the rule itself; only the repo
defaults would change.

**Measured during the review, and NOT yet explained:** `orch` and
`ops-console` already carry `"auto-land": true` in `orch.json`; the other
eleven watched repos set no key. Yet on 2026-09-16 four ops-console PRs sat at
REVIEW for ~24 h and were merged by hand. Something defeated auto-land on a
repo that has it enabled. §7 defect 5 (#332, the missing `expanduser()`) is one
candidate — it fails silently toward auto-land **off** — but it has not been
shown to be the cause here. **Do not flip the repo defaults until this is
understood**: if something defeats auto-land where it is already on, widening
the default spreads the silent failure to eleven more repos rather than fixing
anything.

Note also the fail-safe direction this interacts with. `repo_auto_land`
(`core.py:104-122`) documents, deliberately, that *every* miss errs toward
False — "the failure mode is a repo that opted in silently not getting it,
never a repo merging when it did not ask." Inverting the default inverts that
property too, and #332 would then fail toward **merge**. That is an operator
call to make explicitly, not a side effect to inherit.

### Out-of-scope item raised in the same session

**#313 — `main`'s test import is broken** (`NameError: _write_esc_journal` in
`test_core.py`), so every open branch's CI is red regardless of its diff. PR
#312 is the written fix and is unlabelled, so orch cannot pick it up. Not a
finding of this report — this report's gate ran clean because it read the tree
rather than running `main`'s suite — but it blocks any auto-land-on-green
posture for the one watched repo that actually has CI.

### Item 1 ruled: delete the automatic wake

**Do it.** All ten ranked items are now ruled.

The operator's framing of dashboard-op — *"the set of skills that agent can do
to manage orch; the agent I talk to about orch, who can run the skills"* — is
what item 1 **keeps**: the role, key, `MODEL_BY_ROLE` and `DENY_BY_ROLE`
entries, and `echo "<question>" | ~/orch/spawn.py dashboard-op`. Only the
digest-shaped automatic wake is deleted.

Accepted losses, stated plainly: no automatic condition-6 wedged judgment (the
widget already renders it and the operator kills), no automatic escalation
filing by dashboard-op (0 uses in 24 h; repo-orch holds the same verb), and no
unprompted cross-repo *noticing* (0 `noticed` rows in 24 h, 8 decisive acts in
15 days). Cross-repo patterns must now be **asked for**. That is the real cost
of this cut and it was accepted knowingly.

Follow-on, per §3 item 1: close #338 and the dashboard half of #187 as mooted,
and close #302 ("give dashboard-op authority to unstick") as **no** — adding
blast radius to a level with zero measured acts is the wrong direction.

### §4 stored state, and §6's survey — walked, not yet acted on

§4's argument was reviewed and stands: the tree already stores and gates on
five records, and the practiced rule ("a recorded fact may suppress or defer
for ≤ TTL, and may never authorize; unreadable degrades to wake") preserves the
"a dead session costs only conversation" benefit. The `parked.json` record it
proposes is **not authorized here** — per §4's own discipline, item 2 is the
zero-record version for the dominant case, and the record gets built only if
measurement after items 1–3 shows residual re-derivation cost. #339 should
assert the practiced rule in place of the false sentence at `core.py:10`.

§6's split stands as written: ~8 structural issues, ~40 incidental UI/feature
requests, and a handful mooted by items 1–2.

### Still open after this session

- Whether to flip repo `auto-land` defaults, pending the ops-console question
  above.
- The sequencing in §8 is unchanged and unstarted: #339 → item 2 (#299) →
  item 1 → item 4 → rulings-dependent work → #223 last.

---

## 11. The synthesis — 2026-09-16, after the rulings

Recorded because it was derived in review and existed nowhere in this file.
It is the frame the §3 items and #268 both sit inside.

### Cost is a product, not a sum

    cost ≈ (number of wakes) × (what each wake reads)

The audit's §3 attacks the left factor. #268 attacks the right one. They
**multiply**, so cutting either helps and cutting both compounds — but it also
means neither is worth over-engineering alone.

**Left factor, measured 24 h:** 725 session starts (repo-orch 322 ·
dashboard-op 272 · issue-orch 131); dashboard-op woke 274 times and recorded
**zero** decisive acts; lifetime 742 spawns → 8 acts, one per 93. Ruled: item 1
(#343) deletes the automatic wake, item 2 (#299) stops waking on human-owed
REVIEW, item 4 (#344, **landed**) cuts the payload.

**Right factor, measured the same day:** `state/repos/*/*.jsonl` holds
**~416,000 tokens** of prose across 897 fields in 6,853 rows;
`state/repos/orch/orch.jsonl` alone is 1.6 MB. Of that, the single `note`
field is **~299,000 tokens** across 478 rows — 2,504 chars average, worst row
6,683. The journal tail is re-read on **every** wake (`core.journal_tail`
feeds the next brief; the repo-management skill loads STANDING ORDER first),
so unlike a PR body it is re-paid per wake, per repo, forever.

### Sequencing follows from the product

**Land item 1 (#343) before investing in #268's compression.** If the wake
count collapses, the right factor shrinks with it and the compression may not
be worth its complexity. The reverse order pays for compression that a
later cut makes cheap anyway. This is the same "measure, then cut" discipline
§8 already applies to items 2 and 3 — stated here for #268, which §8 did not
sequence.

### The finding underneath both halves

**Agents were writing for a human audience that does not exist.**

`_CAVEMAN_PREAMBLE` (`core.py`) compresses agent prose but carves out
*"code, commit messages, and PR bodies — write these normally"*. That carve-out
is written as though a person reads those artifacts at rest. In this system
nobody does: a PR body is read by the operator once at merge and by a reviewer
agent; a journal note is read only by the next agent. Measured consequence —
PR bodies average **~1,010 tokens** (max 8,868 chars, for a PR about four
tooltips), and the journal number above.

So the exemption is **inverted** for this system: the artifacts only agents
read are the ones that should compress hardest. Operator ruling, 2026-09-16:
*"the PR body or journal needs to be compressed since only agents read them."*

### The fix is smaller than it sounds

Not structured journals, not a new notation, not a format migration. The
journal is already well-shaped JSONL with cheap fields; `note` is one
free-text field absorbing content that belongs in fields. The worst row
decomposes as: PR/issue numbers and timestamps (derivable from GitHub),
open/ready/claimed counts (already in `status.json`), a blocked-wake count
(one integer) — and perhaps 400 chars of genuine cross-issue judgment, which
is the only part worth prose.

One enforceable rule captures it:

> If a fact is derivable from GitHub, from `status.json`, or from another
> field in the same row, it does not belong in `note`.

Detail and field candidates are in #268; the PR-body schema is #363. Both are
assertable by #339 in a way a prose blob is not — a missing field is
detectable, a missing sentence is not.

### What does NOT compress

Unchanged from §5 and the preamble's own NEVER-COMPRESS list: issue and PR
numbers, `file:line` cites, measured values, exact error strings, and the
what-breaks section. Compression here means **notation, not omission**.
`why` (22 rows, ~18.9k tokens) stays as is. STANDING ORDER rows are judgment,
re-read every wake, and must be measured separately before anyone touches them
— length may be earning itself there.

---

## 12. Correction to §11 — measured, 2026-09-16, later the same day

§11 framed the right-hand factor as *authored prose* and treated journal and
PR-body compression as a cost lever. **That framing was wrong on the numbers.**
A measurement pass on #363 priced every token orch has ever produced; the
correction is recorded here rather than by editing §11, so the reasoning that
led to it stays visible.

### Where the money actually is

Population: all 861 orch transcripts under `~/.claude/projects/**/*.jsonl`
(main + subagent), 53,343 assistant messages. Cost-weighted at Opus-class
rates:

| slice | cost | share |
|---|---|---|
| cache read | $10,559.59 | **56.8%** |
| cache write | $6,337.17 | **34.1%** |
| output | $1,681.84 | 9.1% |
| fresh input | $1.64 | 0.0% |
| **total** | **$18,580.25** | |

Output is 0.303% of all tokens by count and 9.1% by cost. **Everything orch
has ever written — every PR body, issue body, journal row, comment and line
of code — is 9% of spend. The other 91% is loading context into sessions.**

### What that does to §11's two factors

The left factor stands, and is now the *only* factor that matters:
cache read + cache write = **90.9%**, and both are paid per session start.
Fewer sessions and smaller briefs are the whole game.

The right factor was mis-stated. §11 said "what each wake reads" and then
argued it through the *writing* of journal notes. Re-reading is a cache read;
writing is output. Those are different budgets with a 6× price difference per
token, and the one §11 attacked is the small one.

### PR bodies are not a cost lever — settled

25 most recent PRs: mean body 4,153 chars (~1,038 tokens), 103,848 chars total,
priced as output at **$1.95 — 0.0105% of total spend**. The 93% encoding cut
demonstrated in #363 therefore saves **$1.81 across 25 PRs**, about 7 cents per
PR. Against #262's $34/PR supervision figure, authoring the body is **0.2% of
what a PR costs**, and the schema would take that to 0.015%.

#363's own "measure before ruling" section set the bar at 1% of spend. The
answer is 0.01%. **It is a tidiness question, not a cost question**, and should
be decided on whether short bodies are easier to read — not on savings.

### What survives, in sharper form

Journal notes are still worth compressing, but **not for the reason §11 gave.**
A journal note is written once (output, cheap) and re-read on every subsequent
wake (cache read, expensive). Its cost is a function of how often it is
re-read, not how long it took to write. The same is true of `agents/*.md` and
of the wake payload.

That reorders the work:

1. **Cut session starts.** Item 1 / #343 (delete dashboard-op's automatic
   wake, ~37% of starts) and item 2 / #299. Each avoided session avoids a
   whole cache-write + cache-read cycle.
2. **Cut what every session loads.** Item 4 / #344 (**landed**) removed 189 KB
   of 236 KB from the wake payload — 71% of it the operator's own transcript
   list. This is the highest-leverage class remaining, because it is paid on
   every start forever.
3. **Compress what is re-read most**, measured by re-read frequency: the
   journal tail, STANDING ORDER, `agents/*.md`. #268's "do not compress agent
   docs" verdict was argued on enforcement grounds (they ARE the enforcement,
   `core.py:2164`) and that argument still holds — but its cost side now reads
   differently, because those docs are re-read on every wake and priced as
   cache reads. #268 should be re-opened on that basis, not on output volume.
4. **Do not spend effort on authored prose as a cost measure.** 9% ceiling,
   and the PR-body slice of it is 0.01%.

### Method caveat

`total_cost_usd` is emitted once per session on the terminal `result` event
(`orch/runlog.py:174`). There is no per-tool, per-message or per-artifact cost
field anywhere in orch or in the transcripts it reads, so per-artifact spend
cannot be read directly — it is bounded from the token side, which is recorded
per assistant message. The bound is tight enough to settle this question and
would not be tight enough for a closer one.
