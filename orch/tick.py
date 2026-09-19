#!/usr/bin/env python3
"""The tick. Dumb on purpose: it reads the world, records what it saw, rebuilds
the dashboard, and knocks once on dashboard-op. It claims nothing, spawns
nothing deeper, merges nothing. Judgment belongs to whoever holds context.

This is the unmoved mover - mechanical, restartable, incapable of being
wrong about the work. Do not make it smarter.

It holds exactly three numbers, NUDGE_IDLE_MINS, ACK_TTL_MINS, and
LEASE_TTL_MINS (all from core). All three are policy about the tick's own
attention - never about the work. No other thresholds anywhere in this file.

Correction to the older claim above: the tick now performs exactly TWO label
writes, and both are mechanical rather than judgments about the work.

  1. Lease expiry (agent-working -> agent-stuck, via core.lease_expired).
     Fully derived from a timestamp already in hand (core.claim_age_mins
     reads only the `agent-working` labeled-event time from the issue's
     timeline -- never createdAt, updatedAt, comments, or any comment or
     journal content), gated by a single number the same way idle_over
     gates on NUDGE_IDLE_MINS.

  2. Void claim (agent-working removed, no label added, via
     core.void_claim). Adds NO number: it is a pure ledger/git read -- no
     prior-run row, no `result` line in the session log, no commits on the
     branch -- so it needs no threshold and holds no opinion. It returns an
     issue to the pool rather than marking it abandoned; see
     core.void_claim's docstring for why agent-stuck would be wrong there.

Nothing else in this file writes to the forge.
"""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from orch import core, feed
from orch.core import ORCH_HOME, NUDGE_IDLE_MINS, ACK_TTL_MINS, DRY_RUN, log, now

# SPAWN_FLOOR_SECS / ACK_TTL_MINS are a PAIR (orch#174), always read together:
#   SPAWN_FLOOR_SECS  - lower bound on agentic spacing. No spawn for a given
#                        slug happens more often than this, no matter how
#                        fast the tick's derivation cadence (ORCH_TICK_SECS,
#                        see ticker.py) runs.
#   ACK_TTL_MINS * 60 - upper bound on wrong-suppression silence. A wrong
#                        suppression can hide a slug for at most this long.
# Both numbers are stated in SECONDS here on purpose - "N ticks" is a latent
# bug once the derivation cadence and the agentic floor are allowed to move
# independently (orch#174: derivation moved to 60s, this floor did not).
# The assert below is the guardrail the issue calls out: a floor >= the TTL
# would let a wrong suppression outlive its own expiry check, recreating the
# self-sealing suppression wake_gate's OR-expiry clause was built to prevent.
SPAWN_FLOOR_SECS = int(os.environ.get("SPAWN_FLOOR_SECS", "600"))
assert SPAWN_FLOOR_SECS < ACK_TTL_MINS * 60, (
    f"SPAWN_FLOOR_SECS ({SPAWN_FLOOR_SECS}s) must be < ACK_TTL_MINS*60 "
    f"({ACK_TTL_MINS * 60}s) - a floor at or past the TTL would swallow the "
    "expiry and let a wrong suppression seal itself shut"
)

PUBLIC = ORCH_HOME / "public"
STATE = ORCH_HOME / "state"
FEED_PATH = PUBLIC / "status.json"
HISTORY_PATH = ORCH_HOME / "history.jsonl"
TICK_LOCK = core.TICK_LOCK
# Step-4 journal-only-on-change gate. NOT the spawn-suppression digest (see
# should_spawn below) - this file only decides whether "still nothing" noise
# gets written to repo journals. Keep the two questions separate.
STEP4_DIGEST_FILE = STATE / ".last-observed-digest"


# === idle, defined once =====================================================

def idle_over(activity):
    """activity: newest of work_mtime, transcript mtime, and updatedAt, as a
    unix ts, or None if there's no activity at all (treated as infinitely
    idle -> over). idle_over is a boolean so it flips exactly once at the
    threshold - no repeat-wake spam."""
    if activity is None:
        return True
    return (now() - activity) > NUDGE_IDLE_MINS * 60


# === the digest — the most important invariant in this file ================
# Hash EXACTLY the facts the conditions read - nothing more, nothing less.
# Less re-creates the suppression bug (a condition that cannot flip the
# digest can never cause a wake); more re-creates wake spam. A new condition
# and its digest inputs land in the same commit, always.

def digest_of(thin):
    """thin is the already-built per-repo/issue shape (see build_thin below)
    plus the error-alert set. Sorted-key JSON, sha256, truncated to
    16 hex chars."""
    blob = json.dumps(thin, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def per_repo_digests(thin):
    """slug -> digest over ONLY that repo's entry of build_thin's output.

    The global digest is deliberately global: it must flip when ANY fact a
    condition reads moves, anywhere, or a wake gets suppressed. That is the
    right shape for the wake gate and it does not change here.

    But it is the WRONG shape for "has this repo's state actually changed
    since I last acted on it". With several repos live, some repo is always
    moving, so the global digest is a fresh value on nearly every tick (#96:
    112 handled rows, 101 distinct digests) and a repo frozen for hours never
    sees a repeat. dashboard-op's rule "defer only when the chain already ran
    for this same digest" is then unsatisfiable, and the defer it needs is
    exactly the one it can never justify.

    So hand it a per-repo digest too. Same hash function on purpose - a
    second hash here would drift from digest_of the first time either is
    touched. This is ADDITIVE: it is not the global digest, it gates no
    ack beyond should_spawn's own suppression, and nothing else reads it.
    Do NOT "simplify" the global digest onto these - per-repo gating would
    lose the error-alert set, which is repo-less and must still be able to
    raise needs_attention on its own.
    """
    return {r["repo"]: digest_of(r) for r in thin["repos"]}


def _orphan_pr_numbers(rows):
    """[{number, ...}] -> [int]. Anything unusable is SKIPPED, never raised.

    build_thin runs over the whole feed, so an exception here takes down the
    entire tick rather than one repo. core.World.orphan_prs always writes an
    int `number`, but a hand-edited or stale cached feed can hold `None` in
    place of the list, or a non-numeric `number` -- neither is worth losing a
    tick over, and a dropped row degrades to "one fewer orphan in the digest",
    which the next clean read corrects."""
    out = []
    for o in rows or []:
        if not isinstance(o, dict):
            continue
        try:
            out.append(int(o.get("number")))
        except (TypeError, ValueError):
            continue
    return out


def _orphan_pr_reasons(rows):
    """[{number, issue, reason, ...}] -> [{number, issue, reason}] , sorted by
    number. Same tolerance as _orphan_pr_numbers (malformed rows dropped, not
    raised) but keeps `issue` and `reason` alive into the digest -- condition
    9 (orch#352) needs `issue` to route an unlabelled orphan's SPAWN, and
    `reason` to tell an unlabelled orphan (issue still open, spawnable) apart
    from a closed one (issue gone, nothing to spawn against). A row with no
    usable `number` is dropped exactly like _orphan_pr_numbers drops it;
    `issue` and `reason` are left as-is (None-safe .get()) since route() and
    compute_conditions already treat a missing/unknown reason as the weaker,
    non-spawning claim."""
    out = []
    for o in rows or []:
        if not isinstance(o, dict):
            continue
        try:
            num = int(o.get("number"))
        except (TypeError, ValueError):
            continue
        issue = o.get("issue")
        try:
            issue = int(issue) if issue is not None else None
        except (TypeError, ValueError):
            issue = None
        out.append({"number": num, "issue": issue, "reason": o.get("reason")})
    return sorted(out, key=lambda o: o["number"])


def build_thin(data):
    """per repo -> per issue {number, state, work_state, orch_alive,
                               idle_over, contended, startable, never_owned}
       plus the error-alert set.

    NO unit layer: units are recorded (issue-orch's plan), never derived, and
    recorded facts never enter the digest - the tick sees nothing smaller
    than the issue.

    Liveness and activity are read off the feed data, never re-derived here:
    feed's single pass is the one pass, and this is one of its two consumers.
    A second walker over the world here would be exactly the drift the
    digest rule forbids.
    """
    repos = []
    for r in data.get("repos", []):
        slug = r.get("slug") or r.get("repo")
        issues_out = []
        for i in r.get("issues", []):
            n = i.get("issue")
            issues_out.append({
                "number": n,
                "state": i.get("state"),
                "work_state": i.get("work_state"),
                "orch_alive": bool(i.get("orch_alive")),
                "idle_over": idle_over(i.get("activity")),
                "contended": bool(i.get("contended")),
                # The window opening (a fresh spawn's ledger row lands) or
                # closing (the label claim lands) is a real state change,
                # not incidental - it must flip the wake digest like any
                # other fact a condition reads.
                "startable": bool(i.get("startable")),
                # never_owned distinguishes an issue whose claim has no
                # session behind it, ever, from an issue whose owner died
                # mid-flight. It is derived at read time from feed data
                # already present (prior_runs, orch_alive); it is never a
                # recorded state of its own. Condition 5 below reads it, so
                # it is a digest input by the rule at tick.py:48-50 - hash
                # exactly the facts the conditions read, nothing more,
                # nothing less.
                "never_owned": i.get("prior_runs", 0) == 0 and not bool(i.get("orch_alive")),
                # considered: digest input for condition 8 (the set-level
                # consolidation wake). .get() defaults to False when absent,
                # which is the correct degrade: unknown -> uncovered -> wake.
                "considered": bool(i.get("considered")),
            })
        # unconsidered: digest input for condition 8 (the set-level
        # consolidation wake). Repo-level, not per-issue -- built by
        # feed.repo_json from ALL open issues (world.issues), not from
        # r["issues"]/candidates(). .get() defaults to [] when absent, and
        # each element is coerced to int so the digest hashes a stable shape
        # regardless of the feed's own int/str choices.
        # orphan_prs: PR NUMBERS only. An orphaned PR (open, issue closed) has
        # no issue row to ride in on, so if it is not in the digest the wake
        # cannot see it appear or disappear -- the digest would be identical
        # whether or not one exists (orch#279). Numbers alone are enough to
        # change the hash when the set changes; the branch and issue are
        # display detail and stay out so a rename does not spuriously wake.
        # orphan_reasons: same PRs as orphan_prs above, but carrying `issue`
        # and `reason` -- condition 9 needs both to split "issue still open,
        # spawnable" from "issue closed, nothing to spawn against" (orch#352).
        # A separate key rather than widening orphan_prs itself: orphan_prs's
        # shape (bare ints) is an existing digest contract other tests pin,
        # and nothing about the digest rule requires collapsing the two.
        repos.append({"repo": slug, "issues": issues_out,
                      "unconsidered": sorted(int(n) for n in r.get("unconsidered", [])),
                      "orphan_prs": sorted(_orphan_pr_numbers(r.get("orphan_prs"))),
                      "orphan_reasons": _orphan_pr_reasons(r.get("orphan_prs"))})

    error_alerts = sorted(
        a.get("key", json.dumps(a, sort_keys=True))
        for a in data.get("alerts", [])
        if a.get("level") == "error"
    )
    return {"repos": repos, "error_alerts": error_alerts}


# === the nine conditions ====================================================

def compute_conditions(data, thin):
    """Returns (needs_attention: int, notes: list[str], conds: list[dict]).
    Pure mechanics: no threshold beyond NUDGE_IDLE_MINS (already baked into
    thin's idle_over), no judgment about what to do.

    needs_attention and notes are UNCHANGED in position and value - existing
    callers and tests depend on both. The third value is purely additive:
    one dict per fired condition,
        {"slug": str|None, "issue": int|None, "cond": int, "orch_alive": bool}
    so route() can decide structurally instead of re-parsing a display
    string. Condition 4 is repo-less: slug None, issue None.
    """
    needs_attention = 0
    notes = []
    conds = []

    for r in thin["repos"]:
        slug = r["repo"]
        for i in r["issues"]:
            n = i["number"]

            # 1. Ready or abandoned.
            #
            # `startable` is NOT the same fact as "UNCLAIMED and ready" - it
            # already excludes the window where spawn.py has written the
            # ledger row for a fresh issue-orch but that session has not yet
            # claimed the `agent-ready` label (a startup act that happens
            # minutes into its own run, not at spawn time). A spawned
            # issue-orch is an OWNER from the moment its ledger row exists;
            # firing this condition on a merely-UNCLAIMED-and-ready issue
            # during that window is the double-spawn path onto live work
            # (see #47 near-miss against #44/#42/#14). Do NOT "simplify"
            # this back to `i["state"] == "UNCLAIMED" and ready` - that
            # reintroduces exactly the bug this field exists to close. See
            # condition 5 and 7 below for the same do-not-simplify shape.
            if i["state"] == "ABANDONED" or i["startable"]:
                needs_attention += 1
                notes.append(f"{slug}#{n}: ready/abandoned")
                conds.append({"slug": slug, "issue": n, "cond": 1,
                              "orch_alive": i["orch_alive"]})

            # 2. Blocked.
            if i["work_state"] == "BLOCKED":
                needs_attention += 1
                notes.append(f"{slug}#{n}: blocked")
                conds.append({"slug": slug, "issue": n, "cond": 2,
                              "orch_alive": i["orch_alive"]})

            # 3. Contended.
            if i["contended"]:
                needs_attention += 1
                notes.append(f"{slug}#{n}: contended")
                conds.append({"slug": slug, "issue": n, "cond": 3,
                              "orch_alive": i["orch_alive"]})

            # 5. Unowned work. issue CLAIMED AND issue-orch not alive AND
            # (work_state REVIEW OR issue idle_over).
            #
            # The old `not units_alive` conjunct is gone BY CONSTRUCTION, not
            # simplified away: workers are now Agent-tool subagents - threads
            # inside issue-orch's own process - and cannot outlive it, so
            # "issue-orch dead but a worker alive" is not a reachable state.
            #
            # The REVIEW disjunct is a deliberate spec correction, not a
            # simplification target: finished work is not idle, it is
            # waiting - the work is done and the PR is open, so there is
            # nothing to infer about staleness. Requiring idle_over there
            # would delay every auto-land by NUDGE_IDLE_MINS for no reason.
            # REVIEW with no live owner is a fact; the other path into this
            # condition is an inference, and that keeps the idle gate. This
            # adds no new threshold - do not "simplify" it back to a single
            # idle_over check.
            if (i["state"] == "CLAIMED" and not i["orch_alive"]
                    and (i["work_state"] == "REVIEW" or i["idle_over"])):
                # never_owned distinguishes one state already inside this
                # condition (owner died mid-flight) from another (the claim
                # never had a session). This is a note/dict distinction, not
                # an eighth condition: DESIGN.md:721 sets a high bar for an
                # eighth condition, and this state does not clear it -
                # condition 5 already fires here and already routes an
                # owner, so a new condition would double-fire on one state.
                # Routing stays UNCHANGED on purpose: condition 5 is already
                # in SPAWN_CONDS, and re-entry already passes through
                # core.spawn()'s flock refusal, so there is no new automatic
                # path that could route around that refusal and double-spawn
                # onto live work.
                orphan = i["never_owned"]
                needs_attention += 1
                notes.append(f"{slug}#{n}: unowned work"
                             + (" (never owned — claim with no session ever)" if orphan else ""))
                conds.append({"slug": slug, "issue": n, "cond": 5,
                              "orch_alive": i["orch_alive"], "never_owned": orphan})

            # 6. Wedged. Information only - nothing in this tree ever kills a
            # wedged session; dashboard-op applies the long-running-lease
            # rule and the kill, if any, is the operator's act.
            if i["orch_alive"] and i["idle_over"]:
                needs_attention += 1
                notes.append(f"{slug}#{n}: wedged (info)")
                conds.append({"slug": slug, "issue": n, "cond": 6,
                              "orch_alive": i["orch_alive"]})

            # 7. Landed but still claimed. Deliberately NOT gated on
            # orch_alive and NOT gated on idle_over. The merged PR behind
            # LANDED is a derived fact, not an inference, so it needs no
            # threshold to be trusted - unlike condition 5's idle_over path,
            # there is nothing here to wait out. A live owner that has
            # landed and just hasn't dropped the label yet costs one note
            # that self-clears next tick once the label is gone; gating
            # this on liveness instead would reopen exactly the race this
            # condition exists to close - the owner dying inside the window
            # between merge and label-drop, with nothing left to notice it.
            # This tick only REPORTS the inconsistency; it never edits
            # labels itself. The wake this produces reaches issue-orch,
            # which clears its own label on wake, per the #42 instruction.
            if i["work_state"] == "LANDED" and i["state"] == "CLAIMED":
                needs_attention += 1
                notes.append(f"{slug}#{n}: landed but still claimed")
                conds.append({"slug": slug, "issue": n, "cond": 7,
                              "orch_alive": i["orch_alive"]})

        # 8. Set-level consolidation wake. Fires per REPO, not per issue -
        # it is the escape hatch for the deadlock where no issue anywhere
        # carries agent-ready and repo-orch is the only actor that can ADD
        # that label: conditions 1-7 are all predicates over a SINGLE
        # issue's movement, so when every issue is quiet none of them can
        # fire, ever, and nothing spawns. This condition instead asks
        # whether the REPO's open issues have been looked at at all.
        #
        # Do NOT simplify this to "agent-ready count is zero". That would
        # fire forever on a repo whose backlog is genuinely finished -
        # every issue closed or otherwise done, zero ready, zero uncovered,
        # and nothing wrong. The condition is about issues nobody has
        # CONSIDERED, not about the ready count; a finished backlog has no
        # uncovered issues left to name, so it goes quiet on its own, while
        # "ready count is zero" would never stop firing.
        #
        # It self-suppresses because a consolidation pass records BOTH
        # nominations and declines in its `covered` list (see
        # core.consolidated_coverage / feed's `considered`) - an issue read
        # and judged unrelated still goes covered, same as one nominated.
        # If a decline recorded nothing, an uninteresting issue would never
        # go covered and this condition would spin forever on it.
        #
        # THE SEAM, explicit because it was the bug: this reads the
        # REPO-LEVEL `r["unconsidered"]` list, built by feed.repo_json from
        # ALL open issues (world.issues) - it does NOT read `r["issues"]`.
        # `r["issues"]` is built from world.candidates(), which returns ONLY
        # issues already carrying agent-ready or agent-working - i.e. the
        # exact complement of the set this condition exists to watch. Before
        # this fix, condition 8 iterated `r["issues"]` and so could only ever
        # fire on an issue that was ALREADY labelled - the same issues
        # condition 1 already covers - so it fired only when condition 1
        # would fire anyway, and never on the deadlock it was built for (29
        # open issues, zero labels, candidates() == [], r["issues"] == []).
        # Do NOT "simplify" this back to `r["issues"]` - that reintroduces
        # exactly that bug. `r["unconsidered"]` already excludes claimed
        # (agent-working) issues - see feed.repo_json's `unconsidered`
        # computation for where the real set comes from and why.
        uncovered = r["unconsidered"]
        if uncovered:
            needs_attention += 1
            sample = ", ".join(f"#{n}" for n in uncovered[:3])
            notes.append(f"{slug}: {len(uncovered)} issue(s) not "
                         f"consolidated since filing ({sample})")
            # ONE dict per repo, not one per issue - this is a set-level
            # condition, and route() already collapses a slug that fired
            # under several conditions to one spawn, so one dict is all
            # route() needs to add this repo to the spawn set.
            #
            # "issue": None - the condition is about the SET, so it names
            # no single issue. route() keys on `slug`, which is present
            # here, so this still routes correctly; route() only skips a
            # dict on a falsy slug, never on a falsy issue (see condition 4
            # for the existing repo-less/issue-less shape it already
            # handles).
            #
            # orch_alive is False: this is a repo-level fact with no single
            # issue's session behind it, so there is no one session whose
            # liveness could answer for the whole set. It is not gated on
            # liveness for the same reason - a live issue-orch working some
            # OTHER issue in this repo does not resolve the set being
            # unconsidered; only a consolidation pass does that.
            conds.append({"slug": slug, "issue": None, "cond": 8,
                          "orch_alive": False})

        # 9. Orphaned PR: open, on an issue-<n> branch, no longer surfaced by
        # any label/condition path. A condition and not merely a digest
        # input, because needs_attention is what tells a human or an
        # on-demand dashboard-op invocation that there is anything to look
        # at at all -- a changed digest with no condition raises it for
        # nobody, so the orphan would still reach no human (orch#279
        # consequence 3).
        #
        # Two reasons, two different facts (core.World.orphan_prs, orch#279
        # then orch#352's correction):
        #
        #   closed     The issue is genuinely gone. No open issue to spawn
        #              issue-orch against, so this is information only -- a
        #              repo-level, issue-less fact (orch_alive False, no
        #              session's liveness stands in for it; see condition 8's
        #              comment for the same shape).
        #
        #   unlabelled The issue is still OPEN -- orphan_prs builds this row
        #              by reading it out of self.issues, which is
        #              `--state open`, so "unlabelled" is open BY
        #              CONSTRUCTION. It merely lost its label, which is
        #              exactly what issue-orch releases as it lands, so
        #              nothing else re-arms it. That makes it spawnable, and
        #              route() needs the issue number to spawn against, so
        #              this half emits ONE dict PER ISSUE (not per repo)
        #              carrying it.
        #
        # `stuck` (orch#440: agent-stuck issue, open PR) is deliberately NOT
        # its own branch here -- `closed_n`'s test is `!= "unlabelled"`, not
        # `== "closed"`, so `stuck` already falls into the non-spawning side
        # without any code change. That is the correct routing: like
        # `closed`, there is no agent move here, only a human's (repo-orch's
        # red line forbids nominating over agent-stuck), so it must produce
        # an issue:None row, never a spawn against the stuck issue.
        #
        # A missing/unknown reason falls back to the same non-spawning side
        # deliberately: spawning on an unverified reason is the dangerous
        # direction (a phantom issue-orch against nothing), while
        # under-reporting merely repeats the orch#279 failure this condition
        # already guards against with a note. isinstance-guarded per row,
        # same tolerance tui_model.zone_a and _orphan_pr_reasons apply -- a
        # malformed row must not take down the tick for every repo.
        orphaned = r.get("orphan_reasons") or []
        closed_n = sum(1 for o in orphaned
                       if isinstance(o, dict) and o.get("reason") != "unlabelled")
        unlabelled = [o for o in orphaned
                      if isinstance(o, dict) and o.get("reason") == "unlabelled"]
        if orphaned:
            needs_attention += 1
            sample = ", ".join(f"#{o['number']}" for o in orphaned[:3]
                                if isinstance(o, dict))
            notes.append(f"{slug}: {len(orphaned)} open PR(s) nothing will "
                         f"surface ({sample})")
            # ONE append call site for cond 9 (test_conformance's
            # conditions_are_uniquely_numbered_no_dupes greps source text for
            # cond-number literals and requires each number appear exactly
            # once) -- so the closed/unlabelled split happens in the ISSUE
            # values fed into one shared append, not in two separate append
            # call sites each spelling out the number.
            cond9_issues = ([None] if closed_n else []) + \
                [o["issue"] for o in unlabelled if o.get("issue") is not None]
            for issue in cond9_issues:
                conds.append({"slug": slug, "issue": issue, "cond": 9,
                              "orch_alive": False})

    # 4. Oracle failure: gh read failed for a repo, or the tick itself is
    # stale - the error-alert set (feed's alerts with level == "error").
    if thin["error_alerts"]:
        needs_attention += len(thin["error_alerts"])
        notes.extend(f"error alert: {a}" for a in thin["error_alerts"])
        # Condition 4 is repo-less by nature: the error-alert set is not
        # attached to any one repo. slug/issue are None so route() can never
        # turn it into a spawn target.
        conds.extend({"slug": None, "issue": None, "cond": 4,
                      "orch_alive": False} for _ in thin["error_alerts"])

    return needs_attention, notes, conds


# === routing: the mechanical half of the old dashboard-op table =============
# Moved here from agents/dashboard-op.md per docs/dashboard-op-split.md. Only
# the MECHANICAL rows moved. This changes WHERE the decision is made, never
# WHAT any level is allowed to do - DENY_BY_ROLE in core.py is untouched.
#
# The tick is the ORIGIN of every wake in this system, so it must stay cheap:
# route() reads ONLY facts compute_conditions already computed, in memory, in
# the same call chain. No gh read, no subprocess, no filesystem scan.

# Conditions that mean "spawn this repo's repo-orch".
#
# 9 belongs here now (orch#387): both halves spawn the SLUG, not the issue.
# A closed-half row carries issue: None, but the spawn target was always
# repo-orch on the repo slug - repo-orch, not the tick, resolves what to do
# once it wakes (issue-orch against the unlabelled issue, or nothing but a
# human decision for the closed one). Before orch#387, the closed half fired
# condition 9 for six consecutive wakes on a real PR (#388) with no route to
# a spawn, and a human had to merge it by hand. See route()'s docstring for
# the two halves' remaining difference (what repo-orch can do once woken).
SPAWN_CONDS = (1, 5, 7, 8, 9)


def route(conds):
    """conds: compute_conditions' third value. Returns a sorted list of repo
    slugs that should get `repo-orch <slug>` spawned. Pure - no side effects,
    no I/O.

    Conditions 1, 5, 7, 8 and 9 spawn outright: each already encodes its own
    liveness test (`startable` for 1, `not orch_alive` for 5) or deliberately
    does not want one (7 - see its comment; 9 likewise, see below). Do NOT
    add a second orch_alive check to those; that would re-open the races
    those comments describe.

    Condition 8 also needs no liveness test, for a different reason than 7:
    it is repo-level, not issue-level - there is no single issue's session
    whose liveness could stand in for the whole set being unconsidered. A
    live issue-orch grinding on some OTHER issue in the repo does not
    resolve the set-level fact this condition reports, so gating it on any
    one issue's orch_alive would be answering the wrong question. It always
    carries orch_alive: False (see its comment in compute_conditions) and
    that is not a liveness test to skip - it is a "no single session
    applies" marker.

    Condition 2 (blocked) is the one row that needs the liveness test applied
    HERE. The old dashboard-op table read "alive -> skip, dead -> spawn", but
    condition 2 above fires on work_state == "BLOCKED" alone. So the skip
    lives in the router, on the orch_alive carried in the dict.

    Conditions 3, 4 and 6 never spawn. Each for a different reason, and none
    of them is a spawn-suppression detail to be tidied away:

      3. Contended. repo-orch holds no kill and no labels, so it CANNOT
         clear contention. Spawning one into a contended issue adds a third
         actor to a two-actor fault. Do NOT "simplify" this to a spawn with
         a note; there is nothing the spawned session could do.

      4. Oracle failure. The world is unreadable - a gh read failed for a
         repo, or the tick itself is stale. The facts a repo-orch would
         reason over did not load, so anything it concluded would be built
         on an unread world. Note the matching rule in should_spawn: the
         tick must NOT record a suppression for condition 4 either. An
         unreadable world has to keep waking, loudly, until it is readable.

         orch#174 (SPAWN_FLOOR_SECS): condition 4 carries slug=None (see its
         compute_conditions comment), so it can never reach should_spawn's
         records dict, floor included - it is exempt from the floor for the
         same structural reason it is exempt from the ACK_TTL_MINS
         suppression above. It also never reaches a `wake_gate` floor: that
         function does not exist in this codebase (deleted in d30c57d, the
         automatic dashboard-op wake removal) - what the issue calls "the
         dashboard wake" is now just needs_attention/notes/error_alerts
         flowing into public/status.json, rebuilt in full every tick with no
         suppression mechanism of any kind. Condition 4 reaches that output
         unfloored because there is nothing left there to floor it.

      6. Wedged. Judging a live-but-idle session as wedged rather than
         merely SLOW is a judgment over a journal tail, not a predicate over
         a field - by row count the two are identical, and only reading the
         tail tells them apart (docs/dashboard-op-split.md section 3). The
         split keeps that agentic. It is information only. The tick never
         kills anything, ever.

    Condition 9 (orphaned PR) DOES spawn, on the repo SLUG, regardless of
    which half fired (orch#387). SPLIT by reason (orch#279 then orch#352's
    correction, then orch#387's correction of THAT) - the two halves are
    still different facts, but route()'s only job is picking a spawn
    target, and the target was never the issue - it is repo-orch on the
    slug, which then resolves what to do once it wakes:

      closed:     the issue is genuinely gone. repo-orch's per-issue spawn
                  verb (issue-orch) has nothing to spawn against, so a
                  woken repo-orch can only raise this to a human (merge it,
                  close it, reopen the issue) - but that raise has to be
                  scheduled by something, and before orch#387 nothing
                  scheduled it: this half's dict carries issue: None and
                  fell through route() unspawned, so a closed-issue orphan
                  could fire condition 9 forever with no session ever
                  landing it (live case: PR #388, six wakes, merged by
                  hand). Spawning repo-orch here does not make the tick
                  merge anything - it makes a session exist that can.

      unlabelled: the issue is still OPEN - orphan_prs (core.py) builds
                  this row by reading it straight out of self.issues, which
                  is `--state open`, so it is open BY CONSTRUCTION. It only
                  lost its label (exactly what issue-orch releases as it
                  lands), so there IS an issue to spawn issue-orch against,
                  and letting it sit is the orch#340 failure this
                  correction exists for: an open PR on an open issue,
                  unmerged all night, because nothing picked it up.

         A missing/unknown reason still falls back to the closed shape
         (issue: None) - see compute_conditions' comment on cond9_issues.
         That no longer changes whether it spawns (both halves do), only
         what repo-orch finds when it looks.

    One slug under several spawn-worthy conditions yields ONE spawn.
    """
    slugs = set()
    for c in conds:
        slug = c.get("slug")
        if not slug:
            # Condition 4 and anything else repo-less. Never a spawn target.
            continue
        cond = c.get("cond")
        if cond in SPAWN_CONDS:
            slugs.add(slug)
        elif cond == 2 and not c.get("orch_alive"):
            slugs.add(slug)
    return sorted(slugs)


# === the tick's own spawn record (replaces the agent-performed ack) =========

def spawn_record_path():
    return STATE / "tick-spawns.json"


def read_spawn_records():
    """slug -> {"digest": str, "at": float}. Missing or corrupt file -> {}.

    MUST NOT raise: a record that cannot be read has to look like "no
    record", which means SPAWN, never silence. Failure degrades to loud
    repetition, not to a suppression nobody can see.
    """
    try:
        raw = json.loads(spawn_record_path().read_text())
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for slug, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        digest = rec.get("digest")
        at = rec.get("at")
        if not isinstance(digest, str) or not digest:
            continue
        if not isinstance(at, (int, float)):
            continue
        out[slug] = {"digest": digest, "at": float(at)}
    return out


def write_spawn_record(slug, digest):
    """Upsert this slug's record with core.now(). Whole dict written back."""
    if not isinstance(digest, str) or not digest:
        # No digest to key a suppression to -> record nothing, so the next
        # tick spawns again. Never write a record that can't be compared.
        return
    records = read_spawn_records()
    records[slug] = {"digest": digest, "at": core.now()}
    spawn_record_path().parent.mkdir(parents=True, exist_ok=True)
    spawn_record_path().write_text(json.dumps(records, sort_keys=True,
                                              separators=(",", ":")))


def should_spawn(slug, per_repo_digest, records):
    """Suppress iff we have a record for this slug AND its digest equals the
    repo's CURRENT per-repo digest AND that record is younger than
    ACK_TTL_MINS. Otherwise spawn.

    SPAWN_FLOOR_SECS (orch#174) is checked FIRST, before the digest
    comparison, and unconditionally suppresses if the last record for this
    slug is younger than the floor - even on a changed digest. This is the
    agentic-spacing half of the cadence split: ORCH_TICK_SECS controls how
    often the world is DERIVED (cheap, read-only), SPAWN_FLOOR_SECS controls
    how often a slug may actually be SPAWNED INTO (expensive, a model
    session). Moving the derivation cadence to 60s must not also move the
    agentic spacing to 60s; this floor is what keeps them independent.

    This preserves the old ack's lease semantics without asking an agent to
    echo a hash back: a standing human-owed condition goes quiet without
    going invisible (the condition still re-derives and still shows on the
    widget every tick), and the TTL bounds a WRONG suppression instead of
    letting it seal itself. Without expiry, a wrong suppression would
    suppress the very spawn that would reveal the mistake, and a static
    world never changes the digest to break the seal on its own.

    CRITICAL: per_repo_digest comes from per_repo_digests(thin), landed by
    #96 - NEVER the global digest_of(thin). The global digest hashes every
    repo, so it flips whenever ANY repo moves; a repo frozen for hours would
    never see a repeat and the suppression could never be satisfied. #96
    measured 64 distinct global digests against 11 real state changes for one
    repo. Keying this suppression to the global digest would reproduce that
    same defect one level down, inside the tick, where no agent is present to
    notice it.

    Checked FIRST, before the per-slug record: core.wave_backoff() is
    suppress-only (orch#184) -- a detected mass-death wave (account usage
    limit killing every in-flight session near-simultaneously) means new
    spawns would just die into the same wall, so every slug is held back
    for the wave's remaining backoff regardless of its own digest/ack
    state. This can only ever turn a True into a False, never the reverse.
    """
    if core.wave_backoff() is not None:
        return False
    rec = records.get(slug)
    if not rec:
        return True
    if core.now() - rec.get("at", 0) < SPAWN_FLOOR_SECS:
        return False
    if rec.get("digest") != per_repo_digest:
        return True
    return (core.now() - rec.get("at", 0)) > ACK_TTL_MINS * 60


# === lease expiry — the tick's one write ====================================
# Mechanical: core.lease_expired is a pure function of already-fetched
# timestamps (see its docstring and claim_age_mins'). This pass only calls
# it and, on True, performs the SAME two-step every existing comment+label
# call site uses (see e.g. core.a_review_now / server.py's automerge
# toggle): comment FIRST, label second, in that literal order and as two
# separate writes rather than one combined step.
#
# Order is load-bearing, not stylistic. Comment and label ride ONE
# channel (the forge), so if the comment write fails, the label write
# never runs, and the issue stays CLAIMED and loud in the tick log with
# the failure reason attached -- instead of silently reaching ABANDONED
# with no recorded reason a human could read on the issue itself. The
# reverse order (label first) would let a comment failure leave an issue
# already flipped to agent-stuck with no explanation visible anywhere but
# this process's own log.

def lease_expire_pass(data):
    """For every open issue in `data` (the feed's own repo/issue rows) whose
    lease has expired, build a fresh core.World for that repo, comment, then
    relabel agent-working -> agent-stuck. DRY_RUN logs and changes nothing.

    A fresh World per repo (not the one feed.build() already loaded
    internally) is used because feed.build() does not expose its World
    objects upward -- this is the one extra read per TRACKED repo per tick,
    same cost class as the repo-level lookups build() already does once per
    repo (core.repo_auto_land, core.repo_in_flight_cap), not once per issue.
    """
    for r in data.get("repos", []):
        if r.get("state") != "tracked" or not r.get("ok"):
            continue
        slug = r.get("slug") or r.get("repo")
        path = r.get("path")
        if not slug or not path:
            continue
        numbers = [i.get("issue") for i in r.get("issues", []) if i.get("issue")]
        if not numbers:
            continue
        world = core.World()
        # World.load takes "owner/repo", never the bare slug: it resolves the
        # repo entry via _repo_entry_for_owner_slug, and THAT is what picks the
        # backend adapter. Handed a bare slug the lookup returns None, the
        # adapter falls back to its `gh` default, and every tea-backed repo
        # fails to load -- observed 2026-09-17 as "could not load
        # eva-backgrounds, skipping" on every tick, which silently disabled
        # this whole pass for that repo. `slug` above stays the log/scope name.
        if not world.load(r.get("repo") or slug):
            log(f"lease pass: could not load {slug}, skipping")
            continue
        for n in numbers:
            if not core.lease_expired(world, n, path):
                continue
            age = core.claim_age_mins(world, n)
            body = (
                "orch/tick lease-expired\n\n"
                f"agent-working has held this issue for {age:.0f} minutes "
                f"with no observable progress, past LEASE_TTL_MINS="
                f"{core.LEASE_TTL_MINS}. Derived from one timestamp only "
                f"(the `agent-working` labeled-event time on this issue's "
                f"timeline) -- no content was read or judged.\n\n"
                "If this work is still wanted, a human should re-open or "
                "re-nominate it (agent-stuck -> agent-ready)."
            )
            if DRY_RUN:
                log(f"DRY: would expire lease on {slug}#{n} (age={age:.0f}m)")
                continue
            argv, stdin = world.adapter.issue_comment_call(n, body)
            ok, out = core._run(argv, cwd=path, input_text=stdin)
            if not ok:
                log(f"lease expiry comment failed on {slug}#{n}: {out}")
                continue
            ok, out = core._run(
                world.adapter.issue_edit_add_label(n, core.L_STUCK), cwd=path)
            if not ok:
                log(f"lease expiry add-label failed on {slug}#{n}: {out}")
                continue
            ok, out = core._run(
                world.adapter.issue_edit_remove_label(n, core.L_WORKING), cwd=path)
            if not ok:
                # agent-stuck landed, agent-working did not come off: the
                # issue carries both. Loud, and not reported as success --
                # the next tick's add is idempotent and retries the remove.
                log(f"lease expiry remove-label failed on {slug}#{n}: {out}")
                continue
            # Reflect the flip in the rows this tick will write, so
            # status.json and the journal do not spend a full cycle still
            # reading CLAIMED for an issue already relabeled on the forge.
            _mark_expired(r, n)
            log(f"lease expired: {slug}#{n} age={age:.0f}m >= "
                f"{core.LEASE_TTL_MINS}m -> agent-stuck")


def _mark_expired(repo_row, n):
    """Apply the agent-working -> agent-stuck flip to this tick's in-memory
    issue row, matching what feed.build() would have produced had the label
    already been set when it read the repo."""
    for i in repo_row.get("issues", []):
        if i.get("issue") != n:
            continue
        labels = [l for l in i.get("labels", []) if l != core.L_WORKING]
        if core.L_STUCK not in labels:
            labels.append(core.L_STUCK)
        i["labels"] = labels
        i["state"] = "ABANDONED"  # core.issue_state over the new labels
        i["ready"] = False


# === void claim — the tick's other write =====================================
# Same shape as lease_expire_pass above, and the same load-bearing ordering:
# comment FIRST, then remove agent-working. Unlike the lease pass this adds
# NO label -- a void claim goes back to unclaimed, not to agent-stuck (see
# core.void_claim's docstring for why). If the comment write fails, abort
# before the label write, for the identical reason lease_expire_pass does:
# comment and label ride one channel (the forge), so a failed comment must
# not be followed by a silent label change with no reason recorded on the
# issue itself.

def void_claim_pass(data):
    """For every open issue in `data` whose agent-working claim is void
    (core.void_claim -- nothing was ever attempted, see its docstring),
    comment then drop agent-working, returning the issue to the pool.
    DRY_RUN logs and changes nothing.

    A fresh World per repo, same reasoning as lease_expire_pass: feed.build()
    does not expose its World objects upward."""
    for r in data.get("repos", []):
        if r.get("state") != "tracked" or not r.get("ok"):
            continue
        slug = r.get("slug") or r.get("repo")
        path = r.get("path")
        if not slug or not path:
            continue
        numbers = [i.get("issue") for i in r.get("issues", []) if i.get("issue")]
        if not numbers:
            continue
        world = core.World()
        # "owner/repo", not the bare slug -- see the lease pass above.
        if not world.load(r.get("repo") or slug):
            log(f"void claim pass: could not load {slug}, skipping")
            continue
        for n in numbers:
            if not core.void_claim(world, n, path):
                continue
            body = (
                "orch/tick void-claim\n\n"
                "agent-working was held on this issue, but the session's "
                "own log recorded no report and the ledger shows no prior "
                "run and no commits on its branch -- nothing was ever "
                "attempted. Returning this issue to the pool.\n\n"
                "If this claim was live and this is wrong, re-add "
                "agent-working."
            )
            if DRY_RUN:
                log(f"DRY: would void claim on {slug}#{n}")
                continue
            argv, stdin = world.adapter.issue_comment_call(n, body)
            ok, out = core._run(argv, cwd=path, input_text=stdin)
            if not ok:
                log(f"void claim comment failed on {slug}#{n}: {out}")
                continue
            ok, out = core._run(
                world.adapter.issue_edit_remove_label(n, core.L_WORKING), cwd=path)
            if not ok:
                log(f"void claim remove-label failed on {slug}#{n}: {out}")
                continue
            _mark_voided(r, n)
            log(f"void claim: {slug}#{n} -> unclaimed (no report, no commits, "
                f"no prior runs)")


def _mark_voided(repo_row, n):
    """Apply the agent-working removal to this tick's in-memory issue row,
    matching what feed.build() would have produced had the label already
    come off when it read the repo. No label added -- core.issue_state reads
    UNCLAIMED once L_WORKING is gone and L_STUCK was never set."""
    for i in repo_row.get("issues", []):
        if i.get("issue") != n:
            continue
        i["labels"] = [l for l in i.get("labels", []) if l != core.L_WORKING]
        i["state"] = "UNCLAIMED"  # core.issue_state over the new labels
        # `ready` is deliberately NOT touched: feed.issue_json derives it as
        # `core.L_READY in labels` -- purely from agent-ready, which voiding
        # does not remove. _mark_expired sets ready=False because an
        # agent-stuck issue really does lose readiness on the next build;
        # copying that line here would contradict the labels this function
        # just wrote. An issue carrying agent-ready AND agent-working would
        # otherwise render for one whole tick as unclaimed-but-not-ready and
        # not startable -- an issue just returned to the pool, shown as if it
        # were not in it, which is the very queue-shrinkage symptom orch#362
        # is about.
        #
        # `startable` IS recomputed, on the same expression feed.py:265 uses,
        # because it reads `state` and this function just changed it.
        i["startable"] = (bool(i.get("ready"))
                          and not bool(i.get("orch_alive")))


# === main ====================================================================

def main():
    PUBLIC.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)

    # 1. .tick.lock, non-blocking. Held -> skip.
    lock_fh = open(TICK_LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("tick already running, skip")
        return 0

    # 2. Build the world. feed.build() does the per-repo World.load and the
    # abort-repo-on-failed-read stance.
    try:
        data = feed.build()
    except Exception as e:
        (STATE / "feed.err").write_text(str(e))
        log("feed failed (see state/feed.err)")
        return 1

    # 2b. Lease expiry. Runs on the `data` this tick just built, BEFORE the
    # digest/status.json write below, so a flip this tick is reflected in
    # this tick's own dashboard rather than lagging a full cycle behind.
    # See lease_expire_pass's own docstring for the write ordering
    # (comment-then-label) and DRY_RUN handling.
    lease_expire_pass(data)

    # 2c. Void claims. Same placement and reasoning as the lease pass above:
    # runs on this tick's `data`, before the digest/status.json write, so a
    # claim returned to the pool shows up in this tick's own dashboard.
    # Ordered AFTER lease_expire_pass deliberately -- that pass may set
    # agent-stuck, which core.void_claim treats as terminal and skips, so an
    # issue expired this tick is never also voided in the same pass.
    void_claim_pass(data)

    # 5. Conditions + digest (computed before step 4's journal decision, so
    # step 4 can journal on the same digest).
    #
    # This runs BEFORE step 3's write because the feed publishes
    # needs_attention, and only compute_conditions can produce it:
    # build_thin/compute_conditions consume the built feed, so feed.build()
    # cannot derive it itself without a cycle. Writing status.json first and
    # computing after is what left tick.needs_attention null on every tick
    # (#226). Note tick_json() reads status.json's mtime during
    # feed.build() above — it is reading the PREVIOUS tick's output, so
    # deferring this tick's write does not perturb staleness.
    thin = build_thin(data)
    digest = digest_of(thin)
    needs_attention, notes, conds = compute_conditions(data, thin)

    # 3. Rebuild public/status.json and public/widget.html. needs_attention
    # is injected rather than computed in feed.build() per the cycle above;
    # it is always an int, so a client can tell "0 things need you" from a
    # stale/absent field instead of reading both as null.
    data["tick"]["needs_attention"] = needs_attention
    # Write-then-rename, not a bare write_text: a bare write truncates the
    # file at the START, so a reader (the /events SSE loop, GET /status.json)
    # stat()-ing mid-write sees the new mtime attached to a half-written
    # prefix. Worse, /events latches that mtime and never re-reads, so the
    # page goes stale until the next tick. os.replace is atomic on POSIX --
    # same filesystem required, hence the tmp file living next to FEED_PATH
    # rather than in /tmp -- so a reader only ever sees a complete file.
    # Distinct name (not widget.html.tmp): build_widget owns that one path
    # and server.py's __main__ already documents a race on it; ticks don't
    # run concurrently (TICK_LOCK) but there's no reason to share a name.
    tmp_path = FEED_PATH.with_suffix(".json.tick.tmp")
    try:
        tmp_path.write_text(json.dumps(data, separators=(",", ":")))
        os.replace(tmp_path, FEED_PATH)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        subprocess.run(
            [sys.executable, "-m", "orch.build_widget"],
            cwd=str(ORCH_HOME), capture_output=True, timeout=30,
        )
    except Exception:
        log("widget build failed")  # non-fatal, tick continues

    # 4. Journal observation, only when the digest changed - "still nothing"
    # is noise the next session has to read past. This digest-file gates
    # ONLY this journaling decision; it is NOT the spawn-suppression digest
    # (see should_spawn above) - keep the two separate.
    prev_observed = STEP4_DIGEST_FILE.read_text().strip() if STEP4_DIGEST_FILE.exists() else ""
    if digest != prev_observed:
        STEP4_DIGEST_FILE.write_text(digest)
        for r in data.get("repos", []):
            slug = r.get("slug") or r.get("repo")
            if not slug:
                continue
            core.journal_append("repo", slug, None, "tick", "observed", {
                "digest": digest,
                "issues": [{"number": i.get("issue"),
                            "state": i.get("state"),
                            "work_state": i.get("work_state")}
                           for i in r.get("issues", [])],
            })

    # 4b. Wave backoff (orch#184): record ONCE per tick, not per repo -- a
    # mass-death wave is machine-wide, not scoped to any one repo, so this
    # uses the "dashboard" journal scope like the tick's own other
    # machine-wide bookkeeping. Reads _wave_backoff_detail once (not
    # wave_backoff twice) so the count/window that triggered it can be
    # recorded without re-scanning SESSIONS_DIR a second time this tick.
    # Only written while a backoff is actually active -- an inactive tick
    # has nothing to record, same "no noise for nothing happening" stance
    # as the digest gate just above.
    wave_detail = core._wave_backoff_detail()
    if wave_detail is not None:
        core.journal_append("dashboard", None, None, "tick", "wave-backoff", {
            "remaining_secs": round(wave_detail["remaining"]),
            "n": wave_detail["n"],
            "window_secs": wave_detail["window"],
        })

    # 4c. Token spend (orch#185): record ONCE per tick, machine-wide -- spend
    # is not scoped to any one repo, same "dashboard" journal scope as the
    # wave-backoff block just above. Reuses the figure feed.build() already
    # computed into data["host"]["spend"] (via host_json()) rather than
    # calling spend_window() again -- same reasoning as 4b's comment: this
    # scan is 1.57s over 5,736 files, so a second call doubles the tick's
    # cost for the same answer. host_json() remains the single source that
    # actually computes it; this block only reads what it already built.
    # Only written when spend_window() found something to report -- None
    # means no transcripts matched the window, not zero spend, so there is
    # nothing to journal (same "no noise for nothing happening" stance as
    # the digest gate and 4b above).
    spend = data.get("host", {}).get("spend") if isinstance(data.get("host"), dict) else None
    if spend is not None:
        core.journal_append("dashboard", None, None, "tick", "spend", {
            "window_secs": spend["window_secs"],
            "roles": spend["roles"],
            "total": spend["total"],
        })

    history_row = {"at": data.get("generated", core.now_iso()), "digest": digest,
                    "needs_attention": needs_attention}
    with HISTORY_PATH.open("a") as fh:
        fh.write(json.dumps(history_row, separators=(",", ":")) + "\n")

    # 5b. Mechanical routing. The common path spawns repo-orch with no Claude
    # session in between. per_repo is computed ONCE, from the `thin` already
    # built above: a second build_thin() call can straddle an idle-threshold
    # flip (see per_repo_digests' comment) and emit digests that disagree
    # with the ones the suppression is keyed to.
    per_repo = per_repo_digests(thin)
    records = read_spawn_records()
    for slug in route(conds):
        if not should_spawn(slug, per_repo.get(slug), records):
            # Fed, and its own state has not moved. The mechanical path is
            # out of predicates here.
            continue
        # A repo-orch is already working this repo. Double-spawning onto live
        # work is the exact bug condition 1's comment warns about.
        if core.alive(core.key_for("repo-orch", slug)):
            continue
        fired = sorted({c["cond"] for c in conds if c.get("slug") == slug})
        reason = (f"tick routed {slug}: conditions "
                  f"{', '.join(str(c) for c in fired)} fired")
        # Conditions 5 and 9 are the landing-relevant ones (see route()'s
        # docstring): repo-orch wakes with only this condition-number list
        # and would otherwise have to rediscover on its own that finished
        # work may be waiting to land. One short clause, not a rewrite of
        # the reason's shape - the lander is issue-orch via the
        # issue-landing skill.
        if 5 in fired or 9 in fired:
            reason += "; finished work may be waiting to land (issue-orch via issue-landing skill)"
        if DRY_RUN:
            log(f"DRY: would spawn repo-orch {slug} ({reason})")
            continue
        # One repo's spawn failure must not abort the tick for the others -
        # the tick is the origin and always completes. No record is written
        # for a failed spawn: an unrecorded spawn simply retries next tick,
        # whereas a recorded failure would suppress silently.
        try:
            core.spawn("repo-orch", (slug,), reason)
        except Exception as e:
            log(f"spawn repo-orch {slug} failed: {e}")
            continue
        write_spawn_record(slug, per_repo.get(slug))
        log(f"spawned repo-orch {slug} ({reason})")

    log(f"needs_attention={needs_attention} notes={notes[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
