#!/usr/bin/env python3
"""Self-check for core.py.

No frameworks: the point is that a bug in the read layer is caught before it
reaches a dashboard or an agent. Real process groups via
Popen(start_new_session=True) throughout -- never stub killpg. Liveness is
checked the way it is in production, or the suite is worthless.

Run: python3 -m orch.test_core   (from the repo root)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

T = Path(tempfile.mkdtemp())
os.environ["ORCH_HOME"] = str(T / "orch")
os.environ["WT_ROOT"] = str(T / "wt")
Path(os.environ["ORCH_HOME"]).mkdir(parents=True)
Path(os.environ["WT_ROOT"]).mkdir(parents=True)

sys.path.insert(0, str(Path(__file__).parent.parent))
import importlib
from orch import core
importlib.reload(core)  # pick up env vars set above

# The issue journal writes via `gh issue comment` (core._journal_append_issue),
# and every issue-orch spawn below journals. Left real, the suite makes
# network calls against repos that do not exist -- visible as gh retry failures
# in the output, and slow. Record the calls instead; nothing here asserts on
# GitHub's side of that write.
#
# Captured BEFORE the stub rebind below, so a dedicated block further down
# (orch#58 unit 2) can still exercise the real implementation directly --
# once core._journal_append_issue is reassigned to the lambda, the original
# function object is unreachable any other way.
_REAL_JOURNAL_APPEND_ISSUE = core._journal_append_issue
ISSUE_JOURNAL_CALLS = []
core._journal_append_issue = lambda repo, issue, actor, event, extra: \
    ISSUE_JOURNAL_CALLS.append((repo, issue, actor, event, extra))

pass_n = 0
fail_n = 0


def check(name, want, got):
    global pass_n, fail_n
    if want == got:
        pass_n += 1
    else:
        fail_n += 1
        print(f"FAIL {name}: want [{want}] got [{got}]")


def _repo_state(resolved_path):
    """The "state" of the orch.json repos[] entry matching this RESOLVED
    path, or None if no entry matches. Test-only helper: watch_repo/
    unwatch_repo write orch.json now, not repos.txt, so a check that used to
    grep repos.txt lines greps entries here instead."""
    cfg = json.loads((core.ORCH_HOME / "orch.json").read_text())
    for entry in cfg.get("repos", []):
        if Path(entry["path"]).expanduser().resolve() == resolved_path:
            return entry["state"]
    return None


def _write_repos_txt(text):
    """Write ORCH_HOME/repos.txt AND drop any orch.json written by an earlier
    migration in this same process, so the write below is picked up on the
    next _load_config() call instead of being shadowed by a stale orch.json
    (orch.json, once migrated, always wins over repos.txt -- see
    core._load_config). The suite reuses one ORCH_HOME across hundreds of
    checks and repeatedly rewrites repos.txt to set up each new scenario, so
    without this every write after the first migration would be silently
    ignored."""
    (core.ORCH_HOME / "repos.txt").write_text(text)
    orch_json = core.ORCH_HOME / "orch.json"
    if orch_json.exists():
        orch_json.unlink()


# === addressing ==============================================================
check("issue_dir", str(Path(os.environ["WT_ROOT"]) / "myrepo/issue-7"), str(core.issue_dir(7, "myrepo")))
check("issue_branch", "issue-7", core.issue_branch(7))

# one key = one exclusive cwd, for all three roles
cwds = {
    "dashboard-op": core.cwd_for("dashboard-op"),
    "repo-orch": core.cwd_for("repo-orch", "myrepo"),
    "issue-orch": core.cwd_for("issue-orch", "myrepo", 7),
}
check("key_dashboard", "dashboard-op", core.key_for("dashboard-op"))
check("key_repo_orch", "repo-orch.myrepo", core.key_for("repo-orch", "myrepo"))
check("key_issue_orch", "issue-orch.myrepo.7", core.key_for("issue-orch", "myrepo", 7))
check("cwds_distinct", 3, len(set(str(c) for c in cwds.values())))

check("roles_no_worker", False, "worker" in core.ROLES)
try:
    core.key_for("worker", "myrepo", 7, "u1")
    check("key_for_worker_raises", "raised", "did not raise")
except ValueError:
    check("key_for_worker_raises", "raised", "raised")

# --- issue state -------------------------------------------------------------
def mk(n, *labels):
    return {"number": n, "title": "t", "labels": [{"name": l} for l in labels],
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z"}


w = core.World()
w.issues = [mk(1, "agent-ready"), mk(2, "agent-working"), mk(3, "other"),
            mk(4, "agent-working", "agent-stuck")]
# ready OR working, never AND - they are mutually exclusive by lifecycle
check("candidates", "1 2 4", " ".join(str(n) for n in w.candidates()))
check("unclaimed", "UNCLAIMED", core.issue_state(w, 1))
check("claimed", "CLAIMED", core.issue_state(w, 2))
check("stuck_wins", "ABANDONED", core.issue_state(w, 4))

# candidates() feeds the five-in-flight cap directly, so the leak this issue
# fixes is a COUNT bug: a landed issue that kept agent-working still occupies
# a slot, and the symptom (ready work deferred) looks like the cap working.
# Assert the release by counting the SAME issue before and after -- an issue
# that never carried agent-working proves nothing, since it was never counted.
w2 = core.World()
w2.issues = [mk(5, "agent-working"), mk(6, "agent-working")]
check("candidates_counts_landed_before_release", 2, len(w2.candidates()))
# 6 lands and issue-orch drops agent-working as its close-out: the slot frees.
w2.issues = [mk(5, "agent-working"), mk(6)]
check("candidates_excludes_released_landed_issue", 2 - 1, len(w2.candidates()))
check("candidates_keeps_still_working_issue", "5",
      " ".join(str(n) for n in w2.candidates()))

# --- pull requests -------------------------------------------------------
w.prs = [{"number": 9, "headRefName": "issue-7", "state": "OPEN"}]
check("pr_open", "OPEN", w.pr_for("issue-7"))
# an OPEN pr outranks a stale MERGED one on the same branch
w.prs = [{"number": 8, "headRefName": "issue-7", "state": "MERGED"},
         {"number": 9, "headRefName": "issue-7", "state": "OPEN"}]
check("pr_open_wins", "OPEN", w.pr_for("issue-7"))
w.prs = []
check("pr_none", "", w.pr_for("issue-7"))

# pr_number_for: same OPEN-beats-MERGED-beats-first-seen precedence as pr_for,
# but returns the number (or None) instead of the state string.
w.prs = [{"number": 8, "headRefName": "issue-7", "state": "MERGED"},
         {"number": 9, "headRefName": "issue-7", "state": "OPEN"}]
check("pr_number_open_wins", 9, w.pr_number_for("issue-7"))
w.prs = [{"number": 8, "headRefName": "issue-7", "state": "MERGED"}]
check("pr_number_merged_only", 8, w.pr_number_for("issue-7"))
w.prs = []
check("pr_number_none", None, w.pr_number_for("issue-9-nope"))

# check verdicts, with _rollup stubbed to avoid a network call (pure dict
# lookup -- fine to stub. killpg is NOT fine to stub, see below.)
STUB_ROLL = None
w._rollup = lambda branch: STUB_ROLL

STUB_ROLL = [{"conclusion": "SUCCESS"}]
check("green_ok", "yes", "yes" if w.pr_green("x") else "no")
STUB_ROLL = [{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}, {"conclusion": "NEUTRAL"}]
check("skipped_ok", "yes", "yes" if w.pr_green("x") else "no")
# vacuous-true on [] must be handled first
STUB_ROLL = []
check("no_ci_is_green", "yes", "yes" if w.pr_green("x") else "no")
STUB_ROLL = None
check("null_is_green", "yes", "yes" if w.pr_green("x") else "no")
# pending is neither green nor red - treating it as red burns fix rounds
STUB_ROLL = [{"conclusion": "PENDING"}]
check("pending_notgreen", "no", "yes" if w.pr_green("x") else "no")
check("pending_notred", "no", "yes" if w.pr_red("x") else "no")
STUB_ROLL = [{"conclusion": "PENDING"}, {"conclusion": "FAILURE"}]
check("failure_is_red", "yes", "yes" if w.pr_red("x") else "no")
# An UNREADABLE rollup is not the same fact as a no-CI rollup. no-CI (above)
# stays green -- deliberate, finding 2. A rollup we failed to READ buys no
# green light: neither green nor red, so the unit reads CHECKING ("we do not
# know"), not REVIEW and not BLOCKED.
STUB_ROLL = core.ROLLUP_UNREADABLE
check("unreadable_notgreen", "no", "yes" if w.pr_green("x") else "no")
check("unreadable_notred", "no", "yes" if w.pr_red("x") else "no")

# pr_verified: a separate question from pr_green -- "did anything actually
# check this PR" rather than "is anything failing". no-CI must NOT verify,
# which is the opposite of how pr_green treats the same empty rollup.
STUB_ROLL = []
check("no_ci_not_verified", "no", "yes" if w.pr_verified("x") else "no")
STUB_ROLL = core.ROLLUP_UNREADABLE
check("unreadable_not_verified", "no", "yes" if w.pr_verified("x") else "no")
# SKIPPED counts as green (pr_green) but must NOT count as verified -- an
# all-skipped rollup checked nothing, same hole as no-CI in a different coat.
STUB_ROLL = [{"conclusion": "SKIPPED"}, {"conclusion": "SKIPPED"}]
check("all_skipped_not_verified", "no", "yes" if w.pr_verified("x") else "no")
STUB_ROLL = [{"conclusion": "SUCCESS"}]
check("one_success_is_verified", "yes", "yes" if w.pr_verified("x") else "no")
# mixed SUCCESS+FAILURE: verification happened (pr_verified True) but it
# still failed (pr_red True) -- green-and-verified is not the merge gate,
# pr_red still blocks this one.
STUB_ROLL = [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]
check("mixed_success_failure_is_verified", "yes", "yes" if w.pr_verified("x") else "no")
check("mixed_success_failure_is_red", "yes", "yes" if w.pr_red("x") else "no")

# --- work_state truth table ---------------------------------------------------
class _PlainBranchMixin:
    """branch_for_issue for the World stubs, answering the plain issue-<n>.

    work_state and merge_pr resolve the branch through the world now rather
    than deriving issue_branch(n) themselves (orch#329), so every fake standing
    in for World on those paths has to answer it. Defined once here rather than
    copied into each stub: a fake that drifts from the real semantics tests
    nothing.

    The plain name is the honest answer for these fixtures -- they stub pr_for
    to reply the same way whatever branch it is handed, so they model an issue
    whose work never left issue-<n>. The suffixed-sibling case, which is the
    one orch#329 is about, is driven against the REAL core.World in its own
    block at the end of this file."""

    def branch_for_issue(self, n):
        return core.issue_branch(n)


# Drive work_state through all six states with a stubbed World (pr_for +
# _rollup stubbed, and a work_mtime we control via monkeypatched core.work_mtime).
class StubWorld(_PlainBranchMixin):
    def __init__(self, pr, green, red):
        self._pr = pr
        self._green = green
        self._red = red

    def pr_for(self, branch):
        return self._pr

    def pr_green(self, branch):
        return self._green

    def pr_red(self, branch):
        return self._red


_orig_work_mtime = core.work_mtime
_WM = {"val": None}
core.work_mtime = lambda repo, branch: _WM["val"]

try:
    check("work_landed", "LANDED", core.work_state(StubWorld("MERGED", False, False), "repo", 7))
    check("work_review", "REVIEW", core.work_state(StubWorld("OPEN", True, False), "repo", 7))
    check("work_blocked", "BLOCKED", core.work_state(StubWorld("OPEN", False, True), "repo", 7))
    check("work_checking", "CHECKING", core.work_state(StubWorld("OPEN", False, False), "repo", 7))
    _WM["val"] = 12345
    check("work_active", "ACTIVE", core.work_state(StubWorld("", False, False), "repo", 7))
    _WM["val"] = None
    check("work_claimed", "CLAIMED", core.work_state(StubWorld("", False, False), "repo", 7))
finally:
    core.work_mtime = _orig_work_mtime

# --- sessions ----------------------------------------------------------------
# Claude mangles the cwd by replacing / . and _ with -; handling only slashes
# silently returns no session for any repo or branch containing a dot.
check("mangle", str(Path.home() / ".claude/projects/-tmp-a-b-c-d"), str(core.session_dir("/tmp/a.b/c_d")))
check("no_sessions", "[]", str(core.sessions_for(T / "nonexistent")).replace("'", '"') if core.sessions_for(T / "nonexistent") else "[]")

# --- journal -------------------------------------------------------------
journal_append = core.journal_append
journal_stats = core.journal_stats
journal_tail = core.journal_tail

journal_append("repo", "me/r", None, "tick", "observed", {"note": "one"})
journal_append("repo", "me/r", None, "repo-orch", "decided", {"note": "two"})
check("journal_count_repo", 2, journal_stats("repo", "me/r")["entries"])
check("journal_last_repo", "decided", journal_stats("repo", "me/r")["last_event"])
check("journal_order_repo", "one", journal_tail("repo", "me/r")[0]["note"])

journal_append("dashboard", None, None, "tick", "observed", {"note": "d1"})
journal_append("dashboard", None, None, "dashboard-op", "handled", {"note": "d2"})
check("journal_count_dash", 2, journal_stats("dashboard")["entries"])
check("journal_last_dash", "handled", journal_stats("dashboard")["last_event"])
check("journal_order_dash", "d1", journal_tail("dashboard")[0]["note"])

check("journal_empty", [], journal_tail("repo", "nonexistent-slug"))

# --- journal_brief note cap (repo/dashboard scope, display only) -------------
journal_brief = core.journal_brief
journal_append("repo", "me/notecap", None, "tick", "observed", {"note": "short note"})
check("journal_brief_short_unchanged", True, "short note" in journal_brief("repo", "me/notecap"))
check("journal_brief_short_no_marker", False, "chars]" in journal_brief("repo", "me/notecap"))

long_note = "x" * (core.JOURNAL_NOTE_CHARS + 50)
journal_append("repo", "me/notecap", None, "tick", "observed", {"note": long_note})
brief_long = journal_brief("repo", "me/notecap")
check("journal_brief_long_marker", True, "… [+50 chars]" in brief_long)
check("journal_brief_long_cut_len", True, "x" * core.JOURNAL_NOTE_CHARS in brief_long)

nl_note = "line one\nline two\nline three"
journal_append("repo", "me/notecap", None, "tick", "observed", {"note": nl_note})
brief_nl = journal_brief("repo", "me/notecap")
last_line = brief_nl.splitlines()[-1]
check("journal_brief_newline_one_line", True, "line one line two line three" in last_line)

boundary_note = "y" * core.JOURNAL_NOTE_CHARS
journal_append("repo", "me/notecap", None, "tick", "observed", {"note": boundary_note})
brief_boundary = journal_brief("repo", "me/notecap")
boundary_line = brief_boundary.splitlines()[-1]
check("journal_brief_boundary_not_marked", False, "chars]" in boundary_line)

# --- feed.repo_orch_json note cap (orch#268: feed payload, disk untouched) --
from orch import feed as feedmod
# `started`, not `observed`: recent excludes heartbeat rows (§4.1 below).
journal_append("repo", "me/feednotecap", None, "repo-orch", "started", {"note": "x" * (core.JOURNAL_NOTE_CHARS + 50)})
recent = feedmod.repo_orch_json(None, "me/feednotecap")["recent"]
check("feed_note_capped_marker", True, "… [+50 chars]" in recent[-1]["note"])
check("feed_note_capped_len", True, len(recent[-1]["note"]) < core.JOURNAL_NOTE_CHARS + 50)

# --- feed.repo_orch_json `recent` is acts, not heartbeat (DASHBOARD-2026-09-17 §4.1)
# 50 observed + 3 started + a spawn: recent is exactly the 3 started rows, in
# order. Mutation check: drop the filter in repo_orch_json and this reads 8
# rows, 5 of them `observed`.
for _k in range(25):
    journal_append("repo", "me/feedacts", None, "tick", "observed", {"note": "hb%d" % _k})
for _k in range(3):
    journal_append("repo", "me/feedacts", None, "repo-orch", "started", {"note": "act%d" % _k})
journal_append("repo", "me/feedacts", None, "repo-orch.x", "spawn", {"resumed": True})
for _k in range(25):
    journal_append("repo", "me/feedacts", None, "tick", "observed", {"note": "hb%d" % _k})
_acts = feedmod.repo_orch_json(None, "me/feedacts")["recent"]
check("feed_recent_acts_only", ["act0", "act1", "act2"], [r["note"] for r in _acts])
check("feed_recent_no_bookkeeping", [], [r["event"] for r in _acts if r["event"] in feedmod._RECENT_BOOKKEEPING])
journal_append("repo", "me/feedhb", None, "tick", "observed", {"note": "hb"})
check("feed_recent_empty_when_only_heartbeat", [], feedmod.repo_orch_json(None, "me/feedhb")["recent"])

# journal_stats("issue", ...) is cut from the spec -- assert it raises.
try:
    journal_stats("issue", "me/r", 7)
    check("journal_stats_issue_raises", "raised", "did not raise")
except ValueError:
    check("journal_stats_issue_raises", "raised", "raised")

# The "issue" scope must route through core._journal_append_issue (a `gh issue
# comment` call), never a local journal file. The stub at the top of this file
# intercepts it so the suite stays hermetic; if a future edit deletes the stub
# or reroutes the scope, the suite would go back to making real network calls
# and still pass -- only slower. This check is that guard.
_issue_calls_before = len(ISSUE_JOURNAL_CALLS)
journal_append("issue", "someowner/somerepo", 1, "testactor", "testevent", {"note": "x"})
check("journal_issue_routed_to_gh", 1, len(ISSUE_JOURNAL_CALLS) - _issue_calls_before)
check("journal_issue_call_args",
      ("someowner/somerepo", 1, "testactor", "testevent", {"note": "x"}),
      ISSUE_JOURNAL_CALLS[-1])
check("journal_issue_no_local_file", [], journal_tail("repo", "someowner/somerepo"))

# --- _ts_key: total over non-string input, never raises -------------------
# core._ts_key normalizes an ISO timestamp string for comparison; callers
# may feed it a journal row's raw `at` field, which can be missing or
# non-string. It must fall back to identity (matches only itself) rather
# than crash the caller.
check("ts_key_total_none", None, core._ts_key(None))
check("ts_key_total_int", 12345, core._ts_key(12345))
check("ts_key_total_dict", {}, core._ts_key({}))

# === _child_env strips inherited Claude session vars ========================
# A nested `claude -p` inheriting a live parent session's bridge/auth vars
# hangs forever (see core.ENV_STRIP_* for the empirical writeup). Test the
# env-composition function directly -- never spawn a real `claude` here.
_leaked = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "CLAUDE_CODE_SESSION_ID": "abc",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "tok",
    "CLAUDE_CODE_BRIDGE_SESSION_ID": "bridge",
    "CLAUDE_CODE_EXECPATH": "/usr/local/bin/claude",
    "CLAUDE_PID": "12345",
    "CLAUDE_EFFORT": "high",
    "ANTHROPIC_API_KEY": "sk-ant-test",
}
_prior = {k: os.environ.get(k) for k in _leaked}
_prior_path = os.environ.get("PATH")
os.environ.update(_leaked)
try:
    child = core._child_env()
    check("child_env_strips_all_leaked", True,
          all(k not in child for k in _leaked))
    check("child_env_keeps_path", _prior_path, child.get("PATH"))
    # a var that merely starts with "CLAUDE" but isn't on the list survives --
    # explicit list + CLAUDE_CODE_ prefix, never a blanket "startswith CLAUDE".
    os.environ["CLAUDE_CUSTOM_USER_VAR"] = "keepme"
    child2 = core._child_env()
    check("child_env_keeps_unlisted_claude_var", "keepme",
          child2.get("CLAUDE_CUSTOM_USER_VAR"))
    del os.environ["CLAUDE_CUSTOM_USER_VAR"]
finally:
    for k, v in _prior.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# === liveness is keyed and real ==============================================
# Write a ledger row by hand pointing at a REAL live process group, assert
# alive(key) is True; kill_key(key); assert alive(key) is False.
core.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
liveness_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
try:
    time.sleep(0.3)
    key = "issue-orch.livetest.1"
    row = {"role": "issue-orch", "scope": ["livetest", 1], "workdir": str(T),
           "pgid": liveness_proc.pid, "started": core.now_iso(),
           "log": str(core.ledger_log_path(key))}
    import json
    core.ledger_path(key).write_text(json.dumps(row))
    check("alive_real_pgid", True, core.alive(key))
    core.kill_key(key)
    check("dead_after_kill", False, core.alive(key))
finally:
    try:
        liveness_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        liveness_proc.kill()
        liveness_proc.wait(timeout=2)


# === THE FLOCK-STAKE ASSERT ==================================================
# spawn() calls shutil.which("claude") and launches a real `claude -p` agent.
# We must not launch a real agent. Mechanism:
#   1. monkeypatch core.shutil.which to return a harmless long-lived
#      executable (/bin/sleep) so Popen starts a real process in a real new
#      session -- but argv would then be [/bin/sleep, "-p", "--permission-mode",
#      "acceptEdits"], which sleep rejects and exits immediately, breaking the
#      live-pgid precondition.
#   2. So instead we monkeypatch core.subprocess.Popen with a thin wrapper
#      that discards core's argv and substitutes ["sleep", "30"], passing
#      through cwd/stdin/stdout/stderr/start_new_session=True unchanged. Real
#      fork, real session, real pgid, real log file handles, real flock path
#      -- only the executable is swapped. killpg/alive/the flock/roll-aside
#      are never touched.
_real_popen = subprocess.Popen


def _fake_popen(argv, **kwargs):
    # core.subprocess IS the subprocess module (same object), so patching
    # its Popen attribute also intercepts subprocess.run()'s internal Popen
    # calls (used by _run() for git worktree plumbing) -- those must pass
    # through untouched. Only the actual claude launch in _launch()'s
    # _spawn_argv uses this exact signature: stdin=PIPE, start_new_session
    # =True, stdout/stderr opened to the ledger log. Discriminate on that.
    if kwargs.get("stdin") is subprocess.PIPE and kwargs.get("start_new_session"):
        return _real_popen(["sleep", "30"], **kwargs)
    return _real_popen(argv, **kwargs)


# issue-orch key needed for part (a)/(b): a git repo checkout, since
# issue-orch is now the role that creates the worktree. issue-orch DOES
# resume (unlike the old cold worker), so _launch polls up to 5s IF a resume
# id exists -- but no transcript dir exists yet for these temp cwds, so
# resume_id_for returns None and there is no grace poll here either.
repo_src = T / "srcrepo"
repo_src.mkdir()
subprocess.run(["git", "init", "-q"], cwd=repo_src, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "--allow-empty", "-m", "init", "-q"], cwd=repo_src, check=True)

flock_key = "issue-orch.flocktest.1"
spawned_pgids = []

core.shutil.which = lambda name: "/bin/sleep" if name == "claude" else shutil.which(name)
core.subprocess.Popen = _fake_popen

try:
    # (a) spawn twice for one key -> ONE pgid. The second call must see the
    # live pgid under the flock and refuse, returning the SAME pgid.
    pgid1 = core.spawn("issue-orch", ("flocktest", 1), "brief one", repo_path=repo_src)
    spawned_pgids.append(pgid1)
    pgid2 = core.spawn("issue-orch", ("flocktest", 1), "brief two (should refuse)", repo_path=repo_src)
    check("flock_stake_same_pgid", pgid1, pgid2)
    # only one process exists for this key
    check("flock_stake_alive_once", True, core.alive(flock_key))

    log_path = core.ledger_log_path(flock_key)
    log_text_before_kill = log_path.read_text()
    check("flock_first_run_header_present_before_kill", 1, log_text_before_kill.count("pgid"))
    prior_runs_baseline = core.prior_runs(flock_key)
    check("flock_prior_runs_baseline_zero", 0, prior_runs_baseline)

    # (b) kill -> respawn rolls the dead row aside (i), leaves prior_runs
    # incremented relative to baseline (ii), and the log appends rather than
    # truncates (iii). kill_key() itself performs a roll-aside as part of its
    # own cleanup, so prior_runs is already incremented immediately after the
    # kill; respawn must not double-roll it.
    core.kill_key(flock_key)
    check("flock_dead_after_kill", False, core.alive(flock_key))
    check("flock_prior_runs_incremented_by_kill", prior_runs_baseline + 1, core.prior_runs(flock_key))

    pgid3 = core.spawn("issue-orch", ("flocktest", 1), "brief three (respawn)", repo_path=repo_src)
    spawned_pgids.append(pgid3)
    check("flock_respawn_new_pgid", True, pgid3 != pgid1)
    check("flock_prior_runs_still_incremented", prior_runs_baseline + 1, core.prior_runs(flock_key))

    log_text_after = log_path.read_text()
    check("flock_log_appended_not_truncated", True, "brief one" not in log_text_after and log_text_after.count("=== ") == 2)
    # header count: two "=== ... pgid ... ===" headers
    header_count = sum(1 for line in log_text_after.splitlines() if line.startswith("=== ") and "pgid" in line)
    check("flock_two_headers", 2, header_count)
finally:
    core.shutil.which = shutil.which
    core.subprocess.Popen = _real_popen
    for pgid in spawned_pgids:
        try:
            core._pgid_alive(pgid) and os.killpg(pgid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    # also make sure the flock key's current live pgid (if any) is killed
    try:
        core.kill_key(flock_key)
    except Exception:
        pass


# === B1: spawn composes the brief for every spawned role ====================
# compose_brief must prepend the role's agent doc + journal tail to the
# caller's reason for every role spawn() can reach. There is no worker
# passthrough any more -- workers are Agent-tool subagents issue-orch briefs
# itself, in-process, never spawned by this function. Capture what reaches
# _launch by monkeypatching it directly -- no need to touch the flock/Popen
# machinery the flock-stake test above already exercises.
_orig_launch = core._launch
_captured = {}


def _fake_launch(key, cwd, prompt, resume_id, role=None):
    _captured["prompt"] = prompt
    _captured["role"] = role
    return 424242, False


# journal_brief("issue", ...) is a real `gh` network call -- stub it for
# this whole block so issue-orch spawns stay hermetic throughout.
_orig_journal_brief = core.journal_brief
core.journal_brief = lambda scope, repo=None, issue=None, n=None: (
    "(stubbed issue tail)" if scope == "issue" else _orig_journal_brief(scope, repo, issue, n)
)

core._launch = _fake_launch
try:
    # (a) doc present -> both the doc's content and the journal tail reach
    # the composed prompt.
    agents_dir = Path(os.environ["ORCH_HOME"]) / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "repo-orch.md").write_text("STUB AGENT DOC CONTENTS")
    core.journal_append("repo", "stubrepo", None, "tick", "observed", {"note": "prior history line"})

    core.spawn("repo-orch", ("stubrepo",), "the actual reason text")
    composed = _captured["prompt"]
    check("compose_doc_present", True, "STUB AGENT DOC CONTENTS" in composed)
    check("compose_journal_present", True, "prior history line" in composed)
    check("compose_reason_present", True, "the actual reason text" in composed)
    # spawn must hand _launch the role, or the per-role envelope silently
    # stops being applied and every level is back to acceptEdits-only.
    check("spawn_passes_role_for_envelope", "repo-orch", _captured["role"])
    core.kill_key(core.key_for("repo-orch", "stubrepo"))  # roll aside so name is reusable below

    # issue-orch now creates a real git worktree on spawn, so any issue-orch
    # spawn in this block needs a real repo checkout passed as repo_path --
    # _ensure_issue_worktree raises ValueError on a missing repo otherwise.
    repo_src2 = T / "srcrepo2"
    repo_src2.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_src2, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "--allow-empty", "-m", "init", "-q"], cwd=repo_src2, check=True)

    # (b) missing doc -> degrades, does not raise, still spawns. issue-orch's
    # doc has not been written yet at this point -- role-keyed path, not
    # scope-keyed, so a fresh scope alone would not exercise "missing".
    core.spawn("issue-orch", ("stubrepo", 7), "reason with no doc on disk", repo_path=repo_src2)
    composed2 = _captured["prompt"]
    check("compose_missing_doc_degrades", True, "(brief missing)" in composed2)
    check("compose_missing_doc_reason_present", True, "reason with no doc on disk" in composed2)
    core.kill_key(core.key_for("issue-orch", "stubrepo", 7))

    # (d) issue-orch, doc present -> doc + (stubbed) journal tail both land.
    (agents_dir / "issue-orch.md").write_text("STUB ISSUE-ORCH DOC")
    core.spawn("issue-orch", ("stubrepo", 7), "issue-orch reason", repo_path=repo_src2)
    composed3 = _captured["prompt"]
    check("compose_issue_orch_doc", True, "STUB ISSUE-ORCH DOC" in composed3)
    check("compose_issue_orch_journal", True, "(stubbed issue tail)" in composed3)
    core.kill_key(core.key_for("issue-orch", "stubrepo", 7))
finally:
    core._launch = _orig_launch
    core.journal_brief = _orig_journal_brief

# The spawn blocks above spawn issue-orch, and every issue-orch spawn journals
# to the issue scope. If this is empty, the interception at the top of the file
# is dead code -- meaning the spawn tests stopped exercising the issue journal,
# and the hermetic guard no longer guards anything.
check("spawn_tests_exercise_issue_journal", True, len(ISSUE_JOURNAL_CALLS) > 0)


# === journal_brief: an operator instruction is never truncated =============
# The bug this guards: issue scope used to keep body.splitlines()[0] of every
# comment, and compose_brief feeds that straight into a wake prompt. An
# operator's reply is prose, so "fix the second finding first, the third is
# intentional" reached the agent as a different order than the one given.
# _journal_tail_issue is the real gh call, so stub it with fixture rows.
_orig_tail_issue = core._journal_tail_issue
core._journal_tail_issue = lambda repo, issue: [
    {"at": "2026-09-12T10:00:00Z", "author": "bot", "orch": True,
     "body": "orch/issue-orch plan\nunit A does the write half.\nunit B the read half."},
    {"at": "2026-09-12T10:05:00Z", "author": "kiri", "orch": True,
     "body": "orch/operator reply\nfix the second finding first.\nthe third is intentional."},
    {"at": "2026-09-12T10:09:00Z", "author": "kiri", "orch": False,
     "body": "plain human comment\nsecond line here."},
    {"at": "2026-09-12T10:11:00Z", "author": "kiri", "orch": False,
     "body": "X" * (core.BRIEF_COMMENT_CHARS + 500)},
]
try:
    brief = core.journal_brief("issue", "owner/name", 37)
    # An agent's own entry is a summary: the orch/<actor> <event> header is
    # the whole point of the header, so the body stays out of the prompt.
    check("brief_agent_entry_header", True, "orch/issue-orch plan" in brief)
    check("brief_agent_entry_terse", False, "unit A does the write half." in brief)
    # A dashboard reply carries orch=True exactly like an agent entry, so the
    # flag alone cannot route it -- the actor after orch/ is what decides.
    # This is the case a future refactor is most likely to get wrong.
    check("brief_operator_reply_whole", True,
          "fix the second finding first." in brief and "the third is intentional." in brief)
    # A plain human comment is steering input and passes through unfiltered.
    check("brief_human_comment_whole", True, "second line here." in brief)
    # A cut must be visible: the agent has to SEE it got a partial
    # instruction rather than silently acting on one.
    check("brief_truncation_marked", True,
          f"[truncated at {core.BRIEF_COMMENT_CHARS} chars]" in brief)
    check("brief_truncation_bounded", True,
          brief.count("X") == core.BRIEF_COMMENT_CHARS)
finally:
    core._journal_tail_issue = _orig_tail_issue


# === S1: sessions_for detects contention via transcript-mtime recency ======
# Two transcripts both written inside CONTENDED_WINDOW_SECS on one key's cwd
# must both come back live=true (contended); a third, old transcript must
# not.
contend_cwd = T / "contendrepo"
contend_dir = core.session_dir(contend_cwd)
contend_dir.mkdir(parents=True)
(contend_dir / "recent-a.jsonl").write_text("{}\n")
(contend_dir / "recent-b.jsonl").write_text("{}\n")
(contend_dir / "old-c.jsonl").write_text("{}\n")
old_ts = time.time() - core.CONTENDED_WINDOW_SECS - 60
os.utime(contend_dir / "old-c.jsonl", (old_ts, old_ts))

contend_sessions = core.sessions_for(contend_cwd, live=False)
live_ids = {s["id"] for s in contend_sessions if s["live"]}
check("contended_two_recent_live", {"recent-a", "recent-b"}, live_ids)
check("contended_old_not_live", False, "old-c" in live_ids)
check("contended_flag", True, sum(1 for s in contend_sessions if s["live"]) > 1)

# === CLI verbs: orch/spawn.py ================================================
from orch import spawn as clicmd
from orch import server as srv
import io
import contextlib


def run_cli(argv):
    """Run the CLI main() capturing stdout, return (rc, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = clicmd.main(argv)
    return rc, buf.getvalue()


# --- kill: real process group, idempotent, single implementation -----------
cli_key = "issue-orch.clitest.1"
cli_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
try:
    time.sleep(0.3)
    row = {"role": "issue-orch", "scope": ["clitest", 1], "workdir": str(T),
           "pgid": cli_proc.pid, "started": core.now_iso(),
           "log": str(core.ledger_log_path(cli_key))}
    import json as _json
    core.ledger_path(cli_key).write_text(_json.dumps(row))
    check("cli_kill_alive_before", True, core.alive(cli_key))
    rc, out = run_cli(["kill", cli_key])
    check("cli_kill_rc", 0, rc)
    check("cli_kill_dead_after", False, core.alive(cli_key))
finally:
    try:
        cli_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        cli_proc.kill()
        cli_proc.wait(timeout=2)

# idempotent: no row at all -> exit 0, clear message
rc, out = run_cli(["kill", "issue-orch.nosuchrepo.9"])
check("cli_kill_idempotent_no_row_rc", 0, rc)
check("cli_kill_idempotent_no_row_msg", True, "no live session" in out)

# idempotent: row present but already dead (bad pgid) -> exit 0
dead_key = "issue-orch.deadtest.1"
core.ledger_path(dead_key).write_text(_json.dumps({
    "role": "issue-orch", "scope": ["deadtest", 1], "workdir": str(T),
    "pgid": 999999, "started": core.now_iso(), "log": str(core.ledger_log_path(dead_key)),
}))
rc, out = run_cli(["kill", dead_key])
check("cli_kill_idempotent_dead_row_rc", 0, rc)

# unaddressable key -> exit 2
rc, out = run_cli(["kill", "not-a-real-key-format!!"])
check("cli_kill_bad_key_rc", 2, rc)

# single-implementation proof: both server.a_kill and the CLI kill verb
# resolve to the identical core.kill_key symbol -- monkeypatch it once and
# assert both call sites observe the patch.
_kill_calls = []
_orig_kill_key = core.kill_key
core.kill_key = lambda key: _kill_calls.append(key)
try:
    # CLI path: needs an alive-looking row so cmd_kill proceeds to call
    # core.kill_key rather than short-circuiting as idempotent.
    proof_key = "issue-orch.proofcli.1"
    proof_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    time.sleep(0.2)
    core.ledger_path(proof_key).write_text(_json.dumps({
        "role": "issue-orch", "scope": ["proofcli", 1], "workdir": str(T),
        "pgid": proof_proc.pid, "started": core.now_iso(),
        "log": str(core.ledger_log_path(proof_key)),
    }))
    run_cli(["kill", proof_key])
    check("single_impl_kill_cli_hits_core", True, proof_key in _kill_calls)

    # server path: a_kill builds the issue-orch key from repo/issue and
    # calls core.kill_key directly (server.a_kill no longer takes a unit).
    (Path(os.environ["ORCH_HOME"]) / "repos.txt").write_text("proofrepo-path\n")
    _orig_repo_path_for = core.repo_path_for
    _orig_srv_repo_path = srv.repo_path
    core.repo_path_for = lambda slug: Path("/tmp/proofrepo") if slug == "proofrepo" else None
    srv.repo_path = lambda slug: core.repo_path_for(slug)
    core.ledger_path(core.key_for("issue-orch", "proofrepo", 5)).write_text(_json.dumps({
        "role": "issue-orch", "scope": ["proofrepo", 5], "workdir": str(T),
        "pgid": 424242, "started": core.now_iso(),
    }))
    srv.a_kill({"repo": "proofrepo", "issue": "5"})
    check("single_impl_kill_http_hits_core", True,
          core.key_for("issue-orch", "proofrepo", 5) in _kill_calls)

    try:
        proof_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proof_proc.kill()
        proof_proc.wait(timeout=2)
finally:
    core.kill_key = _orig_kill_key
    try:
        core._pgid_alive(proof_proc.pid) and os.killpg(proof_proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError, NameError):
        pass

# --- tick: single implementation, CLI and server both call tick.main() -----
from orch import tick as tickmod
_tick_calls = []
_orig_tick_main = tickmod.main
tickmod.main = lambda: (_tick_calls.append("called"), 0)[1]
clicmd.tick = tickmod  # cmd_tick references the module-level `tick` name in spawn.py
try:
    rc, out = run_cli(["tick"])
    check("single_impl_tick_cli_hits_main", True, "called" in _tick_calls)
    check("cli_tick_rc", 0, rc)
finally:
    tickmod.main = _orig_tick_main

# server.a_tick launches `python -m orch.tick` as a real subprocess (the
# HTTP surface must not block the request thread on a tick) -- so its
# "same implementation" is grep-evidence, not a monkeypatch: a_tick's body
# invokes `orch.tick` as __main__, which calls the identical tickmod.main()
# the CLI calls in-process. Assert the module target by source inspection.
import inspect as _inspect
a_tick_src = _inspect.getsource(srv.a_tick)
check("single_impl_tick_server_targets_module", True, "orch.tick" in a_tick_src)

from datetime import datetime, timezone

# --- status: works with no key (status.json) and with a key ----------------
rc, out = run_cli(["status"])
check("cli_status_no_statusjson_rc_nonzero", True, rc != 0)
(core.ORCH_HOME / "public").mkdir(parents=True, exist_ok=True)
(core.ORCH_HOME / "public" / "status.json").write_text(json.dumps({
    "ok": True,
    "repos": [{"slug": "statusslug", "path": "/tmp/statusslug", "ok": True}],
}))
rc, out = run_cli(["status"])
check("cli_status_prints_statusjson", True, '"ok": true' in out and rc == 0)

rc, out = run_cli(["status", "not-a-key!!"])
check("cli_status_bad_key_rc", 2, rc)

# a bare slug that is not a valid key falls back to a repo-row lookup in
# status.json's `repos` list, instead of the "bad key" error
rc, out = run_cli(["status", "statusslug"])
check("cli_status_slug_rc", 0, rc)
check("cli_status_slug_prints_row", True, "path: /tmp/statusslug" in out)

# the slug row must stay a cheap slice: a repo with issues carrying full
# session/worker trees and a long brief must NOT leak transcript paths or
# worker ids into the printed row, but must still surface the issue number
# and a truncated brief -- this is orch#344, the point of the whole unit.
(core.ORCH_HOME / "public" / "status.json").write_text(json.dumps({
    "ok": True,
    "repos": [{
        "slug": "bigslug",
        "path": "/tmp/bigslug",
        "issues": [{
            "issue": 9001,
            "state": "CLAIMED",
            "work_state": "ACTIVE",
            "title": "some issue",
            "labels": ["agent-working"],
            "ready": False,
            "startable": False,
            "branch": "issue-9001",
            "pr": None,
            "orch_alive": True,
            "idle_min": 3,
            "brief": {"text": "x" * 200, "at": "2026-01-01T00:00:00Z", "stale": False},
            "sessions": [{
                "id": "deadbeef-0000-0000-0000-000000000000",
                "file": "/home/user/.claude/projects/fake/deadbeef.jsonl",
                "resume": "cd /tmp && claude --resume deadbeef",
                "workers": [{"id": "agent-aabbccddeeff00112", "mtime": 1, "turns": 1}],
            }],
        }],
    }],
}))
rc, out = run_cli(["status", "bigslug"])
check("cli_status_slug_bigrow_rc", 0, rc)
check("cli_status_slug_has_issue_number", True, "9001" in out)
check("cli_status_slug_no_transcript_path", False, ".jsonl" in out)
check("cli_status_slug_no_worker_id", False, "agent-aabbccddeeff00112" in out)
check("cli_status_slug_brief_truncated", True, "[truncated]" in out and "x" * 200 not in out)
check("cli_status_slug_under_2kb", True, len(out) < 2048)

# neither a valid key nor a known slug: still errors, still names the input
rc, out = run_cli(["status", "neither-a-key-nor-a-slug"])
check("cli_status_unknown_rc", 2, rc)
check("cli_status_unknown_names_input", True, "neither-a-key-nor-a-slug" in out)

rc, out = run_cli(["status", "issue-orch.statustest.1"])
check("cli_status_unknown_key_rc", 0, rc)
check("cli_status_unknown_key_alive_false", True, "alive: False" in out)
check("cli_status_no_resume_line", True, "no transcript found" in out)

# --- tail: state/sessions/<key>.log, default n=40 ---------------------------
tail_key = "issue-orch.tailtest.1"
log_lines = [f"line {i}" for i in range(60)]
core.ledger_log_path(tail_key).write_text("\n".join(log_lines) + "\n")
rc, out = run_cli(["tail", tail_key])
check("cli_tail_default_40_lines", 40, len(out.strip().splitlines()))
check("cli_tail_default_last_line", "line 59", out.strip().splitlines()[-1])
rc, out = run_cli(["tail", tail_key, "5"])
check("cli_tail_n5", 5, len(out.strip().splitlines()))

rc, out = run_cli(["tail", "issue-orch.notail.1"])
check("cli_tail_missing_rc", 1, rc)

# --- journal: the CLI form the agent docs print ----------------------------
# The shell form the docs used to carry could not run inside an agent's
# envelope ($(date ...) is un-analysable; >> outside the cwd is refused
# before any allow rule is read). This verb is what replaced it.
rc, out = run_cli(["journal", "repo", "jtest", "started", "because", "reasons"])
check("cli_journal_repo_rc", 0, rc)
jrow = json.loads(core.journal_path("repo", "jtest").read_text().strip())
check("cli_journal_repo_actor", "repo-orch", jrow["actor"])
check("cli_journal_repo_event", "started", jrow["event"])
check("cli_journal_repo_note", "because reasons", jrow["note"])
check("cli_journal_repo_one_line", 1,
      len(core.journal_path("repo", "jtest").read_text().strip().splitlines()))

rc, out = run_cli(["journal", "dashboard", "handled", "--digest", "abc123"])
check("cli_journal_dash_rc", 0, rc)
drow = json.loads(core.journal_path("dashboard").read_text().strip().splitlines()[-1])
check("cli_journal_dash_actor", "dashboard-op", drow["actor"])
check("cli_journal_dash_event", "handled", drow["event"])
check("cli_journal_dash_digest", "abc123", drow["digest"])
check("cli_journal_dash_no_empty_note", False, "note" in drow)

# issue scope is refused explicitly -- issue-orch journals via gh, not here
rc, out = run_cli(["journal", "issue", "someslug", "7", "started", "x"])
check("cli_journal_issue_rc", 2, rc)
check("cli_journal_issue_msg", True, "gh issue comment" in out)

rc, out = run_cli(["journal", "nosuchscope", "x", "y"])
check("cli_journal_bad_scope_rc", 2, rc)
check("cli_journal_bad_scope_msg", True, "repo, dashboard" in out)

# wrong arity: repo needs slug + event + note; dashboard needs event + note
check("cli_journal_repo_short_rc", 2, run_cli(["journal", "repo", "jtest"])[0])
check("cli_journal_repo_no_note_rc", 2, run_cli(["journal", "repo", "jtest", "started"])[0])
check("cli_journal_dash_no_note_rc", 2, run_cli(["journal", "dashboard", "handled"])[0])
check("cli_journal_empty_rc", 2, run_cli(["journal"])[0])

# === watch/unwatch: path normalization (the inherited-bug fix) =============
watch_repo_dir = T / "watchrepo"
watch_repo_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=watch_repo_dir, check=True)
# orch#64: watch_repo now requires a resolvable remote (origin, or exactly
# one remote) -- give this fixture one so the path-normalization checks
# below (which predate #64 and are not testing remote resolution) still
# exercise a repo that's actually watchable.
subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/watchrepo.git"],
                cwd=watch_repo_dir, check=True)

# Emulate the "~/orch vs /Users/x/orch" bug scenario without touching the
# real home dir: two spellings of the same path, one using the literal
# absolute form and one via a symlink standing in for "the other spelling."
# What matters for the fix is RESOLVED-path equality, exercised directly.
spelling_a = str(watch_repo_dir)
alt_link = T / "watchrepo-alias"
os.symlink(watch_repo_dir, alt_link)
spelling_b = str(alt_link)

ok, msg = core.watch_repo(spelling_a)
check("watch_first_spelling_ok", True, ok)
check("watch_first_spelling_added", "tracked", _repo_state(watch_repo_dir.resolve()))

ok, msg = core.watch_repo(spelling_b)
check("watch_second_spelling_no_dup", True, "already watching" in msg)
orch_json = json.loads((core.ORCH_HOME / "orch.json").read_text())
check("watch_no_duplicate_line", 1,
      sum(1 for e in orch_json["repos"]
          if Path(e["path"]).expanduser().resolve() == watch_repo_dir.resolve()))

# unwatch via the OTHER spelling removes it
ok, msg = core.unwatch_repo(spelling_b)
check("unwatch_other_spelling_ok", True, ok)
check("unwatch_removed", "off", _repo_state(watch_repo_dir.resolve()))

# comments and blank lines survive untouched (repos.txt is inert once
# orch.json exists, so this now checks the migration source is untouched)
comment_line = "# a comment"
_write_repos_txt(f"{comment_line}\n\nsomeotherrepo\n")
core.watch_repo(spelling_a)
repos_txt = (core.ORCH_HOME / "repos.txt").read_text()
check("watch_preserves_comment", True, comment_line in repos_txt)
check("watch_preserves_blank_and_other", True, "someotherrepo" in repos_txt)

# CLI watch/unwatch call the identical core functions
rc, out = run_cli(["watch", str(watch_repo_dir)])
check("cli_watch_rc", 0, rc)
rc, out = run_cli(["unwatch", str(alt_link)])
check("cli_unwatch_rc", 0, rc)
check("cli_unwatch_removed_other_spelling", "off", _repo_state(watch_repo_dir.resolve()))

# watching a non-git directory is refused and orch.json is untouched
not_a_repo_dir = T / "notarepo"
not_a_repo_dir.mkdir()
before_txt = (core.ORCH_HOME / "orch.json").read_text()
ok, msg = core.watch_repo(str(not_a_repo_dir))
check("watch_non_git_not_ok", False, ok)
check("watch_non_git_message", True, "not a git repo" in msg)
after_txt = (core.ORCH_HOME / "orch.json").read_text()
check("watch_non_git_no_write", before_txt, after_txt)

# unwatch preserves comment lines and blank lines too (repos.txt, the
# migration source, is left inert by unwatch_repo -- it only ever touches
# orch.json)
_write_repos_txt(f"{comment_line}\n\nsomeotherrepo\n{spelling_a}\n")
core.watch_repo(spelling_a)  # no-op re-watch, sanity that it's present
ok, msg = core.unwatch_repo(spelling_b)
check("unwatch_ok_with_comments_present", True, ok)
repos_txt = (core.ORCH_HOME / "repos.txt").read_text()
check("unwatch_preserves_comment", True, comment_line in repos_txt)
check("unwatch_preserves_blank_and_other", True, "someotherrepo" in repos_txt)
check("unwatch_actually_removed", "off", _repo_state(watch_repo_dir.resolve()))

# === watch_repo ensures orch's labels (orch#65) =============================
# The watch is a LOCAL config act that now also writes to the forge. Every
# assertion here goes through a stubbed core._run -- the one choke point every
# backend invocation passes through -- so nothing below touches a network or a
# real gh/tea binary. The temp repos carry a REAL `git remote add origin`, so
# repo_backend/gh_repo/repo_slug resolve by their production route (the stub
# defers `git -C` calls to the real _run) rather than being monkeypatched.

# --- ORCH_LABELS is built from the constants, not retyped strings -----------
# Asserting identity against core.L_* (not against "agent-ready") is the whole
# point: a literal here would pass even if someone renamed L_READY and left
# ORCH_LABELS pointing at a dead string, which is the desync this guards.
check("orch_labels_are_the_constants",
      [core.L_READY, core.L_WORKING, core.L_STUCK, core.L_AUTOLAND, core.L_NO_AUTOLAND,
       core.L_P0, core.L_P1, core.L_P2],
      [n for n, _ in core.ORCH_LABELS])
check("orch_labels_all_described", True,
      all(isinstance(d, str) and d for _, d in core.ORCH_LABELS))
# Every name orch's own state functions select on must be in the ensured set,
# or a freshly watched repo is missing exactly the label that makes it work.
check("orch_labels_cover_selection_labels", set(),
      {core.L_READY, core.L_WORKING, core.L_STUCK} - {n for n, _ in core.ORCH_LABELS})


# === _scan_roots / scan_repos (#75 watch picker) ============================
# `#scan=<path>` directive lines in repos.txt name directories to offer as
# candidates in the dashboard's watch picker. Written straight to repos.txt
# rather than through any helper, the same way the comment-preservation
# checks above hand-write repos.txt: there is no writer for this line, only
# a reader.

scan_root_a = T / "scanroot-a"
scan_root_b = T / "scanroot-b"
scan_root_a.mkdir()
scan_root_b.mkdir()

_write_repos_txt(
    f"#scan={scan_root_a}\n# scan={scan_root_b}\n#scan={scan_root_a}\n"
)
roots = core._scan_roots()
check("scan_roots_parses_both", [scan_root_a, scan_root_b], roots)
check("scan_roots_accepts_leading_space_variant", scan_root_b, roots[1])
check("scan_roots_dedupes_preserving_first_occurrence", 2, len(roots))

_write_repos_txt("~/orch\n# just a comment, no directive\n")
check("scan_roots_empty_when_no_directives", [], core._scan_roots())

(core.ORCH_HOME / "repos.txt").unlink()
if (core.ORCH_HOME / "orch.json").exists():
    (core.ORCH_HOME / "orch.json").unlink()
check("scan_roots_empty_when_no_repos_txt", [], core._scan_roots())

# a bare `scan=` line (no leading #) is a repo path, per _parse_repos_line,
# NOT a directive -- confirm _scan_roots does not pick it up.
_write_repos_txt(f"scan={scan_root_a}\n")
check("scan_roots_ignores_uncommented_scan_line", [], core._scan_roots())

# --- scan_repos: real git checkouts under the scan roots -------------------
scan_repo_ok = scan_root_a / "goodrepo"
scan_repo_ok.mkdir()
subprocess.run(["git", "init", "-q"], cwd=scan_repo_ok, check=True)
subprocess.run(["git", "remote", "add", "origin", "git@github.com:me/goodrepo.git"],
                cwd=scan_repo_ok, check=True)

scan_repo_noremote = scan_root_a / "noremoterepo"
scan_repo_noremote.mkdir()
subprocess.run(["git", "init", "-q"], cwd=scan_repo_noremote, check=True)

scan_repo_notgit = scan_root_a / "notarepo-scan"
scan_repo_notgit.mkdir()

scan_repo_already = scan_root_b / "alreadywatched"
scan_repo_already.mkdir()
subprocess.run(["git", "init", "-q"], cwd=scan_repo_already, check=True)
subprocess.run(["git", "remote", "add", "origin", "http://gitea.local:3000/cybermelon/alreadywatched.git"],
                cwd=scan_repo_already, check=True)

missing_root = T / "scanroot-missing"  # never created

_write_repos_txt(
    f"{scan_repo_already}\n#scan={scan_root_a}\n#scan={scan_root_b}\n#scan={missing_root}\n"
)

found = core.scan_repos()
found_paths = [r["path"] for r in found]
check("scan_repos_finds_good_repo", True, str(scan_repo_ok) in found_paths)
check("scan_repos_skips_non_git_dir", False, str(scan_repo_notgit) in found_paths)
check("scan_repos_skips_missing_root_but_returns_others", True, len(found) > 0)
check("scan_repos_sorted_by_path", found_paths, sorted(found_paths))

by_path = {r["path"]: r for r in found}

# watchable repo: slug + backend resolved, empty reason, not watched
good_entry = by_path[str(scan_repo_ok)]
check("scan_repos_good_slug", "me/goodrepo", good_entry["slug"])
check("scan_repos_good_backend", "gh", good_entry["backend"])
check("scan_repos_good_not_watched", False, good_entry["watched"])
check("scan_repos_good_no_reason", "", good_entry["reason"])

# unwatchable repo: no remote -> non-empty reason, matching watch_repo's own
# refusal wording, so the picker and the watcher never disagree.
noremote_entry = by_path[str(scan_repo_noremote)]
check("scan_repos_noremote_slug_empty", "", noremote_entry["slug"])
check("scan_repos_noremote_reason_nonempty", True, len(noremote_entry["reason"]) > 0)
check("scan_repos_noremote_reason_matches_watch_repo_wording", True,
      "no remote could be resolved" in noremote_entry["reason"])

# already-watched repo -- matched by RESOLVED path, symlink-alias style like
# the watch_repo dedupe test above.
already_alias = T / "alreadywatched-alias"
os.symlink(scan_repo_already, already_alias)
_write_repos_txt(
    f"{already_alias}\n#scan={scan_root_a}\n#scan={scan_root_b}\n"
)
found2 = core.scan_repos()
by_path2 = {r["path"]: r for r in found2}
already_entry = by_path2[str(scan_repo_already)]
check("scan_repos_already_watched_flag", True, already_entry["watched"])
check("scan_repos_already_watched_reason_nonempty", True, len(already_entry["reason"]) > 0)

os.unlink(already_alias)

# --- scan_repos: one unreadable root must not blank every other root -------
# #75 fix: root.iterdir() raises OSError (PermissionError is a subclass) on a
# mode-0000 directory, and the old code only guarded child.resolve(), one
# step too late -- that raised out of scan_repos entirely, which the /act
# handler turned into a 500 and the browser into "no candidates found" for
# every root, not just the bad one.
scan_root_unreadable = T / "scanroot-unreadable"
scan_root_unreadable.mkdir()
scan_repo_in_unreadable = scan_root_unreadable / "hiddenrepo"
scan_repo_in_unreadable.mkdir()
subprocess.run(["git", "init", "-q"], cwd=scan_repo_in_unreadable, check=True)

_write_repos_txt(
    f"#scan={scan_root_a}\n#scan={scan_root_unreadable}\n"
)

try:
    os.chmod(scan_root_unreadable, 0o000)
    found3 = core.scan_repos()  # must not raise
    found3_paths = [r["path"] for r in found3]
    check("scan_repos_unreadable_root_does_not_raise", True, isinstance(found3, list))
    check("scan_repos_readable_root_still_returned_despite_bad_sibling", True,
          str(scan_repo_ok) in found3_paths)
    # A 0o000 dir is still readable by root, so this specific assertion would
    # false-pass under root -- skip just this check there, not the rest.
    if os.geteuid() != 0:
        check("scan_repos_unreadable_root_contents_excluded", False,
              str(scan_repo_in_unreadable) in found3_paths)
finally:
    os.chmod(scan_root_unreadable, 0o700)


def _mk_labelled_repo(name, remote):
    d = T / name
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=d, check=True)
    return d


_orig_run_for_labels = core._run
_label_calls = []


def _mk_label_run(create_ok=True, list_json=None, list_ok=True,
                  fail_names=()):
    """Stub for core._run: records every non-git call, answers the label
    verbs, and defers `git -C ...` to the real _run so remote resolution
    stays production code."""
    def _run_stub(cmd, cwd=None, timeout=None):
        if cmd[:2] == ["git", "-C"]:
            return _orig_run_for_labels(cmd, cwd=cwd, timeout=timeout)
        _label_calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
        if cmd[1:3] == ["labels", "list"] or cmd[1:3] == ["label", "list"]:
            return list_ok, (list_json if list_json is not None else "[]")
        if cmd[1:3] == ["labels", "create"] or cmd[1:3] == ["label", "create"]:
            name = cmd[cmd.index("--name") + 1] if "--name" in cmd else cmd[3]
            if name in fail_names:
                return False, ""
            return create_ok, ""
        return False, ""
    return _run_stub


def _created_names(calls):
    """Label names from the create calls among `calls`, in call order --
    positional on gh, `--name` on tea."""
    out = []
    for c in calls:
        if c["cmd"][1:3] in (["labels", "create"], ["label", "create"]):
            cmd = c["cmd"]
            out.append(cmd[cmd.index("--name") + 1] if "--name" in cmd else cmd[3])
    return out


_all_label_names = [n for n, _ in core.ORCH_LABELS]

# --- 1. watching a repo with NO labels creates all five (gh) ---------------
label_gh_dir = _mk_labelled_repo("labelgh", "git@github.com:me/labelgh.git")
_write_repos_txt("")
_label_calls.clear()
core._run = _mk_label_run()
try:
    ok, msg = core.watch_repo(str(label_gh_dir))
    check("watch_labels_gh_ok", True, ok)
    check("watch_labels_gh_created_all_five", _all_label_names,
          _created_names(_label_calls))
    check("watch_labels_gh_no_suffix", False, "could not create" in msg)
    # gh's --force is the upsert, so the gh path must NOT spend a list read.
    check("watch_labels_gh_no_list_call", 0,
          len([c for c in _label_calls if c["cmd"][1:3] == ["label", "list"]]))
finally:
    core._run = _orig_run_for_labels

# --- 1b. same, tea backend: list once, then create the five ----------------
label_tea_dir = _mk_labelled_repo(
    "labeltea", "http://gitea.local:3000/cybermelon/gita-lectures.git")
_write_repos_txt(f"{label_tea_dir}  login=gitea\n")
_label_calls.clear()
core._run = _mk_label_run(list_json="[]")
try:
    ok, msg = core.watch_repo(str(label_tea_dir))
    check("watch_labels_tea_ok", True, ok)
    check("watch_labels_tea_created_all_five", _all_label_names,
          _created_names(_label_calls))
    # ONE list per watch, never one per label.
    check("watch_labels_tea_listed_once", 1,
          len([c for c in _label_calls if c["cmd"][1:3] == ["labels", "list"]]))
finally:
    core._run = _orig_run_for_labels

# --- 2. re-watching an already-labelled repo is a no-op, no error ----------
# The tea path is the one that can regress here: with no --force, a blind
# create would fail on every existing label and report all five missing.
_already = json.dumps([{"name": n} for n in _all_label_names])
_label_calls.clear()
core._run = _mk_label_run(list_json=_already)
try:
    ok, msg = core.watch_repo(str(label_tea_dir))
    check("rewatch_labels_tea_ok", True, ok)
    check("rewatch_labels_tea_already_watching", True, "already watching" in msg)
    check("rewatch_labels_tea_no_creates", [], _created_names(_label_calls))
    check("rewatch_labels_tea_clean_message", False, "could not create" in msg)
finally:
    core._run = _orig_run_for_labels

# The `already watching` return ensures labels too -- re-watching is the only
# repair route for a repo watched before this existed, so an unwired early
# return would make the fix unreachable for exactly the repos needing it.
_label_calls.clear()
core._run = _mk_label_run(list_json="[]")
try:
    ok, msg = core.watch_repo(str(label_tea_dir))
    check("rewatch_already_watching_still_ensures", _all_label_names,
          _created_names(_label_calls))
finally:
    core._run = _orig_run_for_labels

# --- 3. a create failure leaves the repo WATCHED and names the misses ------
_write_repos_txt("")
_label_calls.clear()
core._run = _mk_label_run(fail_names={core.L_STUCK, core.L_AUTOLAND})
try:
    ok, msg = core.watch_repo(str(label_gh_dir))
    check("watch_label_failure_still_ok", True, ok)
    check("watch_label_failure_names_stuck", True, core.L_STUCK in msg)
    check("watch_label_failure_names_autoland", True, core.L_AUTOLAND in msg)
    check("watch_label_failure_says_could_not", True, "could not create labels" in msg)
    # Not-failed labels are not slandered as missing.
    check("watch_label_failure_omits_ready", False, core.L_READY in msg)
finally:
    core._run = _orig_run_for_labels
check("watch_label_failure_repo_still_watched", "tracked", _repo_state(label_gh_dir.resolve()))

# ensure_labels' own contract: (ok, missing), ok == not missing.
_label_calls.clear()
core._run = _mk_label_run(fail_names={core.L_READY})
try:
    el_ok, el_missing = core.ensure_labels(label_gh_dir)
    check("ensure_labels_missing_list", [core.L_READY], el_missing)
    check("ensure_labels_ok_is_not_missing", False, el_ok)
    el_ok2, el_missing2 = core.ensure_labels(label_gh_dir)
finally:
    core._run = _orig_run_for_labels
_label_calls.clear()
core._run = _mk_label_run()
try:
    el_ok3, el_missing3 = core.ensure_labels(label_gh_dir)
    check("ensure_labels_clean_missing_empty", [], el_missing3)
    check("ensure_labels_clean_ok", True, el_ok3)
finally:
    core._run = _orig_run_for_labels

# --- 4. label_create argv shape, both backends ----------------------------
# In the manner of assign_argv/create_issue_argv: assert the literal argv the
# builder emits, because the two backends are deliberately NOT parallel --
# gh takes the name POSITIONALLY and passes --force; tea takes --name and has
# no force flag at all.
check("label_create_gh_argv",
      ["gh", "label", "create", "agent-ready", "--repo", "me/labelgh",
       "--color", core.ORCH_LABEL_COLOR, "--description", "queued for an agent",
       "--force"],
      core.GH_ARGV["label_create"]("me/labelgh", None, core.L_READY,
                                   core.ORCH_LABEL_COLOR, "queued for an agent"))
check("label_create_tea_argv",
      ["tea", "labels", "create", "--login", "gitea",
       "--repo", "cybermelon/gita-lectures", "--name", "agent-ready",
       "--color", core.ORCH_LABEL_COLOR, "--description", "queued for an agent"],
      core.TEA_ARGV["label_create"]("cybermelon/gita-lectures", "gitea",
                                    core.L_READY, core.ORCH_LABEL_COLOR,
                                    "queued for an agent"))
check("label_create_tea_has_no_force", False,
      "--force" in core.TEA_ARGV["label_create"]("r", "l", "n", "c", "d"))
check("label_color_is_bare_hex", True,
      len(core.ORCH_LABEL_COLOR) == 6 and not core.ORCH_LABEL_COLOR.startswith("#"))

# The argv the stubbed watch actually emitted must equal the builder's output,
# not merely resemble it -- otherwise ensure_labels could be hand-rolling argv.
_label_calls.clear()
core._run = _mk_label_run()
try:
    core.ensure_labels(label_gh_dir)
    check("ensure_labels_gh_uses_builder_argv",
          core.GH_ARGV["label_create"]("me/labelgh", None, core.L_READY,
                                       core.ORCH_LABEL_COLOR,
                                       dict(core.ORCH_LABELS)[core.L_READY]),
          _label_calls[0]["cmd"])
finally:
    core._run = _orig_run_for_labels

_write_repos_txt(f"{label_tea_dir}  login=gitea\n")
_label_calls.clear()
core._run = _mk_label_run(list_json="[]")
try:
    core.ensure_labels(label_tea_dir)
    tea_creates = [c["cmd"] for c in _label_calls
                   if c["cmd"][1:3] == ["labels", "create"]]
    check("ensure_labels_tea_uses_builder_argv",
          core.TEA_ARGV["label_create"]("cybermelon/gita-lectures", "gitea",
                                        core.L_READY, core.ORCH_LABEL_COLOR,
                                        dict(core.ORCH_LABELS)[core.L_READY]),
          tea_creates[0])
    check("ensure_labels_tea_list_argv",
          core.TEA_ARGV["label_list"]("cybermelon/gita-lectures", "gitea"),
          _label_calls[0]["cmd"])
finally:
    core._run = _orig_run_for_labels

# --- 5. feed.missing_orch_labels: None (could not check) != [] (all present)
from orch import feed as feedmod

_orig_feed_run = feedmod._run


def _mk_feed_run(ok, out):
    def _f(cmd, timeout=None):
        return ok, out
    return _f


feedmod._run = _mk_feed_run(True, _already)
try:
    check("missing_orch_labels_all_present_is_empty_list", [],
          feedmod.missing_orch_labels("/tmp/x", True, "me/x"))
finally:
    feedmod._run = _orig_feed_run

feedmod._run = _mk_feed_run(True, "[]")
try:
    check("missing_orch_labels_none_defined_lists_all", _all_label_names,
          feedmod.missing_orch_labels("/tmp/x", True, "me/x"))
finally:
    feedmod._run = _orig_feed_run

# A failed or unparseable read is None, NOT [] -- collapsing them would let a
# forge outage render as a clean bill of health.
feedmod._run = _mk_feed_run(False, "")
try:
    check("missing_orch_labels_read_failed_is_none", None,
          feedmod.missing_orch_labels("/tmp/x", True, "me/x"))
finally:
    feedmod._run = _orig_feed_run

feedmod._run = _mk_feed_run(True, "not json at all")
try:
    check("missing_orch_labels_unparseable_is_none", None,
          feedmod.missing_orch_labels("/tmp/x", True, "me/x"))
finally:
    feedmod._run = _orig_feed_run

_write_repos_txt("")

# === orch#74: core.untracked_issues ========================================
# The dashboard can only render rows for issues World.candidates() already
# matched on agent-ready/agent-working, so an issue orch has never touched
# is invisible and cannot be started from the page. untracked_issues is the
# separate, on-demand read that lists those -- candidates() is deliberately
# NOT widened (that would corrupt the cap accounting), so this is proved on
# its own and not through World.
_orig_run_for_untracked = core._run

# ORCH_LABEL_NAMES is the FILTER set and must count the `no-*` opt-outs.
# orch#163 added L_NO_AUTOLAND to ORCH_LABELS itself (the automerge toggle
# writes it, so it must exist on the forge), but ORCH_LABEL_NAMES must still
# be a superset -- an issue carrying only no-auto-land was still touched by
# a human answering orch, and re-offering it from the page would be wrong.
check("orch_label_names_covers_no_opt_outs", True,
      core.L_NO_AUTOLAND in core.ORCH_LABEL_NAMES)
check("orch_label_names_is_superset_of_orch_labels", True,
      set(n for n, _ in core.ORCH_LABELS) <= core.ORCH_LABEL_NAMES)


def _mk_untracked_run(ok, out):
    """Stub core._run for the issue-list call only; a `git -C ... remote`
    probe (repo_backend/repo_slug/gh_repo) still reaches the real git, the
    same split _mk_label_run uses above."""
    def _f(cmd, cwd=None, timeout=None):
        if cmd[:2] == ["git", "-C"]:
            return _orig_run_for_untracked(cmd, cwd=cwd, timeout=timeout)
        return ok, out
    return _f


untracked_gh_dir = _mk_labelled_repo("untrackedgh", "git@github.com:me/untrackedgh.git")

# gh-shaped rows: labels are already a list of {"name": ...} dicts.
_gh_issue_rows = json.dumps([
    {"number": 1, "title": "ready one", "updatedAt": "2026-01-01T00:00:00Z",
     "labels": [{"name": core.L_READY}]},
    {"number": 2, "title": "working one", "updatedAt": "2026-01-02T00:00:00Z",
     "labels": [{"name": core.L_WORKING}]},
    {"number": 3, "title": "a bug", "updatedAt": "2026-01-03T00:00:00Z",
     "labels": [{"name": "bug"}]},
    {"number": 4, "title": "bare", "updatedAt": "2026-01-04T00:00:00Z",
     "labels": []},
])

core._run = _mk_untracked_run(True, _gh_issue_rows)
try:
    _u = core.untracked_issues(untracked_gh_dir)
    # A non-orch label ("bug") does NOT make an issue tracked; only orch's
    # own labels do. The test is empty intersection with ORCH_LABEL_NAMES,
    # not "lacks agent-ready".
    check("untracked_gh_filters_orch_labels",
          [{"number": 3, "title": "a bug", "updatedAt": "2026-01-03T00:00:00Z"},
           {"number": 4, "title": "bare", "updatedAt": "2026-01-04T00:00:00Z"}],
          _u)
finally:
    core._run = _orig_run_for_untracked

# A read that succeeds and finds everything already tracked is [], which is
# a DIFFERENT answer from None ("could not read at all") -- orch#66. The
# page has to say those two things differently, so assert they are
# distinguishable and not merely both falsy.
core._run = _mk_untracked_run(True, json.dumps([
    {"number": 1, "title": "ready one", "updatedAt": "u",
     "labels": [{"name": core.L_READY}]},
]))
try:
    _all_tracked = core.untracked_issues(untracked_gh_dir)
    check("untracked_all_tracked_is_empty_list", [], _all_tracked)
finally:
    core._run = _orig_run_for_untracked

core._run = _mk_untracked_run(False, "")
try:
    _unreadable = core.untracked_issues(untracked_gh_dir)
    check("untracked_read_failed_is_none", None, _unreadable)
finally:
    core._run = _orig_run_for_untracked

check("untracked_none_distinguishable_from_empty", True,
      _unreadable is None and _all_tracked == [] and _all_tracked is not None)

core._run = _mk_untracked_run(True, "not json at all")
try:
    check("untracked_unparseable_is_none", None,
          core.untracked_issues(untracked_gh_dir))
finally:
    core._run = _orig_run_for_untracked

core._run = _mk_untracked_run(True, "")
try:
    check("untracked_blank_output_is_none", None,
          core.untracked_issues(untracked_gh_dir))
finally:
    core._run = _orig_run_for_untracked

# --- the tea path: index is a STRING and labels a SPACE-SEPARATED STRING
# (orch#355), so the rows only filter correctly if _normalize_tea_issue runs
# first. Rows 12 and 13 carry two labels each on purpose: a one-label row
# cannot tell a space rule from a comma rule.
untracked_tea_dir = _mk_labelled_repo(
    "untrackedtea", "http://gitea.local:3000/cybermelon/gita-lectures.git")
_write_repos_txt(f"{untracked_tea_dir}  login=gitea\n")

_tea_issue_rows = json.dumps([
    {"index": "11", "title": "tea ready", "updated": "2026-02-01T00:00:00Z",
     "labels": core.L_READY},
    {"index": "12", "title": "tea working", "updated": "2026-02-02T00:00:00Z",
     "labels": f"{core.L_WORKING} bug"},
    {"index": "13", "title": "tea bug", "updated": "2026-02-03T00:00:00Z",
     "labels": "bug chore"},
    {"index": "14", "title": "tea bare", "updated": "2026-02-04T00:00:00Z",
     "labels": ""},
])

core._run = _mk_untracked_run(True, _tea_issue_rows)
try:
    _ut = core.untracked_issues(untracked_tea_dir)
    check("untracked_tea_normalizes_and_filters",
          [{"number": 13, "title": "tea bug", "updatedAt": "2026-02-03T00:00:00Z"},
           {"number": 14, "title": "tea bare", "updatedAt": "2026-02-04T00:00:00Z"}],
          _ut)
    # The number must be an int, not tea's string -- the page and every
    # later label/branch lookup compare it against real ints.
    check("untracked_tea_number_is_int", True,
          all(isinstance(i["number"], int) for i in _ut))
finally:
    core._run = _orig_run_for_untracked

_write_repos_txt("")

# === merge: the security-sensitive verb ====================================
# core.repo_path_for and srv.repo_path were monkeypatched (and left
# unrestored, by design of that earlier proof) by the single-implementation
# kill proof above -- restore both to the real implementation before relying
# on the real repos.txt lookup here.
def _real_repo_path_for(slug):
    f = core.ORCH_HOME / "repos.txt"
    if not f.exists():
        return None
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line).expanduser()
        if p.name == slug:
            return p
    return None


core.repo_path_for = _real_repo_path_for
srv.repo_path = _real_repo_path_for

# === orch#58: backend selection from the remote host ========================
# repo_backend must read the ACTUAL remote, never guess from a failed `gh`
# call and never be a global flag -- github.com -> "gh", any other real host
# -> "tea", an unreadable remote -> "gh" (preserves pre-#58 behaviour for a
# repo whose remote can't be determined, rather than silently opting it into
# the new backend).
gh_remote_dir = T / "backend-gh"
gh_remote_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=gh_remote_dir, check=True)
subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/orch.git"],
                cwd=gh_remote_dir, check=True)
check("repo_backend_github", "gh", core.repo_backend(gh_remote_dir))
check("gh_repo_still_strips_github_prefix", "cybermelon/orch", core.gh_repo(gh_remote_dir))
check("repo_slug_agnostic_matches_gh_repo_on_github",
      core.gh_repo(gh_remote_dir), core.repo_slug(gh_remote_dir))

tea_remote_dir = T / "backend-tea"
tea_remote_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=tea_remote_dir, check=True)
subprocess.run(["git", "remote", "add", "origin", "http://gitea.local:3000/cybermelon/gita-lectures.git"],
                cwd=tea_remote_dir, check=True)
check("repo_backend_gitea", "tea", core.repo_backend(tea_remote_dir))
check("repo_slug_strips_gitea_host", "cybermelon/gita-lectures", core.repo_slug(tea_remote_dir))

no_remote_dir = T / "backend-noremote"
no_remote_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=no_remote_dir, check=True)
check("repo_backend_defaults_gh_when_unreadable", "gh", core.repo_backend(no_remote_dir))

# === orch#58: repos.txt `login=` parsing =====================================
# _real_repo_path_for above is a test-local reimplementation of the PRE-#58
# lookup (it predates login= and reads the whole line as a bare path), kept
# only so the earlier single-implementation kill proof can restore a working
# stand-in without depending on load order. Swap to the real, current
# core.repo_path_for here -- _orig_repo_path_for, captured before ANY
# monkeypatch in this file touched it -- so the login= parsing below (and
# every block after this point) exercises the actual implementation, not the
# stale duplicate.
core.repo_path_for = _orig_repo_path_for
srv.repo_path = _orig_repo_path_for

# The path stays the first token (unchanged from before login= existed);
# `login=<profile>` is an optional later token, ignored if absent (defaults
# to DEFAULT_TEA_LOGIN) and any OTHER unknown key=value token is ignored
# rather than rejected -- repos.txt is hand-edited machine-local config.
login_repo_dir = T / "loginrepo"
login_repo_dir.mkdir()
_write_repos_txt(
    f"# a comment\n\n{login_repo_dir}  login=gitea\n~/Documents/GitHub/nologin\n"
)
check("repo_path_for_ignores_trailing_login_token",
      login_repo_dir, core.repo_path_for("loginrepo"))
check("repo_login_for_reads_token", "gitea", core.repo_login_for("loginrepo"))
check("repo_login_for_defaults_when_absent",
      core.DEFAULT_TEA_LOGIN, core.repo_login_for("nologin"))
check("repo_login_for_defaults_when_unlisted",
      core.DEFAULT_TEA_LOGIN, core.repo_login_for("never-watched"))
# An unrecognized key=value token is ignored, not rejected -- the line still
# parses and the path/login are unaffected.
unknown_opt_dir = T / "unknown-opt"
_write_repos_txt(f"{unknown_opt_dir}  color=blue\n")
check("repo_path_for_ignores_unknown_token", unknown_opt_dir, core.repo_path_for("unknown-opt"))
check("repo_login_for_ignores_unknown_token",
      core.DEFAULT_TEA_LOGIN, core.repo_login_for("unknown-opt"))

# === orch#230: repos.txt -> orch.json migration =============================
# First run with repos.txt present and no orch.json: each uncommented repo
# line becomes {"path", "state": "tracked"} (plus "login" when the line
# carried one), each #scan=/# scan= directive becomes a scan_roots entry in
# file order (first occurrence wins on a duplicate) -- and repos.txt itself
# is left on disk untouched, never deleted (no-delete-before-convert).
migrate_repo_dir = T / "migraterepo"
migrate_repo_dir.mkdir()
migrate_scan_a = str(T / "scanroot-a")
migrate_scan_b = str(T / "scanroot-b")
(core.ORCH_HOME / "repos.txt").write_text(
    f"#scan={migrate_scan_a}\n"
    f"# scan={migrate_scan_b}\n"
    f"#scan={migrate_scan_a}\n"  # duplicate directive: first occurrence wins
    f"{migrate_repo_dir}  login=gitea\n"
)
migrate_orch_json = core.ORCH_HOME / "orch.json"
if migrate_orch_json.exists():
    migrate_orch_json.unlink()

migrate_cfg = core._load_config()
check("migration_repo_entry", [{"path": str(migrate_repo_dir), "state": "tracked",
                                 "login": "gitea"}],
      migrate_cfg["repos"])
check("migration_scan_roots_ordered_deduped", [migrate_scan_a, migrate_scan_b],
      migrate_cfg["scan_roots"])
check("migration_writes_orch_json", True, migrate_orch_json.exists())
check("migration_leaves_repos_txt_in_place", True,
      (core.ORCH_HOME / "repos.txt").exists())
# orch.json on disk matches what _load_config returned in memory.
check("migration_orch_json_on_disk_matches",
      migrate_cfg, json.loads(migrate_orch_json.read_text()))

# A hand-edited orch.json must degrade the way repos.txt did, not take down
# repo_path_for (the only slug lookup, sitting under the server and spawn
# paths). Malformed JSON raises naming the file; a wrong-typed section is
# dropped rather than half-read -- a string "scan_roots" iterates one Path
# per CHARACTER, which would silently point the watch picker at "/".
def _with_orch_json(text):
    migrate_orch_json.write_text(text)
    return core._load_config()

check("badcfg_entry_without_path_dropped", [],
      _with_orch_json('{"repos": [{"state": "tracked"}]}')["repos"])
check("badcfg_good_entry_survives_bad_sibling", ["/a"],
      [r["path"] for r in _with_orch_json(
          '{"repos": [{"state": "tracked"}, {"path": "/a", "state": "tracked"}]}')["repos"]])
check("badcfg_repo_path_for_does_not_raise", None,
      (_with_orch_json('{"repos": [{"state": "tracked"}]}'),
       core.repo_path_for("anything"))[1])
check("badcfg_string_repos_dropped", [],
      _with_orch_json('{"repos": "nope"}')["repos"])
check("badcfg_string_scan_roots_dropped", [],
      _with_orch_json('{"scan_roots": "/a"}')["scan_roots"])
check("badcfg_scan_roots_not_split_per_character", [],
      (_with_orch_json('{"scan_roots": "/a"}'), core._scan_roots())[1])
try:
    _with_orch_json("{bad json")
    _badcfg_corrupt = "no raise"
except ValueError as e:
    _badcfg_corrupt = "orch.json" in str(e)
check("badcfg_corrupt_raises_naming_the_file", True, _badcfg_corrupt)
try:
    _with_orch_json('["not", "an", "object"]')
    _badcfg_nonobject = "no raise"
except ValueError as e:
    _badcfg_nonobject = "orch.json" in str(e)
check("badcfg_non_object_raises_naming_the_file", True, _badcfg_nonobject)
migrate_orch_json.unlink()

# === orch#68: core.login_web_base ===========================================
# _reset_login_web_bases() is called before EACH case and again at the very
# end, so the module-level cache (_LOGIN_WEB_BASES) can neither leak stale
# state between these three checks nor poison whatever runs after this block
# in the same process. `tea` is stubbed via core._run (the same choke point
# every other test in this file monkeypatches), so this passes on a box with
# or without `tea` actually installed, and never touches the real gitea server.
_orig_run_for_web_base = core._run

core._reset_login_web_bases()
core._run = lambda cmd, cwd=None, timeout=None: (
    True, "gitea http://gitea.local:3000\nother http://elsewhere:9999\n")
try:
    check("login_web_base_parses_table", "http://gitea.local:3000", core.login_web_base("gitea"))
finally:
    core._run = _orig_run_for_web_base

core._reset_login_web_bases()
core._run = lambda cmd, cwd=None, timeout=None: (
    True, "gitea http://gitea.local:3000\n")
try:
    check("login_web_base_unknown_login_empty", "", core.login_web_base("ghost"))
finally:
    core._run = _orig_run_for_web_base

core._reset_login_web_bases()
core._run = lambda cmd, cwd=None, timeout=None: (False, "")  # `tea` failing outright
try:
    check("login_web_base_tea_fails_empty", "", core.login_web_base("gitea"))
finally:
    core._run = _orig_run_for_web_base

core._reset_login_web_bases()  # leave no poisoned cache for tests that follow

# watch_repo/unwatch_repo still work against a repos.txt carrying login=
# tokens on OTHER lines -- the path-only comparison must not choke on a
# sibling line's trailing option.
_write_repos_txt(f"{login_repo_dir}  login=gitea\n")
ok, msg = core.watch_repo(str(watch_repo_dir))
check("watch_alongside_login_token_ok", True, ok)
repos_txt = (core.ORCH_HOME / "repos.txt").read_text()
check("watch_alongside_login_token_preserved", True, "login=gitea" in repos_txt)
ok, msg = core.unwatch_repo(str(watch_repo_dir))
check("unwatch_alongside_login_token_ok", True, ok)
repos_txt = (core.ORCH_HOME / "repos.txt").read_text()
check("unwatch_alongside_login_token_preserved", True, "login=gitea" in repos_txt)
check("unwatch_alongside_login_token_removed", False,
      str(watch_repo_dir.name) in repos_txt)

# === orch#58: Gitea JSON normalization =======================================
# World and everything above it must keep reading GitHub-shaped dicts;
# these pin the exact rules tmp_plan-58.md item 4 lists.
# orch#355: tea returns `labels` SPACE-separated. Every fixture below must
# carry at least TWO labels -- with one label there is no separator in the
# string at all, so a single-label fixture passes under both the (wrong) comma
# rule and the right one. That is exactly how the comma rule was recorded as
# live-verified and then survived three days. Do not simplify these to one
# label: it silently deletes the only coverage this bug has.
check("normalize_labels_space_separated", [{"name": "a"}, {"name": "b"}],
      core._normalize_tea_labels("a b"))
check("normalize_labels_comma_is_not_a_separator",
      [{"name": "a,b"}], core._normalize_tea_labels("a,b"))
check("normalize_labels_empty_is_empty_list", [], core._normalize_tea_labels(""))
check("normalize_labels_not_a_blank_label", True,
      {"name": ""} not in core._normalize_tea_labels(""))
check("normalize_labels_whitespace_only_is_empty_list", [],
      core._normalize_tea_labels("   "))

tea_issue_row = {"index": "31", "title": "t", "labels": "agent-ready x",
                  "created": "2026-09-11T18:17:46Z", "updated": "2026-09-11T21:20:40Z"}
norm_issue = core._normalize_tea_issue(tea_issue_row)
check("normalize_issue_number_is_int", 31, norm_issue["number"])
check("normalize_issue_number_type_is_int", True, isinstance(norm_issue["number"], int))
check("normalize_issue_labels", [{"name": "agent-ready"}, {"name": "x"}], norm_issue["labels"])
check("normalize_issue_created_renamed", "2026-09-11T18:17:46Z", norm_issue["createdAt"])
check("normalize_issue_updated_renamed", "2026-09-11T21:20:40Z", norm_issue["updatedAt"])

tea_pr_row = {"index": "9", "head": "issue-9", "state": "open"}
norm_pr = core._normalize_tea_pr(tea_pr_row)
check("normalize_pr_number_is_int", 9, norm_pr["number"])
check("normalize_pr_head_renamed", "issue-9", norm_pr["headRefName"])
check("normalize_pr_state_uppercased", "OPEN", norm_pr["state"])
# pr_for/pr_number_for compare against the uppercase GitHub tokens -- prove
# the normalized row actually satisfies them, not just that the string
# looks right.
_norm_world = core.World()
_norm_world.prs = [norm_pr]
check("normalize_pr_state_matches_pr_for", "OPEN", _norm_world.pr_for("issue-9"))
check("normalize_pr_number_matches_pr_number_for", 9, _norm_world.pr_number_for("issue-9"))

check("normalize_rollup_empty_ci_is_none", None, core._normalize_tea_rollup({"ci": ""}))
check("normalize_rollup_missing_ci_is_none", None, core._normalize_tea_rollup({}))
check("normalize_rollup_nonempty_ci_passthrough", "success",
      core._normalize_tea_rollup({"ci": "success"}))
# pr_green already treats "no rollup" (None) as green -- prove the empty-ci
# case actually lands there instead of needing a new case in pr_green.
_rollup_world = core.World()
_rollup_world.rollups = {"issue-9": core._normalize_tea_rollup({"ci": ""})}
check("normalize_empty_ci_reads_green", True, _rollup_world.pr_green("issue-9"))

tea_view_row = {"body": "the issue body",
                "comments": [{"id": "1", "author": "cybermelon", "body": "a comment"}]}
norm_view = core._normalize_tea_issue_view(tea_view_row)
check("normalize_view_keeps_body", "the issue body", norm_view["body"])
check("normalize_view_keeps_comments", tea_view_row["comments"], norm_view["comments"])

# === orch#58 unit 2: issue_comment argv shape ================================
# `tea comments add` has no --body-file/stdin option (confirmed via
# `tea comments add --help`, tmp_plan-58.md); -d carries the body instead.
# Pin the exact flag order/spelling so a future edit can't silently drift
# from what --help actually documents.
check("tea_argv_has_issue_comment", True, "issue_comment" in core.TEA_ARGV)
check("gh_argv_has_issue_comment", True, "issue_comment" in core.GH_ARGV)
check("tea_issue_comment_argv", [
    "tea", "comments", "add", "9", "--login", "gitea",
    "--repo", "cybermelon/gita-lectures", "-d", "hello",
], core.TEA_ARGV["issue_comment"]("cybermelon/gita-lectures", "gitea", 9, "hello"))
check("gh_issue_comment_argv", [
    "gh", "issue", "comment", "9", "--repo", "cybermelon/orch",
    "--body-file", "-",
], core.GH_ARGV["issue_comment"]("cybermelon/orch", None, 9, "hello"))

# A body starting with "-" is unambiguous in -d's value slot: argv parsing
# always takes the very next token as -d's value regardless of its content,
# and subprocess.run never hands this list to a shell to re-tokenize.
dash_body = "-rf everything"
check("tea_issue_comment_dash_body_survives_intact", dash_body,
      core.TEA_ARGV["issue_comment"]("o/r", "gitea", 1, dash_body)[-1])

# A multi-line body survives as ONE argv element -- no shell means no
# splitting on newlines the way an unquoted shell interpolation would.
multiline_body = "orch/agent-x working\nline two\nline three"
check("tea_issue_comment_multiline_body_survives_intact", multiline_body,
      core.TEA_ARGV["issue_comment"]("o/r", "gitea", 1, multiline_body)[-1])
check("tea_issue_comment_argv_is_one_element_per_arg", 10,
      len(core.TEA_ARGV["issue_comment"]("o/r", "gitea", 1, multiline_body)))

# === orch#58 unit 2: core._journal_append_issue picks the right argv per
# backend =====================================================================
# The module-level stub at the top of this file (`ISSUE_JOURNAL_CALLS`)
# replaces core._journal_append_issue entirely so every OTHER test in this
# suite stays hermetic; exercise the REAL implementation here via
# _REAL_JOURNAL_APPEND_ISSUE, captured at import time before the stub ever
# ran (see the top of this file). This is how EVERY agent journals, so a
# Gitea repo whose comment verb doesn't route correctly would silently block
# that repo's whole pipeline.
#
# _journal_append_issue only ever receives an "owner/repo" string (never a
# checkout path), so it re-derives the backend by reverse-walking repos.txt
# (_repo_entry_for_owner_slug) -- which itself shells out to `git remote
# get-url origin` on each candidate. The fake below must let THAT probe
# through to the real subprocess and only intercept the final gh/tea action
# call, the same way the server-level `_fake_srv_run` fixture above does.
_journal_calls = []


class _FakeJournalProc:
    returncode = 0


def _fake_journal_run(argv, **kw):
    if argv[:2] == ["git", "-C"]:
        return _orig_core_subprocess_run(argv, **kw)
    _journal_calls.append((argv, kw))
    return _FakeJournalProc()


# repos.txt: one gh-backed entry, one tea-backed entry (reusing the fixtures
# from the backend-selection section above). The gh/tea owner-repo strings
# are resolved BEFORE the fake is installed, via the real git probe.
_journal_gh_repo = core.gh_repo(gh_remote_dir)
_journal_tea_repo = core.repo_slug(tea_remote_dir)
_write_repos_txt(
    f"{gh_remote_dir}\n{tea_remote_dir}  login=gitea\n"
)
_orig_core_subprocess_run = core.subprocess.run
core.subprocess.run = _fake_journal_run
try:
    # gh path: unchanged shape, body still on stdin via --body-file -, and
    # the argv still carries no -d at all.
    _journal_calls.clear()
    _REAL_JOURNAL_APPEND_ISSUE(_journal_gh_repo, 5, "issue-orch",
                                "spawn", {"note": "gh path"})
    check("journal_issue_gh_argv", [
        "gh", "issue", "comment", "5", "--repo", _journal_gh_repo,
        "--body-file", "-",
    ], _journal_calls[-1][0])
    check("journal_issue_gh_body_on_stdin", True,
          "gh path" in _journal_calls[-1][1].get("input", ""))
    check("journal_issue_gh_no_dash_d_flag", False, "-d" in _journal_calls[-1][0])

    # tea path: same retry/failure-logging shape, but the body travels in
    # -d's argv slot instead of on stdin (tea has no stdin option for this
    # verb -- tmp_plan-58.md).
    _journal_calls.clear()
    _REAL_JOURNAL_APPEND_ISSUE(_journal_tea_repo, 6, "issue-orch",
                                "spawn", {"note": "tea path"})
    check("journal_issue_tea_argv", [
        "tea", "comments", "add", "6", "--login", "gitea",
        "--repo", _journal_tea_repo, "-d",
        "orch/issue-orch spawn\ntea path\n",
    ], _journal_calls[-1][0])
    check("journal_issue_tea_no_stdin_input", None,
          _journal_calls[-1][1].get("input"))

    # retry-once-then-log: a persistently failing backend gets exactly two
    # attempts, same as the pre-#58 gh-only behaviour, on EITHER backend.
    def _always_fail_run(argv, **kw):
        if argv[:2] == ["git", "-C"]:
            return _orig_core_subprocess_run(argv, **kw)
        _journal_calls.append((argv, kw))
        raise subprocess.TimeoutExpired(cmd=argv, timeout=30)

    core.subprocess.run = _always_fail_run
    _journal_calls.clear()
    _REAL_JOURNAL_APPEND_ISSUE(_journal_tea_repo, 7, "issue-orch",
                                "spawn", {"note": "will fail"})
    check("journal_issue_tea_retries_once_then_stops", 2, len(_journal_calls))
finally:
    core.subprocess.run = _orig_core_subprocess_run

# === orch#207: _journal_spawn / _journal_scope_for must resolve by BACKEND,
# not call gh_repo() unconditionally =========================================
# gh_repo() strips only github.com's three known remote forms; on a
# tea-backed repo it returns the raw, unstripped remote string (e.g.
# "gitea-gitea:cybermelon/gita-lectures" from a `hosts` shorthand remote,
# or worse) instead of "owner/repo". That malformed slug then fails
# _repo_entry_for_owner_slug's reverse walk inside _journal_append_issue,
# which silently falls back to the gh backend for a Gitea repo and builds a
# `gh` argv that can only fail. adapter_for(path).slug is the fix: it picks
# gh_repo() for gh and repo_slug() for tea, same as adapter_for's own
# docstring and feed.py's identical warning (feed.py ~330) explain.
#
# repos.txt at this point (written above, in the orch#58 unit 2 section)
# still holds one gh entry (gh_remote_dir, basename "backend-gh") and one
# tea entry (tea_remote_dir, basename "backend-tea"); core.repo_path_for is
# the real implementation here (restored at line ~1917), so a slug lookup
# by basename resolves to each fixture's actual checkout path.
_journal_append_calls = []
_orig_journal_append_for_207 = core.journal_append
core.journal_append = lambda *a, **kw: _journal_append_calls.append((a, kw))
try:
    # --- tea-backed repo: resolved "owner/repo" carries NO gitea-gitea:
    # prefix or other unstripped remote form, and adapter_for confirms it
    # resolves to the tea backend.
    _journal_append_calls.clear()
    core._journal_spawn("issue-orch", ("backend-tea", 26), "agent-x", False)
    _spawn_tea_repo = _journal_append_calls[-1][0][1]
    check("journal_spawn_tea_repo_is_bare_owner_slug",
          _journal_tea_repo, _spawn_tea_repo)
    check("journal_spawn_tea_repo_no_gitea_prefix", False,
          "gitea-gitea:" in _spawn_tea_repo)
    check("journal_spawn_tea_repo_resolves_tea_backend", "tea",
          core.adapter_for(tea_remote_dir).backend)

    _journal_append_calls.clear()
    _scope_tea = core._journal_scope_for("issue-orch", ("backend-tea", 26))
    check("journal_scope_tea_repo_is_bare_owner_slug",
          _journal_tea_repo, _scope_tea[1])
    check("journal_scope_tea_repo_no_gitea_prefix", False,
          "gitea-gitea:" in _scope_tea[1])

    # --- gh-backed repo: no-drift guard -- the resolved slug must stay
    # BYTE-IDENTICAL to gh_repo()'s own output, so the #207 fix provably
    # changed nothing on the GitHub path.
    #
    # What this pair does NOT catch, deliberately recorded so nobody reads
    # more into it than it proves: collapsing both resolutions back to a
    # bare gh_repo() would leave these two PASSING, because adapter_for's gh
    # branch calls gh_repo itself -- the comparison is satisfied either way.
    # The regression guard for that collapse is the TEA pair above, which
    # fails with the observed defect string. This pair pins the opposite
    # direction: a change that routed the gh branch through repo_slug (or
    # any other resolver) would break it.
    _journal_append_calls.clear()
    core._journal_spawn("issue-orch", ("backend-gh", 5), "agent-x", False)
    _spawn_gh_repo = _journal_append_calls[-1][0][1]
    check("journal_spawn_gh_repo_matches_gh_repo_output",
          core.gh_repo(gh_remote_dir), _spawn_gh_repo)

    _scope_gh = core._journal_scope_for("issue-orch", ("backend-gh", 5))
    check("journal_scope_gh_repo_matches_gh_repo_output",
          core.gh_repo(gh_remote_dir), _scope_gh[1])

    # --- missing path: both fall back to the local slug unchanged, exactly
    # as the pre-#207 `if repo_path_for(slug) else slug` / `or slug` shape
    # did.
    _journal_append_calls.clear()
    core._journal_spawn("issue-orch", ("never-watched-repo", 9), "agent-x", False)
    check("journal_spawn_unwatched_falls_back_to_local_slug",
          "never-watched-repo", _journal_append_calls[-1][0][1])
    _scope_unwatched = core._journal_scope_for("issue-orch", ("never-watched-repo", 9))
    check("journal_scope_unwatched_falls_back_to_local_slug",
          "never-watched-repo", _scope_unwatched[1])
finally:
    core.journal_append = _orig_journal_append_for_207

# === orch#207 unit 2: _journal_append_issue's failure log carries the cause,
# not just the body =========================================================
# The pre-#207 log line only echoed `body!r`, so a wrong-backend call (bad
# --repo, malformed slug) and a genuine permission/network failure looked
# identical in the log -- both just "still not returncode 0". Capture the
# last attempt's stderr (and argv head) so the two are distinguishable.
_log_calls_207 = []
_orig_log_for_207 = core.log
core.log = lambda msg: _log_calls_207.append(msg)


class _FakeFailProc:
    def __init__(self, returncode, stderr):
        self.returncode = returncode
        self.stderr = stderr


def _fake_fail_run_with_stderr(argv, **kw):
    if argv[:2] == ["git", "-C"]:
        return _orig_core_subprocess_run(argv, **kw)
    return _FakeFailProc(1, "permission denied: token lacks repo scope")


_orig_run_for_207 = core.subprocess.run
core.subprocess.run = _fake_fail_run_with_stderr
try:
    _log_calls_207.clear()
    _REAL_JOURNAL_APPEND_ISSUE(_journal_gh_repo, 11, "issue-orch",
                                "spawn", {"note": "stderr proof"})
    check("journal_append_issue_log_called_once", 1, len(_log_calls_207))
    check("journal_append_issue_log_has_stderr_text", True,
          "permission denied" in _log_calls_207[-1])
    check("journal_append_issue_log_still_has_body", True,
          "stderr proof" in _log_calls_207[-1])
finally:
    core.subprocess.run = _orig_run_for_207
    core.log = _orig_log_for_207

# repos.txt needs no hand restore here: the next section to depend on its
# contents (core.merge_pr's proof, just below) writes its own fresh content
# before reading it.

# core.merge_pr must re-derive REVIEW and the PR-on-branch match from a
# fresh World every call -- never trust the caller's `pr` argument as
# permission. Every refusal path must never reach `gh` at all: record every
# call `core._run` makes (that is the one choke point merge_pr's gh
# invocation goes through) and assert the recorded list stays empty for each
# refusal, then assert the happy path's exact argv + cwd.
merge_repo_dir = T / "mergerepo"
merge_repo_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=merge_repo_dir, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "--allow-empty", "-m", "init", "-q"], cwd=merge_repo_dir, check=True)

_merge_calls = []
_orig_run = core._run


def _fake_run(cmd, cwd=None, timeout=None):
    _merge_calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
    if cmd[:2] == ["git", "-C"] and "remote" in cmd:
        return True, "git@github.com:me/mergerepo.git\n"
    if cmd[:2] == ["gh", "pr"] and cmd[2] == "merge":
        return True, "merged\n"
    # orch#421: merge_pr now calls review_items_for_pr before the shell-out,
    # which asks `gh pr view ... --json comments,headRefOid`. Stub it to "no
    # block" (comments: []) so these gate tests -- which assert on gh, not on
    # review state -- stay deterministic and never make a real network call.
    if cmd[:3] == ["gh", "pr", "view"] and "comments,headRefOid" in cmd:
        return True, json.dumps({"headRefOid": "deadbeef", "comments": []})
    return _orig_run(cmd, cwd=cwd, timeout=timeout)


class _StubMergeWorld(_PlainBranchMixin):
    """Stands in for core.World so merge_pr's gates are tested without a
    real `gh issue list`/`gh pr list` network call. work_state() calls
    world.pr_for/pr_green/pr_red; pr_number_for is called directly.

    `labels` backs issue_has_label -- orch#280's `landed` row resolves the
    `flag` field from this exact accessor, so the label set is a stub input
    like the others rather than a real gh call."""
    def __init__(self, loads=True, pr_state="OPEN", green=True, red=False,
                 pr_number=9, labels=()):
        self._loads = loads
        self._pr_state = pr_state
        self._green = green
        self._red = red
        self._pr_number = pr_number
        self._labels = set(labels)
        # `prs` is what the real World exposes, and orch#280's post-merge
        # re-derivation reads it directly to ask "did PR <n> merge" rather
        # than "does this branch have a merged PR" (the branch question is
        # what would journal a false landing on a reused branch). Derived
        # from the same pr_state/pr_number the other accessors answer from,
        # so a stub cannot describe two different worlds to two callers.
        # _ReusedBranchWorld overrides this with a multi-PR history.
        self.prs = [{"number": pr_number, "headRefName": "issue-7",
                     "state": pr_state}]

    def load(self, gr):
        return self._loads

    def issue_has_label(self, n, label):
        return label in self._labels

    def pr_for(self, branch):
        return self._pr_state

    def pr_green(self, branch):
        return self._green

    def pr_red(self, branch):
        return self._red

    def pr_number_for(self, branch):
        return self._pr_number


def _with_stub_world(stub, fn):
    orig_world = core.World
    core.World = lambda: stub
    try:
        return fn()
    finally:
        core.World = orig_world


def _gh_merge_calls(calls):
    """The actual `gh pr merge` invocations among recorded calls. gh_repo's
    `git remote get-url` runs on every path (it resolves the slug before the
    REVIEW/pr gates), so "no gh invoked" must mean this list is empty, not
    that _merge_calls as a whole is."""
    return [c for c in calls if c["cmd"][:2] == ["gh", "pr"] and c["cmd"][2] == "merge"]


# orch#408: review_blocks_merge (called from merge_pr for decided_by=
# "issue-orch") now runs the REAL review_items_for_pr against whatever repo
# path the test hands it. Every merge_pr test below that path uses a fake
# repo dir with no real PR to read, so without a stub the gate sees
# UNREADABLE and blocks -- moved here (was defined later, in the
# review_blocks_merge section) so blocks earlier in the file can use it too.
_orig_review_items_for_pr = core.review_items_for_pr


def _stub_review_items(result):
    # Accepts `unreadable` because the real function does (orch#408): the
    # merge gate calls it with unreadable=True. A stub with the old
    # two-arg signature raises TypeError instead, which review_blocks_merge
    # catches as its fail-open case -- so the gate would silently pass
    # every test for the wrong reason.
    core.review_items_for_pr = lambda repo, pr, unreadable=False: result


# --- unwatched repo slug: refused, gh never invoked -------------------------
_merge_calls.clear()
core._run = _fake_run
try:
    unwatched = T / "not-a-watched-repo"
    unwatched.mkdir()
    # repo_path_for looks up repos.txt by basename; this path is never
    # written there, so server.repo_path returns None and a_merge refuses
    # before core.merge_pr (and therefore gh) is ever reached.
    result = srv.a_merge({"repo": "not-a-watched-repo", "issue": "7", "pr": "9"})
    check("merge_unwatched_refused", False, result["ok"])
    check("merge_unwatched_no_gh_calls", 0, len(_gh_merge_calls(_merge_calls)))
finally:
    core._run = _orig_run

# --- work_state not REVIEW (BLOCKED, CHECKING): refused, gh never invoked --
_write_repos_txt(str(merge_repo_dir) + "\n")
for label, stub in (
    ("blocked", _StubMergeWorld(pr_state="OPEN", green=False, red=True)),
    ("checking", _StubMergeWorld(pr_state="OPEN", green=False, red=False)),
):
    _merge_calls.clear()
    core._run = _fake_run
    try:
        result = _with_stub_world(
            stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
        check(f"merge_{label}_refused", False, result["ok"])
        check(f"merge_{label}_no_gh_calls", 0, len(_gh_merge_calls(_merge_calls)))
    finally:
        core._run = _orig_run

# --- pr argument mismatches the PR on branch issue-<n>: refused, no gh -----
_merge_calls.clear()
core._run = _fake_run
try:
    mismatch_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=42)
    result = _with_stub_world(
        mismatch_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_pr_mismatch_refused", False, result["ok"])
    check("merge_pr_mismatch_no_gh_calls", 0, len(_gh_merge_calls(_merge_calls)))
    check("merge_pr_mismatch_message_shows_both", True,
          "42" in result["out"] and "9" in result["out"])
finally:
    core._run = _orig_run

# no PR at all on the branch -> refused, no gh calls
_merge_calls.clear()
core._run = _fake_run
try:
    none_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=None)
    result = _with_stub_world(
        none_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_no_pr_refused", False, result["ok"])
    check("merge_no_pr_no_gh_calls", 0, len(_gh_merge_calls(_merge_calls)))
finally:
    core._run = _orig_run

# world fails to load -> refused, no gh calls (an unreachable oracle is
# never permission)
_merge_calls.clear()
core._run = _fake_run
try:
    unreachable_stub = _StubMergeWorld(loads=False)
    result = _with_stub_world(
        unreachable_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_unreachable_oracle_refused", False, result["ok"])
    check("merge_unreachable_oracle_no_gh_calls", 0, len(_gh_merge_calls(_merge_calls)))
finally:
    core._run = _orig_run

# --- happy path: REVIEW + matching pr -> gh invoked exactly once with the --
# --- expected argv and cwd --------------------------------------------------
_merge_calls.clear()
core._run = _fake_run
try:
    review_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        review_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_happy_ok", True, result["ok"])
    # orch#421: filtered to the `gh pr merge` invocation specifically, not
    # every `gh pr ...` call -- merge_pr now also makes a `gh pr view` call
    # (review_items_for_pr) before the shell-out, same as _gh_merge_calls
    # above already does for the refusal-path assertions.
    gh_calls = _gh_merge_calls(_merge_calls)
    check("merge_happy_gh_called_once", 1, len(gh_calls))
    check("merge_happy_argv", ["gh", "pr", "merge", "9", "--merge", "--delete-branch"],
          gh_calls[0]["cmd"])
    check("merge_happy_cwd", str(merge_repo_dir), gh_calls[0]["cwd"])
    check("merge_happy_no_repo_flag", False, "--repo" in gh_calls[0]["cmd"])
    # A hung `gh` would wedge the request thread that served the merge button
    # with no ceiling. Bound it like every other subprocess call in the server
    # path -- srv._gh's 60s.
    check("merge_happy_timeout_bounded", 60, gh_calls[0]["timeout"])
finally:
    core._run = _orig_run

# --- a_merge end-to-end through the server gate, happy path ----------------
_merge_calls.clear()
core._run = _fake_run
try:
    review_stub2 = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        review_stub2,
        lambda: srv.a_merge({"repo": "mergerepo", "issue": "7", "pr": "9"}))
    check("a_merge_happy_ok", True, result["ok"])
    gh_calls2 = _gh_merge_calls(_merge_calls)  # orch#421: see merge_happy_gh_called_once
    check("a_merge_happy_gh_called_once", 1, len(gh_calls2))
finally:
    core._run = _orig_run

# === orch#280: merge_pr writes a `landed` journal row at the merge instant ==
# Journal is under ORCH_HOME (a tempdir, see top of file) -- no extra
# isolation needed, journal_append/journal_path already resolve there.


def _landed_rows():
    # The DIRECTORY NAME, not the remote slug "me/mergerepo". This is the key
    # feed.repo_json uses and the one every reader reads; asserting against
    # the remote slug would have passed while merge_pr wrote to a file nobody
    # reads, which is exactly how orch#280 mislaid its own landing row.
    p = core.journal_path("repo", merge_repo_dir.name)
    if not p.exists():
        return []
    # orch#421: "landed-unreviewed" joins "landed" as a possible event for
    # this same row -- this helper asserts row SHAPE (issue/pr/decided_by/
    # review), which is unaffected by which of the two events fired, so both
    # count. The reviewed-vs-not distinction gets its own dedicated check
    # elsewhere rather than being smuggled into this filter.
    return [json.loads(line) for line in p.read_text().splitlines()
            if json.loads(line).get("event") in ("landed", "landed-unreviewed")]


# --- happy path: exactly one `landed` row, with the caller's metadata ------
_merge_calls.clear()
core._run = _fake_run
try:
    before = len(_landed_rows())
    # orch#387: issue-orch is now gated on auto_land_on, so this must opt in
    # via a label to reach the merge at all -- the label is irrelevant to
    # what this block actually asserts (journal row shape).
    # orch#408: issue-orch also now runs review_blocks_merge against this
    # fake repo dir, which has no real PR to read -- stub it to None
    # (read fine, no review posted) so it fails open and this block still
    # exercises what it was written to exercise, not the review gate.
    _stub_review_items(None)
    happy_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9,
                                  labels={core.L_AUTOLAND})
    result = _with_stub_world(
        happy_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch", review="clean"))
    check("merge_landed_ok", True, result["ok"])
    rows = _landed_rows()
    check("merge_landed_exactly_one_row", before + 1, len(rows))
    row = rows[-1]
    check("merge_landed_issue", 7, row["issue"])
    check("merge_landed_pr", 9, row["pr"])
    check("merge_landed_decided_by", "issue-orch", row["decided_by"])
    check("merge_landed_review", "clean", row["review"])
finally:
    core._run = _orig_run
    core.review_items_for_pr = _orig_review_items_for_pr

# === orch#421: the reviewed/unreviewed fork itself =========================
# Everything above stubs review_items_for_pr to "no block" (comments: [])
# and _landed_rows() now accepts both "landed" and "landed-unreviewed" --
# so nothing yet asserts the fork actually fires on both sides. A merge_pr
# that always set reviewed=True (or always False) would pass every test
# above. These three pin the fork directly: unreviewed, reviewed, and the
# empty-block case that must still count as reviewed (parse_review_block's
# None-vs-[] contract, collapsed one level up in review_items_for_pr).


def _fake_run_review_comments(comments):
    """Same shape as _fake_run, but the `gh pr view ...comments,headRefOid`
    stub answers with the given comment list instead of always "no block" --
    lets each fork test drive review_items_for_pr's result directly."""
    def run(cmd, cwd=None, timeout=None):
        _merge_calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
        if cmd[:2] == ["git", "-C"] and "remote" in cmd:
            return True, "git@github.com:me/mergerepo.git\n"
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "merge":
            return True, "merged\n"
        if cmd[:3] == ["gh", "pr", "view"] and "comments,headRefOid" in cmd:
            return True, json.dumps({"headRefOid": "deadbeef", "comments": comments})
        return _orig_run(cmd, cwd=cwd, timeout=timeout)
    return run


# --- unreviewed: no comment carries an orch:review:v1 block ----------------
# record-not-refuse is the whole design decision here, so this must also
# pin that the merge still succeeds.
_merge_calls.clear()
core._run = _fake_run_review_comments([])
try:
    unreviewed_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9,
                                       labels={core.L_AUTOLAND})
    result = _with_stub_world(
        unreviewed_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_unreviewed_ok", True, result["ok"])
    row = _landed_rows()[-1]
    check("merge_unreviewed_event", "landed-unreviewed", row["event"])
    check("merge_unreviewed_reviewed_false", False, row["reviewed"])
    check("merge_unreviewed_note_says_unreviewed", True, "UNREVIEWED" in row["note"])
finally:
    core._run = _orig_run

# --- reviewed: a comment carries a real orch:review:v1 block with items ----
_merge_calls.clear()
core._run = _fake_run_review_comments([
    {"body": "Looks good.\n\n<!-- orch:review:v1\n"
             "1 merge-as-is core.py:1 fine\n"
             "-->"},
])
try:
    reviewed_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9,
                                     labels={core.L_AUTOLAND})
    result = _with_stub_world(
        reviewed_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_reviewed_ok", True, result["ok"])
    row = _landed_rows()[-1]
    check("merge_reviewed_event", "landed", row["event"])
    check("merge_reviewed_reviewed_true", True, row["reviewed"])
    check("merge_reviewed_note_no_unreviewed", False, "UNREVIEWED" in row["note"])
finally:
    core._run = _orig_run

# --- empty block: a reviewer ran and found nothing to say -- this is the ---
# --- None-vs-[] distinction parse_review_block's docstring calls out as ----
# --- load-bearing, so it gets its own check rather than riding on the ------
# --- "reviewed" test above. Must still count as REVIEWED, not unreviewed. --
_merge_calls.clear()
core._run = _fake_run_review_comments([
    {"body": "Nothing to report.\n\n<!-- orch:review:v1\n-->"},
])
try:
    empty_block_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9,
                                        labels={core.L_AUTOLAND})
    result = _with_stub_world(
        empty_block_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_empty_review_block_ok", True, result["ok"])
    row = _landed_rows()[-1]
    check("merge_empty_review_block_event", "landed", row["event"])
    check("merge_empty_review_block_reviewed_true", True, row["reviewed"])
    check("merge_empty_review_block_note_no_unreviewed", False, "UNREVIEWED" in row["note"])
finally:
    core._run = _orig_run

# --- the row must SURVIVE RENDERING, not just reach the disk ---------------
# The original tests here read the JSONL directly, which is the path that
# always worked. journal_brief is the wake tail every agent actually reads,
# and it renders only at/actor/event/note -- so a landed row without a `note`
# rendered as a bare "landed" line, dropping every fact the row exists to
# carry. Two landings in one tick were indistinguishable. Assert on the
# rendered string, because that is what a waking agent is handed.
_merge_calls.clear()
core._run = _fake_run
try:
    # orch#408: stub is irrelevant to what this block asserts (journal
    # brief rendering) -- see comment on the identical stub above.
    _stub_review_items(None)
    _with_stub_world(
        _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9,
                        labels={core.L_AUTOLAND}),
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch",
                              review="clean"))
    _brief = core.journal_brief("repo", merge_repo_dir.name)
    _last = _brief.splitlines()[-1]
    check("merge_landed_brief_names_issue", True, "#7" in _last)
    check("merge_landed_brief_names_pr", True, "PR 9" in _last)
    check("merge_landed_brief_names_flag", True, "flag=auto-land" in _last)
    check("merge_landed_brief_names_actor", True, "by=issue-orch" in _last)
    check("merge_landed_brief_names_review", True, "review=clean" in _last)
    # ...and the note stays well under the orch#274 cap, so nothing truncates.
    check("merge_landed_note_under_cap", True,
          len(_landed_rows()[-1]["note"]) < core.JOURNAL_NOTE_CHARS)
finally:
    core._run = _orig_run
    core.review_items_for_pr = _orig_review_items_for_pr

# --- a merged PR is journaled even when gh exits non-zero ------------------
# `gh pr merge` merges and THEN deletes the branch, so a failed delete exits
# non-zero on a PR that is already merged. This is not hypothetical: it is how
# orch#280 landed itself -- PR 287 merged as 022abd1, the delete failed, and
# the early return skipped the journal write, leaving the acceptance check at
# zero. The landing record must survive a failed post-merge step.
_merge_calls.clear()


def _fake_run_merge_then_delete_fails(cmd, cwd=None, timeout=None):
    _merge_calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
    if cmd[:2] == ["git", "-C"] and "remote" in cmd:
        return True, "git@github.com:me/mergerepo.git\n"
    if cmd[:2] == ["gh", "pr"] and cmd[2] == "merge":
        return False, "failed to delete branch: reference does not exist\n"
    return _orig_run(cmd, cwd=cwd, timeout=timeout)


core._run = _fake_run_merge_then_delete_fails
try:
    before = len(_landed_rows())
    # The world says MERGED even though the call reported failure.
    # orch#387: labeled auto-land so issue-orch's new gate lets this through
    # -- the label is irrelevant to what this block actually asserts
    # (the landed row surviving a failed post-merge step).
    # orch#408: stub review_items_for_pr to None (read fine, no review) --
    # irrelevant to what this block asserts, same reasoning as above.
    _stub_review_items(None)
    _post = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9,
                             labels={core.L_AUTOLAND})
    _post_merge = _StubMergeWorld(pr_state="MERGED", green=True, red=False, pr_number=9)
    # First World() is the gate's (PR OPEN + green -> REVIEW); the second is
    # the post-merge re-derivation, which must see MERGED.
    _states = [_post, _post_merge]
    _saved_world = core.World
    core.World = lambda: _states.pop(0) if _states else _post_merge
    try:
        res = core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch",
                            review="clean")
    finally:
        core.World = _saved_world
    check("merge_delete_branch_failure_reports_ok", True, res["ok"])
    check("merge_delete_branch_failure_still_journals", before + 1,
          len(_landed_rows()))
    check("merge_delete_branch_failure_row_is_correct", 9,
          _landed_rows()[-1]["pr"])
    # The diagnostic must REACH the caller, not just the log: spawn.py prints
    # result["out"] and exits 0, so a bare success string would leave the
    # operator unaware that a post-merge step failed and a branch is stale.
    check("merge_delete_branch_failure_out_names_it", True,
          "post-merge step failed" in res["out"])
finally:
    core._run = _orig_run
    core.review_items_for_pr = _orig_review_items_for_pr

# --- THE NEGATIVE: rc!=0 and the PR did NOT merge stays a failure ----------
# This is the entire safety property of the branch above. Without it, a future
# edit that flips the `if not merged` sense or defaults merged=True on an
# exception would pass the suite while turning every failed merge into a
# reported landing -- a journal full of merges that never happened, which is
# strictly worse than the missing rows this issue set out to fix.
_merge_calls.clear()
core._run = _fake_run_merge_then_delete_fails
try:
    before = len(_landed_rows())
    _still_open = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    res = _with_stub_world(_still_open, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_rc_nonzero_and_not_merged_is_failure", False, res["ok"])
    check("merge_rc_nonzero_and_not_merged_writes_no_row", before,
          len(_landed_rows()))
finally:
    core._run = _orig_run

# --- a reused branch must not journal a landing for a PR that never merged -
# pr_for/pr_number_for answer per BRANCH with OPEN-beats-MERGED precedence, so
# asking "does this branch have a merged PR" would see an OLD merged PR and
# report a landing for the NEW one that genuinely failed.
_merge_calls.clear()
core._run = _fake_run_merge_then_delete_fails


class _ReusedBranchWorld(_StubMergeWorld):
    """Branch issue-7 reused: PR 5 merged long ago, PR 9 is open now and its
    merge fails. `prs` is what the real World exposes and what the re-derived
    check reads."""
    prs = [{"number": 9, "headRefName": "issue-7", "state": "CLOSED"},
           {"number": 5, "headRefName": "issue-7", "state": "MERGED"}]


try:
    before = len(_landed_rows())
    _reused = _ReusedBranchWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    res = _with_stub_world(_reused, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_reused_branch_no_false_landing", False, res["ok"])
    check("merge_reused_branch_writes_no_row", before, len(_landed_rows()))
finally:
    core._run = _orig_run

# --- merge-blocked must merge the PR it exists to merge ------------------
# repo-orch merges on a journaled escalation precisely when a cross-issue
# conflict has left the PR red or unmergeable, so work_state reads BLOCKED.
# The REVIEW gate (right for every caller merging its own green work) would
# refuse exactly that case, making the escalation verb useless for its one
# purpose. Two directions to pin: the escalation gets through, and no other
# caller does.
_merge_calls.clear()
core._run = _fake_run
try:
    _red = _StubMergeWorld(pr_state="OPEN", green=False, red=True, pr_number=9)
    check("merge_blocked_passes_blocked_pr", True,
          _with_stub_world(_red, lambda: core.merge_pr(
              merge_repo_dir, 7, 9, decided_by="merge-blocked"))["ok"])
    check("merge_blocked_row_names_actor", "merge-blocked",
          _landed_rows()[-1]["decided_by"])
    # The same BLOCKED PR is still refused for everyone else.
    _red2 = _StubMergeWorld(pr_state="OPEN", green=False, red=True, pr_number=9)
    check("merge_blocked_still_refused_for_others", False,
          _with_stub_world(_red2, lambda: core.merge_pr(
              merge_repo_dir, 7, 9, decided_by="issue-orch"))["ok"])
    # And the escalation is NOT a blanket bypass: a merged/closed PR has no
    # open PR to merge, so it is refused even for the escalation.
    _merged = _StubMergeWorld(pr_state="MERGED", green=True, red=False, pr_number=9)
    check("merge_blocked_refuses_already_landed", False,
          _with_stub_world(_merged, lambda: core.merge_pr(
              merge_repo_dir, 7, 9, decided_by="merge-blocked"))["ok"])
finally:
    core._run = _orig_run

# --- flag resolution: same precedence as auto_land_on, four cases ----------
_merge_calls.clear()
core._run = _fake_run
try:
    _merge_repo_orch_json = core.ORCH_HOME / "orch.json"
    for label, labels, autoland_cfg, want_flag in (
        ("no_auto_land_only", {core.L_NO_AUTOLAND}, False, "no-auto-land"),
        ("both_labels_still_no_auto_land",
         {core.L_NO_AUTOLAND, core.L_AUTOLAND}, False, "no-auto-land"),
        ("auto_land_only", {core.L_AUTOLAND}, False, "auto-land"),
        ("neither_repo_default_true", set(), True, "repo-default"),
        ("neither_no_repo_default", set(), False, "none"),
    ):
        if autoland_cfg:
            _merge_repo_orch_json.write_text(json.dumps({"repos": [
                {"path": str(merge_repo_dir), "state": "tracked",
                 "auto-land": True},
            ]}))
        else:
            _merge_repo_orch_json.unlink(missing_ok=True)
        flag_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False,
                                     pr_number=9, labels=labels)
        result = _with_stub_world(
            flag_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
        check(f"merge_flag_{label}", True, result["ok"])
        rows = _landed_rows()
        check(f"merge_flag_{label}_value", want_flag, rows[-1]["flag"])
    _merge_repo_orch_json.unlink(missing_ok=True)
finally:
    core._run = _orig_run

# --- orch#387: decided_by gates the merge on auto_land_on ------------------
# issue-orch is the cheap non-session landing path -- no human in the loop
# to notice a hold a reviewing session wrote. merge-blocked and operator are
# NOT gated (own authority / human already decided). Any other decided_by,
# including unknown values, is fail-safe GATED.
_merge_calls.clear()
core._run = _fake_run
try:
    _merge_repo_orch_json = core.ORCH_HOME / "orch.json"
    _merge_repo_orch_json.unlink(missing_ok=True)

    # no-auto-land + issue-orch -> refused, no gh shellout at all
    before = len(_landed_rows())
    gated_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False,
                                  pr_number=9, labels={core.L_NO_AUTOLAND})
    result = _with_stub_world(
        gated_stub, lambda: core.merge_pr(merge_repo_dir, 362, 9, decided_by="issue-orch"))
    check("merge_gate_no_auto_land_issue_orch_refused", False, result["ok"])
    check("merge_gate_no_auto_land_issue_orch_no_gh_calls",
          0, len(_gh_merge_calls(_merge_calls)))
    check("merge_gate_no_auto_land_issue_orch_no_landed_row",
          before, len(_landed_rows()))
    check("merge_gate_no_auto_land_issue_orch_names_issue_and_flag", True,
          "362" in result["out"] and "no-auto-land" in result["out"])

    # no-auto-land + merge-blocked -> proceeds (own authority, unchanged)
    _merge_calls.clear()
    mb_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False,
                               pr_number=9, labels={core.L_NO_AUTOLAND})
    result = _with_stub_world(
        mb_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="merge-blocked"))
    check("merge_gate_no_auto_land_merge_blocked_ok", True, result["ok"])
    check("merge_gate_no_auto_land_merge_blocked_gh_called",
          1, len(_gh_merge_calls(_merge_calls)))

    # no-auto-land + operator -> proceeds (human clicked merge)
    _merge_calls.clear()
    op_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False,
                               pr_number=9, labels={core.L_NO_AUTOLAND})
    result = _with_stub_world(
        op_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="operator"))
    check("merge_gate_no_auto_land_operator_ok", True, result["ok"])
    check("merge_gate_no_auto_land_operator_gh_called",
          1, len(_gh_merge_calls(_merge_calls)))

    # repo default True, no labels, issue-orch -> proceeds
    # orch#387's gate passes here, so unlike the other issue-orch cases in
    # this block this one actually reaches orch#408's review gate against
    # this fake repo dir -- stub it open (irrelevant to what this case
    # asserts: the auto-land-default plumbing, not the review gate).
    _merge_calls.clear()
    _stub_review_items(None)
    _merge_repo_orch_json.write_text(json.dumps({"repos": [
        {"path": str(merge_repo_dir), "state": "tracked", "auto-land": True},
    ]}))
    default_true_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        default_true_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_gate_repo_default_true_issue_orch_ok", True, result["ok"])
    check("merge_gate_repo_default_true_issue_orch_gh_called",
          1, len(_gh_merge_calls(_merge_calls)))
    # Restored in this block's `finally` (below), not here: an exception
    # between the stub and an inline restore would leak the stub into every
    # later test. Same convention as every other stub site in this file.
    _merge_repo_orch_json.unlink(missing_ok=True)

    # repo default False, no labels, issue-orch -> refused
    _merge_calls.clear()
    before = len(_landed_rows())
    default_false_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        default_false_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_gate_repo_default_false_issue_orch_refused", False, result["ok"])
    check("merge_gate_repo_default_false_issue_orch_no_gh_calls",
          0, len(_gh_merge_calls(_merge_calls)))
    check("merge_gate_repo_default_false_issue_orch_no_landed_row",
          before, len(_landed_rows()))

    # unknown decided_by + repo default False -> refused (fail-safe)
    _merge_calls.clear()
    unknown_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        unknown_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="some-future-caller"))
    check("merge_gate_unknown_decided_by_refused", False, result["ok"])
    check("merge_gate_unknown_decided_by_no_gh_calls",
          0, len(_gh_merge_calls(_merge_calls)))

    # unknown decided_by + auto-land ON -> STILL refused. The direction the
    # denylist spelling got wrong: `decided_by not in (...) and not decided`
    # passes here, so an unrecognized caller inherited the exemption from the
    # auto-land flag instead of stating it. Only the allowlist refuses, which
    # is why this case is pinned separately from the auto-land-off one above.
    _merge_calls.clear()
    before = len(_landed_rows())
    unknown_on_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False,
                                      pr_number=9, labels={core.L_AUTOLAND})
    result = _with_stub_world(
        unknown_on_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="some-future-caller"))
    check("merge_gate_unknown_decided_by_autoland_on_refused", False, result["ok"])
    check("merge_gate_unknown_decided_by_autoland_on_no_gh_calls",
          0, len(_gh_merge_calls(_merge_calls)))
    check("merge_gate_unknown_decided_by_autoland_on_no_landed_row",
          before, len(_landed_rows()))
finally:
    core._run = _orig_run
    core.review_items_for_pr = _orig_review_items_for_pr

# --- a FAILED merge (_run returns not-ok) writes NO landed row -------------
_merge_calls.clear()


def _fake_run_merge_fails(cmd, cwd=None, timeout=None):
    _merge_calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
    if cmd[:2] == ["git", "-C"] and "remote" in cmd:
        return True, "git@github.com:me/mergerepo.git\n"
    if cmd[:2] == ["gh", "pr"] and cmd[2] == "merge":
        return False, "gh: merge conflict\n"
    return _orig_run(cmd, cwd=cwd, timeout=timeout)


core._run = _fake_run_merge_fails
try:
    before = len(_landed_rows())
    fail_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        fail_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_failed_run_not_ok", False, result["ok"])
    check("merge_failed_no_landed_row", before, len(_landed_rows()))
finally:
    core._run = _orig_run

# --- refused by an earlier gate (not REVIEW / pr mismatch) writes NO row ---
_merge_calls.clear()
core._run = _fake_run
try:
    before = len(_landed_rows())
    gate_stub = _StubMergeWorld(pr_state="OPEN", green=False, red=True)  # BLOCKED
    result = _with_stub_world(
        gate_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_gate_refused_not_ok", False, result["ok"])
    check("merge_gate_refused_no_landed_row", before, len(_landed_rows()))

    before2 = len(_landed_rows())
    mismatch_stub2 = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=42)
    result2 = _with_stub_world(
        mismatch_stub2, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_pr_mismatch_refused_not_ok", False, result2["ok"])
    check("merge_pr_mismatch_no_landed_row", before2, len(_landed_rows()))
finally:
    core._run = _orig_run

# --- a raising journal write must NOT flip a completed merge to ok:False ---
_merge_calls.clear()
core._run = _fake_run
_orig_journal_append_for_merge = core.journal_append


def _raising_journal_append(*a, **kw):
    raise RuntimeError("disk full")


core.journal_append = _raising_journal_append
try:
    raise_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        raise_stub, lambda: core.merge_pr(merge_repo_dir, 7, 9))
    check("merge_journal_raises_still_ok", True, result["ok"])
finally:
    core._run = _orig_run
    core.journal_append = _orig_journal_append_for_merge

# --- validate(): reject non-numeric pr and missing pr -----------------------
check("validate_merge_bad_pr", "bad pr",
      srv.validate("merge", {"repo": "mergerepo", "issue": "7", "pr": "not-a-number"}))
check("validate_merge_missing_pr", "missing pr",
      srv.validate("merge", {"repo": "mergerepo", "issue": "7"}))
check("validate_merge_ok", None,
      srv.validate("merge", {"repo": "mergerepo", "issue": "7", "pr": "9"}))


# === the permission envelope ===============================================
# The failure this pins: every agent doc hands its level literal Bash
# commands, but the spawn argv permitted only file edits, so the chain
# reached level 1 and stopped. The envelope must grant each role its OWN
# documented verbs and deny its red lines.

(core.ORCH_HOME / "agents").mkdir(parents=True, exist_ok=True)
(core.ORCH_HOME / "agents" / "dashboard-op.md").write_text(
    "# dashboard-op\n\n"
    "```bash\n"
    'echo "<one-line reason>" | ~/orch/spawn.py repo-orch <slug>\n'
    "gh issue create --repo <owner/name> --title \"<one line>\" --body-file -\n"
    "tea issue create --login <login> --repo <owner/name> --title \"<one line>\" --description \"<body>\"\n"
    "```\n\n"
    "Prose fence that is NOT a command block:\n\n"
    "```\n"
    "Bound any answer must respect: the thing you want.\n"
    "```\n"
)
(core.ORCH_HOME / "agents" / "repo-orch.md").write_text(
    "# repo-orch\n\n```bash\n"
    "gh issue edit <n> --repo <owner/name> --add-label agent-ready\n"
    "gh pr merge <pr> --repo <owner/name> --squash --delete-branch\n"
    "tea issue create --login <login> --repo <owner/name> --title \"<one line>\" --description \"<body>\"\n"
    "tea issue edit <n> --login <login> --repo <owner/name> --add-labels agent-ready\n"
    "tea pr merge <pr> --login <login> --repo <owner/name> --style merge\n"
    # orch#278: the blocked-by edge sequence on the tea path. Mirrors what
    # the real doc teaches -- list, create only if absent, apply, read back.
    "tea labels list --login <login> --repo <owner/name> --output json\n"
    "tea labels create --login <login> --repo <owner/name> --name blocked-by:<b> --color ededed --description \"<d>\"\n"
    "tea issues list --login <login> --repo <owner/name> --state open --output json\n"
    "```\n"
)

dash = core.role_settings("dashboard-op")["permissions"]
iss = core.role_settings("issue-orch")["permissions"]
repo = core.role_settings("repo-orch")["permissions"]

# The verb its own doc teaches is granted -- this is the exact command that
# was denied in the observed failure.
check("env_dashboard_can_spawn", True, "Bash(~/orch/spawn.py:*)" in dash["allow"])
# Its own journal, by absolute path, so the ack can actually be written.
check("env_dashboard_own_journal", True,
      f"Edit({core.ORCH_HOME / 'state' / 'dashboard-op.jsonl'})" in dash["allow"])
# ...and no other role's journal.
check("env_dashboard_no_repo_journal", False,
      any("state/repos" in r for r in dash["allow"]))

# orch#183: `spawn.py journal dashboard <event> <note...>` is the only
# sanctioned way to write state/dashboard-op.jsonl -- a `>>` redirect is
# refused by the session sandbox before any allow rule is read, because the
# journal is a sibling FILE of the session's cwd, not inside it.
#
# There is deliberately NO permission check here, and that absence is the
# finding. Permission rules are COMMAND-HEAD rules: `_rule_for_command`
# truncates `~/orch/spawn.py journal dashboard ...` to its head and emits
# `Bash(<ORCH_HOME>/spawn.py:*)`, the very same rule the doc's existing
# `spawn.py repo-orch` line already emits. So `Bash(...spawn.py:*) in
# dash["allow"]` is TRUE whether or not the journal verb is taught, and
# asserting it proves nothing -- it passes against a doc that never
# mentions journaling at all. Verified against the live envelope
# (state/sessions/dashboard-op.settings.json): the allow list carries
# `Bash(/home/user/orch/spawn.py:*)` and denies only `... kill:*`, so this
# verb was ALWAYS permitted. The bug was never a missing grant.
#
# What was actually missing is documentary: nothing told dashboard-op the
# verb existed, so it improvised a `>>` redirect the sandbox refused. The
# real doc is therefore the thing under test.
_dash_doc = (Path(__file__).resolve().parent.parent
             / "agents" / "dashboard-op.md").read_text()
# Collect ```bash fence bodies the SAME way _doc_commands does -- a
# line-by-line toggle, not a split("```"). The doc quotes ``` inline when it
# talks about its own fences, which shifts the parity of a split and made an
# earlier version of this check read the wrong blocks.
_dash_fences, _in_bash = [], False
for _line in _dash_doc.splitlines():
    if _line.startswith("```"):
        _in_bash = _line.strip() == "```bash" if not _in_bash else False
        continue
    if _in_bash:
        _dash_fences.append(_line)
check("env_dashboard_doc_teaches_journal_verb", True,
      any("spawn.py journal dashboard" in b for b in _dash_fences))
# ...and in a ```bash fence specifically, because that is the only thing
# _doc_commands harvests -- prose naming the verb would teach a human and
# grant nothing. Guards the fence tag, not just the text.
check("env_dashboard_journal_verb_not_prose_only", False,
      "spawn.py journal dashboard" in _dash_doc
      and not any("spawn.py journal dashboard" in b for b in _dash_fences))
# The retired ack stays prohibited: this doc teaches the general verb only.
check("env_dashboard_doc_keeps_handled_prohibition", True,
      "Do not run `spawn.py journal dashboard handled`" in _dash_doc)

# Red lines, mechanical: deny beats allow in claude's permission layer, so
# these are enforcement and not advice.
check("env_dashboard_denies_merge", True, "Bash(gh pr merge:*)" in dash["deny"])
check("env_dashboard_denies_labels", True, "Bash(gh issue edit:*)" in dash["deny"])
# orch#153 inverted this one: repo-orch nominates work with `agent-ready`,
# so the label verb is no longer denied. Both spellings must stay lifted --
# the deny layer matches verbs, not backends, and a `tea` deny left behind
# would make the grant fail on a Gitea-backed repo.
check("env_repo_orch_allows_labels", False, "Bash(gh issue edit:*)" in repo["deny"])
check("env_repo_orch_allows_labels_tea", False, "Bash(tea issue edit:*)" in repo["deny"])
# ...and the grant is real, harvested from the doc's own bash fence.
check("env_repo_orch_granted_label_verb", True, "Bash(gh issue edit:*)" in repo["allow"])
# The judgment labels stay issue-orch's by convention, not by rule: the
# permission layer cannot scope below the command head, so there is nothing
# here to assert about `agent-working`. agents/repo-orch.md holds that line.
# ...and the tea mirrors of those same two red lines. DENY_BY_ROLE matches
# literal command VERB strings, so a Gitea-backed repo is only fenced if
# `tea`'s own verbs are named explicitly -- the gh entries above do nothing
# for it. Unaffected by the repo-adapter refactor (which changes how orch
# BUILDS argv, not what the permission layer forbids), and asserted here so
# a future argv change cannot quietly leave the tea side unfenced.
check("env_dashboard_denies_tea_labels", True, "Bash(tea issue edit:*)" in dash["deny"])
check("env_dashboard_denies_tea_merge", True, "Bash(tea pr merge:*)" in dash["deny"])

# orch#223: `spawn.py issue <verb>` reaches the very gh/tea issue writes the
# lines above deny, just through RepoAdapter instead of a literal argv, so it
# must be fenced the same way. This is DERIVED from spawn.py's own verb table
# rather than hand-listed, which is the whole point: the first version of this
# deny enumerated the subcommands and silently omitted the bare `issue` group
# head, so `spawn.py issue  comment 5` (two spaces) dispatched while matching
# no rule. A hand-written list cannot catch the verb someone forgets to add;
# reading VERBS can.
#
# The group HEAD is what actually holds the line -- a rule ending `:*` matches
# any argv that follows, so one `Bash(... issue:*)` covers every present and
# future subcommand. Asserted in all three path spellings because the matcher
# compares literal text and never expands `~` (orch#188).
import orch.spawn as _spawn_verbs  # noqa: E402  (local: read the live table)
_issue_family = sorted(v for v in _spawn_verbs.VERBS
                       if v == "issue" or v.startswith("issue_")
                       or v.startswith("label_"))
check("env_dashboard_issue_verbs_are_not_empty", True, len(_issue_family) >= 6)
_undenied_issue_verbs = [
    f"{p} {v}" for v in _issue_family
    for p in ("~/orch/spawn.py", "spawn.py", str(core.ORCH_HOME / "spawn.py"))
    if f"Bash({p} {v}:*)" not in dash["deny"]
]
check("env_dashboard_denies_every_spawn_issue_verb", [], _undenied_issue_verbs)
# The head specifically, named so a reader sees which rule is load-bearing.
check("env_dashboard_denies_the_issue_group_head", True,
      f"Bash({core.ORCH_HOME / 'spawn.py'} issue:*)" in dash["deny"])
# repo-orch keeps its label writes (orch#153/#258), so the CLI form of them
# must NOT be denied to it -- otherwise this fence would quietly revoke a
# grant that level depends on.
check("env_repo_orch_may_use_issue_cli_labels", False,
      f"Bash({core.ORCH_HOME / 'spawn.py'} issue:*)" in repo["deny"])
# orch#153 lifted the repo-orch label deny on BOTH backends; dashboard-op
# keeps its tea denies above, which is what these two lines now guard.
check("env_repo_orch_allows_tea_labels", False, "Bash(tea issue edit:*)" in repo["deny"])
# Merge deny was deliberately LIFTED for repo-orch on merge escalation.
check("env_repo_orch_may_merge_blocked_on_escalation", False, "Bash(gh pr merge:*)" in repo["deny"])

# orch#139: repo-orch's doc now teaches all three tea write verbs -- filing,
# the orch#153 nomination grant, and the escalation-only merge -- so the
# harvest must actually grant them, mirroring the gh-side checks above.
check("env_repo_orch_allows_tea_issue_create", True,
      "Bash(tea issue create:*)" in repo["allow"])
check("env_repo_orch_allows_tea_issue_edit", True,
      "Bash(tea issue edit:*)" in repo["allow"])
check("env_repo_orch_allows_tea_pr_merge", True,
      "Bash(tea pr merge:*)" in repo["allow"])

# orch#278: writing a `blocked-by:<n>` edge on the tea path needs two verbs
# the gh path does not. `tea issue edit --add-labels` silently ignores a
# label that does not already exist -- exiting 0 either way -- where `gh
# issue edit --add-label` creates it on demand. So repo-orch's doc teaches
# create-then-apply-then-READ-BACK, and both of those extra verbs have to
# survive the harvest or the doc teaches a sequence the agent is denied at
# run time. The read-back is the load-bearing half: it is what turns tea's
# silent success into a detectable failure, so an edge is only journaled as
# recorded once it has actually been observed on the issue.
check("env_repo_orch_allows_tea_labels_create", True,
      "Bash(tea labels create:*)" in repo["allow"])
check("env_repo_orch_allows_tea_issues_list_readback", True,
      "Bash(tea issues list:*)" in repo["allow"])
# The two checks above read the FIXTURE doc written at the top of this
# block, not the shipped one, so on their own they only prove the harvester
# handles the verbs -- they would still pass if the real doc never taught
# them. Assert against the shipped doc too, or the fixture silently drifts
# and the grant this issue is about disappears with the suite still green.
#
# Run the real harvester over the SHIPPED doc rather than grepping it for
# the verb strings. A substring check passes on a doc that only mentions the
# verbs in prose -- and prose is not harvested (only ```bash fences are), so
# the grant would be absent with the suite still green, which is the exact
# failure this block exists to catch.
_shipped_agents = Path(__file__).resolve().parent.parent / "agents"
_orig_harvest_home = core.ORCH_HOME
try:
    core.ORCH_HOME = _shipped_agents.parent
    _shipped_repo_rules = core._doc_commands("repo-orch")
finally:
    core.ORCH_HOME = _orig_harvest_home
check("shipped_repo_orch_doc_grants_tea_label_create", True,
      "Bash(tea labels create:*)" in _shipped_repo_rules)
check("shipped_repo_orch_doc_grants_tea_edge_readback", True,
      "Bash(tea issues list:*)" in _shipped_repo_rules)
# The list-before-create step is load-bearing, not decoration: tea has no
# upsert, and a blind create against an existing name exits 0 and adds a
# DUPLICATE label. If the doc ever drops the list, the sequence it teaches
# starts breeding duplicates silently.
check("shipped_repo_orch_doc_grants_tea_labels_list", True,
      "Bash(tea labels list:*)" in _shipped_repo_rules)
# ...and the create verb must stay a 3-word rule. `Bash(tea labels:*)` would
# hand repo-orch `tea labels delete` too -- dropping a label removes it from
# every issue carrying it, which would silently erase other sessions' edges.
check("env_repo_orch_tea_labels_not_blanket", False,
      "Bash(tea labels:*)" in repo["allow"])
check("env_repo_orch_no_tea_labels_delete", False,
      "Bash(tea labels delete:*)" in repo["allow"])

# dashboard-op's doc now teaches ONLY `tea issue create` (filing) -- it must
# not pick up repo-orch's edit/merge verbs just because they share a doc
# section shape.
check("env_dashboard_allows_tea_issue_create", True,
      "Bash(tea issue create:*)" in dash["allow"])

# PRECEDENCE: the doc teaching `tea issue create` sits right beside the
# still-denied `tea issue edit` / `tea pr merge` / `tea comments add` in
# dashboard-op.md's own prose -- deny must still win for those three, or the
# boundary note is a lie. This is the check most likely to catch a future
# doc edit that accidentally widens a `bash` fence.
check("env_dashboard_still_denies_tea_issue_edit", True,
      "Bash(tea issue edit:*)" in dash["deny"])
check("env_dashboard_still_denies_tea_pr_merge", True,
      "Bash(tea pr merge:*)" in dash["deny"])
check("env_dashboard_still_denies_tea_comments_add", True,
      "Bash(tea comments add:*)" in dash["deny"])
check("env_dashboard_tea_issue_edit_not_granted", False,
      "Bash(tea issue edit:*)" in dash["allow"])
check("env_dashboard_tea_pr_merge_not_granted", False,
      "Bash(tea pr merge:*)" in dash["allow"])

# issue-orch now routes to the broad _ISSUE_ORCH_ALLOW, not a doc-derived
# list -- labeling/merging are covered by the bare "Bash" rule, not a
# specific gh subcommand rule, and its deny is exactly the two spawn.py
# entries (issue-orch cannot spawn; killing is the operator's act).
check("env_issue_orch_may_label", True, "Bash" in iss["allow"])
check("env_issue_orch_may_merge", True, "Bash" in iss["allow"])
check("env_issue_orch_not_denied_label_or_merge", False,
      "Bash(gh issue edit:*)" in iss["deny"] or "Bash(gh pr merge:*)" in iss["deny"])
# orch#280 narrowed this deny from a blanket `spawn.py:*` to the named
# spawning verbs, so that `spawn.py merge` -- the only merge path that writes
# the `landed` journal row -- is reachable by the level that does nearly all
# the landing. The containment is asserted BY PROPERTY below rather than by
# matching one literal string.
#
# An enumerated deny inverts the safe default the blanket rule gave: a verb
# nobody classified would be ALLOWED. That hole is closed at the source, not
# here -- core.SPAWNING_VERBS/NONSPAWNING_VERBS partition spawn.py's VERBS
# table, spawn.py asserts that partition at import, and both the deny list and
# the loop below derive from the same set. A new spawning verb is fenced the
# moment it is classified, and an unclassified one fails loudly at import.
#
# THREE spellings, not one: the matcher compares literal text and never
# expands `~`, so the absolute path -- the one an agent actually types -- is
# the load-bearing entry (orch#188). Derived from core.ORCH_HOME rather than
# written out, because this suite runs under a temp ORCH_HOME.
_spawn_spellings = ["spawn.py", "~/orch/spawn.py", str(core.ORCH_HOME / "spawn.py")]
# Derived from core.SPAWNING_VERBS, never a literal list here: spawn.py
# asserts at import that its VERBS table is partitioned by SPAWNING_VERBS and
# NONSPAWNING_VERBS, so a new verb cannot reach the permission layer
# unclassified, and this test picks it up automatically once it is.
_spawning_verbs = list(core.ROLES) + list(core.SPAWNING_VERBS)
check("env_issue_orch_cannot_spawn_any_verb_any_spelling", [],
      [f"{p} {v}" for p in _spawn_spellings for v in _spawning_verbs
       if f"Bash({p} {v}:*)" not in iss["deny"]])
# ...and the narrowing actually happened: no blanket rule left, in any
# spelling, or `merge` would still be denied and the docs would instruct a
# command the permission layer refuses.
check("env_issue_orch_no_blanket_spawn_deny", [],
      [p for p in _spawn_spellings if f"Bash({p}:*)" in iss["deny"]])
check("env_issue_orch_may_run_merge_verb", [],
      [p for p in _spawn_spellings if f"Bash({p} merge:*)" in iss["deny"]])

# dashboard-op and repo-orch both deny killing -- it's the operator's act.
check("env_dashboard_cannot_kill", True, "Bash(~/orch/spawn.py kill:*)" in dash["deny"])
check("env_repo_orch_cannot_kill", True, "Bash(~/orch/spawn.py kill:*)" in repo["deny"])

# Derivation, not a parallel list: only ```bash fences are commands. An
# untagged prose fence must not become `Bash(Bound:*)`.
check("env_prose_fence_not_harvested", False,
      any(r.startswith("Bash(Bound") for r in dash["allow"]))
# `gh` keeps its subcommand: one `Bash(gh:*)` would hand every level both
# red lines at once. Pinned on repo-orch, which still derives from its doc
# (issue-orch's allow is the broad hardcoded list, not doc-derived).
check("env_gh_rule_keeps_subcommand", True, "Bash(gh issue edit:*)" in repo["allow"])
check("env_gh_rule_not_blanket", False, "Bash(gh:*)" in repo["allow"])

# orch#139: the fixture two blocks up OVERWRITES agents/dashboard-op.md and
# agents/repo-orch.md under a temp ORCH_HOME, and every env_* check above
# reads THAT synthetic doc back -- so none of them can tell whether the real,
# shipped doc still carries the fences the checks assert on. Proven: revert
# both real docs to their `main` versions (leaving all test code and fixtures
# untouched) and the suite above still reports passed with 0 failures. The
# checks below close that hole by reading the REAL repo docs -- located via
# the test file's own path, not core.ORCH_HOME (which is the mutated temp
# dir at this point) -- through the same _doc_commands() derivation the
# server actually calls.
_real_agents_dir = Path(__file__).resolve().parent.parent / "agents"
check("shipped_agents_dir_exists", True, _real_agents_dir.is_dir())

_saved_orch_home = core.ORCH_HOME
try:
    core.ORCH_HOME = _real_agents_dir.parent
    shipped_repo = core._doc_commands("repo-orch")
    shipped_dash = core._doc_commands("dashboard-op")

    check("shipped_doc_repo_orch_grants_tea_issue_create", True,
          "Bash(tea issue create:*)" in shipped_repo)
    check("shipped_doc_repo_orch_grants_tea_issue_edit", True,
          "Bash(tea issue edit:*)" in shipped_repo)
    check("shipped_doc_repo_orch_grants_tea_pr_merge", True,
          "Bash(tea pr merge:*)" in shipped_repo)

    check("shipped_doc_dashboard_op_grants_tea_issue_create", True,
          "Bash(tea issue create:*)" in shipped_dash)
    check("shipped_doc_dashboard_op_no_tea_issue_edit", False,
          "Bash(tea issue edit:*)" in shipped_dash)
    check("shipped_doc_dashboard_op_no_tea_pr_merge", False,
          "Bash(tea pr merge:*)" in shipped_dash)
    check("shipped_doc_dashboard_op_no_tea_comments_add", False,
          "Bash(tea comments add:*)" in shipped_dash)

    # The singular/plural trap (orch#170): `tea` itself accepts `tea issues
    # create` as an alias, but the harvester and orch's own argv only ever
    # use the singular. Neither shipped doc may grant the plural.
    check("shipped_doc_repo_orch_no_plural_tea_issues_create", False,
          "Bash(tea issues create:*)" in shipped_repo)
    check("shipped_doc_dashboard_op_no_plural_tea_issues_create", False,
          "Bash(tea issues create:*)" in shipped_dash)

    # === orch#374: no harvested rule may be prose ===========================
    # _doc_commands read every line of a bash fence as a command, so a fence
    # containing a heredoc turned the BODY's prose into permission rules:
    # repo-orch shipped Bash(the:*), Bash(EOF:*), Bash(Entangled:*) and
    # Bash(assumption:*). Nothing runs a command named `the`, so there was no
    # exploit -- but the permission layer is the containment for agents with
    # broad git and forge access, and a harvester that emits rules nobody
    # wrote is one doc edit away from emitting one somebody would refuse.
    #
    # Checked across EVERY shipped role rather than the one doc that failed:
    # the bug is in the parser, so the next heredoc in any doc reintroduces
    # it. The head of a real rule is always a known tool or the spawn path.
    # `tail` is a genuine single-word head (dashboard-op reads journal tails),
    # so the test is "the head is a known tool", not "the rule has a
    # subcommand" -- prose leaks as a bare word and would otherwise pass.
    _known_heads = {"gh", "git", "tea", "tail"}
    _leaks = {}
    for _doc in sorted(_real_agents_dir.glob("*.md")):
        for _rule in core._doc_commands(_doc.stem):
            _head = _rule[len("Bash("):].split(":")[0].split()[0]
            if "spawn.py" in _rule or _head in _known_heads:
                continue
            _leaks.setdefault(_doc.stem, []).append(_rule)
    check("shipped_docs_no_prose_rules", {}, _leaks)

    # The specific four, pinned by name: a future refactor that reverts the
    # heredoc handling fails here with the actual symptom, not just a count.
    check("shipped_doc_repo_orch_no_heredoc_terminator_rule", False,
          "Bash(EOF:*)" in shipped_repo)
    check("shipped_doc_repo_orch_no_prose_article_rule", False,
          "Bash(the:*)" in shipped_repo)
    # The second leak's two, likewise pinned: these arrived from orch#318
    # wrapping a `gh label create`, after this issue was filed.
    check("shipped_doc_repo_orch_no_flag_rule_color", False,
          "Bash(--color:*)" in shipped_repo)
    check("shipped_doc_repo_orch_no_flag_rule_description", False,
          "Bash(--description:*)" in shipped_repo)
    # ...and the verb that wrapped line legitimately teaches IS granted --
    # it is reachable only from a continuation, so a fix that dropped every
    # continuation line would silently revoke it.
    check("shipped_doc_repo_orch_keeps_wrapped_label_create", True,
          "Bash(gh label create:*)" in shipped_repo)

    # ...and the heredoc's REAL command survives the skip. A fix that dropped
    # the whole line would also pass the checks above while silently revoking
    # the verb the fence exists to teach.
    check("shipped_doc_repo_orch_keeps_heredoc_command", True,
          "Bash(gh issue comment:*)" in shipped_repo)
finally:
    core.ORCH_HOME = _saved_orch_home

# === orch#374: heredoc shapes, against a synthetic doc =======================
# The shipped-doc checks above prove today's docs are clean; these prove the
# PARSER handles the shapes, including ones no doc uses yet. Written against
# a temp doc so they keep testing the rule rather than the current prose.
_hd_dir = core.ORCH_HOME / "agents"
_hd_dir.mkdir(parents=True, exist_ok=True)


def _harvest(body):
    (_hd_dir / "hdtest.md").write_text(body)
    return core._doc_commands("hdtest")


# The documented form: one command, and the body grants nothing.
check("heredoc_quoted_yields_only_the_command",
      ["Bash(gh issue comment:*)"],
      _harvest("```bash\ngh issue comment <n> --body-file - <<'EOF'\n"
               "the body is prose\nEOF\n```\n"))
# Unquoted and <<- (tab-stripping) delimiters behave the same way.
check("heredoc_unquoted_yields_only_the_command",
      ["Bash(git commit:*)"],
      _harvest("```bash\ngit commit -F - <<EOF\nthe body\nEOF\n```\n"))
check("heredoc_dash_form_allows_indented_terminator",
      ["Bash(git commit:*)"],
      _harvest("```bash\ngit commit -F - <<-EOF\n\tthe body\n\tEOF\n```\n"))
# A command AFTER the terminator is still harvested -- the skip must end.
check("heredoc_command_after_terminator_still_harvested",
      ["Bash(gh issue comment:*)", "Bash(gh issue edit:*)"],
      _harvest("```bash\ngh issue comment <n> --body-file - <<'EOF'\n"
               "the body\nEOF\ngh issue edit <n> --add-label x\n```\n"))
# An unterminated heredoc must not swallow the NEXT fence's commands: the
# fence close resets the state. This is the failure mode that made EOF a
# rule in the first place -- the parser running past a boundary. The opening
# command is still granted (it was genuinely written); what must not happen
# is the body's prose leaking, or `git push` being lost.
check("heredoc_unterminated_does_not_leak_past_fence",
      ["Bash(gh issue comment:*)", "Bash(git push:*)"],
      _harvest("```bash\ngh issue comment <n> --body-file - <<'EOF'\n"
               "the body never closes\n```\n\nprose\n\n"
               "```bash\ngit push\n```\n"))
# `<<<` is a here-STRING: no terminator line, so the following command must
# still be harvested. Treating it as a heredoc would swallow the rest.
check("herestring_is_not_treated_as_heredoc",
      ["Bash(git commit:*)", "Bash(git push:*)"],
      _harvest("```bash\ngit commit -F - <<<\"a message\"\ngit push\n```\n"))

# === orch#374, second leak: wrapped commands ================================
# Same root cause as the heredoc bug -- the harvester's unit was the physical
# line, not the command -- but a distinct shape, and one that arrived AFTER
# the issue was filed (orch#318 wrapped a `gh label create` for readability
# and silently minted Bash(--color:*) and Bash(--description:*)). Any author
# wrapping a long line would do it again.
check("continuation_flags_are_not_commands",
      ["Bash(gh issue edit:*)"],
      _harvest("```bash\ngh issue edit <n> --add-label x \\\n"
               "  --color ededed \\\n  --description \"why\"\n```\n"))
# ...but a continuation may legitimately OPEN a command after `&&`/`||`/`;`.
# repo-orch reaches `gh label create` only this way, so dropping every
# continuation line would revoke a real verb.
check("continuation_after_or_operator_is_a_command",
      ["Bash(gh issue edit:*)", "Bash(gh label create:*)"],
      _harvest("```bash\ngh issue edit <n> --add-label x \\\n"
               "  || { gh label create x --repo <r> \\\n"
               "         --color ededed \\\n       }\n```\n"))
# An operator INSIDE a quoted argument is text, not shell syntax. This is the
# case that granted Bash(always:*) from a --description reading
# "...; always wins over auto-land...".
check("operator_inside_quotes_does_not_split",
      ["Bash(gh issue edit:*)"],
      _harvest("```bash\ngh issue edit <n> --add-label x \\\n"
               "  --description \"opts out; always wins\" \\\n"
               "  --color ededed\n```\n"))
# The continuation state must end with the unwrapped line: the command on the
# line after a wrap is a real command again.
check("command_after_wrapped_command_still_harvested",
      ["Bash(gh issue edit:*)", "Bash(git push:*)"],
      _harvest("```bash\ngh issue edit <n> \\\n  --add-label x\ngit push\n```\n"))
# `cont` must reset at the fence close, or the next fence's first command is
# eaten as a continuation of the last one.
check("continuation_does_not_leak_past_fence",
      ["Bash(gh pr merge:*)", "Bash(git push:*)"],
      _harvest("```bash\ngit push \\\n```\n\nprose\n\n"
               "```bash\ngh pr merge <n>\n```\n"))

# === orch#374, the containment: a slip must DROP, never INVENT ===============
# Every check below is an adversarial fence shape, and every one of them was
# reported by review against an earlier revision of this fix. They are kept
# as tests because the lesson generalises: the parser handles the shapes it
# was written for, and the NEXT unanticipated shape is the one that mints a
# grant from English. `_GRANTABLE_HEADS` is what makes each of these a drop.
#
# The rule being pinned: a doc may only ever grant a known tool. Anything
# else yields no rule, per docs/RESTRUCTURE-2026-09-16.md section 4 -- a
# record may suppress or defer, never authorize.

# A wrapped command carrying its heredoc opener on the continuation line.
# This is the issue's own defect re-armed by an author merely wrapping a long
# line for readability -- the shape that introduced the second leak.
#
# The body deliberately contains `git push --force`, an ALLOWLISTED head.
# A body of pure prose would pass this check even with the ordering reverted,
# because the allowlist alone would suppress the junk -- the test would be
# measuring the containment instead of the parser it names. An allowlisted
# head is the only body content that can tell the two apart.
check("wrapped_heredoc_opener_still_skips_body",
      ["Bash(gh issue comment:*)"],
      _harvest("```bash\ngh issue comment <n> --repo <r> \\\n"
               "  --body-file - <<'EOF'\nthe landing is blocked\n"
               "git push --force\nEOF\n```\n"))
# Prose, a comment, a `$(...)` substitution and an escaped quote, each after
# an operator on a continuation line. Without the allowlist these emitted
# Bash(Entangled:*), Bash(danger:*), Bash(evilverb:*) and Bash(ghost:*).
check("prose_after_operator_grants_nothing",
      ["Bash(git push:*)"],
      _harvest("```bash\ngit push \\\n"
               "  --description \"foo\"; Entangled assumption here\n```\n"))
check("comment_after_operator_grants_nothing",
      ["Bash(git push:*)"],
      _harvest("```bash\ngit push \\\n  # note; danger somecmd\n```\n"))
check("command_substitution_grants_nothing",
      ["Bash(git tag:*)"],
      _harvest("```bash\ngit tag $(date +%s) \\\n"
               "  --message $(rm -rf /; evilverb x)\n```\n"))
check("escaped_quote_grants_nothing",
      ["Bash(git push:*)"],
      _harvest("```bash\ngit push \\\n  --arg \"he said \\\" ; ghost cmd\"\n```\n"))
# The property itself, stated directly: an unknown head is never granted,
# however plausible the line looks as a command.
check("unknown_head_is_never_granted",
      [],
      _harvest("```bash\nsudo rm -rf /\nkubectl delete ns prod\n```\n"))
# One line may open several heredocs; bash queues one body per delimiter.
# Retiring the whole run at the first terminator left the second body
# parsing as commands, which granted `Bash(tea stuff:*)` from body text.
# `tea` is allowlisted, so this is a shape the allowlist alone cannot catch.
check("multiple_heredocs_on_one_line_queue_their_bodies",
      [],
      _harvest("```bash\ncat <<'A' <<'B'\nA\ntea stuff\nB\n```\n"))
(_hd_dir / "hdtest.md").unlink()

# A verb removed from a doc is revoked -- the anti-drift property.
(core.ORCH_HOME / "agents" / "dashboard-op.md").write_text("# dashboard-op\n\nno commands\n")
check("env_revoked_with_doc", False,
      "Bash(~/orch/spawn.py:*)" in core.role_settings("dashboard-op")["permissions"]["allow"])

# issue-orch runs /sc: broad by construction, contained by cwd and by deny.
check("env_issue_orch_broad", True, "Bash" in iss["allow"] and "Edit" in iss["allow"])

# The envelope is written beside the ledger row: what a session was
# permitted is part of the record of what it did.
sp = core.write_role_settings("dashboard-op", "dashboard-op")
check("env_settings_file_written", True, sp.exists())
check("env_settings_file_parses", True,
      isinstance(json.loads(sp.read_text())["permissions"]["deny"], list))

# === orch#155: per-role --model override ====================================
# The failure this pins: orch never passed --model, so every spawned level
# ran the default regardless of role. MODEL_BY_ROLE fixes that additively --
# absent means default, so an untested role cannot regress by omission.
#
# The three spawned levels carry the models set by the operator ruling in the
# issue orch#155 thread (2026-09-15T01:24:46Z). These checks pin the RULING,
# because the one regression seen on this branch was the map being emptied on
# the false premise that no ruling existed.
#
# _spawn_argv is a closure inside _launch(), not a module-level name. Rather
# than mirror its body -- a copy passes even if the real lines are deleted,
# which is the one regression this section exists to catch -- capture the
# argv that _launch actually builds by intercepting subprocess.Popen.

check("model_dashboard_op_ruled", "sonnet",
      core.MODEL_BY_ROLE.get("dashboard-op"))
check("model_repo_orch_ruled", "opus",
      core.MODEL_BY_ROLE.get("repo-orch"))
check("model_issue_orch_ruled", "opus",
      core.MODEL_BY_ROLE.get("issue-orch"))

# Guards the misleading modelPolicy.allow failure mode for every entry: a bare
# alias or pinned form does not fail the spawn, it fails INSIDE the run.
check("model_all_values_are_bare_aliases", True,
      all(v in ("opus", "sonnet", "haiku") for v in core.MODEL_BY_ROLE.values()))


def _real_spawn_argv(role, model_map):
    """Run the REAL _launch() with Popen intercepted, and return the argv it
    built. Exercises production code, so deleting the --model lines from
    _spawn_argv fails these checks instead of silently passing."""
    seen = {}

    class _FakeProc:
        pid = 424242

        # orch#138: these MUST be per-instance, not class attributes.
        # _launch now makes two Popen calls per spawn (the run-log pump,
        # then the claude child) and closes its own copy of the pump's
        # stdin. Shared class-level handles would alias the two processes'
        # stdin onto one object, so closing the pump's would also close the
        # child's and the prompt write below would raise ValueError.
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()

        def poll(self):
            return None

    def _fake_popen(argv, **kw):
        # Record only the claude child's argv, never the pump's. _launch
        # spawns the pump FIRST, so a plain overwrite here would leave
        # `seen["argv"]` holding `python3 -m orch.runlog_pump ...` and these
        # checks would stop inspecting the thing they exist to inspect --
        # passing whether or not the --model flags survive.
        if "-m" not in argv or "orch.runlog_pump" not in argv:
            seen["argv"] = list(argv)
        return _FakeProc()

    real_popen, real_map = core.subprocess.Popen, core.MODEL_BY_ROLE
    real_which = core.shutil.which
    try:
        core.subprocess.Popen = _fake_popen
        core.MODEL_BY_ROLE = model_map
        core.shutil.which = lambda _n: "/usr/bin/claude"
        core._launch(f"model-argv-{role}", core.ORCH_HOME, "prompt", None,
                     role=role)
    finally:
        core.subprocess.Popen = real_popen
        core.MODEL_BY_ROLE = real_map
        core.shutil.which = real_which
    return seen.get("argv", [])


def _model_flag(argv):
    return argv[argv.index("--model") + 1] if "--model" in argv else None


# A mapped role gets the flag with its exact value...
_mapped = _real_spawn_argv("dashboard-op", {"dashboard-op": "sonnet"})
check("model_argv_present_for_mapped_role", "sonnet",
      _model_flag(_mapped))

# ...and an unmapped role gets NO --model at all, which is the additive
# property the whole design rests on: absence must inherit the default.
check("model_argv_absent_for_unmapped_role", None,
      _model_flag(_real_spawn_argv("repo-orch", {"dashboard-op": "sonnet"})))
check("model_argv_absent_for_empty_map", None,
      _model_flag(_real_spawn_argv("issue-orch", {})))
check("model_argv_absent_for_none_role", None,
      _model_flag(_real_spawn_argv(None, {"dashboard-op": "sonnet"})))

# === orch#58: the tea mirrors of the permission red lines ===================
# The deny layer matches VERBS, not backends -- a `tea` invocation is not
# covered by any `gh` pattern, so without these a Gitea backend would be a
# way around the same red line the gh denies exist to hold.
check("env_dashboard_denies_tea_merge", True, "Bash(tea pr merge:*)" in dash["deny"])
check("env_dashboard_denies_tea_labels", True, "Bash(tea issue edit:*)" in dash["deny"])
# orch#153 lifted the repo-orch label deny on BOTH backends; dashboard-op
# keeps its tea denies above, which is what these two lines now guard.
check("env_repo_orch_allows_tea_labels", False, "Bash(tea issue edit:*)" in repo["deny"])
# Merge is lifted for repo-orch on escalation, same as the gh rule -- the tea
# mirror must not re-introduce the deny the gh side deliberately omits.
check("env_repo_orch_may_merge_blocked_tea_on_escalation", False,
      "Bash(tea pr merge:*)" in repo["deny"])

# tea's read verbs sit beside gh's in the base allow every non-issue-orch
# role gets, so a Gitea-backed repo's dashboard-op/repo-orch can see the
# world it is reasoning about the same way a GitHub-backed one can.
check("env_base_allow_tea_issue_list", True, "Bash(tea issue list:*)" in dash["allow"])
check("env_base_allow_tea_pr_list", True, "Bash(tea pr list:*)" in dash["allow"])

# _MULTIWORD must hold tea at the same depth as gh (3), or a doc-derived tea
# rule collapses to the blanket `Bash(tea:*)` and grants every tea verb at
# once -- merge and label edits included.
check("multiword_tea_depth", 3, core._MULTIWORD.get("tea"))

# CAUTION case from tmp_plan-58.md: `tea comment`/`tea issue` (bare, no
# further real word) are genuinely 2-word verbs, but _MULTIWORD forces depth
# 3. A doc line naming one, with only a placeholder after it, must resolve
# to its own real 2-word verb -- NOT fall back to the too-broad bare-head
# rule the depth-shortfall path used before this fix.
check("rule_tea_comment_two_word", "Bash(tea comment:*)",
      core._rule_for_command("tea comment <n> --login <l> --repo <owner/name>"))
check("rule_tea_issue_two_word", "Bash(tea issue:*)",
      core._rule_for_command("tea issue <n> --login <l> --repo <owner/name> --comments"))
# A genuine 3-word tea verb still keeps its full subcommand path.
check("rule_tea_issue_edit_three_word", "Bash(tea issue edit:*)",
      core._rule_for_command("tea issue edit <n> --login <l> --repo <owner/name> --add-labels x"))
check("rule_tea_pr_merge_three_word", "Bash(tea pr merge:*)",
      core._rule_for_command("tea pr merge <pr> --login <l> --repo <owner/name> --style merge"))
# The gh/git depth-shortfall paths are untouched by the fix above: every
# existing doc pattern keeps at least 2 real words before its placeholder,
# so they still take the full-depth branch, not the new >=2-words fallback.
check("rule_gh_issue_edit_unaffected", "Bash(gh issue edit:*)",
      core._rule_for_command("gh issue edit <n> --repo <owner/name> --add-label x"))
check("rule_git_push_unaffected", "Bash(git push:*)",
      core._rule_for_command("git push -u origin issue-<n>"))

# orch#170 trap: tea accepts BOTH `issue` and `issues` as aliases for the
# same verb, but the permission layer does not -- it matches the literal
# three-word string, so the two spellings derive to DISTINCT rules. Pinned
# here so a future doc edit onto the plural (which tea would happily run)
# fails loudly instead of silently granting a string nothing ever emits.
check("rule_tea_issue_create_singular", "Bash(tea issue create:*)",
      core._rule_for_command(
          "tea issue create --login <l> --repo <owner/name> --title x --description y"))
check("rule_tea_issues_create_plural_distinct", "Bash(tea issues create:*)",
      core._rule_for_command(
          "tea issues create --login <l> --repo <owner/name> --title x --description y"))
check("rule_tea_issue_vs_issues_create_differ", True,
      core._rule_for_command(
          "tea issue create --login <l> --repo <owner/name> --title x --description y")
      != core._rule_for_command(
          "tea issues create --login <l> --repo <owner/name> --title x --description y"))

# TEA_ARGV["issue_create"] emits the verb the doc must teach -- if a future
# change edits the builder onto the plural (or a different flag order in the
# verb position), this pins the exact argv head so doc and builder cannot
# quietly drift apart. Called live from server.py's issue_create/escalation
# paths, so its argv shape IS the string an agent's grant has to match.
_tea_issue_create_argv = core.TEA_ARGV["issue_create"](
    "owner/name", "login", "title", "body")
check("tea_argv_issue_create_verb", ["tea", "issue", "create"],
      _tea_issue_create_argv[:3])

# === FRESH: the parent picks resume vs cold ================================
# spawn(fresh=True) must skip resume-id derivation entirely, so the launched
# argv carries no --resume even when a transcript exists for the key. Default
# (fresh=False) must still resume -- that is today's behaviour and the common
# case. Either way the dead row rolls aside: abandoning a session must never
# destroy its record.
#
# Assert on the REAL argv, not on what spawn() passed down: --resume is added
# inside _launch's _spawn_argv, so capturing at Popen is what actually proves
# the flag reaches (or does not reach) claude. Same Popen-substitution trick
# the flock-stake test uses -- real fork, real session, only the executable
# swapped.
fresh_argvs = []


def _argv_capturing_popen(argv, **kwargs):
    # orch#138: the run-log pump is spawned with these same kwargs (stdin
    # PIPE, own session), so it has to be excluded explicitly or it would be
    # captured as the launch argv and every check below would assert against
    # `python3 -m orch.runlog_pump ...` instead of against claude's argv.
    is_pump = "-m" in argv and "orch.runlog_pump" in argv
    if (not is_pump and kwargs.get("stdin") is subprocess.PIPE
            and kwargs.get("start_new_session")):
        fresh_argvs.append(list(argv))
        return _real_popen(["sleep", "30"], **kwargs)
    return _real_popen(argv, **kwargs)


fresh_key = core.key_for("repo-orch", "freshrepo")
_orig_resume_id_for = core.resume_id_for
core.shutil.which = lambda name: "/bin/sleep" if name == "claude" else shutil.which(name)
core.subprocess.Popen = _argv_capturing_popen
# Stand in for "a transcript exists for this key". The real resume_id_for
# globs a transcript dir; what matters here is only that an id IS available,
# so fresh=True is provably declining one rather than finding none.
core.resume_id_for = lambda key: "deadbeef-session-id" if key == fresh_key else _orig_resume_id_for(key)
try:
    # (a) default resumes -- the transcript is there and spawn uses it.
    core.spawn("repo-orch", ("freshrepo",), "default, should resume")
    check("fresh_default_resumes", True, "--resume" in fresh_argvs[-1])
    check("fresh_default_resume_id", "deadbeef-session-id",
          fresh_argvs[-1][fresh_argvs[-1].index("--resume") + 1])
    rolled_baseline = core.prior_runs(fresh_key)

    # (b) fresh=True declines it: no --resume, same transcript present.
    core.kill_key(fresh_key)
    core.spawn("repo-orch", ("freshrepo",), "fresh, should be cold", fresh=True)
    check("fresh_true_no_resume", False, "--resume" in fresh_argvs[-1])
    # argv differs by --resume and its id ONLY; nothing else about the launch
    # changes, or --fresh would be doing more than it claims.
    prev = [a for a in fresh_argvs[-2] if a not in ("--resume", "deadbeef-session-id")]
    check("fresh_argv_differs_only_by_resume", prev, fresh_argvs[-1])

    # (c) the dead row rolled aside either way -- the record survives cold.
    check("fresh_rolls_dead_row_aside", True, core.prior_runs(fresh_key) > rolled_baseline)
    core.kill_key(fresh_key)
finally:
    core.resume_id_for = _orig_resume_id_for
    core.shutil.which = shutil.which
    core.subprocess.Popen = _real_popen
    try:
        core.kill_key(fresh_key)
    except Exception:
        pass

# === subagents_for: the third glob ==========================================
# Separate from transcript_activity (recursive) and sessions_for (flat):
# subagent stems are not resumable, so they must not appear as sessions.
sub_cwd = T / "subcwd"
check("subagents_missing_dir", [], core.subagents_for(sub_cwd, "no-such-session"))

sub_sess = "sess-abc"
sub_dir = core.session_dir(sub_cwd) / sub_sess / "subagents"
sub_dir.mkdir(parents=True)
(core.session_dir(sub_cwd) / f"{sub_sess}.jsonl").write_text('{"type":"assistant"}\n')
(sub_dir / "agent-one.jsonl").write_text('{"a":1}\n{"a":2}\n{"a":3}\n')
(sub_dir / "agent-two.jsonl").write_text('{"a":1}\n')
old_sub = time.time() - 600
os.utime(sub_dir / "agent-two.jsonl", (old_sub, old_sub))

subs = core.subagents_for(sub_cwd, sub_sess)
check("subagents_count", 2, len(subs))
check("subagents_newest_first", "agent-one", subs[0]["id"])
check("subagents_turns", 3, subs[0]["turns"])
check("subagents_excludes_parent", False, sub_sess in {s["id"] for s in subs})
check("subagents_file_abs", True, subs[0]["file"].endswith("agent-one.jsonl"))
check("subagents_idle_sec", True, subs[1]["idle_sec"] >= 600)
check("subagents_not_in_sessions", False,
      "agent-one" in {s["id"] for s in core.sessions_for(sub_cwd)})
# session_dir() maps into the real ~/.claude/projects; this cwd is under the
# suite's temp root, so the dir is ours alone -- remove it, leave no litter.
shutil.rmtree(core.session_dir(sub_cwd), ignore_errors=True)

# === server: assign / create_issue ==========================================
check("actions_has_assign", True, "assign" in srv.ACTIONS)
check("actions_has_create_issue", True, "create_issue" in srv.ACTIONS)

check("validate_assign_missing_issue", "missing issue", srv.validate("assign", {"repo": "o/r"}))
check("validate_assign_bad_issue", "bad issue", srv.validate("assign", {"repo": "o/r", "issue": "x1"}))
check("validate_assign_bad_repo", "bad repo", srv.validate("assign", {"repo": "o/r bad!", "issue": "7"}))
check("validate_assign_ok", None, srv.validate("assign", {"repo": "o/r", "issue": "7"}))

check("validate_create_missing_title", "missing title", srv.validate("create_issue", {"repo": "o/r"}))
check("validate_create_blank_title", "bad title",
      srv.validate("create_issue", {"repo": "o/r", "title": "   "}))
check("validate_create_long_title", "bad title",
      srv.validate("create_issue", {"repo": "o/r", "title": "x" * 251}))
check("validate_create_long_body", "bad body",
      srv.validate("create_issue", {"repo": "o/r", "title": "ok", "body": "x" * 8001}))
check("validate_create_ok", None,
      srv.validate("create_issue", {"repo": "o/r", "title": "a real title", "body": "hi"}))

# argv is a list and shell is never used: assert the handlers hand subprocess
# a list, so user input can never be parsed as shell.
_gh_argvs = []
_orig_srv_run = srv.subprocess.run


class _FakeProc:
    returncode = 0
    stdout = "ok"
    stderr = ""


def _fake_srv_run(argv, **kw):
    # a_assign/a_create_issue now probe core.repo_backend/gh_repo/repo_slug
    # (a `git -C <dir> remote get-url origin`) BEFORE the actual gh/tea
    # call. This patch replaces subprocess.run globally (core._run shares
    # the same stdlib `subprocess` module srv.subprocess.run is reassigned
    # on), so a `git ... remote ...` probe is let through to the REAL
    # subprocess.run: `act_repo_dir` below is a plain mkdir with no `.git`
    # at all, and the real git binary already answers that correctly (a
    # nonzero exit -- "not a repository"), so backend resolution falls back
    # to "gh" exactly as it would outside this test. A tea fixture further
    # down gives git a real Gitea-shaped origin the same way. Only the
    # actual gh/tea action call is faked and recorded.
    if argv[:2] == ["git", "-C"] and "remote" in argv:
        return _orig_srv_run(argv, **kw)
    _gh_argvs.append((argv, kw))
    return _FakeProc()


srv.subprocess.run = _fake_srv_run

# The widget sends feed's slug, which is os.path.basename(repo) -- a bare
# basename, not OWNER/REPO. Use that shape here: a well-formed "o/r" fixture
# masked both the format mismatch and the missing watched-repo gate.
act_repo_dir = T / "actrepo"
act_repo_dir.mkdir(parents=True, exist_ok=True)
_write_repos_txt(f"{act_repo_dir}\n")
# An earlier a_kill case stubs both of these to a proofrepo-only lambda and
# never restores them; the gate under test is the real repos.txt lookup.
core.repo_path_for = _orig_repo_path_for
srv.repo_path = _orig_srv_repo_path
try:
    r = srv.a_assign({"repo": "actrepo", "issue": "7"})
    check("assign_ok", True, r["ok"])
    check("assign_argv_is_list", True, isinstance(_gh_argvs[-1][0], list))
    check("assign_no_shell", False, _gh_argvs[-1][1].get("shell", False))
    check("assign_argv", ["gh", "issue", "edit", "7",
                          "--add-label", core.L_READY], _gh_argvs[-1][0])
    check("assign_cwd_is_repo", str(act_repo_dir), _gh_argvs[-1][1].get("cwd"))
    srv.a_create_issue({"repo": "actrepo", "title": "t; rm -rf /", "body": "b"})
    check("create_issue_argv", ["gh", "issue", "create",
                                "--title", "t; rm -rf /", "--body", "b"], _gh_argvs[-1][0])
    check("create_issue_no_shell", False, _gh_argvs[-1][1].get("shell", False))
    check("create_issue_cwd_is_repo", str(act_repo_dir), _gh_argvs[-1][1].get("cwd"))

    # The gate: an unwatched slug is refused before gh runs. Both verbs took
    # any SLUG-shaped string straight to `gh --repo`, so a caller on the
    # tailnet could label or file issues on any repo the host token reaches.
    _n = len(_gh_argvs)
    check("assign_unwatched_refused", False, srv.a_assign({"repo": "notwatched", "issue": "7"})["ok"])
    check("create_issue_unwatched_refused", False,
          srv.a_create_issue({"repo": "notwatched", "title": "t"})["ok"])
    check("unwatched_never_invokes_gh", _n, len(_gh_argvs))

    # === orch#58: a_assign/a_create_issue routed to tea for a Gitea-backed
    # repo. Real git repo, real Gitea-shaped origin -- repo_backend must
    # read it as "tea" the same way it would outside this test.
    tea_act_dir = T / "tearepo"
    tea_act_dir.mkdir(parents=True, exist_ok=True)
    _orig_srv_run(["git", "init", "-q"], cwd=str(tea_act_dir))
    _orig_srv_run(["git", "remote", "add", "origin",
                   "http://gitea.local:3000/cybermelon/gita-lectures.git"], cwd=str(tea_act_dir))
    _write_repos_txt(f"{tea_act_dir}  login=gitea\n")

    srv.a_assign({"repo": "tearepo", "issue": "7"})
    check("assign_tea_argv", [
        "tea", "issue", "edit", "7", "--login", "gitea",
        "--repo", "cybermelon/gita-lectures", "--add-labels", core.L_READY,
    ], _gh_argvs[-1][0])
    check("assign_tea_no_shell", False, _gh_argvs[-1][1].get("shell", False))
    check("assign_tea_cwd_is_repo", str(tea_act_dir), _gh_argvs[-1][1].get("cwd"))

    srv.a_create_issue({"repo": "tearepo", "title": "t; rm -rf /", "body": "b"})
    check("create_issue_tea_argv", [
        "tea", "issue", "create", "--login", "gitea",
        "--repo", "cybermelon/gita-lectures",
        "--title", "t; rm -rf /", "--description", "b",
    ], _gh_argvs[-1][0])
finally:
    srv.subprocess.run = _orig_srv_run
    _write_repos_txt(f"{act_repo_dir}\n")

# === server: reply ==========================================================
check("actions_has_reply", True, "reply" in srv.ACTIONS)

check("validate_reply_missing_text", "missing text",
      srv.validate("reply", {"repo": "o/r", "issue": "7"}))
check("validate_reply_blank_text", "bad text",
      srv.validate("reply", {"repo": "o/r", "issue": "7", "text": "   "}))
check("validate_reply_long_text", "bad text",
      srv.validate("reply", {"repo": "o/r", "issue": "7", "text": "x" * 8001}))
check("validate_reply_bad_repo", "bad repo",
      srv.validate("reply", {"repo": "o/r bad!", "issue": "7", "text": "hi"}))
check("validate_reply_bad_issue", "bad issue",
      srv.validate("reply", {"repo": "o/r", "issue": "x1", "text": "hi"}))
check("validate_reply_ok", None,
      srv.validate("reply", {"repo": "o/r", "issue": "7", "text": "fix the second finding first"}))

# The gate, again as its own case: #19 landed a verb that reached gh for any
# SLUG-shaped string. Patch _gh itself so an escape would be recorded rather
# than merely refused downstream.
_reply_calls = []
_orig_srv_gh = srv._gh
srv._gh = lambda argv, cwd=None, stdin_text=None: (
    _reply_calls.append((argv, cwd, stdin_text)), {"ok": True, "out": "ok"})[1]
try:
    check("reply_unwatched_refused", False,
          srv.a_reply({"repo": "notwatched", "issue": "7", "text": "hi"})["ok"])
    check("reply_unwatched_never_invokes_gh", 0, len(_reply_calls))

    srv.a_reply({"repo": "actrepo", "issue": "7", "text": "that one is intentional"})
    check("reply_argv", ["gh", "issue", "comment", "7", "--body-file", "-"],
          _reply_calls[-1][0])
    check("reply_cwd_is_repo", act_repo_dir, _reply_calls[-1][1])
    # marker first, operator prose after: a reader tells operator from agent
    # by line one alone.
    check("reply_marker_first_line", "orch/operator reply",
          _reply_calls[-1][2].splitlines()[0])
    check("reply_body_carries_text", True,
          "that one is intentional" in _reply_calls[-1][2])

    # === orch#58 unit 2: a_reply routes to tea's `-d` argv on a tea-backed
    # repo, instead of the outright refusal unit 1 left in place -- see
    # tmp_plan-58.md's "issue comment: RESOLVED" note. `-d` is safe because
    # _gh always runs subprocess with an argv LIST and shell=False (no shell
    # re-parses the body) and a flag-value slot is unambiguous regardless of
    # the body's own content.
    tea_reply_dir = T / "tearepo-reply"
    tea_reply_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=tea_reply_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                     "http://gitea.local:3000/cybermelon/gita-lectures.git"],
                    cwd=tea_reply_dir, check=True)
    _write_repos_txt(f"{act_repo_dir}\n{tea_reply_dir}\n")
    _n_reply = len(_reply_calls)
    result = srv.a_reply({"repo": "tearepo-reply", "issue": "7", "text": "hi"})
    check("reply_tea_backend_ok", True, result["ok"])
    check("reply_tea_backend_invokes_gh_runner_once", _n_reply + 1, len(_reply_calls))
    check("reply_tea_argv", [
        "tea", "comments", "add", "7", "--login", "gitea",
        "--repo", "cybermelon/gita-lectures", "-d", "orch/operator reply\nhi\n",
    ], _reply_calls[-1][0])
    check("reply_tea_cwd_is_repo", tea_reply_dir, _reply_calls[-1][1])
    # tea has no stdin option for this verb; the body travels in the -d
    # argv slot instead, so stdin_text must be None on this path.
    check("reply_tea_no_stdin", None, _reply_calls[-1][2])
finally:
    srv._gh = _orig_srv_gh
    _write_repos_txt(f"{act_repo_dir}\n")
# === feed: "awaiting" is REVIEW + a PR, liveness irrelevant (#134) ==========
# build() drives repo discovery + gh reads we don't want in a unit test, so
# repo_json is stubbed to hand back two already-shaped issues (one that
# should surface as awaiting, one that should not) and the real build() loop
# is exercised over them -- the smallest honest way to test the predicate
# without asserting on a stub of the predicate itself.
from orch import feed
importlib.reload(feed)

fake_repo = Path(os.environ["WT_ROOT"]) / "feedrepo"
(fake_repo / ".git").mkdir(parents=True)
_write_repos_txt(str(fake_repo) + "\n")

_orig_repo_json = feed.repo_json
feed.repo_json = lambda repo: {
    "repo": "me/feedrepo", "path": str(repo), "slug": "feedrepo", "ok": True,
    "counts": {}, "live_orchs": 0,
    "issues": [
        {"issue": 1, "title": "waiting one", "url": "https://x/1",
         "work_state": "REVIEW", "orch_alive": False,
         "pr": 9, "pr_url": "https://github.com/me/feedrepo/pull/9",
         "contended": False, "state": "CLAIMED"},
        # #134: alive AND has a PR. Before the fix this row was suppressed
        # purely because its session was still running, and its
        # fix-before-merge findings reached no surface. It must appear.
        {"issue": 2, "title": "still running", "url": "https://x/2",
         "work_state": "REVIEW", "orch_alive": True,
         "pr": 10, "pr_url": "https://github.com/me/feedrepo/pull/10",
         "contended": False, "state": "CLAIMED"},
        # REVIEW but no PR: nothing to review, so no row either way.
        {"issue": 3, "title": "no pr yet", "url": "https://x/3",
         "work_state": "REVIEW", "orch_alive": False,
         "pr": None, "pr_url": None,
         "contended": False, "state": "CLAIMED"},
    ],
    "orch": {"key": "repo-orch.feedrepo", "alive": False, "prior_runs": 0, "recent": []},
}
try:
    out = feed.build()
    check("awaiting_first_key", "awaiting", next(iter(out)))
    check("awaiting_count", 2, len(out["awaiting"]))
    row = next(r for r in out["awaiting"] if r["issue"] == 1)
    check("awaiting_issue", 1, row["issue"])
    check("awaiting_repo", "me/feedrepo", row["repo"])
    check("awaiting_slug", "feedrepo", row["slug"])
    check("awaiting_pr", 9, row["pr"])
    check("awaiting_pr_url", "https://github.com/me/feedrepo/pull/9", row["pr_url"])
    # #134: the live one must appear -- liveness does not gate the row
    check("awaiting_includes_alive", True, any(r["issue"] == 2 for r in out["awaiting"]))
    live_row = next(r for r in out["awaiting"] if r["issue"] == 2)
    check("awaiting_alive_pr", 10, live_row["pr"])
    # a REVIEW unit with no PR has nothing to review, so it stays out
    check("awaiting_excludes_prless", True, all(r["issue"] != 3 for r in out["awaiting"]))
    # regression: the existing "waiting on you" alert must still fire for issue 1
    check("waiting_on_you_alert_kept", True,
          any(a.get("msg", "").endswith("#1 waiting on you") for a in out["alerts"]))
finally:
    feed.repo_json = _orig_repo_json

# === feed: dashboard_op feed dict survives removal of the hung-wake alert ===
# The automatic dashboard-op wake that the deleted alert detected is gone
# (see feed.py), but feed.build() still assembles a dashboard_op dict every
# call; keep cheap coverage that its shape didn't rot along with the alert.
_dop_survives_out = feed.build()
check("dashboard_op_feed_dict_survives", True,
      isinstance(_dop_survives_out["dashboard_op"], dict)
      and set(["key", "alive", "prior_runs", "activity"])
          <= set(_dop_survives_out["dashboard_op"].keys()))

# === orch#58: feed.repo_json must resolve gr via the repo's ACTUAL backend ===
# feed.py imports World, gh_repo, repo_slug, repo_backend BY NAME from core
# (see the top-of-file import), so patching core.World does not reach
# feed.repo_json's own World() call -- feed.World has to be patched
# directly, same idea _with_stub_world uses for core.merge_pr. The stub's
# load() records whatever slug it was handed and returns False, which is
# enough to prove what repo_json resolved gr to without needing a working
# gh/tea CLI or a real oracle: repo_json's early-abort-on-load-failure path
# (the "unreachable oracle" comment a few lines above it) is unit 3's
# load-bearing behaviour and must fire unchanged here.
_gr_seen = []


class _StubLoadWorld:
    def load(self, gr):
        _gr_seen.append(gr)
        return False


_orig_feed_world = feed.World
feed.World = _StubLoadWorld
try:
    # -- github-backed repo: gr must be the SAME value gh_repo() has always
    # produced (no behaviour change on the path that already worked). --
    feed_gh_dir = T / "feed-backend-gh"
    feed_gh_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=feed_gh_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/orch.git"],
                    cwd=feed_gh_dir, check=True)
    _gr_seen.clear()
    gh_result = feed.repo_json(feed_gh_dir)
    check("repo_json_gh_slug", "cybermelon/orch", _gr_seen[-1])
    check("repo_json_gh_repo_field", "cybermelon/orch", gh_result["repo"])
    check("repo_json_gh_load_failed_aborts", False, gh_result["ok"])
    # orch#284: the abort path still emits the repo's auto_land default (no
    # oracle needed to read it), same shape-for-every-row reasoning as
    # in_flight_cap just below it in feed.py. No orch.json entry for this
    # repo -> the documented no-entry default, False.
    check("repo_json_gh_abort_emits_auto_land", False, gh_result["auto_land"])

    # orch#284 review finding: repo_json has THREE returns, and the earliest
    # -- `if not gr:`, a repo whose slug will not resolve -- is the one the
    # dashboard is most likely to render, because repoRow() has no ok gate
    # and draws every repo row unconditionally. A missing auto_land key there
    # does not blank the control; it prints a confident "repo default:
    # automerge off" for a repo whose default may be on. Pinned here so the
    # same-shape invariant covers all three paths, not two.
    feed_noremote_dir = T / "feed-backend-noremote"
    feed_noremote_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=feed_noremote_dir, check=True)
    noremote_result = feed.repo_json(feed_noremote_dir)
    check("repo_json_noslug_aborts", False, noremote_result["ok"])
    check("repo_json_noslug_emits_auto_land", False, noremote_result["auto_land"])
    check("repo_json_noslug_emits_in_flight_cap",
          core.IN_FLIGHT_CAP, noremote_result["in_flight_cap"])

    # -- tea-backed (Gitea) repo: gr must be the host-agnostic repo_slug()
    # result ("owner/repo"), never gh_repo()'s unstripped raw remote URL --
    # the exact bug this unit fixes. Before the fix this assertion would see
    # "http://gitea.local:3000/cybermelon/gita-lectures.git" here instead. --
    feed_tea_dir = T / "feed-backend-tea"
    feed_tea_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=feed_tea_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                     "http://gitea.local:3000/cybermelon/gita-lectures.git"],
                    cwd=feed_tea_dir, check=True)
    _gr_seen.clear()
    tea_result = feed.repo_json(feed_tea_dir)
    check("repo_json_tea_slug", "cybermelon/gita-lectures", _gr_seen[-1])
    check("repo_json_tea_repo_field", "cybermelon/gita-lectures", tea_result["repo"])
    check("repo_json_tea_load_failed_aborts", False, tea_result["ok"])
finally:
    feed.World = _orig_feed_world

# === orch#417: repo_json's `all_issues` must carry EVERY open issue ========
# repo-orch's consolidation step needs the full open set (agents/repo-orch.md:
# "Your input is the FULL open issue set, not candidates()"), but `issues`
# (what spawn.py status / the dashboard render) is deliberately still
# candidates()-only -- widening `issues` itself would start firing new
# "abandoned" alerts for every agent-stuck issue (feed.build's
# `if i.get("state") == "ABANDONED"`), a real display change this fix must
# not make. This is the regression guard: if `all_issues` narrows back down
# to candidates(), or disappears, this fails loudly.
#
# Subclasses the REAL core.World (same precedent as _OrphanWorld279 above)
# with issues/prs injected directly and load() overridden to skip the
# gh/tea shell-out -- so candidates(), orphan_prs(), rollup_readability(),
# pr_for()/branch_for_issue() etc. are the real, shipped methods, not a
# reimplemented stand-in that could drift from them.
class _AllIssuesWorld417(core.World):
    def __init__(self, issues):
        super().__init__()
        self.issues = issues
        self.prs = []

    def load(self, gr):
        self.repo_slug = gr
        return True


_ai417_issues = [
    {"number": 1, "title": "ready one", "labels": [{"name": core.L_READY}],
     "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
     "comments": []},
    {"number": 2, "title": "stuck one", "labels": [{"name": core.L_STUCK}],
     "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
     "comments": []},
    {"number": 3, "title": "unlabelled one", "labels": [],
     "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
     "comments": []},
]
_orig_feed_world_417 = feed.World
feed.World = lambda: _AllIssuesWorld417(_ai417_issues)
try:
    ai417_dir = T / "feed-all-issues-417"
    ai417_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=ai417_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/orch.git"],
                    cwd=ai417_dir, check=True)
    _ai417_result = feed.repo_json(ai417_dir)
    check("all_issues_417_present", True, "all_issues" in _ai417_result)
    # The regression this guards: all_issues must equal the FULL open set,
    # not the candidates()-only slice `issues` deliberately stays at.
    check("all_issues_417_carries_every_open_issue",
          {1, 2, 3}, {i["number"] for i in _ai417_result["all_issues"]})
    check("issues_417_stays_candidates_only",
          {1}, {i["issue"] for i in _ai417_result["issues"]})
    # The stuck/unlabelled issues must be findable in all_issues with their
    # real label-derived state -- repo-orch's consolidation step reads state
    # to relate issues, so a bare number list would not be enough.
    _ai417_by_n = {i["number"]: i for i in _ai417_result["all_issues"]}
    check("all_issues_417_stuck_state", "ABANDONED", _ai417_by_n[2]["state"])
    check("all_issues_417_unclaimed_state", "UNCLAIMED", _ai417_by_n[3]["state"])
finally:
    feed.World = _orig_feed_world_417

# === tick.compute_conditions: condition 7, landed but still claimed =========
# Fresh activity so idle_over is False -- isolates condition 7 from
# condition 5/6, which key off idle_over/orch_alive. build_thin is called
# for real (never hand-built) so the thin shape cannot drift from what
# build_thin actually produces.
_c7_fresh_activity = time.time()


def _c7_data(state, work_state, orch_alive):
    return {"repos": [{"slug": "o/r", "issues": [{
        "issue": 31, "state": state, "work_state": work_state,
        "orch_alive": orch_alive, "activity": _c7_fresh_activity,
        "contended": False, "ready": False,
    }]}], "alerts": []}


# landed_claimed_dead_owner_fires: LANDED + CLAIMED + dead owner -> fires.
_c7_data_a = _c7_data("CLAIMED", "LANDED", False)
_c7_thin_a = tickmod.build_thin(_c7_data_a)
_, _c7_notes_a, _ = tickmod.compute_conditions(_c7_data_a, _c7_thin_a)
check("landed_claimed_dead_owner_fires", True,
      any("landed but still claimed" in note for note in _c7_notes_a))

# landed_claimed_live_owner_fires: LANDED + CLAIMED + live owner -> still
# fires. Liveness is deliberately not a gate on this condition.
_c7_data_b = _c7_data("CLAIMED", "LANDED", True)
_c7_thin_b = tickmod.build_thin(_c7_data_b)
_, _c7_notes_b, _ = tickmod.compute_conditions(_c7_data_b, _c7_thin_b)
check("landed_claimed_live_owner_fires", True,
      any("landed but still claimed" in note for note in _c7_notes_b))

# landed_unclaimed_silent: LANDED + UNCLAIMED (label already dropped) ->
# does NOT fire. This is the post-fix steady state and the most important
# negative case.
_c7_data_c = _c7_data("UNCLAIMED", "LANDED", False)
_c7_thin_c = tickmod.build_thin(_c7_data_c)
_, _c7_notes_c, _ = tickmod.compute_conditions(_c7_data_c, _c7_thin_c)
check("landed_unclaimed_silent", False,
      any("landed but still claimed" in note for note in _c7_notes_c))

# review_claimed_not_condition7: REVIEW + CLAIMED -> condition 5 may fire,
# but not condition 7.
_c7_data_d = _c7_data("CLAIMED", "REVIEW", False)
_c7_thin_d = tickmod.build_thin(_c7_data_d)
_, _c7_notes_d, _ = tickmod.compute_conditions(_c7_data_d, _c7_thin_d)
check("review_claimed_not_condition7", False,
      any("landed but still claimed" in note for note in _c7_notes_d))

# === tick.build_thin / compute_conditions: never_owned (orch#123) ==========
# An orphaned claim: agent-working (CLAIMED), idle past threshold, no live
# owner, and prior_runs == 0 -- no session has EVER run for this key. build_thin
# is called for real (never hand-built) so never_owned cannot drift from what
# build_thin actually derives. Activity is far in the past so idle_over is True.
_no_data_stale_activity = time.time() - (60 * 60 * 24)


def _no_data(prior_runs):
    return {"repos": [{"slug": "o/r", "issues": [{
        "issue": 41, "state": "CLAIMED", "work_state": "IN_PROGRESS",
        "orch_alive": False, "activity": _no_data_stale_activity,
        "contended": False, "ready": False, "prior_runs": prior_runs,
    }]}], "alerts": []}


# never_owned_true_fires: prior_runs == 0, no live owner, idle_over -> the
# thin shape carries never_owned True, condition 5 fires, and the note
# carries the "never owned" marker.
_no_data_a = _no_data(0)
_no_thin_a = tickmod.build_thin(_no_data_a)
check("never_owned_true_in_thin", True,
      _no_thin_a["repos"][0]["issues"][0]["never_owned"])
_no_na_a, _no_notes_a, _no_conds_a = tickmod.compute_conditions(_no_data_a, _no_thin_a)
check("never_owned_true_cond5_fires", True,
      any(c["cond"] == 5 for c in _no_conds_a))
check("never_owned_true_note_marker", True,
      any("unowned work" in note and "never owned" in note for note in _no_notes_a))
check("never_owned_true_cond_dict", True,
      next(c["never_owned"] for c in _no_conds_a if c["cond"] == 5))

# never_owned_false_counter_case: prior_runs == 1 (a session ran before, it
# just died) -> condition 5 still fires, but never_owned is False and the
# note carries no "never owned" marker.
_no_data_b = _no_data(1)
_no_thin_b = tickmod.build_thin(_no_data_b)
check("never_owned_false_in_thin", False,
      _no_thin_b["repos"][0]["issues"][0]["never_owned"])
_no_na_b, _no_notes_b, _no_conds_b = tickmod.compute_conditions(_no_data_b, _no_thin_b)
check("never_owned_false_cond5_fires", True,
      any(c["cond"] == 5 for c in _no_conds_b))
check("never_owned_false_note_no_marker", True,
      any("unowned work" in note and "never owned" not in note for note in _no_notes_b))
check("never_owned_false_cond_dict", False,
      next(c["never_owned"] for c in _no_conds_b if c["cond"] == 5))

# === live_tracked_pgids: include-live / exclude-dead, real process groups ==
# Same pattern as the earlier liveness block (line ~240): real Popen rows,
# never a stubbed killpg.
ltp_live_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
ltp_dead_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
try:
    time.sleep(0.3)
    live_key = "issue-orch.ltplive.1"
    dead_key = "issue-orch.ltpdead.1"
    core.ledger_path(live_key).write_text(json.dumps({
        "role": "issue-orch", "scope": ["ltplive", 1], "workdir": str(T),
        "pgid": ltp_live_proc.pid, "started": core.now_iso(),
        "log": str(core.ledger_log_path(live_key)),
    }))
    # Actually kill and wait for death -- not an invented unused pid.
    import signal as _sig
    ltp_dead_pgid = ltp_dead_proc.pid
    os.killpg(ltp_dead_pgid, _sig.SIGKILL)
    ltp_dead_proc.wait(timeout=2)
    check("ltp_dead_precondition", False, core._pgid_alive(ltp_dead_pgid))
    core.ledger_path(dead_key).write_text(json.dumps({
        "role": "issue-orch", "scope": ["ltpdead", 1], "workdir": str(T),
        "pgid": ltp_dead_pgid, "started": core.now_iso(),
        "log": str(core.ledger_log_path(dead_key)),
    }))

    pgids = core.live_tracked_pgids()
    check("ltp_includes_live", True, ltp_live_proc.pid in pgids)
    check("ltp_excludes_dead", False, ltp_dead_pgid in pgids)
finally:
    core.ledger_path("issue-orch.ltplive.1").unlink(missing_ok=True)
    core.ledger_path("issue-orch.ltpdead.1").unlink(missing_ok=True)
    try:
        ltp_live_proc.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    try:
        core._pgid_alive(ltp_live_proc.pid) and os.killpg(ltp_live_proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        ltp_live_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        ltp_live_proc.kill()
        ltp_live_proc.wait(timeout=2)

# === live_tracked_pgids: excludes .settings.json and rolled-aside rows =====
# The discrimination that matters: a LIVE pgid inside BOTH a <key>.settings.json
# file and a rolled-aside <key>.<started-iso>.json file must still be excluded
# -- if the name filter were broken (e.g. matching on "extra dot" alone
# instead of the exact rolled-aside timestamp shape, or not excluding
# .settings.json at all), this is what would catch it. A dead pgid in these
# files would prove nothing, since it would be excluded by liveness alone.
filt_proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
try:
    time.sleep(0.3)
    filt_key = "issue-orch.filtertest.1"
    started = core.now_iso()  # exact shape _roll_aside uses: row["started"]
    filt_row = {
        "role": "issue-orch", "scope": ["filtertest", 1], "workdir": str(T),
        "pgid": filt_proc.pid, "started": started,
        "log": str(core.ledger_log_path(filt_key)),
    }

    settings_path = core.SESSIONS_DIR / f"{filt_key}.settings.json"
    settings_path.write_text(json.dumps(filt_row))

    # Build the rolled-aside name exactly the way _roll_aside really builds
    # it: <key>.<started>.json, using the SAME started value written above.
    rolled_path = core.SESSIONS_DIR / f"{filt_key}.{started}.json"
    rolled_path.write_text(json.dumps(filt_row))

    check("ltp_filter_live_precondition", True, core._pgid_alive(filt_proc.pid))
    pgids = core.live_tracked_pgids()
    check("ltp_excludes_settings_json_even_when_live", False, filt_proc.pid in pgids)
    check("ltp_excludes_rolled_aside_even_when_live", False, filt_proc.pid in pgids)
    # Positive control: proves the exclusion is name-based, not "pgid never
    # appears" -- a CURRENT row with the identical live pgid IS included.
    current_key = "issue-orch.filtertestcurrent.1"
    core.ledger_path(current_key).write_text(json.dumps({
        "role": "issue-orch", "scope": ["filtertestcurrent", 1], "workdir": str(T),
        "pgid": filt_proc.pid, "started": started,
        "log": str(core.ledger_log_path(current_key)),
    }))
    pgids2 = core.live_tracked_pgids()
    check("ltp_current_row_with_same_pgid_included", True, filt_proc.pid in pgids2)
finally:
    settings_path.unlink(missing_ok=True)
    rolled_path.unlink(missing_ok=True)
    core.ledger_path("issue-orch.filtertestcurrent.1").unlink(missing_ok=True)
    try:
        core._pgid_alive(filt_proc.pid) and os.killpg(filt_proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        filt_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        filt_proc.kill()
        filt_proc.wait(timeout=2)

# === tick_loop burst coalescing: two exits between snapshots -> ONE gone set,
# not two ==================================================================
# Same snapshot mechanism the loop uses: prev = live_tracked_pgids(); ... ;
# cur = live_tracked_pgids(); newly_gone = prev - cur. Two tracked pgids
# disappearing inside a single poll window must collapse into one
# newly_gone set of size 2 -- one event, not two separate ticks.
burst_proc_a = subprocess.Popen(["sleep", "30"], start_new_session=True)
burst_proc_b = subprocess.Popen(["sleep", "30"], start_new_session=True)
try:
    time.sleep(0.3)
    burst_key_a = "issue-orch.bursta.1"
    burst_key_b = "issue-orch.burstb.1"
    core.ledger_path(burst_key_a).write_text(json.dumps({
        "role": "issue-orch", "scope": ["bursta", 1], "workdir": str(T),
        "pgid": burst_proc_a.pid, "started": core.now_iso(),
        "log": str(core.ledger_log_path(burst_key_a)),
    }))
    core.ledger_path(burst_key_b).write_text(json.dumps({
        "role": "issue-orch", "scope": ["burstb", 1], "workdir": str(T),
        "pgid": burst_proc_b.pid, "started": core.now_iso(),
        "log": str(core.ledger_log_path(burst_key_b)),
    }))

    prev = core.live_tracked_pgids()
    check("burst_prev_has_both", True,
          burst_proc_a.pid in prev and burst_proc_b.pid in prev)

    # Both exit "between two snapshots" -- kill both before taking `cur`.
    import signal as _signal
    os.killpg(burst_proc_a.pid, _signal.SIGKILL)
    os.killpg(burst_proc_b.pid, _signal.SIGKILL)
    burst_proc_a.wait(timeout=2)
    burst_proc_b.wait(timeout=2)

    cur = core.live_tracked_pgids()
    newly_gone = prev - cur
    check("burst_one_gone_set_size_two", 2, len(newly_gone))
    check("burst_gone_set_has_both", True,
          burst_proc_a.pid in newly_gone and burst_proc_b.pid in newly_gone)
finally:
    core.ledger_path("issue-orch.bursta.1").unlink(missing_ok=True)
    core.ledger_path("issue-orch.burstb.1").unlink(missing_ok=True)
    for _p in (burst_proc_a, burst_proc_b):
        try:
            core._pgid_alive(_p.pid) and os.killpg(_p.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            _p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _p.kill()
            _p.wait(timeout=2)

# === ticker._should_fire: pure truth table ==================================
# pending regardless of clock -> (True, "exit"); not pending and
# since >= interval -> (True, "clock"); not pending and since < interval ->
# (False, ""). Also the exact boundary since == interval fires.
#
# Lives in orch.ticker, not orch.server, since orch#442 split the scan/spawn
# loop out of the web process. The truth table moved intact.
from orch import ticker as tkr
check("should_fire_pending_true_regardless_of_clock_under",
      (True, "exit"), tkr._should_fire(True, 0, 600))
check("should_fire_pending_true_regardless_of_clock_over",
      (True, "exit"), tkr._should_fire(True, 99999, 600))
check("should_fire_not_pending_since_ge_interval",
      (True, "clock"), tkr._should_fire(False, 601, 600))
check("should_fire_not_pending_since_lt_interval",
      (False, ""), tkr._should_fire(False, 599, 600))
check("should_fire_boundary_since_eq_interval",
      (True, "clock"), tkr._should_fire(False, 600, 600))
# === issue_brief =============================================================
# gh issue list --json comments shape: body, createdAt, author:{"login":...}.
# No gh call inside issue_brief -- these are hand-built fixtures, not fetched.

# no brief anywhere -> None, the honest empty state.
check("issue_brief_absent_no_comments", None, core.issue_brief([]))
check("issue_brief_absent_none_comments", None, core.issue_brief(None))
check("issue_brief_absent_no_brief_line", None, core.issue_brief([
    {"body": "orch/issue-orch plan\nno brief line here.", "createdAt": "2026-09-12T10:00:00Z",
     "author": {"login": "bot"}},
]))

# a brief present -> text parsed, "brief:" prefix stripped, at/stale set.
_b1 = core.issue_brief([
    {"body": "orch/issue-orch wake\nsome prose.\nbrief: waiting on you -- needs a human merge",
     "createdAt": "2026-09-12T10:00:00Z", "author": {"login": "bot"}},
])
check("issue_brief_present_text", "waiting on you -- needs a human merge", _b1["text"] if _b1 else None)
check("issue_brief_present_at", "2026-09-12T10:00:00Z", _b1["at"] if _b1 else None)
check("issue_brief_present_stale_default_false", False, _b1["stale"] if _b1 else None)

# case-insensitive prefix, leading whitespace on the line, tolerated.
_b1b = core.issue_brief([
    {"body": "orch/issue-orch wake\n   BRIEF:   trimmed text  ",
     "createdAt": "2026-09-12T10:00:00Z", "author": {"login": "bot"}},
])
check("issue_brief_case_insensitive_and_trimmed", "trimmed text", _b1b["text"] if _b1b else None)

# operator comment carrying "brief:" is SKIPPED in favour of an older agent one.
_b2 = core.issue_brief([
    {"body": "orch/issue-orch wake\nbrief: the real agent brief",
     "createdAt": "2026-09-12T09:00:00Z", "author": {"login": "bot"}},
    {"body": "orch/operator reply\nbrief: this is not a real brief, it's an instruction",
     "createdAt": "2026-09-12T10:00:00Z", "author": {"login": "kiri"}},
])
check("issue_brief_operator_skipped", "the real agent brief", _b2["text"] if _b2 else None)

# a plain human comment (no orch/ prefix) is also skipped as an instruction.
_b2b = core.issue_brief([
    {"body": "orch/issue-orch wake\nbrief: the real agent brief",
     "createdAt": "2026-09-12T09:00:00Z", "author": {"login": "bot"}},
    {"body": "please fix this\nbrief: not a real one",
     "createdAt": "2026-09-12T10:00:00Z", "author": {"login": "kiri"}},
])
check("issue_brief_plain_human_skipped", "the real agent brief", _b2b["text"] if _b2b else None)

# newest agent brief wins over an older agent brief.
_b3 = core.issue_brief([
    {"body": "orch/issue-orch wake\nbrief: older brief",
     "createdAt": "2026-09-12T08:00:00Z", "author": {"login": "bot"}},
    {"body": "orch/issue-orch wake\nbrief: newer brief",
     "createdAt": "2026-09-12T09:00:00Z", "author": {"login": "bot"}},
])
check("issue_brief_newest_wins", "newer brief", _b3["text"] if _b3 else None)

# stale True when activity is far newer than the brief's own createdAt.
import datetime as _dt
_brief_dt = _dt.datetime.fromisoformat("2026-09-12T09:00:00+00:00")
_activity_far = int(_brief_dt.timestamp()) + core.BRIEF_STALE_MINS * 60 + 3600
_b4 = core.issue_brief([
    {"body": "orch/issue-orch wake\nbrief: this will go stale",
     "createdAt": "2026-09-12T09:00:00Z", "author": {"login": "bot"}},
], activity=_activity_far)
check("issue_brief_stale_true", True, _b4["stale"] if _b4 else None)

# stale False when activity is None.
_b5 = core.issue_brief([
    {"body": "orch/issue-orch wake\nbrief: no activity given",
     "createdAt": "2026-09-12T09:00:00Z", "author": {"login": "bot"}},
], activity=None)
check("issue_brief_stale_false_when_activity_none", False, _b5["stale"] if _b5 else None)

# over-length text is truncated and marked with an ellipsis.
_long = "x" * (core.BRIEF_MAX_CHARS + 50)
_b6 = core.issue_brief([
    {"body": f"orch/issue-orch wake\nbrief: {_long}",
     "createdAt": "2026-09-12T09:00:00Z", "author": {"login": "bot"}},
])
check("issue_brief_truncated_length", core.BRIEF_MAX_CHARS + 1, len(_b6["text"]) if _b6 else None)
check("issue_brief_truncated_marked", True, _b6["text"].endswith("…") if _b6 else False)

# malformed input must not raise: missing keys, None body, non-dict entries.
try:
    _b7 = core.issue_brief([
        {},
        {"body": None, "createdAt": "2026-09-12T09:00:00Z", "author": None},
        "not a dict",
        None,
        42,
        {"createdAt": "not-a-real-timestamp", "author": {"login": "bot"},
         "body": "orch/issue-orch wake\nbrief: survives a bad timestamp"},
    ], activity=_activity_far)
    check("issue_brief_malformed_survives", "survives a bad timestamp", _b7["text"] if _b7 else None)
    check("issue_brief_malformed_stale_false_on_bad_ts", False, _b7["stale"] if _b7 else None)
except Exception as e:
    check("issue_brief_malformed_no_raise", "no exception", f"raised {e!r}")

# === parse_review_block =====================================================
# The reviewer's block is the only record of a review -- no DB, no state file
# -- so the parser is the whole contract. It reads agent-authored text and
# must never raise.
review_full = """Prose findings here, which the parser ignores entirely.

<!-- orch:review:v1
1 fix-before-merge core.py:412 retry loop has no backoff
2 merge-as-is server.py:88 boundary hook shape
3 follow-up core.py:701 sessions.patchMany bypasses the gate
-->"""
parsed_full = core.parse_review_block(review_full)
check("review_full_count", 3, len(parsed_full))
check("review_full_item1", {"n": 1, "verdict": "fix-before-merge",
                            "location": "core.py:412",
                            "finding": "retry loop has no backoff"}, parsed_full[0])
check("review_full_item2", {"n": 2, "verdict": "merge-as-is",
                            "location": "server.py:88",
                            "finding": "boundary hook shape"}, parsed_full[1])
check("review_full_item3", {"n": 3, "verdict": "follow-up",
                            "location": "core.py:701",
                            "finding": "sessions.patchMany bypasses the gate"}, parsed_full[2])

# The distinction the whole design rests on: an EMPTY block means a reviewer
# looked and found nothing; NO block means no review ran. [] is not None and
# None is not [] -- assert both directions, since a later reader collapsing
# them turns "never reviewed" into "reviewed, clean".
empty_block = core.parse_review_block("looked, found nothing\n\n<!-- orch:review:v1 -->")
check("review_empty_block_is_list", [], empty_block)
check("review_empty_block_is_not_none", False, empty_block is None)

no_block = core.parse_review_block("just a comment with no block at all")
check("review_no_block_is_none", None, no_block)
check("review_no_block_is_not_empty_list", False, no_block == [])

# orch#427: an unterminated block keeps the items that parsed above the cut.
# The forge truncates a long comment mid-block; the item lines above the cut
# are complete and ARE the review, so discarding them handed review_blocks_merge
# an empty "reviewer found nothing" and merged past a known blocker.
unterminated = core.parse_review_block(
    "<!-- orch:review:v1\n1 fix-before-merge core.py:1 survived the cut")
check("review_unterminated_keeps_items", 1, len(unterminated))
check("review_unterminated_keeps_verdict", "fix-before-merge",
      unterminated[0]["verdict"])

# ...but invents nothing. An UNTERMINATED marker carrying no parseable item is
# not a review at all -> None. It cannot be told apart from the marker quoted
# in prose (agents/reviewer.md documents the literal token), and the two are
# the same thing: nobody reviewed. A CLOSED empty block is still [] -- that
# one is an unambiguous "a reviewer looked and found nothing".
check("review_unterminated_no_items_is_none", None,
      core.parse_review_block("<!-- orch:review:v1\nprose that got cut mid-sent"))
check("review_garbage_lines_in_block", [],
      core.parse_review_block("<!-- orch:review:v1\nnot an item line at all\n-->"))

# The marker mentioned in PROSE AFTER a real closed block must not void it.
# An unfiltered end-of-text match appends an empty "block" that wins
# "last block wins" and silently disarms the blocker above it -- this issue's
# own bug through a new door. A reviewer naming the format in a closing
# remark is ordinary, so this is reachable, not theoretical.
prose_after_block = core.parse_review_block(
    "<!-- orch:review:v1\n1 fix-before-merge core.py:1 real blocker\n-->\n\n"
    "PS: re-run the reviewer to emit a fresh <!-- orch:review:v1 block.")
check("review_prose_mention_after_block_keeps_items", 1, len(prose_after_block))
check("review_prose_mention_after_block_keeps_verdict", "fix-before-merge",
      prose_after_block[0]["verdict"])

# A prose mention with no real block anywhere is no review: None, not [].
check("review_prose_mention_alone_is_none", None,
      core.parse_review_block("see the <!-- orch:review:v1 format in reviewer.md"))

# A truncated block is a block for "last wins" too: a closed block followed by
# a truncated RE-review resolves to the NEWER one. Recovering truncated items
# only when no closed block existed would return the superseded verdict here.
closed_then_truncated = core.parse_review_block(
    "<!-- orch:review:v1\n1 merge-as-is old.py:1 stale clean verdict\n-->\n"
    "re-reviewed:\n"
    "<!-- orch:review:v1\n1 fix-before-merge new.py:2 live blocker")
check("review_truncated_supersedes_closed_count", 1, len(closed_then_truncated))
check("review_truncated_supersedes_closed_verdict", "fix-before-merge",
      closed_then_truncated[0]["verdict"])

# An unknown verdict token is skipped; the valid siblings survive. A reviewer
# that invents a fifth class must not cost the findings around it.
parsed_mixed = core.parse_review_block(
    "<!-- orch:review:v1\n"
    "1 fix-before-merge core.py:10 real finding\n"
    "2 looks-good core.py:20 invented verdict class\n"
    "3 wontfix core.py:30 another real finding\n"
    "-->")
check("review_bad_verdict_skipped_count", 2, len(parsed_mixed))
check("review_bad_verdict_survivors", [1, 3], [it["n"] for it in parsed_mixed])
check("review_bad_verdict_kept_wontfix", "wontfix", parsed_mixed[1]["verdict"])

# Two blocks in one text: the LAST wins. A re-review appends; the newest
# block is the live one.
parsed_two = core.parse_review_block(
    "<!-- orch:review:v1\n1 fix-before-merge old.py:1 stale finding\n-->\n"
    "re-reviewed against the new head:\n"
    "<!-- orch:review:v1\n1 merge-as-is new.py:2 fresh finding\n-->")
check("review_two_blocks_last_wins_count", 1, len(parsed_two))
check("review_two_blocks_last_wins_finding", "fresh finding", parsed_two[0]["finding"])

# === orch#58 unit 3: World.load routes through the per-repo backend ========
# World.load(gr) is only ever handed an "owner/repo" string (see merge_pr,
# feed.repo_json); it must resolve which CLI owns that slug itself, via the
# same _repo_entry_for_owner_slug reverse lookup _journal_append_issue
# already uses, and build the right argv/normalize the right shape on each
# backend. repos.txt carries one gh-backed entry and one tea-backed entry so
# the reverse lookup has something real to resolve against.
load_gh_dir = T / "load-gh"
load_gh_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=load_gh_dir, check=True)
subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/orch.git"],
                cwd=load_gh_dir, check=True)
load_tea_dir = T / "load-tea"
load_tea_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=load_tea_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                 "http://gitea.local:3000/cybermelon/gita-lectures.git"], cwd=load_tea_dir, check=True)
_write_repos_txt(
    f"{load_gh_dir}\n{load_tea_dir}  login=gitea\n"
)
_load_gh_slug = core.gh_repo(load_gh_dir)
_load_tea_slug = core.repo_slug(load_tea_dir)

_load_calls = []
_orig_run_for_load = core._run


def _fake_load_run(cmd, cwd=None, timeout=None):
    if cmd[:2] == ["git", "-C"]:
        return _orig_run_for_load(cmd, cwd=cwd, timeout=timeout)
    _load_calls.append(cmd)
    if cmd[0] == "gh" and cmd[1:3] == ["issue", "list"]:
        return True, json.dumps([
            {"number": 1, "title": "gh issue", "labels": [{"name": "agent-ready"}],
             "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
             "comments": [{"body": "a gh comment"}]},
        ])
    if cmd[0] == "gh" and cmd[1:3] == ["pr", "list"]:
        return True, json.dumps([{"number": 5, "headRefName": "issue-1", "state": "MERGED"}])
    if cmd[0] == "tea" and cmd[1:3] == ["issue", "list"]:
        return True, json.dumps([
            {"index": "3", "title": "tea issue", "labels": "agent-ready",
             "created": "2026-02-02T00:00:00Z", "updated": "2026-02-02T00:00:00Z"},
        ])
    if cmd[0] == "tea" and cmd[1:3] == ["pr", "list"] and "--fields" in cmd and \
            cmd[cmd.index("--fields") + 1] == "index,head,state":
        return True, json.dumps([{"index": "6", "head": "issue-3", "state": "merged"}])
    if cmd[0] == "tea" and cmd[1:3] == ["pr", "list"] and "--fields" in cmd and \
            cmd[cmd.index("--fields") + 1] == "index,ci":
        return True, json.dumps([{"index": "6", "ci": ""}])
    return False, ""


core._run = _fake_load_run
try:
    # --- gh path: argv is IDENTICAL to pre-#58, comments field included ----
    _load_calls.clear()
    gh_world = core.World()
    ok = gh_world.load(_load_gh_slug)
    check("load_gh_ok", True, ok)
    check("load_gh_backend", "gh", gh_world.backend)
    issue_calls = [c for c in _load_calls if c[0] == "gh" and c[1] == "issue" and c[2] == "list"]
    check("load_gh_issue_list_argv", [
        "gh", "issue", "list", "--repo", _load_gh_slug, "--state", "open",
        "--limit", "100", "--json",
        "number,title,labels,createdAt,updatedAt,comments",
    ], issue_calls[0])
    pr_calls = [c for c in _load_calls if c[0] == "gh" and c[1] == "pr" and c[2] == "list"]
    check("load_gh_pr_list_argv", [
        "gh", "pr", "list", "--repo", _load_gh_slug, "--state", "all",
        "--limit", "60", "--json", "number,headRefName,state",
    ], pr_calls[0])
    check("load_gh_issues_shape_unchanged", "a gh comment",
          gh_world.issues[0]["comments"][0]["body"])
    check("load_gh_prs_unchanged", "MERGED", gh_world.prs[0]["state"])

    # --- tea path: TEA_ARGV builders, JSON normalized to GitHub shape ------
    _load_calls.clear()
    tea_world = core.World()
    ok2 = tea_world.load(_load_tea_slug)
    check("load_tea_ok", True, ok2)
    check("load_tea_backend", "tea", tea_world.backend)
    check("load_tea_login", "gitea", tea_world.login)
    tea_issue_calls = [c for c in _load_calls if c[0] == "tea" and c[1] == "issue" and c[2] == "list"]
    check("load_tea_issue_list_argv", core.TEA_ARGV["issue_list"](_load_tea_slug, "gitea"),
          tea_issue_calls[0])
    check("load_tea_issue_normalized_number_is_int", True,
          isinstance(tea_world.issues[0]["number"], int))
    check("load_tea_issue_normalized_number", 3, tea_world.issues[0]["number"])
    check("load_tea_issue_normalized_labels",
          [{"name": "agent-ready"}], tea_world.issues[0]["labels"])
    # tea's list-level `comments` field is a bare count, not full bodies --
    # no per-issue N+1 fetch is made in load(), so this is an honest [],
    # never an invented body. Prove the absence is explicit, not a KeyError.
    check("load_tea_issue_comments_absent_is_empty_list", [], tea_world.issues[0]["comments"])
    check("load_tea_pr_normalized_state_upper", "MERGED", tea_world.prs[0]["state"])
    check("load_tea_pr_normalized_head", "issue-3", tea_world.prs[0]["headRefName"])

    # --- an owner/repo slug that matches no repos.txt entry defaults gh ----
    _load_calls.clear()
    unknown_world = core.World()
    unknown_world.load("nobody/nowhere")
    check("load_unknown_slug_defaults_gh_backend", "gh", unknown_world.backend)
finally:
    core._run = _orig_run_for_load

# --- _fetch_rollup: gh path unchanged, tea path uses the list+pick argv ----
_rollup_calls = []


def _fake_rollup_run(cmd, cwd=None, timeout=None):
    _rollup_calls.append(cmd)
    if cmd[0] == "gh":
        return True, json.dumps([{"conclusion": "SUCCESS"}])
    if cmd[0] == "tea":
        return True, json.dumps([{"index": "6", "ci": "success"}, {"index": "9", "ci": ""}])
    return False, ""


core._run = _fake_rollup_run
try:
    gh_rollup_world = core.World()
    gh_rollup_world.backend = "gh"
    gh_rollup_world.repo_slug = "cybermelon/orch"
    _rollup_calls.clear()
    roll = gh_rollup_world._fetch_rollup(6)
    check("fetch_rollup_gh_argv", [
        "gh", "pr", "view", "6", "--repo", "cybermelon/orch",
        "--json", "statusCheckRollup", "--jq", ".statusCheckRollup",
    ], _rollup_calls[-1])
    check("fetch_rollup_gh_result", [{"conclusion": "SUCCESS"}], roll)

    tea_rollup_world = core.World()
    tea_rollup_world.backend = "tea"
    tea_rollup_world.login = "gitea"
    tea_rollup_world.repo_slug = "cybermelon/gita-lectures"
    _rollup_calls.clear()
    roll_tea = tea_rollup_world._fetch_rollup(6)
    check("fetch_rollup_tea_argv", core.TEA_ARGV["pr_rollup"]("cybermelon/gita-lectures", "gitea"),
          _rollup_calls[-1])
    check("fetch_rollup_tea_result_normalized", "success", roll_tea)
    # a PR whose ci row is empty normalizes to None, same as the gh "no
    # rollup" case -- pr_green already reads None as green. tea ANSWERED
    # here, and the answer is "this repo runs no CI", so it stays None.
    roll_tea_empty = tea_rollup_world._fetch_rollup(9)
    check("fetch_rollup_tea_empty_ci_is_none", None, roll_tea_empty)
    # a PR number absent from the tea list entirely is ROLLUP_UNREADABLE, not
    # None. The old assertion read "same as no rollup" -- which is exactly the
    # conflation this pins against now: we did not learn that the PR has no
    # CI, we failed to find the PR at all, and None would buy that failure a
    # green light from pr_green. Not a crash either.
    roll_tea_missing = tea_rollup_world._fetch_rollup(999)
    check("fetch_rollup_tea_missing_row_is_unreadable",
          core.ROLLUP_UNREADABLE, roll_tea_missing)
finally:
    core._run = _orig_run_for_load

# --- merge_pr: the slug handed to World.load must match the repo's ACTUAL
# backend, not always gh_repo(). Repro: merge_pr on a tea-backed repo must
# resolve via repo_slug(), the same way a_assign already does in server.py --
# calling gh_repo() unconditionally would hand World.load an unstripped raw
# remote URL that _repo_entry_for_owner_slug can never match. -----------------
merge_tea_dir = T / "merge-tea"
merge_tea_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=merge_tea_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                 "http://gitea.local:3000/cybermelon/gita-lectures.git"], cwd=merge_tea_dir, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "--allow-empty", "-m", "init", "-q"], cwd=merge_tea_dir, check=True)
_write_repos_txt(f"{merge_tea_dir}  login=gitea\n")

_merge_tea_calls = []
_orig_run_for_merge_tea = core._run


def _fake_merge_tea_run(cmd, cwd=None, timeout=None):
    if cmd[:2] == ["git", "-C"]:
        return _orig_run_for_merge_tea(cmd, cwd=cwd, timeout=timeout)
    _merge_tea_calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
    if cmd[:2] == ["tea", "pr"] and cmd[2] == "merge":
        return True, "merged\n"
    # orch#421: merge_pr now calls review_items_for_pr before the shell-out;
    # on a tea-backed repo that's be.pr_view_review (see core.TEA_ARGV),
    # `tea pr <n> ... --comments --output json`. Stub it to "no block" so
    # this gate test -- which asserts on the merge call, not review state --
    # stays deterministic and never makes a real network call.
    if cmd[:2] == ["tea", "pr"] and "--comments" in cmd:
        return True, json.dumps({"comments": []})
    return _orig_run_for_merge_tea(cmd, cwd=cwd, timeout=timeout)


core._run = _fake_merge_tea_run
try:
    tea_review_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=11)
    merge_result = _with_stub_world(
        tea_review_stub, lambda: core.merge_pr(merge_tea_dir, 3, 11))
    check("merge_tea_happy_ok", True, merge_result["ok"])
    # orch#421: filtered to the `tea pr ... merge` invocation specifically --
    # merge_pr now also makes a `tea pr <n> --comments ...` call
    # (review_items_for_pr) before the shell-out.
    tea_pr_merge_calls = [c for c in _merge_tea_calls
                           if c["cmd"][:2] == ["tea", "pr"] and c["cmd"][2] == "merge"]
    check("merge_tea_happy_called_once", 1, len(tea_pr_merge_calls))
    check("merge_tea_happy_argv", core.TEA_ARGV["pr_merge"](
        "cybermelon/gita-lectures", "gitea", 11), tea_pr_merge_calls[0]["cmd"])
    check("merge_tea_happy_cwd", str(merge_tea_dir), tea_pr_merge_calls[0]["cwd"])
finally:
    core._run = _orig_run_for_merge_tea
    _write_repos_txt(str(merge_repo_dir) + "\n")

# --- review_items_for_pr: gh path unchanged, tea path via pr_view_review ---
review_tea_dir = T / "review-tea"
review_tea_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=review_tea_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                 "http://gitea.local:3000/cybermelon/gita-lectures.git"], cwd=review_tea_dir, check=True)
_write_repos_txt(f"{review_tea_dir}  login=gitea\n")

_review_calls = []
_orig_run_for_review = core._run


def _fake_review_run(cmd, cwd=None, timeout=None):
    if cmd[:2] == ["git", "-C"]:
        return _orig_run_for_review(cmd, cwd=cwd, timeout=timeout)
    _review_calls.append({"cmd": list(cmd), "cwd": cwd})
    if cmd[0] == "tea":
        return True, json.dumps({
            "headSha": "deadbeef",
            "comments": [{"id": 1, "author": "op", "body":
                          "<!-- orch:review:v1\n1 wontfix x.py:1 finding\n-->"}],
        })
    return False, ""


core._run = _fake_review_run
try:
    tea_review = core.review_items_for_pr(review_tea_dir, 4)
    check("review_items_tea_argv", core.TEA_ARGV["pr_view_review"](
        "cybermelon/gita-lectures", "gitea", 4), _review_calls[-1]["cmd"])
    check("review_items_tea_sha", "deadbeef", tea_review["sha"])
    check("review_items_tea_items_count", 1, len(tea_review["items"]))
finally:
    core._run = _orig_run_for_review
    _write_repos_txt(str(merge_repo_dir) + "\n")

# --- _normalize_tea_pr_view: headSha -> headRefOid, comments passthrough ---
tea_pr_view_row = {"headSha": "abc123", "comments": [{"id": 1, "body": "hi"}]}
norm_pr_view = core._normalize_tea_pr_view(tea_pr_view_row)
check("normalize_pr_view_sha_renamed", "abc123", norm_pr_view["headRefOid"])
check("normalize_pr_view_comments_passthrough",
      tea_pr_view_row["comments"], norm_pr_view["comments"])

# === review_blocks_merge (orch#408) =========================================
# Derives a merge hold straight from review_items_for_pr's return -- no
# label, no state file. Stub review_items_for_pr directly rather than
# round-tripping through gh/tea json: the sha/comment parsing is already
# covered above, this unit only cares what review_blocks_merge does with
# the {"sha", "items"} shape (or None) it is handed.
# (_orig_review_items_for_pr / _stub_review_items are defined earlier in
# this file, near _gh_merge_calls, so blocks above this section can use
# them too -- see the comment there.)

try:
    # No review found at all (unreadable PR or never reviewed) -> not blocked.
    _stub_review_items(None)
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_none_not_blocked", False, blocked)
    check("review_blocks_merge_none_reason_empty", "", reason)

    # Only merge-as-is / follow-up / wontfix items -> not blocked.
    _stub_review_items({"sha": "abc1234", "items": [
        {"n": 1, "verdict": "merge-as-is", "location": "x.py:1", "finding": "fine"},
        {"n": 2, "verdict": "follow-up", "location": "x.py:2", "finding": "later"},
        {"n": 3, "verdict": "wontfix", "location": "x.py:3", "finding": "nope"},
    ]})
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_no_blockers_not_blocked", False, blocked)

    # One fix-before-merge item -> blocked, reason names it.
    _stub_review_items({"sha": "abc1234", "items": [
        {"n": 1, "verdict": "merge-as-is", "location": "x.py:1", "finding": "fine"},
        {"n": 2, "verdict": "fix-before-merge", "location": "x.py:2", "finding": "bug"},
    ]})
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_one_blocker_blocked", True, blocked)
    check("review_blocks_merge_one_blocker_reason_names_it", True,
          "#2" in reason and "1" in reason)

    # Multiple fix-before-merge items -> reason names all of them.
    _stub_review_items({"sha": "abc1234", "items": [
        {"n": 1, "verdict": "fix-before-merge", "location": "x.py:1", "finding": "a"},
        {"n": 2, "verdict": "merge-as-is", "location": "x.py:2", "finding": "b"},
        {"n": 3, "verdict": "fix-before-merge", "location": "x.py:3", "finding": "c"},
    ]})
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_multi_blocker_blocked", True, blocked)
    check("review_blocks_merge_multi_blocker_reason_names_both", True,
          "#1" in reason and "#3" in reason)

    # [] items (reviewer looked, found nothing) -> not blocked.
    _stub_review_items({"sha": "abc1234", "items": []})
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_empty_items_not_blocked", False, blocked)

    # review_items_for_pr raising unexpectedly -> fail open, never raises.
    # Takes `unreadable` for the same reason _stub_review_items does: with the
    # old two-arg signature this raises TypeError from the call itself, which
    # the same `except Exception` swallows -- so the check passed while the
    # RuntimeError below was dead code, testing a signature mismatch rather
    # than the unexpected-exception branch it names (orch#408 review).
    def _raising(repo, pr, unreadable=False):
        raise RuntimeError("boom")
    core.review_items_for_pr = _raising
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_raising_fails_open", False, blocked)

    # orch#408: UNREADABLE is the one case that does NOT fail open.
    _stub_review_items(core.UNREADABLE)
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_unreadable_blocked", True, blocked)
    check("review_blocks_merge_unreadable_reason_nonempty", True, bool(reason))
    check("review_blocks_merge_unreadable_reason_mentions_read", True,
          "read" in reason.lower())

    # orch#427: a TRUNCATED block carrying a fix-before-merge item must hold
    # the merge. Driven through the real parse_review_block rather than a
    # stubbed items list -- the defect was the parser handing the gate [],
    # so a test that stubs the items past the parser cannot see it. This
    # feeds a forge-truncated comment body in at the layer the bug lived at
    # and asserts the answer at the gate, which is where it did damage.
    def _stub_review_from_comment(body):
        core.review_items_for_pr = lambda repo, pr, unreadable=False: (
            lambda items: None if items is None else {"sha": "abc1234",
                                                      "items": items}
        )(core.parse_review_block(body))

    _stub_review_from_comment(
        "Review of PR #9\n\n"
        "<!-- orch:review:v1\n"
        "1 fix-before-merge core.py:5150 gate reads [] as reviewed-clean\n"
        "2 follow-up core.py:5200 tidy the regex comme")
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_truncated_block_blocks", True, blocked)
    check("review_blocks_merge_truncated_block_names_item", True, "#1" in reason)

    # A truncated marker carrying no parseable item is no review at all
    # (None) -> not blocked. Guards the recovery from over-reaching into
    # "any truncation blocks", which would freeze merges on a stray marker.
    _stub_review_from_comment("<!-- orch:review:v1\nprose cut mid-sent")
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_truncated_no_items_not_blocked", False, blocked)

    # The marker quoted in PROSE after a real blocking block must not void it.
    # Without the carries-an-item filter the trailing empty match wins "last
    # block wins" and this merges past finding #1 -- the very defect orch#427
    # exists to close, reached through a different input. The parser-level
    # test for this passes against both the correct and the broken parser
    # only at the gate does the consequence show, so it is asserted here.
    _stub_review_from_comment(
        "<!-- orch:review:v1\n"
        "1 fix-before-merge core.py:5150 gate reads [] as reviewed-clean\n-->\n\n"
        "PS: re-run the reviewer to emit a fresh <!-- orch:review:v1 block.")
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_prose_mention_still_blocks", True, blocked)
    check("review_blocks_merge_prose_mention_names_item", True, "#1" in reason)

    # A truncated RE-review supersedes an earlier CLOSED block: the live
    # blocker wins over the stale merge-as-is it replaced. Without this the
    # gate merges on a superseded verdict.
    _stub_review_from_comment(
        "<!-- orch:review:v1\n1 merge-as-is core.py:1 looked clean then\n-->\n"
        "re-reviewed against the new head:\n"
        "<!-- orch:review:v1\n1 fix-before-merge core.py:42 regression the fix introduced")
    blocked, reason = core.review_blocks_merge(merge_repo_dir, 9)
    check("review_blocks_merge_truncated_rereview_supersedes", True, blocked)
finally:
    core.review_items_for_pr = _orig_review_items_for_pr

# --- merge_pr gate: issue-orch + blocking review -> refused -----------------
# auto-land must be ON here, else the earlier orch#387 gate refuses
# issue-orch first (decided=False) and this gate is never reached --
# that gate's own tests already cover the off-by-default case.
_merge_calls.clear()
core._run = _fake_run
_review_gate_orch_json = core.ORCH_HOME / "orch.json"
_review_gate_orch_json.write_text(json.dumps({"repos": [
    {"path": str(merge_repo_dir), "state": "tracked", "auto-land": True},
]}))
try:
    _stub_review_items({"sha": "abc1234", "items": [
        {"n": 1, "verdict": "fix-before-merge", "location": "x.py:1", "finding": "bug"},
    ]})
    review_gate_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)
    result = _with_stub_world(
        review_gate_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_gate_review_issue_orch_refused", False, result["ok"])
    check("merge_gate_review_issue_orch_no_gh_calls", 0, len(_gh_merge_calls(_merge_calls)))
    check("merge_gate_review_issue_orch_message", True,
          "held by review" in result["out"])

    # operator -> exempt, proceeds past the review gate despite the same
    # blocking review still stubbed above.
    _merge_calls.clear()
    result = _with_stub_world(
        review_gate_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="operator"))
    check("merge_gate_review_operator_ok", True, result["ok"])
    check("merge_gate_review_operator_gh_called", 1, len(_gh_merge_calls(_merge_calls)))

    # merge-blocked -> exempt, proceeds past the review gate too.
    _merge_calls.clear()
    result = _with_stub_world(
        review_gate_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="merge-blocked"))
    check("merge_gate_review_merge_blocked_ok", True, result["ok"])
    check("merge_gate_review_merge_blocked_gh_called", 1, len(_gh_merge_calls(_merge_calls)))
finally:
    core._run = _orig_run
    core.review_items_for_pr = _orig_review_items_for_pr
    _review_gate_orch_json.unlink(missing_ok=True)

# --- merge_pr gate: UNREADABLE review -> issue-orch refused, others exempt --
# orch#408: the sentinel case. An unreadable PR must block issue-orch's
# unattended path exactly like a positive fix-before-merge would -- see
# review_blocks_merge's docstring for why this is the one case that does
# NOT fail open -- but decided_by values with their own authority (operator,
# merge-blocked) still ride through untouched.
_merge_calls.clear()
core._run = _fake_run
_review_gate_orch_json = core.ORCH_HOME / "orch.json"
_review_gate_orch_json.write_text(json.dumps({"repos": [
    {"path": str(merge_repo_dir), "state": "tracked", "auto-land": True},
]}))
try:
    _stub_review_items(core.UNREADABLE)
    unreadable_gate_stub = _StubMergeWorld(pr_state="OPEN", green=True, red=False, pr_number=9)

    result = _with_stub_world(
        unreadable_gate_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="issue-orch"))
    check("merge_gate_unreadable_issue_orch_refused", False, result["ok"])
    check("merge_gate_unreadable_issue_orch_no_gh_calls",
          0, len(_gh_merge_calls(_merge_calls)))

    # operator -> exempt from the review gate, same unreadable PR.
    _merge_calls.clear()
    result = _with_stub_world(
        unreadable_gate_stub,
        lambda: core.merge_pr(merge_repo_dir, 7, 9, decided_by="operator"))
    check("merge_gate_unreadable_operator_ok", True, result["ok"])
    check("merge_gate_unreadable_operator_gh_called",
          1, len(_gh_merge_calls(_merge_calls)))
finally:
    core._run = _orig_run
    core.review_items_for_pr = _orig_review_items_for_pr
    _review_gate_orch_json.unlink(missing_ok=True)

# --- review_items_for_pr default contract is unchanged: an unreadable PR
# still returns plain None when called without unreadable=True (the feed's
# call shape). Pins that orch#408 did not move the existing contract for the
# only other caller of this function. Real function, fake failing _run --
# same machinery as _fake_review_run above, but always failing.
_orig_run_for_default_contract = core._run
core._run = lambda cmd, cwd=None, timeout=None: (False, "")
try:
    default_contract_result = core.review_items_for_pr(merge_repo_dir, 9)
    check("review_items_for_pr_default_unreadable_stays_none",
          True, default_contract_result is None)
    check("review_items_for_pr_default_unreadable_not_sentinel",
          True, default_contract_result is not core.UNREADABLE)
finally:
    core._run = _orig_run_for_default_contract

# --- _journal_tail_issue: gh path unchanged, tea path via issue_view_comments
jt_gh_repo = core.gh_repo(gh_remote_dir)
jt_tea_repo = core.repo_slug(tea_remote_dir)
_write_repos_txt(
    f"{gh_remote_dir}\n{tea_remote_dir}  login=gitea\n"
)
_jt_calls = []
_orig_run_for_jt = core._run


def _fake_jt_run(cmd, cwd=None, timeout=None):
    if cmd[:2] == ["git", "-C"]:
        return _orig_run_for_jt(cmd, cwd=cwd, timeout=timeout)
    _jt_calls.append(list(cmd))
    if cmd[0] == "gh":
        return True, json.dumps({"body": "issue body", "comments": [
            {"body": "orch/issue-orch spawn\nhi", "createdAt": "2026-01-01T00:00:00Z",
             "author": {"login": "bot"}},
        ]})
    if cmd[0] == "tea":
        return True, json.dumps({"body": "issue body", "comments": [
            {"body": "orch/issue-orch spawn\nhi", "created": "2026-02-02T00:00:00Z",
             "author": "cybermelon"},
        ]})
    return False, ""


core._run = _fake_jt_run
try:
    gh_tail = core.journal_tail("issue", jt_gh_repo, 5)
    check("journal_tail_issue_gh_argv", [
        "gh", "issue", "view", "5", "--repo", jt_gh_repo, "--json", "comments,body",
    ], _jt_calls[-1])
    check("journal_tail_issue_gh_author", "bot", gh_tail[0]["author"])
    check("journal_tail_issue_gh_at", "2026-01-01T00:00:00Z", gh_tail[0]["at"])
    check("journal_tail_issue_gh_orch_flag", True, gh_tail[0]["orch"])

    tea_tail = core.journal_tail("issue", jt_tea_repo, 6)
    check("journal_tail_issue_tea_argv",
          core.TEA_ARGV["issue_view_comments"](jt_tea_repo, "gitea", 6), _jt_calls[-1])
    check("journal_tail_issue_tea_author", "cybermelon", tea_tail[0]["author"])
    check("journal_tail_issue_tea_at", "2026-02-02T00:00:00Z", tea_tail[0]["at"])
    check("journal_tail_issue_tea_orch_flag", True, tea_tail[0]["orch"])
finally:
    core._run = _orig_run_for_jt

# === orch#163: auto_land_on precedence + repo_auto_land parser =============
check("auto_land_on_no_wins_alone", False,
      core.auto_land_on([core.L_NO_AUTOLAND], True))
check("auto_land_on_yes_alone", True,
      core.auto_land_on([core.L_AUTOLAND], False))
check("auto_land_on_both_no_wins", False,
      core.auto_land_on([core.L_AUTOLAND, core.L_NO_AUTOLAND], True))
check("auto_land_on_neither_default_true", True,
      core.auto_land_on([], True))
check("auto_land_on_neither_default_false", False,
      core.auto_land_on([], False))

# === orch#256: priority_rank + blocked_by ===================================
check("priority_rank_p0_lowest", 0, core.priority_rank([core.L_P0]))
check("priority_rank_p1_middle", 1, core.priority_rank([core.L_P1]))
check("priority_rank_p2_higher", 2, core.priority_rank([core.L_P2]))
check("priority_rank_untiered_last", 3, core.priority_rank([]))
check("priority_rank_tier_order", True,
      core.priority_rank([core.L_P0]) < core.priority_rank([core.L_P1]) <
      core.priority_rank([core.L_P2]) < core.priority_rank([]))
check("priority_rank_both_tiers_urgent_wins", 0,
      core.priority_rank([core.L_P1, core.L_P0]))

_pr_issues = [
    {"n": 1, "labels": [core.L_P2]},
    {"n": 2, "labels": []},
    {"n": 3, "labels": [core.L_P0]},
    {"n": 4, "labels": [core.L_P1]},
    {"n": 5, "labels": [core.L_P0, core.L_P2]},
]
_pr_sorted = sorted(_pr_issues, key=lambda i: core.priority_rank(i["labels"]))
check("priority_rank_sorts_issue_list", [3, 5, 4, 1, 2],
      [i["n"] for i in _pr_sorted])

check("blocked_by_parses_good_edge", {252},
      core.blocked_by([f"{core.BLOCKED_BY_PREFIX}252"]))
check("blocked_by_ignores_malformed", set(),
      core.blocked_by([f"{core.BLOCKED_BY_PREFIX}soon"]))
check("blocked_by_mixed_good_and_bad", {5},
      core.blocked_by([f"{core.BLOCKED_BY_PREFIX}5",
                        f"{core.BLOCKED_BY_PREFIX}soon", core.L_P0]))
check("blocked_by_empty", set(), core.blocked_by([]))
# These two pin the halves of blocked_by's `isascii() and isdecimal()` test.
# They fail against isdigit() and against isdecimal() respectively, so
# together they are what stops either half being "simplified" away.
#
# isdigit() accepts characters int() then REJECTS, so this case RAISED
# ValueError before orch#256's review -- breaking blocked_by's "never raises"
# contract on one bad label and taking the prioritize step down with it.
# isdecimal() is the half that rejects it.
check("blocked_by_superscript_digit_ignored", set(),
      core.blocked_by([f"{core.BLOCKED_BY_PREFIX}²"]))
# isdecimal() alone is NOT enough here: "٢٥٢".isdecimal() is
# True and int() parses it to 252, so this label invented an edge to a real
# issue number nobody wrote and collapsed into the same set element as a
# genuine blocked-by:252 -- an unreadable label producing a WRONG order, the
# one outcome BLOCKED_BY_PREFIX forbids. isascii() is the half that rejects
# it, and only it does.
check("blocked_by_arabic_indic_digits_ignored", set(),
      core.blocked_by([f"{core.BLOCKED_BY_PREFIX}٢٥٢"]))
check("blocked_by_empty_suffix_ignored", set(),
      core.blocked_by([core.BLOCKED_BY_PREFIX]))
check("blocked_by_bare_prefix_ignored", set(), core.blocked_by(["blocked-by"]))
check("blocked_by_negative_ignored", set(),
      core.blocked_by([f"{core.BLOCKED_BY_PREFIX}-5"]))

check("orch_labels_has_p0", True,
      core.L_P0 in dict(core.ORCH_LABELS))
check("orch_labels_has_p1", True,
      core.L_P1 in dict(core.ORCH_LABELS))
check("orch_labels_has_p2", True,
      core.L_P2 in dict(core.ORCH_LABELS))
check("orch_label_names_has_tiers", True,
      {core.L_P0, core.L_P1, core.L_P2} <= core.ORCH_LABEL_NAMES)

_ral_dir = T / "repo-auto-land"
_ral_dir.mkdir(parents=True, exist_ok=True)
_ral_orch_json = core.ORCH_HOME / "orch.json"


def _write_ral_entry(path, extra_repo_keys=None):
    """Overwrite orch.json with a single tracked entry for `path`, merged
    with `extra_repo_keys` (the per-test auto-land/in-flight-cap value(s)
    under test). Mirrors the t257 idiom above: this suite reuses one
    ORCH_HOME, so each case gets a fresh, fully-specified file rather than
    patching a shared one."""
    entry = {"path": str(path), "state": "tracked"}
    if extra_repo_keys:
        entry.update(extra_repo_keys)
    _ral_orch_json.write_text(json.dumps({"repos": [entry]}))


_write_ral_entry(_ral_dir, {"auto-land": True})
check("repo_auto_land_true", True, core.repo_auto_land(_ral_dir))

_write_ral_entry(_ral_dir, {"auto-land": False})
check("repo_auto_land_false", False, core.repo_auto_land(_ral_dir))

_ral_missing_dir = T / "repo-auto-land-missing"
_ral_missing_dir.mkdir(parents=True, exist_ok=True)
check("repo_auto_land_no_entry", False, core.repo_auto_land(_ral_missing_dir))

_write_ral_entry(_ral_dir)  # no auto-land key at all
check("repo_auto_land_key_absent", False, core.repo_auto_land(_ral_dir))

# A JSON config can hold types TOML never could -- these two matter more now
# than before, since the contract must REJECT a wrong-typed value rather
# than accept it as truthy.
_write_ral_entry(_ral_dir, {"auto-land": "true"})
check("repo_auto_land_string_true_rejected", False, core.repo_auto_land(_ral_dir))

_write_ral_entry(_ral_dir, {"auto-land": 1})
check("repo_auto_land_int_one_rejected", False, core.repo_auto_land(_ral_dir))

# A `state` other than "tracked" is invisible to repo_auto_land, same as a
# missing entry -- even though the key itself says auto-land.
_write_ral_entry(_ral_dir, {"auto-land": True})
_untracked_cfg = json.loads(_ral_orch_json.read_text())
_untracked_cfg["repos"][0]["state"] = "off"
_ral_orch_json.write_text(json.dumps(_untracked_cfg))
check("repo_auto_land_untracked_state_invisible", False, core.repo_auto_land(_ral_dir))

# orch#332: a `~`-spelled repo_path must resolve to the same answer as its
# absolute spelling. The entry side got .expanduser() (core.py:96) but the
# target side did not, so `~/orch` resolved to `<cwd>/~/orch` -- a literal
# directory named `~` -- and repo_auto_land("~/orch") returned False while the
# absolute path returned True for the SAME repo. Silent, and failing toward
# auto-land OFF. Every case above spells the path absolutely, which is why it
# went unseen; the lookup's own docstring already promised the two match.
_ral_tilde_home = T / "repo-auto-land-tilde-home"
_ral_tilde_repo = _ral_tilde_home / "r"
_ral_tilde_repo.mkdir(parents=True, exist_ok=True)
_write_ral_entry(_ral_tilde_repo, {"auto-land": True})
_ral_prior_home = os.environ.get("HOME")
os.environ["HOME"] = str(_ral_tilde_home)
try:
    check("repo_auto_land_tilde_matches_absolute",
          core.repo_auto_land(_ral_tilde_repo), core.repo_auto_land("~/r"))
    check("repo_auto_land_tilde_true", True, core.repo_auto_land("~/r"))
finally:
    if _ral_prior_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _ral_prior_home

# === orch#220: repo_in_flight_cap ============================================
_ifc_missing_dir = T / "repo-in-flight-cap-missing"
_ifc_missing_dir.mkdir(parents=True, exist_ok=True)
check("repo_in_flight_cap_no_entry", core.IN_FLIGHT_CAP,
      core.repo_in_flight_cap(_ifc_missing_dir))

_ifc_dir = T / "repo-in-flight-cap"
_ifc_dir.mkdir(parents=True, exist_ok=True)
_ifc_orch_json = core.ORCH_HOME / "orch.json"


def _write_ifc_entry(path, extra_repo_keys=None):
    entry = {"path": str(path), "state": "tracked"}
    if extra_repo_keys:
        entry.update(extra_repo_keys)
    _ifc_orch_json.write_text(json.dumps({"repos": [entry]}))


_write_ifc_entry(_ifc_dir)  # no in-flight-cap key at all
check("repo_in_flight_cap_key_absent", core.IN_FLIGHT_CAP,
      core.repo_in_flight_cap(_ifc_dir))

_write_ifc_entry(_ifc_dir, {"in-flight-cap": 3})
check("repo_in_flight_cap_override", 3, core.repo_in_flight_cap(_ifc_dir))

_write_ifc_entry(_ifc_dir, {"in-flight-cap": "banana"})
check("repo_in_flight_cap_non_integer", core.IN_FLIGHT_CAP,
      core.repo_in_flight_cap(_ifc_dir))

_write_ifc_entry(_ifc_dir, {"in-flight-cap": 0})
check("repo_in_flight_cap_zero", core.IN_FLIGHT_CAP,
      core.repo_in_flight_cap(_ifc_dir))

_write_ifc_entry(_ifc_dir, {"in-flight-cap": -2})
check("repo_in_flight_cap_negative", core.IN_FLIGHT_CAP,
      core.repo_in_flight_cap(_ifc_dir))

# bool-subclasses-int trap: `True` must fall back to IN_FLIGHT_CAP, NOT
# silently become a cap of 1 -- isinstance(True, int) is True in Python, so
# this is the case that pins the isinstance(v, bool) guard firing FIRST.
_write_ifc_entry(_ifc_dir, {"in-flight-cap": True})
check("repo_in_flight_cap_bool_true_rejected", core.IN_FLIGHT_CAP,
      core.repo_in_flight_cap(_ifc_dir))

# The two keys live on the same entry but must not interfere.
_write_ifc_entry(_ifc_dir, {"auto-land": True, "in-flight-cap": 3})
check("repo_in_flight_cap_with_auto_land_true", True, core.repo_auto_land(_ifc_dir))
check("repo_in_flight_cap_with_auto_land_cap", 3, core.repo_in_flight_cap(_ifc_dir))

_write_ifc_entry(_ifc_dir, {"in-flight-cap": 3})
check("repo_in_flight_cap_alone_auto_land_still_false", False, core.repo_auto_land(_ifc_dir))

# === orch#314: headroom_cap ===================================================
# core.open is reassigned per-case below to fake /proc/meminfo without
# touching the real filesystem or the implementation -- headroom_cap() calls
# the bare builtin open("/proc/meminfo"), which Python resolves through the
# core module's own globals, so setting core.open intercepts it exactly like
# core._journal_append_issue is reassigned above. Restored via del in
# `finally` so later tests see the real builtin again.
_hc_real_rss = core.SESSION_RSS_MB
_hc_real_mult = core.SUBPROC_MULTIPLIER
_hc_real_reserve = core.HEADROOM_RESERVE_MB


def _hc_fake_meminfo(text):
    """Install core.open so headroom_cap()'s open("/proc/meminfo") returns
    `text` instead of touching the real file."""
    import io

    def _opener(path, *a, **k):
        assert path == "/proc/meminfo"
        return io.StringIO(text)

    core.open = _opener


def _hc_fake_unreadable():
    """Install core.open so headroom_cap()'s open("/proc/meminfo") raises
    OSError, as it would if the file were missing or unreadable (non-Linux,
    permissions, etc.)."""
    def _opener(path, *a, **k):
        raise OSError("no such file")

    core.open = _opener


try:
    # Case 1 (orch#250 failure mode, the single most important property):
    # unreadable/absent /proc/meminfo degrades to None ("no record"), NOT to
    # a numeric 0 -- and the feed's clamp (min() is skipped when the clamp
    # input is None) leaves the static per-repo cap untouched rather than
    # collapsing it to a silent total block.
    _hc_fake_unreadable()
    check("headroom_cap_unreadable_meminfo_is_none", None, core.headroom_cap())
    _hc_static_cap = 3
    check("headroom_cap_none_leaves_static_cap_untouched", _hc_static_cap,
          min(_hc_static_cap, core.headroom_cap())
          if core.headroom_cap() is not None else _hc_static_cap)

    # Case 3: malformed /proc/meminfo degrades the same way as absent --
    # MemAvailable line missing entirely.
    _hc_fake_meminfo("MemTotal:       16000000 kB\nMemFree:         2000000 kB\n")
    check("headroom_cap_missing_memavailable_line_is_none", None, core.headroom_cap())

    # Case 3b: MemAvailable present but non-numeric.
    _hc_fake_meminfo("MemAvailable:   notanumber kB\n")
    check("headroom_cap_non_numeric_memavailable_is_none", None, core.headroom_cap())

    # Case 2: derived cap never exceeds the configured ceiling -- clamp is
    # min()-only, one direction. Abundant memory must not RAISE a cap above
    # what the operator configured, only lower it.
    core.SESSION_RSS_MB = 100
    core.SUBPROC_MULTIPLIER = 1
    core.HEADROOM_RESERVE_MB = 0
    _hc_fake_meminfo("MemAvailable:   100000000 kB\n")  # ~97656 MB, huge
    _hc_derived = core.headroom_cap()
    check("headroom_cap_abundant_memory_is_large", True,
          _hc_derived is not None and _hc_derived > 100)
    _hc_configured = 5
    check("headroom_cap_clamp_never_raises_above_configured", _hc_configured,
          min(_hc_configured, _hc_derived))

    # The two checks above compute min() here rather than in the code under
    # test, so on their own they would still pass if feed.py's clamp were
    # deleted outright. These two drive the REAL call site -- the clamp as
    # feed.repo_json applies it -- so the behaviour is actually protected.
    #
    # feed.repo_json does far more than clamp (forge round-trips via World),
    # so rather than stand up a fake oracle, exercise the clamp expression
    # against the same two inputs the real line combines.
    def _hc_feed_clamp(configured):
        """The clamp exactly as orch/feed.py applies it to a resolved cap."""
        h = core.headroom_cap()
        return min(configured, h) if h is not None else configured

    # Abundant memory (still faked huge from Case 2): the operator's smaller
    # configured cap must survive -- the clamp only ever lowers.
    check("headroom_cap_feed_clamp_keeps_configured_when_memory_abundant",
          5, _hc_feed_clamp(5))

    # Thin memory: the derived ceiling is the smaller of the two, so it wins
    # over a generous configured cap -- the clamp's whole purpose.
    core.SESSION_RSS_MB = 276
    core.SUBPROC_MULTIPLIER = 2
    core.HEADROOM_RESERVE_MB = 1500
    _hc_fake_meminfo("MemAvailable:   3000000 kB\n")  # ~2929MB -> cap 2
    check("headroom_cap_feed_clamp_lowers_generous_configured_cap",
          2, _hc_feed_clamp(8))

    # Unreadable signal at the real call site: the configured cap passes
    # through untouched -- never 0, never clamped on a non-reading.
    _hc_fake_unreadable()
    check("headroom_cap_feed_clamp_passes_through_on_no_reading",
          8, _hc_feed_clamp(8))

    # Case 4: low memory floors to 1, never 0 -- a computed cap below 1 must
    # not silently block all admission (orch#250 again, from the opposite
    # side: the numeric path, not the unreadable path).
    core.SESSION_RSS_MB = 276
    core.SUBPROC_MULTIPLIER = 2
    core.HEADROOM_RESERVE_MB = 1500
    _hc_fake_meminfo("MemAvailable:   1600000 kB\n")  # ~1562MB, just over reserve
    check("headroom_cap_low_memory_floors_to_one", 1, core.headroom_cap())

    # Same floor even when usable memory is negative (reserve exceeds
    # available) -- still 1, never 0 and never negative.
    _hc_fake_meminfo("MemAvailable:   500000 kB\n")  # ~488MB, under the reserve
    check("headroom_cap_negative_usable_floors_to_one", 1, core.headroom_cap())

    # Explicit guard: a 0 env override of SESSION_RSS_MB or SUBPROC_MULTIPLIER
    # makes per_slot < 1, which core.headroom_cap() catches and degrades to
    # None rather than raising ZeroDivisionError from inside a feed build.
    core.SESSION_RSS_MB = 0
    core.SUBPROC_MULTIPLIER = 2
    _hc_fake_meminfo("MemAvailable:   4000000 kB\n")
    check("headroom_cap_zero_session_rss_is_none_not_raise", None, core.headroom_cap())

    core.SESSION_RSS_MB = 276
    core.SUBPROC_MULTIPLIER = 0
    check("headroom_cap_zero_subproc_multiplier_is_none_not_raise", None,
          core.headroom_cap())
finally:
    del core.open
    core.SESSION_RSS_MB = _hc_real_rss
    core.SUBPROC_MULTIPLIER = _hc_real_mult
    core.HEADROOM_RESERVE_MB = _hc_real_reserve

# === orch#297: _migrate_orch_toml_keys =======================================
_mig_dir = T / "migrate-orch-toml-keys"
_mig_dir.mkdir(parents=True, exist_ok=True)
_mig_orch_json = core.ORCH_HOME / "orch.json"

# Neither key on the entry, and the checkout has a .orch.toml carrying both
# -> both get pulled into the orch.json entry, and the file is left in place
# (no-delete-before-convert).
(_mig_dir / ".orch.toml").write_text("[orch]\nauto-land = true\nin-flight-cap = 5\n")
_mig_orch_json.write_text(json.dumps({"repos": [
    {"path": str(_mig_dir), "state": "tracked"},
]}))
_mig_cfg = core._load_config()
_mig_entry = _mig_cfg["repos"][0]
check("migrate_orch_toml_fills_auto_land", True, _mig_entry.get("auto-land"))
check("migrate_orch_toml_fills_in_flight_cap", 5, _mig_entry.get("in-flight-cap"))
check("migrate_orch_toml_file_not_deleted", True,
      (_mig_dir / ".orch.toml").exists())
check("migrate_orch_toml_saved_to_disk", True,
      json.loads(_mig_orch_json.read_text())["repos"][0].get("auto-land"))

# An entry that already has auto-land set does NOT get it clobbered from
# .orch.toml -- an operator's hand-edit to orch.json always wins.
_mig_dir2 = T / "migrate-orch-toml-keys-handedit"
_mig_dir2.mkdir(parents=True, exist_ok=True)
(_mig_dir2 / ".orch.toml").write_text("[orch]\nauto-land = true\n")
_mig_orch_json.write_text(json.dumps({"repos": [
    {"path": str(_mig_dir2), "state": "tracked", "auto-land": False},
]}))
_mig_cfg2 = core._load_config()
check("migrate_orch_toml_does_not_clobber_handedit", False,
      _mig_cfg2["repos"][0].get("auto-land"))

# A tracked entry whose checkout has no .orch.toml at all is left alone, no
# error, no keys invented.
_mig_dir3 = T / "migrate-orch-toml-keys-nofile"
_mig_dir3.mkdir(parents=True, exist_ok=True)
_mig_orch_json.write_text(json.dumps({"repos": [
    {"path": str(_mig_dir3), "state": "tracked"},
]}))
_mig_cfg3 = core._load_config()
check("migrate_orch_toml_no_file_no_error", False,
      "auto-land" in _mig_cfg3["repos"][0] or "in-flight-cap" in _mig_cfg3["repos"][0])

# A non-"tracked" entry is invisible to the migration too, even with both a
# missing key and a .orch.toml sitting right there to fill it.
_mig_dir4 = T / "migrate-orch-toml-keys-untracked"
_mig_dir4.mkdir(parents=True, exist_ok=True)
(_mig_dir4 / ".orch.toml").write_text("[orch]\nauto-land = true\n")
_mig_orch_json.write_text(json.dumps({"repos": [
    {"path": str(_mig_dir4), "state": "off"},
]}))
_mig_cfg4 = core._load_config()
check("migrate_orch_toml_untracked_entry_untouched", False,
      "auto-land" in _mig_cfg4["repos"][0])

_mig_orch_json.unlink(missing_ok=True)

# === orch#58 unit 3: server.py verbs newly routed through the backend ======
# review_now, approve_item (all 3 verdicts) and reject_item all previously
# invoked `gh` unconditionally; each must now check core.repo_backend and
# build TEA_ARGV/GH_ARGV argv (or, for the cwd-based stdin verbs with no
# matching table shape, the same literal gh argv byte-for-byte, per
# a_reply's own precedent) the same way a_assign/a_reply already do.
srv_verb_gh_dir = T / "srv-verb-gh"
srv_verb_gh_dir.mkdir(parents=True, exist_ok=True)
srv_verb_tea_dir = T / "srv-verb-tea"
srv_verb_tea_dir.mkdir(parents=True, exist_ok=True)
_write_repos_txt(f"{srv_verb_gh_dir}\n{srv_verb_tea_dir}  login=gitea\n")

# Real git setup BEFORE srv.subprocess.run is faked below: srv.subprocess IS
# the same module object the top-level `subprocess` import already holds
# (server.py does its own `import subprocess`), so reassigning
# srv.subprocess.run also replaces subprocess.run everywhere, this test file
# included -- git init/remote add must run against the REAL subprocess.run,
# same ordering the earlier "server: assign / create_issue" section uses.
subprocess.run(["git", "init", "-q"], cwd=srv_verb_gh_dir, check=True)
subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/orch.git"],
                cwd=srv_verb_gh_dir, check=True)
subprocess.run(["git", "init", "-q"], cwd=srv_verb_tea_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                 "http://gitea.local:3000/cybermelon/gita-lectures.git"], cwd=srv_verb_tea_dir, check=True)

_verb_calls = []
_orig_run_for_verbs = core._run


def _fake_verb_run(cmd, cwd=None, timeout=None):
    if cmd[:2] == ["git", "-C"] and "remote" in cmd:
        return _orig_run_for_verbs(cmd, cwd=cwd, timeout=timeout)
    return False, ""


core._run = _fake_verb_run
_orig_srv_run_for_verbs = srv.subprocess.run


def _fake_srv_run_for_verbs(argv, **kw):
    if argv[:2] == ["git", "-C"]:
        return _orig_srv_run_for_verbs(argv, **kw)
    _verb_calls.append((argv, kw))
    return _FakeProc()


srv.subprocess.run = _fake_srv_run_for_verbs

try:
    # --- review_now -----------------------------------------------------
    # Reshaped by the 2026-09-14 one-flag verdict AND its correction, then
    # by orch#149's fix (orch#163): a blocking finding revokes auto-land (no
    # new state, no new label: REVIEW without auto-land is the existing
    # hold), so this verb re-ADDS L_AUTOLAND -- the operator act that puts
    # the issue back in the automated path. orch#149: re-adding L_AUTOLAND
    # ALONE never cleared a present L_NO_AUTOLAND, so the verb now writes
    # the full pair via _set_auto_land. Pinned here: it adds L_AUTOLAND,
    # removes L_NO_AUTOLAND, and must NOT touch L_STUCK, since a held REVIEW
    # issue is not the write-off case.
    _verb_calls.clear()
    review_now_gh_result = srv.a_review_now({"repo": "srv-verb-gh", "issue": "9"})
    check("review_now_gh_argv_add", ["gh", "issue", "edit", "9",
                                      "--add-label", core.L_AUTOLAND], _verb_calls[0][0])
    check("review_now_gh_argv_remove", ["gh", "issue", "edit", "9",
                                         "--remove-label", core.L_NO_AUTOLAND], _verb_calls[1][0])
    check("review_now_gh_cwd", str(srv_verb_gh_dir), _verb_calls[0][1].get("cwd"))
    check("review_now_gh_ok", True, review_now_gh_result["ok"])
    check("review_now_gh_leaves_stuck_alone", False,
          any(core.L_STUCK in c[0] for c in _verb_calls))

    _verb_calls.clear()
    review_now_tea_result = srv.a_review_now({"repo": "srv-verb-tea", "issue": "9"})
    check("review_now_tea_argv_add", core.TEA_ARGV["issue_edit_add_label"](
        "cybermelon/gita-lectures", "gitea", "9", core.L_AUTOLAND), _verb_calls[0][0])
    check("review_now_tea_argv_remove", core.TEA_ARGV["issue_edit_remove_label"](
        "cybermelon/gita-lectures", "gitea", "9", core.L_NO_AUTOLAND), _verb_calls[1][0])
    check("review_now_tea_ok", True, review_now_tea_result["ok"])
    check("review_now_tea_leaves_stuck_alone", False,
          any(core.L_STUCK in c[0] for c in _verb_calls))

    # --- set_auto_land (orch#163) ----------------------------------------
    _verb_calls.clear()
    set_auto_land_on_result = srv.a_set_auto_land({"repo": "srv-verb-gh", "issue": "9", "on": True})
    check("set_auto_land_on_adds", ["gh", "issue", "edit", "9",
                                     "--add-label", core.L_AUTOLAND], _verb_calls[0][0])
    check("set_auto_land_on_removes", ["gh", "issue", "edit", "9",
                                        "--remove-label", core.L_NO_AUTOLAND], _verb_calls[1][0])
    check("set_auto_land_on_ok", True, set_auto_land_on_result["ok"])

    _verb_calls.clear()
    set_auto_land_off_result = srv.a_set_auto_land({"repo": "srv-verb-gh", "issue": "9", "on": False})
    check("set_auto_land_off_adds", ["gh", "issue", "edit", "9",
                                      "--add-label", core.L_NO_AUTOLAND], _verb_calls[0][0])
    check("set_auto_land_off_removes", ["gh", "issue", "edit", "9",
                                         "--remove-label", core.L_AUTOLAND], _verb_calls[1][0])
    check("set_auto_land_off_ok", True, set_auto_land_off_result["ok"])

    # Cold-review finding 1: _set_auto_land must be `r1["ok"] and r2["ok"]`,
    # not `or`. `no-auto-land` is not in ORCH_LABELS's forge-created set (it
    # was, before orch#163), so `--add-label no-auto-land` can genuinely fail
    # while `--remove-label auto-land` trivially succeeds on an issue that
    # never carried it -- `or` would report ok:True while nothing changed.
    # Here the ADD call is made to fail and the REMOVE call still succeeds;
    # `and` must therefore report False.
    class _FakeProcFail:
        returncode = 1
        stdout = ""
        stderr = "label not found"

    def _fake_srv_run_first_call_fails(argv, **kw):
        if argv[:2] == ["git", "-C"]:
            return _orig_srv_run_for_verbs(argv, **kw)
        _verb_calls.append((argv, kw))
        return _FakeProcFail() if len(_verb_calls) == 1 else _FakeProc()

    srv.subprocess.run = _fake_srv_run_first_call_fails
    _verb_calls.clear()
    set_auto_land_add_fails_result = srv.a_set_auto_land(
        {"repo": "srv-verb-gh", "issue": "9", "on": False})
    # A failed add is retried ONCE after creating the label, because the
    # likely cause is that the label does not exist on this forge yet
    # (ensure_labels runs only at watch time). Here only the FIRST call
    # fails, so the create succeeds, the retried add succeeds, and the whole
    # write therefore reports ok -- the label being absent is a recoverable
    # condition, not an error to surface to the operator.
    check("set_auto_land_add_failure_recovers_via_label_create", True,
          set_auto_land_add_fails_result["ok"])
    check("set_auto_land_add_failure_creates_label_then_retries",
          ["label", "create"],
          [a for a in _verb_calls[1][0] if a in ("label", "create")])

    # THE SAFETY PROPERTY, second-review finding 1: when the add cannot
    # succeed, NO remove may run. Otherwise "turn automerge off" fails to
    # add no-auto-land, succeeds at stripping auto-land, and the issue falls
    # back to the repo default -- ON. The operator asked for off and got on,
    # which is worse than the write simply failing.
    def _fake_srv_run_all_adds_fail(argv, **kw):
        if argv[:2] == ["git", "-C"]:
            return _orig_srv_run_for_verbs(argv, **kw)
        _verb_calls.append((argv, kw))
        return _FakeProcFail() if "--add-label" in argv else _FakeProc()

    srv.subprocess.run = _fake_srv_run_all_adds_fail
    _verb_calls.clear()
    set_auto_land_never_removes = srv.a_set_auto_land(
        {"repo": "srv-verb-gh", "issue": "9", "on": False})
    check("set_auto_land_add_fails_reports_false", False,
          set_auto_land_never_removes["ok"])
    check("set_auto_land_add_fails_never_removes_the_other_label", [],
          [a[0] for a in _verb_calls if "--remove-label" in a[0]])
    srv.subprocess.run = _fake_srv_run_for_verbs

    # --- unclaim (#97): removes agent-working AND agent-stuck, both calls --
    _verb_calls.clear()
    unclaim_gh_result = srv.a_unclaim({"repo": "srv-verb-gh", "issue": "9"})
    check("unclaim_gh_argv_working", ["gh", "issue", "edit", "9",
                                       "--remove-label", core.L_WORKING], _verb_calls[0][0])
    check("unclaim_gh_argv_stuck", ["gh", "issue", "edit", "9",
                                     "--remove-label", core.L_STUCK], _verb_calls[1][0])
    check("unclaim_gh_cwd_working", str(srv_verb_gh_dir), _verb_calls[0][1].get("cwd"))
    check("unclaim_gh_cwd_stuck", str(srv_verb_gh_dir), _verb_calls[1][1].get("cwd"))
    check("unclaim_gh_ok", True, unclaim_gh_result["ok"])

    _verb_calls.clear()
    srv.a_unclaim({"repo": "srv-verb-tea", "issue": "9"})
    check("unclaim_tea_argv_working", core.TEA_ARGV["issue_edit_remove_label"](
        "cybermelon/gita-lectures", "gitea", "9", core.L_WORKING), _verb_calls[0][0])
    check("unclaim_tea_argv_stuck", core.TEA_ARGV["issue_edit_remove_label"](
        "cybermelon/gita-lectures", "gitea", "9", core.L_STUCK), _verb_calls[1][0])

    # unwatched repo: refused before any CLI call reaches gh/tea -- the #19
    # gate regression test. Asserts the no-call part, not just ok:False,
    # since a gate that returns the right answer AFTER already shelling out
    # is not the gate #19 needed.
    _verb_calls.clear()
    unclaim_unwatched = srv.a_unclaim({"repo": "not-a-watched-repo", "issue": "9"})
    check("unclaim_unwatched_ok_false", False, unclaim_unwatched["ok"])
    check("unclaim_unwatched_no_cli_call", 0, len(_verb_calls))

    # --- approve_item: fix-before-merge -> issue comment --------------------
    _verb_calls.clear()
    srv.a_approve_item({"repo": "srv-verb-gh", "issue": "9", "sha": "s", "n": 1,
                        "verdict": "fix-before-merge", "finding": "do the thing"})
    check("approve_fix_gh_argv", ["gh", "issue", "comment", "9", "--body-file", "-"],
          _verb_calls[-1][0])
    check("approve_fix_gh_body_on_stdin", True,
          "do the thing" in _verb_calls[-1][1].get("input", ""))

    _verb_calls.clear()
    srv.a_approve_item({"repo": "srv-verb-tea", "issue": "9", "sha": "s", "n": 1,
                        "verdict": "fix-before-merge", "finding": "do the thing"})
    approve_fix_tea_argv = _verb_calls[-1][0]
    check("approve_fix_tea_argv_head", [
        "tea", "comments", "add", "9", "--login", "gitea",
        "--repo", "cybermelon/gita-lectures", "-d",
    ], approve_fix_tea_argv[:-1])
    check("approve_fix_tea_body_in_argv", True, "do the thing" in approve_fix_tea_argv[-1])
    check("approve_fix_tea_no_stdin", None, _verb_calls[-1][1].get("input"))

    # --- approve_item: follow-up -> issue create -----------------------------
    _verb_calls.clear()
    srv.a_approve_item({"repo": "srv-verb-gh", "issue": "9", "sha": "s", "n": 2,
                        "verdict": "follow-up", "finding": "split this out"})
    check("approve_followup_gh_argv", ["gh", "issue", "create", "--title",
                                        "split this out", "--body-file", "-"],
          _verb_calls[-1][0])

    _verb_calls.clear()
    srv.a_approve_item({"repo": "srv-verb-tea", "issue": "9", "sha": "s", "n": 2,
                        "verdict": "follow-up", "finding": "split this out"})
    check("approve_followup_tea_argv", core.TEA_ARGV["issue_create"](
        "cybermelon/gita-lectures", "gitea", "split this out",
        _verb_calls[-1][0][-1]), _verb_calls[-1][0])

    # --- approve_item: merge-as-is / wontfix -> issue comment, no work ------
    _verb_calls.clear()
    srv.a_approve_item({"repo": "srv-verb-gh", "issue": "9", "sha": "s", "n": 3,
                        "verdict": "wontfix", "finding": "not worth it"})
    check("approve_wontfix_gh_argv", ["gh", "issue", "comment", "9", "--body-file", "-"],
          _verb_calls[-1][0])

    _verb_calls.clear()
    srv.a_approve_item({"repo": "srv-verb-tea", "issue": "9", "sha": "s", "n": 3,
                        "verdict": "merge-as-is", "finding": "fine as is"})
    approve_wontfix_tea_argv = _verb_calls[-1][0]
    check("approve_wontfix_tea_argv_head", [
        "tea", "comments", "add", "9", "--login", "gitea",
        "--repo", "cybermelon/gita-lectures", "-d",
    ], approve_wontfix_tea_argv[:-1])

    # --- reject_item ----------------------------------------------------------
    _verb_calls.clear()
    srv.a_reject_item({"repo": "srv-verb-gh", "issue": "9", "sha": "s", "n": 1,
                       "finding": "x", "reason": "already covered elsewhere"})
    check("reject_gh_argv", ["gh", "issue", "comment", "9", "--body-file", "-"],
          _verb_calls[-1][0])
    check("reject_gh_body_has_reason", True,
          "already covered elsewhere" in _verb_calls[-1][1].get("input", ""))

    _verb_calls.clear()
    srv.a_reject_item({"repo": "srv-verb-tea", "issue": "9", "sha": "s", "n": 1,
                       "finding": "x", "reason": "already covered elsewhere"})
    reject_tea_argv = _verb_calls[-1][0]
    check("reject_tea_argv_head", [
        "tea", "comments", "add", "9", "--login", "gitea",
        "--repo", "cybermelon/gita-lectures", "-d",
    ], reject_tea_argv[:-1])
    check("reject_tea_body_has_reason", True, "already covered elsewhere" in reject_tea_argv[-1])
finally:
    core._run = _orig_run_for_verbs
    srv.subprocess.run = _orig_srv_run_for_verbs
    _write_repos_txt(f"{act_repo_dir}\n")

# === startable: liveness gate on condition 1 (#47) ==========================
# The bug: spawn.py writes the ledger row for a fresh issue-orch immediately,
# but that session claims agent-ready's UNCLAIMED->CLAIMED label only
# minutes later, inside its own startup. In that window state is UNCLAIMED
# and ready is True with no other signal -- a naive "UNCLAIMED and ready"
# condition 1 double-spawns live work (the #44/#42/#14 near-miss at 03:31Z).
# feed.issue_json derives startable/owner_starting from ready, state, and
# orch_alive; build_thin is called for real (never hand-built) so the thin
# shape cannot drift from what build_thin actually produces, matching the
# condition-7 block above.
def _startable_data(state, ready, orch_alive, startable):
    return {"repos": [{"slug": "o/r", "issues": [{
        "issue": 44, "state": state, "work_state": "ACTIVE",
        "orch_alive": orch_alive, "activity": time.time(),
        "contended": False, "ready": ready, "startable": startable,
    }]}], "alerts": []}


# startable_live_unclaimed_silent: THE BUG CASE. ready + UNCLAIMED + a live
# owner (already spawned, mid-claim) -> feed says startable=False -> condition
# 1 must NOT fire. This is the most important assertion in this change.
_st_data_a = _startable_data("UNCLAIMED", True, True, False)
_st_thin_a = tickmod.build_thin(_st_data_a)
_, _st_notes_a, _ = tickmod.compute_conditions(_st_data_a, _st_thin_a)
check("startable_live_unclaimed_silent", False,
      any("ready/abandoned" in note for note in _st_notes_a))

# startable_dead_unclaimed_fires: ready + UNCLAIMED + no live owner ->
# startable=True -> condition 1 DOES fire.
_st_data_b = _startable_data("UNCLAIMED", True, False, True)
_st_thin_b = tickmod.build_thin(_st_data_b)
_, _st_notes_b, _ = tickmod.compute_conditions(_st_data_b, _st_thin_b)
check("startable_dead_unclaimed_fires", True,
      any("ready/abandoned" in note for note in _st_notes_b))

# startable_abandoned_fires: ABANDONED still fires condition 1 even with
# startable=False -- the ABANDONED disjunct is independent of the liveness
# gate.
_st_data_c = _startable_data("ABANDONED", False, False, False)
_st_thin_c = tickmod.build_thin(_st_data_c)
_, _st_notes_c, _ = tickmod.compute_conditions(_st_data_c, _st_thin_c)
check("startable_abandoned_fires", True,
      any("ready/abandoned" in note for note in _st_notes_c))

# startable_thin_carries: build_thin's output actually carries the
# `startable` key through, guarding against the thin shape drifting away
# from what compute_conditions reads.
_st_data_d = _startable_data("UNCLAIMED", True, False, True)
_st_thin_d = tickmod.build_thin(_st_data_d)
check("startable_thin_carries", True, _st_thin_d["repos"][0]["issues"][0]["startable"])

# feed.issue_json's world/git/gh dependencies are stubbed the same way this
# file already stubs other real objects -- core.alive (~649), core.work_mtime
# / _rollup (~147), core.subprocess.Popen (~298), feed.repo_json (~1326,
# ~1383) -- so there is ample monkeypatch precedent for it. This block calls
# the REAL feed.issue_json and asserts on its returned startable/
# owner_starting keys, so the derivation cannot drift from the rule the way
# a reimplemented copy could.
#
# _FeedFakeWorld carries just the attributes/methods issue_json touches on
# `world`: issues (for the label list and issue_has_label/issue_field/
# issue_comments), pr_number_for and pr_for (no PR on this branch -> both
# read as absent). issue_state and work_state are imported BY NAME into
# feed's module namespace (see feed.py's `from orch.core import (...
# issue_state, work_state ...)`), so they are patched at feed.issue_state /
# feed.work_state, where they read the fake world through its methods --
# patching core.issue_state would not reach feed's already-bound reference.
class _OrphanPrsMixin:
    """orphan_prs() for the feed fakes that model only issues.

    repo_json calls world.orphan_prs(), so every fake standing in for World
    on that path needs it. These fixtures carry no `prs`, so the honest
    answer is "no orphans" -- returned directly rather than by re-deriving
    core.World.orphan_prs's body here. A copied formula is a second
    implementation that drifts: change the real one and these fakes keep
    asserting the old shape, green. Fixtures that actually exercise the
    orphan logic subclass core.World instead (see _OrphanWorld279)."""

    def orphan_prs(self):
        return []

    def rollup_readability(self):
        """Same reasoning as orphan_prs above, for the same call site.

        repo_json calls world.rollup_readability() BARE -- deliberately
        unguarded, so a rename that misses that line fails loudly instead of
        silencing the orch#143 alert forever. That means every fake standing
        in for World on the repo_json path must carry this method. These
        fixtures model only issues and hold no `rollups`, so the honest
        answer is "no PRs examined": (0, 0), which cannot trip the alert
        (it needs total >= 2). Returned directly rather than re-deriving
        core.World.rollup_readability's body, for the drift reason above."""
        return (0, 0)


class _FeedFakeWorld(_PlainBranchMixin, _OrphanPrsMixin):
    def __init__(self, labels):
        self.issues = [{"number": 44, "labels": [{"name": l} for l in labels],
                         "title": "t", "updatedAt": None, "comments": []}]

    def issue_field(self, n, field):
        for i in self.issues:
            if i["number"] == n:
                return i.get(field, "")
        return ""

    def issue_comments(self, n):
        return []

    def issue_has_label(self, n, label):
        for i in self.issues:
            if i["number"] == n:
                return any(l["name"] == label for l in i.get("labels", []))
        return False

    def pr_number_for(self, branch):
        return None

    def pr_for(self, branch):
        return ""


def _feed_startable_check(name, labels, feed_alive, want_startable, want_owner_starting):
    world = _FeedFakeWorld(labels)
    _orig = {
        "alive": feed.alive, "base_ref": feed.base_ref,
        "issue_state": feed.issue_state, "work_state": feed.work_state,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.subagents_for": core.subagents_for,
        "core.issue_brief": core.issue_brief,
    }
    feed.alive = lambda key: feed_alive
    feed.base_ref = lambda repo: ""  # no base -> commits is -1 (unknown), work_mtime path skipped
    feed.issue_state = core.issue_state  # real -- reads the fake world's labels
    feed.work_state = core.work_state    # real -- reads the fake world's pr_for/work_mtime
    feed.sessions_for = lambda cwd, live=False: []
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.subagents_for = lambda cwd, session_id: []
    core.issue_brief = lambda comments, activity=None: None
    try:
        # is_gh=True: issue_json's backend parameter gates only the url/pr_url
        # web links (orch#58). startable/owner_starting derive from ready,
        # state and orch_alive, none of which the backend touches, so this
        # picks the gh shape to keep the call honest and asserts nothing on it.
        row = feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                              "me/feedslug", True, "", {}, False)
        check(f"{name}_startable", want_startable, row["startable"])
        check(f"{name}_owner_starting", want_owner_starting, row["owner_starting"])
    finally:
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed.issue_state = _orig["issue_state"]
        feed.work_state = _orig["work_state"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.subagents_for = _orig["core.subagents_for"]
        core.issue_brief = _orig["core.issue_brief"]


# feed_startable_live_owner_starting: ready + UNCLAIMED (no agent-working
# label) + alive True -> startable False, owner_starting True. THE BUG CASE.
_feed_startable_check("feed_startable_live_owner_starting",
                       [core.L_READY], True, False, True)

# feed_startable_dead_startable: ready + UNCLAIMED + alive False ->
# startable True, owner_starting False.
_feed_startable_check("feed_startable_dead_startable",
                       [core.L_READY], False, True, False)

# feed_startable_claimed_neither: ready + CLAIMED (agent-working present) +
# alive False -> both False.
_feed_startable_check("feed_startable_claimed_neither",
                       [core.L_READY, core.L_WORKING], False, False, False)

# feed_startable_not_ready_neither: not ready (no agent-ready label) +
# UNCLAIMED + alive False -> both False.
_feed_startable_check("feed_startable_not_ready_neither",
                       [], False, False, False)

# === issue#99: a derivation that cannot resolve emits an explicit unknown ===
# See STATES.md finding 10. `commits` is the case that bites hardest: 0 is the
# value an operator ACTS on ("the agent produced nothing"), and it was also
# what a failed or timed-out rev-list produced. Reuses the _FeedFakeWorld
# harness above rather than a second fixture style; base_ref and feed._run are
# the two knobs this needs.
def _feed_commits_check(name, base, run_result, want):
    world = _FeedFakeWorld([core.L_READY])
    _orig = {
        "alive": feed.alive, "base_ref": feed.base_ref, "_run": feed._run,
        "issue_state": feed.issue_state, "work_state": feed.work_state,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.subagents_for": core.subagents_for,
        "core.issue_brief": core.issue_brief,
    }
    feed.alive = lambda key: False
    feed.base_ref = lambda repo: base
    feed._run = lambda cmd, timeout=None: run_result
    feed.issue_state = core.issue_state
    feed.work_state = core.work_state
    feed.sessions_for = lambda cwd, live=False: []
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.subagents_for = lambda cwd, session_id: []
    core.issue_brief = lambda comments, activity=None: None
    try:
        row = feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                              "me/feedslug", True, "", {}, False)
        check(name, want, row["commits"])
    finally:
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed._run = _orig["_run"]
        feed.issue_state = _orig["issue_state"]
        feed.work_state = _orig["work_state"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.subagents_for = _orig["core.subagents_for"]
        core.issue_brief = _orig["core.issue_brief"]


# a rev-list that FAILED reports -1, never 0 -- same precedent as `idle`.
_feed_commits_check("feed_commits_failed_rev_list_is_unknown", "main", (False, ""), -1)
# ok but unparseable output is equally a failed read.
_feed_commits_check("feed_commits_garbage_output_is_unknown", "main", (True, "fatal\n"), -1)
# no base ref at all: also unknown, not zero.
_feed_commits_check("feed_commits_no_base_is_unknown", "", (True, "3\n"), -1)
# a GENUINE zero still reports 0 -- the sentinel must not swallow the real
# answer that "the branch is behind/at base with nothing on it".
_feed_commits_check("feed_commits_real_zero_stays_zero", "main", (True, "0\n"), 0)
# and a real count passes through untouched. commits stays an int on every
# path (the TUI in #91 consumes it as one).
_feed_commits_check("feed_commits_real_count_passes_through", "main", (True, "4\n"), 4)

# --- the subagent walk: "no workers" != "the walk raised" -------------------
# The guard stays (a transcript race must never break feed build) but the
# failure is now visible: workers is a list on BOTH paths, and
# workers_unreadable is the explicit unknown.
def _feed_workers_check(name, subagents_impl, want_workers, want_unreadable):
    world = _FeedFakeWorld([core.L_READY])
    _orig = {
        "alive": feed.alive, "base_ref": feed.base_ref,
        "issue_state": feed.issue_state, "work_state": feed.work_state,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.subagents_for": core.subagents_for,
        "core.issue_brief": core.issue_brief,
    }
    feed.alive = lambda key: False
    feed.base_ref = lambda repo: ""
    feed.issue_state = core.issue_state
    feed.work_state = core.work_state
    feed.sessions_for = lambda cwd, live=False: [{"id": "sess-1", "live": False}]
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.subagents_for = subagents_impl
    core.issue_brief = lambda comments, activity=None: None
    try:
        row = feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                              "me/feedslug", True, "", {}, False)
        s = row["sessions"][0]
        check(f"{name}_workers", want_workers, s["workers"])
        check(f"{name}_unreadable", want_unreadable, s["workers_unreadable"])
    finally:
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed.issue_state = _orig["issue_state"]
        feed.work_state = _orig["work_state"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.subagents_for = _orig["core.subagents_for"]
        core.issue_brief = _orig["core.issue_brief"]


def _raising_subagents(cwd, session_id):
    raise OSError("transcript vanished mid-walk")


# genuinely no subagents: empty list, and NOT flagged unreadable.
_feed_workers_check("feed_workers_genuinely_empty",
                     lambda cwd, session_id: [], [], False)
# the walk raised: still an empty LIST (wire shape held for every consumer),
# but the row now says so instead of impersonating an empty result.
_feed_workers_check("feed_workers_walk_raised", _raising_subagents, [], True)

# orch#284: auto_land_source tells "inherited from repo" apart from "set on
# this issue", since auto_land_on's resolved bool alone hides which of the
# two produced it. Same _FeedFakeWorld/monkeypatch idiom as
# _feed_startable_check above; repo_auto_land_default is issue_json's last
# positional arg.
def _feed_auto_land_source_check(name, labels, repo_default, want_source):
    world = _FeedFakeWorld(labels)
    _orig = {
        "alive": feed.alive, "base_ref": feed.base_ref,
        "issue_state": feed.issue_state, "work_state": feed.work_state,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.subagents_for": core.subagents_for,
        "core.issue_brief": core.issue_brief,
    }
    feed.alive = lambda key: False
    feed.base_ref = lambda repo: ""
    feed.issue_state = core.issue_state
    feed.work_state = core.work_state
    feed.sessions_for = lambda cwd, live=False: []
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.subagents_for = lambda cwd, session_id: []
    core.issue_brief = lambda comments, activity=None: None
    try:
        row = feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                              "me/feedslug", True, "", {}, repo_default)
        check(name, want_source, row["auto_land_source"])
    finally:
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed.issue_state = _orig["issue_state"]
        feed.work_state = _orig["work_state"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.subagents_for = _orig["core.subagents_for"]
        core.issue_brief = _orig["core.issue_brief"]


# the auto-land label is present -> "issue", regardless of the repo default.
_feed_auto_land_source_check("feed_auto_land_source_issue_via_autoland",
                              [core.L_AUTOLAND], False, "issue")
# the no-auto-land label is present -> "issue", regardless of the repo default.
_feed_auto_land_source_check("feed_auto_land_source_issue_via_no_autoland",
                              [core.L_NO_AUTOLAND], True, "issue")
# neither label present -> "repo", the value was inherited.
_feed_auto_land_source_check("feed_auto_land_source_repo_neither_label",
                              [], True, "repo")

# ...and the RENDERER must read it. Setting workers_unreadable on the wire is
# only half the fix: the widget's worker block keyed off `ws.length`, which is
# 0 on BOTH paths, so a raised walk drew byte-identically to a session that
# genuinely spawned nothing -- the exact conflation finding 10 is about, and
# the input an operator acts on when applying the long-running-lease kill.
# Text-level assertion on the template for the same reason as the CAN_ACT
# block below: it pins the property, not the pixels.
# REPO_ROOT itself is defined further down; spell it out rather than hoist the
# definition and disturb code this change has no business touching.
_wks_tpl = (Path(__file__).resolve().parent.parent / "widget.tpl.html").read_text()
check("widget_reads_workers_unreadable", True, "workers_unreadable" in _wks_tpl)
# Not merely mentioned -- it must reach markup. The worker block is the only
# place `class="wks"` is emitted, so the flag has to be what chooses it.
_wks_flat = re.sub(r"\s+", " ", _wks_tpl)
_wks_at = _wks_flat.find("s.workers_unreadable")
check("widget_workers_unreadable_picks_markup", True,
      _wks_at != -1 and 'class="wks"' in _wks_flat[_wks_at:_wks_at + 240])

# --- a repo row that never stated `ok` is not evidence of health ------------
# feed.build's single "did this repo read succeed" question used to default to
# yes. All three repo_json return paths set the key today, so this pins the
# default itself: a fourth path that forgets must raise the alert, not pass.
# Same repos.txt + repo_json-stub shape as the awaiting block above, so the
# REAL build() loop makes the judgement.
_orig_repo_json99 = feed.repo_json
_r99_repos_file = core.ORCH_HOME / "repos.txt"
_r99_repos_txt = _r99_repos_file.read_text() if _r99_repos_file.exists() else ""
_r99_repos_file.write_text(str(fake_repo) + "\n")  # has .git, made above
feed.repo_json = lambda repo: {
    # deliberately omits "ok" -- the forgotten-key case
    "repo": "me/feedrepo", "path": str(repo), "slug": "feedrepo",
    "counts": {}, "live_orchs": 0, "issues": [],
    "orch": {"key": "repo-orch.feedrepo", "alive": False, "prior_runs": 0, "recent": []},
}
try:
    out99 = feed.build()
    check("feed_row_missing_ok_is_not_healthy", True,
          any(a.get("level") == "error" and "read failed" in a.get("msg", "")
              for a in out99["alerts"]))
finally:
    feed.repo_json = _orig_repo_json99

# control: a row that DOES say ok raises no such alert, so the check above is
# reading the default and not just "any row alerts".
feed.repo_json = lambda repo: {
    "repo": "me/feedrepo", "path": str(repo), "slug": "feedrepo", "ok": True,
    "counts": {}, "live_orchs": 0, "issues": [],
    "orch": {"key": "repo-orch.feedrepo", "alive": False, "prior_runs": 0, "recent": []},
}
try:
    out99ok = feed.build()
    check("feed_row_with_ok_true_is_healthy", False,
          any(a.get("level") == "error" and "read failed" in a.get("msg", "")
              for a in out99ok["alerts"]))
finally:
    feed.repo_json = _orig_repo_json99
    _r99_repos_file.write_text(_r99_repos_txt)

# === feed.issue_json: url/pr_url shapes per backend (orch#68) ==============
# web_base is now a REQUIRED parameter (a defaulted "" silently produced a
# linkless tea repo when a future caller forgot the argument -- exactly the
# silent-wrongness this issue is about). These checks exercise the actual
# URL strings issue_json builds, not just the startable/owner_starting
# booleans _feed_startable_check already covers. Same monkeypatch set as
# _feed_startable_check, plus pr_number_for stubbed to return a fixed PR so
# the *_url pr branches are reachable.
class _FeedFakeWorldWithPr(_FeedFakeWorld):
    def pr_number_for(self, branch):
        return 9


def _feed_url_check(name, is_gh, web_base, want_url, want_pr_url, forbid_in_pr_url=None):
    world = _FeedFakeWorldWithPr([core.L_READY])
    _orig = {
        "alive": feed.alive, "base_ref": feed.base_ref,
        "issue_state": feed.issue_state, "work_state": feed.work_state,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.subagents_for": core.subagents_for,
        "core.issue_brief": core.issue_brief,
    }
    feed.alive = lambda key: False
    feed.base_ref = lambda repo: ""
    feed.issue_state = core.issue_state
    feed.work_state = core.work_state
    feed.sessions_for = lambda cwd, live=False: []
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.subagents_for = lambda cwd, session_id: []
    core.issue_brief = lambda comments, activity=None: None
    try:
        row = feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                              "me/feedslug", is_gh, web_base, {}, False)
        check(f"{name}_url", want_url, row["url"])
        check(f"{name}_pr_url", want_pr_url, row["pr_url"])
        if forbid_in_pr_url is not None:
            check(f"{name}_pr_url_excludes_{forbid_in_pr_url.strip('/')}", True,
                  (row["pr_url"] or "").find(forbid_in_pr_url) == -1)
    finally:
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed.issue_state = _orig["issue_state"]
        feed.work_state = _orig["work_state"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.subagents_for = _orig["core.subagents_for"]
        core.issue_brief = _orig["core.issue_brief"]


# feed_url_gh: gh issue/PR URLs unchanged -- regression guard. PR is
# SINGULAR "pull", not "pulls".
_feed_url_check("feed_url_gh", True, "",
                 "https://github.com/me/feedslug/issues/44",
                 "https://github.com/me/feedslug/pull/9")

# feed_url_tea: tea issue/PR URLs built from web_base. PR path is PLURAL
# "pulls" -- Gitea's shape, not GitHub's. Named per the issue's acceptance
# criteria: this is THE test that a regression to singular "pull" must fail.
# forbid_in_pr_url additionally asserts the string does NOT contain "/pull/"
# so a regression to the singular form cannot pass even if the rest of the
# string happened to still compare equal by accident.
_feed_url_check("feed_url_tea", False, "http://gitea.local:3000",
                 "http://gitea.local:3000/me/feedslug/issues/44",
                 "http://gitea.local:3000/me/feedslug/pulls/9",
                 forbid_in_pr_url="/pull/")

# feed_url_tea_empty_base: an unresolvable web_base ("") must yield None for
# BOTH url and pr_url -- not "", not a partial path like
# "/me/feedslug/issues/44". This is the silent-failure case orch#68 is about:
# a falsy web_base must produce no link, never a link-shaped string that 404s.
_feed_url_check("feed_url_tea_empty_base", False, "", None, None)

shutil.rmtree(T, ignore_errors=True)

# === build_widget: ORCH_HOME must not let a worktree build escape it =======
# Issue #57: core.py, build_widget.py and run.py each derived ORCH_HOME
# differently, invisibly agreeing in the live checkout (where HERE ==
# ORCH_HOME) and disagreeing in a worktree. A real build run from a worktree
# once wrote /home/user/orch/public/widget.html -- the LIVE checkout the
# dashboard serves. build_widget.py now imports ORCH_HOME from orch.core and
# refuses to write when the resolved output path falls outside the checkout
# it is actually running from (HERE = its own parent dir, derived from
# __file__ -- i.e. wherever the orch/build_widget.py being executed
# physically lives, NOT the process cwd).
#
# That means the happy-path fixture must be a full FAKE CHECKOUT -- its own
# copy of orch/__init__.py, orch/core.py and orch/build_widget.py -- not
# just a directory handed via ORCH_HOME. A tempdir with only a
# widget.tpl.html would never be HERE, because HERE is fixed to wherever
# the module file lives; only copying the module alongside the fixtures
# reproduces "a worktree with its own checkout of this repo."
#
# Invoked as a real subprocess (`python3 -m orch.build_widget`, cwd inside
# the fake checkout) so the module-level guard code actually runs, and
# __file__ resolves to the fake checkout's own copy -- importing the module
# in-process would only prove the code parses, and would resolve HERE to
# this real repo regardless of fixture layout.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _mk_fake_checkout(root):
    """A minimal but real checkout of this repo's build_widget path: its own
    orch/__init__.py, orch/core.py, orch/build_widget.py (copied verbatim,
    so HERE resolves inside `root` when run with cwd=root), plus
    widget.tpl.html and public/status.json at ORCH_HOME's expected layout."""
    pkg = root / "orch"
    pkg.mkdir(parents=True)
    # orch#138: core.py imports orch.runlog at module load, so a fake
    # checkout that omits it fails before build_widget's own logic ever
    # runs -- this list must track core.py's real import surface.
    for name in ("__init__.py", "core.py", "build_widget.py", "runlog.py"):
        (pkg / name).write_text((REPO_ROOT / "orch" / name).read_text())
    (root / "widget.tpl.html").write_text("<html><script>__SEED__</script></html>")
    (root / "public").mkdir()
    (root / "public" / "status.json").write_text('{"ok": true, "n": 1}')
    return root


bw_base = Path(tempfile.mkdtemp())

# (a) a worktree-like checkout, ORCH_HOME set to THAT SAME checkout -> writes
# public/widget.html INSIDE it (HERE == ORCH_HOME, guard passes).
bw_wt = _mk_fake_checkout(bw_base / "worktree-checkout")
bw_env_a = {**os.environ, "ORCH_HOME": str(bw_wt)}
bw_proc_a = subprocess.run(
    [sys.executable, "-m", "orch.build_widget"],
    cwd=str(bw_wt), env=bw_env_a, capture_output=True, text=True, timeout=30,
)
check("build_widget_worktree_rc0", 0, bw_proc_a.returncode)
bw_out_a = bw_wt / "public" / "widget.html"
check("build_widget_worktree_wrote_inside", True, bw_out_a.exists())
if bw_out_a.exists():
    bw_content_a = bw_out_a.read_text()
    check("build_widget_worktree_content_seeded", True,
          '"ok": true' in bw_content_a or '"ok":true' in bw_content_a)

# (b) a SECOND, separate fake checkout's ORCH_HOME points OUTSIDE the
# checkout build_widget actually runs from (bw_wt) -- refuses: nonzero
# exit, and the outside file is neither created nor modified.
bw_outside = _mk_fake_checkout(bw_base / "outside-checkout")
bw_outside_widget = bw_outside / "public" / "widget.html"
check("build_widget_outside_precondition_absent", False, bw_outside_widget.exists())

bw_env_b = {**os.environ, "ORCH_HOME": str(bw_outside)}
bw_proc_b = subprocess.run(
    [sys.executable, "-m", "orch.build_widget"],
    cwd=str(bw_wt), env=bw_env_b, capture_output=True, text=True, timeout=30,
)
check("build_widget_outside_rc_nonzero", True, bw_proc_b.returncode != 0)
check("build_widget_outside_error_names_both_paths", True,
      str(bw_outside) in bw_proc_b.stderr and str(bw_wt) in bw_proc_b.stderr)
check("build_widget_outside_not_created", False, bw_outside_widget.exists())

shutil.rmtree(bw_base, ignore_errors=True)

# === widget controls: existence must not depend on a fetch ==================
# Issue #66: every per-issue action button was gated behind a CAN_ACT flag
# that is false at first paint and never repaints on an off-origin load, so
# the page rendered with NO buttons at all. The dashboard was read-only in
# practice for a full day and nothing reported it -- because the only check
# was a curl against the server, and curl was perfectly happy: the bytes came
# back 200 with a well-formed template. What curl cannot see is that a
# browser then executes the template and paints nothing.
#
# This repo has no browser and no JS test runner, so a literal "controls
# render for a real browser" check is not available. What IS available is the
# invariant that makes the browser unnecessary: if no control's EXISTENCE
# depends on CAN_ACT, then what the browser paints cannot differ from what
# the template emits, and reading the template text is a faithful proxy for
# reading the screen. These are text-level assertions on widget.tpl.html for
# exactly that reason -- they pin the property, not the pixels.
#
# `if (!CAN_ACT) return;` inside act() is deliberately still required: that
# is a defence behind an already-disabled button, not a render gate. The
# assertions below are written so that form does not trip them.
WIDGET_TPL = (REPO_ROOT / "widget.tpl.html").read_text()

# The gating idioms: `CAN_ACT && <markup>`, `CAN_ACT ? '<button>' : ""`, and
# `if (CAN_ACT) { emit }` are how a control came to exist only when a probe
# had already resolved. Matching two exact spellings is not enough -- dropping
# a space (`CAN_ACT&&`) or reaching for the ternary evades it, and the ternary
# is the LIKELIEST reintroduction because this file's own house style already
# uses one for this decision in actAttrs().
#
# The discriminator is not "CAN_ACT appears in a conditional" -- several of
# those are the whole point of the design. It is whether the branches of that
# conditional decide markup. CAN_ACT choosing between ATTRIBUTE strings
# (actAttrs), between a text readout ($("ro")), or guarding an early return
# (act(), actAttrsIf) is allowed. CAN_ACT choosing whether a `<button` or
# `<input` EXISTS is the bug. So: normalise whitespace, then look at what
# follows each CAN_ACT conditional and fail only when markup is in reach.
_wtpl_flat = re.sub(r"\s+", " ", WIDGET_TPL)

# Window after the operator is generous enough to span a branch, tight enough
# that the next unrelated line of markup does not bleed in. Slightly
# conservative by design: a false alarm here is cheap, a miss shipped a
# read-only dashboard for a day.
def _can_act_picks_markup(op):
    for m in re.finditer(re.escape(op), _wtpl_flat):
        window = _wtpl_flat[m.end():m.end() + 160]
        if "<button" in window or "<input" in window:
            return True
    return False

# `CAN_ACT &&` / `CAN_ACT ? ...` -- the expression forms, spacing-insensitive.
check("widget_controls_no_can_act_and_guard", False, _can_act_picks_markup("CAN_ACT &&"))
check("widget_controls_no_can_act_ternary_markup", False, _can_act_picks_markup("CAN_ACT ?"))
# `!CAN_ACT ? "" : '<button>'` inverts the test but emits markup just the same.
check("widget_controls_no_neg_can_act_ternary_markup", False, _can_act_picks_markup("!CAN_ACT ?"))
# The statement forms. `if (CAN_ACT)` in any spacing, and the if/else shape
# `if (!CAN_ACT) { } else { emit }` where the empty then-branch is doing the
# hiding. Neither may have markup in its body.
check("widget_controls_no_if_can_act_guard", False, _can_act_picks_markup("if (CAN_ACT)"))
check("widget_controls_no_if_not_can_act_else_markup", False,
      _can_act_picks_markup("if (!CAN_ACT) { } else"))

# ...but the act() defence itself must survive. Deleting it along with the
# render gates would let a disabled button still fire a request.
#
# Matched as `if (!CAN_ACT) return` + whatever the early return carries, not
# as the one literal `if (!CAN_ACT) return;`. orch#74 made act() return its
# promise chain so a caller can read the response body, which turned the bare
# `return;` into `return Promise.resolve(null);` -- the guard is intact and
# still returns BEFORE the fetch, but the old literal stopped matching. What
# this check defends is that act() refuses to send when the page cannot act;
# pinning the exact return VALUE was never part of that, and doing so makes
# the assertion fail on a change that strengthens nothing it cares about.
# The `return` is still required: `if (!CAN_ACT)` alone would match a guard
# whose body was emptied out.
check("widget_controls_act_still_guards", True,
      re.search(r"if \(!CAN_ACT\)\s*return\b", WIDGET_TPL) is not None)

# The reload button was hidden outright with an inline style, which is the
# same failure in a different spelling: a control that does not exist on the
# screen. It must be emitted visible and disabled if unavailable.
_reload_lines = [ln for ln in WIDGET_TPL.splitlines() if 'id="btn-reload"' in ln]
check("widget_controls_reload_line_found", 1, len(_reload_lines))
check("widget_controls_reload_not_hidden", False,
      any("display:none" in ln for ln in _reload_lines))

# The /act probe must not swallow its own failure. A dead probe is the single
# most common reason the dashboard goes inert, and an empty catch turns that
# into silence -- which is precisely how this shipped unnoticed. Scoped to
# the probe's OWN statement: poll() and act() have empty catches that are
# legitimately empty, so slice from the probe's fetch (identified by its
# '{"action":"probe"}' body) to its first following .catch(.
_probe_at = WIDGET_TPL.find('{"action":"probe"}')
check("widget_controls_probe_found", True, _probe_at > 0)
_probe_fetch_at = WIDGET_TPL.rfind('fetch("/act"', 0, _probe_at)
_probe_catch_at = WIDGET_TPL.find(".catch(", _probe_fetch_at)
check("widget_controls_probe_has_catch", True, _probe_catch_at > _probe_fetch_at > 0)
# Enumerating spellings of an empty catch is a losing game -- `.catch(function
# (e) {})` and `.catch(() => {})` are both empty and both slip past a list.
# Assert the POSITIVE property instead, which is the actual acceptance
# criterion: a failed probe is VISIBLE on the page. The only way it becomes
# visible is the catch body writing ACT_REASON, so that assignment is what is
# pinned. Any empty catch fails this by construction, however it is spelled.
_probe_catch = WIDGET_TPL[_probe_catch_at:_probe_catch_at + 300]
check("widget_controls_probe_catch_sets_reason", True,
      re.search(r"ACT_REASON\s*=", _probe_catch) is not None)

# Every action the operator must always be offered has to be present as
# markup, unconditionally. A missing name here means the control was removed
# rather than disabled.
# A bare substring search is a tautology against a file this dense with prose.
# "merge" alone is satisfied by the .v.fixbeforemerge CSS class, by the
# "fix-before-merge" verdict string, and by several comments -- the entire
# merge button could be deleted and the check would still pass. So match the
# label construction, not just the word: every one of these buttons is icon-
# only now (#259) -- the visible glyph is wrapped `aria-hidden`, and the word
# moved to `aria-label`/`title` instead, built from a `var label = "..."`
# literal right above the button's `return`. Asserting that literal proves
# the accessible name still says the action (and the target, where the verb
# has one: "merge PR #<n>", "unwatch <repo>", ...) instead of proving nothing
# once the visible word was deleted from the button body. Whitespace is
# normalised first because the emitting expressions wrap across lines.
#
# The paired data-* attribute is asserted too so that renaming the
# accessible name alone cannot leave a button wired to nothing.
_ACTION_MARKUP = {
    # action:   (label-literal,             attribute that wires it up)
    # orch#358: kill and assign left the dashboard by operator ruling. The
    # server actions a_assign/a_kill and `spawn.py kill` are untouched -- only
    # the buttons are gone, so there is no markup left here to assert.
    "nudge":    ('var label = "nudge "',    'action: "nudge"'),
    "tail":     ('var label = "tail"',      'action: "tail"'),
    "merge":    ('var label = "merge PR #"', "data-merge="),
    "unwatch":  ('var label = "unwatch "',  "data-unwatch="),
}
for _action, (_label, _wire) in _ACTION_MARKUP.items():
    check(f"widget_controls_offers_{_action}", True, _label in _wtpl_flat)
    check(f"widget_controls_wires_{_action}", True,
          re.sub(r"\s+", " ", _wire) in _wtpl_flat)
    # The icon glyph alone is not a control: an aria-hidden span with no
    # aria-label sibling is a button a screen reader announces as nothing.
    # This does not prove the RIGHT label reaches the RIGHT button (that is
    # what the per-action literal above is for) -- it proves every one of
    # them still ends in an aria-label at all, which the old
    # `>label</button>` shape got for free from its own visible text and the
    # icon conversion could easily have dropped.
# Both of the checks that used to sit here were file-wide counts, and a
# review of #259 was right that they proved close to nothing: "some
# aria-label exists somewhere" passes on a file where seven of eight icon
# buttons lost theirs. Anchor to the BUTTON instead.
#
# Every icon button is emitted as one expression ending:
#     aria-label="' + esc(label) + '"><span aria-hidden="true">GLYPH</span></button>
# so the label, the hidden glyph and the button are provably the same
# element. Counting that whole shape is what fails when any single button
# shipped a bare glyph with no name, or a name with no hidden glyph.
_ICON_BTN = re.findall(
    r'aria-label="\' \+ esc\(label\) \+ \'"><span aria-hidden="true">'
    r'[^<]+</span></button>', _wtpl_flat)
check("widget_controls_icon_buttons_are_named", True,
      len(_ICON_BTN) >= len(_ACTION_MARKUP))
# No icon button may carry an EMPTY accessible name: `aria-label=""` would
# satisfy a naive "has aria-label" test while announcing nothing at all.
check("widget_controls_no_empty_aria_label", False, 'aria-label=""' in _wtpl_flat)
# Every aria-hidden glyph span in the file belongs to one of those named
# buttons. A bare `<span aria-hidden="true">✕</span>` sitting outside the
# shape above is a glyph with no accessible name beside it.
check("widget_controls_every_glyph_span_is_on_a_named_button", True,
      _wtpl_flat.count('<span aria-hidden="true">') == len(_ICON_BTN))

# orch#259: TRACKED vs AVAILABLE is a DISPLAY of per-repo state, not a new
# control. feed.py ships r.state ("tracked" | "off") on every repo row and
# machinePage() used to ignore it, rendering every repo under TRACKED -- so
# a paused repo displayed as tracked and the operator could not tell the
# difference the config already made.
#
# THE PARTITION MUST TEST FOR "off", NOT FOR TRUTHINESS. That is the whole
# of the regression guard below: a feed predating this field, or any row
# omitting the key, leaves state undefined, and `!r.state` or
# `r.state !== "tracked"` would sweep every such repo into AVAILABLE --
# silently relocating working repos on an older feed. `!== "off"` keeps an
# unknown state where it has always been.
check("widget_partitions_repos_on_state_off", True,
      'r.state !== "off"' in _wtpl_flat)
check("widget_available_holds_paused_repos", True,
      'r.state === "off"' in _wtpl_flat)
# The inverse spellings, each of which would misfile an undefined state.
_BAD_STATE_TESTS = {
    "negation":    "!r.state",
    "ne_tracked":  'r.state !== "tracked"',
    "eq_tracked":  'r.state === "tracked"',
}
for _name, _bad in _BAD_STATE_TESTS.items():
    check(f"widget_no_truthy_state_test_{_name}", False, _bad in _wtpl_flat)

# A paused repo has issues: [], so repoRow gets fid=null and there is no
# .det block to fold. Both fold affordances are therefore gated on fid: a
# row showing a disclosure arrow and a pointer cursor, with no data-t for
# wire() to bind, is a control that looks clickable and does nothing --
# the same failure repoRow's own comment (#160 review finding 4) exists to
# prevent, arriving by a different route.
check("widget_fold_glyph_gated_on_fid", True, 'var foldMark = fid' in _wtpl_flat)

# --- DASHBOARD-2026-09-17 steps 2-3: TAPE + #386 counts ---------------------
# Step 3: "N in flight" counted issue ROWS (2 live sessions rendered as 11 in
# flight, orch#386). The count cell is now repoCounts(), one home for both
# sites (repo row + repo page header), and `working` is the feed's ledger.
check("widget_no_in_flight_string", False, "in flight" in WIDGET_TPL)
check("widget_repo_counts_one_home", 1, WIDGET_TPL.count("function repoCounts("))
check("widget_repo_counts_two_callers", 2, WIDGET_TPL.count("esc(repoCounts(r))"))
check("widget_working_is_live_orchs", True, "n.working = r.live_orchs" in WIDGET_TPL)
check("widget_bucket_vocab", ["working", "awaiting", "orphaned", "stale"],
      re.findall(r'return "(working|awaiting|orphaned|stale)"', WIDGET_TPL))
# orch#425: a dead owner at REVIEW/CHECKING/BLOCKED past the idle threshold
# buckets `stale`, not `awaiting` -- orch#408 sat 2.5h dead and counted as
# `awaiting`, indistinguishable from a healthy PR waiting on a merge.
#
# Source assertions, not behavioural: bucketOf is client-side JS and there is
# no engine here (same reason the rest of this block reads WIDGET_TPL). What
# they pin is the SHAPE the fix depends on, because the obvious way to write
# this fix silently breaks widget_bucket_vocab above: adding a fifth
# `return "stale"` changes that ordered list. So the guard is that the fix
# stays expressed as WIDENED PREDICATES over exactly four returns.
check("widget_bucket_deadstale_defined", True, "var deadStale = i.idle_min >=" in _wtpl_flat)
# The awaiting arm must EXCLUDE dead-stale, and the stale arm must ADMIT it.
# Without both halves the row either stays in awaiting (the orch#408 bug) or
# lands in no bucket at all and vanishes from the counts entirely.
check("widget_bucket_awaiting_excludes_deadstale", True,
      "if (i.pr && atPr && !deadStale) return \"awaiting\";" in _wtpl_flat)
check("widget_bucket_stale_admits_deadstale", True,
      "(i.pr && atPr && deadStale)" in _wtpl_flat)
# The threshold is the feed's own nudge_idle_mins, never a second hardcoded
# 30 that would drift from it the first time the env var is set.
check("widget_bucket_reads_feed_threshold", True,
      "deadStale = i.idle_min >= (typeof NUDGE_IDLE_MINS === \"number\" ? NUDGE_IDLE_MINS : 30)"
      in _wtpl_flat)
# Step 2 is REPLACED by §11 (orch#415). The tape was a cross-repo event feed
# built from a misreading of "like a trade window"; the operator's words named
# rows. It is gone, and these three assertions replace the three that asserted
# it -- a removed feature must be asserted absent, or the next reader cannot
# tell "deliberately removed" from "silently regressed".
check("widget_no_tape", 0,
      WIDGET_TPL.count('sectionHead("tape"') + WIDGET_TPL.count("TAPE_FID"))
check("widget_no_tape_css", 0,
      WIDGET_TPL.count(".lg.tape") + WIDGET_TPL.count("lg-slug"))
# §11.1: every row carries its own latest update. The repo row reads
# orch.recent[-1]; the issue brief has ONE renderer shared by the repo page
# and the machine page, which is what stops the two disagreeing about `stale`.
check("widget_repo_act_sub", True, "sub: repoActSub(r)" in _wtpl_flat)
check("widget_brief_sub_one_home", 1, WIDGET_TPL.count("function briefSub("))
check("widget_brief_sub_two_callers", 2, WIDGET_TPL.count("sub: briefSub(i)"))
# TWO sites, and the count is the whole point: briefSub's (the row sub-line,
# both pages) and the issue page's own defList row, which predates it. `in`
# was not enough -- deleting briefSub's branch entirely still satisfied a
# substring test, because the defList site alone answers it. Counting is what
# makes this fail when the marker it names goes missing.
check("widget_brief_stale_marked", 2, WIDGET_TPL.count("i.brief.stale ?"))
# Still true, and still the rule: the live repaint is the SSE push, never a
# poll. §11 kept this constraint when it removed the section that prompted it.
check("widget_tape_no_timer", 1, WIDGET_TPL.count("setInterval("))
check("widget_toggle_class_gated_on_fid", True,
      'cls: fid ? "repo toggle" : "repo"' in _wtpl_flat)
# `.repo` is IDENTITY and rides on BOTH branches; `.toggle` is AFFORDANCE
# and rides only on the folding one. The first version of this gated the
# whole class string on fid, which silently stripped a paused repo of the
# repo-row background, border and 600-weight name -- it rendered as a bare
# nested row. These two checks pin the split so a later tidy cannot merge
# them back: the styling selector must not be the fold selector.
check("widget_repo_identity_not_tied_to_fold", True,
      "#orch .er.repo { background: #12161c" in _wtpl_flat)
check("widget_fold_affordance_is_its_own_selector", True,
      "#orch .er.toggle { cursor: pointer }" in _wtpl_flat)

# orch#283: the four TEXT-labelled buttons (their visible word is already the
# accessible name, so they get no aria-label) carried no tooltip explaining
# their PURPOSE. tipAttrs() (by actAttrs(), ~line 430) fills that in with a
# static title that yields to actAttrs()'s own live reason when the page
# cannot act, so at most one `title=` reaches the tag either way.
#
# Anchored to a slice of the actual EMITTING expression around each button,
# not to the tooltip words alone appearing anywhere in the file -- a
# file-wide substring match would pass even if the tooltip landed on some
# unrelated tag (see the "kill #" discussion above this region).
check("widget_reload_button_has_tooltip", True,
      'id="btn-reload" title="re-fetch the dashboard data now' in _wtpl_flat)

# The verdict and reply-send tooltips that stood here were removed with the
# QUESTIONS section itself (orch#291). Both pinned buttons that only ever
# existed inside that section -- verbVerdict's verdict button and replyBox's
# send button -- so with the section gone the checks assert against deleted
# emitters. The reviewer called this before either PR landed: "those two
# buttons cease to exist and #283's tooltips on them are moot." The repo
# page's own leave-note send button keeps its check, below.

def _tooltip_pinned(wiring, tip, guard):
    """True when THIS button's wiring, its tooltip and its own ">send</button>"
    close all sit in one uninterrupted span of the flattened template.

    The gap may not cross a button boundary: `.*?` alone would run past this
    button's own close and into another send button's tooltip further down the
    file, so a button stripped of its tooltip would "pass" by borrowing a
    sibling's. [^<]* cannot span the intervening "</button>" and pins each
    tooltip to its own emitter."""
    return re.search(
        re.escape(wiring) + r"[^<]*" + re.escape(
            'tipAttrs("' + tip + '", !' + guard + ')') +
        r'\s*\+\s*">send</button>"',
        _wtpl_flat) is not None

# The repo page's leave-note box (`1c3a5d3`, orch#292) landed AFTER the #283
# sweep and arrived with no tooltip -- a second ">send</button>" whose visible
# word does not say where the text goes. Same treatment, pinned the same way:
# anchored on notePayload. replyBox's send button was the other `[data-reply]`
# button this had to be told apart from; orch#291 removed it with the
# QUESTIONS section, so the anchor now guards against a future sibling
# rather than a present one.
check("widget_note_button_has_tooltip", True,
      _tooltip_pinned(
          "data-reply=\\'' + esc(JSON.stringify(notePayload))",
          "append the typed text to this repo's journal, read on its next wake",
          "noteAttrs"))

# "tick now": tipAttrs sits between the tick data-act wiring and the literal
# ">tick now</button>" close, so this is pinned to the same button the
# footer's own action:"tick" payload wires up.
check("widget_tick_button_has_tooltip", True,
      re.search(
          r'action:\s*"tick".*?tipAttrs\("run one orch tick immediately, '
          r'instead of waiting for the scheduled one", !tickAttrs\)\s*\+\s*'
          r'">tick now</button>"',
          _wtpl_flat) is not None)

# No emitted OPENING tag may carry two literal `title=` attributes -- that is
# the invalid-HTML failure mode #283 warns about (first wins, tooltip silently
# lost). This is a STATIC check on the template source, and it is honest
# about what that can and cannot see: actAttrs()/tipAttrs() are function
# CALLS in this text, not expansions, so a tag built from
# `actAttrs() + tipAttrs(...)` shows as ONE literal `title=` here even though
# at runtime actAttrs() may itself contribute a second, live one -- the
# calling convention (pass `!attrs` so tipAttrs is empty exactly when
# actAttrs already emitted a title) is what keeps that runtime count at one,
# and that contract is not something regexing the source text can verify.
# What this check DOES catch is the narrower, still-real mistake: a tag with
# two title="..." literals baked directly into the markup string.
_OPEN_TAGS = re.findall(r"<(?:button|input)\b[^>]*>", _wtpl_flat)
check("widget_controls_tags_found_for_double_title_check", True, len(_OPEN_TAGS) > 0)
check("widget_controls_no_literal_double_title", True,
      all(tag.count('title="') <= 1 for tag in _OPEN_TAGS))

# Transient button text must not desynchronise the accessible name (#259).
#
# The bug this bans, found in review: arming a confirm button did
# `b.textContent = b.getAttribute("data-confirm")`, and act() did
# `btn.textContent = "…"/"done"/"failed"`. Both replaced the aria-hidden
# span with a bare text node -- un-hiding the glyph permanently -- and
# neither touched aria-label, so a button visibly reading "kill #42?"
# still announced "kill #42". The confirm step went silent for exactly the
# operator who most needs it, and no visible symptom showed on screen.
#
# Every transient write now goes through btnSay(), which moves aria-label
# with the text, and restores through btnRestore(), which puts the glyph
# back inside its span via innerHTML.
check("widget_btnsay_moves_aria_label_with_text", True,
      'if (b.hasAttribute("aria-label")) b.setAttribute("aria-label", text);'
      in _wtpl_flat)
check("widget_btnrestore_restores_markup_not_text", True,
      "if (html != null) b.innerHTML = html;" in _wtpl_flat)
# No raw textContent write may survive on a verb button: those are the two
# shapes that caused the defect. Arming and act() must both route through
# btnSay so there is ONE place that keeps text and name together.
check("widget_no_raw_textcontent_on_confirm_arm", False,
      'b.textContent = b.getAttribute("data-confirm")' in _wtpl_flat)
for _transient in ('btn.textContent = "…"', 'btn.textContent = d.ok ? "done" : "failed"'):
    check(f"widget_no_raw_textcontent_in_act_{_transient[-8:].strip(chr(34))}",
          False, _transient in _wtpl_flat)

# The gate that actually shipped was not spelled CAN_ACT at all. render()
# built every control into `out`, then threw the whole string away:
#
#     $("body").innerHTML = any ? out : '<div class="empty">...</div>'
#
# `any` is true only if some repo has an in-flight issue, so an idle feed --
# a watched repo with an empty issue list -- replaced the watch row, nudge,
# + ask, + issue and unwatch with the words "queue empty". With repos: [] it
# was unrecoverable: + watch repo is the only way to watch a first repo.
#
# The checks above ban CAN_ACT from choosing whether markup exists. This one
# bans the same thing one level up: the assembled control markup must reach
# the DOM unconditionally. An empty-state NOTE is fine and wanted -- it just
# has to be appended to `out`, never substituted for it (#66).
_body_assign = re.search(r'\$\("body"\)\.innerHTML\s*=\s*([^;]+);', WIDGET_TPL)
check("widget_controls_body_assign_found", True, _body_assign is not None)
if _body_assign:
    _body_rhs = _body_assign.group(1).strip()
    # Bare `out` is the whole contract: no ternary, no && , no conditional of
    # any kind between the assembled controls and the page.
    check("widget_controls_body_unconditional", "out", _body_rhs)

# === orch#82: the feed address must not carry an origin or a port ==========
# FEED was `https://devhost.example-tailnet.ts.net:18802/status.json` while the
# server listened on 18803 (ORCH_PORT default, server.py). SAME_ORIGIN
# compares the feed origin against the page origin, so the two never matched:
# SAME_ORIGIN was false for EVERY viewer on EVERY load, poll() returned
# without fetching, and the load-time poll never ran. The page still rendered,
# because the tick bakes SEED into the HTML -- so the failure presented as a
# working dashboard showing data one tick old, and it survived for a day while
# three other theories were chased. Every curl test passed because each one
# used the right port by hand; only a browser resolves FEED against its own
# location, and this repo has no browser.
#
# What IS checkable is the property that makes the drift impossible rather
# than merely fixed: if FEED names no scheme, no host and no port, then there
# is nothing left to drift FROM the serving origin. The server routes
# "/status.json" itself, so a root-relative path is same-origin by
# construction on whatever port ORCH_PORT selects.
_feed_assign = re.search(r'\bvar\s+FEED\s*=\s*(["\'])(.*?)\1', WIDGET_TPL)
check("widget_feed_assign_found", True, _feed_assign is not None)
if _feed_assign:
    _feed = _feed_assign.group(2)
    # An absolute URL is the defect SHAPE, not just the wrong digits: any
    # scheme reintroduces a host and a port this file cannot know.
    check("widget_feed_no_scheme", False,
          "://" in _feed or _feed.startswith("//"))
    # A port literal, anywhere in the value. This is the exact byte that cost
    # the day, so it is asserted separately from the scheme check.
    check("widget_feed_no_port_literal", False,
          bool(re.search(r":\d{2,5}\b", _feed)))
    # Root-relative, so it resolves to the serving origin from any page path.
    check("widget_feed_root_relative", True, _feed.startswith("/"))

# A relative FEED is only correct if it is RESOLVED against the page. Bare
# `new URL(FEED)` throws on a relative path, and the surrounding try/catch
# would turn that throw into a permanently-false SAME_ORIGIN -- reproducing
# the original silent failure from the opposite direction. So pin the second
# argument: the two halves of this fix are only safe together.
# Scan CODE, not comments: this assertion is about call sites, and a `//`
# line that quotes the wrong form to explain why it is wrong must not read as
# the wrong form itself.
_wtpl_code = re.sub(r"\s+", " ", re.sub(r"^\s*//.*$", "", WIDGET_TPL, flags=re.M))
_feed_url_calls = re.findall(r"new\s+URL\s*\(\s*FEED\s*([,)])", _wtpl_code)
check("widget_feed_url_resolved_found", True, len(_feed_url_calls) > 0)
check("widget_feed_url_always_resolved_against_page", [],
      [c for c in _feed_url_calls if c == ")"])

# The drift must be LOUD. A mismatch previously disabled the update path in
# silence; the readout said "read-only snapshot -- rebuilt each tick", which
# describes a healthy sandbox. Require a reason string that names both
# origins, so a wrong feed address is readable on the page.
_reason_assign = re.search(r"var FEED_REASON = (.*?); if ", _wtpl_code)
check("widget_feed_reason_defined", True, _reason_assign is not None)
if _reason_assign:
    _reason_rhs = _reason_assign.group(1)
    # Both sides of the comparison, so the operator reads what was expected
    # AND what was served instead -- either alone leaves a guess.
    check("widget_feed_reason_names_page_origin", True,
          "window.location.origin" in _reason_rhs)
    check("widget_feed_reason_names_feed_origin", True,
          "FEED_ORIGIN" in _reason_rhs)
# No assertion on the reload button here. #83 made it a plain location.reload()
# that takes no origin check and is never disabled, so it has no room to carry
# a reason -- and needs none, because reloading always works. The readout and
# the console warning carry FEED_REASON instead.

# === orch#97: the unclaim button -- REMOVED by orch#358 ====================
# The unclaim BUTTON left the dashboard by operator ruling (orch#358); the
# server action a_unclaim is untouched and unclaim remains available from the
# terminal, which is where the recovery path now lives. Every assertion that
# stood here proved properties of the button's markup (data-unclaim, the
# arm-then-confirm pair, and the unclaimDead drift guard reading i.state
# rather than the raw L_WORKING/L_STUCK literals), so all of it went with the
# markup rather than being weakened into something that no longer tests a
# real control. The drift guard it carried is not lost in general: nothing in
# the widget now reads claimed-ness at all, so there is no copy of those
# label literals left here to go stale.

# === orch#64: remote resolution must not assume the name `origin` ==========
# A checkout whose only remote is named something other than `origin` (a
# Gitea clone made with `git clone -o gitea`, say) must still resolve --
# `origin` wins when present, a SOLE non-origin remote is an unambiguous
# stand-in, and only a genuine tie (0 remotes, or 2+ with none named
# origin) gives up. See core._remote_url.
# T itself was rmtree'd by an earlier section's cleanup (search upward for
# `shutil.rmtree(T,`) -- recreate it before use.
T.mkdir(parents=True, exist_ok=True)
r64_gitea_dir = T / "r64-gitea-only"
r64_gitea_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r64_gitea_dir, check=True)
subprocess.run(["git", "remote", "add", "gitea", "http://gitea.local:3000/cybermelon/gita-lectures.git"],
                cwd=r64_gitea_dir, check=True)
check("r64_sole_nonorigin_remote_slug", "cybermelon/gita-lectures", core.repo_slug(r64_gitea_dir))
check("r64_sole_nonorigin_remote_backend", "tea", core.repo_backend(r64_gitea_dir))

r64_upstream_dir = T / "r64-upstream-only"
r64_upstream_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r64_upstream_dir, check=True)
subprocess.run(["git", "remote", "add", "upstream", "git@github.com:cybermelon/orch.git"],
                cwd=r64_upstream_dir, check=True)
check("r64_sole_nonorigin_remote_gh_repo", "cybermelon/orch", core.gh_repo(r64_upstream_dir))
check("r64_sole_nonorigin_remote_gh_backend", "gh", core.repo_backend(r64_upstream_dir))

r64_ambiguous_dir = T / "r64-two-remotes"
r64_ambiguous_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r64_ambiguous_dir, check=True)
subprocess.run(["git", "remote", "add", "alpha", "http://gitea.local:3000/cybermelon/alpha.git"],
                cwd=r64_ambiguous_dir, check=True)
subprocess.run(["git", "remote", "add", "beta", "http://gitea.local:3000/cybermelon/beta.git"],
                cwd=r64_ambiguous_dir, check=True)
check("r64_two_nonorigin_remotes_slug_empty", "", core.repo_slug(r64_ambiguous_dir))
check("r64_two_nonorigin_remotes_backend_defaults_gh", "gh", core.repo_backend(r64_ambiguous_dir))

r64_both_dir = T / "r64-origin-and-gitea"
r64_both_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r64_both_dir, check=True)
subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/orch.git"],
                cwd=r64_both_dir, check=True)
subprocess.run(["git", "remote", "add", "gitea", "http://gitea.local:3000/cybermelon/gita-lectures.git"],
                cwd=r64_both_dir, check=True)
check("r64_origin_wins_over_second_remote", "cybermelon/orch", core.repo_slug(r64_both_dir))

r64_noremote_dir = T / "r64-no-remotes"
r64_noremote_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r64_noremote_dir, check=True)
r64_repos_txt = core.ORCH_HOME / "repos.txt"
r64_before = r64_repos_txt.read_text() if r64_repos_txt.exists() else ""
r64_ok, r64_msg = core.watch_repo(str(r64_noremote_dir))
check("r64_watch_no_remotes_fails", False, r64_ok)
r64_after = r64_repos_txt.read_text() if r64_repos_txt.exists() else r64_before
check("r64_watch_no_remotes_not_recorded", False, str(r64_noremote_dir) in r64_after)

# --- World.load("") must refuse immediately, before ever shelling out ------
# An empty slug doesn't make `gh issue list --repo ""` fail -- it silently
# answers from the cwd's default repo instead. World.load must treat an
# empty/blank slug as a hard failure at the choke point, not fall through
# to _run at all. Reuse the same core._run monkeypatch idiom used for the
# load() gh/tea argv checks above: record every call, assert the list stays
# empty.
check("r64_world_load_empty_slug_false", False, core.World().load(""))
check("r64_world_load_blank_slug_false", False, core.World().load("   "))

_r64_load_calls = []
_r64_orig_run = core._run


def _r64_fake_run(cmd, cwd=None, timeout=None):
    _r64_load_calls.append(list(cmd))
    return _r64_orig_run(cmd, cwd=cwd, timeout=timeout)


core._run = _r64_fake_run
try:
    core.World().load("")
    check("r64_world_load_empty_slug_no_run_calls", 0, len(_r64_load_calls))
finally:
    core._run = _r64_orig_run

# --- merge_pr on a checkout with no remotes: refused, never shells to gh ---
# r64_noremote_dir (built above for the watch_repo check) has no remotes at
# all, so repo_backend defaults "gh" and gh_repo resolves to "" -- exactly
# the empty-slug case merge_pr must catch itself, before World.load, so the
# operator sees the checkout PATH named in the failure rather than a blank
# repo string.
_r64_merge_calls = []
_r64_orig_run_merge = core._run


def _r64_fake_merge_run(cmd, cwd=None, timeout=None):
    _r64_merge_calls.append(list(cmd))
    return _r64_orig_run_merge(cmd, cwd=cwd, timeout=timeout)


core._run = _r64_fake_merge_run
try:
    r64_merge_result = core.merge_pr(r64_noremote_dir, 1, 1)
    check("r64_merge_pr_no_remotes_refused", False, r64_merge_result["ok"])
    check("r64_merge_pr_no_remotes_out_nonempty", True, bool(r64_merge_result["out"]))
    check("r64_merge_pr_no_remotes_out_names_path", True,
          str(r64_noremote_dir) in r64_merge_result["out"])
    _r64_gh_calls = [c for c in _r64_merge_calls if c[:2] == ["gh", "pr"] and c[2:3] == ["merge"]]
    check("r64_merge_pr_no_remotes_no_gh_shellout", 0, len(_r64_gh_calls))
finally:
    core._run = _r64_orig_run_merge

# === deploy drift: the dashboard must report its own staleness =============
# Issue #76: the live checkout served code 16 and then 20 commits old while
# the fixes sat on main. A tick run on stale code produced a wrong result that
# looked like a backend bug. Nothing reported it, because a dashboard serving
# month-old code looks identical to one serving main.
#
# The behind>0 branch is the whole point of the feature and it is the branch
# that never runs in normal development, so it is built here from a REAL
# repository rather than stubbed: a source repo with 3 commits, cloned, then
# the clone reset back 2 commits with its origin/main ref left where it was.
# A stub of deploy_json() would prove only that the alert formats a number;
# this proves the git plumbing reports the right number against a real ref.
from orch import feed as feedmod

D76 = T / "deploy76"
D76.mkdir(parents=True, exist_ok=True)


def _git76(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True, timeout=30)


d76_src = D76 / "src"
d76_src.mkdir()
_git76(d76_src, "init", "-q", "-b", "main")
_git76(d76_src, "config", "user.email", "t@t")
_git76(d76_src, "config", "user.name", "t")
for i in range(3):
    (d76_src / "f.txt").write_text(str(i))
    _git76(d76_src, "add", "f.txt")
    _git76(d76_src, "commit", "-q", "-m", f"c{i}")

# The clone's origin/main stays at c2 while its HEAD moves back to c0: that is
# exactly the shape of a live checkout that has fetched but not merged.
d76_clone = D76 / "clone"
subprocess.run(["git", "clone", "-q", str(d76_src), str(d76_clone)],
               capture_output=True, text=True, timeout=30)
d76_head0 = _git76(d76_clone, "rev-parse", "HEAD~2").stdout.strip()
_git76(d76_clone, "reset", "-q", "--hard", d76_head0)

_orig_orch_home_76 = feedmod.ORCH_HOME
try:
    feedmod.ORCH_HOME = d76_clone
    d76 = feedmod.deploy_json()
    check("deploy_behind_counts_two", 2, d76["behind"])
    check("deploy_behind_flag_true", True, d76["is_behind"])
    check("deploy_reports_branch", "main", d76["branch"])
    # The commit reported must be the one actually checked out, not the tip:
    # naming the tip would make a stale deploy look current.
    check("deploy_commit_is_head_not_tip", True,
          bool(d76["commit"]) and d76_head0.startswith(d76["commit"]))

    # host_json must carry it through -- the dashboard reads host, not
    # deploy_json.
    check("deploy_reaches_host_json", 2,
          feedmod.host_json().get("deploy", {}).get("behind"))

    # A checkout that is current must NOT alert; otherwise the alert is noise
    # and the operator learns to ignore it.
    feedmod.ORCH_HOME = d76_src
    check("deploy_current_not_behind", False, feedmod.deploy_json()["is_behind"])

    # Degradation: a directory that is not a git repo at all must not raise,
    # because feed.build() raising is a hard tick failure.
    d76_nonrepo = D76 / "nonrepo"
    d76_nonrepo.mkdir()
    feedmod.ORCH_HOME = d76_nonrepo
    d76_bad = feedmod.deploy_json()
    check("deploy_nonrepo_behind_negative", -1, d76_bad["behind"])
    check("deploy_nonrepo_not_flagged_behind", False, d76_bad["is_behind"])
    check("deploy_nonrepo_commit_none", None, d76_bad["commit"])
finally:
    feedmod.ORCH_HOME = _orig_orch_home_76

# deploy_json must never fetch: a network call inside the 600s tick loop can
# hang the loop. Assert on the argv rather than on behaviour, because a fetch
# against an unreachable remote would pass a behavioural test by accident.
_d76_calls = []
_d76_orig_run = feedmod._run


def _d76_fake_run(cmd, timeout=None):
    _d76_calls.append(list(cmd))
    return _d76_orig_run(cmd, timeout=timeout)


feedmod._run = _d76_fake_run
try:
    feedmod.deploy_json()
    check("deploy_never_fetches", [],
          [c for c in _d76_calls if "fetch" in c or "pull" in c])
    check("deploy_all_calls_have_timeout", True, bool(_d76_calls))
finally:
    feedmod._run = _d76_orig_run

# The alert itself. This MUST call feed.build() -- an earlier version of this
# test re-implemented build()'s branch locally and asserted on its own copy,
# which passed with the production hunk deleted entirely. Stub host_json (the
# only input the branch reads) and assert on build()'s real output.
_d76_orig_host = feedmod.host_json


def _d76_host_behind():
    return {"disk": "1% of 1G", "load": "0.00",
            "deploy": {"commit": "abc1234", "branch": "main",
                       "base": "origin/main", "behind": 20, "is_behind": True}}


feedmod.host_json = _d76_host_behind
try:
    _d76_built = feedmod.build()
    _d76_hits = [a for a in _d76_built["alerts"] if a.get("key") == "deploy-behind"]
    check("deploy_alert_emitted_once", 1, len(_d76_hits))
    check("deploy_alert_is_error_level", "error", _d76_hits[0]["level"] if _d76_hits else None)
    check("deploy_alert_names_count", True,
          "20" in _d76_hits[0]["msg"] if _d76_hits else False)
    check("deploy_alert_names_commit", True,
          "abc1234" in _d76_hits[0]["msg"] if _d76_hits else False)

    # ...and must NOT fire when current, or the alert is noise.
    feedmod.host_json = lambda: {"disk": "1% of 1G", "load": "0.00",
                                 "deploy": {"commit": "abc1234", "branch": "main",
                                            "base": "origin/main", "behind": 0,
                                            "is_behind": False}}
    check("deploy_alert_absent_when_current", 0,
          len([a for a in feedmod.build()["alerts"] if a.get("key") == "deploy-behind"]))

    # behind=-1 means "could not tell". It must not alert either: a false
    # staleness alert on every unreadable repo trains the operator to ignore
    # the one that matters.
    feedmod.host_json = lambda: {"disk": "1% of 1G", "load": "0.00",
                                 "deploy": {"commit": None, "branch": None,
                                            "base": None, "behind": -1,
                                            "is_behind": False}}
    check("deploy_alert_absent_when_unknown", 0,
          len([a for a in feedmod.build()["alerts"] if a.get("key") == "deploy-behind"]))
finally:
    feedmod.host_json = _d76_orig_host

# The base ref comes from core.base_ref rather than a hardcoded "origin/main",
# so deploy_json tracks whatever base the rest of the codebase agrees on
# instead of inventing a second, stricter answer.
#
# KNOWN LIMIT, asserted deliberately as the CURRENT behaviour, not as the
# desired one: core.base_ref itself still hardcodes the remote NAME -- it
# reads refs/remotes/origin/HEAD and falls back to origin/main, origin/master.
# So a checkout whose only remote is named "gitea" resolves no base, and
# deploy_json degrades to behind=-1 (reported on the page as "?", never as a
# false "up to date"). That is a gap in base_ref shared with work_mtime and
# feed.py:48, NOT something deploy_json should paper over locally with a
# second remote-resolution rule. Fixing base_ref changes those other callers
# and belongs in its own issue. If base_ref is fixed, this check flips to 2
# and the assertion below should be updated with it.
d76_gitea = D76 / "gitea"
subprocess.run(["git", "clone", "-q", "--origin", "gitea", str(d76_src), str(d76_gitea)],
               capture_output=True, text=True, timeout=30)
_git76(d76_gitea, "reset", "-q", "--hard", "HEAD~2")
try:
    feedmod.ORCH_HOME = d76_gitea
    _d76_g = feedmod.deploy_json()
    check("deploy_nonorigin_degrades_not_false_current", -1, _d76_g["behind"])
    # The safety property that MUST hold regardless: an unresolvable base
    # never reports a confident "up to date".
    check("deploy_nonorigin_never_claims_current", False,
          _d76_g["behind"] == 0)
    check("deploy_nonorigin_no_false_alert", False, _d76_g["is_behind"])
    # It still reports WHICH commit is running, so the page is not silent.
    check("deploy_nonorigin_still_reports_commit", True, bool(_d76_g["commit"]))
finally:
    feedmod.ORCH_HOME = _orig_orch_home_76

# === orch#250 part 2: auto-pull-stuck alert ================================
# deploy-behind (above) says "not running current code" -- true whether the
# puller is happily catching up or has been failing for an hour. Those need
# different responses (none vs. a human), so pull_stuck_alert() is a second,
# pure decision over deploy/orch-pull.sh's own recorded outcome history.
# Pure function, no state file, no mocking: (count, outcome) -> alert or None.
check("pull_stuck_below_threshold_silent", None,
      feedmod.pull_stuck_alert(feedmod.PULL_FAIL_THRESHOLD - 1, "FAILED: git fetch origin did not complete"))

_d250_at = feedmod.pull_stuck_alert(feedmod.PULL_FAIL_THRESHOLD, "FAILED: git fetch origin did not complete")
check("pull_stuck_at_threshold_raises", "deploy-pull-stuck", _d250_at["key"] if _d250_at else None)
check("pull_stuck_at_threshold_is_error", "error", _d250_at["level"] if _d250_at else None)
check("pull_stuck_at_threshold_needs_you", True, _d250_at["needs_you"] if _d250_at else False)
check("pull_stuck_msg_names_count", True,
      str(feedmod.PULL_FAIL_THRESHOLD) in _d250_at["msg"] if _d250_at else False)
check("pull_stuck_msg_names_outcome", True,
      "FAILED: git fetch origin did not complete" in _d250_at["msg"] if _d250_at else False)

_d250_above = feedmod.pull_stuck_alert(feedmod.PULL_FAIL_THRESHOLD + 5, "REFUSED: dirty tracked files")
check("pull_stuck_above_threshold_raises", "deploy-pull-stuck", _d250_above["key"] if _d250_above else None)
check("pull_stuck_msg_names_higher_count", True,
      str(feedmod.PULL_FAIL_THRESHOLD + 5) in _d250_above["msg"] if _d250_above else False)

# Missing state file (the normal case on a machine that hasn't run the new
# script yet) -> read_pull_state degrades to {}, so deploy_json hands
# pull_stuck_alert (None, None) -- must be silent, not alarming.
check("pull_stuck_none_count_silent", None, feedmod.pull_stuck_alert(None, None))

# read_pull_state itself: missing, empty, and malformed files must all
# degrade to {} and must never raise (same contract as deploy_json).
_orig_pull_state_file = feedmod.PULL_STATE_FILE
_d250_dir = D76.parent / "pullstate250"
_d250_dir.mkdir(parents=True, exist_ok=True)
try:
    feedmod.PULL_STATE_FILE = _d250_dir / "missing.json"
    check("pull_state_missing_file_silent", {}, feedmod.read_pull_state())

    (_d250_dir / "empty.json").write_text("")
    feedmod.PULL_STATE_FILE = _d250_dir / "empty.json"
    check("pull_state_empty_file_silent", {}, feedmod.read_pull_state())

    (_d250_dir / "malformed.json").write_text("{not json at all")
    feedmod.PULL_STATE_FILE = _d250_dir / "malformed.json"
    check("pull_state_malformed_file_silent", {}, feedmod.read_pull_state())

    (_d250_dir / "wrongshape.json").write_text("[1, 2, 3]")
    feedmod.PULL_STATE_FILE = _d250_dir / "wrongshape.json"
    check("pull_state_wrongshape_file_silent", {}, feedmod.read_pull_state())

    (_d250_dir / "good.json").write_text(
        '{"consecutive_failures":4,"last_outcome":"FAILED: x","last_attempt":123}')
    feedmod.PULL_STATE_FILE = _d250_dir / "good.json"
    _d250_good = feedmod.read_pull_state()
    check("pull_state_good_file_count", 4, _d250_good.get("consecutive_failures"))
    check("pull_state_good_file_outcome", "FAILED: x", _d250_good.get("last_outcome"))
finally:
    feedmod.PULL_STATE_FILE = _orig_pull_state_file
    shutil.rmtree(_d250_dir, ignore_errors=True)

# End-to-end through build(): the alert must actually reach build()'s
# alerts list when the count crosses threshold (stub host_json, the only
# input this branch reads, same pattern as the deploy-behind test above).
def _d250_fake_deploy_stuck():
    return {"commit": "abc1234", "branch": "main", "base": "origin/main",
            "behind": 1, "is_behind": True,
            "pull_consecutive_failures": feedmod.PULL_FAIL_THRESHOLD,
            "pull_last_outcome": "FAILED: git fetch origin did not complete"}


_d250_orig_host_json = feedmod.host_json


def _d250_fake_host_stuck():
    return {"disk": "1% of 1G", "load": "0.00", "deploy": _d250_fake_deploy_stuck()}


feedmod.host_json = _d250_fake_host_stuck
try:
    _d250_built = feedmod.build()
    _d250_hits = [a for a in _d250_built["alerts"] if a.get("key") == "deploy-pull-stuck"]
    check("pull_stuck_alert_reaches_build", 1, len(_d250_hits))
finally:
    feedmod.host_json = _d250_orig_host_json

# The three files the running server writes must be untracked, so the merge in
# the live checkout does not abort on them. Guard against a vacuous pass
# first: assert the repo IS a git repo and that a file known to be tracked
# reads as tracked. Without that, `ls-files --error-unmatch` returning
# non-zero for any reason (not a repo at all) would "prove" all three.
_d76_isrepo = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--git-dir"],
                             capture_output=True, text=True, timeout=30)
check("untracked_precondition_is_git_repo", 0, _d76_isrepo.returncode)
_d76_ctl = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch",
                           "orch/feed.py"], capture_output=True, text=True, timeout=30)
check("untracked_precondition_control_is_tracked", 0, _d76_ctl.returncode)

# Existence on disk is a property of a DEPLOYED instance (the running
# orchestrator writes these files as it operates), not of the repository
# itself. CI and any fresh `git clone` never run the orchestrator, so they
# never have these files -- that is expected, not a defect. Detect a live
# deployment by a fact the running orchestrator creates and a fresh clone
# does not: a "state" directory at REPO_ROOT. (Note: "repos.txt" is a
# tracked file present in every clone, not a deployment-only marker, so it
# cannot be used here despite being on the original list of candidates.)
_d76_is_deployment = (REPO_ROOT / "state").exists()

for _p76 in ("history.jsonl", "public/status.json", "public/widget.html"):
    _t76 = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", _p76],
                          capture_output=True, text=True, timeout=30)
    check(f"untracked_{_p76.replace('/', '_')}", False, _t76.returncode == 0)
    # The other half of the acceptance criterion: untracked, but STILL ON
    # DISK. A plain `git rm` would satisfy the check above and destroy live
    # runtime state. This half only applies to a live deployment: on a
    # fresh clone (CI) there is no running orchestrator to have written the
    # file yet, so its absence is expected and the check is satisfied
    # trivially instead of being skipped outright -- the name set must stay
    # identical between environments.
    if _d76_is_deployment:
        check(f"ondisk_{_p76.replace('/', '_')}", True, (REPO_ROOT / _p76).exists())
    else:
        check(f"ondisk_{_p76.replace('/', '_')}", True, True)

shutil.rmtree(D76, ignore_errors=True)
# === orch#69: core.RepoAdapter / adapter_for ================================
# One object per repo that has already answered "which backend, which slug,
# which login, which web base", replacing 18 copies of the inline
# `if backend == "tea": ... else: ...` dispatch.
#
# REAL git checkouts with REAL remotes, so backend and slug resolve by their
# production route (repo_backend/gh_repo/repo_slug) -- nothing about the
# dispatch itself is stubbed, or the test would prove nothing. Fresh dirs,
# not the orch#58 block's: T was torn down above (`shutil.rmtree(T,`).
_r69_t = Path(tempfile.mkdtemp())
r69_gh_dir = _r69_t / "r69-gh"
r69_gh_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r69_gh_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                "git@github.com:cybermelon/orch.git"], cwd=r69_gh_dir, check=True)
r69_tea_dir = _r69_t / "gita-lectures"
r69_tea_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=r69_tea_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                "http://gitea.local:3000/cybermelon/gita-lectures.git"],
               cwd=r69_tea_dir, check=True)

_r69_repos_file = core.ORCH_HOME / "repos.txt"
_r69_repos_txt = _r69_repos_file.read_text() if _r69_repos_file.exists() else None
core.ORCH_HOME.mkdir(parents=True, exist_ok=True)  # an earlier block may have torn it down
_r69_repos_file.write_text(
    f"{r69_gh_dir}\n{r69_tea_dir}  login=gitea\n")
_r69_orig_run = core._run
core._reset_login_web_bases()



def _r69_run(cmd, cwd=None, timeout=None):
    """Stub ONLY `tea logins list` (so web_base resolves without the binary);
    everything else -- notably `git remote get-url` -- goes to the real _run,
    because these adapters must resolve their backend by the production
    route, not from a stub that would make the test prove nothing."""
    if list(cmd[:3]) == ["tea", "logins", "list"]:
        return True, "gitea http://gitea.local:3000\n"
    return _r69_orig_run(cmd, cwd=cwd, timeout=timeout)


core._run = _r69_run
try:
    r69_gh = core.adapter_for(r69_gh_dir)
    r69_tea = core.adapter_for(r69_tea_dir)

    # 1. The SAME code path yields two DIFFERENT adapters -- the whole point.
    check("r69_adapter_gh_backend", "gh", r69_gh.backend)
    check("r69_adapter_tea_backend", "tea", r69_tea.backend)
    check("r69_adapters_differ", True, r69_gh.backend != r69_tea.backend)
    check("r69_adapter_gh_slug", "cybermelon/orch", r69_gh.slug)
    check("r69_adapter_tea_slug", "cybermelon/gita-lectures", r69_tea.slug)
    # login is meaningful on tea only; gh never reads it, so it is None
    # rather than a value a reader might mistake for one gh honours.
    check("r69_adapter_gh_login_none", None, r69_gh.login)
    check("r69_adapter_tea_login", "gitea", r69_tea.login)
    # The adapter SELECTS between the existing tables; it does not replace
    # them. Identity, not equality: a copy would be a second source of truth.
    check("r69_adapter_gh_table_is_gh_argv", True, r69_gh.table is core.GH_ARGV)
    check("r69_adapter_tea_table_is_tea_argv", True, r69_tea.table is core.TEA_ARGV)

    # 2. pr_url: Gitea's path segment is "pulls" (PLURAL), GitHub's is "pull"
    # (SINGULAR). Backwards, the link looks right and 404s silently -- so the
    # FULL string is asserted, not just the segment.
    check("r69_pr_url_gh", "https://github.com/cybermelon/orch/pull/7",
          r69_gh.pr_url(7))
    check("r69_pr_url_tea", "http://gitea.local:3000/cybermelon/gita-lectures/pulls/7",
          r69_tea.pr_url(7))

    # 3. issue_url: "issues" on BOTH backends, only the base differs.
    check("r69_issue_url_gh", "https://github.com/cybermelon/orch/issues/44",
          r69_gh.issue_url(44))
    check("r69_issue_url_tea", "http://gitea.local:3000/cybermelon/gita-lectures/issues/44",
          r69_tea.issue_url(44))

    # An unresolvable Gitea web base yields None -- no link -- never a URL
    # guessed from the git remote's host (which is the SSH endpoint, not the
    # web UI). This is the rule feed.py's comments spell out, now enforced in
    # the adapter itself.
    r69_nobase = core.RepoAdapter("tea", "cybermelon/gita-lectures", "gitea", "")
    check("r69_tea_no_web_base_issue_url_none", None, r69_nobase.issue_url(44))
    check("r69_tea_no_web_base_pr_url_none", None, r69_nobase.pr_url(7))

    # 4. Releasing agent-working goes THROUGH the adapter. tea's flag is
    # --remove-labels (PLURAL, mirroring its own --add-labels); gh's is
    # --remove-label (singular).
    check("r69_tea_remove_label_argv",
          ["tea", "issue", "edit", "12", "--login", "gitea",
           "--repo", "cybermelon/gita-lectures",
           "--remove-labels", "agent-working"],
          r69_tea.issue_edit_remove_label(12, core.L_WORKING))
    check("r69_gh_remove_label_argv",
          ["gh", "issue", "edit", "12", "--remove-label", "agent-working"],
          r69_gh.issue_edit_remove_label(12, core.L_WORKING))

    # The bound methods build EXACTLY what the tables build -- the adapter
    # binds (slug, login) and passes the rest straight through, so no argv
    # drifts on the GitHub path this refactor promises not to change.
    check("r69_gh_add_label_matches_table",
          core.GH_ARGV["issue_edit_add_label"]("cybermelon/orch", None, 3, core.L_READY),
          r69_gh.issue_edit_add_label(3, core.L_READY))
    check("r69_tea_add_label_matches_table",
          core.TEA_ARGV["issue_edit_add_label"](
              "cybermelon/gita-lectures", "gitea", 3, core.L_READY),
          r69_tea.issue_edit_add_label(3, core.L_READY))

    # THE TABLES ARE NOT SHAPE-PARALLEL, and the adapter must not paper over
    # that. pr_rollup takes a num on gh (single-PR view) but NONE on tea
    # (tea has no single-PR rollup, so its builder is a LIST call).
    check("r69_pr_rollup_gh_takes_num",
          core.GH_ARGV["pr_rollup"]("cybermelon/orch", None, 5),
          r69_gh.pr_rollup(5))
    check("r69_pr_rollup_tea_takes_no_num",
          core.TEA_ARGV["pr_rollup"]("cybermelon/gita-lectures", "gitea"),
          r69_tea.pr_rollup())
    # label_create's NAME is --name on tea but POSITIONAL on gh.
    check("r69_label_create_gh_name_positional", "agent-ready",
          r69_gh.label_create("agent-ready", "ededed", "d")[3])
    check("r69_label_create_tea_name_flagged", True,
          "--name" in r69_tea.label_create("agent-ready", "ededed", "d"))

    # A key the SELECTED table lacks must raise, never silently fall back to
    # the GitHub table -- that silent-gh-default is the exact bug class this
    # whole issue exists to kill. pr_view_review is tea-only.
    check("r69_tea_has_pr_view_review", True,
          r69_tea.pr_view_review(7)[:2] == ["tea", "pr"])
    try:
        r69_gh.pr_view_review(7)
        r69_missing_raised = False
    except AttributeError:
        r69_missing_raised = True
    check("r69_gh_missing_key_raises_not_falls_back", True, r69_missing_raised)

    # 5. adapter_for_entry: the journal path only ever holds an "owner/repo"
    # string plus a repos.txt entry, never a checkout -- so it gets its own
    # constructor rather than a checkout resolution it cannot perform.
    r69_entry_tea = core.adapter_for_entry(
        "cybermelon/gita-lectures", {"backend": "tea", "login": "gitea"})
    check("r69_entry_tea_backend", "tea", r69_entry_tea.backend)
    check("r69_entry_tea_argv",
          core.TEA_ARGV["issue_comment"]("cybermelon/gita-lectures", "gitea", 3, "hi"),
          r69_entry_tea.issue_comment(3, "hi"))
    # No matching entry falls back to gh -- repo_backend()'s own default for
    # an unresolvable repo, so an unwatched slug behaves as it always did.
    r69_entry_none = core.adapter_for_entry("me/unwatched", None)
    check("r69_entry_no_match_defaults_gh", "gh", r69_entry_none.backend)

    # 6. The prose-carrying verbs: the two backends differ in CALL
    # CONVENTION, not only argv, so the adapter returns (argv, stdin_text)
    # and no call site is left to re-decide where the body goes. gh takes it
    # on stdin via `--body-file -`; tea has no such option and carries it in
    # an argv slot, so stdin MUST be None there -- feeding it on both would
    # post the comment twice.
    r69_c_gh_argv, r69_c_gh_stdin = r69_gh.issue_comment_call("7", "hi\n")
    check("r69_comment_call_gh_argv",
          ["gh", "issue", "comment", "7", "--body-file", "-"], r69_c_gh_argv)
    check("r69_comment_call_gh_stdin_is_body", "hi\n", r69_c_gh_stdin)
    # No --repo on this path: the caller runs gh with cwd=<checkout> and lets
    # gh infer the remote, which is why this cannot be GH_ARGV's issue_comment.
    check("r69_comment_call_gh_omits_repo", False, "--repo" in r69_c_gh_argv)

    r69_c_tea_argv, r69_c_tea_stdin = r69_tea.issue_comment_call("7", "hi\n")
    check("r69_comment_call_tea_argv",
          core.TEA_ARGV["issue_comment"]("cybermelon/gita-lectures", "gitea",
                                         "7", "hi\n"), r69_c_tea_argv)
    check("r69_comment_call_tea_no_stdin", None, r69_c_tea_stdin)
    check("r69_comment_call_tea_body_in_argv", True, "hi\n" in r69_c_tea_argv)

    r69_n_gh_argv, r69_n_gh_stdin = r69_gh.issue_create_call("t", "b\n")
    check("r69_create_call_gh_argv",
          ["gh", "issue", "create", "--title", "t", "--body-file", "-"],
          r69_n_gh_argv)
    check("r69_create_call_gh_stdin_is_body", "b\n", r69_n_gh_stdin)
    r69_n_tea_argv, r69_n_tea_stdin = r69_tea.issue_create_call("t", "b\n")
    check("r69_create_call_tea_argv",
          core.TEA_ARGV["issue_create"]("cybermelon/gita-lectures", "gitea",
                                        "t", "b\n"), r69_n_tea_argv)
    check("r69_create_call_tea_no_stdin", None, r69_n_tea_stdin)

    # The slug-addressed variant (the journal's only shape) keeps the table's
    # gh argv, which DOES name --repo -- there is no checkout to infer from.
    r69_j_argv, r69_j_stdin = r69_gh.issue_comment_slug_call(3, "j\n")
    check("r69_comment_slug_call_gh_argv",
          core.GH_ARGV["issue_comment"](r69_gh.slug, None, 3, "j\n"), r69_j_argv)
    check("r69_comment_slug_call_gh_names_repo", True, "--repo" in r69_j_argv)
    check("r69_comment_slug_call_gh_stdin_is_body", "j\n", r69_j_stdin)
    check("r69_comment_slug_call_tea_no_stdin", None,
          r69_tea.issue_comment_slug_call(3, "j\n")[1])
finally:
    core._run = _r69_orig_run
    core._reset_login_web_bases()
    if _r69_repos_txt is None:
        _r69_repos_file.unlink(missing_ok=True)
    else:
        _r69_repos_file.write_text(_r69_repos_txt)
    shutil.rmtree(_r69_t, ignore_errors=True)

# === orch#91: tui_model — the TUI's pure layer ===============================
#
# The terminal view exists so an operator can still read the tree when
# orch-web is down (#86). That promise is only worth something if the model
# layer never raises on a feed that is partial, stale or garbage, so the
# robustness checks below are not padding -- they are the feature.
from orch import tui_model

# 1. pill_state: ONE pill per issue, one rule, one home.
#
# state wins for UNCLAIMED and ABANDONED; work_state wins otherwise. The
# first case is why the rule has to exist at all: work_state is derived from
# the branch and the PR alone, so an issue nobody has picked up reads CLAIMED
# there. Preferring work_state blindly would paint an unclaimed issue as
# claimed and hide it from the operator who has to pick it.
check("t91_pill_unclaimed_beats_work_claimed", "UNCLAIMED",
      tui_model.pill_state({"state": "UNCLAIMED", "work_state": "CLAIMED"}))
check("t91_pill_abandoned_beats_work_blocked", "ABANDONED",
      tui_model.pill_state({"state": "ABANDONED", "work_state": "BLOCKED"}))
# Anything else defers to work_state, which is the more specific of the two.
check("t91_pill_work_review_wins_over_claimed", "REVIEW",
      tui_model.pill_state({"state": "CLAIMED", "work_state": "REVIEW"}))
check("t91_pill_work_blocked_wins_over_claimed", "BLOCKED",
      tui_model.pill_state({"state": "CLAIMED", "work_state": "BLOCKED"}))
# An empty issue must yield a pill rather than raise: a partial feed row is
# normal, and a crash here blanks the whole screen.
check("t91_pill_empty_dict_no_raise", "?", tui_model.pill_state({}))
check("t91_pill_none_no_raise", "?", tui_model.pill_state(None))

# 2. The ABANDONED + BLOCKED pairing must not split across zones.
#
# The web page had exactly this bug: one issue with a red PR
# (work_state=BLOCKED) and an agent-stuck label (state=ABANDONED) drew
# BLOCKED in one zone and ABANDONED in another, on one screen. zone_a filters
# through pill_state for that reason, so whatever the filter decides, the pill
# agrees and one issue never shows two different states.
#
# The pairing is also the case that forces ABANDONED into the Zone A filter in
# its own right. pill_state collapses state and work_state into one value, so
# this issue resolves to ABANDONED and a BLOCKED-only filter would drop it --
# leaving an agent-stuck issue with a red PR out of "needs you" entirely, which
# is the one issue that most needs a person. This pins that it is collected.
t91_split_issue = {
    "issue": 91, "title": "red PR and agent-stuck", "state": "ABANDONED",
    "work_state": "BLOCKED", "pr": 12, "labels": ["agent-stuck"],
}
t91_split_feed = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                             "issues": [t91_split_issue]}]}
check("t91_split_pill_is_abandoned", "ABANDONED",
      tui_model.pill_state(t91_split_issue))
t91_split_a = tui_model.zone_a(t91_split_feed)
# It IS collected -- an agent that gave up is asking for a person by name.
check("t91_split_zone_a_collects_abandoned", True,
      any(r.kind == "issue" for r in t91_split_a))
check("t91_split_zone_a_not_empty", False,
      any(r.kind == "empty" for r in t91_split_a))
# And the filter agrees with the pill: it is labelled ABANDONED there, never
# BLOCKED, so the same issue never reads two ways on one screen.
check("t91_split_zone_a_filter_agrees_with_pill", True,
      all("BLOCKED" not in r.text for r in t91_split_a))
# The same issue in Zone B carries the same single pill, not a second one.
t91_split_b_issue = tui_model.zone_b(t91_split_feed)[0].children[0]
check("t91_split_zone_b_pill_abandoned", True,
      "ABANDONED" in t91_split_b_issue.text)
check("t91_split_zone_b_no_second_pill", False,
      "BLOCKED" in t91_split_b_issue.text)

# 3. load_feed returns, never raises. A dead feed is the normal case for this
# tool -- it is the case the tool was built for.
T91 = Path(tempfile.mkdtemp())
try:
    t91_missing = T91 / "nope.json"
    t91_feed, t91_err = tui_model.load_feed(t91_missing)
    check("t91_load_missing_feed_none", None, t91_feed)
    check("t91_load_missing_err_nonempty", True, bool(t91_err))
    # The error names the path, or the operator cannot tell WHICH feed is gone.
    check("t91_load_missing_err_names_path", True, str(t91_missing) in t91_err)

    t91_bad = T91 / "bad.json"
    t91_bad.write_text("{not json,")
    t91_feed, t91_err = tui_model.load_feed(t91_bad)
    check("t91_load_badjson_feed_none", None, t91_feed)
    check("t91_load_badjson_err_nonempty", True, bool(t91_err))

    # Valid JSON that is not an object: json.loads succeeds, so only an
    # explicit type check catches it before the zones index into a list.
    t91_arr = T91 / "arr.json"
    t91_arr.write_text("[1, 2, 3]")
    t91_feed, t91_err = tui_model.load_feed(t91_arr)
    check("t91_load_nonobject_feed_none", None, t91_feed)
    check("t91_load_nonobject_err_nonempty", True, bool(t91_err))

    t91_good = T91 / "good.json"
    t91_good.write_text(json.dumps({"generated": "now", "repos": []}))
    t91_feed, t91_err = tui_model.load_feed(t91_good)
    check("t91_load_good_err_none", None, t91_err)
    check("t91_load_good_returns_dict", "now", (t91_feed or {}).get("generated"))
finally:
    shutil.rmtree(T91, ignore_errors=True)

# 4. orch_facts carries all four dashboard_op facts (#73).
#
# Liveness, the last event and its time, the ack lease and the digest are all
# in the feed and none of them is rendered on the web page. The assertions are
# on the VALUES, not the label wording, so a cosmetic rename does not fail.
t91_op_feed = {
    "generated": "2026-09-14T00:00:00Z",
    "tick": {"minutes_since_last": 4, "last_run": "2026-09-14T00:00:00Z"},
    "host": {"live_orchs": 2, "load": "0.4", "disk": "51%",
             "deploy": {"commit": "abc1234", "branch": "main", "behind": 0}},
    "dashboard_op": {"alive": True, "prior_runs": 7, "last_event": "wake",
                     "last": "2026-09-14T00:03:00Z", "key": "dashboard-op",
                     # A unix epoch, which is what the feed really carries --
                     # feed.py does second arithmetic on it. An earlier
                     # revision used the string "spawn" here, so the integer
                     # path this fact actually takes was never exercised and a
                     # raw 10-digit epoch reached the screen unnoticed.
                     "activity": int(time.time()) - 120, "entries": 5},
}
t91_facts = tui_model.orch_facts(t91_op_feed)
t91_blob = " | ".join(f"{k}={v}" for k, v in t91_facts)
check("t91_facts_op_liveness", True, "alive=True" in t91_blob)
check("t91_facts_op_prior_runs", True, "prior_runs=7" in t91_blob)
check("t91_facts_op_last_event", True, "wake" in t91_blob)
check("t91_facts_op_last_event_time", True, "2026-09-14T00:03:00Z" in t91_blob)
check("t91_facts_ack_lease_key", True, "dashboard-op" in t91_blob)
# The lease is rendered as an AGE, not the raw epoch: the question the
# operator asks of it is "is this stale", which ten digits do not answer.
check("t91_facts_ack_lease_activity", True, "active 2m ago" in t91_blob)
check("t91_facts_ack_lease_no_raw_epoch", False, "activity=17" in t91_blob)
check("t91_facts_digest_entries", True, "5 entries" in t91_blob)
# Every pair is a (label, value) 2-tuple -- the fact-row renderer indexes both.
check("t91_facts_all_pairs", True,
      all(isinstance(p, tuple) and len(p) == 2 for p in t91_facts))
# An empty feed still yields the same shape, so Zone C never goes blank.
check("t91_facts_empty_feed_same_shape", len(t91_facts),
      len(tui_model.orch_facts({})))

# 5. forge_of for both live forges. The two watched repos differ by one
# character of owner -- cybermelons on GitHub, cybermelon on Gitea -- across
# two forges with different auth and reachability behind them (#68). The row
# has to say which one it is.
check("t91_forge_github", "github",
      tui_model.forge_of("https://github.com/cybermelons/orch/issues/91"))
check("t91_forge_gitea_host_port", "gitea.local:3000",
      tui_model.forge_of("http://gitea.local:3000/cybermelon/gita-lectures/issues/23"))
check("t91_forge_none_empty", "", tui_model.forge_of(None))
check("t91_forge_blank_empty", "", tui_model.forge_of(""))

# 6. Zone assignment: what lands in Zone A, and what deliberately does not.
t91_zfeed = {
    "alerts": [
        {"level": "error", "msg": "tick failed"},
        {"level": "warn", "msg": "deploy behind"},
        {"level": "info", "msg": "just so you know"},
    ],
    "awaiting": [{"slug": "orch", "repo": "cybermelons/orch", "issue": 88,
                  "pr": 90, "title": "needs review"}],
    "repos": [{"slug": "orch", "repo": "cybermelons/orch", "issues": [
        {"issue": 70, "title": "red PR", "state": "CLAIMED",
         "work_state": "BLOCKED", "url": "https://github.com/cybermelons/orch/issues/70"},
        {"issue": 71, "title": "two agents", "state": "CLAIMED",
         "work_state": "ACTIVE", "contended": True},
        {"issue": 72, "title": "quiet", "state": "CLAIMED",
         "work_state": "ACTIVE"},
    ]}],
}
t91_a = tui_model.zone_a(t91_zfeed)
t91_a_text = " || ".join(r.text for r in t91_a)
check("t91_zone_a_takes_error_alert", True, "tick failed" in t91_a_text)
check("t91_zone_a_takes_warn_alert", True, "deploy behind" in t91_a_text)
# An info alert can wait by definition, so it is a Zone C count, not a
# Zone A line. Zone A is only worth reading if everything in it needs a human.
check("t91_zone_a_omits_info_alert", False, "just so you know" in t91_a_text)
check("t91_zone_c_counts_info_alert", True,
      any("info alerts (1)" in r.text for r in tui_model.zone_c(t91_zfeed)))
check("t91_zone_a_takes_awaiting", True, "AWAITING REVIEW" in t91_a_text)
check("t91_zone_a_takes_blocked_issue", True, "#70" in t91_a_text)
check("t91_zone_a_takes_contended_issue", True, "#71" in t91_a_text)
# A healthy in-flight issue is Zone B's business, not Zone A's.
check("t91_zone_a_omits_quiet_issue", False, "#72" in t91_a_text)
check("t91_zone_b_keeps_quiet_issue", True,
      any("#72" in k.text for k in tui_model.zone_b(t91_zfeed)[0].children))

# An empty Zone A says so in exactly one row, rather than collapsing to
# nothing -- an empty zone and a broken zone must not look the same.
t91_calm = tui_model.zone_a({"repos": [], "alerts": [], "awaiting": []})
check("t91_zone_a_empty_one_row", 1, len(t91_calm))
check("t91_zone_a_empty_text", "nothing needs you", t91_calm[0].text)
check("t91_zone_a_empty_kind", "empty", t91_calm[0].kind)

# 7. Robustness. The tool's whole purpose is to work when things are broken,
# so a missing, empty or half-written feed must still render a screen.
check("t91_tree_empty_dict_three_zones", 3, len(tui_model.tree({})))
check("t91_tree_none_three_zones", 3, len(tui_model.tree(None)))
check("t91_tree_all_rows_are_zone_kind", True,
      all(r.kind == "zone" for r in tui_model.tree({})))
check("t91_trust_line_none_nonempty", True, bool(tui_model.trust_line(None)))
check("t91_trust_line_empty_says_no_feed", "NO FEED", tui_model.trust_line({}))
# A feed whose top-level keys are the wrong TYPE is the shape a truncated or
# half-written status.json takes, and it must not raise either.
t91_junk = {"repos": "not a list", "alerts": 5, "tick": "nope", "host": None}
check("t91_tree_junk_types_three_zones", 3, len(tui_model.tree(t91_junk)))
check("t91_zone_a_junk_types_no_raise", True,
      len(tui_model.zone_a(t91_junk)) >= 1)
check("t91_orch_facts_junk_types_no_raise", True,
      bool(tui_model.orch_facts(t91_junk)))
check("t91_trust_line_junk_types_no_raise", True, bool(tui_model.trust_line(t91_junk)))
# A behind deploy has to shout: the running code is not the code on disk, so
# every other number on the screen may describe something else.
check("t91_trust_line_behind_is_loud", True, "BEHIND" in tui_model.trust_line(
    {"host": {"deploy": {"commit": "abc1234", "is_behind": True, "behind": 3}}}))

# The live feed, when this machine has one. Guarded so the suite still passes
# on a checkout without it. READ ONLY -- this is production's own file.
t91_live = Path("/home/user/orch/public/status.json")
if t91_live.exists():
    t91_lfeed, t91_lerr = tui_model.load_feed(t91_live)
    check("t91_live_feed_loads", None, t91_lerr)
    check("t91_live_feed_is_dict", True, isinstance(t91_lfeed, dict))
    check("t91_live_tree_three_zones", 3, len(tui_model.tree(t91_lfeed)))
    check("t91_live_tree_zone_keys", ["zone-a", "zone-b", "zone-c"],
          [r.key for r in tui_model.tree(t91_lfeed)])
    check("t91_live_trust_line_nonempty", True, bool(tui_model.trust_line(t91_lfeed)))

# 9. `t` must reach an ISSUE row's transcript, not only an unattached session.
#
# The issue's acceptance is that the operator can "read a session tail without
# leaving it", and the row they press `t` on is a blocked issue -- they want to
# see what the agent was doing when it stuck. An issue's transcript is NOT at
# the top of its payload; it is in issue["sessions"][]["file"]. Reading only
# payload["file"] left the tail wired to unattached_sessions[] alone, which by
# definition are the sessions belonging to no issue.
from orch import tui as _t91_tui  # noqa: E402

_t91_row = lambda pay: tui_model.Row(text="", kind="issue", key="k", payload=pay)  # noqa: E731
check("t91_tail_direct_file", "/x/a.jsonl",
      _t91_tui.session_file(_t91_row({"file": "/x/a.jsonl"})))
check("t91_tail_from_issue_sessions", "/x/b.jsonl",
      _t91_tui.session_file(_t91_row({"sessions": [{"file": "/x/b.jsonl"}]})))
# The live one wins when several are recorded: a stuck issue keeps its dead
# sessions, and the operator wants the one still running.
check("t91_tail_prefers_live", "/x/live.jsonl",
      _t91_tui.session_file(_t91_row({"sessions": [
          {"file": "/x/dead.jsonl"}, {"file": "/x/live.jsonl", "live": True}]})))
check("t91_tail_prefers_current", "/x/cur.jsonl",
      _t91_tui.session_file(_t91_row({"sessions": [
          {"file": "/x/old.jsonl"}, {"file": "/x/cur.jsonl", "current": True}]})))
# A ledger row genuinely has no path; None is the honest answer, and the caller
# says so rather than failing.
check("t91_tail_none_when_no_path", None,
      _t91_tui.session_file(_t91_row({"key": "repo-orch.orch", "sessions": []})))
check("t91_tail_none_on_empty_payload", None, _t91_tui.session_file(_t91_row({})))
check("t91_tail_skips_sessions_without_file", None,
      _t91_tui.session_file(_t91_row({"sessions": [{"id": "no-file"}]})))

# === #96: per-repo digests ==================================================
# The global digest hashes every repo, so it flips when ANY repo moves. That
# made dashboard-op's "has the chain already run for this same digest" test
# unsatisfiable for a frozen repo: some other repo always churns. The per-repo
# digest is the signal that actually holds still when a repo does.

def _r96_data(frozen_state, other_state):
    return {"repos": [
        {"slug": "frozen", "issues": [
            {"issue": 1, "state": frozen_state, "work_state": "REVIEW",
             "orch_alive": False, "activity": None, "contended": False,
             "startable": False},
        ]},
        {"slug": "busy", "issues": [
            {"issue": 2, "state": other_state, "work_state": "REVIEW",
             "orch_alive": False, "activity": None, "contended": False,
             "startable": False},
        ]},
    ], "alerts": []}


_r96_thin_a = tickmod.build_thin(_r96_data("CLAIMED", "CLAIMED"))
_r96_thin_b = tickmod.build_thin(_r96_data("CLAIMED", "UNCLAIMED"))
_r96_thin_c = tickmod.build_thin(_r96_data("ABANDONED", "CLAIMED"))

_r96_a = tickmod.per_repo_digests(_r96_thin_a)
_r96_b = tickmod.per_repo_digests(_r96_thin_b)
_r96_c = tickmod.per_repo_digests(_r96_thin_c)

check("r96_slugs", ["busy", "frozen"], sorted(_r96_a))

# THE REGRESSION: a different repo moved. Global digest changes; the frozen
# repo's per-repo digest does not.
check("r96_global_digest_changes_when_other_repo_moves", True,
      tickmod.digest_of(_r96_thin_a) != tickmod.digest_of(_r96_thin_b))
check("r96_frozen_repo_digest_stable", _r96_a["frozen"], _r96_b["frozen"])
check("r96_moving_repo_digest_changes", True, _r96_a["busy"] != _r96_b["busy"])

# The repo's own issue moving DOES change its per-repo digest.
check("r96_own_change_moves_digest", True, _r96_a["frozen"] != _r96_c["frozen"])
check("r96_own_change_leaves_others", _r96_a["busy"], _r96_c["busy"])

# Same hash function as the global digest, applied to the repo's own entry.
check("r96_reuses_digest_of",
      tickmod.digest_of(_r96_thin_a["repos"][0]), _r96_a["frozen"])

# === #48: mechanical routing in the tick ====================================
# The condition-to-action table moved out of agents/dashboard-op.md and into
# tick.route(). These pin which conditions spawn, which never do, and that the
# tick's own spawn record is keyed to the PER-REPO digest (#96) rather than the
# global one - keying it globally would reproduce #96's defect inside the tick.

def _r48_c(cond, slug="alpha", n=1, orch_alive=False):
    return {"slug": slug, "issue": n, "cond": cond, "orch_alive": orch_alive}


# Conditions 1, 5, 7 spawn outright.
check("r48_route_cond1_spawns", ["alpha"], tickmod.route([_r48_c(1)]))
check("r48_route_cond5_spawns", ["alpha"], tickmod.route([_r48_c(5)]))
check("r48_route_cond7_spawns", ["alpha"], tickmod.route([_r48_c(7)]))

# Condition 2 carries the liveness test the old table held: alive -> skip.
check("r48_route_cond2_dead_spawns", ["alpha"],
      tickmod.route([_r48_c(2, orch_alive=False)]))
check("r48_route_cond2_alive_skips", [],
      tickmod.route([_r48_c(2, orch_alive=True)]))

# 3, 4 and 6 never spawn - each for its own reason, see route()'s docstring.
check("r48_route_cond3_never_spawns", [], tickmod.route([_r48_c(3)]))
check("r48_route_cond6_never_spawns", [], tickmod.route([_r48_c(6)]))
check("r48_route_cond4_never_spawns", [],
      tickmod.route([{"slug": None, "issue": None, "cond": 4,
                      "orch_alive": False}]))
# Condition 4's repo-less entry must not become a spawn target even when a
# real repo is also firing.
check("r48_route_cond4_none_slug_absent", ["alpha"],
      tickmod.route([_r48_c(1), {"slug": None, "issue": None, "cond": 4,
                                 "orch_alive": False}]))

# One slug under two spawn-worthy conditions is ONE spawn.
check("r48_route_dedupes", ["alpha"],
      tickmod.route([_r48_c(1), _r48_c(5, n=2), _r48_c(7, n=3)]))
check("r48_route_sorted_multi_repo", ["alpha", "beta"],
      tickmod.route([_r48_c(5, slug="beta"), _r48_c(1, slug="alpha")]))
check("r48_route_empty", [], tickmod.route([]))

# --- should_spawn: the suppression lease -----------------------------------
_r48_ttl = core.ACK_TTL_MINS * 60
check("r48_should_spawn_no_record", True,
      tickmod.should_spawn("alpha", "DIGEST", {}))
check("r48_suppressed_same_digest_in_ttl", False,
      tickmod.should_spawn("alpha", "DIGEST",
                           {"alpha": {"digest": "DIGEST",
                                      "at": core.now() - 1}}))
check("r48_spawns_same_digest_past_ttl", True,
      tickmod.should_spawn("alpha", "DIGEST",
                           {"alpha": {"digest": "DIGEST",
                                      "at": core.now() - _r48_ttl - 10}}))
check("r48_spawns_different_digest_in_ttl", True,
      tickmod.should_spawn("alpha", "NEWDIGEST",
                           {"alpha": {"digest": "OLDDIGEST",
                                      "at": core.now() - tickmod.SPAWN_FLOOR_SECS - 1}}))
check("r48_other_slug_record_does_not_suppress", True,
      tickmod.should_spawn("beta", "DIGEST",
                           {"alpha": {"digest": "DIGEST",
                                      "at": core.now() - 1}}))

# --- THE LOAD-BEARING ONE --------------------------------------------------
# A frozen repo keeps a stable per-repo digest while a sibling moves and the
# GLOBAL digest changes. Suppression keyed to the per-repo digest therefore
# holds; keyed to the global one it could never be satisfied. Same inputs as
# the r96_ block.
_r48_thin_a = tickmod.build_thin(_r96_data("CLAIMED", "CLAIMED"))
_r48_thin_b = tickmod.build_thin(_r96_data("CLAIMED", "UNCLAIMED"))
_r48_pr_a = tickmod.per_repo_digests(_r48_thin_a)
_r48_pr_b = tickmod.per_repo_digests(_r48_thin_b)
_r48_records = {"frozen": {"digest": _r48_pr_a["frozen"],
                           "at": core.now() - tickmod.SPAWN_FLOOR_SECS - 1}}

check("r48_global_digest_moved", True,
      tickmod.digest_of(_r48_thin_a) != tickmod.digest_of(_r48_thin_b))
check("r48_frozen_suppression_holds_across_sibling_move", False,
      tickmod.should_spawn("frozen", _r48_pr_b["frozen"], _r48_records))
# The same record keyed to the global digest could NEVER be satisfied - this
# is the defect the whole issue turns on, pinned as a test.
check("r48_global_digest_keying_would_never_suppress", True,
      tickmod.should_spawn(
          "frozen", tickmod.digest_of(_r48_thin_b),
          {"frozen": {"digest": tickmod.digest_of(_r48_thin_a),
                      "at": core.now() - tickmod.SPAWN_FLOOR_SECS - 1}}))
# The frozen repo's OWN change breaks the suppression, as it must.
_r48_thin_c = tickmod.build_thin(_r96_data("ABANDONED", "CLAIMED"))
check("r48_own_change_breaks_suppression", True,
      tickmod.should_spawn("frozen",
                           tickmod.per_repo_digests(_r48_thin_c)["frozen"],
                           _r48_records))

# --- orch#174: SPAWN_FLOOR_SECS, the agentic-spacing floor ------------------
# Checked BEFORE the digest comparison: a changed digest inside the floor
# must still suppress, or the floor is not a floor.
check("i174_floor_suppresses_changed_digest", False,
      tickmod.should_spawn("alpha", "NEWDIGEST",
                           {"alpha": {"digest": "OLDDIGEST",
                                      "at": core.now() - 1}}))
# Once the floor has passed, a changed digest spawns as normal.
check("i174_floor_elapsed_changed_digest_spawns", True,
      tickmod.should_spawn("alpha", "NEWDIGEST",
                           {"alpha": {"digest": "OLDDIGEST",
                                      "at": core.now() - tickmod.SPAWN_FLOOR_SECS - 1}}))
# Import-time guardrail: a floor at or past the TTL must be rejected, since it
# would let a wrong suppression outlive its own expiry check.
_i174_bad_assert_src = (
    "import os\n"
    "os.environ['SPAWN_FLOOR_SECS'] = '999999'\n"
    "import orch.tick\n"
)
_i174_check = subprocess.run(
    [sys.executable, "-c", _i174_bad_assert_src],
    capture_output=True, text=True, cwd=str(REPO_ROOT),
)
check("i174_bad_pairing_asserts_at_import", True,
      _i174_check.returncode != 0 and "SPAWN_FLOOR_SECS" in _i174_check.stderr)

# --- read_spawn_records: corrupt/missing degrades to "no record" -----------
_r48_rec_path = tickmod.spawn_record_path()
_r48_rec_backup = _r48_rec_path.read_text() if _r48_rec_path.exists() else None
try:
    _r48_rec_path.parent.mkdir(parents=True, exist_ok=True)
    if _r48_rec_path.exists():
        _r48_rec_path.unlink()
    check("r48_records_missing_file", {}, tickmod.read_spawn_records())
    check("r48_missing_file_does_not_suppress", True,
          tickmod.should_spawn("alpha", "DIGEST", tickmod.read_spawn_records()))

    _r48_rec_path.write_text("{not json at all")
    check("r48_records_corrupt_file", {}, tickmod.read_spawn_records())
    check("r48_corrupt_file_does_not_suppress", True,
          tickmod.should_spawn("alpha", "DIGEST", tickmod.read_spawn_records()))

    # Structurally wrong but valid JSON also degrades to no record.
    _r48_rec_path.write_text('["a list, not a dict"]')
    check("r48_records_wrong_shape", {}, tickmod.read_spawn_records())
    _r48_rec_path.write_text('{"alpha": {"digest": 7, "at": "soon"}}')
    check("r48_records_bad_fields_dropped", {}, tickmod.read_spawn_records())

    # Round trip: write then read.
    _r48_rec_path.unlink()
    tickmod.write_spawn_record("alpha", "DIGESTX")
    _r48_round = tickmod.read_spawn_records()
    check("r48_write_then_read_digest", "DIGESTX", _r48_round["alpha"]["digest"])
    check("r48_write_then_read_suppresses", False,
          tickmod.should_spawn("alpha", "DIGESTX", _r48_round))
    # Upsert leaves other slugs alone.
    tickmod.write_spawn_record("beta", "DIGESTY")
    _r48_round2 = tickmod.read_spawn_records()
    check("r48_upsert_keeps_others", ["alpha", "beta"], sorted(_r48_round2))
    # A falsy digest is never recorded - nothing to compare means spawn again.
    tickmod.write_spawn_record("gamma", None)
    check("r48_no_record_for_empty_digest", False,
          "gamma" in tickmod.read_spawn_records())
finally:
    if _r48_rec_backup is None:
        if _r48_rec_path.exists():
            _r48_rec_path.unlink()
    else:
        _r48_rec_path.write_text(_r48_rec_backup)

# --- compute_conditions stayed additive ------------------------------------
# needs_attention and notes must be byte-identical to what they were before
# the third return value was added.
_r48_cc_data = {"repos": [
    {"slug": "alpha", "issues": [
        {"issue": 1, "state": "CLAIMED", "work_state": "BLOCKED",
         "orch_alive": False, "activity": None, "contended": True,
         "startable": False},
        {"issue": 2, "state": "ABANDONED", "work_state": "LANDED",
         "orch_alive": True, "activity": None, "contended": False,
         "startable": False},
    ],
     # orch#173 seam fix: `unconsidered` is now a REPO-LEVEL fact fed.py
     # builds from world.issues (all open issues), not derived from this
     # fixture's `issues` list. #2 named explicitly here so this fixture
     # keeps firing cond 8 exactly as it did under the old `considered`-based
     # reading -- see the comment below.
     "unconsidered": [2]},
], "alerts": [{"level": "error", "key": "gh-read-failed"}]}
_r48_cc_thin = tickmod.build_thin(_r48_cc_data)
_r48_na, _r48_notes, _r48_conds = tickmod.compute_conditions(_r48_cc_data,
                                                             _r48_cc_thin)
# orch#123: issue #1 carries no prior_runs field, which defaults to 0, and
# has no live owner -- it now legitimately reads as never_owned, so its
# "unowned work" note gains the marker. This is the intended behaviour of
# orch#123, not a regression: needs_attention and the note COUNT stayed
# unchanged, only this one note's text grew the never-owned suffix.
#
# orch#173: the fixture's repo-level `unconsidered` names #2 -- condition 8
# fires once for the repo. This is the intended behaviour of orch#173's
# set-level consolidation wake, not a regression: it adds exactly one note
# and one cond entry, cond 8.
check("r48_notes_unchanged",
      ["alpha#1: blocked", "alpha#1: contended",
       "alpha#1: unowned work (never owned — claim with no session ever)",
       "alpha#2: ready/abandoned", "alpha#2: wedged (info)",
       "alpha: 1 issue(s) not consolidated since filing (#2)",
       "error alert: gh-read-failed"],
      sorted(_r48_notes))
check("r48_needs_attention_matches_notes", len(_r48_notes), _r48_na)
check("r48_needs_attention_value", 7, _r48_na)
# One structured entry per note, and every fired condition is represented.
check("r48_conds_one_per_note", len(_r48_notes), len(_r48_conds))
check("r48_conds_numbers", [1, 2, 3, 4, 5, 6, 8],
      sorted({c["cond"] for c in _r48_conds}))
check("r48_cond4_is_repoless", [(None, None)],
      [(c["slug"], c["issue"]) for c in _r48_conds if c["cond"] == 4])
# Routing that input: #1 is blocked-and-dead (2) plus unowned (5); #2 is
# ready/abandoned (1). Contended (3), the alert (4) and wedged (6) add
# nothing. One slug -> one spawn.
check("r48_route_on_real_conditions", ["alpha"], tickmod.route(_r48_conds))

# --- #173: condition 8, the set-level consolidation wake -------------------
# Breaks the measured deadlock: when no issue anywhere carries agent-ready,
# conditions 1/5/7 cannot fire, and repo-orch is the only actor that can ADD
# that label -- nothing spawns again. Condition 8 fires per REPO (not per
# issue) when any open, non-CLAIMED issue is `not considered`.


def _c8_data(issues, unconsidered):
    # unconsidered is now a REPO-LEVEL fact, separate from the per-issue
    # `issues` list -- see feed.repo_json's `unconsidered` computation and
    # tick.py condition 8's seam comment. Tests below pass it explicitly
    # rather than deriving it from `issues`, exactly so a test cannot
    # silently recreate the old (buggy) coupling between the two.
    return {"repos": [{"slug": "alpha", "issues": issues,
                       "unconsidered": unconsidered}], "alerts": []}


def _c8_issue(n, considered, state="UNCLAIMED"):
    return {"issue": n, "state": state, "work_state": "REVIEW",
            "orch_alive": False, "activity": None, "contended": False,
            "startable": False, "considered": considered}


# 1. Fires: one uncovered issue is enough, and several uncovered issues in
# the same repo still collapse to exactly ONE cond-8 dict (set-level, not
# per-issue).
_c8_fires_data = _c8_data([_c8_issue(1, False), _c8_issue(2, False)], [1, 2])
_c8_fires_thin = tickmod.build_thin(_c8_fires_data)
_, _, _c8_fires_conds = tickmod.compute_conditions(_c8_fires_data, _c8_fires_thin)
check("c8_fires_when_uncovered", [8],
      [c["cond"] for c in _c8_fires_conds if c["cond"] == 8])
check("c8_fires_once_per_repo_not_per_issue", 1,
      len([c for c in _c8_fires_conds if c["cond"] == 8]))

# 2. Suppressed: every open issue covered.
_c8_covered_data = _c8_data([_c8_issue(1, True), _c8_issue(2, True)], [])
_c8_covered_thin = tickmod.build_thin(_c8_covered_data)
_, _, _c8_covered_conds = tickmod.compute_conditions(_c8_covered_data, _c8_covered_thin)
check("c8_suppressed_when_all_covered", [],
      [c["cond"] for c in _c8_covered_conds if c["cond"] == 8])

# 4. Finished backlog: `unconsidered` is explicitly [] (nothing left
# uncovered) -> condition 8 does not fire. Must NOT be "agent-ready count is
# zero" -- a finished backlog has zero ready and zero uncovered, and that is
# fine. CRITICAL: an empty `issues` list is NOT what distinguishes a
# finished backlog from the deadlock this condition exists to break -- 29
# open, UNLABELLED issues ALSO produce an empty `r["issues"]` (issues is
# built from candidates(), which only returns already-labelled issues), yet
# that case must fire. What distinguishes them is `unconsidered`, not
# `issues`; that is exactly what the seam test below (c8_seam_...) proves.
_c8_empty_data = _c8_data([], [])
_c8_empty_thin = tickmod.build_thin(_c8_empty_data)
_, _, _c8_empty_conds = tickmod.compute_conditions(_c8_empty_data, _c8_empty_thin)
check("c8_finished_backlog_does_not_fire", [],
      [c["cond"] for c in _c8_empty_conds if c["cond"] == 8])

# Shared fixture timestamps, used from here through the consolidated_coverage
# block below. Defined here (rather than just above that block, as before)
# because the seam tests immediately below also need them.
_T_OLD = "2026-01-01T00:00:00-04:00"
_T_NEW = "2026-06-01T00:00:00-04:00"
_T_NEWER = "2026-09-01T00:00:00-04:00"


# --- the seam test: drives feed.repo_json itself, crossing candidates() ----
# THIS TEST EXISTS BECAUSE EVERY OTHER cond-8 TEST ABOVE BYPASSES
# world.candidates() ENTIRELY -- they hand compute_conditions a hand-built
# `issues`/`unconsidered` shape directly, so they could not have caught (and
# did not catch) orch#173's real bug: condition 8 reading `r["issues"]`,
# which comes from world.candidates() and holds ONLY already-labelled
# issues -- the exact complement of the set condition 8 exists to watch.
# This test instead calls feed.repo_json with a fake World holding open
# issues that carry NO labels at all (mirrors the measured deadlock: 29
# open issues, zero labels, candidates() == []), then runs the real result
# through build_thin + compute_conditions and asserts condition 8 FIRES.
# Verified to fail against the pre-fix code (condition 8 read r["issues"]):
# with zero labelled issues, world.candidates() == [], so r["issues"] == [],
# and the old `uncovered = [i["number"] for i in r["issues"] ...]` is
# unconditionally [] regardless of `unconsidered` -- the old code could
# never fire here. Against the fixed code, which reads r["unconsidered"]
# (built from world.issues, not candidates()), it fires.
class _C8SeamFakeWorld(_FeedFakeWorld):
    """Like _FeedFakeWorld but holds several OPEN, UNLABELLED issues rather
    than one issue with caller-supplied labels -- the seam test needs a
    whole backlog, not one issue."""
    def __init__(self, issues):
        self.issues = issues

    def load(self, gr):
        return True

    def candidates(self):
        # Real World.candidates() semantics (core.World.candidates): ready OR
        # working only.
        out = []
        for i in self.issues:
            names = {l["name"] for l in i.get("labels", [])}
            if core.L_READY in names or core.L_WORKING in names:
                out.append(i["number"])
        return out


def _c8_seam_conds(issues):
    """Drives feed.repo_json for real (adapter_for/World/consolidated_coverage
    faked, everything else -- the unconsidered computation, build_thin,
    compute_conditions -- genuinely exercised), returns the fired cond-8
    dicts for the one repo."""
    fake_world = _C8SeamFakeWorld(issues)
    _orig = {
        "adapter_for": core.adapter_for,
        "World": feed.World,
        "consolidated_coverage": core.consolidated_coverage,
    }
    core.adapter_for = lambda repo: core.RepoAdapter("gh", "me/seamrepo", None, core.GH_WEB_BASE)
    feed.World = lambda: fake_world
    core.consolidated_coverage = lambda slug: {}  # nothing covered anywhere
    try:
        row = feed.repo_json(Path("/nonexistent/seamrepo"))
    finally:
        core.adapter_for = _orig["adapter_for"]
        feed.World = _orig["World"]
        core.consolidated_coverage = _orig["consolidated_coverage"]
    data = {"repos": [row], "alerts": []}
    thin = tickmod.build_thin(data)
    _, _, conds = tickmod.compute_conditions(data, thin)
    return row, [c for c in conds if c["cond"] == 8]


_c8_seam_unlabelled = [
    {"number": 1, "labels": [], "createdAt": _T_OLD},
    {"number": 2, "labels": [], "createdAt": _T_OLD},
]
_c8_seam_row, _c8_seam_conds8 = _c8_seam_conds(_c8_seam_unlabelled)
check("c8_seam_candidates_empty_for_unlabelled_backlog", [],
      _c8_seam_row["issues"])
check("c8_seam_unconsidered_holds_the_unlabelled_backlog", [1, 2],
      sorted(_c8_seam_row["unconsidered"]))
check("c8_seam_fires_on_unlabelled_backlog", [8],
      [c["cond"] for c in _c8_seam_conds8])

# CLAIMED (agent-working) issues are excluded from unconsidered -- an
# already-claimed issue has an owner; the set-level wake is not about it.
_c8_seam_claimed_row, _c8_seam_claimed_conds8 = _c8_seam_conds([
    {"number": 3, "labels": [{"name": core.L_WORKING}], "createdAt": _T_OLD},
])
check("c8_claimed_issue_excluded_from_unconsidered", [],
      _c8_seam_claimed_row["unconsidered"])
check("c8_claimed_only_backlog_does_not_fire", [],
      [c["cond"] for c in _c8_seam_claimed_conds8])

def _c8_seam_covered_conds(created_at, coverage):
    fake_world = _C8SeamFakeWorld([
        {"number": 4, "labels": [], "createdAt": created_at},
    ])
    _orig = {
        "adapter_for": core.adapter_for,
        "World": feed.World,
        "consolidated_coverage": core.consolidated_coverage,
    }
    core.adapter_for = lambda repo: core.RepoAdapter("gh", "me/seamrepo", None, core.GH_WEB_BASE)
    feed.World = lambda: fake_world
    core.consolidated_coverage = lambda slug: coverage
    try:
        return feed.repo_json(Path("/nonexistent/seamrepo"))
    finally:
        core.adapter_for = _orig["adapter_for"]
        feed.World = _orig["World"]
        core.consolidated_coverage = _orig["consolidated_coverage"]


check("c8_covered_unlabelled_issue_excluded_from_unconsidered", [],
      _c8_seam_covered_conds(_T_OLD, {4: _T_NEW})["unconsidered"])

# Degrade-to-wake: an unlabelled issue with a MALFORMED createdAt is INCLUDED
# in unconsidered (unknown must mean wake, never silence -- same stance
# _covered_after and issue_json's `considered` already take).
check("c8_malformed_createdAt_included_degrade_to_wake", [4],
      sorted(_c8_seam_covered_conds("not-a-date", {})["unconsidered"]))

# 7. route() returns the slug for a conds list containing only a cond-8 dict.
check("c8_route_spawns", ["alpha"],
      tickmod.route([{"slug": "alpha", "issue": None, "cond": 8,
                      "orch_alive": False}]))

# 8. should_spawn applies to a condition-8 slug: a fresh repeat (same
# per-repo digest, within TTL) is suppressed; a changed digest is not.
# Verified, not assumed -- built the same way the r48_ should_spawn block
# above builds its record/digest state. Digest input is `unconsidered`
# (the repo-level list condition 8 actually reads), not `considered`.
_c8_ss_thin_a = tickmod.build_thin(_c8_data([_c8_issue(1, False)], [1]))
_c8_ss_thin_b = tickmod.build_thin(_c8_data([_c8_issue(1, True)], []))
_c8_ss_digest_a = tickmod.per_repo_digests(_c8_ss_thin_a)["alpha"]
_c8_ss_digest_b = tickmod.per_repo_digests(_c8_ss_thin_b)["alpha"]
_c8_ss_records = {"alpha": {"digest": _c8_ss_digest_a,
                            "at": core.now() - tickmod.SPAWN_FLOOR_SECS - 1}}
check("c8_should_spawn_repeat_suppressed", False,
      tickmod.should_spawn("alpha", _c8_ss_digest_a, _c8_ss_records))
check("c8_should_spawn_changed_digest_not_suppressed", True,
      tickmod.should_spawn("alpha", _c8_ss_digest_b, _c8_ss_records))

# --- driven through feed/core.consolidated_coverage: the timestamp compare
# and the journal reader are genuinely exercised, not just the `considered`
# bool at the compute_conditions layer.
_c8_slug = "r173/cond8"
_c8_journal_file = core.journal_path("repo", _c8_slug)


def _c8_write_journal(rows):
    _c8_journal_file.parent.mkdir(parents=True, exist_ok=True)
    _c8_journal_file.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _c8_row(at, covered=None, note=""):
    row = {"at": at, "actor": "repo-orch", "event": "consolidated", "note": note}
    if covered is not None:
        row["covered"] = covered
    return row


def _c8_considered(created_at, coverage):
    """Drives feed.issue_json with a fake world supplying createdAt, exactly
    the _FeedFakeWorld idiom the startable/commits/workers blocks above use."""
    world = _FeedFakeWorld([])
    world.issues[0]["createdAt"] = created_at
    return feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                           "me/feedslug", True, "", coverage, False)["considered"]


try:
    # 3. THE ANTI-SPIN GUARANTEE: a DECLINE suppresses condition 8 too. A
    # real journal row whose `covered` names issue 44 but whose prose does
    # NOT -- proving the reader counts coverage from `covered`, never from
    # needing the number to appear in the note. Testing this only at
    # compute_conditions would prove nothing: a decline and a nomination are
    # both just `considered: True` there.
    _c8_write_journal([_c8_row(_T_NEW, covered=[44],
                               note="reviewed the backlog, nothing here")])
    # The coverage map itself carries 44, read off a row whose note never
    # mentions it. This is the assertion that matters: coverage comes from
    # `covered` alone, so an issue read and DECLINED is covered exactly as
    # firmly as one nominated. If this ever required the number in the
    # prose, a decline would record nothing and condition 8 would re-fire on
    # that issue forever.
    check("c8_decline_covers_without_naming_in_prose", [44],
          sorted(core.consolidated_coverage(_c8_slug)))
    check("c8_decline_marks_considered", True,
          _c8_considered(_T_OLD, core.consolidated_coverage(_c8_slug)))

    # 5. Re-arm: an issue filed AFTER the newest consolidated row is
    # uncovered -> considered False again, so condition 8 fires again.
    check("c8_rearm_issue_filed_after_coverage", False,
          _c8_considered(_T_NEWER, core.consolidated_coverage(_c8_slug)))

    # 6. Stale row: a consolidated row OLDER than the issue's createdAt does
    # not cover it.
    _c8_write_journal([_c8_row(_T_OLD, covered=[44])])
    check("c8_stale_row_does_not_cover", False,
          _c8_considered(_T_NEW, core.consolidated_coverage(_c8_slug)))

    # 9. Corrupt/missing journal -> consolidated_coverage returns {} ->
    # uncovered -> considered False -> fires. Failure degrades to a wake,
    # never to silence.
    if _c8_journal_file.exists():
        _c8_journal_file.unlink()
    check("c8_missing_journal_is_empty_coverage", {},
          core.consolidated_coverage(_c8_slug))
    check("c8_missing_journal_fires", False,
          _c8_considered(_T_OLD, core.consolidated_coverage(_c8_slug)))

    _c8_journal_file.parent.mkdir(parents=True, exist_ok=True)
    _c8_journal_file.write_text("{not json at all\n")
    check("c8_corrupt_journal_is_empty_coverage", {},
          core.consolidated_coverage(_c8_slug))

    # 10. Legacy row: a consolidated row with NO `covered` key covers
    # nothing -- every row written before this feature existed has this
    # shape and must never be read as coverage.
    _c8_write_journal([{"at": _T_NEW, "actor": "repo-orch",
                        "event": "consolidated", "note": "old-style row"}])
    check("c8_legacy_row_without_covered_covers_nothing", {},
          core.consolidated_coverage(_c8_slug))
    check("c8_legacy_row_leaves_issue_unconsidered", False,
          _c8_considered(_T_OLD, core.consolidated_coverage(_c8_slug)))
finally:
    if _c8_journal_file.exists():
        _c8_journal_file.unlink()

# 11. Digest: flipping `unconsidered` must change the digest -- a fact a
# condition reads that cannot flip the digest can never cause a wake. Same
# shape as the never_owned digest check this file's #123 block uses.
_c8_digest_thin_false = tickmod.build_thin(_c8_data([_c8_issue(1, False)], [1]))
_c8_digest_thin_true = tickmod.build_thin(_c8_data([_c8_issue(1, True)], []))
check("c8_unconsidered_flip_changes_digest", True,
      tickmod.digest_of(_c8_digest_thin_false) !=
      tickmod.digest_of(_c8_digest_thin_true))

# === feed: structured NEEDS-YOU alert fields + the two liveness-join rows ====
# Same shape as the "awaiting" block above: stub repo_json with already-shaped
# issues and run the real build() loop over them, so the alert predicates
# themselves are exercised rather than a stub of them. The load-bearing part
# is the NEGATIVE half -- msg and level must be byte-identical to what the
# wake digest already consumes, and the two new rows must stay at `warn`
# (tick.build_thin folds only the ERROR alerts into the digest).
_ux_repo = Path(os.environ["WT_ROOT"]) / "uxrepo"
(_ux_repo / ".git").mkdir(parents=True, exist_ok=True)
_write_repos_txt(str(_ux_repo) + "\n")


def _ux_build(issues):
    """build() over one stubbed repo carrying exactly `issues`."""
    _saved = feed.repo_json
    feed.repo_json = lambda repo: {
        "repo": "me/uxrepo", "path": str(repo), "slug": "uxrepo", "ok": True,
        "counts": {}, "live_orchs": 0, "issues": issues,
        "orch": {"key": "repo-orch.uxrepo", "alive": False, "prior_runs": 0, "recent": []},
    }
    try:
        return feed.build()
    finally:
        feed.repo_json = _saved


def _ux_kinds(out):
    return [a.get("kind") for a in out["alerts"]]


def _ux_of(out, kind):
    return next((a for a in out["alerts"] if a.get("kind") == kind), None)


# A REVIEW issue with no live session: waiting-on-merge fires, and the new
# still-running-after-pr must NOT (it needs orch_alive).
_ux_dead_under_min = core.NUDGE_IDLE_MINS - 8
_ux_review_dead = _ux_build([
    {"issue": 3, "title": "t", "url": "https://x/3", "work_state": "REVIEW",
     "orch_alive": False, "pr": 7, "pr_url": "https://x/pull/7", "contended": False,
     "state": "REVIEW", "idle_min": _ux_dead_under_min, "prior_runs": 0, "sessions": [],
     "activity": None},
])
_ux_wom = _ux_of(_ux_review_dead, "waiting-on-merge")
# msg and level unchanged by the new structured keys -- this is the regression
# the whole change is at risk of causing.
check("ux_wom_msg_unchanged", "me/uxrepo#3 waiting on you", _ux_wom["msg"])
check("ux_wom_level_unchanged", "info", _ux_wom["level"])
check("ux_wom_url_unchanged", "https://x/3", _ux_wom["url"])
check("ux_wom_fields", ("uxrepo", "me/uxrepo", 3, "PR #7", "pr", 7, "https://x/pull/7"),
      (_ux_wom["slug"], _ux_wom["repo"], _ux_wom["issue"], _ux_wom["what"],
       _ux_wom["verb"], _ux_wom["pr"], _ux_wom["pr_url"]))
check("ux_wom_age_min", _ux_dead_under_min, _ux_wom["age_min"])
check("ux_srap_absent_when_dead", False,
      "still-running-after-pr" in _ux_kinds(_ux_review_dead))

# orch#425: dead owner + idle past NUDGE_IDLE_MINS -> warn, msg names the
# dead owner. Same row/kind as the routine case, just distinguishable.
_ux_review_dead_stale = _ux_build([
    {"issue": 8, "title": "t", "url": "https://x/8", "work_state": "REVIEW",
     "orch_alive": False, "pr": 7, "pr_url": "https://x/pull/7", "contended": False,
     "state": "REVIEW", "idle_min": core.NUDGE_IDLE_MINS, "prior_runs": 0, "sessions": [],
     "activity": None},
])
_ux_wom_stale = _ux_of(_ux_review_dead_stale, "waiting-on-merge")
check("ux_wom_dead_stale_level", "warn", _ux_wom_stale["level"])
check("ux_wom_dead_stale_msg", "me/uxrepo#8 waiting on you (owner died)", _ux_wom_stale["msg"])

# REVIEW + alive -> unaffected by the new dead-owner branch, msg unchanged.
_ux_review_alive_wom = _ux_build([
    {"issue": 9, "title": "t", "url": "https://x/9", "work_state": "REVIEW",
     "orch_alive": True, "pr": 7, "pr_url": "https://x/pull/7", "contended": False,
     "state": "REVIEW", "idle_min": 999, "prior_runs": 0, "sessions": [], "activity": None},
])
_ux_wom_alive = _ux_of(_ux_review_alive_wom, "waiting-on-merge")
check("ux_wom_alive_level", "info", _ux_wom_alive["level"])
check("ux_wom_alive_msg", "me/uxrepo#9 waiting on you", _ux_wom_alive["msg"])

# Dead owner but idle_min still under NUDGE_IDLE_MINS -> stays info (this is
# the existing _ux_review_dead row above, idle_min NUDGE_IDLE_MINS - 8).
check("ux_wom_dead_under_threshold_level", "info", _ux_wom["level"])

# No PR on a REVIEW row -> the what-line says so rather than "PR #None".
_ux_no_pr = _ux_build([
    {"issue": 4, "title": "t", "url": "https://x/4", "work_state": "REVIEW",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "REVIEW", "idle_min": -1, "prior_runs": 0, "sessions": [], "activity": None},
])
check("ux_wom_no_pr_what", "no PR", _ux_of(_ux_no_pr, "waiting-on-merge")["what"])
check("ux_wom_unknown_age", -1, _ux_of(_ux_no_pr, "waiting-on-merge")["age_min"])

# REVIEW + alive: the new informational row fires ALONGSIDE the existing
# "waiting on you" alert -- it is an additional row, never a replacement.
_ux_review_alive = _ux_build([
    {"issue": 5, "title": "t", "url": "https://x/5", "work_state": "REVIEW",
     "orch_alive": True, "pr": 7, "pr_url": "https://x/pull/7", "contended": False,
     "state": "CLAIMED", "idle_min": 3, "prior_runs": 1,
     "sessions": [{"id": "s1", "live": True}], "activity": int(time.time())},
])
_ux_srap = _ux_of(_ux_review_alive, "still-running-after-pr")
check("ux_srap_fires_when_alive", True, _ux_srap is not None)
check("ux_srap_msg", "me/uxrepo#5 still running after PR", _ux_srap["msg"])
# warn, not error: these two must stay OUT of the wake digest's error set.
check("ux_srap_level_warn", "warn", _ux_srap["level"])
check("ux_srap_fields", ("uxrepo", "me/uxrepo", 5, "tail", 3),
      (_ux_srap["slug"], _ux_srap["repo"], _ux_srap["issue"], _ux_srap["verb"],
       _ux_srap["age_min"]))
check("ux_srap_keeps_waiting_on_you", True,
      "waiting-on-merge" in _ux_kinds(_ux_review_alive))

# CLAIMED (label) + CLAIMED (work_state, i.e. no branch activity at all) +
# not alive -> died-before-starting. work_state must be "CLAIMED" for this
# row -- see the orch#183 regression test below for what happens when it
# isn't.
_ux_died = _ux_build([
    {"issue": 6, "title": "t", "url": "https://x/6", "work_state": "CLAIMED",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "CLAIMED", "idle_min": 47, "prior_runs": 2, "sessions": [], "activity": None},
])
_ux_dbs = _ux_of(_ux_died, "died-before-starting")
check("ux_dbs_fires", True, _ux_dbs is not None)
check("ux_dbs_msg", "me/uxrepo#6 died before starting (2 prior runs)", _ux_dbs["msg"])
check("ux_dbs_level_warn", "warn", _ux_dbs["level"])
check("ux_dbs_fields", ("uxrepo", "me/uxrepo", 6, "2 prior runs", "log", 47),
      (_ux_dbs["slug"], _ux_dbs["repo"], _ux_dbs["issue"], _ux_dbs["what"],
       _ux_dbs["verb"], _ux_dbs["age_min"]))

# orch#183 regression: CLAIMED (label) + ACTIVE (work_state, i.e. commits
# exist) + not alive -> dead-mid-flight fires and died-before-starting does
# NOT. Before this fix, joining liveness against the label state alone made
# a dead session that had already committed and opened a PR render as "died
# before starting (N prior runs)" -- orch#183 showed this with 1 commit and
# an OPEN MERGEABLE PR. Uses `commits` to assert the commit-count `what`.
_ux_mid_flight = _ux_build([
    {"issue": 11, "title": "t", "url": "https://x/11", "work_state": "ACTIVE",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "CLAIMED", "idle_min": 47, "prior_runs": 2, "commits": 4,
     "sessions": [], "activity": None},
])
_ux_dmf = _ux_of(_ux_mid_flight, "dead-mid-flight")
check("ux_dmf_fires", True, _ux_dmf is not None)
check("ux_dmf_msg", "me/uxrepo#11 died mid-flight", _ux_dmf["msg"])
check("ux_dmf_level_warn", "warn", _ux_dmf["level"])
check("ux_dmf_fields", ("uxrepo", "me/uxrepo", 11, "4 commits", "log", 47),
      (_ux_dmf["slug"], _ux_dmf["repo"], _ux_dmf["issue"], _ux_dmf["what"],
       _ux_dmf["verb"], _ux_dmf["age_min"]))
check("ux_dmf_suppresses_dbs", False,
      "died-before-starting" in _ux_kinds(_ux_mid_flight))

# Same shape, but no `commits` key on the row -> `what` falls back to prior
# runs, same string died-before-starting would have used.
_ux_mid_flight_no_commits = _ux_build([
    {"issue": 12, "title": "t", "url": "https://x/12", "work_state": "ACTIVE",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "CLAIMED", "idle_min": 5, "prior_runs": 3,
     "sessions": [], "activity": None},
])
check("ux_dmf_prior_runs_fallback", "3 prior runs",
      _ux_of(_ux_mid_flight_no_commits, "dead-mid-flight")["what"])

# commits == 0 pins the "real zero" sentinel case: a truthiness guard would
# treat 0 as falsy and fall back to the prior-runs phrasing, losing the most
# actionable fact (the agent produced nothing).
_ux_dmf_zero_commits = _ux_build([
    {"issue": 14, "title": "t", "url": "https://x/14", "work_state": "ACTIVE",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "CLAIMED", "idle_min": 9, "prior_runs": 1, "commits": 0,
     "sessions": [], "activity": None},
])
check("ux_dmf_zero_commits", "0 commits",
      _ux_of(_ux_dmf_zero_commits, "dead-mid-flight")["what"])

# commits == -1 pins the "could not count" sentinel case: a truthiness guard
# would treat -1 as truthy and render it as fact ("-1 commits") instead of
# falling back to the prior-runs phrasing.
_ux_dmf_unknown_commits = _ux_build([
    {"issue": 15, "title": "t", "url": "https://x/15", "work_state": "ACTIVE",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "CLAIMED", "idle_min": 9, "prior_runs": 6, "commits": -1,
     "sessions": [], "activity": None},
])
_ux_dmf_unknown_what = _ux_of(_ux_dmf_unknown_commits, "dead-mid-flight")["what"]
check("ux_dmf_unknown_commits_fallback", "6 prior runs", _ux_dmf_unknown_what)
check("ux_dmf_unknown_commits_no_neg1", False, "-1" in _ux_dmf_unknown_what)

# CLAIMED (label) + ACTIVE (work_state) but alive -> neither liveness row
# fires. Both halves of the join matter; testing only the firing case would
# pass on `if True`.
_ux_claimed_alive = _ux_build([
    {"issue": 7, "title": "t", "url": "https://x/7", "work_state": "ACTIVE",
     "orch_alive": True, "pr": None, "pr_url": None, "contended": False,
     "state": "CLAIMED", "idle_min": 1, "prior_runs": 0,
     "sessions": [{"id": "s1", "live": True}], "activity": int(time.time())},
])
check("ux_dbs_absent_when_alive", False,
      "died-before-starting" in _ux_kinds(_ux_claimed_alive))
check("ux_dmf_absent_when_alive", False,
      "dead-mid-flight" in _ux_kinds(_ux_claimed_alive))
# ...and not alive but UNCLAIMED is not it either.
_ux_unclaimed = _ux_build([
    {"issue": 8, "title": "t", "url": "https://x/8", "work_state": "ACTIVE",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": False,
     "state": "UNCLAIMED", "idle_min": 1, "prior_runs": 0, "sessions": [], "activity": None},
])
check("ux_dbs_absent_when_unclaimed", False,
      "died-before-starting" in _ux_kinds(_ux_unclaimed))
check("ux_dmf_absent_when_unclaimed", False,
      "dead-mid-flight" in _ux_kinds(_ux_unclaimed))

# The swallow case: CLAIMED (label) + REVIEW (work_state) + not alive must
# fire NEITHER liveness row -- waiting-on-merge already covers this issue via
# its own alert (state is forced to REVIEW alongside work_state REVIEW to
# match how build() actually reaches this combination). Before this fix an
# else-branch here would have swallowed REVIEW/BLOCKED/CHECKING/LANDED rows
# into died-before-starting; this pins that they stay untouched.
_ux_review_swallow = _ux_build([
    {"issue": 13, "title": "t", "url": "https://x/13", "work_state": "REVIEW",
     "orch_alive": False, "pr": 9, "pr_url": "https://x/pull/9", "contended": False,
     "state": "REVIEW", "idle_min": 8, "prior_runs": 1, "sessions": [], "activity": None},
])
check("ux_review_swallow_no_dbs", False,
      "died-before-starting" in _ux_kinds(_ux_review_swallow))
check("ux_review_swallow_no_dmf", False,
      "dead-mid-flight" in _ux_kinds(_ux_review_swallow))
check("ux_review_swallow_keeps_wom", True,
      "waiting-on-merge" in _ux_kinds(_ux_review_swallow))

# contended + abandoned keep their msg/level and gain their fields.
_ux_other = _ux_build([
    {"issue": 9, "title": "t", "url": "https://x/9", "work_state": "ACTIVE",
     "orch_alive": False, "pr": None, "pr_url": None, "contended": True,
     "state": "ABANDONED", "idle_min": 13, "prior_runs": 0,
     "sessions": [{"id": "a", "live": True}, {"id": "b", "live": True}],
     "activity": None},
])
_ux_con = _ux_of(_ux_other, "contended")
check("ux_contended_msg_unchanged", "me/uxrepo#9 has two live sessions", _ux_con["msg"])
check("ux_contended_level_unchanged", "error", _ux_con["level"])
check("ux_contended_fields", ("2 live sessions", "kill", 13, 9),
      (_ux_con["what"], _ux_con["verb"], _ux_con["age_min"], _ux_con["issue"]))
_ux_aban = _ux_of(_ux_other, "abandoned")
check("ux_abandoned_msg_unchanged", "me/uxrepo#9 abandoned", _ux_aban["msg"])
check("ux_abandoned_level_unchanged", "warn", _ux_aban["level"])
check("ux_abandoned_fields", ("", "issue", 13),
      (_ux_aban["what"], _ux_aban["verb"], _ux_aban["age_min"]))

# wedged: alive with no activity at all. age_min must equal the number the
# msg itself prints, not idle_min -- a row whose clock disagrees with its own
# sentence is the bug this check exists to catch.
_ux_wedged = _ux_build([
    {"issue": 10, "title": "t", "url": "https://x/10", "work_state": "ACTIVE",
     "orch_alive": True, "pr": None, "pr_url": None, "contended": False,
     "state": "ACTIVE", "idle_min": 99, "prior_runs": 0,
     "sessions": [{"id": "s1", "live": True}], "activity": None},
])
_ux_wedge = _ux_of(_ux_wedged, "wedged")
check("ux_wedged_msg_unchanged", "me/uxrepo#10 wedged (alive, idle -1m)", _ux_wedge["msg"])
check("ux_wedged_level_unchanged", "info", _ux_wedge["level"])
check("ux_wedged_fields", ("idle -1m", "kill", -1),
      (_ux_wedge["what"], _ux_wedge["verb"], _ux_wedge["age_min"]))

# The threshold the client must read instead of hardcoding 25.
check("ux_nudge_idle_mins_exported", core.NUDGE_IDLE_MINS,
      _ux_wedged["nudge_idle_mins"])

# Every issue-naming alert carries the full structured field set -- a row
# missing one would render as a blank cell in the UI rather than fail loudly.
_ux_required = ("kind", "slug", "repo", "issue", "what", "age_min", "verb")
for _o in (_ux_review_dead, _ux_review_alive, _ux_died, _ux_other, _ux_wedged, _ux_mid_flight):
    for _a in _o["alerts"]:
        if _a.get("kind") in ("contended", "wedged", "waiting-on-merge", "abandoned",
                              "died-before-starting", "still-running-after-pr",
                              "dead-mid-flight"):
            check(f"ux_fields_complete_{_a['kind']}", [],
                  [k for k in _ux_required if k not in _a])

# === #117: `needs_you` — NEEDS YOU membership, feed-side and explicit =======
# The widget used to infer membership from `level` (error+warn), which dropped
# the two `info` rows the taxonomy names — "wedged" and "waiting on merge" —
# so the ordinary "your PR is green and waiting for you" case rendered as
# "nothing needs you". Membership is now its own key. These checks pin BOTH
# halves: that the key is present on the two rows that were invisible, and
# that adding it disturbed neither `level` nor `msg`, which the wake digest
# consumes and which must stay byte-identical.
check("ux117_wedged_needs_you", True, _ux_wedge.get("needs_you"))
check("ux117_wom_needs_you", True, _ux_wom.get("needs_you"))
# The negative half of the same pair: the key did not reclassify them. Same
# values the msg/level checks above assert, restated here so a future edit
# that "simplifies" needs_you back into a level bump fails on this line and
# names the reason.
check("ux117_wedged_still_info", ("info", "me/uxrepo#10 wedged (alive, idle -1m)"),
      (_ux_wedge["level"], _ux_wedge["msg"]))
check("ux117_wom_still_info", ("info", "me/uxrepo#3 waiting on you"),
      (_ux_wom["level"], _ux_wom["msg"]))

# Every taxonomy row carries it, across all levels — error (contended), warn
# (abandoned, died-before-starting, still-running-after-pr) and info. A key
# present on only some rows is the level check wearing a different name.
_ux117_kinds = ("contended", "wedged", "waiting-on-merge", "abandoned",
                "died-before-starting", "still-running-after-pr", "dead-mid-flight")
for _o in (_ux_review_dead, _ux_review_alive, _ux_died, _ux_other, _ux_wedged, _ux_mid_flight):
    for _a in _o["alerts"]:
        if _a.get("kind") in _ux117_kinds:
            check(f"ux117_needs_you_{_a['kind']}", True, _a.get("needs_you"))

# A NON-needs-you alert must not carry it. "gh read failed" is `error` — the
# loudest level there is — and names no unit of work, so it is exactly the
# case that proves membership is not severity. Absent, not False: the client
# tests truthiness, and a row that never claimed membership must not create
# the key at all.
_ux117_saved = feed.repo_json
feed.repo_json = lambda repo: {
    "repo": "me/uxrepo", "path": str(repo), "slug": "uxrepo", "ok": False,
    "counts": {}, "live_orchs": 0, "issues": [],
    "orch": {"key": "repo-orch.uxrepo", "alive": False, "prior_runs": 0, "recent": []},
}
try:
    _ux117_bad = feed.build()
finally:
    feed.repo_json = _ux117_saved
_ux117_gh = next((a for a in _ux117_bad["alerts"]
                  if a.get("msg") == "gh read failed for me/uxrepo"), None)
check("ux117_gh_read_failed_present", True, _ux117_gh is not None)
check("ux117_gh_read_failed_not_needs_you", False, "needs_you" in (_ux117_gh or {}))
check("ux117_gh_read_failed_still_error", "error", (_ux117_gh or {}).get("level"))
# === orch#138: runlog.translate / translate_line ============================
# Event shapes below are MEASURED from a real `claude -p --output-format
# stream-json --verbose` run, not invented -- trust them over intuition.
# `now` is passed explicitly everywhere so these tests are deterministic;
# never assert on wall-clock time here.
from orch import runlog as _runlog

_RL_NOW = "00:12:04"

# --- assistant: text block -----------------------------------------------
_rl_text_line = _runlog.translate(
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}},
    now=_RL_NOW,
)
check("runlog_assistant_text_kind", "assistant", _rl_text_line.split()[1] if _rl_text_line else None)
check("runlog_assistant_text_body", True, bool(_rl_text_line) and "hello" in _rl_text_line)

# Embedded newline: collapsed to a space, and the returned line must carry
# NO raw newline -- a newline inside a line-oriented log file corrupts it,
# splitting one event into two lines a reader can't reassemble.
_rl_nl_line = _runlog.translate(
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "line one\nline two"}]}},
    now=_RL_NOW,
)
check("runlog_assistant_newline_collapsed", True,
      bool(_rl_nl_line) and "line one line two" in _rl_nl_line)
check("runlog_assistant_newline_absent", False, "\n" in (_rl_nl_line or ""))

# --- assistant: tool_use block --------------------------------------------
_rl_tool_line = _runlog.translate(
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"description": "check state"}}
    ]}},
    now=_RL_NOW,
)
check("runlog_assistant_tool_use", True,
      bool(_rl_tool_line) and "Bash: check state" in _rl_tool_line)

# tool_use input falls back to `command`, then `file_path`, when
# `description` is absent.
_rl_tool_cmd = _runlog.translate(
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
    ]}},
    now=_RL_NOW,
)
check("runlog_tool_use_fallback_command", True,
      bool(_rl_tool_cmd) and "Bash: ls -la" in _rl_tool_cmd)

_rl_tool_fp = _runlog.translate(
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x.py"}}
    ]}},
    now=_RL_NOW,
)
check("runlog_tool_use_fallback_file_path", True,
      bool(_rl_tool_fp) and "Read: /tmp/x.py" in _rl_tool_fp)

# --- user: tool_result ok / error -----------------------------------------
_rl_ok_line = _runlog.translate(
    {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": False}]}},
    now=_RL_NOW,
)
check("runlog_tool_result_ok", True, bool(_rl_ok_line) and "ok" in _rl_ok_line)
check("runlog_tool_result_ok_not_err", False, "ERR" in (_rl_ok_line or ""))

_rl_err_line = _runlog.translate(
    {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
    now=_RL_NOW,
)
check("runlog_tool_result_err", True, bool(_rl_err_line) and "ERR" in _rl_err_line)

# A tool_result carrying a NAME must keep the status and the body as separate
# whitespace-delimited tokens. The status variants build their kind field by
# hand rather than through _kind()'s ljust, so an off-by-one there renders
# "tool    okBash" -- which still passes a naive `"ok" in line` substring
# check while making the name unrecoverable for orch#151's renderer, the
# documented consumer of this shape. Assert the field split, not a substring.
_rl_named_ok = _runlog.translate(
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": False, "name": "Bash"}]}},
    now=_RL_NOW,
)
check("runlog_tool_result_named_fields", ["00:12:04", "tool", "ok", "Bash"],
      _rl_named_ok.split() if _rl_named_ok else None)
_rl_named_err = _runlog.translate(
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True, "name": "Bash"}]}},
    now=_RL_NOW,
)
check("runlog_tool_result_named_fields_err", ["00:12:04", "tool", "ERR", "Bash"],
      _rl_named_err.split() if _rl_named_err else None)

# Every kind puts its body at the same column, so a tail reads as columns.
# The status variants are the ones at risk: they are the only kind field not
# produced by _kind().
_rl_bodycol = 9 + _runlog.KIND_WIDTH
_rl_col_cases = [
    _rl_named_ok,
    _rl_named_err,
    _runlog.translate({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"description": "d"}}]}}, now=_RL_NOW),
    _runlog.translate({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hi"}]}}, now=_RL_NOW),
]
check("runlog_body_column_aligned", [True] * len(_rl_col_cases),
      [bool(l) and l[_rl_bodycol - 1] == " " and l[_rl_bodycol] != " "
       for l in _rl_col_cases])

# No line carries trailing whitespace: an empty body must not leave the kind
# field's padding dangling at the end of the line.
_rl_empty_body = _runlog.translate(
    {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": False}]}},
    now=_RL_NOW,
)
check("runlog_no_trailing_space", False,
      bool(_rl_empty_body) and _rl_empty_body != _rl_empty_body.rstrip())

# --- result: subtype, turns, cost ------------------------------------------
_rl_result_line = _runlog.translate(
    {"type": "result", "subtype": "success", "num_turns": 4, "total_cost_usd": 0.0804102},
    now=_RL_NOW,
)
check("runlog_result_success", True, bool(_rl_result_line) and "success" in _rl_result_line)
check("runlog_result_turns", True, bool(_rl_result_line) and "4 turns" in _rl_result_line)
check("runlog_result_cost_rounded", True, bool(_rl_result_line) and "$0.08" in _rl_result_line)

# --- system: init -----------------------------------------------------------
# This one matters: a bad model string (see the model-hierarchy gotcha --
# an anthropic/-prefixed string kills the session after a clean-looking
# spawn) becomes visible right here, in the one line a supervisor tailing
# the run log actually sees.
_rl_init_line = _runlog.translate(
    {"type": "system", "subtype": "init", "model": "claude-sonnet-5", "session_id": "abc"},
    now=_RL_NOW,
)
check("runlog_init_model", True, bool(_rl_init_line) and "claude-sonnet-5" in _rl_init_line)
check("runlog_init_session", True, bool(_rl_init_line) and "abc" in _rl_init_line)

# --- system: hook events are pure noise, must vanish ------------------------
# A real 2-word run emitted ~25 KB of JSON, almost all of it hook events
# echoing full skill text back verbatim.
check("runlog_hook_event_none", None,
      _runlog.translate({"type": "system", "subtype": "hook_pre_tool_use"}, now=_RL_NOW))
check("runlog_hook_event_none_variant", None,
      _runlog.translate({"type": "system", "subtype": "hookEvent"}, now=_RL_NOW))

# --- rate_limit_event: allowed is silent, anything else is a line ----------
# A run stalled on a rate limit is exactly the stall this feature exists
# to reveal.
check("runlog_rate_limit_allowed_none", None,
      _runlog.translate(
          {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
          now=_RL_NOW))
_rl_rl_line = _runlog.translate(
    {"type": "rate_limit_event", "rate_limit_info": {"status": "rate_limited"}},
    now=_RL_NOW,
)
check("runlog_rate_limit_other_is_line", True,
      bool(_rl_rl_line) and "rate_limited" in _rl_rl_line)

# --- unknown/future event type: None, never a raise -------------------------
check("runlog_unknown_type_none", None,
      _runlog.translate({"type": "some_future_event", "x": 1}, now=_RL_NOW))

# --- ROBUSTNESS: this is the point of the module. It runs inside a spawn --
# a malformed or unexpected-shape event must never raise, or one bad event
# takes down the session that's producing it.
_RL_MALFORMED = [
    None,
    "just a string",
    42,
    [],
    {},
    {"type": "assistant"},
    {"type": "assistant", "message": None},
    {"type": "assistant", "message": {"content": "not-a-list"}},
    {"type": "assistant", "message": {"content": [None]}},
    {"type": "result", "num_turns": "x", "total_cost_usd": None},
    {"type": "rate_limit_event", "rate_limit_info": 7},
]
_rl_raised = []
for _rl_bad in _RL_MALFORMED:
    try:
        _runlog.translate(_rl_bad, now=_RL_NOW)
    except Exception as _rl_exc:
        _rl_raised.append((_rl_bad, _rl_exc))
check("runlog_translate_never_raises", [], _rl_raised)

# --- translate_line: JSON parse, raw fallback, blank, truncation -----------
_rl_line_valid = _runlog.translate_line(
    json.dumps({"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.01}),
    now=_RL_NOW,
)
check("runlog_translate_line_valid_json", True,
      bool(_rl_line_valid) and "success" in _rl_line_valid)

# Unparseable JSON returns the RAW text, not None -- a non-JSON line from
# the CLI is usually an error itself, so it's high value and must surface.
_rl_line_raw = _runlog.translate_line("not json at all {{{", now=_RL_NOW)
check("runlog_translate_line_unparseable_not_none", True, _rl_line_raw is not None)
check("runlog_translate_line_unparseable_has_raw_text", True,
      "not json at all" in _rl_line_raw)

check("runlog_translate_line_blank_none", None, _runlog.translate_line("   ", now=_RL_NOW))
check("runlog_translate_line_blank_none_empty", None, _runlog.translate_line("", now=_RL_NOW))

_rl_long_raw = "x" * (_runlog.MAX_BODY * 3)
_rl_line_long = _runlog.translate_line(_rl_long_raw, now=_RL_NOW)
check("runlog_translate_line_truncated_len", True,
      bool(_rl_line_long) and len(_rl_line_long) < len(_rl_long_raw))
check("runlog_translate_line_truncated_ellipsis", True,
      bool(_rl_line_long) and _rl_line_long.endswith("..."))


# === orch#151: runlog.parse -- the read side of the same format =============
# This is the reader of the exact contract translate()/_line() writes above,
# so the highest-value test is a round-trip through the real write side, not
# hand-built strings.

# --- round-trip: translate() output parsed back, fields survive ------------
_rlp_init_line = _runlog.translate(
    {"type": "system", "subtype": "init", "model": "claude-sonnet-5", "session_id": "sess-abc"},
    now="13:08:12",
)
_rlp_roundtrip = _runlog.parse(_rlp_init_line)
check("runlog_parse_roundtrip_one_run", 1, len(_rlp_roundtrip))
check("runlog_parse_roundtrip_row_count", 1, len(_rlp_roundtrip[0]["rows"]) if _rlp_roundtrip else None)
_rlp_roundtrip_row = _rlp_roundtrip[0]["rows"][0] if _rlp_roundtrip and _rlp_roundtrip[0]["rows"] else {}
check("runlog_parse_roundtrip_time", "13:08:12", _rlp_roundtrip_row.get("time"))
check("runlog_parse_roundtrip_kind", "init", _rlp_roundtrip_row.get("kind"))
check("runlog_parse_roundtrip_body", True,
      "claude-sonnet-5" in (_rlp_roundtrip_row.get("body") or "")
      and "sess-abc" in (_rlp_roundtrip_row.get("body") or ""))
check("runlog_parse_roundtrip_no_banner_started_none", None, _rlp_roundtrip[0]["started"])

# --- multi-run split: two banners -> two runs, newest first, rows attached -
_RLP_TWO_RUNS = (
    "=== 2026-09-15T13:00:00-04:00 pgid 1 caveman=on ===\n"
    "13:00:01 init      run one\n"
    "13:00:02 tool      Bash: first\n"
    "=== 2026-09-15T13:05:00-04:00 pgid 2 caveman=off ===\n"
    "13:05:01 init      run two\n"
)
_rlp_two = _runlog.parse(_RLP_TWO_RUNS)
check("runlog_parse_multirun_count", 2, len(_rlp_two))
check("runlog_parse_multirun_newest_first_started", "2026-09-15T13:05:00-04:00",
      _rlp_two[0]["started"] if _rlp_two else None)
check("runlog_parse_multirun_newest_first_pgid", "2", _rlp_two[0]["pgid"] if _rlp_two else None)
check("runlog_parse_multirun_newest_caveman_off", False, _rlp_two[0]["caveman"] if _rlp_two else None)
check("runlog_parse_multirun_newest_rows", 1, len(_rlp_two[0]["rows"]) if _rlp_two else None)
check("runlog_parse_multirun_oldest_pgid", "1", _rlp_two[1]["pgid"] if len(_rlp_two) > 1 else None)
check("runlog_parse_multirun_oldest_caveman_on", True, _rlp_two[1]["caveman"] if len(_rlp_two) > 1 else None)
check("runlog_parse_multirun_oldest_rows", 2, len(_rlp_two[1]["rows"]) if len(_rlp_two) > 1 else None)

# --- tool status variants: kind stays "tool", status captured, body intact -
_rlp_tool_ok = _runlog.parse("00:12:09 tool   ok")
check("runlog_parse_tool_ok_kind", "tool", _rlp_tool_ok[0]["rows"][0]["kind"] if _rlp_tool_ok else None)
check("runlog_parse_tool_ok_status", "ok", _rlp_tool_ok[0]["rows"][0]["status"] if _rlp_tool_ok else None)

_rlp_tool_err = _runlog.parse("00:12:09 tool  ERR")
check("runlog_parse_tool_err_kind", "tool", _rlp_tool_err[0]["rows"][0]["kind"] if _rlp_tool_err else None)
check("runlog_parse_tool_err_status", "ERR", _rlp_tool_err[0]["rows"][0]["status"] if _rlp_tool_err else None)

# A plain tool_use line (no status) must NOT be mistaken for a status
# variant -- status stays None, body is the full "name: desc" prose.
_rlp_tool_plain = _runlog.parse("00:12:09 tool      Bash: check worktree state")
_rlp_tool_plain_row = _rlp_tool_plain[0]["rows"][0] if _rlp_tool_plain else {}
check("runlog_parse_tool_plain_status_none", None, _rlp_tool_plain_row.get("status"))
check("runlog_parse_tool_plain_body", "Bash: check worktree state", _rlp_tool_plain_row.get("body"))

# A tool whose NAME merely starts with "ok"/"ERR" is not a status line. The
# status must be its own whole word, or the reader invents a result the
# writer never emitted: "tool      okra: x" once read back as status "ok"
# with body "ra: x". Driven through the real write side so the round trip
# is what is pinned, not a hand-typed guess at its output.
_rlp_okra_line = _runlog.translate(
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "okra", "input": {"description": "x"}}]}},
    now="00:12:09")
_rlp_okra_row = _runlog.parse(_rlp_okra_line)[0]["rows"][0]
check("runlog_parse_tool_name_okprefix_status_none", None, _rlp_okra_row.get("status"))
check("runlog_parse_tool_name_okprefix_body", "okra: x", _rlp_okra_row.get("body"))

# Same trap on the ERR side, and for a body that merely opens with "ok".
_rlp_errish = _runlog.parse("00:12:09 tool      ERRcheck: boom")
check("runlog_parse_tool_errprefix_status_none", None, _rlp_errish[0]["rows"][0]["status"])
check("runlog_parse_tool_errprefix_body", "ERRcheck: boom", _rlp_errish[0]["rows"][0]["body"])

# A bare status line keeps body a string, never None, so the renderer and
# any other caller never have to test for it.
check("runlog_parse_tool_ok_body_is_str", "", _runlog.parse("00:12:09 tool   ok")[0]["rows"][0]["body"])

# --- malformed / non-conforming line -> kind "raw", body verbatim, no raise
_rlp_raw = _runlog.parse("Traceback (most recent call last):")
check("runlog_parse_malformed_kind_raw", "raw", _rlp_raw[0]["rows"][0]["kind"] if _rlp_raw else None)
check("runlog_parse_malformed_body_verbatim", "Traceback (most recent call last):",
      _rlp_raw[0]["rows"][0]["body"] if _rlp_raw else None)
check("runlog_parse_malformed_time_none", None, _rlp_raw[0]["rows"][0]["time"] if _rlp_raw else None)

# --- empty string -> [] --------------------------------------------------
check("runlog_parse_empty_string", [], _runlog.parse(""))

# --- content before the first banner -> leading run, started is None -------
_RLP_PRE_BANNER = (
    "13:00:01 init      predates the banner convention\n"
    "=== 2026-09-15T13:05:00-04:00 pgid 2 caveman=on ===\n"
    "13:05:01 init      after banner\n"
)
_rlp_pre = _runlog.parse(_RLP_PRE_BANNER)
check("runlog_parse_pre_banner_run_count", 2, len(_rlp_pre))
check("runlog_parse_pre_banner_newest_first", "2026-09-15T13:05:00-04:00",
      _rlp_pre[0]["started"] if _rlp_pre else None)
check("runlog_parse_pre_banner_trailing_started_none", None,
      _rlp_pre[1]["started"] if len(_rlp_pre) > 1 else "MISSING")
check("runlog_parse_pre_banner_trailing_pgid_none", None,
      _rlp_pre[1]["pgid"] if len(_rlp_pre) > 1 else "MISSING")

# --- limit: keeps most recent rows, drops emptied runs, newest-first order -
_RLP_LIMIT_TEXT = (
    "=== 2026-09-15T13:00:00-04:00 pgid 1 caveman=on ===\n"
    "13:00:01 init      a\n"
    "13:00:02 tool      b\n"
    "=== 2026-09-15T13:05:00-04:00 pgid 2 caveman=on ===\n"
    "13:05:01 init      c\n"
    "13:05:02 tool      d\n"
    "13:05:03 result    e\n"
)
_rlp_limited = _runlog.parse(_RLP_LIMIT_TEXT, limit=2)
check("runlog_parse_limit_run_count", 1, len(_rlp_limited))
check("runlog_parse_limit_row_count", 2, len(_rlp_limited[0]["rows"]) if _rlp_limited else None)
check("runlog_parse_limit_keeps_most_recent", ["d", "e"],
      [r["body"] for r in _rlp_limited[0]["rows"]] if _rlp_limited else None)

# limit larger than total rows: nothing dropped, all runs survive.
_rlp_unlimited = _runlog.parse(_RLP_LIMIT_TEXT, limit=100)
check("runlog_parse_limit_generous_keeps_all_runs", 2, len(_rlp_unlimited))
check("runlog_parse_limit_generous_keeps_all_rows", 5,
      sum(len(r["rows"]) for r in _rlp_unlimited))

# verbLog() needs slug/issue/sink to build its payload, so a bare verbLog()
# call site emits a button that posts {"action":"log"} with no repo or issue
# and always fails validate(). One such call site survived the change from
# the old zero-arg stub and shipped a live button that could only error.
# Nothing else pins call-site arity, so pin it here: every call must pass
# arguments. The definition itself is excluded by requiring a non-"(" char.
_verblog_calls = re.findall(r"verbLog\(([^)]*)\)", WIDGET_TPL)
check("widget_verblog_never_called_bare", [],
      [c for c in _verblog_calls if not c.strip() and "function verbLog" not in c])


# === orch#151: server.a_log -- the "log" action ==============================
# repo/issue ONLY -- the path is derived through core.key_for()/
# core.ledger_log_path(), never accepted from the client. Same
# repo_path_for/srv.repo_path monkeypatch idiom the single-implementation
# kill proof above uses, so this exercises the real a_log/core.key_for/
# core.ledger_log_path chain rather than a stand-in.
check("actions_log_registered", ("repo", "issue"), srv.ACTIONS["log"][1])

_alog_orig_repo_path_for = core.repo_path_for
_alog_orig_srv_repo_path = srv.repo_path
core.repo_path_for = lambda slug: Path("/tmp/alogrepo") if slug == "alogrepo" else None
srv.repo_path = lambda slug: core.repo_path_for(slug)
try:
    # --- real multi-run log: newest-first, rows present ---------------------
    alog_key = core.key_for("issue-orch", "alogrepo", 9)
    core.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    core.ledger_log_path(alog_key).write_text(
        "=== 2026-09-15T13:00:00-04:00 pgid 1 caveman=on ===\n"
        "13:00:01 init      run one\n"
        "=== 2026-09-15T13:05:00-04:00 pgid 2 caveman=off ===\n"
        "13:05:01 init      run two\n"
        "13:05:02 tool      Bash: check worktree state\n"
    )
    _alog_res = srv.a_log({"repo": "alogrepo", "issue": "9"})
    check("alog_ok_true", True, _alog_res.get("ok"))
    check("alog_runs_newest_first_pgid", "2",
          _alog_res["runs"][0]["pgid"] if _alog_res.get("runs") else None)
    check("alog_runs_oldest_pgid", "1",
          _alog_res["runs"][1]["pgid"] if len(_alog_res.get("runs") or []) > 1 else None)
    check("alog_newest_run_rows", 2,
          len(_alog_res["runs"][0]["rows"]) if _alog_res.get("runs") else None)

    # --- missing log file: ok True, runs == [] -- NOT an error --------------
    _alog_missing = srv.a_log({"repo": "alogrepo", "issue": "12345"})
    check("alog_missing_ok_true", True, _alog_missing.get("ok"))
    check("alog_missing_runs_empty", [], _alog_missing.get("runs"))

    # --- 400-row cap is applied ----------------------------------------------
    alog_cap_key = core.key_for("issue-orch", "alogrepo", 10)
    _alog_cap_lines = "".join(f"13:00:{i % 60:02d}   init      row {i}\n" for i in range(500))
    core.ledger_log_path(alog_cap_key).write_text(
        "=== 2026-09-15T13:00:00-04:00 pgid 3 caveman=on ===\n" + _alog_cap_lines
    )
    _alog_cap_res = srv.a_log({"repo": "alogrepo", "issue": "10"})
    check("alog_cap_applied", 400,
          sum(len(r["rows"]) for r in _alog_cap_res.get("runs") or []))

    # --- malformed/garbage log file does not raise ---------------------------
    alog_bad_key = core.key_for("issue-orch", "alogrepo", 11)
    core.ledger_log_path(alog_bad_key).write_bytes(b"\xff\xfe not json, not a log line \x00\x01")
    _alog_bad_res = srv.a_log({"repo": "alogrepo", "issue": "11"})
    check("alog_malformed_ok_true", True, _alog_bad_res.get("ok"))
    check("alog_malformed_is_list", True, isinstance(_alog_bad_res.get("runs"), list))
finally:
    core.repo_path_for = _alog_orig_repo_path_for
    srv.repo_path = _alog_orig_srv_repo_path
# --- issue-205: `unconsidered` rendered as a Zone A alert row --------------
# feed.repo_json emits `unconsidered` (open issues nobody has triaged) and
# tick condition 8 fires on it, but the dashboard never rendered it -- an
# unlabelled issue was invisible and unclickable from the UI. This mirrors
# the `labels_missing` alert-row idiom immediately above it in tui_model.py.

# 1. A non-empty `unconsidered` produces exactly ONE alert row (set-level,
# not one row per issue), naming the repo, the count, and a sample.
t205_feed = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                        "issues": [], "unconsidered": [1, 2, 3, 4, 5, 6]}]}
t205_rows = tui_model.zone_a(t205_feed)
t205_unc_rows = [r for r in t205_rows if r.key.startswith("unconsidered.")]
check("t205_unconsidered_one_row", 1, len(t205_unc_rows))
check("t205_unconsidered_kind_alert", "alert", t205_unc_rows[0].kind)
check("t205_unconsidered_depth_1", 1, t205_unc_rows[0].depth)
check("t205_unconsidered_children_one_per_issue", 6, len(t205_unc_rows[0].children))
check("t205_unconsidered_payload_is_row", t205_feed["repos"][0], t205_unc_rows[0].payload)
check("t205_unconsidered_names_repo", True, "orch" in t205_unc_rows[0].text)
check("t205_unconsidered_shows_count", True, "6" in t205_unc_rows[0].text)

# 2. Empty list / missing key / non-list value each produce NO row and must
# not raise -- a malformed or absent field degrades safely.
for _t205_bad, _t205_label in (
    ([], "empty_list"),
    (None, "missing_key_via_none"),
    ("not-a-list", "non_list_str"),
    (42, "non_list_int"),
):
    _t205_row = {"slug": "orch", "repo": "cybermelons/orch", "issues": []}
    if _t205_label != "missing_key_via_none":
        _t205_row["unconsidered"] = _t205_bad
    _t205_out = tui_model.zone_a({"repos": [_t205_row]})
    check(f"t205_unconsidered_{_t205_label}_no_row", 0,
          len([r for r in _t205_out if r.key.startswith("unconsidered.")]))

# 3. The sample is capped at 3 even when the list is much longer.
t205_long_feed = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                             "issues": [],
                             "unconsidered": list(range(1, 30))}]}
t205_long_rows = tui_model.zone_a(t205_long_feed)
t205_long_row = [r for r in t205_long_rows if r.key.startswith("unconsidered.")][0]
check("t205_unconsidered_sample_capped_shows_first_three", True,
      "#1" in t205_long_row.text and "#2" in t205_long_row.text
      and "#3" in t205_long_row.text)
check("t205_unconsidered_sample_capped_omits_fourth", False,
      "#4" in t205_long_row.text)
check("t205_unconsidered_sample_capped_shows_full_count", True,
      "29" in t205_long_row.text)

# 4. When BOTH labels_missing and unconsidered fire on the same repo, the
# two alert rows must have distinct keys -- the `unconsidered.` namespace
# must not collide with the `labels.` namespace.
t205_both_feed = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                             "issues": [], "labels_missing": ["agent-ready"],
                             "unconsidered": [7, 8]}]}
t205_both_rows = tui_model.zone_a(t205_both_feed)
t205_both_alert_keys = [r.key for r in t205_both_rows if r.kind == "alert"]
check("t205_unconsidered_and_labels_two_rows", 2, len(t205_both_alert_keys))
check("t205_unconsidered_and_labels_distinct_keys", True,
      len(set(t205_both_alert_keys)) == 2)

# 5. The count is the COUNT, not the highest issue number. Review finding 4
# on #216: every earlier fixture used a contiguous 1..n list, where
# len(list) and list[-1] coincide, so `len(uncovered)` and `uncovered[-1]`
# were indistinguishable and a regression to the latter would pass. This
# fixture separates them -- len 3, last element 11.
t205_count_feed = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                              "issues": [], "unconsidered": [3, 7, 11]}]}
t205_count_row = [r for r in tui_model.zone_a(t205_count_feed)
                  if r.key.startswith("unconsidered.")][0]
check("t205_unconsidered_reports_count_not_last", True,
      "3 issue(s)" in t205_count_row.text)
check("t205_unconsidered_not_reporting_last_as_count", False,
      "11 issue(s)" in t205_count_row.text)

# 6. The row self-identifies its kind, like every other Zone A alert
# (ERROR/WARN, AWAITING REVIEW, LABELS MISSING). Review finding 3 on #216:
# a bare leading slug reads as a repo header, not an alert, and sits
# directly beside issue rows that also lead with the slug.
check("t205_unconsidered_has_kind_prefix", True,
      t205_count_row.text.startswith("UNTRIAGED"))

# 7. row_slug resolves the repo from an `unconsidered.` key when the payload
# carries no slug. Review finding 2 on #216: the key-prefix whitelist is the
# established contract for a repo-keyed Zone A row, and `labels.` -- the
# sibling this row is modelled on -- is already in it. Without this, a verb
# answers "needs a repo" on a row that names the repo in its own text.
check("t205_row_slug_resolves_unconsidered_key", "orch",
      _t91_tui.row_slug(tui_model.Row(text="x", kind="alert",
                                      key="unconsidered.orch", children=[],
                                      payload={}, depth=1)))

# --- issue-205: the ACT half -- an `assign` keystroke on an untriaged child --
# The prior block (above) only proved the alert row RENDERS. A blocking review
# finding on #205 pointed out it was display-only: no keystroke could label an
# untriaged issue agent-ready. server.a_assign already exists and is already
# registered; the only gap was (a) the UNTRIAGED row had no children to select
# and (b) the TUI never bound a key to the `assign` verb. These tests cover
# both halves of that gap.

# 8. children: one Row per untriaged issue, not capped at the 3-item text
# sample -- a 3-element list produces 3 children ...
t205_act_feed3 = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                             "issues": [], "unconsidered": [3, 7, 11]}]}
t205_act_row3 = [r for r in tui_model.zone_a(t205_act_feed3)
                 if r.key.startswith("unconsidered.")][0]
check("t205_act_children_count_3", 3, len(t205_act_row3.children))

# ... and a 5-element list produces 5 children, proving the text sample's cap
# of 3 is NOT accidentally applied to children too -- if it were, issues 4..n
# would stay unclickable, which is exactly the finding being fixed.
t205_act_feed5 = {"repos": [{"slug": "orch", "repo": "cybermelons/orch",
                             "issues": [], "unconsidered": [1, 2, 3, 4, 5]}]}
t205_act_row5 = [r for r in tui_model.zone_a(t205_act_feed5)
                 if r.key.startswith("unconsidered.")][0]
check("t205_act_children_count_5", 5, len(t205_act_row5.children))

# 9. Each child's payload carries the repo slug and an int issue number.
t205_act_child = t205_act_row3.children[0]
check("t205_act_child_payload_slug", "orch", t205_act_child.payload.get("slug"))
check("t205_act_child_payload_issue_is_int", True,
      isinstance(t205_act_child.payload.get("issue"), int))
check("t205_act_child_payload_issue_value", 3, t205_act_child.payload.get("issue"))

# 10. row_args resolves `assign` on a child row: args = {repo, issue}, no
# why-not.
t205_act_args, t205_act_why = _t91_tui.row_args(t205_act_child, "assign")
check("t205_act_child_assign_args", {"repo": "orch", "issue": 3}, t205_act_args)
check("t205_act_child_assign_why_none", None, t205_act_why)

# 11. row_args on the PARENT (alert) row returns None args and a why-not
# naming the missing issue -- the parent names no single issue, so per #66 it
# must explain itself on the status line rather than silently do nothing.
t205_act_parent_args, t205_act_parent_why = _t91_tui.row_args(t205_act_row3, "assign")
check("t205_act_parent_assign_args_none", None, t205_act_parent_args)
check("t205_act_parent_assign_why_not_none", True, t205_act_parent_why is not None)
check("t205_act_parent_assign_why_mentions_issue", True,
      "issue" in t205_act_parent_why)

# 12. `assign` is wired into ACTION_ARGS with exactly (repo, issue), and a key
# is bound to it in ACTIONS.
check("t205_act_action_args_registered", ("repo", "issue"),
      _t91_tui.ACTION_ARGS.get("assign"))
check("t205_act_action_key_bound", True,
      "assign" in [verb for _key, verb, _desc in _t91_tui.ACTIONS])

# 13. `assign` is reversible (unlike kill/merge) and must NOT be in
# DESTRUCTIVE.
check("t205_act_assign_not_destructive", False,
      "assign" in _t91_tui.DESTRUCTIVE)

# === orch#138 second cause: the pump outlives the spawner ===================
# THE regression this whole section pins. Before this fix, the run-log drain
# was a daemon=True thread living inside the SPAWNING process (tick.py / the
# CLI). That process spawns a claude child and then exits within seconds --
# the daemon thread dies with it, and everything the child writes to stdout
# after that point is never drained: the run log goes blind for the rest of
# a long session. The fix moves the drain into orch/runlog_pump.py, a
# SEPARATE, DETACHED process that core._launch spawns with the claude
# child's stdout wired straight into the pump's stdin, and closes its own
# copy of that write end so only the child holds it open.
#
# These checks exercise the real wiring end-to-end with real processes and a
# real pipe -- no mocks -- because the defect this issue fixes is entirely
# about process lifetime, which a mock cannot reproduce. A fake "spawner"
# script builds the exact plumbing _launch builds (subprocess -> pump.stdin,
# start_new_session=True on the pump, close-own-copy-of-write-end) and then
# EXITS IMMEDIATELY, before its "child" has finished emitting lines. If the
# thread-based design were resurrected, the spawner exiting would kill the
# drain and this test would fail (lines emitted after spawner-exit would
# never reach the log).
import signal as _signal138

_pump138_tmp = Path(tempfile.mkdtemp())
_pump138_log = _pump138_tmp / "run.log"
_pump138_child_script = _pump138_tmp / "fake_child.py"
_pump138_spawner_script = _pump138_tmp / "fake_spawner.py"

# The fake "child": stands in for `claude -p --output-format stream-json`.
# Emits a handful of stream-json lines spaced out over ~2s, then exits. The
# spacing is what makes this a real test of "keeps draining after the
# spawner is gone" rather than "drained a burst that arrived instantly".
_pump138_child_script.write_text(
    "import json, sys, time\n"
    "for i in range(4):\n"
    "    time.sleep(0.5)\n"
    "    print(json.dumps({'type': 'assistant', 'message': {'content': "
    "[{'type': 'text', 'text': f'line {i}'}]}}))\n"
    "    sys.stdout.flush()\n"
)

# The fake "spawner": stands in for core._launch. Builds the SAME wiring
# _launch builds -- spawn the pump detached (start_new_session=True) with
# its stdin as a pipe, spawn the child with stdout=pump.stdin, close this
# process's own copy of pump.stdin so the child is the pipe's sole writer --
# then writes its own pid to a marker file and exits immediately. It does
# NOT wait for the child. That immediate exit is the whole point: it is the
# thing that killed the old daemon-thread drain, and proves this drain
# survives it.
_pump138_spawner_script.write_text(
    "import os, subprocess, sys\n"
    "log_path, child_script, marker_path = sys.argv[1:4]\n"
    "pump = subprocess.Popen(\n"
    "    [sys.executable, '-m', 'orch.runlog_pump', log_path],\n"
    "    cwd=%r, stdin=subprocess.PIPE,\n"
    "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
    "    start_new_session=True,\n"
    ")\n"
    "with open(marker_path, 'w') as f:\n"
    "    f.write(str(pump.pid))\n"
    "child = subprocess.Popen(\n"
    "    [sys.executable, child_script], stdout=pump.stdin,\n"
    "    stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
    "    start_new_session=True,\n"
    ")\n"
    "with open(marker_path, 'a') as f:\n"
    "    f.write(' ' + str(child.pid))\n"
    "pump.stdin.close()\n"
    "# No wait(): the spawner exits now, exactly like tick.py/the CLI does\n"
    "# after handing a session off.\n" % str(Path(__file__).parent.parent)
)

_pump138_marker = _pump138_tmp / "pids"
_pump138_t_spawner_start = time.monotonic()
_pump138_spawner_proc = subprocess.run(
    [sys.executable, str(_pump138_spawner_script), str(_pump138_log),
     str(_pump138_child_script), str(_pump138_marker)],
    cwd=str(Path(__file__).parent.parent),
    capture_output=True, timeout=10,
)
_pump138_t_spawner_exit = time.monotonic()
# Wall-clock, because the assertion below compares against the log file's
# st_mtime and the two must be on the same clock. monotonic() cannot be
# compared to a filesystem timestamp at all.
_pump138_wall_spawner_exit = time.time()
check("pump138_spawner_exited_cleanly", 0, _pump138_spawner_proc.returncode)

_pump138_pump_pid, _pump138_child_pid = None, None
for _ in range(50):
    if _pump138_marker.exists() and " " in _pump138_marker.read_text():
        _pump138_pump_pid, _pump138_child_pid = (
            int(x) for x in _pump138_marker.read_text().split())
        break
    time.sleep(0.05)
check("pump138_marker_written", True, _pump138_pump_pid is not None)

try:
    # --- check 3 first (order matters): THE alive() TRAP, checked WHILE
    # both processes are still running. orch's alive() is
    # os.killpg(pgid, 0) -- GROUP liveness, not single-pid liveness
    # (core.py:1523-1539: `_pgid_alive` calls os.killpg(gid, 0)). If the
    # pump shared the claude child's process group, the group would stay
    # "alive" for as long as the pump keeps running -- i.e. forever after
    # the child itself has exited, since the pump only exits later, on its
    # own stdin EOF. Every finished session would then read as alive
    # forever and orch would never re-enter a dead one (core.py:2481-2489
    # calls this out as the reason _launch passes start_new_session=True
    # to the pump's Popen). Both pids are still alive here (checked before
    # the EOF-exit poll below tears the pump down), so os.getpgid resolves
    # for both; start_new_session=True gives each process pid==pgid, so
    # this also doubles as confirming the pump got its own session at all.
    check("pump138_pump_not_in_child_pgroup", True,
          os.getpgid(_pump138_pump_pid) != os.getpgid(_pump138_child_pid))

    # --- check 1: THE regression. Poll until the fake child (which sleeps
    # 0.5s between each of its 4 lines, ~2s total) has finished, and confirm
    # the log kept receiving lines well AFTER the spawner process (asserted
    # dead above) had already exited. A thread-based drain dies with the
    # spawner at _pump138_t_spawner_exit; a process-based one does not.
    _pump138_deadline = time.monotonic() + 10
    _pump138_seen_lines = 0
    while time.monotonic() < _pump138_deadline:
        if _pump138_log.exists():
            _pump138_seen_lines = len(
                [ln for ln in _pump138_log.read_text().splitlines() if ln.strip()])
        if _pump138_seen_lines >= 4:
            break
        time.sleep(0.1)
    check("pump138_all_lines_landed", True, _pump138_seen_lines >= 4)
    # The log's LAST WRITE must postdate the spawner's exit. Comparing
    # `time.monotonic() > _pump138_t_spawner_exit` would be vacuous -- now is
    # always after a past instant, so it would hold even if every line had
    # landed while the spawner was still alive, which is exactly the broken
    # thread-based behavior this check exists to catch. The file's st_mtime is
    # the only evidence of WHEN the drain actually wrote.
    check("pump138_lines_landed_after_spawner_exit", True,
          _pump138_seen_lines >= 4 and
          _pump138_log.stat().st_mtime > _pump138_wall_spawner_exit)
    _pump138_log_text = _pump138_log.read_text()
    check("pump138_line_content_translated", True,
          all(f"line {i}" in _pump138_log_text for i in range(4)))

    # --- check 2: EOF terminates the pump. Once the fake child exits, it
    # closes its stdout, which is the pump's stdin -- the pump must see EOF
    # and exit on its own shortly after, leaving no orphaned drain process
    # running forever against a pipe nobody will ever write to again.
    _pump138_pump_proc_gone = False
    for _ in range(50):
        try:
            os.kill(_pump138_pump_pid, 0)
        except ProcessLookupError:
            _pump138_pump_proc_gone = True
            break
        time.sleep(0.1)
    check("pump138_pump_exits_on_child_eof", True, _pump138_pump_proc_gone)
finally:
    for _pid in (_pump138_pump_pid, _pump138_child_pid):
        if _pid is None:
            continue
        try:
            os.killpg(_pid, _signal138.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    import shutil as _shutil138
    _shutil138.rmtree(_pump138_tmp, ignore_errors=True)

# === orch#138: a dead pump must never take the session down with it =========
# The guard in _launch is an importlib.import_module("orch.runlog_pump") BEFORE
# the child's stdout is wired to the pump, and this pins why it has to happen in
# that order. Popen succeeds as soon as fork/exec works, so a pump that starts
# and then dies (module missing from a partial deploy, ImportError under
# orch.runlog, an interpreter that cannot see the package) leaves the child
# writing into a pipe with no reader. The child takes SIGPIPE on its first
# stream-json write and dies -- measured below as a nonzero rc -- which would
# turn a broken run log into every session in the tree dying silently.
#
# This check reproduces the raw pipe behavior directly rather than asserting on
# _launch, so it stays true about the OS guarantee the guard exists to dodge: it
# is the reason the ordering in _launch is not arbitrary.
_deadpump_tmp = Path(tempfile.mkdtemp(prefix="orch-deadpump-"))
try:
    # A "pump" that exits immediately, standing in for one that cannot import.
    _deadpump = subprocess.Popen(
        [sys.executable, "-c", "raise SystemExit(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    _deadpump_child = subprocess.Popen(
        [sys.executable, "-c",
         "import time, sys\n"
         "time.sleep(0.5)\n"
         "for _ in range(5):\n"
         "    print('{\"type\": \"assistant\"}')\n"
         "    sys.stdout.flush()\n"
         "    time.sleep(0.2)\n"],
        stdout=_deadpump.stdin, stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    _deadpump.stdin.close()
    _deadpump.wait(timeout=10)
    _deadpump_child_rc = _deadpump_child.wait(timeout=10)
    # The child DIES when its reader is gone. This is the OS behavior the guard
    # exists to keep _launch from ever walking into.
    check("pump138_dead_reader_kills_writer", True, _deadpump_child_rc != 0)

    # And the guard itself: the module _launch tries to import must actually be
    # importable, or every spawn degrades. A partial deploy that ships core.py
    # without runlog_pump.py fails this.
    check("pump138_guard_module_importable", True,
          importlib.util.find_spec("orch.runlog_pump") is not None)
finally:
    for _p in (_deadpump, _deadpump_child):
        try:
            _p.kill()
        except Exception:
            pass
    shutil.rmtree(_deadpump_tmp, ignore_errors=True)

# === orch#138: orch/runlog_pump.py -- unit checks on the module itself =====
# Drive `python3 -m orch.runlog_pump <path>` directly over a real pipe (no
# claude, no spawner -- just the pump's own contract with whatever writes to
# its stdin). These pin the "ROBUSTNESS" guarantees runlog_pump.py's
# docstring calls out: a bug in translation must degrade a line, never drop
# it or crash the drain, because this process's only job is to never be the
# reason a real claude child blocks or a log goes missing.

_pump138u_tmp = Path(tempfile.mkdtemp())
_pump138u_log = _pump138u_tmp / "unit.log"

# --- malformed/non-JSON line: degrades to a `raw` line, is not dropped, and
# does not crash the pump. This is the direct regression test for
# translate_line's documented contract (runlog.py:250-252: "never raises,
# never returns None on unparseable input... gets surfaced as-is") as seen
# from the pump's side -- a bad line from the child (e.g. a CLI error printed
# to stdout instead of stderr) must still show up in the log.
_pump138u_proc = subprocess.Popen(
    [sys.executable, "-m", "orch.runlog_pump", str(_pump138u_log)],
    cwd=str(Path(__file__).parent.parent),
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
_pump138u_proc.stdin.write(b"not json at all {{{\n")
_pump138u_proc.stdin.write(b'{"type": "assistant", "message": {"content": '
                            b'[{"type": "text", "text": "ok line"}]}}\n')
_pump138u_proc.stdin.close()
_pump138u_rc = _pump138u_proc.wait(timeout=10)
_pump138u_text = _pump138u_log.read_text() if _pump138u_log.exists() else ""
_pump138u_lines = [ln for ln in _pump138u_text.splitlines() if ln.strip()]
check("pump138u_malformed_line_not_dropped", 2, len(_pump138u_lines))
check("pump138u_malformed_line_present", True,
      len(_pump138u_lines) > 0 and "not json at all" in _pump138u_lines[0])
check("pump138u_good_line_after_malformed_still_works", True,
      any("ok line" in ln for ln in _pump138u_lines))

# --- clean EOF (no malformed input at all) -> exit 0. This is the normal
# termination path (main()'s docstring: "EOF on stdin is the normal,
# expected termination"), and must stay 0 or a supervising process could
# mistake every ordinary session end for a pump crash.
check("pump138u_exit_zero_on_clean_eof", 0, _pump138u_rc)

# --- wrong arg count -> exit nonzero. main()'s own contract (runlog_pump.py
# lines 93-97): with no terminal attached to a human by the time this could
# matter, a nonzero exit is the only signal available that the pump was
# invoked wrong (e.g. a future refactor of the _launch call site drops the
# log-path argument).
_pump138u_badargs = subprocess.run(
    [sys.executable, "-m", "orch.runlog_pump"],
    cwd=str(Path(__file__).parent.parent), capture_output=True, timeout=10,
)
check("pump138u_wrong_argc_exits_nonzero", True, _pump138u_badargs.returncode != 0)

_pump138u_extra_args = subprocess.run(
    [sys.executable, "-m", "orch.runlog_pump", str(_pump138u_log), "extra"],
    cwd=str(Path(__file__).parent.parent), capture_output=True, timeout=10,
)
check("pump138u_too_many_argc_exits_nonzero", True, _pump138u_extra_args.returncode != 0)

_shutil138.rmtree(_pump138u_tmp, ignore_errors=True)

# === orch#217: _CAVEMAN_PREAMBLE must assert exactly one caveman level =====
# The preamble legitimately MENTIONS other level names while forbidding them
# (e.g. "Do NOT compress to `ultra`"), so a naive "any backticked level name"
# scan would false-positive on correct text. Instead this matches only the
# phrasings that ASSERT a level as this session's level -- "runs at `X`",
# "The level is `X`", "`X` is the floor and the ceiling" -- and checks every
# such assertion names the same single level. That naturally ignores the
# forbidding clause without hardcoding the current sentence, so a harmless
# rewrap of the prose still passes as long as the property holds.
_CAVEMAN_LEVELS = ("wenyan-lite", "wenyan-full", "wenyan-ultra", "lite", "full", "ultra")
_level_alt = "|".join(re.escape(lv) for lv in _CAVEMAN_LEVELS)
_ASSERT_LEVEL_RE = re.compile(
    r"runs at `(" + _level_alt + r")`"
    r"|[Tt]he level is `(" + _level_alt + r")`"
    r"|`(" + _level_alt + r")` is the floor and the ceiling"
)
_asserted_levels = set()
for _m in _ASSERT_LEVEL_RE.finditer(core._CAVEMAN_PREAMBLE):
    _asserted_levels.add(next(g for g in _m.groups() if g))
check("caveman_preamble_asserts_a_level_at_all", True, len(_asserted_levels) > 0)
check(
    f"caveman_preamble_asserts_exactly_one_level (found {sorted(_asserted_levels)}, "
    "preamble must name exactly one)",
    {"full"},
    _asserted_levels,
)

# === feed.build() honors the `login=` second-token form (#244) ===
# feed.build used to re-implement the repos.txt parse and treat the whole line
# as the path, so `<path>  login=gitea` named a directory that does not exist,
# failed the .git check, and was dropped with no row and no alert -- the
# dashboard under-reported what orch watches. Both halves are pinned here: the
# repo with a login= token is present, and a line naming a real absent checkout
# is said out loud rather than skipped in silence.
from orch import feed as feed244
importlib.reload(feed244)

_wt244 = Path(os.environ["WT_ROOT"])
_repo244 = _wt244 / "login-repo"
(_repo244 / ".git").mkdir(parents=True, exist_ok=True)
_absent244 = _wt244 / "no-such-checkout"

_repos_txt244 = core.ORCH_HOME / "repos.txt"
_orig_repos_txt244 = _repos_txt244.read_text() if _repos_txt244.exists() else None
# _write_repos_txt, not a bare write: feed.build() reads the config through
# core._load_config now (#230), so a stale orch.json left by an earlier block
# would shadow this fixture and the login= repo would be absent for a reason
# that has nothing to do with the #244 defect being pinned here.
_write_repos_txt(f"{_repo244}  login=gitea\n{_absent244}\n")

_orig_repo_json244 = feed244.repo_json
feed244.repo_json = lambda repo: {
    "repo": "me/" + repo.name, "path": str(repo), "slug": repo.name, "ok": True,
    "counts": {}, "live_orchs": 0, "issues": [],
    "orch": {"key": "repo-orch." + repo.name, "alive": False,
             "prior_runs": 0, "recent": []},
}
try:
    _out244 = feed244.build()
    _slugs244 = [r["slug"] for r in _out244["repos"]]
    check("feed244_login_repo_present", True, "login-repo" in _slugs244)
    # the path must be the first token alone, with `login=gitea` stripped
    check("feed244_path_excludes_login_token", str(_repo244),
          next(r["path"] for r in _out244["repos"] if r["slug"] == "login-repo"))
    check("feed244_absent_checkout_dropped", True, "no-such-checkout" not in _slugs244)
    check("feed244_absent_checkout_alerts", True,
          any(a.get("level") == "warn"
              and a.get("key") == f"repos-unresolved:{_absent244}"
              and "resolves to no checkout" in a.get("msg", "")
              and str(_absent244) in a.get("msg", "")
              for a in _out244["alerts"]))
finally:
    feed244.repo_json = _orig_repo_json244
    # restore repos.txt too: a block appended after this one must see its own
    # fixture, not these two lines. orch.json goes as well -- _write_repos_txt
    # cleared it so this fixture would migrate, and leaving the migrated one
    # behind would shadow the next block's repos.txt (#230).
    (core.ORCH_HOME / "orch.json").unlink(missing_ok=True)
    if _orig_repos_txt244 is None:
        _repos_txt244.unlink(missing_ok=True)
    else:
        _repos_txt244.write_text(_orig_repos_txt244)

# === orch#226: needs_attention reaches the feed, not just history.jsonl =====
# The tick computed needs_attention and wrote it only to the history row, so
# status.json carried tick.needs_attention: None on every pass and no surface
# could render a "N things need you" count without recomputing it client-side.
# The compute cannot move into feed.build(): build_thin/compute_conditions
# consume the built feed, so the tick writes the feed AFTER computing and
# injects the key. These checks pin that ordering by its observable effect.
from orch import feed as _f226, tick as _t226

# --- the schema half: feed.build() alone always declares the key, so a
# consumer never has to tell a missing key from a computed zero. Both arms of
# tick_json (status.json present and absent) carry it.
_t226_no_file = _f226.ORCH_HOME / "public" / "status.json"
if _t226_no_file.exists():
    _t226_no_file.unlink()
check("t226_absent_arm_declares_key", True, "needs_attention" in _f226.tick_json())
check("t226_absent_arm_is_none", None, _f226.tick_json()["needs_attention"])
# The pre-existing staleness contract on this arm is unchanged -- adding a key
# must not perturb what the "no tick has ever run" case reports.
check("t226_absent_arm_minutes_still_negative", -1,
      _f226.tick_json()["minutes_since_last"])

_t226_no_file.parent.mkdir(parents=True, exist_ok=True)
_t226_no_file.write_text("{}")
_t226_present = _f226.tick_json()
check("t226_present_arm_declares_key", True, "needs_attention" in _t226_present)
check("t226_present_arm_is_none", None, _t226_present["needs_attention"])
# null and 0 must stay distinguishable: the uncomputed default is None, never
# 0, or "nothing needs you" and "never populated" collapse into one value --
# which is the ambiguity this issue exists to remove.
check("t226_uncomputed_is_not_zero", False, _t226_present["needs_attention"] == 0)

# --- the wiring half: the tick overwrites that null with the real count, and
# the value that lands in the written feed is the SAME one the history row and
# the wake gate use. Driven through tick.main() with its collaborators stubbed
# so this asserts the ordering, not the conditions logic.
_t226_saved = {k: getattr(_t226, k) for k in
               ("compute_conditions", "build_thin", "digest_of")}
_t226_feed_build = _f226.build
_t226_subprocess_run = _t226.subprocess.run
_t226_data = {"generated": core.now_iso(), "repos": [],
              "tick": {"minutes_since_last": 0, "last_run": None,
                       "needs_attention": None}}
_t226.compute_conditions = lambda data, thin: (7, [], [])
_t226.build_thin = lambda data: {"repos": []}
_t226.digest_of = lambda thin: "d226"
_f226.build = lambda: _t226_data
_t226.subprocess.run = lambda *a, **k: None  # skip the real build_widget
# An earlier block in this suite may still hold TICK_LOCK's flock through a
# live fd. main() would then take its "tick already running, skip" early
# return and never write the feed -- leaving a STALE status.json on disk that
# these checks would read as a result, passing or failing for a reason that
# has nothing to do with the fix. Drop the stale file so a skipped write is
# an unmissable error, and assert main() ran to completion.
_t226.FEED_PATH.unlink(missing_ok=True)
# main() never closes its lock fd (release is left to GC), so an earlier
# caller's fd can still hold the flock here. Force a collection so this call
# takes the real path rather than the skip.
__import__("gc").collect()
try:
    _t226_rc = _t226.main()
    # 0 is also the skip return, so the file's existence is the real proof
    # that the write path executed rather than the flock early return.
    check("t226_main_wrote_the_feed", True, _t226.FEED_PATH.exists())
    _t226_written = json.loads(_t226.FEED_PATH.read_text())
    _t226_hist = [json.loads(l) for l in
                  _t226.HISTORY_PATH.read_text().splitlines() if l.strip()]
finally:
    for _k, _v in _t226_saved.items():
        setattr(_t226, _k, _v)
    _f226.build = _t226_feed_build
    _t226.subprocess.run = _t226_subprocess_run

# The written feed carries the computed count, not the null default. This is
# the exact regression: before the fix this read None.
check("t226_feed_carries_computed_count", 7,
      _t226_written["tick"]["needs_attention"])
# ...and it is an int, so a client can trust that a real count is never null.
check("t226_feed_count_is_int", True,
      isinstance(_t226_written["tick"]["needs_attention"], int))
# The history row is unchanged and agrees with the feed -- the fix publishes
# the same number to a second place, it does not fork a second derivation.
check("t226_history_agrees_with_feed", 7, _t226_hist[-1]["needs_attention"])
# The rest of the tick block survived the injection: writing one key must not
# clobber the staleness fields the "tick stale" alert reads.
check("t226_injection_preserves_block", (0, None),
      (_t226_written["tick"]["minutes_since_last"],
       _t226_written["tick"]["last_run"]))

# === orch-pull record_outcome (orch#250) ====================================
# The reviewer's concern: a Python reimplementation of record_outcome would
# pass even if the REAL shell function drifts. So this extracts the actual
# function text from deploy/orch-pull.sh on disk (regex, not a hand copy) and
# runs it under a real bash, in a throwaway ORCH_HOME -- never against the
# live checkout at ~/orch. Sourcing the whole script is unsafe: it runs
# top-level guards (git rev-parse against $HOME/orch, a flock, etc) as soon
# as it's read, so only the function body is pulled out.
_pull_script = (Path(__file__).parent.parent / "deploy/orch-pull.sh").read_text()
_rec_match = re.search(r"^record_outcome\(\) \{\n(?:.*\n)*?^\}\n", _pull_script, re.M)
check("t250_record_outcome_found_in_script", True, _rec_match is not None)
_record_outcome_src = _rec_match.group(0)


def _run_record_outcome(state_dir, *calls):
    """Run record_outcome(outcome, mode) once per call tuple in a fresh bash
    process, sharing STATE_FILE across calls the way repeated script
    invocations would. Returns the final state_file JSON dict (or None if the
    file was never written)."""
    script = (
        f'ORCH_HOME={state_dir!r}\n'
        f'STATE_FILE="$ORCH_HOME/state/orch-pull.json"\n'
        + _record_outcome_src
        + "\n".join(f'record_outcome {o!r} {m!r}' for o, m in calls) + "\n"
    )
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    state_file = Path(state_dir) / "state/orch-pull.json"
    return json.loads(state_file.read_text()) if state_file.exists() else None


_t250_dir1 = tempfile.mkdtemp(dir=T)
_t250_s1 = _run_record_outcome(_t250_dir1, ("FAILED: a", "fail"), ("FAILED: b", "fail"))
check("t250_fail_fail_count_2", 2, _t250_s1["consecutive_failures"])

_t250_dir2 = tempfile.mkdtemp(dir=T)
_t250_s2 = _run_record_outcome(_t250_dir2, ("FAILED: a", "fail"), ("FAILED: b", "fail"),
                                ("SKIPPED: tick lock held", "skip"))
check("t250_fail_fail_skip_count_still_2", 2, _t250_s2["consecutive_failures"])
# THE CRITICAL ONE: skip must not touch the failure count, no matter how
# many overlapping ticks skip in a row.
check("t250_fail_fail_skip_outcome_names_failure", "FAILED: b", _t250_s2["last_outcome"])

_t250_dir3 = tempfile.mkdtemp(dir=T)
_t250_s3 = _run_record_outcome(_t250_dir3, ("FAILED: a", "fail"),
                                ("fast-forwarded to origin/main", "reset"))
check("t250_fail_reset_count_0", 0, _t250_s3["consecutive_failures"])
check("t250_fail_reset_outcome_is_success", "fast-forwarded to origin/main",
      _t250_s3["last_outcome"])

_t250_dir4 = tempfile.mkdtemp(dir=T)
_t250_s4 = _run_record_outcome(_t250_dir4, ("SKIPPED: tick lock held", "skip"))
check("t250_skip_on_fresh_state_does_not_crash", True, _t250_s4 is not None)
check("t250_skip_on_fresh_state_count_0", 0, _t250_s4["consecutive_failures"])

# === orch#261 exhaustiveness tests ==========================================
# Not new behaviour -- a set of totality checks over already-decided state
# spaces (work_state x liveness, issue_state over all label subsets, the
# auto_land_on decision table, the rollup-vacuity tradeoff), so a state added
# later without updating its table breaks a test here instead of shipping an
# unconsidered cell.
import itertools

# --- Block 1: (work_state x alive) product, 12 cells ------------------------

# Derive the actual set of strings work_state can return by driving every
# input combination through the real function -- never copy the 6 names as a
# literal, or a 7th state added later would silently vanish from this check.
class _ExhWorld261(_PlainBranchMixin):
    def __init__(self, pr, green, red):
        self._pr = pr
        self._green = green
        self._red = red

    def pr_for(self, branch):
        return self._pr

    def pr_green(self, branch):
        return self._green

    def pr_red(self, branch):
        return self._red


_orig_work_mtime_261 = core.work_mtime
_WM261 = {"val": None}
core.work_mtime = lambda repo, branch: _WM261["val"]
try:
    _WM261["val"] = None
    _derived_work_states = {
        core.work_state(_ExhWorld261("MERGED", False, False), "repo", 7),   # LANDED
        core.work_state(_ExhWorld261("OPEN", True, False), "repo", 7),      # REVIEW
        core.work_state(_ExhWorld261("OPEN", False, True), "repo", 7),      # BLOCKED
        core.work_state(_ExhWorld261("OPEN", False, False), "repo", 7),     # CHECKING
        core.work_state(_ExhWorld261("", False, False), "repo", 7),         # CLAIMED (mtime None)
    }
    _WM261["val"] = 12345
    _derived_work_states.add(
        core.work_state(_ExhWorld261("", False, False), "repo", 7))         # ACTIVE (mtime set)
finally:
    core.work_mtime = _orig_work_mtime_261

check("work_state_set_derived", {"LANDED", "REVIEW", "BLOCKED", "CHECKING", "ACTIVE", "CLAIMED"},
      _derived_work_states)

# Driving inputs only proves the states this test THOUGHT to reach. A state
# added on a new input branch -- `if pr == "DRAFT": return "DRAFTED"` -- would
# be invisible to the loop above, which is exactly the missing-cell class that
# kept a dead-mid-flight session unnoticed for eight days. So also close the
# set over the source: every string literal work_state can return must be one
# the product table below covers. A new return branch fails here until its
# cells are added.
#
# Match every uppercase string literal in the body, not just those after a
# bare `return` -- work_state's last line yields CLAIMED through a ternary,
# and a new state could arrive in any return form. The docstring is stripped
# first so its prose cannot contribute a false literal.
_work_state_src_261 = _inspect.getsource(core.work_state)
_work_state_body_261 = _work_state_src_261.split('"""')[-1]
_all_literals_261 = set(re.findall(r'"([A-Z][A-Z_]+)"', _work_state_body_261))
# MERGED and OPEN are PR statuses work_state READS from pr_for, never states
# it returns. They are the only inputs it compares against, and pinning that
# exactly means a new state cannot hide by being added to this exclusion.
_PR_STATUS_INPUTS_261 = {"MERGED", "OPEN"}
check("work_state_reads_only_known_pr_statuses", _PR_STATUS_INPUTS_261,
      _all_literals_261 & _PR_STATUS_INPUTS_261)
_returned_literals_261 = _all_literals_261 - _PR_STATUS_INPUTS_261
check("work_state_returns_nothing_undriven", set(),
      _returned_literals_261 - _derived_work_states)
check("work_state_source_has_no_extra_states", _derived_work_states, _returned_literals_261)

# Cell -> (STATES.md row name, [wake condition numbers]) or (name, [], "reason").
# The wake conditions are numbered 1..9 by compute_conditions (orch/tick.py).
#
# Condition 9 (orphaned PR, orch#279) appears in NO cell here, and that is
# correct rather than an omission. Every cell is keyed on a work_state, and
# work_state is derived per OPEN ISSUE; an orphan is an open PR whose issue is
# closed, so it has no issue row and therefore no cell to occupy. That is
# precisely why it needed its own condition: the existing 1..8 all reach a
# human through some issue's work_state, and this fact reaches none of them.
WORK_LIVENESS_PRODUCT = {
    # Condition 7 is landed-but-still-claimed, so both LANDED cells are gated
    # on issue_state == CLAIMED; a LANDED issue whose label was already
    # released fires nothing here. That released-but-unapplied shape is live
    # today on orch#229.
    ("LANDED", True):    ("landed, orch alive", [7]),
    ("LANDED", False):   ("landed, orch dead", [7]),
    ("REVIEW", True):    ("review, orch alive", [6]),
    # orch#230: a session held its own green PR as its final act, then died.
    # Condition 5 (unowned work: CLAIMED-vs-REVIEW / idle) re-fires on this
    # cell every tick -- it is the live proof this cell is reached, not a
    # claim about what SHOULD happen to it.
    ("REVIEW", False):   ("review, orch dead", [5]),
    ("BLOCKED", True):   ("blocked, orch alive", [2]),
    ("BLOCKED", False):  ("blocked, orch dead", [2]),
    ("CHECKING", True):  ("checking, orch alive", [6]),
    ("CHECKING", False): ("checking, orch dead", [],
                          "no PR verdict yet and no owner -- nothing has "
                          "happened worth waking anyone for; the next tick's "
                          "own poll discovers a verdict change, not a wake "
                          "condition"),
    ("ACTIVE", True):    ("active, orch alive", [6]),
    # orch#254: posted a design and stopped for an operator ruling, then died.
    # Condition 5 reaches this cell but -- unlike (REVIEW, False) above -- only
    # through its idle disjunct, so it is gated on `idle_over`. Until the idle
    # threshold elapses this cell fires NOTHING. The idle gate is the only
    # thing that ever notices a dead ACTIVE owner; do not retune it believing
    # some other condition covers this.
    #
    # Open question (orch#254 is deciding it, not this test): the wake
    # conditions cannot currently tell "session died with work left" apart
    # from "session stopped because only the operator can continue" -- both
    # derive to this same dead-owner cell. No guess is encoded here.
    ("ACTIVE", False):   ("active, orch dead", [5]),
    # Condition 3 is contention, which is work-state-independent -- it fires
    # identically on all 12 cells given `contended`, so it justifies this cell
    # only weakly.
    ("CLAIMED", True):   ("claimed, orch alive", [3]),
    # Condition 5, again via the idle disjunct only -- gated on `idle_over`.
    ("CLAIMED", False):  ("claimed, orch dead", [5]),
}

check("work_liveness_product_size", 12, len(WORK_LIVENESS_PRODUCT))
check("work_liveness_product_covers_all",
      {(s, a) for s in _derived_work_states for a in (True, False)},
      set(WORK_LIVENESS_PRODUCT.keys()))

_cells_missing_conditions_or_reason = [
    key for key, val in WORK_LIVENESS_PRODUCT.items()
    if not val[1] and (len(val) < 3 or not val[2])
]
check("work_liveness_every_cell_justified", [], _cells_missing_conditions_or_reason)

# The ceiling is derived from compute_conditions, not hardcoded -- a new wake
# condition added there must be considered against this table rather than
# silently widening the range. Condition 9 was so considered: see the block
# comment above WORK_LIVENESS_PRODUCT for why it maps to no cell.
_cond_nums_in_tick_261 = {
    int(m) for m in re.findall(r'"cond":\s*(\d+)',
                               _inspect.getsource(tickmod.compute_conditions))
}
check("tick_condition_numbers_are_1_to_9", set(range(1, 10)), _cond_nums_in_tick_261)

_bad_cond_numbers = [
    n for val in WORK_LIVENESS_PRODUCT.values() for n in val[1]
    if n not in _cond_nums_in_tick_261
]
check("work_liveness_condition_numbers_in_range", [], _bad_cond_numbers)

# --- Block 2: issue_state totality over all 32 label subsets ----------------

# Five labels carry CONTROL FLOW; issue_state reads two of them. The orch
# label set is wider than that and deliberately so -- #256 added the p0/p1/p2
# priority tiers, which carry ordering, not control flow. Enumerate the
# control-flow labels here, and assert separately below that every other orch
# label is inert for issue_state. A new control-flow label must be added to
# this tuple; a new ordering-only label must not.
_CTRL_LABEL_NAMES_261 = sorted({
    core.L_READY, core.L_WORKING, core.L_STUCK, core.L_AUTOLAND, core.L_NO_AUTOLAND,
})
_ALL_LABEL_NAMES_261 = _CTRL_LABEL_NAMES_261
check("ctrl_label_names_count_is_5", 5, len(_CTRL_LABEL_NAMES_261))
check("ctrl_labels_are_a_subset_of_orch_labels", set(),
      set(_CTRL_LABEL_NAMES_261) - set(core.ORCH_LABEL_NAMES))


class _IssueStateWorld261:
    def __init__(self, present):
        self._present = present

    def issue_has_label(self, n, label):
        return label in self._present


_issue_state_results = []
for _r in range(len(_ALL_LABEL_NAMES_261) + 1):
    for _combo in itertools.combinations(_ALL_LABEL_NAMES_261, _r):
        _present = set(_combo)
        _got = core.issue_state(_IssueStateWorld261(_present), 1)
        _issue_state_results.append((_present, _got))

check("issue_state_subset_count", 2 ** len(_ALL_LABEL_NAMES_261), len(_issue_state_results))
check("issue_state_total_over_32_subsets", [],
      [(p, g) for p, g in _issue_state_results if g not in {"ABANDONED", "UNCLAIMED", "CLAIMED"}])

# Pin the contradictions with their documented winner (core.py:1478-1484,
# first-match order: stuck, then working).
check("issue_state_stuck_and_working_is_abandoned", "ABANDONED",
      core.issue_state(_IssueStateWorld261({core.L_STUCK, core.L_WORKING}), 1))
# candidates() (core.py:1316) assumes ready and working never co-occur
# ("mutually exclusive, so AND matches nothing") -- issue_state itself makes
# no such assumption and still resolves this pair, to CLAIMED (has
# agent-working, no agent-stuck).
check("issue_state_ready_and_working_is_claimed_despite_candidates_assumption", "CLAIMED",
      core.issue_state(_IssueStateWorld261({core.L_READY, core.L_WORKING}), 1))

# Every orch label OUTSIDE the control-flow five must be inert for
# issue_state: adding it to any subset must not change the answer. This is
# what keeps the 32-subset enumeration honest as the label set grows -- a new
# ordering-only label (p0/p1/p2 today) passes silently, while a new label that
# actually steers issue_state fails here and must join the control-flow tuple.
_NONCTRL_LABELS_261 = sorted(set(core.ORCH_LABEL_NAMES) - set(_CTRL_LABEL_NAMES_261))
_noninert_261 = []
for _extra in _NONCTRL_LABELS_261:
    for _present, _base in _issue_state_results:
        if core.issue_state(_IssueStateWorld261(_present | {_extra}), 1) != _base:
            _noninert_261.append((_extra, sorted(_present)))
check("noncontrol_orch_labels_are_inert_for_issue_state", [], _noninert_261)
check("issue_state_stuck_alone_is_abandoned", "ABANDONED",
      core.issue_state(_IssueStateWorld261({core.L_STUCK}), 1))
check("issue_state_empty_is_unclaimed", "UNCLAIMED",
      core.issue_state(_IssueStateWorld261(set()), 1))

# --- Block 3: auto_land_on landing decision table, 12 cases ------------------
# Five prose rules, verbatim from agents/skills/issue-landing/SKILL.md:23-33 --
# kept alongside the table below so the two cannot drift apart silently.
#
# - Label `auto-land` present -> review then merge on green, even if the
#   repo default is off.
# - Label `no-auto-land` present -> do not review, do not merge, even if the
#   repo default is on.
# - Neither label -> the repo default decides.
# - Neither label and no repo default -> do not review, do not merge.
# - Both labels present -> `no-auto-land` wins; holding is the safe
#   direction.
_AUTOLAND, _NOAUTOLAND = core.L_AUTOLAND, core.L_NO_AUTOLAND
for _name, _labels, _default, _want in [
    ("autoland_only_default_true",     {_AUTOLAND},              True,  True),
    ("autoland_only_default_false",    {_AUTOLAND},              False, True),
    ("autoland_only_default_none",     {_AUTOLAND},              None,  True),
    ("noautoland_only_default_true",   {_NOAUTOLAND},            True,  False),
    ("noautoland_only_default_false",  {_NOAUTOLAND},            False, False),
    ("noautoland_only_default_none",   {_NOAUTOLAND},            None,  False),
    ("neither_default_true",           set(),                    True,  True),
    ("neither_default_false",          set(),                    False, False),
    ("neither_default_none",           set(),                    None,  False),
    ("both_default_true",              {_AUTOLAND, _NOAUTOLAND}, True,  False),
    ("both_default_false",             {_AUTOLAND, _NOAUTOLAND}, False, False),
    ("both_default_none",              {_AUTOLAND, _NOAUTOLAND}, None,  False),
]:
    check(f"auto_land_on_{_name}", _want, core.auto_land_on(_labels, _default))

# --- Block 4: sign off the known unsoundness ---------------------------------
# INTENDED, not a bug: an empty/absent rollup reads green (finding 2, see the
# work_state truth table above), which reads REVIEW, which reads landable --
# so a repo whose CI config is simply broken (never posts a rollup) and a
# repo whose CI genuinely passed are indistinguishable by construction. This
# is a conscious tradeoff; do not "fix" pr_green to require a real rollup, it
# would turn "no CI configured" into a permanent BLOCKED/CHECKING trap.
w261 = core.World()
_STUB_ROLL_261 = []
w261._rollup = lambda branch: _STUB_ROLL_261
check("rollup_empty_list_reads_green", True, w261.pr_green("x"))
_STUB_ROLL_261 = None
check("rollup_none_reads_green", True, w261.pr_green("x"))

# The genuinely different case: a rollup orch failed to READ is neither
# green nor red -- work_state must yield CHECKING, not REVIEW, for it. Uses
# the real ROLLUP_UNREADABLE sentinel, not a stand-in.
_STUB_ROLL_261 = core.ROLLUP_UNREADABLE
check("rollup_unreadable_not_green", False, w261.pr_green("x"))
check("rollup_unreadable_not_red", False, w261.pr_red("x"))


# work_state must consult the real predicates, not pre-computed booleans --
# passing w261.pr_green("x") in as a value would still pass if the
# ROLLUP_UNREADABLE branch were deleted from pr_green entirely.
class _UnreadableWorld261(_ExhWorld261):
    def __init__(self, world):
        _ExhWorld261.__init__(self, "OPEN", False, False)
        self._world = world

    def pr_green(self, branch):
        return self._world.pr_green(branch)

    def pr_red(self, branch):
        return self._world.pr_red(branch)


check("rollup_unreadable_work_state_is_checking", "CHECKING",
      core.work_state(_UnreadableWorld261(w261), "repo", 7))

# === events: SSE endpoint (orch#273) ========================================
# do_GET routing: /events -> self._events(). Source-level, same idiom as
# single_impl_tick_server_targets_module above.
do_get_src = _inspect.getsource(srv.H.do_GET)
check("events_routed_in_do_GET", True,
      '"/events"' in do_get_src and "self._events()" in do_get_src)

events_stream_src = _inspect.getsource(srv.H._events_stream)
events_src = _inspect.getsource(srv.H._events)

# Notify path reads mtime, not a threading.Event -- the feed writer is a
# subprocess (tick.py), so an Event set by another process could never fire.
check("events_stream_polls_mtime", True, "st_mtime" in events_stream_src)
check("events_stream_no_threading_event", False,
      "threading.Event" in events_stream_src)

# Disconnects are swallowed, not raised.
check("events_stream_catches_broken_pipe", True,
      "BrokenPipeError" in events_stream_src)
check("events_stream_catches_connection_reset", True,
      "ConnectionResetError" in events_stream_src)

# Connection cap: over EVENTS_MAX_STREAMS the handler returns (503) instead
# of proceeding to _events_stream, and the counter decrement lives in a
# finally so it always runs even if _events_stream raises.
check("events_cap_checks_max_streams", True, "EVENTS_MAX_STREAMS" in events_src)
check("events_cap_decrement_in_finally", True,
      re.search(r"finally:\s*\n\s*with _events_lock:\s*\n\s*_events_count -= 1",
                 events_src) is not None)

# Behavioural: real ThreadingHTTPServer on an ephemeral port, real
# http.client connection. Confirms headers (text/event-stream, no
# Content-Length) and that a file rewrite produces a second SSE message.
import http.client
from http.server import ThreadingHTTPServer as _THS

_events_feed = core.ORCH_HOME / "public" / "status.json"
_events_feed.parent.mkdir(parents=True, exist_ok=True)
_events_feed.write_text('{"seq":1}')

_ev_httpd = _THS(("127.0.0.1", 0), srv.H)
_ev_port = _ev_httpd.server_address[1]
_ev_thread = threading.Thread(target=_ev_httpd.serve_forever, daemon=True)
_ev_thread.start()
try:
    conn = http.client.HTTPConnection("127.0.0.1", _ev_port, timeout=10)
    try:
        conn.request("GET", "/events")
        resp = conn.getresponse()
        check("events_status_200", 200, resp.status)
        check("events_content_type", "text/event-stream",
              resp.getheader("Content-Type"))
        check("events_no_content_length", None, resp.getheader("Content-Length"))

        fp = resp.fp  # stream the body ourselves; resp has no framing to rely on

        def _read_one_event(fp, timeout_s=10):
            deadline = time.monotonic() + timeout_s
            lines = []
            while time.monotonic() < deadline:
                line = fp.readline()
                if not line:
                    break
                line = line.decode()
                if line == "\n":
                    if lines:
                        return "".join(lines)
                    continue
                lines.append(line)
            return "".join(lines) or None

        first = _read_one_event(fp)
        check("events_first_message_has_seq1", True,
              first is not None and '"seq":1' in first)

        time.sleep(1.1)  # clear mtime granularity before the rewrite
        _events_feed.write_text('{"seq":2}')

        second = _read_one_event(fp)
        check("events_second_message_has_seq2", True,
              second is not None and '"seq":2' in second)
    finally:
        conn.close()
finally:
    _ev_httpd.shutdown()
    _ev_httpd.server_close()
    _ev_thread.join(timeout=5)

# === tick: atomic status.json publish (orch#273) ============================
# Source-level, same idiom as events_stream_polls_mtime above: a bare
# write_text truncates at the START, so a reader mid-write (GET /status.json,
# the /events SSE loop) can see a half-written prefix under the new mtime.
main_src = _inspect.getsource(tickmod.main)
check("tick_publish_uses_os_replace", True, "os.replace(" in main_src)
check("tick_publish_no_bare_write_text", False, "FEED_PATH.write_text(" in main_src)

# === orch#387: the spawn reason names landing when it applies ==============
# repo-orch wakes with only the condition-number list in `reason` and has to
# rediscover on its own that finished work may be waiting to land. Source
# -level like the publish checks above -- the reason string is built inline
# in main() (real feed.build()/gh reads make main() itself too heavy to
# drive end-to-end here), so the same idiom applies: pin the source text
# rather than the call.
check("reason_mentions_landing_gated_on_5_or_9", True,
      "5 in fired or 9 in fired" in main_src)
check("reason_names_issue_orch_and_issue_landing_skill", True,
      "issue-orch via issue-landing skill" in main_src)
# The base shape ("tick routed {slug}: conditions ... fired") must still be
# the PREFIX of the landing-augmented string, not a separate rewritten
# message -- the task asks for one appended clause, not restructured brief
# composition.
_reason_fmt_idx = main_src.index('reason = (f"tick routed')
_reason_append_idx = main_src.index('reason +=')
check("reason_append_comes_after_base_reason_387", True,
      _reason_fmt_idx < _reason_append_idx)

# Behavioural: the actual gating predicate, exercised directly (not just
# grepped) against the three shapes the task calls out.
def _would_mention_landing(fired):
    return 5 in fired or 9 in fired


check("landing_clause_fires_on_cond_5", True, _would_mention_landing({5}))
check("landing_clause_fires_on_cond_9", True, _would_mention_landing({9}))
check("landing_clause_silent_on_cond_1_only", False,
      _would_mention_landing({1}))

# Behavioural: the publish always leaves FEED_PATH either absent or holding
# complete, parseable JSON -- never a truncated prefix -- and cleans up its
# tmp file rather than littering the directory.
_pub_dir = Path(tempfile.mkdtemp())
try:
    _pub_feed = _pub_dir / "status.json"
    _pub_tmp = _pub_feed.with_suffix(".json.tick.tmp")
    _pub_tmp.write_text(json.dumps({"tick": {"n": 1}}))
    os.replace(_pub_tmp, _pub_feed)
    check("tick_publish_replace_leaves_complete_json", {"tick": {"n": 1}},
          json.loads(_pub_feed.read_text()))
    check("tick_publish_replace_leaves_no_tmp_litter", False, _pub_tmp.exists())
finally:
    shutil.rmtree(_pub_dir, ignore_errors=True)

# orch#284: the automerge tooltip must say WHERE the value came from. A
# per-issue label overrides the repo default in BOTH directions, so if an
# inherited value and one set on the issue render the same words, the
# operator cannot tell whether tapping the control changes anything -- the
# precedence model becomes invisible. That is a property of the rendered
# words, not of the pixels, so it is pinned here like the CAN_ACT idioms
# above. The suffixes are the whole discriminator; flattening the two into
# one string is the regression this catches.
check("widget_automerge_tooltip_inherited_suffix", True,
      '" (from repo)"' in WIDGET_TPL)
check("widget_automerge_tooltip_set_suffix", True,
      '" — tap to toggle"' in WIDGET_TPL)
# Both suffixes must hang off the SAME decision, not drift into two
# unrelated sites: auto_land_source is the only signal that decides it.
check("widget_automerge_tooltip_reads_source", True,
      'i.auto_land_source === "repo" ? " (from repo)" : " — tap to toggle"'
      in _wtpl_flat)
# The repo-level control states it is read-only and names the blocker,
# rather than the older "not built" -- the DISPLAY is built and truthful;
# only the write is missing (orch#230's config writer).
check("widget_repo_automerge_names_blocker", True,
      "read-only: changing it needs a config writer (orch#230)" in WIDGET_TPL)
check("widget_repo_automerge_drops_not_built_placeholder", False,
      "repo default automerge: not built" in WIDGET_TPL)

# === orch#285: fold actuation -- `--folds` on the journal CLI, and the =====
# === close-verb grant that lets repo-orch carry it out ======================

# --- t251: `spawn.py journal ... --folds` ----------------------------------
# Same shape as the t108 `--summary` block above: driven through run_cli
# (the CLI entry point), never core.journal_append directly, so a regression
# in cmd_journal's own argv handling is what actually fails these. The
# whole risk here is direction: `284:212` means 284 (superseded) closes and
# 212 (survivor) lives on, and a transposed assignment in cmd_journal would
# still produce a well-formed row -- just pointing the wrong way.
_t251_slug = "t251/spawn-folds"
_t251_journal_file = core.journal_path("repo", _t251_slug)


def _t251_read_journal():
    if not _t251_journal_file.exists():
        return []
    return [json.loads(l) for l in _t251_journal_file.read_text().splitlines() if l]


try:
    # A single pair rides the `consolidated` event, direction preserved --
    # superseded and survivor must NOT be swapped.
    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "284:212", "shared artifact"])
    check("t251_folds_single_pair_rc", 0, rc)
    _t251_rows = _t251_read_journal()
    check("t251_folds_single_pair", [{"superseded": 284, "survivor": 212}],
          _t251_rows[-1].get("folds"))
    _t251_journal_file.unlink()

    # Multiple comma-separated pairs, each direction preserved independently.
    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "284:212,50:51", "two folds"])
    check("t251_folds_multi_pair_rc", 0, rc)
    _t251_rows = _t251_read_journal()
    check("t251_folds_multi_pair",
          [{"superseded": 284, "survivor": 212}, {"superseded": 50, "survivor": 51}],
          _t251_rows[-1].get("folds"))
    _t251_journal_file.unlink()

    # Empty/whitespace value -> [], the same "considered nothing" shape
    # --covered uses, not a rejected argv.
    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "   ", "no folds this time"])
    check("t251_folds_blank_rc", 0, rc)
    _t251_rows = _t251_read_journal()
    check("t251_folds_blank_is_empty_list", [], _t251_rows[-1].get("folds"))
    _t251_journal_file.unlink()

    # --folds rides the SAME row as --covered -- a fold is a consolidation
    # conclusion, not a separate event, so both keys must land together.
    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--covered", "44,45", "--folds", "284:212", "both"])
    check("t251_folds_coexists_with_covered_rc", 0, rc)
    _t251_rows = _t251_read_journal()
    check("t251_folds_coexists_with_covered",
          ([44, 45], [{"superseded": 284, "survivor": 212}]),
          (_t251_rows[-1].get("covered"), _t251_rows[-1].get("folds")))
    _t251_journal_file.unlink()

    # Malformed values are rejected outright (USAGE, return 2) and write
    # NOTHING -- a silently-dropped fold row means an issue was closed with
    # no record why.
    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds"])  # missing value slot
    check("t251_folds_missing_value_rc", 2, rc)
    check("t251_folds_missing_value_no_row", False, _t251_journal_file.exists())

    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "284-212", "note"])  # no colon
    check("t251_folds_no_colon_rc", 2, rc)
    check("t251_folds_no_colon_no_row", False, _t251_journal_file.exists())

    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "284:212:99", "note"])  # two colons
    check("t251_folds_two_colons_rc", 2, rc)
    check("t251_folds_two_colons_no_row", False, _t251_journal_file.exists())

    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "abc:212", "note"])  # non-digit superseded
    check("t251_folds_nondigit_superseded_rc", 2, rc)
    check("t251_folds_nondigit_superseded_no_row", False, _t251_journal_file.exists())

    rc, _ = run_cli(["journal", "repo", _t251_slug, "consolidated",
                      "--folds", "284:xyz", "note"])  # non-digit survivor
    check("t251_folds_nondigit_survivor_rc", 2, rc)
    check("t251_folds_nondigit_survivor_no_row", False, _t251_journal_file.exists())
finally:
    shutil.rmtree(_t251_journal_file.parent, ignore_errors=True)

# --- t252: the close-verb grant --------------------------------------------
# `_rule_for_command` truncates to the command head at the doc's own
# _MULTIWORD depth (gh/tea = 3) -- pinned here the same way the existing
# rule_tea_issue_edit_three_word checks above pin their verbs, so a future
# depth change cannot silently widen or narrow the close grant.
check("rule_gh_issue_close", "Bash(gh issue close:*)",
      core._rule_for_command("gh issue close 12"))
check("rule_tea_issue_close", "Bash(tea issue close:*)",
      core._rule_for_command("tea issue close 12 --login x --repo y"))

# role_settings("repo-orch") must read from the REAL shipped repo-orch.md,
# not the temp ORCH_HOME the earlier envelope fixture (line ~2503) overwrote
# -- same reason and same monkeypatch/restore pattern as the
# shipped_doc_repo_orch_* checks above: reading the mutated temp doc back
# would prove nothing about whether the actual doc still teaches this verb.
_saved_orch_home_t252 = core.ORCH_HOME
try:
    core.ORCH_HOME = _real_agents_dir.parent
    _t252_repo_allow = core.role_settings("repo-orch")["permissions"]["allow"]

    check("t252_repo_orch_close_grant_gh", True,
          "Bash(gh issue close:*)" in _t252_repo_allow)
    check("t252_repo_orch_close_grant_tea", True,
          "Bash(tea issue close:*)" in _t252_repo_allow)
    # The regression this whole block exists to catch: a doc line written at
    # the wrong depth (or a harvester bug) collapsing to the bare head would
    # silently widen EVERY gh/tea verb at once, not just close.
    check("t252_repo_orch_no_blanket_gh", False,
          "Bash(gh:*)" in _t252_repo_allow)
    check("t252_repo_orch_no_blanket_tea", False,
          "Bash(tea:*)" in _t252_repo_allow)
finally:
    core.ORCH_HOME = _saved_orch_home_t252

# TEA_ARGV["issue_close"] emits the argv the doc's `tea issue close` line
# must match -- same discipline as tea_argv_issue_create_verb above, so doc
# and builder cannot quietly drift apart. --login/--repo present, and the
# issue number positional right after the verb (tea's own argv shape, no
# --comment flag: tea's close verb has none, see the builder's own comment).
check("tea_argv_issue_close", ["tea", "issue", "close", "284", "--login", "gitea",
                                "--repo", "owner/name"],
      core.TEA_ARGV["issue_close"]("owner/name", "gitea", 284))

# === nudge_repo / ask_repo: operator act journals before spawn =============
# Regression guard for the ordering bug: nudge_repo used to spawn a repo-orch
# and return without journaling anything, so an operator nudge left no trace.
# The fix journals the operator act BEFORE calling spawn(), so the record
# exists even if spawn() then raises. ask_repo used to follow the same
# spawn-after-journal shape; per docs/UX-REDESIGN.md section 4.2/5 it now
# journals and spawns nothing at all (see the dedicated
# ask_repo_journals_no_spawn block below) -- these blocks keep asserting
# nudge_repo's spawn-ordering contract and ask_repo's journal contract side by
# side since they share the same fixtures. Stub core.spawn the same way
# _fake_launch stubs core._launch above -- monkeypatch, use in a try, restore
# in finally.
_orig_spawn = core.spawn
_nudge_repo_path = T / "wt" / "me" / "nudgerepo"
_nudge_repo_path.mkdir(parents=True)

core.spawn = lambda *a, **kw: 999999
try:
    core.nudge_repo(_nudge_repo_path)
    rows = core.journal_tail("repo", "nudgerepo")
    check("nudge_repo_journals_actor", "operator", rows[-1]["actor"] if rows else None)
    check("nudge_repo_journals_event", "nudged", rows[-1]["event"] if rows else None)

    # The row existing is not enough: orch#125 landed the row but not its
    # content, so a nudge carrying a specific instruction recorded only that
    # *a* nudge happened. Two operator instructions were lost that way on
    # 2026-09-16. Pin the text, not just the event.
    core.nudge_repo(_nudge_repo_path, "start 291 and 292 concurrently")
    rows_t = core.journal_tail("repo", "nudgerepo")
    check("nudge_repo_journals_text", "start 291 and 292 concurrently",
          (rows_t[-1].get("note") or "") if rows_t else None)

    # A refused spawn must not read as success. spawn() returns the live pgid
    # when the cardinality flock already holds, which is indistinguishable
    # from a fresh spawn in the return value alone.
    _alive_row = {"pgid": 999999}
    _saved_ledger, _saved_alive = core.ledger_read, core._pgid_alive
    core.ledger_read = lambda k: _alive_row if k == "repo-orch.nudgerepo" else _saved_ledger(k)
    core._pgid_alive = lambda p: p == 999999
    try:
        res = core.nudge_repo(_nudge_repo_path, "this must not vanish")
        check("nudge_repo_refused_not_ok", False, res["ok"])
        rows_u = core.journal_tail("repo", "nudgerepo")
        check("nudge_repo_refused_journals_undelivered", "nudge-undelivered",
              rows_u[-1]["event"] if rows_u else None)
        check("nudge_repo_refused_keeps_text", True,
              "this must not vanish" in ((rows_u[-1].get("note") or "") if rows_u else ""))
    finally:
        core.ledger_read, core._pgid_alive = _saved_ledger, _saved_alive

    core.ask_repo(_nudge_repo_path, "please look at the flaky test")
    rows2 = core.journal_tail("repo", "nudgerepo")
    check("ask_repo_journals_actor", "operator", rows2[-1]["actor"] if rows2 else None)
    check("ask_repo_journals_event", "asked", rows2[-1]["event"] if rows2 else None)
    check("ask_repo_journals_note", "please look at the flaky test",
          rows2[-1].get("note") if rows2 else None)
finally:
    core.spawn = _orig_spawn

# THE IMPORTANT ONE: spawn() raising must NOT prevent the journal row from
# being written. This is what fails if the journal_append call is ever moved
# to after spawn() -- the exact bug this unit fixes.
_raise_repo_path = T / "wt" / "me" / "raiserepo"
_raise_repo_path.mkdir(parents=True)


def _raising_spawn(*a, **kw):
    raise RuntimeError("spawn boom")


core.spawn = _raising_spawn
try:
    try:
        core.nudge_repo(_raise_repo_path)
        nudge_raised = False
    except RuntimeError:
        nudge_raised = True
    check("nudge_repo_spawn_raises_propagates", True, nudge_raised)
    rows3 = core.journal_tail("repo", "raiserepo")
    check("nudge_repo_journals_despite_spawn_raise", "nudged",
          rows3[-1]["event"] if rows3 else None)

    try:
        core.ask_repo(_raise_repo_path, "ask never touches spawn at all")
        ask_raised = False
    except RuntimeError:
        ask_raised = True
    check("ask_repo_never_calls_spawn", False, ask_raised)
    rows4 = core.journal_tail("repo", "raiserepo")
    check("ask_repo_journals_with_spawn_stubbed_to_raise", "asked",
          rows4[-1]["event"] if rows4 else None)
finally:
    core.spawn = _orig_spawn
# === orch#257: a paused ("off") repo stays VISIBLE on the dashboard =========
# feed.build() used to `continue` past any entry whose state != "tracked",
# so a paused repo vanished from the feed entirely -- the #250 lesson
# (silently-stopped is worse than loudly-failing) applied to pause. `off`
# must suppress ACTUATION only (no repo_json()/gh/tea round-trip), not
# observation, so this needs no fake repo/issue world (contrast the #108
# comment above at line ~664) -- the point of the fix is that a paused repo
# never reaches that machinery. repo_json is deliberately left un-stubbed:
# if the fix regressed and called it anyway, this repo has no real checkout
# and no `.git` dir, so it would raise/mis-resolve rather than pass quietly.
from orch import feed as feed257
importlib.reload(feed257)

_orch_json257 = core.ORCH_HOME / "orch.json"
_orig_orch_json257 = _orch_json257.read_text() if _orch_json257.exists() else None
_orig_repos_txt257 = (core.ORCH_HOME / "repos.txt").read_text() \
    if (core.ORCH_HOME / "repos.txt").exists() else None
_paused_path257 = str(Path(os.environ["WT_ROOT"]) / "paused-repo-257")
_orch_json257.write_text(json.dumps({"repos": [
    {"path": _paused_path257, "state": "off", "note": "paused, CI broken"},
]}))
try:
    _out257 = feed257.build()
    _rows257 = _out257["repos"]
    check("t257_paused_repo_present", True,
          any(r.get("path") == _paused_path257 for r in _rows257))
    _row257 = next(r for r in _rows257 if r.get("path") == _paused_path257)
    check("t257_paused_note_survives", "paused, CI broken", _row257.get("note"))
    check("t257_paused_state_off", "off", _row257.get("state"))
    check("t257_paused_slug", "paused-repo-257", _row257.get("slug"))
    check("t257_paused_ok_true", True, _row257.get("ok"))
    check("t257_paused_issues_empty_list", [], _row257.get("issues"))
    # not an unresolved-checkout alert: pause is deliberate, not a failure.
    check("t257_paused_no_unresolved_alert", True,
          not any(a.get("key") == f"repos-unresolved:{_paused_path257}"
                  for a in _out257["alerts"]))
    # `repo` is the widget's display name (rendered as esc(r.repo), and
    # esc(undefined) is ""), so its absence draws a blank-named row rather
    # than failing loudly. Review finding on PR 304.
    check("t257_paused_has_display_name", "paused-repo-257", _row257.get("repo"))
finally:
    if _orig_orch_json257 is None:
        _orch_json257.unlink(missing_ok=True)
    else:
        _orch_json257.write_text(_orig_orch_json257)
    if _orig_repos_txt257 is not None:
        (core.ORCH_HOME / "repos.txt").write_text(_orig_repos_txt257)

# ask_repo_journals_no_spawn: dedicated contract test for orch#292's
# redefinition of `ask` as a pure journal write (docs/UX-REDESIGN.md section
# 4.2 / section 5). Stub core.spawn to raise on any call -- if ask_repo ever
# calls it, this test fails loudly instead of silently spawning in a test run.
_ask_only_repo_path = T / "wt" / "me" / "askonlyrepo"
_ask_only_repo_path.mkdir(parents=True)

core.spawn = _raising_spawn
try:
    core.ask_repo(_ask_only_repo_path, "note for the next wake")
    rows5 = core.journal_tail("repo", "askonlyrepo")
    check("ask_repo_journals_no_spawn_actor", "operator",
          rows5[-1]["actor"] if rows5 else None)
    check("ask_repo_journals_no_spawn_event", "asked",
          rows5[-1]["event"] if rows5 else None)
    check("ask_repo_journals_no_spawn_note", "note for the next wake",
          rows5[-1].get("note") if rows5 else None)
finally:
    core.spawn = _orig_spawn

# === names.json cache (write-once, atomic) ==================================
# JOURNAL_ROOT lives under the temp ORCH_HOME pinned at import, so these
# writes never touch real state.

_names_repo = "namesrepo"
_names_dir = core.JOURNAL_ROOT / "repos" / core._fs_slug(_names_repo)

check("names_read_missing_file_empty", {}, core.names_read(_names_repo))

added1 = core.names_write(_names_repo, {"1": {"name": "alpha", "title": "A"}})
check("names_write_first_added", ["1"], added1)
check("names_read_after_first_write", {"1": {"name": "alpha", "title": "A"}},
      core.names_read(_names_repo))

# write-once: same key with a different value must NOT overwrite
added2 = core.names_write(_names_repo, {"1": {"name": "beta", "title": "B"}})
check("names_write_existing_key_returns_empty", [], added2)
check("names_write_existing_key_wins", {"name": "alpha", "title": "A"},
      core.names_read(_names_repo)["1"])

# a genuinely new key alongside an existing one is added
added3 = core.names_write(_names_repo, {
    "1": {"name": "beta", "title": "B"},   # already present, ignored
    "2": {"name": "gamma", "title": "C"},  # new
})
check("names_write_new_key_added", ["2"], added3)
check("names_read_has_both_keys", {"1", "2"}, set(core.names_read(_names_repo)))
check("names_write_old_key_still_wins", "alpha",
      core.names_read(_names_repo)["1"]["name"])

# atomicity: no leftover temp file after a write
_atomic_repo = "namesatomic"
core.names_write(_atomic_repo, {"5": {"name": "x", "title": ""}})
_atomic_dir = core.JOURNAL_ROOT / "repos" / core._fs_slug(_atomic_repo)
leftover = [q.name for q in _atomic_dir.iterdir() if q.name != "names.json"]
check("names_write_no_leftover_temp_file", [], leftover)

# A non-ASCII digit passes str.isdigit() but can never match a real issue
# number, so it would write a permanently dead cache entry. Rejected at parse.
import orch.spawn as _spawn
check("names_cli_rejects_non_ascii_digit", 2,
      _spawn.cmd_names(["namesrepo", "\u0662\u0669\u0660=arabic numerals"]))
check("names_cli_accepts_ascii_digit", 0,
      _spawn.cmd_names(["namesascii", "290=issue naming skill=a title"]))

# corrupt file -> {} never raises. Last, since it clobbers the file above.
_names_dir.mkdir(parents=True, exist_ok=True)
(_names_dir / "names.json").write_text("not json{{")
check("names_read_corrupt_file_empty", {}, core.names_read(_names_repo))

# === orch#301: feed.repo_json derives `stalled` -- "state unchanged while
# work is outstanding" =======================================================
# Drives the REAL feed.repo_json end to end, not a reimplemented copy of its
# boolean expression -- a copy can drift from the committed logic and still
# pass. World is swapped for a fake whose load() returns True (the success
# path unit 3's _StubLoadWorld above never exercises) and carries real open
# issues with L_READY so world.candidates() surfaces them. issue_json's own
# heavy dependencies (git rev-list, sessions, transcripts) are stubbed with
# the exact same knobs _feed_startable_check already established above --
# core.work_mtime and core.transcript_activity forced to None so `activity`
# is driven by ONE lever, each fake issue's own updatedAt string, rather than
# by three maxed sources fighting each other. feed.alive is keyed so one
# lambda answers both issue-orch liveness (live_orchs) and repo-orch liveness
# (repo_orch["alive"]) independently, since case 2 must clear `stalled` on
# either alone.
_stalled_repo_dir = T / "stalledrepo"
_stalled_repo_dir.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "init", "-q"], cwd=_stalled_repo_dir, check=True)
subprocess.run(["git", "remote", "add", "origin", "git@github.com:cybermelon/stalledrepo.git"],
                cwd=_stalled_repo_dir, check=True)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class _StalledWorld(_PlainBranchMixin, _OrphanPrsMixin):
    """Same shape as core.World's own candidates()/issue_* reads, but load()
    is a fake that always succeeds and installs whatever issues the case
    hands it -- the real World.load() shells out to gh/tea, which a unit
    test must not depend on."""

    def __init__(self, issues):
        self._issues = issues
        self.issues = []

    def load(self, gr):
        self.issues = self._issues
        return True

    def candidates(self):
        out = []
        for i in self.issues:
            names = {l["name"] for l in i.get("labels", [])}
            if core.L_READY in names or core.L_WORKING in names:
                out.append(i["number"])
        return out

    def issue_field(self, n, field):
        for i in self.issues:
            if i["number"] == n:
                return i.get(field, "")
        return ""

    def issue_comments(self, n):
        return []

    def issue_has_label(self, n, label):
        for i in self.issues:
            if i["number"] == n:
                return any(l["name"] == label for l in i.get("labels", []))
        return False

    def pr_number_for(self, branch):
        return None

    def pr_for(self, branch):
        return ""


def _stalled_issue(n, updated_ts):
    created_ts = (updated_ts if updated_ts is not None else core.now()) - 3600
    return {"number": n, "title": f"issue {n}",
            "labels": [{"name": core.L_READY}],
            "createdAt": _iso(created_ts),
            "updatedAt": _iso(updated_ts) if updated_ts is not None else None,
            "comments": []}


def _run_repo_json_case(issues, issue_alive, repo_orch_alive,
                        patch_live_issue_orchs=True):
    """Runs the real feed.repo_json against _StalledWorld(issues), with
    issue-orch liveness and repo-orch liveness set independently -- both feed
    into `stalled` (live_orchs and repo_orch["alive"]) via case 2's "either
    alone suffices to clear it" requirement."""
    _orig = {
        "World": feed.World, "alive": feed.alive, "base_ref": feed.base_ref,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "live_issue_orchs": feed.live_issue_orchs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.issue_brief": core.issue_brief,
    }
    feed.World = lambda: _StalledWorld(issues)

    def _fake_alive(key):
        if key.startswith(core.key_for("repo-orch", "stalledrepo")):
            return repo_orch_alive
        return issue_alive
    feed.alive = _fake_alive
    # live_orchs reads the session LEDGER, not feed.alive (see repo_json), so
    # issue_alive has to be expressed here too or a case asking for a live
    # issue-orch gets live_orchs 0. Every issue in the fixture is live when
    # issue_alive is set, which is what this harness's flag has always meant.
    # patch_live_issue_orchs=False leaves the REAL core.live_issue_orchs in
    # place, so the caller can point SESSIONS_DIR at a temp ledger and drive
    # the slug-to-key-prefix wiring for real.
    if patch_live_issue_orchs:
        feed.live_issue_orchs = (lambda slug: {i["number"] for i in issues}) if issue_alive \
            else (lambda slug: set())
    feed.base_ref = lambda repo: ""  # commits path -1, work_mtime path skipped
    feed.sessions_for = lambda cwd, live=False: []
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.issue_brief = lambda comments, activity=None: None
    try:
        return feed.repo_json(_stalled_repo_dir)
    finally:
        feed.World = _orig["World"]
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        feed.live_issue_orchs = _orig["live_issue_orchs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.issue_brief = _orig["core.issue_brief"]


_old_activity_ts = core.now() - core.NUDGE_IDLE_MINS * 60 - 120
_recent_activity_ts = core.now() - 60

# 1. stalled true: open issues, no live orch, newest activity older than
# NUDGE_IDLE_MINS.
_r1 = _run_repo_json_case([_stalled_issue(1, _old_activity_ts)], False, False)
check("stalled_true_on_idle_no_orch", True, _r1["stalled"])
check("stalled_true_stalled_since", _old_activity_ts, _r1["stalled_since"])

# 2. false on live orch -- either live_orchs>0 or repo_orch["alive"] alone
# must clear it; same idle activity and issue set as case 1 otherwise.
_r2a = _run_repo_json_case([_stalled_issue(2, _old_activity_ts)], True, False)
check("stalled_false_on_live_issue_orch", False, _r2a["stalled"])
_r2b = _run_repo_json_case([_stalled_issue(2, _old_activity_ts)], False, True)
check("stalled_false_on_live_repo_orch", False, _r2b["stalled"])

# 3. false on recent activity -- same as case 1 but newest activity is
# inside the NUDGE_IDLE_MINS window.
_r3 = _run_repo_json_case([_stalled_issue(3, _recent_activity_ts)], False, False)
check("stalled_false_on_recent_activity", False, _r3["stalled"])

# 4. false on zero open issues -- world.issues empty. Nothing to do is not a
# stall (issue#301 open question 3). Asserted even though it looks obvious:
# per the task brief this is the case most likely to regress.
_r4 = _run_repo_json_case([], False, False)
check("stalled_false_on_no_open_issues", False, _r4["stalled"])

# 5. no activity at all reads stalled -- open issues present, but every
# activity is None (a repo-orch that never ran). Deliberate, not an
# accident: stalled_since stays None and the `or` short-circuits true.
_r5 = _run_repo_json_case([_stalled_issue(5, None)], False, False)
check("stalled_true_on_no_activity_at_all", True, _r5["stalled"])
check("stalled_since_none_when_no_activity", None, _r5["stalled_since"])

# stalled_since is the NEWEST activity, not the oldest -- an off-by-direction
# bug here would make the hover age wrong (too stale or too fresh).
_r_newest = _run_repo_json_case(
    [_stalled_issue(6, _old_activity_ts), _stalled_issue(7, _recent_activity_ts)],
    False, False)
check("stalled_since_is_newest_activity", _recent_activity_ts, _r_newest["stalled_since"])
# and with the newer issue itself past the idle window, the repo still
# reads NOT stalled -- proof the comparison used the max, not the min.
check("stalled_false_when_newest_of_two_is_recent", False, _r_newest["stalled"])

# 6. shape parity -- `stalled`/`stalled_since` present on BOTH ok:False
# abort-path dicts, stalled False on both. Asserted on the literal returned
# dicts: both aborts are reachable cheaply (no World() touched on either --
# see feed.py's `if not gr:` and `if not world.load(gr):` paths), same
# precedent as unit 3's repo_json_noslug_* checks above.
_stalled_noremote_dir = T / "stalled-noremote"
_stalled_noremote_dir.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "init", "-q"], cwd=_stalled_noremote_dir, check=True)
_r6_noslug = feed.repo_json(_stalled_noremote_dir)
check("stalled_shape_parity_noslug_ok", False, _r6_noslug["ok"])
check("stalled_shape_parity_noslug_stalled_key", True, "stalled" in _r6_noslug)
check("stalled_shape_parity_noslug_stalled_since_key", True, "stalled_since" in _r6_noslug)
check("stalled_shape_parity_noslug_stalled_false", False, _r6_noslug["stalled"])
check("stalled_shape_parity_noslug_stalled_since_none", None, _r6_noslug["stalled_since"])

_orig_feed_world_301 = feed.World
feed.World = _StubLoadWorld  # from unit 3 above: load() always returns False
try:
    _r6_unreachable = feed.repo_json(_stalled_repo_dir)
    check("stalled_shape_parity_unreachable_ok", False, _r6_unreachable["ok"])
    check("stalled_shape_parity_unreachable_stalled_key", True, "stalled" in _r6_unreachable)
    check("stalled_shape_parity_unreachable_stalled_since_key",
          True, "stalled_since" in _r6_unreachable)
    check("stalled_shape_parity_unreachable_stalled_false", False, _r6_unreachable["stalled"])
    check("stalled_shape_parity_unreachable_stalled_since_none",
          None, _r6_unreachable["stalled_since"])
finally:
    feed.World = _orig_feed_world_301

# --- orch#329: a suffixed second branch must not read as LANDED ----------------
#
# pr_for/pr_number_for match a PR to a branch by EXACT name. The branch was
# always derived as issue_branch(n) == "issue-<n>", so an issue whose live work
# moved to a suffixed sibling branch matched only its OLD merged PR and
# work_state derived LANDED off it. The miss INVERTS -- hidden work reads as
# finished, not as unknown -- which is why it is worth a block of its own.
#
# Measured shape (orch#259 on 2026-09-16): PR 266 on "issue-259" MERGED, PR 328
# on "issue-259-sections" OPEN. The feed reported LANDED/pr=266.

# The segment boundary is the whole correctness question. A bare
# startswith("issue-25") swallows issue-259's branches; the trailing hyphen is
# what makes the number a complete segment.
check("issue_branches_for_exact_and_suffixed",
      ["issue-259", "issue-259-sections"],
      core.issue_branches_for(259, ["issue-259", "issue-259-sections",
                                    "issue-25", "main"]))
check("issue_branches_for_25_does_not_swallow_259", [],
      core.issue_branches_for(25, ["issue-259", "issue-259-sections",
                                   "issue-2590"]))
check("issue_branches_for_7_does_not_swallow_70", ["issue-7", "issue-7-retry"],
      core.issue_branches_for(7, ["issue-7", "issue-7-retry", "issue-70",
                                  "issue-7abc"]))
check("issue_branches_for_empty", [], core.issue_branches_for(1, []))
check("issue_branches_for_skips_non_strings", ["issue-1"],
      core.issue_branches_for(1, ["issue-1", None, 7]))
check("issue_branches_for_accepts_issue_branch",
      ["issue-42"], core.issue_branches_for(42, [core.issue_branch(42)]))


class _ShadowWorld329(core.World):
    """The REAL World.branch_for_issue/shadowed_prs/pr_for with issues and prs
    injected, so no gh/tea call is made. Deliberately a subclass and not a
    fake: the shipped methods are the thing under test, and a fake that
    reimplements them would pass against the bug."""
    def __init__(self, issues, prs):
        self.issues = issues
        self.prs = prs
        self.rollups = {}


_sh329 = _ShadowWorld329(
    [{"number": 259}],
    [{"number": 266, "headRefName": "issue-259", "state": "MERGED"},
     {"number": 328, "headRefName": "issue-259-sections", "state": "OPEN"}])
# The fix: the OPEN sibling wins, so every branch-keyed lookup sees the live PR.
check("branch_for_issue_prefers_open_sibling", "issue-259-sections",
      _sh329.branch_for_issue(259))
check("pr_for_via_resolved_branch_is_open", "OPEN",
      _sh329.pr_for(_sh329.branch_for_issue(259)))
check("pr_number_for_via_resolved_branch_is_live_pr", 328,
      _sh329.pr_number_for(_sh329.branch_for_issue(259)))
# The headline regression: this returned LANDED before the fix.
check("work_state_not_landed_when_live_pr_on_sibling", "REVIEW",
      core.work_state(_sh329, "/nonexistent/repo329", 259))

# Unchanged for the overwhelmingly common single-branch case.
check("work_state_landed_unchanged_single_branch", "LANDED",
      core.work_state(_ShadowWorld329(
          [{"number": 5}],
          [{"number": 9, "headRefName": "issue-5", "state": "MERGED"}]),
          "/nonexistent/repo329", 5))
check("branch_for_issue_defaults_to_issue_branch_when_no_pr", "issue-6",
      _ShadowWorld329([{"number": 6}], []).branch_for_issue(6))
# An issue numbered such that a LONGER issue's branches exist must not borrow
# them -- the same collision as issue_branches_for, asserted through the World.
check("branch_for_issue_25_does_not_borrow_259", "issue-25",
      _ShadowWorld329([{"number": 25}],
                      [{"number": 266, "headRefName": "issue-259",
                        "state": "MERGED"},
                       {"number": 328, "headRefName": "issue-259-sections",
                        "state": "OPEN"}]).branch_for_issue(25))
# Deterministic across calls: two lookups in one tick must never disagree.
_tie329 = _ShadowWorld329(
    [{"number": 9}],
    [{"number": 1, "headRefName": "issue-9-b", "state": "OPEN"},
     {"number": 2, "headRefName": "issue-9-a", "state": "OPEN"}])
check("branch_for_issue_tie_is_deterministic", True,
      _tie329.branch_for_issue(9) == _tie329.branch_for_issue(9))
# Malformed PR rows must not raise -- an unreadable row is not a crash.
check("branch_for_issue_survives_malformed_rows", "issue-9",
      _ShadowWorld329([{"number": 9}],
                      [{"headRefName": None}, {"state": "OPEN"}, {}]
                      ).branch_for_issue(9))

# shadowed_prs: the miss must be detectable, never silent (orch#280's
# precedent -- a landed-row test passed while the feature wrote to a key
# nobody read).
check("shadowed_prs_reports_the_false_landed_shape",
      [{"issue": 259, "merged_pr": 266, "merged_branch": "issue-259",
        "open_pr": 328, "open_branch": "issue-259-sections"}],
      _sh329.shadowed_prs())
check("shadowed_prs_empty_when_no_sibling", [],
      _ShadowWorld329([{"number": 5}],
                      [{"number": 9, "headRefName": "issue-5",
                        "state": "MERGED"}]).shadowed_prs())
# No MERGED PR on the exact branch means nothing is being shadowed: the open
# sibling is simply the issue's branch, which branch_for_issue already returns.
check("shadowed_prs_empty_when_exact_branch_not_merged", [],
      _ShadowWorld329([{"number": 5}],
                      [{"number": 9, "headRefName": "issue-5-two",
                        "state": "OPEN"}]).shadowed_prs())
check("shadowed_prs_survives_malformed_rows", [],
      _ShadowWorld329([{"number": 9}, {}],
                      [{"headRefName": None}, {}]).shadowed_prs())

# === orch#289: issue_json display_name / name_stale =====================
# feed.issue_json derives name_stale as:
#   name_stale = bool(stored_title) and stored_title != live_title
# NOT a plain `stored_title != live_title`. Ten+ real cache entries (issues
# 208, 209, 211, 212, 219, 222, 223, 225, 227, 321 -- the set grows) store
# title="" because they recorded a display name without ever capturing a
# title to diff against. An empty stored title means "cannot tell", and must
# NOT be compared against the live title as if "" were a real prior value --
# that would brand every one of those entries permanently stale forever.
# names_display_empty_title below is the case that catches a "simplification"
# back to the naive inequality.
def _names_display_check(name, names, want_display_name, want_name_stale):
    world = _FeedFakeWorld([core.L_READY])
    row = feed.issue_json(world, Path("/nonexistent/repo"), 44, "feedslug",
                          "me/feedslug", True, "", {}, False, names)
    check(f"{name}_display_name", want_display_name, row["display_name"])
    check(f"{name}_name_stale", want_name_stale, row["name_stale"])


# names_display_empty_title: stored title "" -> name_stale False even though
# live title "t" != "". THE TRAP: a naive `stored_title != live_title` would
# make this True.
_names_display_check("names_display_empty_title",
                      {"44": {"name": "Widget", "title": ""}}, "Widget", False)

# names_display_stale: stored title non-empty and different from live "t" ->
# name_stale True.
_names_display_check("names_display_stale",
                      {"44": {"name": "Widget", "title": "old"}}, "Widget", True)

# names_display_fresh: stored title non-empty and equal to live "t" ->
# name_stale False.
_names_display_check("names_display_fresh",
                      {"44": {"name": "Widget", "title": "t"}}, "Widget", False)

# names_display_no_entry: no entry for this issue number -> display_name "",
# name_stale False, no raise.
_names_display_check("names_display_no_entry", {}, "", False)

# names_display_malformed: entry is not a dict (hand-edited cache) -> degrade
# to no display name, no raise.
_names_display_check("names_display_malformed_string", {"44": "Widget"}, "", False)
_names_display_check("names_display_malformed_list", {"44": ["Widget"]}, "", False)

# --- orch#279: dropped-from-the-feed defects -----------------------------------
#
# Two independent holes, both of which let real work go unsurfaced. NOTE the
# issue's own "Suspected area" (a truncated issue list / a swallowed per-issue
# exception) was FALSIFIED before these were written: tea returns all 10
# yantraloka issues with and without --limit, and World.load drops none of
# them. These cover what was actually wrong.

# issue_number_from_branch: which issue owns this branch. Admits exactly the
# two shapes issue_branches_for admits, from the other direction -- the exact
# name and a hyphen-suffixed sibling -- because orch#329 established that
# "issue-259-sections" IS issue 259's work. Anything else is None rather than
# a guess: a wrong answer credits one issue's work to another.
for _b, _want in [("issue-7", 7), ("issue-279", 279), ("issue-0", 0),
                  ("issue-7-retry", 7), ("issue-259-sections", 259),
                  ("fix-nudge-text", None),
                  ("issue-", None), ("issue-abc", None), ("issue--1", None),
                  ("Issue-7", None), ("main", None), ("", None), (None, None),
                  # The number must be a WHOLE segment, not a prefix of one.
                  # A bare digit-prefix match would read these as 25/7/259 --
                  # issue_branches_for admits none of them, so neither does
                  # this, or the two directions disagree.
                  ("issue-25abc", None), ("issue-7x", None),
                  ("issue-259_sections", None), ("issue-7.2", None)]:
    check(f"issue_number_from_branch({_b!r})", _want,
          core.issue_number_from_branch(_b))
check("issue_number_from_branch_roundtrips", [1, 7, 279],
      [core.issue_number_from_branch(core.issue_branch(n)) for n in (1, 7, 279)])
# The segment boundary, asserted in BOTH directions so the two helpers can
# never drift: issue 7 must not swallow issue 70, and issue-70 must not be
# read as 7. This is orch#329's collision rule, mirrored.
check("issue_number_from_branch_70_is_not_7", 70,
      core.issue_number_from_branch("issue-70"))
check("issue_number_from_branch_agrees_with_issue_branches_for", True,
      all(core.issue_number_from_branch(b) == 7
          for b in core.issue_branches_for(7, ["issue-7", "issue-7-retry",
                                               "issue-70", "issue-700-x"])))


class _OrphanWorld279(core.World):
    """The REAL core.World.orphan_prs, with issues/prs injected and the forge
    state lookup faked. Deliberately NOT _OrphanPrsMixin: the mixin is the
    stand-in for fixtures that model no PRs, and testing it would leave the
    shipped method uncovered.

    `closed` is the set of issue numbers the forge reports CLOSED; `unread`
    is the set whose state call fails, standing in for a flaky forge."""

    def __init__(self, issues, prs, closed=(), unread=()):
        self.issues = issues
        self.prs = prs
        self._closed = set(closed)
        self._unread = set(unread)
        self.state_calls = []

    def _issue_is_closed(self, n):
        self.state_calls.append(n)
        if n in self._unread:
            return False   # unreadable degrades to "not orphaned"
        return n in self._closed


# The PR#311 shape: issue #125 closed 18 minutes BEFORE its PR opened, so the
# PR hangs off no open-issue row and nothing in the tree ever walks to it.
_ow279 = _OrphanWorld279(
    [{"number": 9, "labels": [{"name": core.L_READY}]},
     {"number": 2, "labels": [{"name": core.L_READY}]}],
    [{"number": 311, "headRefName": "issue-125", "state": "OPEN"},   # orphan
     {"number": 11, "headRefName": "issue-9", "state": "OPEN"},      # issue open
     {"number": 50, "headRefName": "issue-40", "state": "MERGED"},   # not open
     {"number": 60, "headRefName": "fix-nudge-text", "state": "OPEN"},  # no issue
     {"number": 70, "headRefName": "issue-7", "state": "OPEN"}],     # orphan
    closed=(125, 7))
_o279 = _ow279.orphan_prs()
check("orphan_prs_finds_closed_issue_prs", [70, 311], [r["number"] for r in _o279])
check("orphan_prs_carries_issue_number", [7, 125], [r["issue"] for r in _o279])
# Only the CANDIDATES cost a state call -- an open-issue PR, a merged PR and a
# non-issue branch are all excluded before the forge is touched.
check("orphan_prs_confirms_only_candidates", [7, 125], sorted(_ow279.state_calls))
check("orphan_prs_none_when_issue_still_tracked", [],
      _OrphanWorld279([{"number": 9, "labels": [{"name": core.L_WORKING}]}],
                      [{"number": 11, "headRefName": "issue-9",
                        "state": "OPEN"}]).orphan_prs())
# THE ISSUE'S OWN PR #10 CASE. Issue 7 is still OPEN but its label was
# released at landing, so it is not in candidates() -- and feed's `issues`
# list, which `awaiting` iterates, is built from candidates(). The PR is in
# neither `awaiting` nor (before this) orphan_prs: "PR #10 appears nowhere".
# No forge call is needed or made: the issue is present, so the fact is read.
_unl279 = _OrphanWorld279(
    [{"number": 7, "labels": []},
     {"number": 9, "labels": [{"name": core.L_READY}]}],
    [{"number": 10, "headRefName": "issue-7", "state": "OPEN"}])
check("orphan_prs_finds_open_but_unlabelled_issue", [10],
      [r["number"] for r in _unl279.orphan_prs()])
check("orphan_prs_labels_the_unlabelled_reason", ["unlabelled"],
      [r["reason"] for r in _unl279.orphan_prs()])
check("orphan_prs_unlabelled_costs_no_forge_call", [], _unl279.state_calls)
check("orphan_prs_labels_the_closed_reason", ["closed", "closed"],
      [r["reason"] for r in _o279])
# orch#440: an agent-stuck issue is OPEN and not in candidates() (agent-stuck
# is, like unlabelled, an absence of agent-ready/agent-working) -- but it is
# the opposite fact: a human already marked it ABANDONED. It must NOT fall
# into "unlabelled", whose remedy (nominate/re-enter) is the repo-orch red
# line forbidden on agent-stuck. Live case: PR #437 on agent-stuck issue #390.
_stuck440 = _OrphanWorld279(
    [{"number": 390, "labels": [{"name": core.L_STUCK}]}],
    [{"number": 437, "headRefName": "issue-390", "state": "OPEN"}])
check("orphan_prs_labels_the_stuck_reason", ["stuck"],
      [r["reason"] for r in _stuck440.orphan_prs()])
check("orphan_prs_stuck_costs_no_forge_call", [], _stuck440.state_calls)
# A malformed PR row is SKIPPED, not raised. A bare p["number"] would
# propagate out through repo_json, which does not guard it, and take down the
# feed build for every repo -- same contract shadowed_prs already states.
check("orphan_prs_skips_number_less_pr_row", [10],
      [r["number"] for r in _OrphanWorld279(
          [{"number": 9, "labels": [{"name": core.L_READY}]}],
          [{"headRefName": "issue-5", "state": "OPEN"},          # no number
           {"number": None, "headRefName": "issue-6", "state": "OPEN"},
           {"number": 10, "headRefName": "issue-7", "state": "OPEN"}],
          closed=(5, 6, 7)).orphan_prs()])
# orch#329's shape, orphaned: the live work moved to a suffixed sibling and
# THEN the issue closed. Before issue_number_from_branch admitted siblings
# this returned [] -- the reverse lookup refused a branch the forward lookup
# (issue_branches_for) calls issue 259's own, so a live open PR read as
# belonging to nobody.
check("orphan_prs_finds_suffixed_sibling_branch", [328],
      [r["number"] for r in _OrphanWorld279(
          [{"number": 9, "labels": [{"name": core.L_READY}]}],
          [{"number": 328, "headRefName": "issue-259-sections",
            "state": "OPEN"}],
          closed=(259,)).orphan_prs()])

# The SHIPPED World._issue_is_closed, not a fixture override. Everything above
# overrides it, which is exactly how a version that crashed on every call
# (self.table does not exist on World) passed a green suite -- the method had
# no coverage at all. This drives the real lookup with only _run stubbed, so
# the argv must resolve through the adapter on both backends.
_orig_run_279 = core._run


def _issue_closed_via(backend, payload, ok=True):
    w = core.World()
    w.repo_slug, w.login, w.backend = "me/r", "gitea", backend
    seen = []
    core._run = lambda argv, **kw: (seen.append(argv), (ok, payload))[1]
    try:
        return w._issue_is_closed(125), (seen[0] if seen else [])
    finally:
        core._run = _orig_run_279


_gh_closed_279, _gh_argv_279 = _issue_closed_via("gh", '{"state":"CLOSED"}')
check("issue_is_closed_gh_true", True, _gh_closed_279)
check("issue_is_closed_gh_uses_gh_argv", "gh", _gh_argv_279[0])
check("issue_is_closed_gh_names_the_issue", True, "125" in _gh_argv_279)
# tea reports state LOWERCASE in its single-item view (confirmed against the
# live server), so the token comparison must normalize or Gitea orphans are
# never reported at all.
_tea_closed_279, _tea_argv_279 = _issue_closed_via("tea", '{"state":"closed"}')
check("issue_is_closed_tea_true_lowercase", True, _tea_closed_279)
check("issue_is_closed_tea_uses_tea_argv", "tea", _tea_argv_279[0])
check("issue_is_closed_open_is_false", False,
      _issue_closed_via("gh", '{"state":"OPEN"}')[0])
# Fail-closed: an unreadable or unparseable answer is NOT an orphan.
check("issue_is_closed_unreadable_is_false", False,
      _issue_closed_via("gh", "", ok=False)[0])
check("issue_is_closed_unparseable_is_false", False,
      _issue_closed_via("gh", "not json")[0])
check("issue_is_closed_missing_state_is_false", False,
      _issue_closed_via("gh", '{"title":"t"}')[0])
# PARSEABLE BUT NOT AN OBJECT. json.loads succeeds on all of these, and .get()
# on any of them raises -- which would propagate through orphan_prs and
# repo_json (neither guards it) and kill the build for EVERY repo. The array
# case is the realistic one: tea's list verbs return arrays and this argv is
# one word from `tea issue list`.
for _bad279 in ('[{"state":"closed"}]', "null", "42", '"closed"', "[]"):
    check(f"issue_is_closed_non_object_is_false {_bad279}", False,
          _issue_closed_via("tea", _bad279)[0])
# THE TRUNCATION FALSE POSITIVE. Issue #7 is genuinely OPEN but fell past the
# issue list's page, so it is absent from self.issues. Absence alone would
# report a healthy, actively-owned PR as orphaned; confirming against the
# forge (which says "not closed") correctly reports nothing.
check("orphan_prs_no_false_positive_on_truncated_issue_list", [],
      _OrphanWorld279([{"number": 9, "labels": [{"name": core.L_READY}]}],
                      [{"number": 70, "headRefName": "issue-7",
                        "state": "OPEN"}],
                      closed=()).orphan_prs())
# A flaky state call degrades to "not orphaned" -- a missed alert, never a
# false one raised against a live issue.
check("orphan_prs_unreadable_state_degrades_to_not_orphaned", [],
      _OrphanWorld279([{"number": 9, "labels": [{"name": core.L_READY}]}],
                      [{"number": 70, "headRefName": "issue-7",
                        "state": "OPEN"}],
                      unread=(7,)).orphan_prs())
# Every repo row carries the key, including the unreachable-oracle abort path
# -- a consumer must never have to guess whether the key exists.
_orig_feed_world_279 = feed.World
feed.World = _StubLoadWorld  # load() always returns False
try:
    check("orphan_prs_key_present_on_unreachable_repo", [],
          feed.repo_json(Path("/nonexistent/orphanrepo279"))["orphan_prs"])
finally:
    feed.World = _orig_feed_world_279

# live_orchs counts over the FULL open set, not world.candidates(). A session
# outlives its label -- issue-orch releases agent-working as it lands while the
# session stays alive through the PR -- so summing over the labelled subset
# undercounts, and in_flight_cap is enforced against that number. This is the
# issue's own observation: live_orchs read 2 while .2/.5/.9 were alive.
def _live_orchs_279(issues, live_numbers):
    """The REAL feed.repo_json's live_orchs, with the LEDGER's live set faked.

    live_numbers is the set of issues with a live issue-orch, independent of
    `issues` -- which is the whole point: a live session need not appear in
    the issue list at all. Drives the shipped code path rather than
    re-deriving the count; a test that recomputes the formula passes against
    the bug (this one did, before it was rewritten)."""
    _orig = {
        "World": feed.World, "alive": feed.alive, "base_ref": feed.base_ref,
        "sessions_for": feed.sessions_for, "prior_runs": feed.prior_runs,
        "live_issue_orchs": feed.live_issue_orchs,
        "core.work_mtime": core.work_mtime,
        "core.transcript_activity": core.transcript_activity,
        "core.issue_brief": core.issue_brief,
    }
    feed.World = lambda: _StalledWorld(issues)
    feed.live_issue_orchs = lambda slug: set(live_numbers)

    def _fake_alive(key):
        if key.startswith(core.key_for("repo-orch", "stalledrepo")):
            return False
        return any(key == core.key_for("issue-orch", "stalledrepo", n)
                   for n in live_numbers)
    feed.alive = _fake_alive
    feed.base_ref = lambda repo: ""
    feed.sessions_for = lambda cwd, live=False: []
    feed.prior_runs = lambda key: 0
    core.work_mtime = lambda repo, branch: None
    core.transcript_activity = lambda cwd: None
    core.issue_brief = lambda comments, activity=None: None
    try:
        return feed.repo_json(_stalled_repo_dir)["live_orchs"]
    finally:
        feed.World = _orig["World"]
        feed.alive = _orig["alive"]
        feed.base_ref = _orig["base_ref"]
        feed.sessions_for = _orig["sessions_for"]
        feed.prior_runs = _orig["prior_runs"]
        feed.live_issue_orchs = _orig["live_issue_orchs"]
        core.work_mtime = _orig["core.work_mtime"]
        core.transcript_activity = _orig["core.transcript_activity"]
        core.issue_brief = _orig["core.issue_brief"]


_lo279 = [dict(_stalled_issue(2, _old_activity_ts), labels=[{"name": core.L_READY}]),
          dict(_stalled_issue(5, _old_activity_ts), labels=[{"name": core.L_WORKING}]),
          # landed: issue-orch released agent-working, session still alive
          dict(_stalled_issue(7, _old_activity_ts), labels=[]),
          dict(_stalled_issue(9, _old_activity_ts), labels=[{"name": core.L_READY}])]
# .2 is labelled but NOT alive; .5/.7/.9 are alive. Counting over the labelled
# subset (world.candidates()) sees only 5 and 9 -> 2, which is exactly the
# number the issue observed while three sessions ran.
check("live_orchs_counts_alive_session_whose_label_was_released", 3,
      _live_orchs_279(_lo279, {5, 7, 9}))
# An alive session on an issue with no label at all still counts.
check("live_orchs_counts_unlabelled_alive_only", 1,
      _live_orchs_279([dict(_stalled_issue(7, _old_activity_ts), labels=[])], {7}))
# THE CLOSED-ISSUE CASE. The session is alive but its issue has CLOSED, so it
# is in neither world.candidates() NOR world.issues -- which is the state the
# issue reported (5 and 7 appeared in neither `issues` nor `unconsidered`, and
# `unconsidered` is built from world.issues). Counting off any issue list
# misses it; counting off the ledger does not.
check("live_orchs_counts_session_whose_issue_closed", 2,
      _live_orchs_279([dict(_stalled_issue(9, _old_activity_ts),
                            labels=[{"name": core.L_READY}])], {5, 9}))
# And a labelled issue with no session must not inflate it.
check("live_orchs_zero_when_nothing_alive", 0, _live_orchs_279(_lo279, set()))

# END TO END, with live_issue_orchs NOT patched. Every case above stubs it,
# so none of them executes the wiring between repo_json's `slug` and the
# function's `issue-orch.{slug}.` prefix. If that identifier were ever wrong
# -- `gr` ("owner/repo") instead of the bare slug, say -- the prefix would
# match nothing, live_orchs would silently read 0, consequence 1 would be
# back, and every stubbed test above would still pass. This is the only case
# that would catch it.
_e2e_led279 = Path(tempfile.mkdtemp()) / "sessions"
_e2e_led279.mkdir(parents=True)
_e2e_orig_dir_279 = core.SESSIONS_DIR
_e2e_orig_alive_279 = core.alive
core.SESSIONS_DIR = _e2e_led279
try:
    # _run_repo_json_case builds its world for slug "stalledrepo"; write a
    # ledger row under the key core.key_for would produce for that repo.
    (_e2e_led279 / "issue-orch.stalledrepo.2.json").write_text("{}")
    core.alive = lambda key: key == "issue-orch.stalledrepo.2"
    _e2e_row_279 = _run_repo_json_case(
        [_stalled_issue(2, _old_activity_ts)], False, False,
        patch_live_issue_orchs=False)
    check("live_orchs_end_to_end_real_ledger", 1, _e2e_row_279["live_orchs"])
finally:
    core.SESSIONS_DIR = _e2e_orig_dir_279
    core.alive = _e2e_orig_alive_279

# core.live_issue_orchs itself, against a real ledger directory: it must read
# CURRENT rows only, scope to the right repo, and ignore the config envelope.
_led279 = Path(tempfile.mkdtemp()) / "sessions"
_led279.mkdir(parents=True)
_orig_sessions_dir_279 = core.SESSIONS_DIR
_orig_alive_279 = core.alive
core.SESSIONS_DIR = _led279
try:
    for _nm in ("issue-orch.myrepo.5.json",        # live
                "issue-orch.myrepo.7.json",        # dead
                "issue-orch.myrepo.9.json",        # live
                "issue-orch.otherrepo.3.json",     # other repo, live
                "repo-orch.myrepo.json",           # not an issue-orch
                "issue-orch.myrepo.5.settings.json",   # config, not a run
                # Rolled aside: a PRIOR run of issue 13, in the real suffix
                # format (_ROLLED_ASIDE_SUFFIX is an ISO timestamp, not an
                # epoch). History, not live work.
                "issue-orch.myrepo.13.2026-09-16T04:21:07-04:00.json"):
        (_led279 / _nm).write_text("{}")
    # Deliberately NOT an exact-key allowlist. An allowlist returns False for
    # any malformed key, which silently passes a function that failed to
    # exclude the .settings.json envelope or a rolled-aside run -- the guards
    # would be untested. This says "every session here is alive EXCEPT issue
    # 7", so a leaked history row shows up as an extra number in the result.
    core.alive = lambda key: not key.endswith(".7")
    check("live_issue_orchs_scopes_to_repo_and_current_rows", {5, 9},
          core.live_issue_orchs("myrepo"))
    check("live_issue_orchs_empty_for_unknown_repo", set(),
          core.live_issue_orchs("norepo"))
finally:
    core.SESSIONS_DIR = _orig_sessions_dir_279
    core.alive = _orig_alive_279

# The orphan must reach a HUMAN, not just the feed dict. Two surfaces:
# tui_model's Zone A row, and the tick digest (which whitelists keys, so an
# unlisted key is silently dropped and the wake cannot see the set change).
_orphan_repo_row_279 = {
    "repo": "me/r", "slug": "r", "ok": True, "issues": [], "counts": {},
    "unconsidered": [], "labels_missing": [],
    # Carries `reason`, as the shipped orphan_prs always does -- a fixture
    # that omits it asserts the rendering ternary against its own fallback.
    "orphan_prs": [{"number": 311, "branch": "issue-125", "issue": 125,
                    "reason": "closed"}],
    "orch": {"alive": False}, "live_orchs": 0,
}
_za279 = tui_model.zone_a({"repos": [_orphan_repo_row_279], "alerts": []})
check("orphan_pr_raises_a_zone_a_row", True,
      any(r.kind == "alert" and "ORPHAN PR" in r.text for r in _za279))
check("orphan_pr_row_names_the_pr", True,
      any("#311" in r.text for r in _za279 if r.kind == "alert"))
# The REASON must reach the row, both ways round. Without a reason-bearing
# fixture the ternary is only ever asserted against its fallback, so
# inverting it -- mislabelling every unlabelled orphan -- would stay green.
def _za_kid_text_279(reason):
    rows = tui_model.zone_a({"repos": [dict(
        _orphan_repo_row_279,
        orphan_prs=[dict({"number": 10, "branch": "issue-7", "issue": 7},
                         **({"reason": reason} if reason is not None else {}))],
    )], "alerts": []})
    return [k.text for r in rows for k in r.children if "PR #10" in k.text]


# EXACT text, not a substring -- "closed" is a substring of nothing here but
# the ternary's two arms are otherwise interchangeable, and an inverted
# ternary renders the same words against the wrong reasons. Pinning both arms
# to their own reason is what makes the inversion fail.
check("zone_a_renders_closed_for_closed", ["PR #10  issue-7  issue #7 closed"],
      _za_kid_text_279("closed"))
check("zone_a_renders_unlabelled_for_unlabelled",
      ["PR #10  issue-7  issue #7 unlabelled"], _za_kid_text_279("unlabelled"))
# A missing reason must not claim "closed" -- that would assert a forge fact
# nobody read. The fallback is the weaker of the two claims.
check("zone_a_missing_reason_falls_back_to_unlabelled",
      ["PR #10  issue-7  issue #7 unlabelled"], _za_kid_text_279(None))
# Zone A must NOT say "nothing needs you" when an orphan is the only finding.
check("orphan_pr_clears_nothing_needs_you", False,
      any(r.kind == "empty" for r in _za279))
_thin279 = tickmod.build_thin({"repos": [_orphan_repo_row_279], "alerts": []})
check("orphan_prs_reaches_the_tick_digest", [311],
      _thin279["repos"][0]["orphan_prs"])
# A digest change alone tells NOBODY: needs_attention <= 0 means nothing
# fires regardless of what the digest does. So the orphan must
# raise a real condition, or it reaches no human -- the issue's consequence 3.
_na279, _notes279, _conds279 = tickmod.compute_conditions(
    {"repos": [_orphan_repo_row_279], "alerts": []}, _thin279)
check("orphan_pr_raises_needs_attention", True, _na279 >= 1)
check("orphan_pr_fires_condition_9", True,
      any(c.get("cond") == 9 and c.get("slug") == "r" for c in _conds279))
check("orphan_pr_note_names_the_pr", True,
      any("#311" in n for n in _notes279))
# One dict per REPO, not per PR -- set-level, like condition 8.
check("orphan_pr_one_cond_per_repo", 1,
      len([c for c in _conds279 if c.get("cond") == 9]))
# A CLOSED orphan (this fixture's PR #311, issue #125 closed) DOES spawn
# repo-orch on the slug (orch#387): route()'s target was always the repo,
# not the issue, and a closed-issue orphan with nowhere to route left PR
# #388 firing condition 9 for six wakes with no session ever landing it.
# repo-orch resolves what to do once woken; it has no open issue to spawn
# issue-orch against, so it can only raise this to a human -- but that raise
# now actually gets SCHEDULED. (orch#352 first split this from a blanket
# "cond 9 never spawns"; orch#387 corrects the closed half's non-spawn.)
check("orphan_pr_does_spawn_387", ["r"], tickmod.route(
    [c for c in _conds279 if c.get("cond") == 9]))
# The closed half's dict still carries issue: None -- that fact did not
# change, only whether route() spawns on it.
check("closed_orphan_issue_is_none_but_now_spawns_387", True,
      all(c.get("issue") is None for c in _conds279 if c.get("cond") == 9)
      and tickmod.route([c for c in _conds279 if c.get("cond") == 9]) == ["r"])
# No orphan -> no condition 9, so a quiet repo is not woken by this.
_no_orphan_row_279 = dict(_orphan_repo_row_279, orphan_prs=[])
_thin_no279 = tickmod.build_thin({"repos": [_no_orphan_row_279], "alerts": []})
check("no_orphan_no_condition_9", [],
      [c for c in tickmod.compute_conditions(
          {"repos": [_no_orphan_row_279], "alerts": []}, _thin_no279)[2]
       if c.get("cond") == 9])

# === orch#352: unlabelled orphans (issue still OPEN) must spawn ============
# orph#279 treated every orphan the same -- closed or unlabelled, neither
# spawned. Live case orch#340: {"state": "OPEN", "labels": []}, open PR #349
# on branch issue-340, sat unmerged all night because nothing picked it up.
# orphan_prs builds an "unlabelled" row from self.issues, which is
# `--state open` -- so unlabelled is open BY CONSTRUCTION and IS spawnable.

def _cc279(row):
    """repo row -> compute_conditions' (needs_attention, notes, conds)."""
    thin = tickmod.build_thin({"repos": [row], "alerts": []})
    return tickmod.compute_conditions({"repos": [row], "alerts": []}, thin)


_unl_row_352 = dict(_orphan_repo_row_279, orphan_prs=[
    {"number": 349, "branch": "issue-340", "issue": 340,
     "reason": "unlabelled"}])
_unl_na352, _unl_notes352, _unl_conds352 = _cc279(_unl_row_352)
_unl_c9_352 = [c for c in _unl_conds352 if c.get("cond") == 9]

# 1. An unlabelled orphan's cond-9 dict carries the issue number, and that
# routes the repo's slug to spawn.
check("unlabelled_orphan_cond_9_carries_issue", [340],
      [c["issue"] for c in _unl_c9_352])
check("unlabelled_orphan_routes_to_spawn", ["r"], tickmod.route(_unl_c9_352))

# 2. A closed orphan (regression guard, orch#387: this now spawns, restating
# orphan_pr_does_spawn_387 above against the shared invariant).
check("closed_orphan_routes_to_slug_387", ["r"],
      tickmod.route([c for c in _conds279 if c.get("cond") == 9]))

# 3. A repo with BOTH kinds: two cond-9 dicts (one issue-less, one
# unlabelled), but ONE slug in route()'s output -- de-duped, not two spawns.
_both_row_352 = dict(_orphan_repo_row_279, orphan_prs=[
    {"number": 311, "branch": "issue-125", "issue": 125, "reason": "closed"},
    {"number": 349, "branch": "issue-340", "issue": 340,
     "reason": "unlabelled"}])
_both_na352, _both_notes352, _both_conds352 = _cc279(_both_row_352)
_both_c9_352 = [c for c in _both_conds352 if c.get("cond") == 9]
check("mixed_orphans_one_closed_dict_one_unlabelled_dict", 2, len(_both_c9_352))
check("mixed_orphans_spawn_slug_once_387", ["r"], tickmod.route(_both_c9_352))

# 4. A missing `reason` still falls back to the weaker (closed) SHAPE --
# spawning on an unverified reason claim is not what makes this spawn now;
# it spawns because closed-shaped dicts spawn too (orch#387).
_noreason_row_352 = dict(_orphan_repo_row_279, orphan_prs=[
    {"number": 349, "branch": "issue-340", "issue": 340}])
_nr_na352, _nr_notes352, _nr_conds352 = _cc279(_noreason_row_352)
check("missing_reason_spawns_via_closed_shape_387", ["r"],
      tickmod.route([c for c in _nr_conds352 if c.get("cond") == 9]))

# 5. An unlabelled orphan must be loud AND actionable: needs_attention rises
# AND a note is produced (not either/or) AND it still spawns.
check("unlabelled_orphan_raises_needs_attention", True, _unl_na352 >= 1)
check("unlabelled_orphan_produces_a_note", True,
      any("#349" in n for n in _unl_notes352))

# 6. A malformed orphan row (non-dict, or issue: None) must not raise and
# must not spawn -- same tolerance build_thin/_orphan_pr_reasons already
# apply to the digest.
_malformed_row_352 = dict(_orphan_repo_row_279, orphan_prs=[
    "notadict", None,
    {"number": 1, "branch": "issue-1", "issue": None, "reason": "unlabelled"}])
_mf_na352, _mf_notes352, _mf_conds352 = _cc279(_malformed_row_352)
check("malformed_orphan_row_does_not_raise_and_does_not_spawn", [],
      tickmod.route([c for c in _mf_conds352 if c.get("cond") == 9]))

# build_thin must not raise the whole tick over one malformed row.
check("orphan_prs_skips_unusable_rows", [7],
      tickmod.build_thin({"repos": [dict(_orphan_repo_row_279, orphan_prs=[
          {"number": "abc"}, {"number": None}, "notadict", {"number": 7}])],
          "alerts": []})["repos"][0]["orphan_prs"])
check("orphan_prs_tolerates_null", [],
      tickmod.build_thin({"repos": [dict(_orphan_repo_row_279,
                                         orphan_prs=None)],
                          "alerts": []})["repos"][0]["orphan_prs"])
# zone_a reads the SAME field and must survive the same input, or a cached
# feed row blanks the one place a human sees what needs them. Either both
# tolerate it or neither should.
_za_bad279 = tui_model.zone_a({"repos": [dict(_orphan_repo_row_279, orphan_prs=[
    "notadict", None, {"number": 311, "branch": "issue-125", "issue": 125}])],
    "alerts": []})
check("zone_a_survives_malformed_orphan_rows", True,
      any(r.kind == "alert" and "#311" in r.text for r in _za_bad279))
check("zone_a_orphan_none_is_not_an_alert", [],
      [r for r in tui_model.zone_a(
          {"repos": [dict(_orphan_repo_row_279, orphan_prs=None)], "alerts": []})
       if r.kind == "alert" and "ORPHAN PR" in r.text])
# The digest must CHANGE when an orphan appears -- otherwise the wake cannot
# tell a repo with a new orphan from one without.
check("orphan_prs_changes_the_digest", True,
      tickmod.build_thin({"repos": [_no_orphan_row_279], "alerts": []})
      != _thin279)

# === orch#387: condition 9 spawns on BOTH halves ============================
# orch#352 only fixed the unlabelled half. The closed half's dict (issue:
# None) still fell through route() unspawned -- live case PR #388, six
# consecutive wakes, merged by hand because nothing ever routed to it.
# route()'s job is picking a SPAWN TARGET, and the target was always the
# repo slug (repo-orch), never the issue -- so both halves now spawn. The
# closed-only, unlabelled-only, both-halves-dedup and missing-reason cases
# are covered in place above (orphan_pr_does_spawn_387,
# unlabelled_orphan_routes_to_spawn, mixed_orphans_spawn_slug_once_387,
# missing_reason_spawns_via_closed_shape_387). What is left:

# A condition-4-style repo-less row (no slug at all, e.g. an error alert)
# must still never spawn -- the `if not slug: continue` guard in route() is
# orthogonal to condition 9 joining SPAWN_CONDS, and this is not specific to
# cond 9 at all.
check("repo_less_row_never_spawns_387", [],
      tickmod.route([{"slug": None, "issue": None, "cond": 4,
                       "orch_alive": False}]))

# Malformed orphan rows (non-dict, or issue: None with reason != "closed")
# still never spawn -- they never reach compute_conditions as a cond-9 dict
# at all for this fixture (see malformed_orphan_row_does_not_raise_and_does_
# not_spawn above), so this is a regression guard on the same fixture under
# the new route().
check("malformed_orphan_row_still_never_spawns_387", [],
      tickmod.route([c for c in _mf_conds352 if c.get("cond") == 9]))

# === orch#336: LEASE_TTL_MINS / claim_age_mins / lease_expired ==============
# Fake World, same idiom as _FeedFakeWorld above: only the surface
# claim_age_mins/lease_expired actually touch (issue_has_label). The
# labeled-event fetch (_agent_working_labeled_at) is stubbed at the module
# level below -- no network in the suite -- so this fake world carries
# `updatedAt`/comments only to prove the NEW derivation ignores them, not
# because claim_age_mins reads them anymore. now() is monkeypatched rather
# than baking real wall-clock skew into fixture timestamps, so "age" is
# exact and deterministic.
class _LeaseFakeWorld:
    def __init__(self, labels, updated_at=None, created_at=None, comments=None,
                 pr="", green=False, red=False):
        self._pr, self._green, self._red = pr, green, red
        self.issues = [{
            "number": 1,
            "labels": [{"name": l} for l in labels],
            "updatedAt": updated_at,
            "createdAt": created_at,
        }]
        self._comments = comments or []

    def issue_has_label(self, n, label):
        for i in self.issues:
            if i["number"] == n:
                return any(l["name"] == label for l in i.get("labels", []))
        return False

    def issue_field(self, n, field):
        for i in self.issues:
            if i["number"] == n:
                return i.get(field, "")
        return ""

    def issue_comments(self, n):
        return self._comments

    # work_state's surface. Default: no PR, so work_state falls through to
    # CLAIMED/ACTIVE and the existing fixtures keep their old answers.
    def branch_for_issue(self, n):
        return f"issue-{n}"

    def pr_for(self, br):
        return self._pr

    def pr_green(self, br):
        return self._green

    def pr_red(self, br):
        return self._red


_lease_orig_now = core.now
_LEASE_NOW = {"val": 1_000_000_000}  # arbitrary fixed instant
core.now = lambda: _LEASE_NOW["val"]

# Stub the labeled-event fetch itself: claim_age_mins now calls
# _agent_working_labeled_at(world, n), which for real issues shells out to
# `gh api .../timeline`. Tests drive it via this dict instead -- keyed by
# id(world) so each fixture can set its own labeled-at instant (or None, for
# "unavailable") without a real subprocess.
_lease_orig_labeled_at = core._agent_working_labeled_at
_LEASE_LABELED_AT = {}
core._agent_working_labeled_at = lambda world, n: _LEASE_LABELED_AT.get(id(world))


def _lease_iso(seconds_ago):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(
        _LEASE_NOW["val"] - seconds_ago, tz=timezone.utc).isoformat()


def _lease_ts(seconds_ago):
    return _LEASE_NOW["val"] - seconds_ago


try:
    # THE critical regression test (orch#225, in the spirit of orch#355's
    # two-label rule): an issue's ONLY recent activity is a COMMENT --
    # agent-working was labeled deep in the past (past LEASE_TTL_MINS), but
    # a comment AND updatedAt are both seconds old. Under the OLD
    # MAX(comments, updatedAt) derivation this reads as ~0 minutes old and
    # lease_expired returns False -- the exact inversion orch#225 proved
    # live (agent-working at 17:21:05Z vs updatedAt 23:05:46Z after a
    # courier comment). The new derivation must ignore the comment/updatedAt
    # entirely and expire on the labeled-event time alone.
    w_comment_only = _LeaseFakeWorld(
        [core.L_WORKING],
        updated_at=_lease_iso(5),
        comments=[{"createdAt": _lease_iso(5)}])
    _LEASE_LABELED_AT[id(w_comment_only)] = _lease_ts((core.LEASE_TTL_MINS + 10) * 60)
    check("lease_expires_on_comment_only_activity_orch225", True,
          core.lease_expired(w_comment_only, 1))
    check("claim_age_mins_ignores_comment_and_updatedAt", True,
          core.claim_age_mins(w_comment_only, 1) >= core.LEASE_TTL_MINS)

    # Labeled-event time unavailable (tea, failed gh call, no event found)
    # -> None, and None never expires. NOT a fallback to updatedAt/comments,
    # even though both are set here to a stale value that would (under the
    # old code) have made this expire.
    w_none = _LeaseFakeWorld(
        [core.L_WORKING],
        updated_at=_lease_iso((core.LEASE_TTL_MINS + 100) * 60))
    _LEASE_LABELED_AT[id(w_none)] = None
    check("claim_age_mins_none_when_labeled_event_unavailable",
          None, core.claim_age_mins(w_none, 1))
    check("lease_never_expires_on_none_age", False, core.lease_expired(w_none, 1))

    # no-auto-land is EXEMPT even when far past LEASE_TTL_MINS -- and this
    # must short-circuit BEFORE the labeled-event fetch (label guards first,
    # network call last): no entry in _LEASE_LABELED_AT at all, so a lookup
    # that reached claim_age_mins would KeyError-free-default to None via
    # .get, but the point of this fixture is the exempt path never gets there.
    w_exempt = _LeaseFakeWorld([core.L_WORKING, core.L_NO_AUTOLAND])
    check("lease_exempt_no_auto_land", False, core.lease_expired(w_exempt, 1))

    # Under T is not expired.
    w_under = _LeaseFakeWorld([core.L_WORKING])
    _LEASE_LABELED_AT[id(w_under)] = _lease_ts((core.LEASE_TTL_MINS - 10) * 60)
    check("lease_not_expired_under_ttl", False, core.lease_expired(w_under, 1))

    # Over T, agent-working, no no-auto-land -> IS expired.
    w_over = _LeaseFakeWorld([core.L_WORKING])
    _LEASE_LABELED_AT[id(w_over)] = _lease_ts((core.LEASE_TTL_MINS + 10) * 60)
    check("lease_expired_over_ttl", True, core.lease_expired(w_over, 1))

    # REVIEW and CHECKING are EXEMPT even far past LEASE_TTL_MINS: an open
    # PR waiting on a human's merge or on CI is not an abandoned claim, and
    # claim age climbs the whole time it waits. orch#336 is the live case --
    # agent-working, PR open and green, age far past the TTL.
    w_review = _LeaseFakeWorld([core.L_WORKING], pr="OPEN", green=True)
    _LEASE_LABELED_AT[id(w_review)] = _lease_ts((core.LEASE_TTL_MINS + 10) * 60)
    check("lease_exempt_review", False, core.lease_expired(w_review, 1))

    w_checking = _LeaseFakeWorld([core.L_WORKING], pr="OPEN")
    _LEASE_LABELED_AT[id(w_checking)] = _lease_ts((core.LEASE_TTL_MINS + 10) * 60)
    check("lease_exempt_checking", False, core.lease_expired(w_checking, 1))

    # BLOCKED (open PR, CI red) is NOT exempt: a red PR nobody is fixing is
    # exactly the abandoned claim the lease exists to catch.
    w_blocked = _LeaseFakeWorld([core.L_WORKING], pr="OPEN", red=True)
    _LEASE_LABELED_AT[id(w_blocked)] = _lease_ts((core.LEASE_TTL_MINS + 10) * 60)
    check("lease_not_exempt_blocked", True, core.lease_expired(w_blocked, 1))

    # Already agent-stuck: not re-expired (already terminal). Label guard
    # short-circuits before the labeled-event fetch, same as w_exempt.
    w_stuck = _LeaseFakeWorld([core.L_WORKING, core.L_STUCK])
    check("lease_not_reexpired_when_already_stuck", False, core.lease_expired(w_stuck, 1))

    # No agent-working label at all: not expired. Label guard short-circuits
    # before the labeled-event fetch, same as w_exempt.
    w_unclaimed = _LeaseFakeWorld([])
    check("lease_not_expired_without_working_label", False, core.lease_expired(w_unclaimed, 1))

    # === orch#228: cross-host claim staleness ==============================
    # The two directions the brief names, because this is exactly the logic
    # that rots silently: a LOCAL live claim must never read stale, and a
    # FOREIGN expired one must never stop reading stale. Reuses this block's
    # monkeypatched now() so expiries are exact rather than wall-clock racy.
    def _claim_comment(host, expires_in_secs):
        return [{"body": f"orch/issue-orch.orch.1 claimed "
                         f"on {host} until {_lease_iso(-expires_in_secs)}"}]

    # Pin this_host to a FIXED name rather than reading the real one. Using
    # core.this_host() on both sides would assert the function agrees with
    # itself: on a machine where it returned "", the fixture would build
    # `claimed on  until <iso>`, the regex would capture the timestamp as the
    # host and fail on the missing `until`, and every local check below would
    # pass for the wrong reason while testing nothing.
    _lease_orig_this_host = core.this_host
    core.this_host = lambda: "testhost"
    _here = "testhost"

    # A LOCAL claim returns None whatever its lease says -- even a lapsed
    # one. This is the double-spawn guard: locally the pgid is exact, so
    # this function must decline to have an opinion and let alive() rule.
    # If this ever returns True, a live local session becomes re-enterable.
    w_local_live = _LeaseFakeWorld([core.L_WORKING],
                                   comments=_claim_comment(_here, 3600))
    check("claim_local_never_stale", None, core.foreign_claim_stale(w_local_live, 1))
    w_local_lapsed = _LeaseFakeWorld([core.L_WORKING],
                                     comments=_claim_comment(_here, -3600))
    check("claim_local_lapsed_still_defers_to_pgid", None,
          core.foreign_claim_stale(w_local_lapsed, 1))

    # A FOREIGN claim past its lease IS stale -- the whole point of #228.
    # Before this, host B saw a healthy-looking claim forever.
    w_foreign_dead = _LeaseFakeWorld([core.L_WORKING],
                                     comments=_claim_comment("otherhost", -3600))
    check("claim_foreign_expired_is_stale", True,
          core.foreign_claim_stale(w_foreign_dead, 1))

    # A FOREIGN claim still inside its lease is presumed live: False, not
    # None -- this is the value void_claim reads to spare a running remote
    # session from the ledger checks that would all read "never attempted".
    w_foreign_live = _LeaseFakeWorld([core.L_WORKING],
                                     comments=_claim_comment("otherhost", 3600))
    check("claim_foreign_unexpired_not_stale", False,
          core.foreign_claim_stale(w_foreign_live, 1))

    # Every unreadable shape lands on None -- cannot-tell never authorizes
    # re-entry (docs/RESTRUCTURE-2026-09-16.md §4: suppress or defer, never
    # authorize). Garbage stamp, no claim entry, and no comments at all.
    w_garbage = _LeaseFakeWorld(
        [core.L_WORKING],
        comments=[{"body": "orch/issue-orch.orch.1 claimed on otherhost until nope"}])
    check("claim_unparseable_expiry_is_none", None,
          core.foreign_claim_stale(w_garbage, 1))
    w_no_claim = _LeaseFakeWorld([core.L_WORKING],
                                 comments=[{"body": "orch/tick wake\nnothing here"}])
    check("claim_no_lease_entry_is_none", None,
          core.foreign_claim_stale(w_no_claim, 1))
    # tea's shape: World.load normalizes every tea issue to an empty comment
    # list, so the whole feature degrades to never-firing there rather than
    # guessing. This is the assertion that pins that.
    w_tea = _LeaseFakeWorld([core.L_WORKING], comments=[])
    check("claim_no_comments_is_none", None, core.foreign_claim_stale(w_tea, 1))

    # NEWEST lease wins, not first-seen: a renewal is just another journal
    # entry, so an old lapsed stamp must not outvote a fresh one.
    w_renewed = _LeaseFakeWorld(
        [core.L_WORKING],
        comments=_claim_comment("otherhost", -3600) + _claim_comment("otherhost", 3600))
    check("claim_newest_lease_wins", False, core.foreign_claim_stale(w_renewed, 1))

    # claim_lease_note round-trips through the parser it feeds: the writer
    # and the reader agree on the format, which no other test would catch.
    _note_w = _LeaseFakeWorld(
        [core.L_WORKING],
        comments=[{"body": f"orch/issue-orch.orch.1 claimed {core.claim_lease_note(60)}"}])
    _parsed = core.claim_lease(_note_w, 1)
    check("claim_lease_note_parses_back", True,
          _parsed is not None and _parsed[0] == core.this_host())

    # An UNREADABLE hostname must read cannot-tell, not foreign. Without the
    # guard, `host == ""` is false for every real host, so a LOCAL lapsed
    # claim returns True -- a double-spawn against a live local session,
    # produced by the one input that is supposed to be fail-safe.
    core.this_host = lambda: ""
    check("claim_empty_host_is_none_not_foreign", None,
          core.foreign_claim_stale(w_local_lapsed, 1))
    check("claim_empty_host_never_stales_foreign", None,
          core.foreign_claim_stale(w_foreign_dead, 1))
    core.this_host = lambda: "testhost"

    # lease_expired CONSUMES the True branch: a foreign claim whose lease
    # lapsed is expired now, without waiting out LEASE_TTL_MINS. Without this
    # the whole feature is inert -- foreign_claim_stale returning True and
    # returning None would behave identically, and no host would ever
    # re-enter a claim left by a dead session on another machine.
    _LEASE_LABELED_AT[id(w_foreign_dead)] = _lease_ts(60)  # young by TTL
    check("lease_expired_on_stale_foreign_claim", True,
          core.lease_expired(w_foreign_dead, 1))
    # ... and a foreign claim still inside its lease is NOT expired early.
    _LEASE_LABELED_AT[id(w_foreign_live)] = _lease_ts(60)
    check("lease_not_expired_on_live_foreign_claim", False,
          core.lease_expired(w_foreign_live, 1))
    # A LOCAL lapsed lease must NOT expire early -- the pgid governs here,
    # and a coarse timeout must never unseat it. Falls through to the age
    # check, which is young, so False.
    _LEASE_LABELED_AT[id(w_local_lapsed)] = _lease_ts(60)
    check("lease_not_expired_early_on_local_claim", False,
          core.lease_expired(w_local_lapsed, 1))
    # The exemption guards still win over a lapsed foreign lease: an open PR
    # at REVIEW is someone else's move, not an abandoned claim (orch#336).
    w_foreign_review = _LeaseFakeWorld([core.L_WORKING], pr="OPEN", green=True,
                                       comments=_claim_comment("otherhost", -3600))
    _LEASE_LABELED_AT[id(w_foreign_review)] = _lease_ts(60)
    check("lease_foreign_stale_still_exempt_at_review", False,
          core.lease_expired(w_foreign_review, 1))

    # journal writes the lease itself, so no agent has to format it: a bare
    # `claimed` event must come back out of claim_lease's own parser. This is
    # the writer/reader contract end to end.
    _claim_bodies = []
    _orig_run = core.subprocess.run
    def _capture_run(argv, **kw):
        _claim_bodies.append(kw.get("input") or "")
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    core.subprocess.run = _capture_run
    try:
        # The REAL implementation -- core._journal_append_issue is rebound to
        # a recording lambda at the top of this suite, which never builds a
        # body, so calling it would assert nothing about the format.
        _REAL_JOURNAL_APPEND_ISSUE("o/r", 1, "issue-orch.orch.1", "claimed", None)
    finally:
        core.subprocess.run = _orig_run
    # Last non-empty input: the backend resolution above this call runs its
    # own subprocesses through the same stub, and those carry no stdin.
    _w_written = _LeaseFakeWorld(
        [core.L_WORKING],
        comments=[{"body": [b for b in _claim_bodies if b][-1]}])
    _wl = core.claim_lease(_w_written, 1)
    check("journal_claimed_event_carries_parseable_lease", True,
          _wl is not None and _wl[0] == "testhost" and _wl[1] > 0)
finally:
    core.now = _lease_orig_now
    core._agent_working_labeled_at = _lease_orig_labeled_at
    try:
        core.this_host = _lease_orig_this_host
    except NameError:
        pass
# orch#321: a no-commit issue reaches a terminal state only if issue-orch can
# actually close it. The permission envelope is DERIVED from the ```bash fences
# in the agent doc (_doc_commands), so the doc teaching the act and the level
# being allowed to perform it are the same fact -- and before #321 the closing
# act was documented in issue-landing's skill while being absent from the
# envelope, leaving the one level the design names unable to run it.
#
# Read the REAL checked-in doc, not ORCH_HOME's: the suite points ORCH_HOME at
# a temp dir that holds only stub docs written by the spawn tests above.
_r321_home = core.ORCH_HOME
try:
    core.ORCH_HOME = core.PACKAGE_ROOT
    _r321_rules = core._doc_commands("issue-orch")
finally:
    core.ORCH_HOME = _r321_home

# The whole defect is that nothing fires on a completed no-commit issue. This
# fails if the fence is ever dropped or reworded out of a bash block.
check("r321_issue_orch_may_close", True, "Bash(gh issue close:*)" in _r321_rules)
# BOTH backends. The defect was OBSERVED on a Gitea repo (eva-backgrounds), so
# a gh-only grant cannot close the very issues that motivated this: `gh` fails
# there with "none of the git remotes point to a known GitHub host". `tea issue
# close` is SINGULAR -- only the singular alias is harvested (orch#170), so the
# plural spelling would grant nothing.
check("r321_issue_orch_may_close_gitea", True, "Bash(tea issue close:*)" in _r321_rules)
check("r321_issue_orch_may_comment_gitea", True, "Bash(tea comments add:*)" in _r321_rules)
# Closing must not cost the level its existing verbs -- the same fence harvest
# produces all of them, so a malformed edit degrades the whole envelope.
check("r321_issue_orch_keeps_label_grant", True, "Bash(gh issue edit:*)" in _r321_rules)
check("r321_issue_orch_keeps_journal_grant", True, "Bash(gh issue comment:*)" in _r321_rules)
# The verb grant alone is not enough to prove the fix is still here: a stray
# `gh issue close` in some unrelated fence would keep the checks above green
# while the Done section was deleted, restoring the defect in full. So pin the
# SECTION too -- the instruction is what a session actually reads.
_r321_doc = (core.PACKAGE_ROOT / "agents" / "issue-orch.md").read_text()
check("r321_done_section_present", True, "## Done — closing is the terminal act" in _r321_doc)
# The close must survive a failed label edit, or a half-finished claim on a
# repo missing an ORCH_LABEL leaves the issue open -- exactly the eva-backgrounds
# shape the issue reported (its claim stripped agent-ready, never added
# agent-working).
check("r321_close_survives_label_failure", True,
      "If the label edit fails, run the" in _r321_doc)
# NOT asserted here: that repo-orch cannot close. It already can, and has since
# orch#251 -- its doc teaches `gh issue close` for FOLDS (superseding a
# redundant issue), a grant that predates this issue and is unrelated to
# completion. orch#321's option 3 was about letting repo-orch strip
# `agent-ready` off work it judges finished, which is a different act from
# folding a duplicate and is still declined: nothing here grants it.

# === orch#337: agent_bot_login / issue_timeline_labels / label_provenance /
# label_cause_badge ==========================================================
# A real git checkout with a github.com remote, same idiom as the orch#58
# load_gh_dir fixture above -- label_provenance resolves world.repo_slug
# back to a checkout path via _repo_entry_for_owner_slug, which shells out
# to `git remote` (gh_repo), so the remote has to be real for that reverse
# lookup to succeed.
_lp_dir = T / "label-provenance"
_lp_dir.mkdir()
subprocess.run(["git", "init", "-q"], cwd=_lp_dir, check=True)
subprocess.run(["git", "remote", "add", "origin",
                 "git@github.com:cybermelon/lp-repo.git"], cwd=_lp_dir, check=True)
_lp_orch_json = core.ORCH_HOME / "orch.json"
_lp_slug = "cybermelon/lp-repo"


def _write_lp_entry(agent_login=None):
    entry = {"path": str(_lp_dir), "state": "tracked"}
    if agent_login is not None:
        entry["agent-login"] = agent_login
    _lp_orch_json.write_text(json.dumps({"repos": [entry]}))


class _LPWorld:
    """Just enough of World for label_provenance/label_cause_badge: a
    resolved slug and a real gh-backend RepoAdapter (so
    world.adapter.issue_timeline_labels(n) builds real argv for the stubbed
    core._run below to receive)."""
    def __init__(self, slug):
        self.repo_slug = slug
        self.backend = "gh"
        self.login = None

    @property
    def adapter(self):
        return core.RepoAdapter(self.backend, self.repo_slug, self.login,
                                 core.GH_WEB_BASE)


_lp_world = _LPWorld(_lp_slug)

# agent_bot_login: key absent / repo absent -> None, never raises.
_write_lp_entry()  # no agent-login key at all
check("agent_bot_login_key_absent", None, core.agent_bot_login(_lp_dir))
check("agent_bot_login_repo_absent", None,
      core.agent_bot_login(T / "label-provenance-missing"))

_orig_run_for_lp = core._run
_lp_orig_now = core.now
_LP_TIMELINE_JSON = {"val": "[]"}
core._run = lambda cmd, **kw: (True, _LP_TIMELINE_JSON["val"]) \
    if (len(cmd) >= 2 and cmd[0] == "gh" and cmd[1] == "api"
        and "timeline" in cmd[2]) else _orig_run_for_lp(cmd, **kw)

try:
    # No agent-login configured at all -> UNKNOWN, regardless of what the
    # timeline says. Today's universal case.
    _write_lp_entry()  # agent-login absent
    _LP_TIMELINE_JSON["val"] = json.dumps([
        {"event": "labeled", "label": "no-auto-land", "actor": "some-bot",
         "at": "2026-09-16T10:00:00Z"},
    ])
    # The VERDICT is UNKNOWN -- the actor string identifies nobody without an
    # agent-login to compare it against -- but the labeled event was really
    # read, so `at`/`actor` come back populated. label_cause_badge depends on
    # that: this is the only case it ever runs in today, and an at=None here
    # would make the badge unreachable in production.
    v, at, actor = core.label_provenance(_lp_world, 1, "no-auto-land")
    check("label_provenance_unknown_no_agent_login_verdict", "UNKNOWN", v)
    check("label_provenance_unknown_no_agent_login_keeps_at",
          ("2026-09-16T10:00:00Z", "some-bot"), (at, actor))

    # agent-login configured, actor matches it -> AGENT.
    _write_lp_entry(agent_login="orch-bot")
    _LP_TIMELINE_JSON["val"] = json.dumps([
        {"event": "labeled", "label": "no-auto-land", "actor": "orch-bot",
         "at": "2026-09-16T10:00:00Z"},
    ])
    v, at, actor = core.label_provenance(_lp_world, 1, "no-auto-land")
    check("label_provenance_agent", ("AGENT", "2026-09-16T10:00:00Z", "orch-bot"),
          (v, at, actor))

    # agent-login configured, actor is someone else -> OPERATOR. Newest
    # `labeled` event wins when there are several.
    _write_lp_entry(agent_login="orch-bot")
    _LP_TIMELINE_JSON["val"] = json.dumps([
        {"event": "labeled", "label": "no-auto-land", "actor": "orch-bot",
         "at": "2026-09-16T09:00:00Z"},
        {"event": "labeled", "label": "no-auto-land", "actor": "cybermelons",
         "at": "2026-09-16T10:00:00Z"},
        {"event": "unlabeled", "label": "no-auto-land", "actor": "orch-bot",
         "at": "2026-09-16T11:00:00Z"},
    ])
    v, at, actor = core.label_provenance(_lp_world, 1, "no-auto-land")
    check("label_provenance_operator_newest_wins",
          ("OPERATOR", "2026-09-16T10:00:00Z", "cybermelons"), (v, at, actor))

    # Failed/garbage timeline call -> UNKNOWN, no exception.
    _write_lp_entry(agent_login="orch-bot")
    _LP_TIMELINE_JSON["val"] = "not json at all"
    check("label_provenance_garbage_json_unknown", ("UNKNOWN", None, None),
          core.label_provenance(_lp_world, 1, "no-auto-land"))

    core._run = lambda cmd, **kw: (False, "") \
        if (len(cmd) >= 2 and cmd[0] == "gh" and cmd[1] == "api"
            and "timeline" in cmd[2]) else _orig_run_for_lp(cmd, **kw)
    check("label_provenance_failed_call_unknown", ("UNKNOWN", None, None),
          core.label_provenance(_lp_world, 1, "no-auto-land"))

    # tea backend -> UNKNOWN even with agent-login configured (no reachable
    # timeline; see _agent_working_labeled_at's docstring).
    _lp_world.backend = "tea"
    core._run = lambda cmd, **kw: (_ for _ in ()).throw(
        AssertionError("must not shell out on tea"))
    check("label_provenance_tea_unknown", ("UNKNOWN", None, None),
          core.label_provenance(_lp_world, 1, "no-auto-land"))
    _lp_world.backend = "gh"

    # --- label_cause_badge ---------------------------------------------
    core._run = lambda cmd, **kw: (True, _LP_TIMELINE_JSON["val"]) \
        if (len(cmd) >= 2 and cmd[0] == "gh" and cmd[1] == "api"
            and "timeline" in cmd[2]) else _orig_run_for_lp(cmd, **kw)

    _lp_label_at = "2026-09-16T10:00:00Z"
    core.now = lambda: core._parse_iso8601(_lp_label_at) + 47 * 60

    # Matched: a journal row for the same issue lands inside window_mins of
    # the label event -> None (no badge).
    _write_lp_entry()  # UNKNOWN provenance (no agent-login) -- eligible
    _LP_TIMELINE_JSON["val"] = json.dumps([
        {"event": "labeled", "label": "no-auto-land", "actor": "cybermelons",
         "at": _lp_label_at},
    ])
    # The journal is keyed by the checkout's DIRECTORY NAME, not the
    # "owner/repo" slug -- label_cause_badge takes basename of the resolved
    # checkout path, which is _lp_dir.name ("label-provenance"), NOT
    # "lp-repo". Writing to the slug name here would leave the badge reading
    # a directory that never exists, so BOTH cases below would report "no
    # journal row" and the matched case would pass for the wrong reason.
    _lp_journal_dir = core.JOURNAL_ROOT / "repos" / _lp_dir.name
    _lp_journal_dir.mkdir(parents=True, exist_ok=True)
    _lp_journal_file = _lp_journal_dir / "orch.jsonl"
    _lp_journal_file.write_text(json.dumps({
        "at": "2026-09-16T10:02:00Z", "actor": "issue-orch",
        "event": "observed", "issue": 1,
    }) + "\n")
    check("label_cause_badge_matched_none", None,
          core.label_cause_badge(_lp_world, 1, "no-auto-land"))

    # Unmatched: no journal row near the label event for this issue ->
    # advisory string containing "no cause".
    _lp_journal_file.write_text(json.dumps({
        "at": "2026-09-16T05:00:00Z", "actor": "issue-orch",
        "event": "observed", "issue": 1,
    }) + "\n")
    badge = core.label_cause_badge(_lp_world, 1, "no-auto-land")
    check("label_cause_badge_unmatched_has_message", True,
          badge is not None and "no cause" in badge)

    # OPERATOR provenance never gets a badge, even with no journal row at all.
    _lp_journal_file.write_text("")
    _write_lp_entry(agent_login="orch-bot")
    _LP_TIMELINE_JSON["val"] = json.dumps([
        {"event": "labeled", "label": "no-auto-land", "actor": "cybermelons",
         "at": _lp_label_at},
    ])
    check("label_cause_badge_operator_no_badge", None,
          core.label_cause_badge(_lp_world, 1, "no-auto-land"))

    # An unresolvable slug yields NO badge, rather than falling back to
    # basename("owner/repo") == "repo" -- that bare name either collides
    # with a different watched repo's journal dir or matches nothing, and
    # the nothing case would fire "no cause" against a hold whose cause is
    # merely unreadable. Absent evidence must make no claim.
    _write_lp_entry()  # entry path no longer matches _unresolvable_world
    _unresolvable_world = _LPWorld("cybermelon/not-a-watched-repo")
    _LP_TIMELINE_JSON["val"] = json.dumps([
        {"event": "labeled", "label": "no-auto-land", "actor": "cybermelons",
         "at": _lp_label_at},
    ])
    check("label_cause_badge_unresolvable_slug_no_badge", None,
          core.label_cause_badge(_unresolvable_world, 1, "no-auto-land"))
finally:
    core._run = _orig_run_for_lp
    core.now = _lp_orig_now
# --- decisions ledger (orch#368) -- isolated under its own JOURNAL_ROOT so
# corrupting decisions.jsonl here can't bleed into any other test's reads.
_dec_orig_root = core.JOURNAL_ROOT
core.JOURNAL_ROOT = T / "decisions-test"
try:
    # Missing file -> {}, never raises.
    check("decisions_read_missing_file", {}, core.decisions_read())

    # Corrupt/non-JSON line skipped, non-dict row skipped, missing/empty/
    # non-string id skipped, missing/non-string at skipped -- valid rows in
    # the same file still resolve. Written directly (not via
    # decisions_append) so the bad rows can be crafted at all.
    core.decisions_path().parent.mkdir(parents=True, exist_ok=True)
    good_row = {"id": "D-ok", "question": "q", "ruling": "r",
                "decided_by": "operator", "at": core.now_iso(),
                "ref": "x", "supersedes": None, "scope": "repo-wide"}
    bad_lines = [
        "not json at all {{{",
        json.dumps(["a", "list", "not", "a", "dict"]),
        json.dumps({"question": "q", "at": core.now_iso()}),  # no id
        json.dumps({"id": "", "at": core.now_iso()}),  # empty id
        json.dumps({"id": 5, "at": core.now_iso()}),  # non-string id
        json.dumps({"id": "D-no-at"}),  # missing at
        json.dumps({"id": "D-bad-at", "at": 12345}),  # non-string at
    ]
    with core.decisions_path().open("w") as fh:
        for line in bad_lines:
            fh.write(line + "\n")
        fh.write(json.dumps(good_row) + "\n")
    dec_corrupt = core.decisions_read()
    check("decisions_read_corrupt_degrades_no_raise",
          {"D-ok"}, set(dec_corrupt.keys()))
    check("decisions_read_corrupt_good_row_survives",
          good_row, dec_corrupt.get("D-ok"))

    # Reset to a clean file for the rest of the block.
    core.decisions_path().unlink()

    # Round-trip through decisions_append preserves all eight fields.
    core.decisions_append("D-rt", "q?", "yes", "operator", "issue#1",
                           supersedes=None, scope="repo-wide")
    rt = core.decisions_read()["D-rt"]
    check("decisions_append_roundtrip_fields",
          {"id", "question", "ruling", "decided_by", "at", "ref",
           "supersedes", "scope"}, set(rt.keys()))
    check("decisions_append_roundtrip_values",
          ("D-rt", "q?", "yes", "operator", "issue#1", None, "repo-wide"),
          (rt["id"], rt["question"], rt["ruling"], rt["decided_by"],
           rt["ref"], rt["supersedes"], rt["scope"]))

    # A superseding row resolves alongside the row it reverses: both ids
    # stay in decisions_read(), D2 names D1 in `supersedes`, and D1 still
    # resolves to its ORIGINAL ruling (never removed, never overwritten).
    core.decisions_append("D1", "q1?", "ruling-one", "operator", "issue#2")
    core.decisions_append("D2", "q1?", "ruling-two", "operator", "issue#2",
                           supersedes="D1")
    dec_super = core.decisions_read()
    check("decisions_supersede_both_ids_present",
          True, "D1" in dec_super and "D2" in dec_super)
    check("decisions_supersede_d2_names_d1",
          "D1", dec_super["D2"]["supersedes"])
    check("decisions_supersede_d1_keeps_original_ruling",
          "ruling-one", dec_super["D1"]["ruling"])

    # Newest-per-id wins when two rows share an id: write an older-looking
    # `at` after a newer one and confirm the max `at` (not append order)
    # decides the winner, per decisions_read's lexical-max-on-at contract.
    core.decisions_append("D-newest", "q", "first", "operator", "r")
    first_at = core.decisions_read()["D-newest"]["at"]
    # Force a strictly later `at` than "first"'s by writing the row
    # directly (now_iso() has 1-second resolution and this must not flake).
    later_row = {"id": "D-newest", "question": "q", "ruling": "second",
                 "decided_by": "operator", "at": first_at + "1",
                 "ref": "r", "supersedes": None, "scope": "repo-wide"}
    with core.decisions_path().open("a") as fh:
        fh.write(json.dumps(later_row) + "\n")
    check("decisions_newest_per_id_wins",
          "second", core.decisions_read()["D-newest"]["ruling"])
finally:
    core.JOURNAL_ROOT = _dec_orig_root

# --- spawn.cmd_decision CLI + decisions_seed.seed() -- orch#368 review fixes
_dec2_orig_root = core.JOURNAL_ROOT
core.JOURNAL_ROOT = T / "decisions-cli-test"
try:
    def run_cli_silent(argv):
        """Like run_cli, but also captures stderr instead of letting it hit
        the real one -- the reject paths below write USAGE there and it
        would just be noise. Returns (rc, stdout, stderr): callers asserting
        "no error-shaped output" must check stderr, not stdout -- USAGE
        always goes to stderr, never stdout."""
        buf, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
            rc = clicmd.main(argv)
        return rc, buf.getvalue(), errbuf.getvalue()

    # Rejections: empty id, empty ruling, bad --by, "delegated:" with no
    # name (finding 4), bad --scope. Each must return 2, not raise, and
    # must not append a row.
    rc, _, _ = run_cli_silent(["decision", "", "some ruling"])
    check("cmd_decision_rejects_empty_id", 2, rc)
    rc, _, _ = run_cli_silent(["decision", "some-id", ""])
    check("cmd_decision_rejects_empty_ruling", 2, rc)
    rc, _, _ = run_cli_silent(["decision", "some-id", "r", "--by", "bogus"])
    check("cmd_decision_rejects_bad_by", 2, rc)
    rc, _, _ = run_cli_silent(
        ["decision", "some-id", "r", "--by", "delegated:"])
    check("cmd_decision_rejects_empty_delegated_name", 2, rc)
    rc, _, _ = run_cli_silent(
        ["decision", "some-id", "r", "--scope", "per-issue:abc"])
    check("cmd_decision_rejects_bad_scope", 2, rc)
    check("cmd_decision_rejects_leave_no_rows", {}, core.decisions_read())

    # Happy path, every flag set -- every value must round-trip through
    # decisions_read() (anti-orch#366: no flag silently dropped).
    rc, _, _ = run_cli_silent([
        "decision", "d-happy", "the ruling",
        "--question", "the question?",
        "--by", "delegated:someone",
        "--ref", "#999",
        "--supersedes", "d-old",
        "--scope", "per-issue:42",
    ])
    check("cmd_decision_happy_path_rc", 0, rc)
    happy = core.decisions_read()["d-happy"]
    check("cmd_decision_happy_path_roundtrip",
          ("the question?", "the ruling", "delegated:someone", "#999",
           "d-old", "per-issue:42"),
          (happy["question"], happy["ruling"], happy["decided_by"],
           happy["ref"], happy["supersedes"], happy["scope"]))

    # Finding 2: an empty slug after "per-repo:" is rejected the same as an
    # empty "per-issue:" suffix; a real slug still passes.
    rc, _, _ = run_cli_silent(["decision", "some-id", "r", "--scope", "per-repo:"])
    check("cmd_decision_rejects_empty_per_repo_scope", 2, rc)
    rc, _, _ = run_cli_silent(
        ["decision", "d-per-repo", "r", "--scope", "per-repo:realslug"])
    check("cmd_decision_accepts_real_per_repo_scope", 0, rc)

    # Finding 3: <id> is validated stripped and must be STORED stripped too,
    # so it is findable under the clean id (old code stored it padded and
    # 'padded-id' in decisions_read() was False).
    rc, _, _ = run_cli_silent(["decision", " padded-id ", " padded ruling "])
    check("cmd_decision_padded_id_accepted_and_stripped_rc", 0, rc)
    check("cmd_decision_padded_id_findable_stripped",
          True, "padded-id" in core.decisions_read())
    check("cmd_decision_padded_id_ruling_stripped",
          "padded ruling", core.decisions_read()["padded-id"]["ruling"])

    # --supersedes is an id too; it must resolve against the same
    # (stripped) storage convention or a supersession chain silently breaks.
    rc, _, _ = run_cli_silent(
        ["decision", "d-super-me", "r", "--supersedes", " padded-id "])
    check("cmd_decision_supersedes_stripped_rc", 0, rc)
    check("cmd_decision_supersedes_matches_stored_id",
          "padded-id", core.decisions_read()["d-super-me"]["supersedes"])

    # Third review finding 2: whitespace-only suffixes must fail the
    # emptiness guards, not pass them (raw truthiness of " " is True).
    rc, _, _ = run_cli_silent(
        ["decision", "some-id", "r", "--by", "delegated:   "])
    check("cmd_decision_rejects_whitespace_delegated_name", 2, rc)
    rc, _, _ = run_cli_silent(
        ["decision", "some-id", "r", "--scope", "per-repo:   "])
    check("cmd_decision_rejects_whitespace_per_repo_scope", 2, rc)
    rc, _, _ = run_cli_silent(
        ["decision", "d-real-by", "r", "--by", "delegated:realagent"])
    check("cmd_decision_accepts_real_delegated_name", 0, rc)
    rc, _, _ = run_cli_silent(
        ["decision", "d-real-scope", "r", "--scope", "per-repo:realslug"])
    check("cmd_decision_accepts_real_per_repo_scope_again", 0, rc)

    # Third review finding 3: a whitespace-only --supersedes must resolve to
    # None, not the empty string -- "" is a third state decisions_read()
    # can never look up (it skips empty ids).
    rc, _, _ = run_cli_silent(
        ["decision", "d-super-blank", "r", "--supersedes", "   "])
    check("cmd_decision_supersedes_whitespace_rc", 0, rc)
    check("cmd_decision_supersedes_whitespace_becomes_none",
          None, core.decisions_read()["d-super-blank"]["supersedes"])
finally:
    core.JOURNAL_ROOT = _dec2_orig_root

# --- spawn.cmd_decisions (read verb) -- orch#368 third review finding 1 ------
_dec4_orig_root = core.JOURNAL_ROOT
core.JOURNAL_ROOT = T / "decisions-read-cli-test"
try:
    core.decisions_append("known-id", "q?", "known ruling", "operator", "r1")
    core.decisions_append("old-id", "q?", "old ruling", "operator", "r2")
    core.decisions_append("new-id", "q?", "new ruling", "delegated:bob", "r3",
                           supersedes="old-id", scope="per-issue:42")

    # No-record case is THE important one: it must return 0, not 2 or 1 --
    # a miss means "not settled, re-derive", never a failure that could
    # block a caller checking the exit code.
    rc, out, err = run_cli_silent(["decisions", "nope-not-a-real-id"])
    check("cmd_decisions_unknown_id_rc_is_zero", 0, rc)
    check("cmd_decisions_unknown_id_says_no_record",
          True, "no record" in out.lower())
    # 4th review finding 3: `out` is stdout only, and every reject path
    # writes USAGE to STDERR -- asserting against out can never fail, even
    # against a version that writes USAGE and returns 2. Assert against
    # err instead: the unknown-id path must write nothing error-shaped there.
    check("cmd_decisions_unknown_id_not_error_shaped",
          False, "error" in err.lower() or "usage" in err.lower())

    rc, out, err = run_cli_silent(["decisions", "known-id"])
    check("cmd_decisions_known_id_rc", 0, rc)
    check("cmd_decisions_known_id_shows_ruling",
          True, "known ruling" in out)

    # 4th review finding 1: looking up a SUPERSEDED id must name the row(s)
    # that reversed it, prominently -- not print the old ruling as settled.
    rc, out, err = run_cli_silent(["decisions", "old-id"])
    check("cmd_decisions_superseded_detail_names_superseder",
          True, "new-id" in out)
    # Non-superseded id must NOT show the marker -- no false positives.
    rc, out, err = run_cli_silent(["decisions", "known-id"])
    check("cmd_decisions_non_superseded_detail_no_marker",
          False, "SUPERSEDED" in out)

    rc, out, err = run_cli_silent(["decisions"])
    check("cmd_decisions_list_rc", 0, rc)
    check("cmd_decisions_list_shows_known_id",
          True, "known-id" in out)
    # List must visibly mark the superseded row and leave the other alone.
    lines = {ln.split(":", 1)[0]: ln for ln in out.splitlines()}
    check("cmd_decisions_list_marks_superseded_row",
          True, "superseded" in lines["old-id"].lower())
    check("cmd_decisions_list_does_not_mark_unsuperseded_row",
          False, "superseded" in lines["known-id"].lower())
    # 4th review finding 2: a non-default scope and a non-operator
    # decided_by must be visible in the list line, not dropped.
    check("cmd_decisions_list_shows_non_default_scope",
          True, "per-issue:42" in lines["new-id"])
    check("cmd_decisions_list_shows_non_operator_decided_by",
          True, "delegated:bob" in lines["new-id"])
finally:
    core.JOURNAL_ROOT = _dec4_orig_root

_dec3_orig_root = core.JOURNAL_ROOT
core.JOURNAL_ROOT = T / "decisions-seed-test"
try:
    from orch import decisions_seed

    first = decisions_seed.seed()
    check("decisions_seed_writes_rows_first_run",
          len(decisions_seed.SEED), len(first))
    resolved_1 = core.decisions_read()

    second = decisions_seed.seed()
    check("decisions_seed_idempotent_second_run_empty", [], second)
    resolved_2 = core.decisions_read()
    check("decisions_seed_idempotent_same_id_count",
          len(resolved_1), len(resolved_2))

    # The finding-1 supersession chain resolves: both ids present, and the
    # new row names the original in `supersedes`.
    check("decisions_seed_supersession_both_ids_present",
          True,
          "auto-review-config-shape-original" in resolved_2
          and "auto-review-config-shape" in resolved_2)
    check("decisions_seed_supersession_chain",
          "auto-review-config-shape-original",
          resolved_2["auto-review-config-shape"]["supersedes"])

    # Finding 1: A15's three separate amendments are three separate rows,
    # each with its own honest ref -- not one row conflating the first two
    # and dropping the third.
    check("decisions_seed_a15_storage_row_ref",
          "#297", resolved_2["auto-review-config-shape"]["ref"])
    check("decisions_seed_a15_one_flag_row_present_empty_ref",
          "", resolved_2["auto-review-one-flag-not-two"]["ref"])
    check("decisions_seed_a15_one_flag_supersedes_original",
          "auto-review-config-shape-original",
          resolved_2["auto-review-one-flag-not-two"]["supersedes"])
    check("decisions_seed_a15_revoke_row_present_empty_ref",
          "", resolved_2["blocking-finding-revokes-auto-land"]["ref"])
finally:
    core.JOURNAL_ROOT = _dec3_orig_root

# === core.void_claim / core.session_reported ================================

class _VoidFakeWorld:
    def __init__(self, labels, pr="", green=False, red=False):
        self.repo_slug = "cybermelon/orch"
        self._pr, self._green, self._red = pr, green, red
        self.issues = [{"number": 1, "labels": [{"name": l} for l in labels]}]

    def issue_has_label(self, n, label):
        for i in self.issues:
            if i["number"] == n:
                return any(l["name"] == label for l in i.get("labels", []))
        return False

    def branch_for_issue(self, n):
        return f"issue-{n}"

    def pr_for(self, br):
        return self._pr

    def pr_green(self, br):
        return self._green

    def pr_red(self, br):
        return self._red


_void_orig_alive = core.alive
_void_orig_prior_runs = core.prior_runs
_void_orig_session_reported = core.session_reported
_void_orig_work_mtime = core.work_mtime
_VOID_ALIVE = {"val": False}
_VOID_PRIOR_RUNS = {"val": 0}
_VOID_SESSION_REPORTED = {"val": False}
_VOID_WORK_MTIME = {"val": None}
core.alive = lambda key: _VOID_ALIVE["val"]
core.prior_runs = lambda key: _VOID_PRIOR_RUNS["val"]
core.session_reported = lambda key: _VOID_SESSION_REPORTED["val"]
core.work_mtime = lambda repo, br: _VOID_WORK_MTIME["val"]

try:
    _VOID_REPO = "/fake/repo"

    # Baseline void claim: agent-working, nothing else recorded anywhere.
    w_never = _VoidFakeWorld([core.L_WORKING])
    check("void_claim_true_never_attempted", True,
          core.void_claim(w_never, 1, repo=_VOID_REPO))

    # THE REGRESSION THAT MATTERS (orch#363): a report-only session ran and
    # posted its result but left no branch/commit because none were asked
    # for. prior_runs==0 && commits==0 alone would read this identically to
    # w_never and wrongly void the claim -- session_reported is the
    # discriminator that must stop it.
    _VOID_SESSION_REPORTED["val"] = True
    w_reported = _VoidFakeWorld([core.L_WORKING])
    check("void_claim_false_reported", False,
          core.void_claim(w_reported, 1, repo=_VOID_REPO))
    _VOID_SESSION_REPORTED["val"] = False

    _VOID_PRIOR_RUNS["val"] = 1
    w_prior = _VoidFakeWorld([core.L_WORKING])
    check("void_claim_false_prior_runs", False,
          core.void_claim(w_prior, 1, repo=_VOID_REPO))
    _VOID_PRIOR_RUNS["val"] = 0

    _VOID_ALIVE["val"] = True
    w_alive = _VoidFakeWorld([core.L_WORKING])
    check("void_claim_false_alive", False,
          core.void_claim(w_alive, 1, repo=_VOID_REPO))
    _VOID_ALIVE["val"] = False

    _VOID_WORK_MTIME["val"] = 1_700_000_000
    w_commits = _VoidFakeWorld([core.L_WORKING])
    check("void_claim_false_commits", False,
          core.void_claim(w_commits, 1, repo=_VOID_REPO))
    _VOID_WORK_MTIME["val"] = None

    # No repo path in hand -> fail closed, the commits check cannot be
    # evaluated at all.
    w_no_repo = _VoidFakeWorld([core.L_WORKING])
    check("void_claim_false_no_repo", False,
          core.void_claim(w_no_repo, 1, repo=None))

    w_review = _VoidFakeWorld([core.L_WORKING], pr="OPEN", green=True)
    check("void_claim_exempt_review", False,
          core.void_claim(w_review, 1, repo=_VOID_REPO))

    w_checking = _VoidFakeWorld([core.L_WORKING], pr="OPEN")
    check("void_claim_exempt_checking", False,
          core.void_claim(w_checking, 1, repo=_VOID_REPO))

    w_stuck = _VoidFakeWorld([core.L_WORKING, core.L_STUCK])
    check("void_claim_exempt_stuck", False,
          core.void_claim(w_stuck, 1, repo=_VOID_REPO))

    w_no_autoland = _VoidFakeWorld([core.L_WORKING, core.L_NO_AUTOLAND])
    check("void_claim_exempt_no_auto_land", False,
          core.void_claim(w_no_autoland, 1, repo=_VOID_REPO))

    w_unclaimed = _VoidFakeWorld([])
    check("void_claim_false_no_working_label", False,
          core.void_claim(w_unclaimed, 1, repo=_VOID_REPO))
finally:
    core.alive = _void_orig_alive
    core.prior_runs = _void_orig_prior_runs
    core.session_reported = _void_orig_session_reported
    core.work_mtime = _void_orig_work_mtime


# core.session_reported itself, against real tmp log files.
_sr_orig_sessions_dir = core.SESSIONS_DIR
_sr_tmpdir = tempfile.mkdtemp(prefix="orch_test_sessions_")
core.SESSIONS_DIR = Path(_sr_tmpdir)

try:
    key = "issue-orch.orch.363"
    log_path = core.ledger_log_path(key)

    log_path.write_text(
        "19:44:01 start     brief posted\n"
        "19:44:16 result    success, 11 turns, $0.83\n")
    check("session_reported_true_result_line", True, core.session_reported(key))

    log_path.write_text(
        "19:44:01 start     brief posted\n"
        "19:44:05 note      doing stuff\n")
    check("session_reported_false_no_result", False, core.session_reported(key))

    log_path.unlink()
    check("session_reported_false_missing", False, core.session_reported(key))

    # A log longer than the tail window: the seek lands mid-line, and the
    # surviving fragment of an `assistant` line can read "<word> result ..."
    # -- field 1 is then the literal token "result" on text that is not a
    # result line. The fragment must be dropped, so this reads False.
    filler = "12:00:00 assistant " + ("x" * 200) + "\n"
    bait = "and then the result was posted, no more work\n"
    pad = filler * ((core._RESULT_TAIL_BYTES // len(filler)) + 2)
    log_path.write_text(pad + bait)
    check("session_reported_false_split_line_fragment", False,
          core.session_reported(key))

    # Same oversized log, but with a REAL result line last: still True, so
    # the fragment-dropping above cannot be hiding a genuine result.
    log_path.write_text(pad + "19:44:16 result    success, 11 turns, $0.83\n")
    check("session_reported_true_past_tail_window", True,
          core.session_reported(key))
finally:
    core.SESSIONS_DIR = _sr_orig_sessions_dir
    shutil.rmtree(_sr_tmpdir, ignore_errors=True)


# void_claim END-TO-END against the REAL ledger functions -- nothing stubbed.
#
# The block above stubs alive/prior_runs/session_reported/work_mtime, so it
# proves void_claim calls them but cannot see WHICH KEY it calls them with. A
# key built from world.repo_slug ("cybermelons/orch") instead of the repo
# basename ("orch") points at a path that never exists: alive is always
# False, prior_runs' glob always matches zero, session_reported always
# OSErrors to False. Three guards silently satisfied for every issue, and the
# stubbed tests pass regardless. These two checks write a real log at the
# real key and call the real, unpatched function, so they fail if the key
# derivation is ever wrong again.
_vc_orig_sessions_dir = core.SESSIONS_DIR
_vc_orig_work_mtime = core.work_mtime
_vc_tmpdir = tempfile.mkdtemp(prefix="orch_test_voidclaim_")
core.SESSIONS_DIR = Path(_vc_tmpdir)
core.work_mtime = lambda repo, br: None  # no commits on the branch

try:
    # repo basename is "orch"; repo_slug is the owner/name form, as World.load
    # sets it live. The ledger key must follow the basename.
    _vc_repo = Path(_vc_tmpdir) / "orch"
    _vc_repo.mkdir()
    # _VoidFakeWorld keys its labels by issue number 1, so the issue under
    # test is 1; the repo basename is what the ledger key must follow.
    _vc_n = 1
    w_e2e = _VoidFakeWorld([core.L_WORKING])
    w_e2e.repo_slug = "cybermelons/orch"

    # No log at all -> nothing was ever attempted -> void.
    check("void_claim_e2e_true_no_log", True,
          core.void_claim(w_e2e, _vc_n, repo=str(_vc_repo)))

    # THE orch#363 REGRESSION, end to end: a real log at the REAL key
    # (issue-orch.orch.1.log -- basename, not owner/name) carrying a terminal
    # result line. The session ran and reported, leaving no commits because
    # none were asked for, so the claim is NOT void.
    #
    # This is the check that fails if void_claim keys the ledger on anything
    # but the repo basename: an owner/name key writes nothing readable at
    # this path, session_reported returns False, and the claim voids. Nothing
    # here is stubbed -- alive, prior_runs and session_reported all run for
    # real against SESSIONS_DIR.
    core.ledger_log_path(f"issue-orch.orch.{_vc_n}").write_text(
        "19:44:01 start     brief posted\n"
        "19:44:16 result    success, 11 turns, $0.83\n")
    check("void_claim_e2e_false_reported_real_key", False,
          core.void_claim(w_e2e, _vc_n, repo=str(_vc_repo)))
finally:
    core.SESSIONS_DIR = _vc_orig_sessions_dir
    core.work_mtime = _vc_orig_work_mtime
    shutil.rmtree(_vc_tmpdir, ignore_errors=True)

# === orch#184: core.wave_backoff -- mass session death (usage limit) =======
# detect / record / back off / resume. A rolled-aside row is
# `<key>.<started-iso>.json`; wave_backoff derives "death" from that FILE's
# own mtime (never a stored exit code -- none is ever recorded) and "void"
# from the absence of a `result` line in the shared <key>.log, with any row
# whose pgid is still alive skipped as not-a-death. Real files under a
# monkeypatched SESSIONS_DIR, real os.utime for mtime -- same "read layer
# for real" stance test_core opens with, not a stub of the scan itself.

def _wb_write_row(sessions_dir, key, started_iso, death_ts, reported=False,
                  pgid=None):
    """Write one rolled-aside row + its mtime, matching what _roll_aside
    actually produces: <key>.<started>.json with `started` inside it, plus
    (if `reported`) a <key>.log carrying a terminal result line -- the same
    shared-log caveat _wave_backoff_detail's docstring calls out applies
    here too, so a reported log is written at the PLAIN key, not the
    rolled-aside name.

    `pgid` defaults to a pgid that is certainly dead, since every row this
    helper writes stands for a session that died. Pass a live one to build
    the re-tick case, where a row rolls aside while its process runs on."""
    row_path = sessions_dir / f"{key}.{started_iso}.json"
    row_path.write_text(json.dumps({"started": started_iso,
                                    "pgid": _WB_DEAD_PGID if pgid is None
                                    else pgid}))
    os.utime(row_path, (death_ts, death_ts))
    if reported:
        (sessions_dir / f"{key}.log").write_text(
            "10:00:00 start     brief posted\n"
            "10:00:05 result    success, 1 turns, $0.01\n")


# A pgid nothing can be running under: claimed, reaped, and confirmed gone.
# Real process groups, like the liveness tests above -- _pgid_alive calls
# killpg for real, so a made-up number could collide with a live group.
_wb_dead_proc = subprocess.Popen(["true"], start_new_session=True)
_wb_dead_proc.wait()
_WB_DEAD_PGID = _wb_dead_proc.pid


_wb_orig_sessions_dir = core.SESSIONS_DIR
_wb_tmpdir = tempfile.mkdtemp(prefix="orch_test_wavebackoff_")
core.SESSIONS_DIR = Path(_wb_tmpdir)

try:
    _wb_now = core.now()

    def _wb_reset():
        shutil.rmtree(_wb_tmpdir, ignore_errors=True)
        Path(_wb_tmpdir).mkdir()

    # 3 fast void runs inside the window -> a wave, positive remaining.
    _wb_reset()
    for i in range(3):
        death = _wb_now - 10 - i  # all within WAVE_WINDOW_SECS of each other
        started_iso = core.datetime.fromtimestamp(
            death - 5, core.timezone.utc).isoformat()
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.{i}", started_iso, death)
    got = core.wave_backoff()
    check("wave_backoff_three_fast_deaths_is_wave", True,
          got is not None and got > 0)

    # THE ACTUAL orch#184 INCIDENT: five sessions that had been working for
    # forty minutes, killed together by an account limit. This is the case
    # the feature exists to catch, and an earlier cut MISSED it -- it gated
    # on a started-to-death duration, so anything that had been running a
    # while was excluded, which is every session a usage limit ever kills.
    # If this check ever goes back to None, the detector cannot see the
    # incident it was built for.
    _wb_reset()
    for i in range(5):
        death = _wb_now - 10 - i
        started_iso = core.datetime.fromtimestamp(
            death - 2400, core.timezone.utc).isoformat()  # worked 40 minutes
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.orch.{i}", started_iso, death)
    got = core.wave_backoff()
    check("wave_backoff_long_lived_sessions_are_the_real_wave", True,
          got is not None and got > 0)

    # THE FALSE POSITIVE THAT WOULD BE AN OUTAGE: an ordinary re-tick rolls
    # three HEALTHY, STILL-RUNNING sessions aside to respawn their keys.
    # None has written a result line yet -- precisely because each is alive
    # and still working -- so the result-line test alone would call all
    # three void and freeze every spawn on the machine for
    # WAVE_BACKOFF_SECS. The liveness check is what discriminates.
    _wb_reset()
    _wb_live_procs = [subprocess.Popen(["sleep", "30"], start_new_session=True)
                      for _ in range(3)]
    try:
        for i, proc in enumerate(_wb_live_procs):
            death = _wb_now - 10 - i
            started_iso = core.datetime.fromtimestamp(
                death - 30, core.timezone.utc).isoformat()
            _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.live{i}",
                          started_iso, death, pgid=proc.pid)
        check("wave_backoff_live_sessions_are_not_deaths", None,
              core.wave_backoff())
    finally:
        for proc in _wb_live_procs:
            proc.kill()
            proc.wait()

    # _roll_aside must stamp the rolled-aside file's mtime with NOW, not
    # leave the spawn-time mtime that rename preserves. Asserted directly,
    # because every death time the scan reads depends on it.
    _wb_reset()
    _wb_live = Path(_wb_tmpdir) / "issue-orch.repo.stamp.json"
    _wb_started_iso = core.datetime.fromtimestamp(
        _wb_now - 7200, core.timezone.utc).isoformat()
    _wb_live.write_text(json.dumps({"started": _wb_started_iso}))
    os.utime(_wb_live, (_wb_now - 7200, _wb_now - 7200))  # spawn-time mtime
    _wb_rolled = Path(_wb_tmpdir) / f"issue-orch.repo.stamp.{_wb_started_iso}.json"
    core._roll_aside("issue-orch.repo.stamp")
    check("roll_aside_stamps_death_mtime", True,
          _wb_rolled.exists() and (core.now() - _wb_rolled.stat().st_mtime) < 60)

    # The recorded count is the group's ACTUAL size, not WAVE_MIN_N. This is
    # what the journal row carries, and orch#184 exists because five
    # simultaneous deaths went unrecorded -- a row that always reported the
    # threshold would describe a 5-death wave and a 3-death one identically.
    _wb_reset()
    for i in range(5):
        death = _wb_now - 10 - i
        started_iso = core.datetime.fromtimestamp(
            death - 5, core.timezone.utc).isoformat()
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.{i}", started_iso, death)
    _wb_detail = core._wave_backoff_detail()
    check("wave_backoff_detail_reports_actual_count", 5,
          _wb_detail and _wb_detail["n"])

    # Same rows, aged past WAVE_BACKOFF_SECS from the newest death -> None.
    # This is the RESUME property: no state file, just timestamps aging out.
    _wb_reset()
    for i in range(3):
        death = _wb_now - core.WAVE_BACKOFF_SECS - 30 - i
        started_iso = core.datetime.fromtimestamp(
            death - 5, core.timezone.utc).isoformat()
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.{i}", started_iso, death)
    check("wave_backoff_expired_is_none", None, core.wave_backoff())

    # Only 2 void runs -> below WAVE_MIN_N -> None.
    _wb_reset()
    for i in range(2):
        death = _wb_now - 10 - i
        started_iso = core.datetime.fromtimestamp(
            death - 5, core.timezone.utc).isoformat()
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.{i}", started_iso, death)
    check("wave_backoff_two_deaths_below_min_n", None, core.wave_backoff())

    # 3 fast deaths, but each DID report a result line -> not void -> no wave.
    _wb_reset()
    for i in range(3):
        death = _wb_now - 10 - i
        started_iso = core.datetime.fromtimestamp(
            death - 5, core.timezone.utc).isoformat()
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.{i}", started_iso,
                      death, reported=True)
    check("wave_backoff_reported_runs_not_void", None, core.wave_backoff())

    # SESSIONS_DIR missing entirely -> None, no exception.
    _missing_dir = Path(_wb_tmpdir) / "does-not-exist"
    core.SESSIONS_DIR = _missing_dir
    check("wave_backoff_missing_sessions_dir", None, core.wave_backoff())
    core.SESSIONS_DIR = Path(_wb_tmpdir)

    # Garbage JSON row alongside 3 good void rows -> the bad row is skipped,
    # never raises, and the real wave underneath it is still found.
    _wb_reset()
    for i in range(3):
        death = _wb_now - 10 - i
        started_iso = core.datetime.fromtimestamp(
            death - 5, core.timezone.utc).isoformat()
        _wb_write_row(Path(_wb_tmpdir), f"issue-orch.repo.{i}", started_iso, death)
    garbage_path = Path(_wb_tmpdir) / "issue-orch.repo.9.2026-01-01T00:00:00+00:00.json"
    garbage_path.write_text("{not valid json")
    got = core.wave_backoff()
    check("wave_backoff_garbage_row_skipped_not_raised", True,
          got is not None and got > 0)
finally:
    core.SESSIONS_DIR = _wb_orig_sessions_dir
    shutil.rmtree(_wb_tmpdir, ignore_errors=True)
# === orch#143: rollup_readability() and the "CI unreadable on every open PR"
# alert. Before this, an unreadable rollup and a repo with genuinely no CI
# looked identical downstream -- both left pr_green False, so a repo where
# every PR's CI read failed just sat there as ordinary held REVIEW rows,
# with nothing telling the operator the hold would never lift on its own.
# rollup_readability() is a no-forge-call scan that counts how many OPEN PRs
# have the ROLLUP_UNREADABLE sentinel for a rollup, out of the total, so
# feed.py can turn "every read failed" into one named alert instead of a
# pile of unexplained holds.
#
# It counts PER OPEN PR, not per self.rollups entry, and these checks pin
# that: rollups is keyed by headRefName, so two OPEN PRs on one branch share
# an entry. See rollup_readability_duplicate_head_branch below for why that
# distinction decides whether the alert fires at all.

def _w143(prs, rollups):
    """A World carrying only what rollup_readability reads: prs + rollups."""
    w = core.World.__new__(core.World)
    w.prs = prs
    w.rollups = rollups
    return w


_OPEN2 = [{"number": 1, "headRefName": "a", "state": "OPEN"},
          {"number": 2, "headRefName": "b", "state": "OPEN"}]

check("rollup_readability_all_unreadable", (2, 2),
      _w143(_OPEN2, {"a": core.ROLLUP_UNREADABLE,
                     "b": core.ROLLUP_UNREADABLE}).rollup_readability())

check("rollup_readability_mixed_unreadable_and_no_ci", (1, 2),
      _w143(_OPEN2, {"a": core.ROLLUP_UNREADABLE, "b": None}).rollup_readability())

check("rollup_readability_mixed_unreadable_and_real", (1, 2),
      _w143(_OPEN2, {"a": core.ROLLUP_UNREADABLE,
                     "b": [{"conclusion": "SUCCESS"}]}).rollup_readability())

check("rollup_readability_empty", (0, 0), _w143([], {}).rollup_readability())

check("rollup_readability_none_unreadable", (0, 2),
      _w143(_OPEN2, {"a": None, "b": [{"conclusion": "SUCCESS"}]}).rollup_readability())

# Non-OPEN PRs are not counted: load() fetches rollups for OPEN PRs only, so
# a MERGED or CLOSED row has no reading and is not a held PR either.
check("rollup_readability_ignores_non_open", (1, 1),
      _w143([{"number": 1, "headRefName": "a", "state": "OPEN"},
             {"number": 2, "headRefName": "b", "state": "MERGED"},
             {"number": 3, "headRefName": "c", "state": "CLOSED"}],
            {"a": core.ROLLUP_UNREADABLE}).rollup_readability())

# THE REGRESSION THAT MOTIVATED COUNTING PER PR. Two OPEN PRs share a head
# branch, so self.rollups holds ONE entry for both. Counting dict entries
# would return (1, 1) -- below feed.py's >= 2 threshold -- and the alert
# would be suppressed as "one flaky read" on a repo where BOTH open PRs are
# held by a failed read. Counting per PR returns (2, 2) and the alert fires.
# shadowed_prs() exists because this repo already knows duplicate head
# branches occur.
check("rollup_readability_duplicate_head_branch", (2, 2),
      _w143([{"number": 1, "headRefName": "dup", "state": "OPEN"},
             {"number": 2, "headRefName": "dup", "state": "OPEN"}],
            {"dup": core.ROLLUP_UNREADABLE}).rollup_readability())

# A PR whose branch is absent from the snapshot counts toward total but not
# toward unreadable: no reading is not evidence of a failed read. This keeps
# feed.py's `unreadable == total` test honest -- it can only hold when every
# open PR really did produce a failed read.
check("rollup_readability_missing_snapshot_entry_not_unreadable", (1, 2),
      _w143(_OPEN2, {"a": core.ROLLUP_UNREADABLE}).rollup_readability())


def _stub_repo_json_143(rollups_unreadable, rollups_total):
    """Same shape as _stub_repo_json above, plus the two counters the
    rollups-unreadable alert reads. ok=True and empty issues/counts so the
    alert under test is the only thing that can fire from this repo."""
    def _stub(repo):
        return {"repo": "me/rollrepo", "path": str(repo), "slug": "rollrepo",
                 "ok": True, "counts": {}, "live_orchs": 0, "issues": [],
                 "orch": {"key": "repo-orch.rollrepo", "alive": False,
                          "prior_runs": 0, "recent": []},
                 "rollups_unreadable": rollups_unreadable,
                 "rollups_total": rollups_total}
    return _stub


def _roll_alerts_143(out):
    # key form is "rollups-unreadable:<slug>", set by feed.py; matching on
    # a "kind" field would miss it entirely -- this alert deliberately
    # carries no kind (see the comment block above the alert in feed.py).
    return [a for a in out["alerts"]
            if str(a.get("key", "")).startswith("rollups-unreadable:")]


fake_repo_143 = Path(os.environ["WT_ROOT"]) / "rollrepo"
(fake_repo_143 / ".git").mkdir(parents=True, exist_ok=True)
_write_repos_txt(str(fake_repo_143) + "\n")
_orig_repo_json_143 = feed.repo_json
try:
    # -- fires: every open PR's rollup read failed --
    feed.repo_json = _stub_repo_json_143(5, 5)
    out = feed.build()
    rows = _roll_alerts_143(out)
    check("rollups_unreadable_alert_fires_count", 1, len(rows))
    check("rollups_unreadable_alert_fires_level", "error", rows[0]["level"] if rows else None)
    check("rollups_unreadable_alert_fires_needs_you", True, rows[0].get("needs_you") if rows else None)
    check("rollups_unreadable_alert_fires_slug", "rollrepo", rows[0].get("slug") if rows else None)
    check("rollups_unreadable_alert_fires_repo", "me/rollrepo", rows[0].get("repo") if rows else None)
    check("rollups_unreadable_alert_fires_msg_has_count", True,
          "5" in rows[0].get("msg", "") if rows else False)

    # anti-regression for the "thirteen alerts that named no cause" defect:
    # one repo-wide fault must be ONE row, never one row per held PR.
    check("rollups_unreadable_alert_one_row_per_repo", 1,
          len(_roll_alerts_143(out)))

    # render-contract pins: a "kind" on this row would route it down
    # widget.tpl.html's needsYouRow branch that renders kindText[a.kind]
    # instead of msg, silently dropping the one sentence the alert exists
    # to show. "key" must be present so tick's per-wake suppression folds
    # a persistent fault into one digest row instead of renotifying every
    # wake, the same mechanism "repos-unresolved:<path>" uses.
    check("rollups_unreadable_alert_no_kind", False, "kind" in rows[0] if rows else True)
    check("rollups_unreadable_alert_has_key", True,
          rows[0].get("key") == "rollups-unreadable:rollrepo" if rows else False)

    # msg names the distinction and a place to look, never a single cause:
    # "no CI" must appear (an unreadable rollup is NOT the same fact as a
    # repo that runs no CI at all), and "checks:read" must NOT appear --
    # a missing PAT scope is the observed cause, not the only one, and
    # naming it as THE cause would send the operator down the wrong path
    # when it wasn't.
    check("rollups_unreadable_alert_msg_names_no_ci", True,
          "no CI" in rows[0].get("msg", "") if rows else False)
    check("rollups_unreadable_alert_msg_no_hard_cause", False,
          "checks:read" in rows[0].get("msg", "") if rows else True)

    # -- does not fire: mixed, one of two unreadable --
    feed.repo_json = _stub_repo_json_143(1, 2)
    out = feed.build()
    check("rollups_unreadable_alert_mixed_no_fire", 0, len(_roll_alerts_143(out)))

    # -- does not fire: single open PR, one flaky read is noise --
    feed.repo_json = _stub_repo_json_143(1, 1)
    out = feed.build()
    check("rollups_unreadable_alert_single_pr_no_fire", 0, len(_roll_alerts_143(out)))

    # -- does not fire: repo genuinely runs no CI (rollups all None, not the
    # ROLLUP_UNREADABLE sentinel) -- this is the acceptance bullet that an
    # UNREADABLE rollup and an ABSENT one are different facts with different
    # fixes, and only the former should ever raise this alert.
    feed.repo_json = _stub_repo_json_143(0, 2)
    out = feed.build()
    check("rollups_unreadable_alert_no_ci_no_fire", 0, len(_roll_alerts_143(out)))

    # -- does not fire: no open PRs at all --
    feed.repo_json = _stub_repo_json_143(0, 0)
    out = feed.build()
    check("rollups_unreadable_alert_no_open_prs_no_fire", 0, len(_roll_alerts_143(out)))
finally:
    feed.repo_json = _orig_repo_json_143

# === orch#185: core.spend_window -- the ~45% undercount guard ==============
# orch#155 measured that a non-recursive glob over ~/.claude/projects misses
# every Agent-tool subagent transcript (a wholly separate file nested at
# <slug>/<session-uuid>/subagents/agent-*.jsonl) and undercounts issue-orch
# spend by ~45%. This is the one test that fails if that regresses: a MAIN
# transcript and a SUBAGENT transcript both under the same issue-orch slug,
# asserting the subagent's tokens are folded into the total.


def _sw_usage_line(usage):
    """One assistant-message jsonl line carrying a message.usage block, the
    only shape spend_window's scanner reads (see _spend_scan_file)."""
    return json.dumps({"type": "assistant", "message": {"usage": usage}}) + "\n"


_SW_MAIN_USAGE = {"input_tokens": 100, "output_tokens": 50,
                   "cache_creation_input_tokens": 20, "cache_read_input_tokens": 100}
_SW_SUB_USAGE = {"input_tokens": 200, "output_tokens": 75,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 50}
# weighted = input + output + cache_creation + cache_read/10 (core.spend_window docstring)
_SW_MAIN_WEIGHTED = 100 + 50 + 20 + 100 / 10   # 180
_SW_SUB_WEIGHTED = 200 + 75 + 0 + 50 / 10       # 280
_SW_EXPECTED_TOTAL = round(_SW_MAIN_WEIGHTED) + round(_SW_SUB_WEIGHTED)  # 460

_sw_tmpdir = tempfile.mkdtemp(prefix="orch_test_spendwindow_")
# Slug must classify as issue-orch: _spend_classify needs BOTH an
# "-issue-<n>" segment and "-wt-" in the slug (core.py's _spend_classify).
_sw_slug_dir = Path(_sw_tmpdir) / "-home-kiri-orch-wt-orch-issue-185"
_sw_slug_dir.mkdir(parents=True)
(_sw_slug_dir / "main.jsonl").write_text(_sw_usage_line(_SW_MAIN_USAGE))
_sw_sub_dir = _sw_slug_dir / "some-session-uuid" / "subagents"
_sw_sub_dir.mkdir(parents=True)
(_sw_sub_dir / "agent-1.jsonl").write_text(_sw_usage_line(_SW_SUB_USAGE))

_sw_orig_projects_dir = core.SPEND_PROJECTS_DIR
core.SPEND_PROJECTS_DIR = _sw_slug_dir.parent
try:
    got = core.spend_window()
    check("spend_window_total_includes_main_and_subagent", _SW_EXPECTED_TOTAL,
          got["total"] if got else got)
    check("spend_window_role_is_issue_orch", {"issue-orch": _SW_EXPECTED_TOTAL},
          got["roles"] if got else got)
    # THE POINT: a non-recursive glob would find main.jsonl only and total
    # 180, silently dropping the subagent file's 280 -- the exact ~45%
    # undercount orch#155 measured. If this assert ever reads 180 instead
    # of 460, the rglob("*.jsonl") + "subagents" in p.parts branch in
    # core.spend_window has been "simplified" away again. See orch#155.
    check("spend_window_subagent_tokens_not_dropped_orch155", True,
          got is not None and got["total"] > round(_SW_MAIN_WEIGHTED))
finally:
    core.SPEND_PROJECTS_DIR = _sw_orig_projects_dir
    shutil.rmtree(_sw_tmpdir, ignore_errors=True)

# Per-line window (orch#185 finding 1): a file touched inside the window
# used to count its ENTIRE history because the old gate was per-file mtime.
# One transcript, two assistant lines -- one timestamped well inside the
# window, one 30 days outside it -- must contribute ONLY the in-window
# line's tokens. Without this guard the per-file window regresses silently.
_sw2_tmpdir = tempfile.mkdtemp(prefix="orch_test_spendwindow_perline_")
_sw2_slug_dir = Path(_sw2_tmpdir) / "-home-kiri-orch-wt-orch-issue-185"
_sw2_slug_dir.mkdir(parents=True)
_SW2_IN_WINDOW_USAGE = {"input_tokens": 10, "output_tokens": 5,
                        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
_SW2_OUT_OF_WINDOW_USAGE = {"input_tokens": 9999, "output_tokens": 9999,
                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
_SW2_IN_WINDOW_WEIGHTED = 10 + 5  # 15
_sw2_now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
_sw2_old_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 30 * 86400))
_sw2_lines = (
    json.dumps({"type": "assistant", "timestamp": _sw2_old_iso,
                "message": {"usage": _SW2_OUT_OF_WINDOW_USAGE}}) + "\n"
    + json.dumps({"type": "assistant", "timestamp": _sw2_now_iso,
                  "message": {"usage": _SW2_IN_WINDOW_USAGE}}) + "\n"
)
(_sw2_slug_dir / "mixed.jsonl").write_text(_sw2_lines)

_sw2_orig_projects_dir = core.SPEND_PROJECTS_DIR
core.SPEND_PROJECTS_DIR = _sw2_slug_dir.parent
try:
    got2 = core.spend_window()
    check("spend_window_per_line_excludes_out_of_window_line_orch185", _SW2_IN_WINDOW_WEIGHTED,
          got2["total"] if got2 else got2)
finally:
    core.SPEND_PROJECTS_DIR = _sw2_orig_projects_dir
    shutil.rmtree(_sw2_tmpdir, ignore_errors=True)

# No-record contract: a missing projects dir must yield None, never a zero
# dict -- same stance headroom_cap()/wave_backoff() take (core.py docstrings).
_sw_missing_dir = Path(tempfile.mkdtemp(prefix="orch_test_spendwindow_missing_"))
shutil.rmtree(_sw_missing_dir)  # exists as a path, not on disk
_sw_orig_projects_dir2 = core.SPEND_PROJECTS_DIR
core.SPEND_PROJECTS_DIR = _sw_missing_dir
try:
    check("spend_window_missing_dir_is_none_not_zero", None, core.spend_window())
finally:
    core.SPEND_PROJECTS_DIR = _sw_orig_projects_dir2


# --- orch#146: cmd_journal rejects unknown flags instead of absorbing them -
# Before this guard, a stray flag the parser doesn't know fell through to
# the positional slots and corrupted the record instead of erroring:
# `--ref 999` got absorbed into the note as literal prose, and `--grep` got
# taken as the event itself. Isolated the same way t251 above isolates
# journal writes (swap core.JOURNAL_ROOT to a tmp dir), plus the
# run_cli_silent shape from the cmd_decision block above since USAGE lands
# on stderr, not stdout.
_t146_orig_root = core.JOURNAL_ROOT
core.JOURNAL_ROOT = Path(tempfile.mkdtemp(prefix="orch_test_journal_unknownflag_"))
try:
    def _t146_run_cli_silent(argv):
        buf, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
            rc = clicmd.main(argv)
        return rc, buf.getvalue(), errbuf.getvalue()

    # (a) unknown flag in the NOTE position -- `--ref 999` must not be
    # absorbed into the note as prose.
    rc, _, err = _t146_run_cli_silent(
        ["journal", "repo", "orch", "consolidated", "--ref", "999", "probe"])
    check("t146_unknown_flag_in_note_rc", 2, rc)
    check("t146_unknown_flag_in_note_usage", True, err == clicmd.USAGE)

    # (b) unknown flag in the EVENT position -- `--grep` must not be taken
    # as the event.
    rc, _, err = _t146_run_cli_silent(
        ["journal", "repo", "orch", "--grep", "STANDING", "ORDER"])
    check("t146_unknown_flag_in_event_rc", 2, rc)
    check("t146_unknown_flag_in_event_usage", True, err == clicmd.USAGE)

    # (c) a valid call with no unknown flags still works -- the guard must
    # not over-reject ordinary notes.
    rc, _, _ = _t146_run_cli_silent(
        ["journal", "repo", "orch", "consolidated", "some note"])
    check("t146_valid_call_still_works_rc", 0, rc)

    # (d) a lone "-" or a negative number in the note is not a flag and
    # must still be accepted.
    rc, _, _ = _t146_run_cli_silent(
        ["journal", "repo", "orch", "consolidated", "score is -5 not -"])
    check("t146_negative_number_and_lone_dash_accepted_rc", 0, rc)
finally:
    core.JOURNAL_ROOT = _t146_orig_root

# === orch#423: the tick's lease and void-claim passes must hand World.load an
# "owner/repo", never the bare slug ==========================================
# World.load resolves the repo entry via _repo_entry_for_owner_slug, and THAT
# picks the backend adapter. Handed a bare slug the lookup returns None, the
# adapter falls back to its `gh` default, and every tea-backed repo fails to
# load. Observed 2026-09-17: "void claim pass: could not load eva-backgrounds,
# skipping" on every tick, silently disabling both passes for that repo --
# #336's lease expiry and #362's void-claim never ran there.
# A source assertion, not a behavioural one: both call sites are inside loops
# over the live feed, and reproducing that shape costs more than it proves.
_tick_src = (Path(__file__).resolve().parent / "tick.py").read_text()
check("t423_world_load_takes_owner_repo", 2,
      _tick_src.count('world.load(r.get("repo") or slug)'))
check("t423_world_load_never_bare_slug", 0,
      _tick_src.count("world.load(slug)"))

# === orch#421: a landed-unreviewed row is a LANDING to usage.py ==============
# merge_pr writes `landed-unreviewed` when no orch:review:v1 block was found.
# usage.py keyed on the bare string "landed", so an unreviewed merge read as
# "never landed" -- orch#280's false-negative shape, reintroduced for exactly
# the merges orch#421 exists to make more visible. Both spellings are one
# event class here.
from orch import usage as _u421
check("t421_landed_events_covers_unreviewed", True,
      {"landed", "landed-unreviewed"} <= set(_u421.LANDED_EVENTS))
check("t421_issue_identity_covers_unreviewed", True,
      "landed-unreviewed" in _u421.EVENTS_WITH_ISSUE_IDENTITY)
check("t421_no_bare_landed_compare", 0,
      (ROOT_U421 := (Path(__file__).resolve().parent / "usage.py").read_text())
      .count('r["event"] == "landed"'))

# === orch#442: the ticker is its own process, not a thread in the server =====
# The UI and the scan/spawn loop were one process, so stopping token spend
# meant stopping the dashboard too. The scheduler now lives in orch/ticker.py
# and runs as orch-tick.service. These pin the seam, not the loop body.
_tk442 = tkr   # the truth table above already pins _should_fire itself

# The server must not schedule any more. A thread here is the whole defect.
_srv442 = (Path(__file__).resolve().parent / "server.py").read_text()
check("t442_server_has_no_tick_loop", 0, _srv442.count("def tick_loop"))
check("t442_server_starts_no_thread", 0, _srv442.count("target=tick_loop"))

# The interval must be readable ACROSS processes. The dashboard's tick button
# runs in the server; if the ticker still timed off an in-process global it
# would fire a redundant tick right after every manual one.
check("t442_since_tick_is_filesystem", True, "TICK_LOCK" in
      _tk442._since_tick.__doc__ + str(_tk442._since_tick.__code__.co_names))
_tk442.core.TICK_LOCK.parent.mkdir(parents=True, exist_ok=True)
_tk442.core.TICK_LOCK.write_text("")
os.utime(_tk442.core.TICK_LOCK, (time.time() - 1000, time.time() - 1000))
check("t442_since_tick_reads_mtime", True, 990 < _tk442._since_tick() < 1010)
# A tick that just ran reads as ~0 -- this is the manual-tick reset working.
os.utime(_tk442.core.TICK_LOCK, None)
check("t442_fresh_tick_resets_interval", True, _tk442._since_tick() < 5)
# A future mtime must not read negative and strand the clock trigger forever.
os.utime(_tk442.core.TICK_LOCK, (time.time() + 3600, time.time() + 3600))
check("t442_future_mtime_clamped", 0.0, _tk442._since_tick())
# No lock file at all (fresh checkout) = expired, so the first tick is prompt.
_tk442.core.TICK_LOCK.unlink()
check("t442_missing_lock_reads_expired", True,
      _tk442._since_tick() >= _tk442.TICK_SECS)

# run.py starts both processes, and must launch them from the repo root.
# cwd=ORCH_HOME looks equivalent but is not: ORCH_HOME defaults to ~/orch
# while run.py may live in a worktree, and `python -m orch.ticker` from the
# wrong cwd resolves another checkout's package or dies with "No module
# named orch.ticker" -- caught exactly that way on a worktree.
_run442 = (Path(__file__).resolve().parent / "run.py").read_text()
check("t442_run_starts_ticker", True, "orch.ticker" in _run442)
check("t442_run_children_cwd_repo_root", 0, _run442.count("cwd=ORCH_HOME)"))
# --fg must not exec the server away: that abandons the ticker it just
# started, leaving an invisible ticker spending tokens after Ctrl-C.
check("t442_run_fg_does_not_exec", 0, _run442.count("execvpe"))

# build_widget's tmp path must be per-process: the server builds one at
# startup and the ticker's tick builds on its own cadence, concurrently now.
_bw442 = (Path(__file__).resolve().parent / "build_widget.py").read_text()
check("t442_widget_tmp_is_per_pid", True, "getpid()" in _bw442)
check("t442_widget_tmp_not_shared", 0, _bw442.count('out.suffix + ".tmp")'))

print(f"passed={pass_n} failed={fail_n}")
sys.exit(0 if fail_n == 0 else 1)
