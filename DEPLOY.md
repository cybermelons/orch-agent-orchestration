# DEPLOY.md — how orch ships to the live dashboard on devhost

orch is **not** deployed by running `python3 -m orch.server` from a checkout by
hand. It ships through a **three-part chain on devhost**: a systemd timer fetches,
a git hook builds and restarts, and a systemd service keeps the process alive.

Compare `ops-console/DEPLOY.md`, which uses the same timer-pull pattern against
a separate serve directory. orch differs in one way: it deploys **in place**,
from the live checkout at `~/orch`, because that checkout is also the machine's
state directory (`state/`, `wt/`, `repos.txt`).

```
  you: git push origin main   (or an agent auto-lands a PR)
        │
        ▼
  GitHub  cybermelons/orch
        │
        ▼
  devhost  orch-pull.timer            (every 15 min, OnBootSec 5 min)
        └─▶ orch-pull.service       (oneshot, KillMode=process)
              └─▶ ~/orch/deploy/orch-pull.sh
                    • git fetch origin
                    • git merge --ff-only  (only if behind, tree clean,
                                            .tick.lock free)
        │
        │  the merge fires the hook:
        ▼
  devhost  .git/hooks/post-merge
        • python3 -m orch.build_widget   (ORCH_HOME pinned)
        • systemctl --user restart orch-web orch-tick  (live checkout,
                                                        enabled units only)
        │
        ├──────────────────────────┐
        ▼                          ▼
  orch-web.service          orch-tick.service   (both Restart=always,
   • serves 127.0.0.1:18803   • python3 -m orch.ticker      Linger=yes)
   • dashboard + actions      • ticks on a tracked exit, or
   • never ticks                every ORCH_TICK_SECS (600s)
        │                          │
        ▼                          └─▶ python3 -m orch.tick (the work)
  tailscale serve → https://devhost.example-tailnet.ts.net:18803
```

**Two services, on purpose.** Ticking spends tokens (it spawns sessions);
the dashboard only shows state. Splitting them means either can be stopped
alone — `systemctl --user stop orch-tick` halts the spend and leaves the
dashboard up, which is the lever that did not exist on 2026-09-17 (#442).

**Landing time:** a merge to `main` reaches the live dashboard in **up to 15
minutes** (one timer cycle), plus a few seconds to rebuild and restart.

## Why three parts and not one

A git hook cannot fetch. `post-merge` only fires when a merge happens, and
before the timer existed nothing made merges happen — so the hook worked
perfectly and was never triggered. Drift reached **20 commits** on the morning
of 2026-09-14 and **24** that evening, with the dashboard serving stale code
while every fix sat merged on GitHub.

| part | supplies | without it |
|---|---|---|
| `orch-pull.timer` | the trigger | nothing fetches; the hook never fires |
| `post-merge` hook | build + restart | new code on disk, old code running |
| `orch-web.service` | process liveness | a kill leaves the dashboard down |
| `orch-tick.service` | the tick cadence | the dashboard goes stale; nothing spawns |

## Hosts / paths

| Host | What | Path |
|---|---|---|
| devhost | live checkout, state, worktrees | `/home/user/orch` |
| devhost | unit files (installed) | `~/.config/systemd/user/` |
| devhost | unit files (source of truth) | `~/orch/deploy/` |
| devhost | server log | `~/orch/state/server.log` |
| tailnet | the URL the browser loads | `https://devhost.example-tailnet.ts.net:18803` |

The dashboard binds `127.0.0.1` and is reached over Tailscale Serve. It is
**tailnet only** — there is no authentication, and the tailnet is the boundary
(see issues #28, #59). GitHub cannot reach in, which is why deployment is
pull-based rather than a webhook.

## Install on a fresh machine

    cp deploy/orch-web.service deploy/orch-tick.service \
       deploy/orch-pull.service deploy/orch-pull.timer \
       ~/.config/systemd/user/
    cp deploy/post-merge .git/hooks/post-merge
    chmod +x .git/hooks/post-merge
    systemctl --user daemon-reload
    systemctl --user enable --now orch-web orch-tick orch-pull.timer
    loginctl enable-linger "$USER"     # survive logout

Git hooks are not versioned, so `post-merge` must be copied on every checkout.

Machine-local config (`orch.json`) is deliberately **untracked** — it used to
be a tracked file that was also this same live checkout, so every operator
edit dirtied the tree, and the auto-pull dirty guard above then refused to
fetch forever (orch#250). A fresh deployment has none; `orch.json` is
created the first time orch runs, by migrating `repos.txt` in place if one
exists (see `orch/core.py`'s `_load_config`), or empty otherwise — there is
no example file to copy for it. `repos.txt`, and any watched repo's own
`.orch.toml` if it still has one, are each read once during migration and
then left on disk, inert (never deleted — no-delete-before-convert) and
never read again; `orch.json` is the only file orch reads for config from
that point on.

### Upgrading an existing deployment past the untracking (one time, orch#250)

A checkout that predates the untracking has `repos.txt` as a **tracked**
file holding live config. The commit that untracks it is a delete, so
fast-forwarding onto it removes it from disk on a clean tree, and on a dirty
tree git refuses the merge outright. Neither is what you want, and the
second one is how the deployment got stuck in the first place.

Back up the config, then take the upstream state, then restore:

```bash
cd ~/orch
cp repos.txt /tmp/repos.txt.keep 2>/dev/null
cp orch.json /tmp/orch.json.keep 2>/dev/null
git fetch origin
git reset --hard origin/main      # discards local-only commits; config is in /tmp
cp /tmp/repos.txt.keep repos.txt 2>/dev/null
cp /tmp/orch.json.keep orch.json 2>/dev/null
git status --porcelain            # expect: clean, or untracked-only
```

`git reset --hard` here is deliberate and safe **only because the config was
copied out first** — the live checkout is a deployment target, not a place to
develop, so it should carry no local commits worth keeping. Verify
`git status --porcelain --untracked-files=no` is empty afterwards; that is
exactly what the auto-pull guard checks.

    systemctl --user status orch-web orch-tick  # are they up, since when
    systemctl --user restart orch-web           # restart it — never `nohup`
    systemctl --user list-timers orch-pull.timer
    journalctl --user -u orch-pull.service -n 20   # last pull, and why it skipped
    journalctl --user -u orch-tick -n 20           # recent ticks, and their trigger

## Stopping the token spend without going blind

    systemctl --user stop orch-tick      # no more scheduled ticks, no spawning
    systemctl --user start orch-tick     # resume

The dashboard stays up and keeps serving the last state either way. The
dashboard's own tick button also still works while the ticker is stopped —
it runs one tick from the server process. Stopping the service stops the
*schedule*, not the operator.

To survive a reboot in the stopped state, `disable` it as well as stopping
it; `post-merge` restarts only units that are enabled, so a disabled
`orch-tick` stays down across a deploy rather than being quietly revived.

**Never restart the server with `nohup python3 -m orch.server &`.** A
hand-started copy is a child of the invoking shell and dies with it. Killing
the managed process to make room for one takes the dashboard down until
somebody notices — that happened on 2026-09-13 (issue #86).

## Deploying right now, without waiting

    git -C ~/orch pull            # the hook does the rest

## When it is not deploying

The dashboard reports its own staleness: `deployment is N commit(s) behind
origin/main`. If that alert stands and does not clear, read the pull log — the
script refuses rather than guesses, and every refusal prints one line:

| line | meaning |
|---|---|
| `REFUSED -- ... is not the live checkout` | ran from a worktree (issue #57 guard) |
| `REFUSED -- working tree ... is dirty` | uncommitted work present; it will never discard it |
| `SKIPPED -- .tick.lock held` | a tick was running; retries next interval |
| `FAILED -- could not resolve a base ref` | no `origin/HEAD`, `origin/main` or `origin/master` |
| `FAILED -- fast-forward merge ... did not apply` | the checkout diverged; needs a human |

Failure is loud on purpose. A timer that reported success while not pulling
would turn a visible human-owed condition into an invisible one, which is worse
than having no timer.

Note the fetch runs **before** the tick-lock check, so an interval that skips
the merge still refreshes the behind count — `feed.py` never fetches for itself.

## Do not enable

`~/.config/systemd/user/orch-tick.timer` predates all of this and is
inactive. `deploy/orch-tick.service` replaces the stale service file it
drove (that one's `ExecStart` pointed at `~/orch/tick.sh`, which does not
exist) with a long-running `Restart=always` ticker. Install the service;
leave the **timer** disabled — it would be a second cadence competing with
the ticker's own loop.

## Verified

2026-09-14 on devhost: timer enabled and ran (`up to date with origin/main,
nothing to do`); hook observed rebuilding and restarting on a real merge;
`orch-web` restart-after-kill confirmed (PID 2053356 → 2053865 in seconds).

See `deploy/README.md` for the per-unit detail and the reasoning behind each
guard.
