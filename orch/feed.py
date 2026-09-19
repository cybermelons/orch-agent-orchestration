#!/usr/bin/env python3
"""The feed: everything, at every level. tick.py calls build() -> dict.

    repos -> issues -> sessions

Plus agent_sessions (the ledger, keyed) and unattached_sessions (transcripts
matching no key) — where work goes missing. Every field is read from git, gh,
the filesystem, or a ledger row/journal. No model turns spent here.
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from orch import core
from orch.core import (NUDGE_IDLE_MINS, ORCH_HOME, World, alive, base_ref, gh_repo,
                        issue_branch, issue_dir, issue_state, journal_stats, journal_tail,
                        key_for, ledger_log_path, ledger_read, live_issue_orchs,
                        now, now_iso, prior_runs,
                        repo_backend, repo_slug, sessions_for, work_state)

# role -> dot-component count of a CURRENT key (key_for output split on ".").
# A rolled-aside row's stem is "<key>.<iso-ts>" — the iso timestamp carries no
# dots (now_iso() uses timespec="seconds"), so it always adds exactly one
# component. This is how current rows are told apart from rolled ones without
# naively splitting on "." (keys themselves contain dots).
_KEY_ARITY = {"dashboard-op": 1, "repo-orch": 2, "issue-orch": 3}

def _run(cmd, timeout=None):
    import subprocess
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, ""


def _covered_after(created_at, cov_at):
    """True iff `cov_at` (a coverage map's `at` string) is strictly NEWER than
    `created_at` (an issue's own createdAt). Degrades to False -- not covered
    -- on any missing/unparseable input, NEVER raises.

    The ONE comparison both issue_json's `considered` and repo_json's
    `unconsidered` computation use -- factored out so the two cannot drift
    into subtly different definitions of "covered". See issue_json's
    docstring-comment below for the timestamp-shape reasoning (gh/tea emit
    "...Z", core.now_iso() emits a numeric UTC offset; naive string
    comparison sorts them wrong)."""
    try:
        if not created_at or not cov_at:
            return False
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        cov_dt = datetime.fromisoformat(cov_at.replace("Z", "+00:00"))
        return cov_dt > created_dt
    except (ValueError, TypeError, AttributeError):
        return False


# --- one issue -----------------------------------------------------------------

def issue_json(world, repo, n, slug, gr, is_gh, web_base, coverage,
               repo_auto_land_default, names=None):
    labels = [l["name"] for i in world.issues if i["number"] == n for l in i.get("labels", [])]
    key = key_for("issue-orch", slug, n)
    orch_alive = alive(key)
    # The claim's journalled lease, for display (orch#228): which host holds
    # this issue and how long its lease still runs. Reads only comment
    # bodies World.load already fetched, so it costs no round trip. Whole
    # thing is None when nothing readable exists -- never a guess, and never
    # an empty-looking holder that a viewer could read as "nobody".
    claim_holder = None
    _lease = core.claim_lease(world, n)
    if _lease is not None:
        _host, _expiry = _lease
        claim_holder = {
            "host": _host,
            # ISO8601, not the raw unix float _parse_iso8601 hands back: every
            # other timestamp this feed exposes is a minute count or an ISO
            # string, and a bare epoch float is not something the dashboard
            # can render without a conversion it does not do.
            "until": datetime.fromtimestamp(_expiry).astimezone().isoformat(
                timespec="seconds"),
            # Minutes left, negative once lapsed. Signed on purpose: "-40"
            # says the lease went stale 40 minutes ago, which is the fact
            # an operator wants, where a floor at 0 would render a long-dead
            # claim identically to one expiring this second.
            "mins_left": int((_expiry - core.now()) // 60),
            "local": _host == core.this_host(),
        }
    wt = issue_dir(n, slug)
    # Resolved, not assumed to be issue-<n>: orch#329, a suffixed sibling
    # branch can carry the live PR while issue-<n> only has an old merged one.
    br = world.branch_for_issue(n)

    # -1 is "we could not count", NOT "zero commits". 0 is the value an
    # operator acts on ("the agent produced nothing"), and it is also what a
    # failed or timed-out rev-list yields, so the two must not share a value.
    # Same precedent as `idle` three lines below, which already emits -1 for
    # an unreadable mtime. A real zero still reports 0.
    base = base_ref(repo)
    commits = -1
    if base:
        ok, out = _run(["git", "-C", str(repo), "rev-list", "--count", f"{base}..{br}"])
        commits = int(out.strip()) if ok and out.strip().isdigit() else -1

    mt = core.work_mtime(repo, br)
    idle = (now() - mt) // 60 if mt is not None else -1

    updated_at = world.issue_field(n, "updatedAt")
    updated_ts = int(datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()) \
        if updated_at else None

    # considered: True iff a `consolidated` journal row NEWER than this
    # issue's own createdAt lists its number. A row written before the issue
    # existed cannot have considered it -- that asymmetry is what makes the
    # condition re-arm the instant a new issue is filed, and only then, so
    # comparing the wrong way (or not at all) either never fires or never
    # re-arms. `coverage` is the {issue_number: newest_at} map -- resolved
    # ONCE per repo by repo_json (see the comment there) and threaded down
    # here as a parameter, same precedent as is_gh/web_base two params over:
    # a per-issue re-read of the whole journal file (consolidated_coverage
    # re-reads it in full, see core.py) would be the exact per-issue cost
    # repo_json already pays once, for every other repo-wide fact, to avoid.
    # createdAt is fetched by the same world read that already yields
    # updatedAt (core.py:702, core.py:1193 both list it in --json), so no new
    # forge field is added here -- just read via world.issue_field like
    # updated_at above.
    #
    # Degrade-to-False, not degrade-to-True: an issue whose filing time is
    # absent or unparseable, or a coverage `at` that fails to parse, must
    # WAKE the operator rather than hide. Reading it as "considered" would be
    # silent suppression of a real gap -- exactly the failure stance
    # consolidated_coverage itself refuses (its own docstring: "failure
    # degrades to loud repetition, not to a suppression nobody can see").
    # Never let this comparison raise out of issue_json.
    # Both sides are ISO-8601 but NOT the same shape: gh/tea emit "...Z" (UTC,
    # zero suffix) for createdAt, while core.now_iso() -- the source of every
    # coverage `at` -- emits datetime.now(tz).astimezone().isoformat(
    # timespec="seconds"), i.e. a real numeric UTC offset, never "Z". Two
    # differently shaped ISO strings do not compare correctly as plain
    # strings, so _covered_after parses both to aware datetimes and compares
    # timestamps -- the same fromisoformat(...replace("Z","+00:00")) approach
    # updated_at already uses a few lines up. Factored into _covered_after so
    # this and repo_json's `unconsidered` computation share ONE comparison
    # and cannot drift into subtly different definitions of "covered".
    created_at = world.issue_field(n, "createdAt")
    considered = _covered_after(created_at, coverage.get(n))

    # transcript half of activity: RECURSIVE (core.transcript_activity), not
    # sessions_for's flat newest — workers are now Agent-tool subagents
    # living INSIDE issue-orch's own process, and they write to
    # <session-id>/subagents/ beneath this cwd's transcript dir. A flat glob
    # would miss them, reading an issue-orch whose subagents are grinding as
    # idle and false-firing condition 6 — this is load-bearing twice over
    # now: it is both how a busy-but-uncommitted issue-orch avoids reading
    # idle, and how its subagent workers (which have no ledger key, no cwd
    # of their own) are seen at all. See DESIGN.md "The six conditions" and
    # core.transcript_activity.
    transcript_mt = core.transcript_activity(wt)
    activity = max(t for t in (mt, transcript_mt, updated_ts) if t is not None) \
        if mt is not None or transcript_mt is not None or updated_ts is not None else None

    sessions = sessions_for(wt, live=orch_alive)
    # Subagent workers hang off each session's own transcript dir (see
    # core.subagents_for). Never let a transcript race break feed build.
    for s in sessions:
        # The guard stays, but the failure is no longer silent: "no workers"
        # and "the walk raised" must not look identical. workers stays a list
        # on every path (consumers index it); workers_unreadable is the
        # explicit unknown, set only on the failure path.
        try:
            s["workers"] = core.subagents_for(wt, s["id"])
            s["workers_unreadable"] = False
        except Exception:
            s["workers"] = []
            s["workers_unreadable"] = True

    # gr is "owner/repo" only -- see repo_json's comment on why it can never
    # be a raw remote URL -- so github.com's fixed, well-known URL shape can
    # always be built from gr alone, but a Gitea instance's web base URL
    # (host, port, scheme) is operator-specific and NOT recoverable from
    # "owner/repo" alone. Do not guess it from the git remote's host either:
    # that host is the git/SSH endpoint, which is not guaranteed to be the
    # same host, port or scheme as the Gitea web UI a human would click
    # through to. Instead, for tea-backed repos, web_base comes from the tea
    # login profile via core.login_web_base (resolved once per repo by the
    # caller, repo_json, keyed on the login repo_login_for(slug) names).
    # When that lookup fails for any reason web_base is "" and stays that
    # way -- an unresolvable base yields no link rather than a guessed one
    # that LOOKS like a URL but 404s or points at the wrong service; both
    # call sites already treat a falsy url/pr_url as "no link available"
    # (see widget.tpl.html). Gitea's pull-request path is "pulls" (PLURAL) where
    # GitHub's is "pull" (singular) -- get this backwards and the link still
    # looks right but 404s silently. That asymmetry now lives in exactly one
    # place, core.RepoAdapter.pr_url, so it cannot be re-derived wrongly at a
    # second call site. is_gh and web_base are both resolved once by
    # the caller (repo_json), not re-derived per issue -- repo_backend()
    # shells out to `git remote get-url` and login_web_base() to `tea`, and
    # every issue in the repo shares the same answer.
    # Built from the values the CALLER already resolved -- no re-derivation
    # and, crucially, no shell-out: core.RepoAdapter is constructed directly
    # here rather than via core.adapter_for(), because adapter_for reads the
    # git remote and (for tea) the tea login table, which is exactly the
    # per-issue cost repo_json paid once to avoid. The adapter owns the
    # pull/pulls asymmetry and the "no base -> no link" rule so neither is
    # spelled out at a call site again.
    be = core.RepoAdapter("gh" if is_gh else "tea", gr, None,
                          core.GH_WEB_BASE if is_gh else web_base)
    pr = world.pr_number_for(br)
    pr_url = be.pr_url(pr)

    ready = core.L_READY in labels
    state = issue_state(world, n)
    # repo_auto_land_default is resolved ONCE per repo by the caller
    # (repo_json), the same pattern is_gh/web_base already use just above --
    # every issue in the repo must agree on the repo-level default within a
    # single tick, so a per-issue core.repo_auto_land(repo) read (from
    # orch.json) could let a config change mid-loop split issues in the SAME
    # repo with identical labels across two different defaults. issue_json
    # only combines that resolved default with this issue's own labels.
    auto_land = core.auto_land_on(labels, repo_auto_land_default)
    # auto_land_source: the per-issue label overrides the repo default in
    # BOTH directions (core.auto_land_on), so the resolved bool alone hides
    # whether an operator set it here or it merely fell through from the
    # repo. "issue" when this issue carries L_AUTOLAND or L_NO_AUTOLAND,
    # "repo" when it carries neither and inherited the repo default --
    # makes the precedence model visible rather than just its output.
    auto_land_source = "issue" if (core.L_AUTOLAND in labels or core.L_NO_AUTOLAND in labels) else "repo"

    # names is core.names_read(repo), resolved ONCE per repo by repo_json and
    # threaded down here, same pattern as coverage/is_gh/web_base above --
    # it reads a whole file, so per-issue re-reads would pay that cost N
    # times per tick for an answer that is identical for every issue.
    # names=None means the caller never looked (the cache is a display-only
    # convenience, so a caller that does not care may omit it) -- distinct
    # from {} meaning "looked, found nothing". Both render the same: no
    # display name, full title alone.
    entry = (names or {}).get(str(n))
    entry = entry if isinstance(entry, dict) else {}  # hand-edited file -> degrade, never raise
    display_name = entry.get("name") or ""
    stored_title = entry.get("title") or ""
    live_title = world.issue_field(n, "title")
    # orch#289: an empty stored title means "we recorded the name but never
    # captured a title to diff against" -- NOT "the title is now ''". Ten+
    # cache entries (and growing) store title="" for exactly this reason.
    # Comparing live_title != "" there would brand all of them permanently
    # stale. So the staleness check only RUNS when the stored title is
    # non-empty; an empty/absent stored title skips the check (name_stale
    # stays False) rather than running it against "".
    name_stale = bool(stored_title) and stored_title != live_title

    return {
        "issue": n,
        "state": state,
        "work_state": work_state(world, repo, n),
        "title": live_title,
        "display_name": display_name,
        "name_stale": name_stale,
        "labels": labels,
        "ready": ready,
        "auto_land": auto_land,
        "auto_land_source": auto_land_source,
        "branch": br,
        "commits": commits,
        "idle_min": idle,
        "activity": activity,
        "url": be.issue_url(n),
        "brief": core.issue_brief(world.issue_comments(n), activity),
        "pr": pr,
        "pr_url": pr_url,
        "sessions": sessions,
        "contended": sum(1 for s in sessions if s["live"]) > 1,
        "orch_alive": orch_alive,
        "prior_runs": prior_runs(key),
        # spawn.py writes the ledger row the instant an issue-orch is
        # spawned; the label claim is a later act INSIDE that session's own
        # startup, landing minutes after. In that window state is already
        # UNCLAIMED and ready is already True, but the issue is not
        # ownerless -- a live issue-orch is mid-claim. UNCLAIMED+ready alone
        # is therefore NOT "no owner": it is two cases, no live session
        # (truly startable) and a live session still starting (owned,
        # already spawned). `owner_starting` names that second case instead
        # of `startable` only denying the first, so a consumer joining
        # state/ready with liveness cannot collapse them back into one. Both
        # are derived here at read time from orch_alive (which itself reads
        # the ledger row's pgid at read time, see alive()) -- neither is
        # ever a recorded state of its own.
        "startable": ready and state == "UNCLAIMED" and not orch_alive,
        "owner_starting": ready and state == "UNCLAIMED" and orch_alive,
        # Who holds this claim and for how long (orch#228). None when no
        # readable lease exists -- an older claim journalled before leases
        # existed, or a tea repo (World.load normalizes those to an empty
        # comment list, so claim_lease can never read one there). Rendered
        # only as information: the re-entry decision is void_claim's, and it
        # is deliberately NOT recomputed here, so nothing a viewer sees can
        # be mistaken for the authority that acts on it.
        "claim_holder": claim_holder,
        "considered": considered,
    }


# --- one repo ------------------------------------------------------------------

# How long to wait on the one label-list read below. Matches the order of the
# other per-repo forge reads; a hung forge must not stall a whole tick.
_LABEL_LIST_TIMEOUT = 20


def missing_orch_labels(repo, is_gh, gr):
    """Which of core.ORCH_LABELS are NOT defined on repo's remote.

    Returns a list of label NAMES, or None if we could not find out (the read
    failed, timed out, or came back unparseable). None is deliberately NOT the
    empty list: "we could not check" and "nothing is missing" are different
    answers, and collapsing them would let a forge outage render as a clean
    bill of health -- the exact silent-emptiness failure this function exists
    to expose.

    READ ONLY. This never creates a label. core.ensure_labels does that, and
    is a WRITE that belongs on the watch path; the feed is a read path that
    runs every tick and must not mutate the forge as a side effect of being
    looked at.

    Both backends' label_list argv return a JSON array of objects carrying a
    `name`, so the parse is shared even though the argv are not."""
    # Adapter built from the caller's already-resolved is_gh/gr rather than
    # via core.adapter_for(): `repo` here may not be a readable checkout at
    # all, and re-reading the remote would be a second shell-out for an
    # answer repo_json already has. The login is only meaningful on tea.
    be = core.RepoAdapter(
        "gh" if is_gh else "tea", gr,
        None if is_gh else core.repo_login_for(os.path.basename(str(repo))),
        core.GH_WEB_BASE if is_gh else "")
    ok, out = _run(be.label_list(), timeout=_LABEL_LIST_TIMEOUT)
    if not ok:
        return None
    try:
        existing = {row.get("name", "") for row in json.loads(out or "[]")}
    except (ValueError, AttributeError):
        return None
    return [n for n, _ in core.ORCH_LABELS if n not in existing]


def _capped_note_row(row):
    """journal_tail returns disk-raw rows; the feed payload is re-read every
    tick, so long notes here are pure token waste (disk itself stays
    untouched — journal_brief applies the same cap for the same reason)."""
    raw = row.get("note")
    if not raw or not isinstance(raw, str):
        return row
    if len(raw) <= core.JOURNAL_NOTE_CHARS:
        return row
    cut = len(raw) - core.JOURNAL_NOTE_CHARS
    return {**row, "note": raw[:core.JOURNAL_NOTE_CHARS] + f"… [+{cut} chars]"}


# Wake bookkeeping, excluded from `recent` (docs/DASHBOARD-2026-09-17.md
# §4.1). `observed` is the tick's per-issue snapshot -- its information is
# already the state pill on every row and the tick age in the nav; `spawn` /
# `spawn-cold-retry` are "repo-orch woke" -- the repo row's live dot. Measured
# 2026-09-17: 99 of 104 `recent` rows on the live feed were `observed`, so
# every JOURNAL section rendered eight heartbeat lines and no acts. A DENY
# list of three, never an allow list: any event added later is an act until
# ruled otherwise, and passes through with no edit here (same stance as
# orch/usage.py, orch#298).
_RECENT_BOOKKEEPING = frozenset({"observed", "spawn", "spawn-cold-retry"})
_RECENT_ROWS = 8
# How far back to look for those 8 acts. Free: core._tail_rows reads the
# whole file regardless of n. 5,678 observed/day means 200 rows is ~50min of
# heartbeat on a busy repo -- enough to find acts on any repo that has any.
_RECENT_SCAN = 200


def repo_orch_json(gr, slug):
    key = key_for("repo-orch", slug)
    acts = [r for r in journal_tail("repo", slug, n=_RECENT_SCAN)
            if r.get("event") not in _RECENT_BOOKKEEPING]
    return {
        "key": key, "alive": alive(key), "prior_runs": prior_runs(key),
        "recent": [_capped_note_row(r) for r in acts[-_RECENT_ROWS:]],
    }


def repo_json(repo):
    slug = os.path.basename(repo)
    # gr must be resolved by the SAME backend the repo actually uses, exactly
    # as core.merge_pr now does: gh_repo() only strips github.com's three
    # known remote forms, so calling it unconditionally on a tea-backed repo
    # (say a Gitea remote like http://gitea.local:3000/owner/repo.git) would hand
    # World.load the raw, unstripped remote URL rather than "owner/repo".
    # World.load uses that string BOTH to build the backend's own argv AND,
    # via core._repo_entry_for_owner_slug, to decide which backend answers
    # at all -- so a malformed slug here doesn't just look wrong in the
    # dashboard, it makes the tea repo's tick unable to load its oracle at
    # all, tripping the "unreachable oracle" abort below on every tick. Do
    # not simplify this back to a bare gh_repo(repo) call.
    # core.adapter_for resolves backend and slug together, once, picking
    # gh_repo() for gh and repo_slug() for tea -- the two are NOT
    # interchangeable, which is what the paragraph above is about.
    be = core.adapter_for(repo)
    is_gh = be.backend != "tea"
    gr = be.slug
    # An empty gr must abort here, before World.load: handing "" to
    # `gh issue list --repo ""` doesn't fail -- gh silently answers from the
    # cwd's default repo instead, so the feed would report ok: True while
    # showing a DIFFERENT repo's issues entirely (orch#64's cross-repo bleed).
    # This sits above the web_base resolution below on purpose: a repo whose
    # slug won't resolve has nothing to link to, so paying that `tea`
    # shell-out first would be a round-trip spent on a repo we are about to
    # refuse anyway.
    if not gr:
        # unconsidered: [] to match, for the same reason issues is [] here --
        # every repo row must have the same shape regardless of which abort
        # path produced it. in_flight_cap: not yet resolved on this path (it
        # needs nothing gr provides), so fall back to the global default
        # directly rather than leave the key out of the row's shape.
        # auto_land: same reasoning, and the row is rendered unconditionally
        # (repoRow has no ok gate), so omitting the key would not blank the
        # control -- it would print a confident "automerge off" for a repo
        # whose default is on. False is the documented no-file default.
        # stalled: same reasoning again -- an unresolvable slug is a
        # different fault from a stall and must never claim to be stalled.
        # orphan_prs: same shape rule -- an unresolvable slug means we never
        # looked for orphaned PRs, which is [] here, not a missing key.
        # rollups_*: same shape rule once more -- we never read a rollup on
        # this path, and 0/0 is "no PRs examined", which cannot trip the
        # unreadable-rollup alert (it needs total >= 2). Zero is the honest
        # count of reads we did not perform, not a claim that they succeeded.
        return {"repo": gr, "path": str(repo), "ok": False, "issues": [],
                "all_issues": [],
                "unconsidered": [], "in_flight_cap": core.IN_FLIGHT_CAP,
                "orphan_prs": [],
                "rollups_unreadable": 0, "rollups_total": 0,
                "auto_land": False, "stalled": False, "stalled_since": None}
    # web_base is resolved once per repo, here, never per issue: it costs a
    # `tea` shell-out (core.login_web_base), and every issue in the repo
    # shares the same login and thus the same base. gh repos never need it --
    # github.com's URL is fixed -- so it is left "" rather than resolved.
    # Reading be.web_base resolves it lazily, exactly once per adapter (see
    # core.RepoAdapter); gh needs no lookup at all, and the per-issue
    # adapters built inside issue_json are handed this value rather than
    # re-resolving it.
    web_base = "" if is_gh else be.web_base
    # coverage is resolved once per repo, here, never per issue: exactly the
    # is_gh/web_base precedent immediately above -- core.consolidated_coverage
    # reads and parses the WHOLE repo journal file per call (see its
    # docstring: no tail-read is safe, the newest covering row can be
    # arbitrarily old), so calling it once per issue would re-read that same
    # file N times per tick for an answer that is identical for every issue
    # in the repo. Resolved here and handed down to issue_json as a plain
    # dict parameter, same threading style as is_gh/web_base.
    coverage = core.consolidated_coverage(slug)
    # names is resolved once per repo, here, never per issue: core.names_read
    # reads and parses a whole file (see its docstring), same precedent as
    # coverage/is_gh/web_base above -- passed down to issue_json as a plain
    # dict rather than re-read per issue.
    # Keyed on `slug`, NOT on `repo`: names_path runs the slug through
    # core._fs_slug, which is a str.replace -- handing it the checkout Path
    # raises TypeError (Path.replace takes different args). The cache on disk
    # is written under the basename slug too (state/repos/orch/names.json),
    # matching journal_tail("repo", slug) just below in repo_orch_json.
    names = core.names_read(slug)
    # Resolved once per repo, here, never per issue: core.repo_auto_land
    # (from orch.json) is a repo-level default that every issue in the repo
    # shares (orch#163). Same pattern as is_gh/web_base just above -- passed
    # down to issue_json rather than re-derived there, so a config change
    # mid-loop cannot split one repo's issues across two different defaults
    # in a single feed.
    repo_auto_land_default = core.repo_auto_land(repo)
    # Resolved once per repo, here, same as repo_auto_land_default just
    # above: core.repo_in_flight_cap (from orch.json) is a repo-level fact
    # shared by every issue in the repo, so resolving it once keeps every
    # issue in this feed agreeing on the same cap even if orch.json changes
    # mid-loop.
    in_flight_cap = core.repo_in_flight_cap(repo)
    # Clamp by measured machine-wide headroom (orch#314): min() only, one
    # direction -- headroom_cap() returning None ("no record", see its
    # docstring) leaves the repo's configured/static cap untouched, and a
    # number never RAISES the cap above what the operator configured.
    _headroom = core.headroom_cap()
    if _headroom is not None:
        in_flight_cap = min(in_flight_cap, _headroom)
    world = World()
    if not world.load(gr):
        # Abort the repo rather than derive a false empty: an unreachable
        # oracle must not read as "nothing is happening".
        # labels_missing is None, not []: the oracle was unreachable, so we
        # did not check and must not imply the labels are fine. Present as a
        # key at all so every repo row has the same shape. in_flight_cap is
        # already resolved above (it needs no oracle), so this abort path
        # reports the real value rather than the global fallback.
        # auto_land is likewise already resolved above with no oracle needed
        # -- this is the repo's DEFAULT only, the same default the per-issue
        # auto_land_on() call overrides in both directions; emitted for
        # display only, nothing here writes it back to orch.json.
        # stalled: an unreachable oracle is a different fault from a stall
        # and must never claim to be stalled -- same reasoning as above.
        # orphan_prs is [] here for the same reason labels_missing is None:
        # the oracle was unreachable, so we did not look. Present as a key so
        # every repo row has the same shape.
        return {"repo": gr, "path": str(repo), "ok": False, "issues": [],
                "all_issues": [],
                "labels_missing": None, "unconsidered": [], "orphan_prs": [],
                "in_flight_cap": in_flight_cap,
                "auto_land": repo_auto_land_default,
                "stalled": False, "stalled_since": None,
                "rollups_unreadable": 0, "rollups_total": 0}

    issues = [issue_json(world, repo, n, slug, gr, is_gh, web_base, coverage, repo_auto_land_default, names) for n in world.candidates()]
    # all_issues: orch#417. repo-orch's consolidation step ("Consolidate --
    # relate the issues before you order them" in agents/repo-orch.md) needs
    # the FULL open issue set, not the agent-ready/agent-working slice above.
    # `issues` stays exactly candidates()-only -- the dashboard/widget render
    # straight off it (widget.tpl.html's repoCounts/bucketOf, feed.build's
    # alert loop below) and widening it would start firing "abandoned" alerts
    # for every agent-stuck issue on every tick, a real display change this
    # fix must not make (see feed.build's `if i.get("state") == "ABANDONED"`).
    # So this is a SEPARATE key, built straight off world.issues (every open
    # issue, already fetched by World.load -- zero extra forge calls) with
    # only cheap, pure-over-labels fields: no issue_json() per row, which
    # would run a git rev-list + session walk + transcript walk for every
    # unlabelled/stuck issue, every tick, for data repo-orch's consolidate
    # step does not need (it wants number/title/labels/timestamps).
    # Comment BODIES are deliberately excluded: they were 181 KB of this
    # payload's 189 KB (orch#417 follow-up, measured over 29 open issues) and
    # blew status.json's 150 KB conformance budget on their own. Consolidation
    # relates issues by title and label; an agent that needs one issue's
    # discussion fetches that issue's comments on demand.
    all_issues = [
        {"number": wi.get("number"), "title": wi.get("title"),
         "labels": [l.get("name") for l in wi.get("labels", [])],
         "createdAt": wi.get("createdAt"), "updatedAt": wi.get("updatedAt"),
         "state": core.issue_state(world, wi.get("number"))}
        for wi in world.issues if wi.get("number") is not None
    ]
    # unconsidered: open issue NUMBERS condition 8 should fire on -- the
    # complement of what `issues` covers. `issues` above comes from
    # world.candidates(), which returns ONLY issues already carrying
    # agent-ready or agent-working (core.World.candidates) -- exactly the
    # already-labelled set, which is the precise complement of the set
    # condition 8 exists to watch (orch#173's blocking review finding: a
    # 29-issue, zero-label deadlock produces candidates() == [], so reading
    # r["issues"] there never sees the backlog at all). Built straight off
    # world.issues instead, which carries EVERY open issue.
    #
    # Deliberately NOT issue_json(): that function does a git rev-list, a
    # session walk and a transcript walk per issue -- real per-tick cost --
    # and unlabelled issues (the common case in the deadlock this exists to
    # break) have none of that machinery running yet, so paying for it here
    # would tax every open issue every tick for a fact that only needs
    # number/labels/createdAt. Read straight off world.issues instead.
    #
    # An issue belongs in unconsidered when BOTH:
    #  - not covered: _covered_after(createdAt, coverage.get(n)) is False --
    #    the SAME comparison issue_json's `considered` uses, factored into
    #    one helper so the two cannot drift (see _covered_after).
    #  - not claimed: does not carry agent-working. A claimed issue already
    #    has an owner; the set-level wake is about issues nobody is even
    #    looking at, not about ones mid-flight.
    #
    # Degrade direction: a missing/unparseable createdAt, or any per-issue
    # error, counts as UNCONSIDERED (goes IN the list). Unknown must mean
    # wake, never silence -- same stance _covered_after and issue_json's
    # `considered` already take.
    unconsidered = []
    for wi in world.issues:
        try:
            n = wi.get("number")
            if n is None:
                continue
            names = {l.get("name") for l in wi.get("labels", [])}
            if core.L_WORKING in names:
                continue
            created_at = wi.get("createdAt")
            if not _covered_after(created_at, coverage.get(n)):
                unconsidered.append(n)
        except (AttributeError, TypeError):
            # Any per-issue read error degrades to unconsidered -- unknown
            # must wake, never go silent.
            n = wi.get("number") if isinstance(wi, dict) else None
            if n is not None:
                unconsidered.append(n)
    unconsidered.sort()
    counts = {}
    for i in issues:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    # COST GUARD -- do not lift this out of the `if`. The label read is one
    # extra forge call, and made unconditionally it would be one PER REPO PER
    # TICK forever, for a question whose answer almost never changes. It is
    # spent only when the row is OTHERWISE EMPTY, because an empty row is
    # already the anomaly worth paying to explain: a repo that derived
    # candidates is self-evidently labelled and reachable, and has nothing to
    # ask. Do not "optimize" this into an unconditional call, and do not
    # delete it as redundant with ensure_labels -- ensure_labels runs once at
    # watch time and cannot see a label deleted on the forge afterwards,
    # which is precisely how a repo arrives at this silent-empty state.
    #
    # HONEST LIMITATION: this distinguishes "empty because unlabelled" from
    # "empty because done" only in the unlabelled direction. A fully labelled
    # repo that is genuinely finished returns [] here and raises no alert,
    # which is right. But a repo that is BOTH finished AND unlabelled is
    # flagged too -- we cannot tell it apart without a second call counting
    # open issues, and orch would want its labels there either way. The alert
    # wording below therefore states the missing labels as the fact and the
    # emptiness as a consequence to check, rather than asserting the repo is
    # broken.
    labels_missing = missing_orch_labels(repo, is_gh, gr) if not issues else []
    # Counted from the session LEDGER, not from any issue list. Every issue
    # list available here is incomplete in the direction that hurts: `issues`
    # above is world.candidates(), which drops an issue the moment its label
    # is released at landing, and world.issues is `--state open`, which drops
    # it the moment it closes -- while the session stays alive through the PR
    # and merge in both cases. in_flight_cap is enforced against this number
    # (agents/repo-orch.md tells repo-orch to count live issues off this
    # slice), so an undercount lets a repo-orch overshoot its cap. orch#279
    # observed both halves: live_orchs read 2 while .2/.5/.9 were alive, and
    # two of those issues were in neither `issues` nor `unconsidered` -- and
    # `unconsidered` is built from world.issues, so they had already closed.
    # The ledger has no such blind spot and costs no forge call.
    live_orchs = len(live_issue_orchs(slug))
    repo_orch = repo_orch_json(gr, slug)
    # stalled is DERIVED here, per the operator's ruling, and never stored:
    # "stalled = state unchanged while work is outstanding". "Work
    # outstanding" is deliberately world.issues (the full open set), not the
    # `issues` local above -- that one is world.candidates(), the labelled
    # subset, and would miss an unlabelled backlog exactly like orch#173's
    # deadlock did for `unconsidered`.
    #
    # The two reads are DELIBERATELY different populations, and the asymmetry
    # is the point rather than an artifact of where `activity` happens to be
    # computed. "Work outstanding" asks about the whole open set; "is it
    # moving" asks only about issues orch has actually triaged. So an
    # unlabelled issue bumped seconds ago (an operator comment, a label
    # removed) does NOT count as movement and the repo still badges -- which
    # is right: nothing has triaged that issue, and a `updatedAt` bump is not
    # repo-orch doing its job. Widening this scan to world.issues' raw
    # updatedAt would silence exactly the repo the ruling most wants seen,
    # the one whose repo-orch has never run.
    #
    # No activity at all (empty issues, or every activity None) counts as
    # not-moving, not as unknown -- same direction. This is display only:
    # acting on a stall is orch#299 and is deliberately NOT done here.
    stalled_since = None
    for i in issues:
        act = i.get("activity")
        if act is not None and (stalled_since is None or act > stalled_since):
            stalled_since = act
    stalled = bool(
        world.issues
        and live_orchs == 0 and not repo_orch["alive"]
        and (stalled_since is None or (now() - stalled_since) > NUDGE_IDLE_MINS * 60)
    )
    # Counts only, from the snapshot load() already fetched -- no extra forge
    # call. An unreadable rollup is a read that FAILED, which is a different
    # fact from a repo that runs no CI; build() below needs both numbers to
    # tell a repo-wide credential fault from one flaky read.
    # A BARE CALL, deliberately -- do not wrap this in getattr or try/except.
    # rollup_readability is an unconditional method on World, and this line is
    # reached only after world.load() succeeded, so the only way it can be
    # missing is a rename or deletion that missed this call site. Guarding it
    # would turn that into a permanently silent (0, 0): the alert stops
    # firing, the repo reads as healthy, and nothing says otherwise -- the
    # exact silent drop orch#143 exists to end, reintroduced one layer up.
    # An AttributeError at the first tick is the loud failure we want.
    rollups_unreadable, rollups_total = world.rollup_readability()
    return {
        "repo": gr, "path": str(repo), "slug": slug, "ok": True,
        "counts": counts,
        # [] = checked, all present. Non-empty = these names are absent.
        # None = could not check (see missing_orch_labels).
        "labels_missing": labels_missing,
        "rollups_unreadable": rollups_unreadable,
        "rollups_total": rollups_total,
        "live_orchs": live_orchs,
        "in_flight_cap": in_flight_cap,
        # The repo default that per-issue auto_land_on() overrides in both
        # directions (see issue_json's auto_land_source) -- display only,
        # nothing here writes it back to orch.json.
        "auto_land": repo_auto_land_default,
        "issues": issues,
        "all_issues": all_issues,
        "unconsidered": unconsidered,
        # Open PRs whose issue already closed. They hang off no issue row, so
        # without this key nothing in the tree ever raises them -- they read as
        # handled because they exist and are green (orch#279).
        "orphan_prs": world.orphan_prs(),
        "orch": repo_orch,
        "stalled": stalled,
        "stalled_since": stalled_since,
    }


# --- agent_sessions: the ledger itself, current rows only ----------------------

def _is_current_stem(stem):
    """True iff stem is a bare key (not a rolled-aside <key>.<ts> row).
    Matches component count against the role's known arity rather than a
    naive split — keys contain dots (issue-orch.foo.42) so length alone
    would misclassify."""
    parts = stem.split(".")
    arity = _KEY_ARITY.get(parts[0])  # role names use "-", never ".", so parts[0] is the role
    return arity is not None and len(parts) == arity


def agent_sessions_json():
    d = ORCH_HOME / "state" / "sessions"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        stem = f.stem
        if not _is_current_stem(stem):
            continue  # rolled row — recorded, not current; never presented as live
        row = ledger_read(stem)
        if not row:
            continue
        log_path = ledger_log_path(stem)
        log_mtime = int(log_path.stat().st_mtime) if log_path.exists() else None
        out.append({
            "key": stem, "role": row.get("role"), "scope": row.get("scope"),
            "alive": alive(stem), "log_mtime": log_mtime,
            "prior_runs": prior_runs(stem),
        })
    return out


# --- sessions attached to nothing ----------------------------------------------
# Ported near-verbatim from old feed.py: exploration in another directory, an
# orphan whose issue closed. Where work gets lost, so it is listed too.
# "-orch-wt-" is excluded because every orch cwd under wt/ mangles to contain
# that substring (see DESIGN.md Addressing) — those belong to keyed actors,
# not orphans.

# orch#416: how many unattached sessions the feed publishes. Env-overridable
# so a cap that turns out wrong is a unit drop-in, not a release -- same
# stance as ACK_TTL_MINS. 40 keeps roughly a working day of the operator's
# own sessions at observed volume while bounding the payload.
UNATTACHED_CAP = int(os.environ.get("ORCH_UNATTACHED_CAP", "40"))


def unattached_json():
    root = Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        if not d.is_dir() or "-orch-wt-" in d.name:
            continue
        files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            continue
        f = files[0]
        idle = (now() - int(f.stat().st_mtime)) // 60
        if idle > 2880:  # older than 2 days
            continue
        out.append({
            "project": d.name,
            "path": d.name.lstrip("-").replace("-", "/"),
            "id": f.stem,
            "file": str(f),
            "idle_min": idle,
        })
    out.sort(key=lambda r: r["idle_min"])
    # orch#416: cap the list. This is the operator's own interactive
    # ~/.claude/projects dirs -- a widget curiosity that grows with every
    # session they run, and it had become 74.6% of a 264 KB status.json
    # (208,390 of 264,298 bytes, measured 2026-09-17). Every repo-orch wake
    # may read that file, and context loading is 90.9% of spend
    # (docs/RESTRUCTURE-2026-09-16.md section 12), so an unbounded list of
    # the operator's shell history was the single largest thing in the
    # payload. The 2-day cutoff above bounds AGE but not COUNT, and a busy
    # day makes those the same thing.
    #
    # Sorted newest-first already, so the cap keeps the rows a human would
    # actually scan and drops the tail nobody reads.
    #
    # Returns (rows, total). The TRUE pre-cap count must travel with the
    # capped list: orch#421's review caught that capping here silently
    # truncated what tui_model.py:421-423 reports a count from, and
    # build_widget.py:58-59 warns in as many words against moving a cap into
    # this function precisely because the TUI reads the full list. The fix is
    # not to drop the cap -- the payload problem is real -- but to publish
    # `unattached_total` beside the rows, which is the convention
    # build_widget.py:65 already established for its own display trim. Any
    # consumer can then say "40 of 57" instead of claiming there are 40.
    return out[:UNATTACHED_CAP], len(out)


# --- tick staleness ------------------------------------------------------------
# Derive staleness from something real and local: the mtime of the previous
# tick's own output. Not from the ticker service's state -- a stopped ticker
# and a wedged one look identical from here, and the feed should report the
# staleness either way rather than two different stories.

def tick_json():
    """Tick staleness, derived from the previous tick's own output. The
    scheduler is a separate process (orch.ticker) with nothing to
    introspect across that boundary — public/status.json's mtime is the
    honest local fact:
    the tick rewrites it every pass. Report -1 rather than fake a green
    light when no tick has ever run."""
    # needs_attention is declared here, null, and overwritten by the tick
    # after compute_conditions runs (see tick.main step 5 -> step 3). Only
    # the tick can produce it without a cycle, so a plain `python -m
    # orch.feed` leaves it null. Declaring the key means a consumer reading
    # a feed this module built alone sees an explicit "not computed" rather
    # than a missing key, and a real count is always an int -- so null and 0
    # stay distinguishable (#226).
    p = ORCH_HOME / "public" / "status.json"
    if not p.exists():
        return {"minutes_since_last": -1, "last_run": None, "needs_attention": None}
    mt = int(p.stat().st_mtime)
    last_run = __import__("datetime").datetime.fromtimestamp(mt).astimezone().isoformat(timespec="seconds")
    return {"minutes_since_last": (now() - mt) // 60, "last_run": last_run,
            "needs_attention": None}


_DEPLOY_TIMEOUT = 5

# orch#250 part 2: the auto-pull timer (deploy/orch-pull.sh) runs every 5
# minutes and now records its outcome to PULL_STATE_FILE. This many
# CONSECUTIVE failures/refusals (SKIPPED does not count -- see
# read_pull_state's docstring) means ~15 minutes of a stuck pull, which is
# long enough to rule out one transient blip (a momentary network hiccup, a
# single overlapping tick) while still catching a stuck deploy quickly.
PULL_FAIL_THRESHOLD = 3

PULL_STATE_FILE = ORCH_HOME / "state" / "orch-pull.json"


def read_pull_state():
    """{"consecutive_failures": int, "last_outcome": str} or {} if unusable.

    Missing (normal on any machine that hasn't run the new orch-pull.sh yet),
    empty, or malformed -> {}, silently. MUST NOT raise: same stance as
    deploy_json() itself -- a failure here would fail the whole tick.
    """
    try:
        raw = json.loads(PULL_STATE_FILE.read_text())
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    n = raw.get("consecutive_failures")
    outcome = raw.get("last_outcome")
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return {}
    if not isinstance(outcome, str) or not outcome:
        return {}
    return {"consecutive_failures": int(n), "last_outcome": outcome}


def deploy_json():
    """Version facts for the checkout at ORCH_HOME.

    Read-only: never fetches (this runs inside the tick loop). The behind
    count is measured against the last-fetched base ref already in the repo.
    Never raises — a failure here would fail the whole tick.

    The base comes from core.base_ref, NOT a hardcoded "origin/main": #67
    made remote resolution name-agnostic, and a deployment on a gitea remote
    or a master-default repo would otherwise fail the rev-list, degrade to
    behind=-1, and never alert -- a silent false negative on exactly the
    staleness this function exists to report.

    Also carries the auto-pull's own outcome history (orch#250 part 2) --
    consecutive_failures/last_outcome from read_pull_state(), both None when
    no usable record exists -- so a caller can tell "behind and pulling"
    (normal, self-correcting) from "behind and the puller is stuck" (needs a
    human) without a second read of this same file.
    """
    g = ["git", "-C", str(ORCH_HOME)]

    def one(args):
        ok, out = _run(g + args, timeout=_DEPLOY_TIMEOUT)
        out = out.strip()
        return out if ok and out else None

    commit = one(["rev-parse", "--short", "HEAD"])
    branch = one(["rev-parse", "--abbrev-ref", "HEAD"])
    base = base_ref(ORCH_HOME)
    count = one(["rev-list", "--count", f"HEAD..{base}"]) if base else None
    try:
        behind = int(count)
    except (TypeError, ValueError):
        behind = -1
    pull_state = read_pull_state()
    return {"commit": commit, "branch": branch, "base": base or None,
            "behind": behind, "is_behind": behind > 0,
            "pull_consecutive_failures": pull_state.get("consecutive_failures"),
            "pull_last_outcome": pull_state.get("last_outcome")}


def pull_stuck_alert(consecutive_failures, last_outcome):
    """Pure decision: (count, outcome) -> alert dict, or None.

    Below PULL_FAIL_THRESHOLD, or no usable count/outcome at all (None from
    a missing/malformed state file) -> None, silently. At or above threshold
    -> the alert, naming both the count and the last outcome string so an
    operator can tell a dirty-tree refusal from a network failure without
    opening journalctl.

    Factored out of deploy_json()'s callers so it is testable directly,
    without needing to fake a state file on disk.
    """
    if not isinstance(consecutive_failures, (int, float)) or isinstance(consecutive_failures, bool):
        return None
    if not isinstance(last_outcome, str) or not last_outcome:
        return None
    if consecutive_failures < PULL_FAIL_THRESHOLD:
        return None
    return {"level": "error", "key": "deploy-pull-stuck", "needs_you": True,
            "msg": (f"auto-pull has failed {int(consecutive_failures)} time(s) in a row "
                    f"(last: {last_outcome}) — it has stopped updating the deployment, "
                    f"this needs a human")}


def host_json():
    disk = shutil.disk_usage(Path.home())
    pct = int(disk.used * 100 / disk.total) if disk.total else 0
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    try:
        deploy = deploy_json()
    except Exception:
        deploy = {"commit": None, "branch": None, "base": None,
                  "behind": -1, "is_behind": False}
    return {
        "disk": f"{pct}% of {disk.total // (1024**3)}G",
        "load": " ".join(f"{x:.2f}" for x in load),
        "deploy": deploy,
        # Machine-wide in-flight ceiling derived from measured headroom
        # (orch#314) -- None here means "no record" (see headroom_cap's
        # docstring), i.e. no clamp is being applied anywhere this tick.
        # Surfaced so the operator can see what the machine decided rather
        # than a repo's cap silently shrinking with no visible cause.
        "headroom_cap": core.headroom_cap(),
        # Weighted per-role token spend over the trailing window (orch#185)
        # -- None here means "no record" (see spend_window's docstring),
        # same stance as headroom_cap above: never read a missing value as
        # zero spend.
        "spend": core.spend_window(),
    }


# --- assemble --------------------------------------------------------------

def build():
    repo_rows = []
    unresolved_repos = []
    # core._load_config, not a second read of the config file: this loop used
    # to parse repos.txt itself, which treated `login=` as part of the
    # directory name so every repo using the documented second-token form
    # vanished from the dashboard with no diagnostic (#244). Sharing the
    # loader also means the feed sees `state` -- an unwatched repo is "off"
    # in the config rather than deleted, and a feed still reading raw lines
    # would keep emitting rows for it forever (#230).
    for entry in core._load_config()["repos"]:
        repo = Path(entry["path"]).expanduser()
        if entry.get("state") != "tracked":
            # Paused ("off"): must stay VISIBLE, never vanish (#257, same
            # lesson #230 taught for deleted repos). `off` suppresses
            # ACTUATION only, not observation -- so this is a cheap static
            # row, never a repo_json() forge round-trip (gh/tea shell-out).
            # `repo` is the row's DISPLAY NAME and is not optional: the
            # widget renders it as esc(r.repo), and esc(undefined) is "", so
            # a row without it draws as a blank-named entry. The basename is
            # used rather than a resolved "owner/repo" on purpose -- that
            # resolution is a forge/remote read, which is the actuation
            # `off` suppresses.
            repo_rows.append({
                "repo": repo.name, "path": str(repo), "slug": repo.name,
                "state": entry.get("state"),
                "note": str(entry.get("note") or ""),
                "ok": True, "issues": [],
            })
            continue
        if not (repo / ".git").is_dir():
            unresolved_repos.append(str(repo))
            continue
        row = repo_json(repo)
        row["state"] = entry.get("state")
        repo_rows.append(row)

    host = host_json()
    host["live_orchs"] = sum(r.get("live_orchs", 0) for r in repo_rows)
    tick = tick_json()

    # Per-issue (no unit loop — units are recorded, never derived; nothing
    # smaller than the issue exists here). awaiting and alerts are both
    # rendered FIRST in the returned dict below (awaiting first of the two):
    # DESIGN.md's "six conditions" says the widget surfaces these
    # prominently, at the top, and dict insertion order is the JSON key
    # order the consumer reads top-down.
    # `needs_you` (issue #117) marks the alerts that ARE rows in the NEEDS YOU
    # taxonomy of docs/UX-REDESIGN.md 4.1. MEMBERSHIP IS A SEPARATE AXIS FROM
    # SEVERITY, and this key exists precisely so the two never get conflated
    # again -- do not "simplify" it back into a `level` check:
    #
    #   * `level` decides what tick.build_thin folds into the wake digest, and
    #     `msg` is what it folds. Both are load-bearing exactly as they are,
    #     so neither may move to make a row visible in the UI.
    #   * The taxonomy's own rows straddle every level. "wedged" and
    #     "waiting on merge" are deliberately `info` (nothing failed, and
    #     waking the dashboard-op on a green PR would be noise) yet both are
    #     named rows in 4.1's table -- the widget filtering on error+warn made
    #     the ordinary "your PR is ready to merge" case render as
    #     "nothing needs you".
    #   * The converse also holds: "gh read failed" and the missing-labels
    #     warning are real error/warn alerts that name no unit of work and are
    #     NOT needs-you rows.
    #
    # 4.1 says these rows are "computed feed-side (derivation lives tick-side
    # once)", so the predicate has exactly ONE home and it is here. The client
    # reads `a.needs_you` and computes nothing -- the same lesson pillState
    # records in widget.tpl.html, where one zone re-deriving a rule rendered
    # two different answers for one issue on one screen.
    alerts = []
    # A configured repo that resolves to no checkout is indistinguishable from a
    # repo nobody configured unless the drop is said out loud (#244).
    for path in unresolved_repos:
        alerts.append({"level": "warn", "key": f"repos-unresolved:{path}",
                       "msg": f"repos.txt line resolves to no checkout: {path}"})
    awaiting = []
    for r in repo_rows:
        # A paused ("off") repo carries a static row, not a repo_json() one
        # (#257). This originally guarded a live crash: `r["repo"]` at the
        # escalation alert raised KeyError and took the WHOLE feed down, not
        # just this row.
        #
        # That crash path is GONE -- the escalation alert was removed with
        # the escalation object (#198) -- and removing this `continue`
        # raises nothing today, verified by mutation (the suite stays
        # passed=1474 failed=0). The paused row above is fully populated:
        # repo, path, slug, state, note, ok=True, issues=[]. Every read in
        # this loop is either `.get`-guarded or a key that row sets, and the
        # bare reads (`r["slug"]`) sit inside `for i in r.get("issues", [])`,
        # which an empty list never enters.
        #
        # So this is defensive, not load-bearing, and no mutation test will
        # catch its removal. Keep it anyway: it makes the pause guarantee
        # STRUCTURAL rather than incidental. Every condition, and so every
        # spawn route that reads one, is fed from this loop, so a paused
        # repo cannot produce work to route. Without the `continue` that
        # guarantee would rest on the paused-row builder continuing to set
        # `ok: True` and `issues: []` -- drop the `ok` and this loop emits
        # "gh read failed" for a repo nobody is reading. That is a property
        # of the builder, not of this loop, and this is what keeps the two
        # independent.
        #
        # No test covers this, deliberately (#308). Two candidates were
        # written and both proved vacuous -- they pass with the `continue`
        # deleted, because the paused row's own ok=True and issues=[]
        # suppress every alert independently. A test only bites if the row
        # ALSO drops `ok`, which no builder path produces, so it would
        # assert against a row that cannot occur. A test that survives its
        # own mutation reports coverage that is not there; this comment is
        # the honest record instead.
        if r.get("state") not in (None, "tracked"):
            continue
        # Default False: a row that never stated `ok` is not evidence of
        # health. All three repo_json return paths set it today; a fourth
        # that forgets must read as failed, not as fine.
        if not r.get("ok", False):
            alerts.append({"level": "error", "msg": f"gh read failed for {r['repo']}"})
        # An empty repo with labels missing is NOT a healthy idle repo: orch
        # derives candidates by label, so absent labels mean it can never see
        # work there, and the zero-issue row is an artifact of the missing
        # labels rather than a report about the repo. Named here so a human
        # reading the dashboard knows the row is unexplained AND knows the
        # fix. Only ever set on an otherwise-empty row (see repo_json).
        # `warn`, not `error`: nothing failed, and the repo may also simply
        # be done -- see repo_json's HONEST LIMITATION note for why the
        # wording stops short of calling the emptiness a fault.
        missing = r.get("labels_missing")
        if missing:
            alerts.append({"level": "warn", "msg": (
                f"{r['repo']} has no issues and is missing orch labels "
                f"({', '.join(missing)}) — run watch_repo to create them; "
                f"until then orch cannot see work in this repo")})
        # A repo-wide unreadable rollup, derived rather than reported. When
        # EVERY open PR's CI status fails to read, nothing can merge: pr_green
        # is False for an unreadable rollup, so auto-land holds every PR, and
        # each one renders as an ordinary held REVIEW. The display is not
        # wrong -- it just cannot say why the hold will never lift (orch#143).
        #
        # ONE row per repo, not per PR. Five held PRs were five ordinary rows
        # and thirteen alerts that named no cause; a fourteenth vague row
        # would reproduce that defect rather than fix it.
        #
        # Threshold is `>= 2 and all of them`: one flaky read is noise, but
        # every PR in a repo failing the same read is a repo-level fault. The
        # wording names the CONDITION and a place to look, never a cause -- a
        # fine-grained PAT without `checks:read` is the observed cause, not
        # the only one, and a wrong cause sends the operator down the wrong
        # path. Display only: this gates nothing, and a transient double
        # failure clears itself on the next tick's fresh snapshot.
        #
        # DELIBERATELY CARRIES NO `kind`/`verb`/`what`/`age_min`, and adding
        # them would silently gut this alert -- do not "complete" it to match
        # the per-issue rows below. widget.tpl.html's needsYouRow branches on
        # `kind`: without one the row IS its `msg` (widget.tpl.html:1404),
        # which is the only branch that renders this sentence at all. With a
        # `kind`, the row renders `kindText[a.kind] || a.kind` -- the raw slug
        # "rollups-unreadable" plus `what` -- and the msg, the whole point of
        # the row, is never shown. `verb` has the same problem: every branch
        # of needsYouVerb ends at verbLink(a.url, ...) and a repo-scope alert
        # has no issue URL to link, so it would render a dead control.
        #
        # `slug` + `repo` with NO `issue` is what routes it: repoIssueRows
        # filters on `a.slug === r.slug && a.issue == null`
        # (widget.tpl.html:1294), so it nests under its repo rather than
        # joining the machine-scope strip. `key` keys tick's per-wake
        # suppression the same way "repos-unresolved:<path>" does, so a
        # persistent fault folds into one digest row instead of renotifying
        # every wake.
        _roll_bad = r.get("rollups_unreadable", 0)
        _roll_all = r.get("rollups_total", 0)
        if _roll_all >= 2 and _roll_bad == _roll_all:
            alerts.append({"level": "error",
                            "msg": (f"{r['repo']}: CI status unreadable on all "
                                    f"{_roll_all} open PRs — orch cannot tell green "
                                    f"from red, so auto-land cannot merge any of "
                                    f"them. Not the same as a repo with no CI. "
                                    f"Check the forge credential's scope and the "
                                    f"backend CLI's auth."),
                            "needs_you": True,
                            "key": f"rollups-unreadable:{r['slug']}",
                            "slug": r["slug"], "repo": r["repo"]})
        for i in r.get("issues", []):
            # Liveness deliberately absent (#134). "This PR has findings
            # somebody must act on" and "the session is still running" are
            # orthogonal axes, and DESIGN.md is explicit that liveness never
            # feeds a state function. A live issue-orch does NOT make the row
            # redundant: nothing wakes a session on a PR comment, so the
            # alive-but-idle session that opened the PR and stopped is the
            # most likely owner of an unread fix-before-merge finding, not
            # the least. A live REVIEW unit still raises its own
            # "still-running-after-pr" alert below; that is a separate row
            # about the session, not about the review.
            if i.get("work_state") == "REVIEW" and i.get("pr"):
                # One `gh` read per awaiting row. That looks unbounded in a
                # loop and is not: awaiting rows are REVIEW units with a PR,
                # and the in-flight cap is 5, so this is at most a handful of
                # calls per build -- dropping the liveness gate widens the set
                # to at most that same cap.
                try:
                    review = core.review_items_for_pr(r["path"], i["pr"])
                except Exception:
                    # review_items_for_pr promises not to raise; one
                    # unreadable PR must not take the whole feed with it
                    # regardless. None reads as "no review", which is the
                    # honest answer when the read failed.
                    review = None
                awaiting.append({
                    "repo": r["repo"], "slug": r["slug"], "issue": i["issue"],
                    "title": i["title"], "url": i["url"],
                    "pr": i.get("pr"), "pr_url": i.get("pr_url"),
                    "review": review,
                })
            # Structured row fields alongside msg/level, never instead of
            # them. `msg` is the wake digest's text and `level` decides what
            # the digest folds in (see the #108 comment above), so both keep
            # their exact existing values on
            # every alert here; `kind`/`what`/`verb`/`age_min` exist so the
            # operator UI can render a NEEDS YOU row without re-parsing an
            # English sentence back into fields. `kind` values are the settled
            # row vocabulary in docs/UX-REDESIGN.md 4.1; `verb` is that
            # table's verbs column. -1 in age_min is "could not determine",
            # the same convention idle_min and commits already use -- never
            # None, which a consumer would have to special-case twice.
            live_sessions = sum(1 for s in i.get("sessions", []) if s.get("live"))
            if i.get("contended"):
                alerts.append({"level": "error",
                                "msg": f"{r['repo']}#{i['issue']} has two live sessions",
                                "url": i["url"],
                                "needs_you": True,
                                "kind": "contended",
                                "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                "what": f"{live_sessions} live sessions",
                                "age_min": i.get("idle_min", -1),
                                "verb": "kill"})
            # Same source as tick's condition 6 (tick.idle_over): `activity`,
            # not idle_min (commit-only) - an issue-orch busy in Agent-tool
            # subagents but uncommitted must not read wedged.
            act = i.get("activity")
            act_idle_min = (now() - act) // 60 if act is not None else -1
            if i.get("orch_alive") and (act is None or (now() - act) > NUDGE_IDLE_MINS * 60):
                # age_min is act_idle_min, NOT idle_min: act_idle_min is the
                # number this alert's own msg already prints, and it is the
                # activity-based idle tick's condition 6 uses. Passing
                # idle_min (commit-only) would put a different number in the
                # row's clock than in its own sentence.
                alerts.append({"level": "info",
                                "msg": f"{r['repo']}#{i['issue']} wedged (alive, idle {act_idle_min}m)",
                                "url": i["url"],
                                "needs_you": True,
                                "kind": "wedged",
                                "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                "what": f"idle {act_idle_min}m",
                                "age_min": act_idle_min,
                                "verb": "kill"})
            if i.get("work_state") == "REVIEW":
                # orch#425: a dead owner sitting at REVIEW reads identical to a
                # live one waiting on a routine merge -- a human only caught it
                # by chance. -1 is "idle unknown" (not computable), not "idle
                # 0", so it must not be treated as past the threshold.
                _review_idle = i.get("idle_min", -1)
                _dead_stale = not i.get("orch_alive") and _review_idle >= NUDGE_IDLE_MINS
                alerts.append({"level": "warn" if _dead_stale else "info",
                                "msg": (f"{r['repo']}#{i['issue']} waiting on you (owner died)"
                                        if _dead_stale else
                                        f"{r['repo']}#{i['issue']} waiting on you"),
                                "url": i["url"],
                                "needs_you": True,
                                "kind": "waiting-on-merge",
                                "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                "what": f"PR #{i['pr']}" if i.get("pr") else "no PR",
                                "age_min": i.get("idle_min", -1),
                                "verb": "pr",
                                "pr": i.get("pr"), "pr_url": i.get("pr_url")})
            if i.get("state") == "ABANDONED":
                # what is deliberately empty: the taxonomy's what-line for an
                # abandoned row is "—" because the journal holds the why, and
                # inventing a filler string here would put a reason on the row
                # that nothing actually established.
                alerts.append({"level": "warn", "msg": f"{r['repo']}#{i['issue']} abandoned", "url": i["url"],
                                "needs_you": True,
                                "kind": "abandoned",
                                "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                "what": "",
                                "age_min": i.get("idle_min", -1),
                                "verb": "issue"})
            # The three liveness-join rows (UX-REDESIGN 4.1 / section 8). All
            # are `warn` ON PURPOSE and must stay that way: tick.build_thin
            # reduces the ERROR alerts to the wake digest, so promoting any
            # to `error` would start waking the dashboard-op on rows that are
            # for a human reading the page, not for the machine to act on.
            # None is a failure either -- these name a session that died
            # before doing any branch work, one that died after committing
            # (mid-flight), and one still alive after its PR opened, which is
            # normal for a few minutes. The split on work_state matters: a
            # dead session that already committed and opened a PR is not the
            # same story as one that never touched the branch, and joining
            # liveness against the LABEL state alone (ignoring work_state)
            # conflated the two -- that was the orch#183 false positive.
            # REVIEW / BLOCKED / CHECKING / LANDED deliberately fire NEITHER
            # row here; they have their own alert rows elsewhere in this loop.
            if i.get("state") == "CLAIMED" and not i.get("orch_alive"):
                if i.get("work_state") == "CLAIMED":
                    alerts.append({"level": "warn",
                                    "msg": f"{r['repo']}#{i['issue']} died before starting "
                                           f"({i.get('prior_runs')} prior runs)",
                                    "url": i["url"],
                                    "needs_you": True,
                                    "kind": "died-before-starting",
                                    "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                    "what": f"{i.get('prior_runs')} prior runs",
                                    "age_min": i.get("idle_min", -1),
                                    "verb": "log"})
                elif i.get("work_state") == "ACTIVE":
                    # Presence-and-validity, not truthiness: 0 commits and -1
                    # (could-not-count sentinel, see the comment at issue_json's
                    # `commits` computation above) must not collapse to the same
                    # branch. A truthy check would render a real 0 as "N prior
                    # runs" (losing "the agent produced nothing") and render -1
                    # as "-1 commits" (a sentinel presented as a fact).
                    _c = i.get("commits")
                    _what = (f"{_c} commits" if isinstance(_c, int) and _c >= 0
                             else f"{i.get('prior_runs')} prior runs")
                    alerts.append({"level": "warn",
                                    "msg": f"{r['repo']}#{i['issue']} died mid-flight",
                                    "url": i["url"],
                                    "needs_you": True,
                                    "kind": "dead-mid-flight",
                                    "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                    "what": _what,
                                    "age_min": i.get("idle_min", -1),
                                    "verb": "log"})
            if i.get("work_state") == "REVIEW" and i.get("orch_alive"):
                alerts.append({"level": "warn",
                                "msg": f"{r['repo']}#{i['issue']} still running after PR",
                                "url": i["url"],
                                "needs_you": True,
                                "kind": "still-running-after-pr",
                                "slug": r["slug"], "repo": r["repo"], "issue": i["issue"],
                                "what": f"PR #{i['pr']}" if i.get("pr") else "no PR",
                                "age_min": i.get("idle_min", -1),
                                "verb": "tail"})

    if isinstance(tick["minutes_since_last"], int) and tick["minutes_since_last"] > NUDGE_IDLE_MINS:
        # The taxonomy's "oracle / tick stale" row (4.1). It names no unit of
        # work, so it carries no `kind` and the widget renders it from `msg` --
        # but it IS a needs-you row, hence the key.
        alerts.append({"level": "error", "needs_you": True,
                        "msg": f"tick stale: no tick in {tick['minutes_since_last']} minutes"})

    dop_key = key_for("dashboard-op")
    dop_alive = alive(dop_key)
    dop_activity = core.transcript_activity(core.cwd_for("dashboard-op"))

    # Deployment drift: the checkout serving this dashboard is behind its
    # base ref, so everything derived here may be from stale code.
    _dep = host.get("deploy") or {}
    _behind = _dep.get("behind", -1)
    if isinstance(_behind, int) and _behind > 0:
        alerts.append({"level": "error", "key": "deploy-behind",
                        "needs_you": True,
                        "msg": (f"deployment is {_behind} commit(s) behind "
                                f"{_dep.get('base') or 'its base'} "
                                f"(running {_dep.get('commit') or 'unknown'} on "
                                f"{_dep.get('branch') or 'unknown'}) — this page is NOT "
                                f"running current code; pull and restart")})

    # Auto-pull stuck: a separate fact from deploy-behind above. Behind-and-
    # pulling is normal and self-correcting (the timer will fix it on its own
    # next tick); behind-and-the-puller-is-repeatedly-failing is not, and
    # deploy-behind alone never says which one this is.
    _pull_alert = pull_stuck_alert(_dep.get("pull_consecutive_failures"),
                                    _dep.get("pull_last_outcome"))
    if _pull_alert:
        alerts.append(_pull_alert)

    # orch#421: rows are capped, the count is not. See unattached_json().
    _unat_rows, _unat_total = unattached_json()

    return {
        "awaiting": awaiting,
        "alerts": alerts,
        "generated": now_iso(),
        # The idle threshold every "wedged"/"checking Nm" row is judged
        # against, published so the client reads the one value the tick and
        # this file already share instead of hardcoding a copy that silently
        # drifts the moment NUDGE_IDLE_MINS is tuned via its env var.
        "nudge_idle_mins": NUDGE_IDLE_MINS,
        "tick": tick,
        "host": host,
        "dashboard_op": {
            "key": dop_key, "alive": dop_alive, "prior_runs": prior_runs(dop_key),
            "activity": dop_activity,
            **journal_stats("dashboard"),
        },
        "repos": repo_rows,
        "agent_sessions": agent_sessions_json(),
        "unattached_sessions": _unat_rows,
        "unattached_total": _unat_total,
    }


if __name__ == "__main__":
    print(json.dumps(build(), separators=(",", ":")))
