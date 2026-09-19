#!/usr/bin/env python3
"""orch web UI — the deterministic control surface.

Serves the dashboard and runs a fixed set of actions. Same-origin, so the page
polls without CORS and acts without any bridge.

Binds loopback only; tailscale fronts it. Actions are a closed set with
validated arguments and no shell — a page that can run arbitrary commands is a
remote shell, not a dashboard.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orch import core
from orch import runlog

ORCH = core.ORCH_HOME
# Port must remain env-overridable: two servers cannot fight because the
# fixed-port bind fails for the second.
PORT = int(os.environ.get("ORCH_PORT", "18803"))
# cwd for the tick subprocess: the repo root (dir containing the orch/
# package), so `python -m orch.tick` resolves the package regardless of the
# server's own cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent

SLUG = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")   # owner/repo, or a repo basename
MAXBODY = 64 * 1024

# /events: poll status.json's mtime rather than push. The writer is
# `python -m orch.tick`, a SUBPROCESS of this server (see the do_POST
# below) -- an in-process threading.Event can't see across that boundary,
# only the filesystem can.
EVENTS_POLL_SECS = 1
EVENTS_HEARTBEAT_SECS = 15
EVENTS_MAX_STREAMS = 8
_events_lock = threading.Lock()
_events_count = 0


def repo_path(slug):
    """Only a repo we are already watching. Never an arbitrary path."""
    return core.repo_path_for(slug)


# --- actions: the whole closed set --------------------------------------

def a_kill(a):
    """Ledger-based: TERM the row's pgid, escalate, clear. No more
    .orch.pgid — the ledger row is the lease."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    issue = int(a["issue"])
    key = core.key_for("issue-orch", repo.name, issue)
    row = core.ledger_read(key)
    if not row:
        return {"ok": True, "out": "no session recorded"}
    gid = row.get("pgid")
    core.kill_key(key)
    return {"ok": True, "out": f"killed pgid {gid if gid is not None else '(unstarted)'}"}


def a_nudge(a):
    """Hand the repo-orch a nudge and let it decide. openclaw is cut —
    this reaches an agent via spawn(), never a shell. Logic lives in
    core.nudge_repo — the CLI's `nudge` verb calls the same function."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    return core.nudge_repo(repo, str(a.get("text", "")))


def a_ask(a):
    """Leave a note for the repo-orch. The one open-ended action, and it
    reaches an agent, never a shell. Logic lives in core.ask_repo — the
    CLI's `ask` verb calls the same function.

    It spawns nothing: the text becomes an operator row in the repo
    journal, which the next repo-orch reads on its natural wake
    (docs/UX-REDESIGN.md section 4.2/5). `nudge` is the one launch-now
    verb, for when the operator wants a wake rather than a note."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    return core.ask_repo(repo, a.get("text", ""))


def a_merge(a):
    """Land a REVIEW unit's PR. Thin gate only -- logic lives in
    core.merge_pr, which the CLI could share the same way nudge/ask do. The
    REVIEW re-check happens in core, not here, because this handler's `repo`,
    `issue` and `pr` are operator/page input and none of them is permission
    by itself: core re-derives world state fresh and refuses anything that
    isn't exactly REVIEW on the PR actually attached to this issue's branch,
    so a stale button click or a mismatched PR number can never land."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    issue = int(a["issue"])
    pr = int(a["pr"])
    return core.merge_pr(repo, issue, pr)


def a_tick(_):
    """The operator asking for a tick. Fire-and-forget: orch.tick takes
    .tick.lock itself and skips if one is already running, and touching
    that lock is what resets orch.ticker's interval -- no message to the
    ticker process is needed, or possible."""
    subprocess.Popen([sys.executable, "-m", "orch.tick"], cwd=str(REPO_ROOT))
    return {"ok": True, "out": "tick started"}


def a_watch(a):
    """git-repo paths only. Logic lives in core.watch_repo — the CLI's
    `watch` verb calls the same function."""
    ok, out = core.watch_repo(a["path"])
    return {"ok": ok, "out": out}


def a_unwatch(a):
    """Logic lives in core.unwatch_repo — the CLI's `unwatch` verb calls
    the same function."""
    ok, out = core.unwatch_repo(a["path"])
    return {"ok": ok, "out": out}


def a_candidates(a):
    """Repos found under the configured `#scan=` roots, for the dashboard's
    watch picker. Logic lives in core.scan_repos. Takes NO arguments
    deliberately: validate() below is keyed by argument NAME globally, so any
    action carrying a `path` key inherits that key's SLUG-regex restriction.
    An action with no arguments adds no new key and inherits no restriction."""
    return {"ok": True, "out": "", "repos": core.scan_repos()}


def a_tail(a):
    """Last turns of one session, so you can see what it did without leaving."""
    f = Path(a["file"])
    # Only ever a transcript under the projects tree.
    if not str(f).startswith(str(Path.home() / ".claude" / "projects")) or f.suffix != ".jsonl":
        return {"ok": False, "out": "not a session transcript"}
    if not f.exists():
        return {"ok": False, "out": "no such session"}
    out = []
    for line in f.read_text(errors="replace").splitlines()[-400:]:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        c = d.get("message", {}).get("content", "")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        if c:
            out.append(f"{d['type']}: {str(c)[:600]}")
    return {"ok": True, "out": "\n\n".join(out[-12:]) or "(no text turns)"}


def _gh(argv, cwd=None, stdin_text=None):
    """argv list, shell=False. User input is never interpolated into a
    shell string — the closed action set is only closed if the arguments
    cannot escape into one. cwd selects the repo: gh reads the remote from
    the checkout, so no caller-supplied --repo is needed.

    stdin_text exists so free-form operator prose can reach gh on STDIN
    (`--body-file -`) instead of as an argv element: a multi-line body, or
    one that happens to start with `-`, is fragile or outright ambiguous as
    an argument, and keeping it off argv keeps "arguments cannot escape"
    true for the one action that carries arbitrary text."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                           cwd=str(cwd) if cwd else None, input=stdin_text)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"ok": False, "out": str(e) or "gh failed"}
    out = (p.stdout or "") + (p.stderr or "")
    return {"ok": p.returncode == 0, "out": out.strip()}


def a_assign(a):
    """Label an issue agent-ready; the tick picks it up from there. The repo
    is resolved through the same watched-repo gate as kill/nudge/ask — the
    feed sends a bare basename, which `gh --repo` would reject anyway.

    Routed through the per-repo backend (core.repo_backend): a Gitea-backed
    repo runs `tea issue edit --add-labels`, a GitHub-backed one keeps the
    original `gh issue edit --add-label` argv unchanged."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    be = core.adapter_for(repo)
    return _gh(be.issue_edit_add_label(a["issue"], core.L_READY), cwd=repo)


def a_untracked(a):
    """List a repo's open issues that carry none of orch's labels, so the
    page can offer to start one. Gated to a watched repo exactly like
    a_assign, and read-only: this adds no label-write path of its own --
    `assign` above is still the only thing that marks an issue.

    core.untracked_issues returns None for "could not read", which is NOT
    the same answer as an empty list ("read fine, nothing untracked") and
    must not be flattened into one here -- see orch#66."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    issues = core.untracked_issues(repo)
    if issues is None:
        return {"ok": False, "out": "cannot read issues for this repo"}
    return {"ok": True, "out": "", "issues": issues}


def a_create_issue(a):
    """File a new issue from the dashboard, without leaving it. Gated to a
    watched repo, as above. Routed through the per-repo backend the same way
    a_assign is."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    title, body = str(a["title"]), str(a.get("body", ""))
    be = core.adapter_for(repo)
    return _gh(be.issue_create(title, body), cwd=repo)


def a_reply(a):
    """Answer a review without leaving the dashboard. Gated to a watched
    repo like every repo-taking verb — #19 landed a verb that skipped this
    gate and any SLUG-shaped string reached gh, so the gate is the point of
    the first two lines, not boilerplate.

    On the gh path the operator's prose goes on stdin via `--body-file -`,
    never on argv: see _gh. The first line is a marker in the same
    `orch/<actor> <event>` namespace the agents' journal entries use
    (core._journal_append_issue), so a later reader — human or agent — can
    tell from line one alone that this comment is the operator answering,
    not an agent journalling. That gh argv and its stdin discipline are
    unchanged by the tea routing below.

    Routed to tea via `-d <body>` on a Gitea-backed repo: `tea comments add`
    has no --body-file/stdin option (see tmp_plan-58.md), only a positional
    argument or --description/-d, both of which put the body on argv. That
    is safe here for the same reason core.TEA_ARGV's issue_comment builder
    is safe: `_gh` always calls subprocess.run with an argv LIST and
    shell=False, so no shell ever re-parses the body — a multi-line reply or
    one that happens to start with `-` reaches tea intact as a single argv
    element — and `-d` unconditionally takes the very next token as its
    value, so a body starting with `-` is never mistaken for another flag."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    text = str(a.get("text", ""))
    body = f"orch/operator reply\n{text}\n"
    be = core.adapter_for(repo)
    argv, stdin = be.issue_comment_call(a["issue"], body)
    return _gh(argv, cwd=repo, stdin_text=stdin)


def _set_auto_land(be, repo, issue, on):
    """Write the intended automerge state across the label PAIR.
    Setting one label alone is orch#149's bug: removing auto-land is a
    no-op when the flag comes from the repo default, and adding it never
    clears no-auto-land.

    ADD FIRST, AND ABORT IF IT FAILS. The order is load-bearing, not
    arbitrary. The add is the half that can genuinely fail -- the label may
    not exist on the forge, because ensure_labels only runs at watch time
    and a repo watched before L_NO_AUTOLAND joined ORCH_LABELS never got
    it. The remove, by contrast, succeeds trivially even when there is
    nothing to remove.

    So running the remove after a failed add is what turns a failed write
    into a state change in the WRONG DIRECTION: asked to turn automerge
    off, we would fail to add no-auto-land, succeed at stripping the
    auto-land that was holding the state, and leave the issue resolving to
    the repo default -- ON. The operator asked for off and got on.
    Aborting leaves the issue exactly as it was, which is the only safe
    outcome when we cannot express the intended state."""
    add, remove = (core.L_AUTOLAND, core.L_NO_AUTOLAND) if on else (core.L_NO_AUTOLAND, core.L_AUTOLAND)
    r1 = _gh(be.issue_edit_add_label(issue, add), cwd=repo)
    if not r1["ok"]:
        # The label may simply not exist on this forge yet. ensure_labels
        # runs only at watch time, so a repo watched before L_NO_AUTOLAND
        # joined ORCH_LABELS never received it, and nothing on the tick path
        # would ever create it -- feed.repo_json's label check is
        # deliberately spent only on a repo with NO issues (its COST GUARD
        # comment), which is exactly not this case. So create it on demand
        # and retry once, rather than making the operator re-run `watch`.
        desc = dict(core.ORCH_LABELS).get(add, "")
        _gh(be.label_create(add, core.ORCH_LABEL_COLOR, desc), cwd=repo)
        r1 = _gh(be.issue_edit_add_label(issue, add), cwd=repo)
    if not r1["ok"]:
        return {"ok": False, "out": r1["out"]}
    r2 = _gh(be.issue_edit_remove_label(issue, remove), cwd=repo)
    return {"ok": r2["ok"], "out": f"{r1['out']}\n{r2['out']}".strip()}


def a_review_now(a):
    """Add L_AUTOLAND to an issue: opt it into review-then-merge.

    Reshaped by the 2026-09-14 one-flag verdict and its correction.
    `auto-review` is gone, so there is no label meaning "review but do not
    merge" for this verb to add any more.

    orch#408: this does NOT lift a blocking-finding hold. That hold is
    derived fresh from the PR's last `orch:review:v1` block
    (`core.review_blocks_merge`) and is not stored in any label, so no
    label write -- this one included -- can clear it. The only thing that
    lifts it is a fresh clean re-review posting a new block. What this verb
    DOES do is real and separate: it adds L_AUTOLAND, which is a genuine
    operator act when the repo default is off, or when the issue previously
    got L_NO_AUTOLAND for an unrelated reason. On a REVIEW issue with no
    blocking finding, adding it is what puts (or keeps) the issue in the
    automated path.

    Note this is the SAME argv the old auto-review verb used -- an
    add-label call -- but the label and the meaning are different: it grants
    review-then-merge rather than review-only. It does not touch L_STUCK:
    ABANDONED is the write-off case, cleared by `unclaim`, and a held
    REVIEW issue is not abandoned.

    Unchanged from the old shape: nothing visible happens on the click, the
    next tick does the work, and the reviewing agent session is only spent
    because the operator decided to spend it.

    Routed through the per-repo backend exactly like a_assign: a
    Gitea-backed repo runs `tea issue edit --add-labels`, a GitHub-backed
    one runs `gh issue edit --add-label`.

    orch#149 fix: now writes the full label PAIR via _set_auto_land instead
    of a bare add-label, because re-adding L_AUTOLAND alone never cleared a
    present L_NO_AUTOLAND -- the control silently no-op'd whenever a prior
    hold had set no-auto-land."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    be = core.adapter_for(repo)
    return _set_auto_land(be, repo, a["issue"], True)


def a_set_auto_land(a):
    """Operator-driven automerge toggle (orch#163): write the intended
    state across the label pair via _set_auto_land. `on` arrives from JSON
    and is coerced to a real bool since the client may send true/false,
    "true"/"false", or 1/0."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    be = core.adapter_for(repo)
    on = a["on"]
    if isinstance(on, str):
        on = on.strip().lower() in ("true", "1", "yes", "on")
    else:
        on = bool(on)
    return _set_auto_land(be, repo, a["issue"], on)


def a_unclaim(a):
    """Return a CLAIMED (or ABANDONED) issue to UNCLAIMED by stripping both
    claim-shaped labels, so the spawn chain can pick it up again from a dead
    or stuck owner. Gated to a watched repo exactly like a_assign/a_reply --
    #19's lesson: a removal verb is at least as dangerous as an additive one,
    so it gets the same gate, no exceptions.

    Routed through core.adapter_for exactly like a_assign/a_review_now: a
    Gitea-backed repo runs `tea issue edit --remove-labels`, a GitHub-backed
    one runs `gh issue edit --remove-label`. No `if backend == "tea"` branch
    here -- #69 collapsed 18 of those into one adapter per backend, and this
    verb routes through it the same way the two label-writing verbs above
    do, rather than reopening that branch.

    Deliberately does NOT add agent-ready: re-queuing is the existing
    `assign` verb, and folding it in here would make one button carry two
    operator decisions (clear the dead claim, AND decide to re-spawn) where
    the operator may only want the first.

    Most issues carry only ONE of agent-working/agent-stuck, so removing the
    other one is expected to be a no-op, not a failure. The `ok` returned
    here is NOT the AND of the two calls' exit codes -- it answers "does the
    issue end unclaimed", which a no-op removal still satisfies. Concretely:
    ok is True if either call succeeds, since a single successful removal
    (of whichever label the issue actually carried) is sufficient to clear
    the claim; both calls failing is the only case that leaves the issue
    still claimed.

    This is the conservative direction on both backends' actual behaviour:
    gh's `issue edit --remove-label` goes through gh's GraphQL edit mutation
    (removeLabelsFromLabelable), which is documented as idempotent over
    labels already absent, and NOT the older REST delete-label endpoint
    (which 404s on a missing label) -- so the gh call for the label the
    issue does not carry is expected to still report success. tea's
    `issue edit --remove-labels` behaviour on a not-present label could not
    be confirmed with confidence here (no live call was made per the task's
    ground rules, and tea's CLI help does not document it either way) --
    picking "ok if either call succeeds" is the safe choice regardless: it
    cannot report a false failure on the common one-label case on either
    backend, and on the rare case where BOTH calls genuinely fail, the
    combined output below still shows the operator exactly what each call
    said."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    be = core.adapter_for(repo)
    r1 = _gh(be.issue_edit_remove_label(a["issue"], core.L_WORKING), cwd=repo)
    r2 = _gh(be.issue_edit_remove_label(a["issue"], core.L_STUCK), cwd=repo)
    return {"ok": r1["ok"] or r2["ok"], "out": f"{r1['out']}\n{r2['out']}".strip()}


def _item_id(a):
    """`<sha>:<n>` — an item's stable identity. The sha pins the finding to
    the diff it was found against, so a re-review that renumbers cannot make
    a record point at a different finding than the operator acted on."""
    return f"{str(a.get('sha', ''))}:{a['n']}"


def a_approve_item(a):
    """Approving a finding turns it into work here, rather than leaving the
    operator to file it by hand later — which is the step that gets skipped.

    What that means depends on the verdict, so an unknown one is refused
    rather than guessed at. Gated to a watched repo like every repo-taking
    verb (#19); prose goes on stdin on the gh path, never argv (see _gh).

    Routed through the per-repo backend exactly like a_reply/a_create_issue:
    a Gitea-backed repo's comment goes through TEA_ARGV's issue_comment
    (body in `-d`, no stdin -- tea has no body-file/stdin option for this
    verb) and its follow-up issue through TEA_ARGV's issue_create; a
    GitHub-backed repo keeps the original stdin-based argv unchanged."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    verdict = str(a.get("verdict", ""))
    iid = _item_id(a)
    finding = str(a.get("finding", ""))
    issue = str(a["issue"])
    be = core.adapter_for(repo)

    if verdict == "fix-before-merge":
        # `orch/operator reply` is the marker core.journal_brief splits on to
        # recognise an operator instruction, so the issue-orch picks this up
        # as work on its next wake rather than reading it as chatter.
        body = (f"orch/operator reply\nReview item {iid} approved: fix before merge.\n"
                f"{finding}\n")
        argv, stdin = be.issue_comment_call(issue, body)
        return _gh(argv, cwd=repo, stdin_text=stdin)

    if verdict == "follow-up":
        title = finding.strip().splitlines()[0][:MAXTITLE] if finding.strip() else f"review item {iid}"
        body = (f"Filed from review item {iid} on issue #{issue}.\n\n{finding}\n\n"
                f"Follow-up: this does NOT block the merge of #{issue}.\n")
        argv, stdin = be.issue_create_call(title, body)
        return _gh(argv, cwd=repo, stdin_text=stdin)

    if verdict in ("merge-as-is", "wontfix"):
        # Acknowledged, no work created -- but recorded, so the thread shows
        # the item was seen rather than silently dropped.
        body = (f"orch/operator reply\nReview item {iid} acknowledged as {verdict}. "
                f"No work created.\n{finding}\n")
        argv, stdin = be.issue_comment_call(issue, body)
        return _gh(argv, cwd=repo, stdin_text=stdin)

    return {"ok": False, "out": "unknown verdict"}


def a_reject_item(a):
    """Overrule a finding -- and record WHY, in the disagreement form
    agents/issue-orch.md prescribes: name the finding, name the decision.

    A later reader has to be able to tell "raised and deliberately
    overruled" from "missed"; a record that only says "rejected" separates
    neither, and the missed one gets re-filed. That is why an empty reason
    is refused instead of posted.

    Routed through the per-repo backend exactly like a_reply/a_approve_item."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    reason = str(a.get("reason", "")).strip()
    if not reason:
        return {"ok": False, "out": "rejection needs a reason"}
    body = (f"orch/operator reply\nReview finding: {_item_id(a)} "
            f"{str(a.get('finding', ''))}\nOperator decision: {reason}\n"
            f"Not re-raised.\n")
    be = core.adapter_for(repo)
    argv, stdin = be.issue_comment_call(str(a["issue"]), body)
    return _gh(argv, cwd=repo, stdin_text=stdin)


def a_probe(_):
    """Lets the page learn whether it can act, without guessing from a 400."""
    return {"ok": True, "out": ""}


def a_log(a):
    """Parsed run log for one issue-orch session, for the dashboard's RUN LOG
    section (orch#151). Takes `repo`/`issue` ONLY -- never a client-supplied
    path or filename. The file path is derived here via core.key_for() and
    core.ledger_log_path(), the same construction core.py:255 uses, so there
    is no caller-controlled path to traverse with at all. Contrast a_tail,
    which takes a `file` argument and must defend with a prefix+suffix check
    -- this action has no such argument to defend.

    `repo` and `issue` both inherit validate()'s existing SLUG/isdigit
    checks by key name; no new client-controlled key is introduced."""
    repo = repo_path(a["repo"])
    if not repo:
        return {"ok": False, "out": "not a watched repo"}
    issue = int(a["issue"])
    key = core.key_for("issue-orch", repo.name, issue)
    path = core.ledger_log_path(key)
    if not path.exists():
        # A key that never spawned is a normal empty state, not an error --
        # the page renders "no run log on record" rather than a failure.
        return {"ok": True, "out": "", "runs": []}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        # Read error (permissions, disappeared mid-read) must not 500 the
        # dashboard -- degrade to the same empty state as a missing file.
        return {"ok": True, "out": "", "runs": []}
    # runlog.parse() never raises (degrades malformed lines to kind "raw"),
    # but nothing here depends on that promise holding forever.
    try:
        runs = runlog.parse(text, limit=400)
    except Exception:
        return {"ok": True, "out": "", "runs": []}
    return {"ok": True, "out": "", "runs": runs}


ACTIONS = {
    "probe": (a_probe, ()),
    "log": (a_log, ("repo", "issue")),
    "kill": (a_kill, ("repo", "issue")),
    "nudge": (a_nudge, ("repo",)),  # optional: text
    "ask": (a_ask, ("repo", "text")),
    "merge": (a_merge, ("repo", "issue", "pr")),
    "tick": (a_tick, ()),
    "watch": (a_watch, ("path",)),
    "unwatch": (a_unwatch, ("path",)),
    "candidates": (a_candidates, ()),
    "tail": (a_tail, ("file",)),
    "assign": (a_assign, ("repo", "issue")),
    "untracked": (a_untracked, ("repo",)),
    "create_issue": (a_create_issue, ("repo", "title")),
    "reply": (a_reply, ("repo", "issue", "text")),
    "review_now": (a_review_now, ("repo", "issue")),
    "set_auto_land": (a_set_auto_land, ("repo", "issue", "on")),
    "unclaim": (a_unclaim, ("repo", "issue")),
    "approve_item": (a_approve_item, ("repo", "issue", "sha", "n", "verdict", "finding")),
    "reject_item": (a_reject_item, ("repo", "issue", "sha", "n", "finding", "reason")),
}

MAXTITLE = 250
MAXBODYTEXT = 8000
MAXAT = 64


def validate(name, a):
    if name not in ACTIONS:
        return "unknown action"
    _, required = ACTIONS[name]
    for k in required:
        if k not in a:
            return f"missing {k}"
    if "repo" in a and not SLUG.match(str(a["repo"])):
        return "bad repo"
    if "issue" in a and not str(a["issue"]).isdigit():
        return "bad issue"
    if "pr" in a and not str(a["pr"]).isdigit():
        return "bad pr"
    if "path" in a and not SLUG.match(str(a["path"]).replace("~", "")):
        return "bad path"
    if "title" in a:
        t = str(a["title"])
        if not t.strip() or len(t) > MAXTITLE:
            return "bad title"
    # Per-key, so this also constrains `ask` — deliberately: an empty ask is
    # as useless as an empty reply, and neither should reach an agent.
    if "text" in a:
        s = str(a["text"])
        if not s.strip() or len(s) > MAXBODYTEXT:
            return "bad text"
    # An `at` is an ISO timestamp used verbatim as the `ref` of a journal
    # row, so it is bounded: something absurdly long is not a timestamp and
    # has no business being written into the journal.
    if "at" in a:
        s = str(a["at"])
        if not s.strip() or len(s) > MAXAT:
            return "bad at"
    if "body" in a and len(str(a["body"])) > MAXBODYTEXT:
        return "bad body"
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            f = ORCH / "public" / "widget.html"
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8") \
                if f.exists() else self._send(404, b"no dashboard built yet", "text/plain")
        if path == "/status.json":
            f = ORCH / "public" / "status.json"
            return self._send(200, f.read_bytes()) if f.exists() \
                else self._send(404, b'{"error":"no feed"}')
        if path == "/events":
            return self._events()
        self._send(404, b'{"error":"not found"}')

    def _events(self):
        global _events_count
        with _events_lock:
            if _events_count >= EVENTS_MAX_STREAMS:
                return self._send(503, b"too many live streams, try again shortly",
                                   "text/plain")
            _events_count += 1
        try:
            self._events_stream()
        finally:
            with _events_lock:
                _events_count -= 1

    def _events_stream(self):
        f = ORCH / "public" / "status.json"
        last_mtime = None
        last_sent = time.monotonic()
        try:
            # Inside the try, not before it: wbufsize=0 makes end_headers()
            # write to the socket synchronously, and a peer that already left
            # (tab closed mid-load, tailscale serve dropping the upstream leg)
            # raises BrokenPipeError right here -- it must land in the same
            # except below, or it escapes to socketserver's handle_error and
            # dumps a traceback into the journal on every aborted page load.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # No Content-Length -- the body has no end until the client leaves.
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                try:
                    mtime = f.stat().st_mtime
                except FileNotFoundError:
                    mtime = None
                if mtime != last_mtime:
                    last_mtime = mtime
                    # Read-then-send even on the very first loop, so a client
                    # paints immediately instead of waiting for the next tick.
                    body = f.read_text() if mtime is not None else '{"error":"no feed"}'
                    # Already written as one line by tick.py, but a stray
                    # newline would split into a second (unprefixed, invalid)
                    # SSE field -- strip rather than trust the writer.
                    for line in body.splitlines() or [""]:
                        self.wfile.write(f"data: {line}\n".encode())
                    self.wfile.write(b"\n")
                    self.wfile.flush()
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= EVENTS_HEARTBEAT_SECS:
                    # Idle connections get killed by intermediaries (and a
                    # dead peer never tells us) without something on the wire.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_sent = time.monotonic()
                time.sleep(EVENTS_POLL_SECS)
        except (BrokenPipeError, ConnectionResetError):
            # The client left. Normal, not an error -- every tab close does this.
            pass

    def do_POST(self):
        if self.path.split("?")[0] != "/act":
            return self._send(404, b'{"error":"not found"}')
        n = int(self.headers.get("Content-Length", 0))
        if n > MAXBODY:
            return self._send(413, b'{"error":"too large"}')
        try:
            a = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, b'{"error":"bad json"}')
        name = str(a.get("action", ""))
        bad = validate(name, a)
        if bad:
            return self._send(400, json.dumps({"error": bad}).encode())
        try:
            self._send(200, json.dumps(ACTIONS[name][0](a)).encode())
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "out": str(e)}).encode())


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"orch web on 127.0.0.1:{PORT}", file=sys.stderr)
    # public/widget.html is untracked, so a fresh checkout has none until the
    # first tick. Build once up front so the page is not a 404 until then.
    #
    # A failure is non-fatal (the 404 stands and the next tick retries), but it
    # is NOT silent: build_widget's own stderr explains why, and the #57 guard
    # refusing an out-of-checkout ORCH_HOME reports it there. Swallowing that
    # message leaves an operator with a 404 and no clue.
    try:
        _bw = subprocess.run(
            [sys.executable, "-m", "orch.build_widget"],
            cwd=str(ORCH), capture_output=True, text=True, timeout=30,
        )
        if _bw.returncode != 0:
            print("startup widget build failed: %s" % (_bw.stderr or _bw.stdout or
                  "rc=%d" % _bw.returncode).strip(), file=sys.stderr)
    except Exception as e:
        print("startup widget build failed: %s" % e, file=sys.stderr)
    # No tick thread here. The scan/spawn loop is orch.ticker, its own process
    # under orch-tick.service, so it can be stopped (token spend) without
    # taking the dashboard down with it -- issue #442.
    srv.serve_forever()
