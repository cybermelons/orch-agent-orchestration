#!/usr/bin/env python3
"""Self-check for usage.py.

Same shape as test_core.py: flat script, hand-rolled check(), ORCH_HOME/
WT_ROOT pointed at a tempdir before importing core (and here, usage).

Run: python3 -m orch.test_usage   (from the repo root)
"""
import json
import os
import shutil
import sys
import tempfile
import zlib
from pathlib import Path

T = Path(tempfile.mkdtemp())

# The idle tests window by timestamp, so they get a base year of their own
# derived from the tempdir name rather than a fixed wall-clock date. Since
# orch#370 the fixtures live under T (see TRANSCRIPTS below) and a crashed
# run leaves nothing behind to collide with, so this is belt-and-braces
# now -- but it also keeps the windows away from the real clock, which is
# what stops a fixture timestamp ever being read as a real turn. Any year
# is fine; nothing here compares against the real clock.
_YEAR = 2100 + (zlib.crc32(T.name.encode()) % 500)  # crc32, not hash(): PYTHONHASHSEED
def _ts(hhmmss, day="16"):
    return f"{_YEAR}-09-{day}T{hhmmss}Z"

os.environ["ORCH_HOME"] = str(T / "orch")
os.environ["WT_ROOT"] = str(T / "wt")
Path(os.environ["ORCH_HOME"]).mkdir(parents=True)
Path(os.environ["WT_ROOT"]).mkdir(parents=True)

sys.path.insert(0, str(Path(__file__).parent.parent))
import importlib
from orch import core
importlib.reload(core)  # pick up env vars set above
from orch import usage
importlib.reload(usage)

pass_n = 0
fail_n = 0


def check(name, want, got):
    global pass_n, fail_n
    if want == got:
        pass_n += 1
    else:
        fail_n += 1
        print(f"FAIL {name}: want [{want}] got [{got}]")


# === fixtures ================================================================

SESSIONS = core.SESSIONS_DIR
SESSIONS.mkdir(parents=True, exist_ok=True)

REPOS = core.JOURNAL_ROOT / "repos"

# Transcript fixtures live in a tree THIS RUN OWNS, under the per-run
# tempdir, and every transcript/idle assertion below passes TRANSCRIPTS as
# usage's `root`.
#
# They used to be written into the real shared ~/.claude/projects and the
# skip-counter checks took a before/after delta over it. That tree is
# written to by every live Claude Code session on this machine, so the
# suite was reading a moving target twice and subtracting: a
# partially-flushed line counts as a skip in the baseline and differently
# in the second read, and the delta can come out 0 with nothing wrong.
# Measured at 58/2 and 59/1 under concurrency against 60/0 quiet, same code
# (orch#370). The mitigation there was `>=`, which survives another session
# ADDING turns but not the baseline itself being captured mid-write -- the
# shared tree was the defect, not the comparison.
#
# Directory names mirror core.session_dir()'s encoding so the fixture tree
# has the real tree's shape; only the parent differs.
TRANSCRIPTS = T / "transcripts"
TRANSCRIPTS.mkdir(parents=True, exist_ok=True)


def write_transcript(cwd, filename, lines):
    d = TRANSCRIPTS / core.session_dir(cwd).name
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    with f.open("w") as fh:
        for l in lines:
            fh.write(json.dumps(l) + "\n")
    return f


def write_ledger(key, role, scope, workdir, started, historical_suffix=None):
    name = f"{key}.{historical_suffix}.json" if historical_suffix else f"{key}.json"
    row = {"role": role, "scope": list(scope), "workdir": str(workdir),
           "pgid": 123, "started": started, "log": str(SESSIONS / f"{key}.log")}
    (SESSIONS / name).write_text(json.dumps(row))


def write_journal(repo, rows):
    d = REPOS / repo
    d.mkdir(parents=True, exist_ok=True)
    f = d / "orch.jsonl"
    with f.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def turn(role, cwd, ts, session_id="s1"):
    return {"type": role, "message": {"role": role}, "cwd": cwd,
            "timestamp": ts, "sessionId": session_id}


# --- ledger_rows: current + rolled-aside history -----------------------------

agent_cwd = T / "myrepo" / "issue-7"
agent_cwd_old = T / "myrepo" / "issue-7-old"
human_cwd = T / "humandir"

write_ledger("issue-orch.myrepo.7", "issue-orch", ("myrepo", "7"),
             agent_cwd, "2026-09-16T10:00:00-04:00")
write_ledger("issue-orch.myrepo.7", "issue-orch", ("myrepo", "7"),
             agent_cwd_old, "2026-09-15T10:00:00-04:00",
             historical_suffix="2026-09-15T10:00:00-04:00")
(SESSIONS / "issue-orch.myrepo.7.settings.json").write_text("{}")

rows = usage.ledger_rows()
by_key_current = [r for r in rows if r["key"] == "issue-orch.myrepo.7" and not r["historical"]]
by_key_hist = [r for r in rows if r["historical"]]
check("ledger_current_count", 1, len(by_key_current))
check("ledger_historical_count", 1, len(by_key_hist))
check("ledger_settings_skipped", 0, len([r for r in rows if "settings" in r["key"]]))
check("ledger_scope_join", ["myrepo", "7"], by_key_current[0]["scope"])
check("ledger_repo_issue", ("myrepo", "7"), (by_key_current[0]["repo"], by_key_current[0]["issue"]))

# === classify(): all three outcomes, especially unknown =====================

workdirs = {r["workdir"] for r in rows if r["workdir"]}

check("classify_agent_ledger_match", "agent",
      usage.classify(str(agent_cwd), workdirs))
check("classify_agent_substring_second", "agent",
      usage.classify("/home/user/orch/wt/otherrepo-wt-9", workdirs))
# real measured case from the plan: a genuine agent dir that matches
# neither the ledger nor the path substring -- classifies as "human" by
# default, exactly the silent-misclassification the plan measured against
# the OLD heuristic. This module cannot fix that without a better agent
# signal; it is named here as the residual gap, not asserted away.
check("classify_default_is_human_not_unknown",
      "human",
      usage.classify("/home/user/.claude/projects/-home-kiri-orch-state-dashboard-op", workdirs))
check("classify_unknown_none_cwd", "unknown", usage.classify(None, workdirs))
check("classify_human_plain_home", "human",
      usage.classify(str(human_cwd), workdirs))

# === journal_rows(): both schemas read, counted separately ==================

write_journal("myrepo", [
    {"at": "2026-09-16T09:00:00-04:00", "actor": "tick", "event": "observed", "issues": []},
    {"at": "2026-09-14T20:14:01-04:00", "level": "repo", "repo": "myrepo",
     "action": "deferred", "summary": "legacy row"},
    {"at": "2026-09-16T09:05:00-04:00", "actor": "issue-orch", "event": "landed",
     "issue": 7, "pr": 42},
])
j_rows, j_counts = usage.journal_rows(repo="myrepo")
check("journal_current_count", 2, j_counts["current_schema"])
check("journal_legacy_count", 1, j_counts["legacy_schema"])
legacy_read = [r for r in j_rows if r["event"] == "deferred"]
check("journal_legacy_row_read_not_dropped", 1, len(legacy_read))
check("journal_legacy_actor_from_level", "repo", legacy_read[0]["actor"])

# The event vocabulary moves under this reader in BOTH directions, so it
# keeps no allowlist and special-cases exactly one event (`landed`).
# Appearing: orch#125 landed `nudged`/`asked` (core.py:2955, core.py:2969).
# Vanishing: orch#198 removes the escalation object, so `escalated` may stop
# being written at all. A reader that enumerated known events would drop the
# new ones silently and would have to be edited for the removal; this one
# passes both through as ordinary rows and never needs to know the list.
write_journal("evolvingrepo", [
    {"at": "2026-09-16T01:00:00-04:00", "actor": "operator", "event": "nudged"},
    {"at": "2026-09-16T01:01:00-04:00", "actor": "operator", "event": "asked",
     "note": "hi"},
    {"at": "2026-09-16T01:02:00-04:00", "actor": "repo-orch",
     "event": "totally-unknown-future"},
])
ev_rows, ev_counts = usage.journal_rows(repo="evolvingrepo")
check("journal_unknown_events_all_read", 3, len(ev_rows))
check("journal_unknown_events_none_unparseable", 0, ev_counts["unparseable"])
check("journal_new_event_nudged_survives", 1,
      len([r for r in ev_rows if r["event"] == "nudged"]))
check("journal_new_event_asked_survives", 1,
      len([r for r in ev_rows if r["event"] == "asked"]))
check("journal_future_event_survives", 1,
      len([r for r in ev_rows if r["event"] == "totally-unknown-future"]))
# No `escalated` row exists in this journal at all -- the orch#198 end state.
# Reading it must be ordinary, not an error and not an empty result.
check("journal_no_escalated_rows_is_fine", 0,
      len([r for r in ev_rows if r["event"] == "escalated"]))

# a genuinely unparseable line, counted not dropped
(REPOS / "myrepo" / "orch.jsonl").open("a").write("not json at all\n")
j_rows2, j_counts2 = usage.journal_rows(repo="myrepo")
check("journal_unparseable_counted", 1, j_counts2["unparseable"])
check("journal_rows_unaffected_by_bad_line", 3, len(j_rows2))

# === transcript_turns(): unparseable timestamp lands in skipped, not silent =
#
# Read against TRANSCRIPTS, a tree only this run writes to, so each skip
# counter is an EXACT count of the fixture's own bad lines. No baseline and
# no subtraction: there is no concurrent writer to survive (orch#370).
write_transcript(agent_cwd, "sess1.jsonl", [
    turn("user", str(agent_cwd), "2026-09-16T10:00:00.000Z"),
    turn("assistant", str(agent_cwd), "2026-09-16T10:00:05.000Z"),
    turn("user", str(agent_cwd), "not-a-timestamp"),
    {"type": "system", "message": {"role": "system"}, "cwd": str(agent_cwd),
     "timestamp": "2026-09-16T10:00:10.000Z"},  # not a turn
    "not even json",
])
turns, skipped = usage.transcript_turns(root=TRANSCRIPTS)
fixture_turns = [t for t in turns if t["cwd"] == str(agent_cwd)]
check("transcript_turns_count", 2, len(fixture_turns))
# Exact, not ">= 1". Each of these is the whole fixture's count, which also
# catches a line counted TWICE -- something the old delta could not see.
check("transcript_bad_timestamp_counted", 1, skipped["bad_timestamp"])
check("transcript_not_a_turn_counted", 1, skipped["not_a_turn"])
check("transcript_bad_json_counted", 1, skipped["bad_json"])

# --- the default root still reads the REAL tree ------------------------------
#
# Pointing the assertions above at a fixture tree buys determinism but drops
# the one thing the old shared-tree version did prove: that the default root
# resolves and parses the real ~/.claude/projects layout. This keeps that,
# and ONLY that.
#
# It asserts on real data (roles actually read off the live tree) rather than
# on row/skip KEY SETS: those are fixed by the dict literals in
# transcript_turns() and hold whatever the tree contains, so checking them
# would pass by construction and prove nothing about the layout.
#
# Nothing here depends on a count, so no concurrent writer can move it, and
# an empty real tree (fresh machine, or CI) is a legitimate result rather
# than a failure.
#
# Bounded by SAMPLING the real tree rather than walking it: measured 4567
# files / 17s for the full walk on a working machine, and since/until filter
# AFTER each file is read they do not bound the I/O at all. A few real files
# prove the layout parses just as well as all of them, on a gate that runs
# every landing. The sample is copied into a directory of its own so the
# read is of a snapshot nothing is writing to.
_sample_root = T / "real_sample"
(_sample_root / "sampled").mkdir(parents=True, exist_ok=True)
for _src in usage._transcript_files()[:5]:
    try:
        shutil.copy(_src, _sample_root / "sampled" / _src.name)
    except OSError:
        pass
_real_rows, _real_skipped = usage.transcript_turns(root=_sample_root)
check("transcript_default_root_roles_are_turns", True,
      all(r["role"] in ("user", "assistant") for r in _real_rows))
check("transcript_default_root_rows_have_epoch", True,
      all(isinstance(r["epoch"], float) for r in _real_rows))

# === idle_windows(): gap exactly at threshold boundary (off-by-one) =========

# human_cwd is neither a ledger workdir nor an agent-path substring, so
# classify() reads it "human" -- exactly the cwd idle_windows must count.
# Second session's first turn lands EXACTLY 10:00 after the first session's
# last turn: a 10-minute gap must count as idle at threshold_min=10 (the
# ">=" boundary), and must NOT count at threshold_min=11. Windowed tight
# (since/until) around the fixture so real-machine turns outside it don't
# leak into the gap list -- idle_windows walks the real transcript tree the
# same as transcript_turns(), see the note above.
write_transcript(human_cwd, "sess2.jsonl", [
    turn("user", str(human_cwd), _ts("11:00:00.000"), "s2"),
    turn("assistant", str(human_cwd), _ts("11:00:05.000"), "s2"),
    turn("user", str(human_cwd), _ts("11:10:05.000"), "s2"),  # +10:00 gap
])
# Bounds hug the fixture turns (1 min either side) so the leading and
# trailing edge gaps stay under threshold and only the between-turns gap
# is measured here; the edges get their own test below.
_since, _until = _ts("10:59:00"), _ts("11:11:00")
result_at = usage.idle_windows(threshold_min=10, since=_since, until=_until,
                               root=TRANSCRIPTS)
_between = [g for g in result_at["gaps"] if g["edge"] is None]
check("idle_gap_at_exact_threshold_counted", 1, len(_between))
if _between:
    check("idle_gap_minutes", 10.0, _between[0]["minutes"])

result_over = usage.idle_windows(threshold_min=11, since=_since, until=_until,
                                 root=TRANSCRIPTS)
check("idle_gap_just_over_threshold_excluded", 0,
      len([g for g in result_over["gaps"] if g["edge"] is None]))

# === idle_windows(): the window's EDGES are idle too ========================
#
# Gaps strictly between observed turns miss the half of the question that
# matters most. Asked "how idle was 09:00-11:30" about a window whose only
# human turns are at 11:00-11:10, between-turns-only reports a single
# 10-minute gap and calls ~2h of dead morning "busy". The issue's headline
# is a percentage OF A WINDOW, so the edges have to be in it.
_e_since, _e_until = _ts("09:00:00"), _ts("11:30:00")
edged = usage.idle_windows(threshold_min=15, since=_e_since, until=_e_until,
                           root=TRANSCRIPTS)
_lead = [g for g in edged["gaps"] if g["edge"] == "leading"]
_trail = [g for g in edged["gaps"] if g["edge"] == "trailing"]
check("idle_leading_edge_counted", 1, len(_lead))
check("idle_leading_edge_minutes", 120.0, _lead[0]["minutes"] if _lead else None)
check("idle_trailing_edge_counted", 1, len(_trail))
check("idle_trailing_edge_minutes", round(19.0 + 55 / 60.0, 6),
      round(_trail[0]["minutes"], 6) if _trail else None)

# A bounded window with NO human turns at all is 100% idle, not "busy".
# Between-turns-only returned [] here, indistinguishable from a full window.
empty = usage.idle_windows(threshold_min=5, since=_ts("03:00:00", day="17"),
                           until=_ts("05:00:00", day="17"), root=TRANSCRIPTS)
check("idle_empty_window_is_all_idle", 1, len(empty["gaps"]))
check("idle_empty_window_minutes", 120.0,
      empty["gaps"][0]["minutes"] if empty["gaps"] else None)
check("idle_empty_window_reports_zero_turns", 0, empty["human_turns"])

# a turn with no cwd at all is the "unknown" bucket -- must be counted, and
# must not silently become either a human or an agent turn.
write_transcript(human_cwd, "sess3.jsonl", [
    {"type": "user", "message": {"role": "user"}, "cwd": None,
     "timestamp": _ts("13:00:00.000"), "sessionId": "s3"},
])
result_unknown = usage.idle_windows(
    threshold_min=10, since=_ts("12:59:00"),
    until=_ts("13:01:00"), root=TRANSCRIPTS)
check("idle_unknown_counted", 1, result_unknown["unknown_count"])

# === around_merge(): missing landed row reported, never fabricated =========

write_journal("prrepo", [
    {"at": "2026-09-16T05:00:00-04:00", "actor": "issue-orch", "event": "started",
     "note": "before"},
    {"at": "2026-09-16T05:08:40-04:00", "actor": "issue-orch", "event": "observed",
     "note": "merge happened here, no landed row -- orch#280's defect"},
])
res_missing = usage.around_merge("prrepo", 288)
check("around_merge_missing_landed_is_none", None, res_missing["landed"])
check("around_merge_missing_has_reason", True,
      isinstance(res_missing["reason"], str) and len(res_missing["reason"]) > 0)

write_journal("prrepo", [
    {"at": "2026-09-16T06:00:00-04:00", "actor": "issue-orch", "event": "landed",
     "issue": 9, "pr": 301},
])
res_found = usage.around_merge("prrepo", 301)
check("around_merge_found_landed", "landed", res_found["landed"]["event"])
check("around_merge_found_no_reason", None, res_found["reason"])

# A since/until bound may arrive as an ISO string OR as epoch seconds from
# time.time(). core._at_epoch() returns None for a float, and None means "no
# bound" -- so before _bound() existed, a float bound silently disabled all
# filtering and idle_windows reported 89707% of a 32.6h window. Failing open
# on a bound the caller explicitly passed is the failure mode to keep dead.
check("bound_none", None, usage._bound(None))
check("bound_iso", 1789538848.0, usage._bound("2026-09-16T02:07:28-04:00"))
check("bound_epoch_float", 1789538848.0, usage._bound(1789538848.0))
check("bound_epoch_int", 1789538848.0, usage._bound(1789538848))
check("bound_iso_and_epoch_agree", usage._bound("2026-09-16T02:07:28-04:00"),
      usage._bound(1789538848.0))

# session_dir() maps into the real ~/.claude/projects (see write_transcript);
# every dir this run created is under T, so it is ours alone -- remove it,
# === an unparseable bound RAISES, it does not quietly widen the window =====
#
# _bound() returns None both for "no bound given" and "could not parse". Left
# unchecked, window() split against itself: its two comprehensions dropped
# every session and journal row while transcript_turns() skipped its own
# bound check and returned every turn ever recorded -- "nothing ran in this
# window, but you typed 40,000 turns", with nothing saying the bound was bad.
_raised = None
try:
    usage.window("09/16/2026", "09/17/2026")
except ValueError as e:
    _raised = str(e)
check("window_bad_bound_raises", True, _raised is not None)
check("window_bad_bound_names_the_value", True,
      _raised is not None and "09/16/2026" in _raised)

_raised_t = None
try:
    usage.transcript_turns(since="not a timestamp")
except ValueError as e:
    _raised_t = str(e)
check("transcript_turns_bad_bound_raises", True, _raised_t is not None)
# A good bound still works -- the guard fires on unparseable, not on present.
check("window_good_bound_ok", True,
      isinstance(usage.window("2026-09-16T00:00:00Z",
                              "2026-09-16T23:59:59Z"), dict))

# === around_merge(): identity, not "first row for this repo" ===============

write_journal("amrepo", [
    {"at": "2026-09-16T02:00:00-04:00", "actor": "issue-orch",
     "event": "landed", "issue": 77, "pr": 555},
    # a landed row carrying NO issue -- no session can honestly be matched
    {"at": "2026-09-16T03:00:00-04:00", "actor": "issue-orch",
     "event": "landed", "pr": 556},
])
# A string PR number must find the same row an int does. The journal holds
# an int; a caller holding "555" from a URL or a `gh --json` pipeline got
# "no record" for a row sitting right there.
check("around_merge_pr_as_str_finds_row", 555,
      usage.around_merge("amrepo", "555")["landed"]["raw"]["pr"])
check("around_merge_pr_as_int_finds_row", 555,
      usage.around_merge("amrepo", 555)["landed"]["raw"]["pr"])

# Landed row with no issue -> no session, and a reason saying why. Matching
# on repo alone would hand back whatever glob() yielded first.
_noissue = usage.around_merge("amrepo", 556)
check("around_merge_no_issue_returns_no_session", None, _noissue["session"])
check("around_merge_no_issue_gives_reason", True,
      isinstance(_noissue["session_reason"], str))

# A landed row that EXISTS but cannot be placed in time is not "no record".
write_journal("badat", [
    {"at": "", "actor": "issue-orch", "event": "landed", "issue": 8,
     "pr": 900},
])
_unread = usage.around_merge("badat", 900)
check("around_merge_unreadable_landed_is_not_absent", True,
      "unreadable" in (_unread["reason"] or "")
      or "EXISTS" in (_unread["reason"] or ""))
check("around_merge_unreadable_landed_returned", 900,
      (_unread.get("landed_row_unreadable") or {}).get("raw", {}).get("pr"))

# === classify(): `worktree` matches a path SEGMENT, not any substring ======
#
# A human directory merely containing the word was labelled agent, which
# deletes real typing from human_turns and extends an idle gap across it.
check("classify_human_dir_containing_word", "human",
      usage.classify("/home/user/src/worktree-manager", set()))
check("classify_real_worktree_segment", "agent",
      usage.classify("/home/user/.claude/worktrees/abc", set()))
check("classify_wt_marker_segment", "agent",
      usage.classify("/home/user/orch/wt/orch/issue-298-wt-x", set()))
check("classify_ledger_match_still_wins", "agent",
      usage.classify("/some/plain/dir", {"/some/plain/dir"}))

# Leave no litter. Since orch#370 the transcript fixtures are inside T with
# everything else, so removing T is the whole cleanup -- and a crashed run
# now litters only a tempdir the OS reclaims, never the real
# ~/.claude/projects tree this suite used to write into.
shutil.rmtree(T, ignore_errors=True)

print(f"passed={pass_n} failed={fail_n}")
sys.exit(0 if fail_n == 0 else 1)
