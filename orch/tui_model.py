#!/usr/bin/env python3
"""The TUI's model layer: status.json -> display rows. No curses, no I/O.

The terminal view and the web dashboard render the SAME architecture, written
down in docs/DASHBOARD-IA.md: three zones in strict attention order, Zone A
(needs you) first. This module is the half that can be unit-tested, so every
function below is pure except load_feed, which is the one boundary that reads
a file.

The feed is read FROM DISK, never over HTTP, because the point of a terminal
view is that it still works when orch-web is down — the exact failure #86
fixed, which left the dashboard dark until a human happened to notice.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROW_KINDS = (
    "zone",      # a zone header (A/B/C)
    "repo",      # a repo group head inside Zone B
    "issue",     # one tracked issue
    "alert",     # an error/warn alert
    "awaiting",  # a unit awaiting review
    "session",   # a ledger or unattached session
    "fact",      # a label/value pair
    "empty",     # "nothing needs you" and its siblings
)

# state wins over work_state for exactly these two. See IA section 4: work_state
# is derived from the branch and PR alone, so with no commits and no PR it reads
# CLAIMED whether or not anyone actually holds the issue (core.py:1312). Trusting
# work_state blindly paints an unpicked issue like one an agent is mid-work on.
_STATE_WINS = ("UNCLAIMED", "ABANDONED")


@dataclass
class Row:
    text: str
    kind: str
    key: str
    children: list = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    depth: int = 0


# --- the boundary ---------------------------------------------------------------

def load_feed(path):
    """(feed, None) or (None, error). Never raises: a dead feed is the normal case."""
    p = Path(path)
    try:
        raw = p.read_text()
    except FileNotFoundError:
        return None, f"no feed at {p}"
    except OSError as e:
        return None, f"cannot read {p}: {e.strerror or e}"
    try:
        d = json.loads(raw)
    except ValueError as e:
        return None, f"bad JSON in {p}: {e}"
    if not isinstance(d, dict):
        return None, f"bad feed in {p}: expected an object"
    return d, None


# --- the single pill ------------------------------------------------------------

def pill_state(issue):
    """The ONE state pill for an issue. The rule has exactly one home: this one.

    Zone A and Zone B both show a pill for the same issue, so a rule written
    inline in a zone is a rule the other zone cannot reach. That is a real bug
    the web review caught: an issue with a red PR (work_state=BLOCKED) and an
    agent-stuck label (state=ABANDONED) rendered two different pills on one
    screen. Zone A's BLOCKED filter routes through here too, for the same reason.
    """
    issue = issue or {}
    state = (issue.get("state") or "").strip()
    work = (issue.get("work_state") or "").strip()
    if state in _STATE_WINS:
        return state
    return work or state or "?"


def forge_of(url):
    """The concrete host behind a URL: "github" or e.g. "gitea.local:3000"; "" if none.

    Named on the repo row because the live set has cybermelons/orch on GitHub
    and cybermelon/gita-lectures on Gitea — one character of owner apart, with
    different auth, reachability and PR path shapes behind them (#68).
    """
    if not url:
        return ""
    try:
        netloc = urlparse(str(url)).netloc
    except ValueError:
        return ""
    if not netloc:
        return ""
    host = netloc.split("@")[-1]
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    return host


# --- small formatting helpers ---------------------------------------------------

def _mins(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n < 0:
        return "-"
    if n < 60:
        return f"{n}m"
    if n < 1440:
        return f"{n // 60}h"
    return f"{n // 1440}d"


def _age(ts):
    """A unix timestamp rendered as an age, for facts the feed carries raw.

    dashboard_op.activity is seconds since the epoch (feed.py does arithmetic
    on it as such). Printed raw it is 10 digits the operator has to convert in
    their head, and the question they are asking -- is this lease stale --
    is exactly the one the raw number does not answer.
    """
    try:
        secs = time.time() - int(ts)
    except (TypeError, ValueError):
        return "?"
    return _mins(int(secs // 60)) if secs >= 0 else "-"


def _rows(feed, key):
    """A top-level list from the feed, defensively: a partial feed is normal."""
    v = (feed or {}).get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _issues(repo):
    v = (repo or {}).get("issues")
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _repo_forge(repo):
    """Derived from whatever URL the repo already carries — no new feed key."""
    for i in _issues(repo):
        f = forge_of(i.get("url"))
        if f:
            return f
    return ""


def _issue_text(issue, repo_slug=""):
    n = issue.get("issue")
    title = (issue.get("title") or "").strip()
    bits = [f"#{n}" if n is not None else "#?", pill_state(issue)]
    if issue.get("pr"):
        bits.append(f"PR {issue['pr']}")
    if issue.get("contended"):
        bits.append("CONTENDED")
    if issue.get("orch_alive"):
        bits.append("orch")
    idle = issue.get("idle_min")
    if isinstance(idle, int) and idle >= 0:
        bits.append(f"idle {_mins(idle)}")
    head = " ".join(bits)
    if repo_slug:
        head = f"{repo_slug} {head}"
    return f"{head}  {title}".rstrip()


def _issue_facts(issue):
    """The detail that lives behind a drill-in, per IA section 3."""
    out = [
        ("state", issue.get("state") or "-"),
        ("work_state", issue.get("work_state") or "-"),
        ("branch", issue.get("branch") or "-"),
        ("commits", str(issue.get("commits", "-"))),
        ("labels", ", ".join(issue.get("labels") or []) or "-"),
        ("ready", str(bool(issue.get("ready")))),
        ("idle", _mins(issue.get("idle_min"))),
        ("orch", f"alive={bool(issue.get('orch_alive'))} prior={issue.get('prior_runs', 0)}"),
        ("url", issue.get("url") or "-"),
    ]
    if issue.get("pr"):
        out.append(("pr", f"{issue['pr']} {issue.get('pr_url') or ''}".strip()))
    brief = issue.get("brief")
    if isinstance(brief, dict) and brief.get("text"):
        stale = " (stale)" if brief.get("stale") else ""
        out.append(("brief", f"{brief['text']}{stale}"))
    sessions = issue.get("sessions")
    if isinstance(sessions, list):
        live = sum(1 for s in sessions if isinstance(s, dict) and s.get("live"))
        out.append(("sessions", f"{len(sessions)} ({live} live)"))
        # The id and the resume string, not only the count. IA section 3 lists
        # "sessions, workers, resume strings" as belonging behind the issue
        # drill-in, and the resume string is the whole reason to drill in: with
        # only a count the operator has to leave the TUI and grep status.json
        # by hand to attach to the agent, which is the round trip this exists
        # to remove.
        for n, s in enumerate(s for s in sessions if isinstance(s, dict)):
            mark = " live" if s.get("live") else ""
            turns = s.get("turns")
            out.append((f"session {n}", f"{s.get('id') or '-'}{mark}"
                                        f"{f' {turns} turns' if turns else ''}"))
            if s.get("resume"):
                out.append((f"resume {n}", str(s["resume"])))
    return out


def _fact_rows(pairs, prefix, depth):
    return [Row(text=f"{k}: {v}", kind="fact", key=f"{prefix}.{k}", children=[],
                payload={}, depth=depth)
            for k, v in pairs]


def _issue_row(issue, repo, depth):
    slug = (repo or {}).get("slug") or ""
    key = f"issue.{slug}.{issue.get('issue')}"
    return Row(
        text=_issue_text(issue),
        kind="issue",
        key=key,
        children=_fact_rows(_issue_facts(issue), key, depth + 1),
        payload=issue,
        depth=depth,
    )


# --- Zone A ---------------------------------------------------------------------

def zone_a(feed):
    """Everything waiting on a human, merged into ONE list regardless of source."""
    out = []
    for n, a in enumerate(_rows(feed, "alerts")):
        level = (a.get("level") or "").lower()
        if level not in ("error", "warn"):
            continue  # info alerts are a Zone C count; by definition they can wait
        out.append(Row(text=f"{level.upper()}  {a.get('msg') or ''}".rstrip(),
                       kind="alert", key=f"alert.{n}",
                       children=_fact_rows([("url", a.get("url") or "-")], f"alert.{n}", 1),
                       payload=a, depth=1))

    for w in _rows(feed, "awaiting"):
        slug = w.get("slug") or w.get("repo") or "?"
        key = f"awaiting.{slug}.{w.get('issue')}"
        pr = f" PR {w['pr']}" if w.get("pr") else ""
        facts = [("repo", w.get("repo") or "-"), ("url", w.get("url") or "-")]
        if w.get("pr_url"):
            facts.append(("pr_url", w["pr_url"]))
        review = w.get("review")
        if review:
            facts.append(("review", str(review)))
        out.append(Row(text=f"AWAITING REVIEW  {slug}#{w.get('issue')}{pr}  {w.get('title') or ''}".rstrip(),
                       kind="awaiting", key=key,
                       children=_fact_rows(facts, key, 1), payload=w, depth=1))

    for r in _rows(feed, "repos"):
        for i in _issues(r):
            # pill_state, not raw work_state: the filter and the pill must agree.
            #
            # ABANDONED is here alongside BLOCKED because pill_state collapses
            # two facts into one, and filtering on BLOCKED alone loses one of
            # them. An issue an agent gave up on (agent-stuck -> ABANDONED)
            # normally ALSO has a red PR (work_state BLOCKED); the pill resolves
            # that pair to ABANDONED, so a BLOCKED-only filter drops the very
            # issue that most needs a human and Zone A reads "nothing needs you".
            # agent-stuck is an agent asking for a person by name, so it belongs
            # in Zone A on its own, not only when some other axis also fires.
            if pill_state(i) in ("BLOCKED", "ABANDONED") or i.get("contended"):
                row = _issue_row(i, r, 1)
                row.text = f"{(r.get('slug') or '')} {row.text}".strip()
                out.append(row)
        missing = r.get("labels_missing")
        if isinstance(missing, list) and missing:
            key = f"labels.{r.get('slug') or r.get('repo')}"
            out.append(Row(text=f"LABELS MISSING  {r.get('repo') or '?'}: {', '.join(str(m) for m in missing)}",
                           kind="alert", key=key, children=[], payload=r, depth=1))
        uncovered = r.get("unconsidered")
        if isinstance(uncovered, list) and uncovered:
            slug = r.get('slug') or r.get('repo')
            key = f"unconsidered.{slug}"
            sample = ", ".join(f"#{n}" for n in uncovered[:3])
            kids = [Row(text=f"#{n}  untriaged", kind="issue",
                        key=f"unconsidered.{slug}.{n}",
                        children=[], payload={"slug": slug, "issue": n}, depth=2)
                    for n in uncovered]
            out.append(Row(text=f"UNTRIAGED  {r.get('slug') or r.get('repo') or '?'}: "
                                f"{len(uncovered)} issue(s) not consolidated "
                                f"since filing ({sample})",
                           kind="alert", key=key, children=kids, payload=r, depth=1))
        # Orphaned PRs: open, on an issue-<n> branch, issue no longer open.
        # Zone A because there is nobody else to raise them -- issue-orch is
        # spawned per ISSUE, so a closed issue is never re-entered and its open
        # PR has no owner, no label and no condition. It reads as handled
        # because it exists and is green (orch#279). Without a row here the
        # feed carries the fact and nothing shows it to a person.
        # Same tolerance tick._orphan_pr_numbers applies, for the same reason
        # and against the same input: a stale or hand-edited cached feed can
        # hold a non-dict here, and an unguarded o.get() would raise and blank
        # the whole of Zone A -- the one place a human sees what needs them.
        orphans = [o for o in (r.get("orphan_prs") or []) if isinstance(o, dict)]
        if orphans:
            slug = r.get('slug') or r.get('repo')
            key = f"orphanpr.{slug}"
            sample = ", ".join(f"#{o.get('number')}" for o in orphans[:3])
            kids = [Row(text=f"PR #{o.get('number')}  {o.get('branch')}  "
                             f"issue #{o.get('issue')} "
                             f"{o.get('reason') or 'unlabelled'}",
                        kind="pr", key=f"orphanpr.{slug}.{o.get('number')}",
                        children=[], payload={"slug": slug, "pr": o.get('number'),
                                              "issue": o.get('issue'),
                                              "reason": o.get('reason')}, depth=2)
                    for o in orphans]
            out.append(Row(text=f"ORPHAN PR  {slug or '?'}: "
                                f"{len(orphans)} open PR(s) nothing will surface "
                                f"({sample})",
                           kind="alert", key=key, children=kids, payload=r, depth=1))

    if not out:
        out.append(Row(text="nothing needs you", kind="empty", key="zone-a.empty",
                       children=[], payload={}, depth=1))
    return out


# --- Zone B ---------------------------------------------------------------------

def zone_b(feed):
    """One row per tracked issue, grouped by repo. The work orch does unaided."""
    out = []
    for r in _rows(feed, "repos"):
        slug = r.get("slug") or r.get("repo") or "?"
        issues = _issues(r)
        forge = _repo_forge(r)
        counts = r.get("counts") if isinstance(r.get("counts"), dict) else {}
        summary = " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
        head = f"{r.get('repo') or slug}"
        if forge:
            head += f"  [{forge}]"
        if not r.get("ok", True):
            head += "  NOT OK"
        if summary:
            head += f"  {summary}"
        kids = [_issue_row(i, r, 2) for i in issues]
        if not kids:
            kids = [Row(text="no tracked issues", kind="empty",
                        key=f"repo.{slug}.empty", children=[], payload={}, depth=2)]
        out.append(Row(text=head, kind="repo", key=f"repo.{slug}",
                       children=kids, payload=r, depth=1))
    if not out:
        out.append(Row(text="no repos", kind="empty", key="zone-b.empty",
                       children=[], payload={}, depth=1))
    return out


# --- Zone C ---------------------------------------------------------------------

def orch_facts(feed):
    """(label, value) pairs for orch itself — the view #73 says does not exist.

    The four dashboard_op facts are the point: liveness, last event, the ack
    lease and the digest are all in the feed and none is rendered on the page.
    """
    feed = feed or {}
    tick = feed.get("tick") if isinstance(feed.get("tick"), dict) else {}
    host = feed.get("host") if isinstance(feed.get("host"), dict) else {}
    dep = host.get("deploy") if isinstance(host.get("deploy"), dict) else {}
    op = feed.get("dashboard_op") if isinstance(feed.get("dashboard_op"), dict) else {}

    behind = dep.get("behind", 0) or 0
    deploy = f"{dep.get('commit') or '?'} on {dep.get('branch') or '?'}"
    if dep.get("is_behind"):
        deploy += f" — BEHIND {behind} of {dep.get('base') or 'base'}"

    return [
        ("tick", f"{_mins(tick.get('minutes_since_last'))} ago ({tick.get('last_run') or '?'})"),
        ("generated", str(feed.get("generated") or "?")),
        ("deploy", deploy),
        ("live orchs", str(host.get("live_orchs", "?"))),
        ("load", str(host.get("load") or "?")),
        ("disk", str(host.get("disk") or "?")),
        ("dashboard-op", f"alive={bool(op.get('alive'))} prior_runs={op.get('prior_runs', 0)}"),
        ("op last event", f"{op.get('last_event') or '-'} at {op.get('last') or '-'}"),
        ("ack lease", f"{op.get('key') or '-'} active {_age(op.get('activity'))} ago"),
        ("digest", f"{op.get('entries', 0)} entries"),
    ]


def zone_c(feed):
    out = [Row(text="orch", kind="session", key="orch.panel",
               children=_fact_rows(orch_facts(feed), "orch.panel", 2),
               payload=(feed or {}).get("dashboard_op") or {}, depth=1)]

    info = [a for a in _rows(feed, "alerts") if (a.get("level") or "").lower() == "info"]
    if info:
        out.append(Row(
            text=f"info alerts ({len(info)})", kind="alert", key="alerts.info",
            children=[Row(text=a.get("msg") or "", kind="alert", key=f"alerts.info.{n}",
                          children=[], payload=a, depth=2)
                      for n, a in enumerate(info)],
            payload={}, depth=1))

    ledger = _rows(feed, "agent_sessions")
    live = sum(1 for s in ledger if s.get("alive"))
    out.append(Row(
        text=f"agent sessions ({len(ledger)}, {live} alive)", kind="session",
        key="ledger",
        children=[Row(text=f"{'*' if s.get('alive') else ' '} {s.get('key') or '?'}"
                           f"  {s.get('role') or '-'}  prior={s.get('prior_runs', 0)}",
                      kind="session", key=f"ledger.{s.get('key') or n}",
                      children=[], payload=s, depth=2)
                  for n, s in enumerate(ledger)],
        payload={}, depth=1))

    unat = _rows(feed, "unattached_sessions")
    # orch#421: the feed caps these rows (orch#416 -- an unbounded list of the
    # operator's own transcript dirs was 74.6% of status.json). `len(unat)` is
    # therefore the PUBLISHED count, not the real one, so report both when they
    # differ: "40 of 991" rather than claiming there are 40. Falls back to the
    # row count when the key is absent, so an older feed still renders.
    _unat_total = feed.get("unattached_total")
    if not isinstance(_unat_total, int) or _unat_total < len(unat):
        _unat_total = len(unat)
    _unat_label = (f"{len(unat)} of {_unat_total}" if _unat_total > len(unat)
                   else f"{len(unat)}")
    out.append(Row(
        text=f"unattached sessions ({_unat_label})", kind="session", key="unattached",
        children=[Row(text=f"{s.get('project') or '?'}  {str(s.get('id') or '')[:8]}"
                           f"  idle {_mins(s.get('idle_min'))}",
                      kind="session", key=f"unattached.{s.get('id') or n}",
                      children=[], payload=s, depth=2)
                  for n, s in enumerate(unat)],
        payload={}, depth=1))
    return out


# --- the whole tree -------------------------------------------------------------

def trust_line(feed):
    """Should the operator believe the rest of the screen? One line, always shown."""
    feed = feed or {}
    if not feed:
        return "NO FEED"
    tick = feed.get("tick") if isinstance(feed.get("tick"), dict) else {}
    host = feed.get("host") if isinstance(feed.get("host"), dict) else {}
    dep = host.get("deploy") if isinstance(host.get("deploy"), dict) else {}
    commit = dep.get("commit") or "?"
    line = f"orch  tick {_mins(tick.get('minutes_since_last'))} ago  deploy {commit}"
    if dep.get("is_behind"):
        # Loud on purpose: behind means the running code is not the code on disk,
        # so every other number on the screen may be describing something else.
        line += f"  !! BEHIND {dep.get('behind', 0)} COMMITS — PULL AND RESTART"
    else:
        line += "  up to date"
    return line


def tree(feed):
    """Zone A, then Zone B, then Zone C, each under a zone header Row."""
    zones = (
        ("A  NEEDS YOU", "zone-a", zone_a),
        ("B  IN FLIGHT", "zone-b", zone_b),
        ("C  THE MACHINE", "zone-c", zone_c),
    )
    out = []
    for title, key, fn in zones:
        try:
            kids = fn(feed)
        except Exception as e:  # a malformed feed must not blank the screen
            kids = [Row(text=f"error building zone: {e}", kind="empty",
                        key=f"{key}.error", children=[], payload={}, depth=1)]
        out.append(Row(text=title, kind="zone", key=key, children=kids,
                       payload={}, depth=0))
    return out
