# deploy

Machine-local service config, kept in the repo so it is reviewable and does
not exist only on one box.

## orch-web.service

The web UI. Install on a new machine:

    cp deploy/orch-web.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now orch-web
    loginctl enable-linger "$USER"   # survive logout

`Restart=always` is deliberate. `Restart=on-failure` was the original setting
and it did not survive a `SIGTERM` — a clean kill left the dashboard down
with nothing to bring it back (issue #86).

Restart it with `systemctl --user restart orch-web`, never `nohup`.

The server serves the dashboard and runs operator actions. It does **not**
tick — `orch-tick.service` below does, as a separate process.

## orch-tick.service

The scan/spawn loop (`python3 -m orch.ticker`): it watches for a tracked
session exiting or the clock expiring, then runs `python3 -m orch.tick`.

    cp deploy/orch-tick.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now orch-tick

**Why this is not a thread inside orch-web.** Ticking is what spends tokens:
it spawns agent sessions. As one process there was no way to stop the spend
without also losing the dashboard, or to keep watching while spending was
off. That was the exact bind on 2026-09-17 — budget exhausted, and the only
lever available took the visibility down with it (issue #442). Now:

    systemctl --user stop orch-tick      # stop spending, keep the dashboard
    systemctl --user start orch-tick     # resume

The dashboard's own tick button still works while `orch-tick` is stopped: it
runs a one-shot `orch.tick` from the server process. Stopping the service
stops the *schedule*, not the operator.

`KillMode=process` is deliberate, same as `orch-pull.service`: a tick spawns
worker sessions, and a control-group kill on restart would take those
workers down with the scheduler that happened to start them.

The two processes share no memory — they never did. Everything crossing
between them is already on disk (`public/status.json`, `.tick.lock`), which
is why this split needed no state untangling. The tick interval is
`.tick.lock`'s mtime rather than an in-process timer, so a manual tick from
the dashboard resets the ticker's clock across the process boundary.

## post-merge hook

Rebuilds `public/widget.html` and restarts the service whenever code arrives
via `git merge` or `git pull`. Install:

    cp deploy/post-merge .git/hooks/post-merge
    chmod +x .git/hooks/post-merge

Git hooks are not versioned, so this must be copied into place on each
checkout.

Two guards, both deliberate:

- **The build gates the restart.** A failed build exits non-zero and does NOT
  restart, so a half-written page is never served.
- **Only the live checkout restarts the service.** A merge inside an issue
  worktree builds into that worktree and leaves the machine's server alone.
  `ORCH_HOME` is pinned from `git rev-parse --show-toplevel` for the same
  reason (issue #57: an unpinned worktree build overwrites the live
  checkout).

## orch-pull timer

The `post-merge` hook above only fires on a merge, and nothing fetched. So
commits landed on `origin/main` and sat there until a person ran `git pull`.
Drift on 2026-09-14 reached 20 commits in the morning and 24 in the evening.

`orch-pull.sh` closes that gap. It fetches, and it fast-forwards the live
checkout when the checkout is behind. The merge then fires the `post-merge`
hook, which does the rebuild and the restart. **The timer contains no build
logic and no restart logic.** Do not add any.

Install:

    cp deploy/orch-pull.service deploy/orch-pull.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now orch-pull.timer

Check it with `systemctl --user list-timers orch-pull.timer`. Read the last
run with `journalctl --user -u orch-pull.service -n 20`.

### The guards, and why each one exists

- **The live checkout only.** `ORCH_HOME` is pinned to `$HOME/orch`, and the
  script refuses to run if that path is not a git toplevel. Every git command
  uses `git -C "$ORCH_HOME"`, so the script acts on the live checkout even
  when a person starts it from a worktree. This is the issue #57 guard.
- **Never pull a dirty tree.** A dirty tree means a person has uncommitted
  work. The script refuses and exits non-zero. It never discards that work.
- **Fast-forward or nothing.** The merge uses `--ff-only`. There is no reset,
  no force, and no clobber. A diverged checkout fails loudly and waits for a
  person.
- **The base ref is resolved, never hardcoded.** The script resolves
  `refs/remotes/origin/HEAD` and falls back to `origin/main`, then
  `origin/master`. This mirrors `core.base_ref()` at `orch/core.py:403-413`.
  The dashboard alert measures against that same resolved ref
  (`orch/feed.py:457`). A hardcoded `origin/main` could pull against a ref
  the alert does not measure, and the alert would then never clear.
- **Never merge during a tick.** The script takes `$ORCH_HOME/.tick.lock`
  with `flock -n` and **holds it across the merge**. Do not release the lock
  before the merge: that leaves a window where a tick starts between the test
  and the merge, which is the interleaving this guard prevents. A tick that
  wants to start meanwhile takes the skip path it already has
  (`orch/tick.py:575-580`).
- **The lock file is opened with `>>`, never `>`.** Append does not truncate.
  `orch/ticker.py` opens the same lock with `"a+"` for this reason.

### Failure is loud, deliberately

Every refusal and every failure prints one line and exits non-zero, so the
oneshot unit enters a failed state that `systemctl --user status` shows.

This matters more than it looks. A timer that reports success while it does
not pull converts a **visible** human-owed condition into an **invisible**
one. That is worse than having no timer. The dashboard's
`deployment is N commits behind` alert is the loud signal working correctly.
Never replace it with a quiet automated path.

Note that the fetch runs before the tick-lock check. So an interval that
skips the merge still refreshes the behind count, because `orch/feed.py`
never fetches for itself (`orch/feed.py:436-438`).

### The old orch-tick.timer

`~/.config/systemd/user/` on devhost also holds an `orch-tick.timer`, left from
before any of this. It is disabled, and its `orch-tick.service` pointed at
`%h/orch/tick.sh`, a file that does not exist.

`deploy/orch-tick.service` (above) **replaces that unit file** and is a
long-running `Restart=always` service, not a `Type=oneshot` the timer drives.
Installing it overwrites the stale one. **Do not enable the timer**: it would
be a second cadence competing with the ticker's own loop.
