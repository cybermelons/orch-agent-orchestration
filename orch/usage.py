"""Read-only usage-inspection query layer over orch's existing state.

Answers orch#298's five questions by JOINING three things that already
exist on disk -- claude transcripts, the session ledger, and the repo
journals -- rather than adding a fourth store. Writes nothing. Adds no
dependency (stdlib only).

House style, copied from core.transitions() (core.py:3722): derive on read,
refuse to interpolate missing data, and name the limitation in the
docstring of the function that hits it.

The join key is `cwd` (transcript row) == `workdir` (ledger row), not the
transcript directory's path. See classify()'s docstring for why the
path-substring heuristic alone is not trustworthy.
"""
import json
from pathlib import Path

from . import core

# Only `landed` journal rows carry an issue number today (measured 2/2).
# started/deferred/escalated/retracted/filed all carry 0/N. This is
# orch#280's ground, not this module's to fix -- exposed as a constant so a
# caller can see *why* "what did the loop decide about issue N" has no
# general answer, instead of that gap being silently absent from every
# result here.
# orch#421 added `landed-unreviewed`: the same landing, recorded with its
# review status, written by merge_pr for any merge where no orch:review:v1
# block was found. Both spellings are a LANDING and must be treated as one
# here -- keying on the bare string "landed" made an unreviewed merge read as
# "never landed", which is orch#280's false-negative shape (see the comment
# above) reintroduced for exactly the merges orch#421 exists to make MORE
# visible. Any future landing variant belongs in this set.
LANDED_EVENTS = frozenset({"landed", "landed-unreviewed"})
EVENTS_WITH_ISSUE_IDENTITY = frozenset(LANDED_EVENTS)


def _bound(v):
    """A since/until bound as epoch seconds, from either an ISO string or a
    number. None passes through as "no bound".

    Both shapes reach these queries in practice: journal and ledger rows
    carry ISO strings, while a caller doing date arithmetic naturally holds
    epoch floats from time.time(). core._at_epoch() parses only the string
    shape and returns None for a float -- and None is indistinguishable
    from "no bound", so a float bound silently disabled all filtering and
    returned gaps from outside the window (measured: 89707% of a 32.6h
    window before this existed). Failing open on a bound the caller
    explicitly passed is the worst option available, so accept both.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return core._at_epoch(v)


def _transcript_files(root=None):
    """Every <root>/*/*.jsonl, current layout, all sessions. `root` defaults
    to ~/.claude/projects.

    There is no per-cwd shortcut here: this function's job is the reverse
    direction of core.session_dir(cwd) (core.py:687) -- given a row, learn
    its cwd -- so it has to walk everything once. Callers that already know
    a cwd should build session_dir(cwd) directly and stay on that one
    coupling, as core.py:680-685 asks; this is the other side of that same
    seam, used only for the windowed/all-transcripts queries.

    `root` exists so a caller can point the walk at a tree it owns. The
    default tree is shared with every live Claude Code session on this
    machine, which makes any exact count over it a moving target: a test
    that reads it twice and subtracts can see a partially-flushed line
    counted once and not the second time, and no choice of comparison
    operator repairs that (orch#370). Production callers pass nothing and
    get the real tree.
    """
    root = Path(root) if root is not None else Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"))


def transcript_turns(since=None, until=None, root=None):
    """Rows of {at, epoch, role, cwd, file, session_id}, one per user/
    assistant turn, across every transcript file.

    A row counts as a turn when message.role is "user" or "assistant" --
    system/meta lines (mode switches, informational notices) are not turns
    and are excluded silently, that is normal shape, not damage.

    Measured: cwd is present on 96/127 sampled rows. It is captured as-is,
    including None when the line omits it -- callers doing the ledger join
    (classify(), window()) must handle a None cwd themselves; this function
    does not paper over it.

    since/until, when given, are ISO timestamps compared on parsed epoch;
    a row whose timestamp will not parse is EXCLUDED from the result and
    counted in skipped["bad_timestamp"] rather than silently dropped -- the
    caller can see how much of the picture is missing.

    Returns (rows, skipped) where skipped is a dict of
    {"bad_json": n, "not_a_turn": n, "bad_timestamp": n} -- counts of what
    was NOT read into rows and why, so a caller never mistakes a short
    result for a complete one.

    `root` overrides the transcript tree walked, for callers that need a
    tree nothing else is writing to; see _transcript_files().
    """
    # Same rule as window(): a bound that was passed but will not parse is
    # an error. Silently treating it as "no bound" returns the WHOLE tree
    # and calls it a windowed result.
    since_ep = _bound(since)
    until_ep = _bound(until)
    if since is not None and since_ep is None:
        raise ValueError(
            f"transcript_turns(): could not parse `since` bound {since!r}")
    if until is not None and until_ep is None:
        raise ValueError(
            f"transcript_turns(): could not parse `until` bound {until!r}")

    rows = []
    skipped = {"bad_json": 0, "not_a_turn": 0, "bad_timestamp": 0}
    for f in _transcript_files(root):
        try:
            lines = f.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError is a ValueError, not an OSError, so it was
            # not caught here and escaped the function entirely -- breaking
            # the `skipped` contract this docstring makes, that a caller can
            # always see how much of the picture is missing. A live session
            # flushing a transcript mid-multibyte-character is enough to
            # reach it, which is the same partially-flushed-write state
            # orch#370 is about. Skip the file, same as an unreadable one.
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                skipped["bad_json"] += 1
                continue
            if not isinstance(d, dict):
                skipped["bad_json"] += 1
                continue
            msg = d.get("message")
            role = msg.get("role") if isinstance(msg, dict) else None
            if role not in ("user", "assistant"):
                skipped["not_a_turn"] += 1
                continue
            ts = d.get("timestamp")
            ep = core._at_epoch(ts) if ts else None
            if ep is None:
                skipped["bad_timestamp"] += 1
                continue
            if since_ep is not None and ep < since_ep:
                continue
            if until_ep is not None and ep > until_ep:
                continue
            rows.append({
                "at": ts,
                "epoch": ep,
                "role": role,
                "cwd": d.get("cwd"),
                "file": str(f),
                "session_id": d.get("sessionId"),
            })
    return rows, skipped


def ledger_rows():
    """Every state/sessions/*.json row, current AND rolled-aside history,
    as {key, role, scope, repo, issue, workdir, pgid, started, started_epoch,
    log, historical}.

    Reuses core.SESSIONS_DIR and core._ROLLED_ASIDE_SUFFIX exactly as
    live_tracked_pgids() (core.py:1722) does, so rolled-aside rows are
    labelled `historical: True` rather than mixed in with current ones --
    the same distinction that function draws for the same reason. The
    `.settings.json` permission envelope is skipped; it is not a session.

    `scope`/`repo`/`issue` come straight from the row's own `scope` list
    ([] for dashboard-op, [repo] for repo-orch, [repo, issue] for
    issue-orch) -- this is the ONLY reliable issue-identity join available
    today (see EVENTS_WITH_ISSUE_IDENTITY).
    """
    out = []
    if not core.SESSIONS_DIR.is_dir():
        return out
    for p in core.SESSIONS_DIR.glob("*.json"):
        name = p.name
        if name.endswith(".settings.json"):
            continue
        historical = bool(core._ROLLED_ASIDE_SUFFIX.search(name))
        try:
            row = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(row, dict):
            continue
        scope = row.get("scope") or []
        repo = scope[0] if len(scope) >= 1 else None
        issue = scope[1] if len(scope) >= 2 else None
        started = row.get("started")
        out.append({
            "key": name[:-len(".json")],
            "role": row.get("role"),
            "scope": scope,
            "repo": repo,
            "issue": issue,
            "workdir": row.get("workdir"),
            "pgid": row.get("pgid"),
            "started": started,
            "started_epoch": core._at_epoch(started) if started else None,
            "log": row.get("log"),
            "historical": historical,
        })
    return out


def journal_rows(repo=None):
    """Journal rows for one repo (or all repos under state/repos/*), with
    both journal schemas normalized to one shape and timestamps parsed.

    MEASURED (this worktree, 2026-09-16): the orch journal alone carries 22
    rows in a LEGACY shape -- {"at", "level", "repo", "action", "summary"}
    -- instead of the current {"at", "actor", "event", ...}. Both parse as
    valid JSON; a reader that filters on `event` drops all 22 silently.
    Every row here is normalized to carry both `actor` (from `actor`, or
    `level` on a legacy row) and `event` (from `event`, or `action` on a
    legacy row), plus the original row under `raw` so nothing is lost.

    Returns (rows, counts) where counts is
    {"current_schema": n, "legacy_schema": n, "unparseable": n} -- rows
    that fail json.loads entirely land in "unparseable", not dropped
    uncounted.
    """
    if repo is not None:
        repos = [repo]
    else:
        root = core.JOURNAL_ROOT / "repos"
        repos = sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    out = []
    counts = {"current_schema": 0, "legacy_schema": 0, "unparseable": 0}
    for slug in repos:
        f = core.journal_path("repo", slug)
        try:
            text = f.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                counts["unparseable"] += 1
                continue
            if not isinstance(row, dict):
                counts["unparseable"] += 1
                continue
            at = row.get("at")
            if "event" in row:
                counts["current_schema"] += 1
                actor = row.get("actor")
                event = row.get("event")
            else:
                counts["legacy_schema"] += 1
                actor = row.get("level")
                event = row.get("action")
            out.append({
                "at": at,
                "epoch": core._at_epoch(at) if at else None,
                "repo": row.get("repo", slug),
                "actor": actor,
                "event": event,
                "raw": row,
            })
    return out, counts


# --- classification ----------------------------------------------------------

def classify(cwd, workdirs):
    """"agent" | "human" | "unknown" for a transcript row's cwd.

    Ledger match FIRST: cwd exactly equal to a known ledger workdir (pass
    the set of `workdir` values from ledger_rows()) is unambiguous --
    that directory was handed to a spawned orch role. Path substring
    (`-wt-`, `worktree`) is only a SECOND, weaker signal for cwds no
    ledger row currently covers (a past agent run whose ledger row rolled
    off, for instance).

    A cwd that fires NEITHER agent rule is "human" -- a normal directory
    a person is sitting in is the expected default shape here, exactly as
    the issue body's own framing has it ("each would be counted as a human
    typing" describing the OLD heuristic's failure to catch real agent
    dirs -- the fix is a better agent signal, not a third default).

    "unknown" is reserved for when there is NO signal to classify at all
    -- cwd itself missing from the row (measured: absent on 31/127 sampled
    transcript rows). That is a REQUIRED loud-failure signal, never folded
    into "human": a caller (idle_windows) must be able to see how much of
    the picture has no cwd to classify, rather than silently treating a
    missing field as either a real person or a real agent.
    """
    if cwd is None:
        return "unknown"
    if cwd in workdirs:
        return "agent"
    # Matched per PATH SEGMENT, not as a bare substring. A plain
    # `"worktree" in cwd` also fires on a human's own directory that merely
    # contains the word -- ~/src/worktree-manager -- and that direction is
    # worse than the false negative the issue body warns about: an agent dir
    # missed only under-counts agents, but a human dir called `agent`
    # deletes real typing from human_turns and EXTENDS an idle gap straight
    # across it, corrupting the one number question 1 exists to produce.
    parts = [p for p in cwd.split("/") if p]
    if any(p == "worktree" or p == "worktrees" or "-wt-" in p for p in parts):
        return "agent"
    return "human"


def idle_windows(threshold_min, since=None, until=None, root=None):
    """Gaps >= threshold_min between consecutive HUMAN turns (question 1
    -- "how much of the window was idle").

    Turns are classified via classify() against the ledger's workdirs, so
    an agent's own turns never count as human activity and never break an
    idle gap. Each gap is {"start", "end", "minutes", "before", "after"}
    where before/after are the bracketing human turn rows, so a caller can
    drill into what happened on either side.

    A gap is INCLUSIVE at the threshold boundary: a gap of exactly
    threshold_min minutes counts as idle (>=, not >).

    When since/until are given, the LEADING gap (since -> first human turn)
    and the TRAILING gap (last human turn -> until) are included, each
    marked `edge: "leading"` / `"trailing"`. Gaps strictly between observed
    turns are the obvious half of this question and the edges are the half
    that silently went missing: asked "how idle was 00:00-08:00" about a
    window whose only human turns are at 07:50 and 07:55, between-turns-only
    reports ZERO idle, when 7h50m of the 8h had nobody typing. The issue's
    own headline ("78% of a 32.6h window had no human typing") is computed
    over a window, not over the turns inside it, so a caller dividing by
    the window length needs the edges or the percentage is simply wrong.

    `human_turns` is returned too. Without it, "no gaps found" and "there
    were no turns to find gaps between" are the same empty list -- and the
    second one is not a busy window, it is no data.

    Also returns unknown_count -- how many turns in the window classified
    as "unknown" rather than human or agent. A large unknown_count means
    the idle picture is untrustworthy (real human turns may be hiding in
    "unknown" and inflating apparent idle time, or vice versa): the caller
    must be able to see that rather than trust a clean-looking gap list.

    `root` overrides the transcript tree walked; see _transcript_files().
    """
    turns, _skipped = transcript_turns(since=since, until=until, root=root)
    ledger = ledger_rows()
    workdirs = {r["workdir"] for r in ledger if r["workdir"]}

    human_turns = []
    unknown_count = 0
    for t in turns:
        cls = classify(t["cwd"], workdirs)
        if cls == "unknown":
            unknown_count += 1
        if cls == "human":
            human_turns.append(t)

    human_turns.sort(key=lambda t: t["epoch"])
    gaps = []

    since_ep = _bound(since)
    until_ep = _bound(until)

    def _add(start_ep, end_ep, before, after, edge):
        minutes = (end_ep - start_ep) / 60.0
        if minutes >= threshold_min:
            gaps.append({
                "start": before["at"] if before else since,
                "end": after["at"] if after else until,
                "minutes": minutes,
                "before": before, "after": after, "edge": edge,
            })

    if since_ep is not None:
        # No human turns at all in a bounded window means the WHOLE window
        # was idle -- not that there is nothing to report.
        first_ep = human_turns[0]["epoch"] if human_turns else until_ep
        if first_ep is not None:
            _add(since_ep, first_ep,
                 None, human_turns[0] if human_turns else None, "leading")

    for a, b in zip(human_turns, human_turns[1:]):
        _add(a["epoch"], b["epoch"], a, b, None)

    if until_ep is not None and human_turns:
        _add(human_turns[-1]["epoch"], until_ep, human_turns[-1], None,
             "trailing")

    return {
        "gaps": gaps,
        "unknown_count": unknown_count,
        "human_turns": len(human_turns),
    }


# --- the join ------------------------------------------------------------

def group_by(rows, key):
    """dict[key(row)] -> [rows], the one grouping helper window()'s callers
    share instead of five bespoke by-transcript / by-repo-issue / by-hour
    functions. `key` is a callable, e.g. lambda r: r["cwd"], or for
    hour-of-day bucketing (question 5):

        import time
        group_by(rows, lambda r: time.localtime(r["epoch"]).tm_hour)

    Use localtime, not epoch // 3600 % 24: the latter buckets by UTC hour
    and silently shifts every bucket by the UTC offset, which moves the
    issue's "22:00 peak" to a different hour of the clock.
    """
    out = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def window(since, until):
    """Everything overlapping [since, until) (question 2): ledger sessions
    started in-window, their repo+issue from the ledger's own scope,
    journal rows, and transcript turns.

    Questions 4 ("busiest transcript") and 5 ("busiest repo/issue or hour")
    are both groupings of these same rows -- use group_by() on the
    returned lists (by "cwd"/"file" for Q4, by ("repo","issue") or by
    epoch // 3600 for Q5) rather than reaching for a new function.
    """
    since_ep = _bound(since)
    until_ep = _bound(until)
    # A bound the caller PASSED but that would not parse is an error, not an
    # absent bound. Letting it through split this function against itself:
    # the two comprehensions below dropped every row (a None bound failed
    # their condition) while transcript_turns skipped its own bound check
    # and returned every turn ever recorded -- "nothing ran in this window,
    # but you typed 40,000 turns", with nothing in the result saying the
    # bound was rejected. Raise instead: both halves now agree, loudly.
    if since is not None and since_ep is None:
        raise ValueError(f"window(): could not parse `since` bound {since!r}")
    if until is not None and until_ep is None:
        raise ValueError(f"window(): could not parse `until` bound {until!r}")

    turns, turn_skipped = transcript_turns(since=since, until=until)

    sessions = [
        r for r in ledger_rows()
        if r["started_epoch"] is not None
        and since_ep <= r["started_epoch"] <= until_ep
    ]

    j_rows, j_counts = journal_rows()
    journal = [
        r for r in j_rows
        if r["epoch"] is not None
        and since_ep <= r["epoch"] <= until_ep
    ]

    return {
        "sessions": sessions,
        "journal": journal,
        "journal_counts": j_counts,
        "turns": turns,
        "turn_skipped": turn_skipped,
    }


def around_merge(repo, pr):
    """Question 3: what happened around a PR's merge.

    Finds the journal `landed` row for (repo, pr) plus the journal rows
    immediately either side of it, and the ledger session (current or
    historical) whose scope matches this repo and, when the landed row
    carries one, this issue.

    MEASURED REALITY (this worktree, 2026-09-16), encoded rather than
    papered over: only 2 `landed` rows exist in the orch journal (PR 287
    written by hand, PR 300 written mechanically after orch#280 landed).
    PR 288 and PR 293 merged with NO landed row -- eaten by the merge
    defect orch#280 fixed. When no landed row exists for the requested PR,
    this returns `landed: None` with a `reason` string. It NEVER
    synthesizes or infers a landing from surrounding rows: a tool that
    silently interpolates a missing landing is worse than one that shows
    the hole.
    """
    rows, counts = journal_rows(repo=repo)
    rows_sorted = sorted(
        (r for r in rows if r["epoch"] is not None),
        key=lambda r: r["epoch"],
    )
    # Rows whose `at` will not parse are excluded from the ordering above --
    # there is nowhere to put them on a timeline. Counted, never silently
    # dropped: a caller comparing len(before)+len(after) against the journal
    # must be able to see that something bracketing this merge is unplaceable.
    undated = sum(1 for r in rows if r["epoch"] is None)

    # `pr` may arrive as a string (from a URL segment, or `gh ... --json
    # number` through a shell) while the journal holds an int. Comparing
    # with == reported a landed row sitting right there as missing. The
    # session match below already coerces with str(); this now matches it.
    def _is_landed(r):
        return r["event"] in LANDED_EVENTS and str(r["raw"].get("pr")) == str(pr)

    landed = None
    idx = None
    for i, r in enumerate(rows_sorted):
        if _is_landed(r):
            landed = r
            idx = i
            break

    if landed is None:
        # A landed row that EXISTS but has an unreadable `at` is a different
        # fact from no row at all, and saying "the journal has no record"
        # about it asserts something false -- the exact sin this function
        # refuses for merges. Distinguish the two.
        unreadable = [r for r in rows if r["epoch"] is None and _is_landed(r)]
        if unreadable:
            return {
                "landed": None,
                "reason": (
                    f"a 'landed' journal row for {repo} PR {pr} EXISTS but "
                    f"its timestamp could not be parsed "
                    f"({unreadable[0]['raw'].get('at')!r}), so it cannot be "
                    f"placed on the timeline. The record is present and "
                    f"unreadable -- this is NOT the same as no record."
                ),
                "before": [],
                "after": [],
                "session": None,
                "journal_counts": counts,
                "undated_rows": undated,
                "landed_row_unreadable": unreadable[0],
            }
        return {
            "landed": None,
            "reason": (
                f"no landing journal row for {repo} PR {pr}. Only "
                f"{sum(1 for r in rows if r['event'] in LANDED_EVENTS)} landing "
                f"row(s) exist in this repo's journal; a merge can land "
                f"with no row (orch#280's merge defect ate PR 288 and "
                f"PR 293 this way before it was fixed) -- absence here "
                f"means the journal has no record, not that nothing "
                f"landed."
            ),
            "before": [],
            "after": [],
            "session": None,
            "journal_counts": counts,
            "undated_rows": undated,
        }

    before = rows_sorted[max(0, idx - 5):idx]
    after = rows_sorted[idx + 1:idx + 6]

    # The session must be matched on issue identity, never on repo alone.
    # Matching on repo and taking the first hit returned whatever
    # SESSIONS_DIR.glob() happened to yield first -- filesystem order, not
    # time order -- so a repo-orch row with no issue, or a rolled-aside run
    # from days earlier, could be handed back as "the session that produced
    # this PR". A confidently wrong session is worse than None for the same
    # reason a fabricated landing is: it is indistinguishable from a real
    # match in the return shape.
    issue = landed["raw"].get("issue")
    session = None
    session_reason = None
    if issue is None:
        session_reason = (
            f"the landed row for {repo} PR {pr} carries no issue number, so "
            f"no session can be matched to it. Only `landed` rows carry "
            f"issue identity at all (see EVENTS_WITH_ISSUE_IDENTITY); "
            f"matching on repo alone would return an arbitrary session."
        )
    else:
        matches = [
            r for r in ledger_rows()
            if r["repo"] == repo
            and r["issue"] is not None
            and str(r["issue"]) == str(issue)
        ]
        matches.sort(key=lambda r: (r["started_epoch"] is None,
                                    r["started_epoch"]))
        if matches:
            # The run active at the merge: latest start at or before it,
            # else the earliest run for the issue.
            at_or_before = [r for r in matches
                            if r["started_epoch"] is not None
                            and r["started_epoch"] <= landed["epoch"]]
            session = at_or_before[-1] if at_or_before else matches[0]
        else:
            session_reason = (
                f"no ledger session for {repo} issue {issue}; its row may "
                f"have rolled off."
            )

    return {
        "landed": landed,
        "reason": None,
        "before": before,
        "after": after,
        "session": session,
        "session_reason": session_reason,
        "journal_counts": counts,
        "undated_rows": undated,
    }
