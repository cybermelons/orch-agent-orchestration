#!/usr/bin/env python3
"""orch ticker — the scheduler that decides *when* to run a tick.

Separate from the web UI on purpose. The tick loop is what spends tokens
(it spawns sessions); the dashboard only shows state. Bundled in one
process there was no way to stop the spend without also losing the view:

    systemctl --user stop orch-tick     # stop spending, keep the dashboard

This process does no work of its own. It watches for a reason to tick and
then runs `python -m orch.tick`, which is where the work has always lived.
"""
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

from orch import core

# Clamped so a bad env value can't make sleep/timeout raise in the loop.
TICK_SECS = max(1, min(int(os.environ.get("ORCH_TICK_SECS", "60")), 86400))
# Watcher cadence: short against TICK_SECS and cheap -- live_tracked_pgids()
# is a few small JSON reads plus killpg(0) -- so polling this often to catch
# a tracked session's exit promptly costs nothing worth avoiding.
POLL_SECS = max(1, min(TICK_SECS, 5))
# cwd for the tick subprocess: the repo root (dir containing the orch/
# package), so `python -m orch.tick` resolves the package regardless of this
# process's own cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _since_tick():
    """Seconds since the last tick, from .tick.lock's mtime.

    The filesystem, not a monotonic global: the dashboard's own tick button
    runs in the *server* process, and an in-process timer there is invisible
    here. Every tick touches this file (orch.tick opens it "w" to take the
    lock), so its mtime is the one timestamp both processes can agree on.

    A missing lock file means no tick has ever run: report the interval as
    expired so a fresh checkout ticks at once rather than waiting one cycle.

    time.time(), not monotonic -- an mtime is wall-clock and has no choice.
    A backward clock step can therefore delay one tick by the size of the
    step; the clamp below keeps it from delaying forever.
    """
    try:
        age = time.time() - core.TICK_LOCK.stat().st_mtime
    except OSError:
        return float(TICK_SECS)
    # A future mtime (clock step, or a copied file) would otherwise read as a
    # negative age and stall the clock trigger indefinitely.
    return max(0.0, age)


def _should_fire(pending, since, interval):
    """-> (bool, str) : whether to fire, and the trigger name for the log.

    Pure function, no I/O, no time call inside -- the loop passes `since`
    in, so this stays testable without a wall clock."""
    if pending:
        return True, "exit"
    if since >= interval:
        return True, "clock"
    return False, ""


def tick_loop():
    """Tick on a tracked session's exit, or when it has been TICK_SECS since
    the last tick, whichever comes first. Any tick resets that timer --
    including the operator's dashboard tick, which runs in the server
    process, because the timer is .tick.lock's mtime rather than a global.

    The clock floor stays because not every world change has a session
    behind it to watch exit -- the operator merges a PR, adds a label, files
    an issue -- and those only surface when the clock forces a look.

    Polls every POLL_SECS for a tracked pgid disappearing. A poll interval's
    worth of exits collapses into one non-empty `gone` set and therefore one
    tick -- that set difference IS the debounce; no separate timer needed.
    """
    prev = core.live_tracked_pgids()
    pending_event = False
    gone = set()
    while True:
        time.sleep(POLL_SECS)
        # Nothing may escape this body: an uncaught exception would stop
        # ticking, and systemd would restart into the same fault.
        try:
            cur = core.live_tracked_pgids()
            newly_gone = prev - cur
            prev = cur
            if newly_gone:
                pending_event = True
                gone |= newly_gone

            fire, trigger = _should_fire(pending_event, _since_tick(), TICK_SECS)
            if not fire:
                continue

            # Check the lock before firing. A held lock means a tick is
            # already running (e.g. the operator's dashboard tick, or a slow
            # prior fire) -- do not fire, and do NOT clear pending_event, so
            # the event carries forward to after the running tick finishes.
            # Never log a skip here: a held lock is the normal quiet case,
            # not a fault, and the point is exactly to not produce a stream
            # of "already running" lines.
            lock_fh = open(core.TICK_LOCK, "a+")  # "a+", never "w" -- no truncation
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_fh.close()
                continue
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

            if trigger == "exit":
                print(f"tick: exit of pgid(s) {gone}", file=sys.stderr)
            else:
                print("tick: clock", file=sys.stderr)

            # timeout kills a wedged tick rather than stalling the schedule;
            # can interrupt an in-flight dashboard-op wake, but the next tick
            # re-derives the world.
            try:
                p = subprocess.run(
                    [sys.executable, "-m", "orch.tick"], cwd=str(REPO_ROOT),
                    capture_output=True, text=True, timeout=TICK_SECS,
                )
                if p.returncode != 0:
                    print(f"tick rc={p.returncode}: {(p.stderr or '')[-300:]}", file=sys.stderr)
            except subprocess.TimeoutExpired:
                print("tick timed out", file=sys.stderr)

            # A fire was attempted (ran to completion or timed out) -- clear
            # the pending event and re-snapshot: the tick itself spawns
            # sessions, and those new pgids must not later read as deaths,
            # nor be mistaken for one now. The interval reset needs no call
            # here; the tick touched .tick.lock, which IS the timer.
            pending_event = False
            gone = set()
            prev = core.live_tracked_pgids()
        except Exception as e:
            print(f"tick error: {e}", file=sys.stderr)


if __name__ == "__main__":
    print(f"orch ticker: every {TICK_SECS}s or on a tracked exit", file=sys.stderr)
    tick_loop()
