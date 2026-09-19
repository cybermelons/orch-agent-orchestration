#!/bin/bash
# Fetch origin and fast-forward the live checkout on a timer.
#
# Nothing fetches today (orch/feed.py:436-438): the behind count in the
# dashboard is read-only and goes stale-clean without a fetch. This script
# closes that gap. It contains no build and no restart logic -- the
# post-merge hook already does that on every merge, this script included.
#
# ORCH_HOME is pinned explicitly, never taken from the environment or from
# `git rev-parse` alone, and the script refuses to run anywhere but the live
# checkout. This is the issue #57 guard: an unpinned worktree run must never
# act as if it were the live checkout.
set -u
set -o pipefail

ORCH_HOME="$HOME/orch"

# Outcome bookkeeping (orch#250 part 2): a state file recording consecutive
# failures/refusals, the last outcome string, and a unix timestamp -- so
# orch/feed.py can tell "behind and self-correcting" (a tick lock skip) apart
# from "behind and stuck" (repeated failures) instead of only ever surfacing
# the former via deploy-behind.
#
# $ORCH_HOME/state already exists as the convention for this kind of file
# (orch/tick.py: STATE = ORCH_HOME / "state", tick-spawns.json etc).
STATE_FILE="$ORCH_HOME/state/orch-pull.json"

# Record the outcome. $1 = outcome string (plain text, goes into JSON, so it
# must not itself contain a double quote or backslash -- true for every call
# site below, all of which pass a fixed short label), $2 = "reset"|"fail"|"skip".
# Never lets a bookkeeping error fail an otherwise healthy pull: every step is
# best-effort and a failure at any point just returns 0, leaving the previous
# (or no) state file in place rather than turning a healthy pull into a
# failed one.
record_outcome() {
    outcome="$1"
    mode="$2"
    mkdir -p "$ORCH_HOME/state" 2>/dev/null || return 0

    prev_count=0
    prev_outcome=""
    if [ -f "$STATE_FILE" ]; then
        prev_count="$(grep -o '"consecutive_failures":[0-9]*' "$STATE_FILE" 2>/dev/null \
            | head -n1 | grep -o '[0-9]*$')"
        case "$prev_count" in ('') prev_count=0 ;; esac
        prev_outcome="$(grep -o '"last_outcome":"[^"]*"' "$STATE_FILE" 2>/dev/null \
            | head -n1 | sed 's/^"last_outcome":"//; s/"$//')"
    fi

    case "$mode" in
        reset) new_count=0 ;;
        fail) new_count=$((prev_count + 1)) ;;
        # skip (tick lock held): a tick was running, which is routine and
        # self-correcting. It is not a failure, so the count is untouched --
        # and the outcome is kept too, so an alert already raised by real
        # failures keeps naming the failure instead of being relabelled
        # "SKIPPED" by the next overlapping tick.
        *) new_count="$prev_count"
           if [ -n "$prev_outcome" ]; then outcome="$prev_outcome"; fi ;;
    esac

    tmp_file="$(mktemp "$ORCH_HOME/state/.orch-pull.json.XXXXXX" 2>/dev/null)" || return 0
    printf '{"consecutive_failures":%s,"last_outcome":"%s","last_attempt":%s}\n' \
        "$new_count" "$outcome" "$(date +%s)" > "$tmp_file" 2>/dev/null \
        && mv -f "$tmp_file" "$STATE_FILE" 2>/dev/null
    rm -f "$tmp_file" 2>/dev/null
    return 0
}

# Guard 1 -- pinned checkout only. A worktree's toplevel differs from
# ORCH_HOME, so this refuses to run inside any issue worktree (issue #57).
toplevel="$(git -C "$ORCH_HOME" rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$toplevel" ] || [ "$toplevel" != "$ORCH_HOME" ]; then
    echo "orch-pull: REFUSED -- toplevel '$toplevel' is not the live checkout '$ORCH_HOME'" >&2
    record_outcome "REFUSED: not the live checkout" fail
    exit 1
fi

# Guard 2 -- refuse if a TRACKED file is modified. Never discard operator work.
#
# Untracked files are deliberately not a refusal reason: ORCH_HOME is also the
# machine's state directory, so it always accumulates untracked things
# (.claude/, docs/artboards/, repos.txt.bak-* backups). Refusing on those
# bought no safety and cost every fetch, forever and silently (orch#250:
# the deploy sat 25 commits behind because of exactly this).
#
# It is NOT true that a fast-forward can never touch an untracked file: if an
# incoming commit adds a path that exists untracked here, git refuses with
# "untracked working tree files would be overwritten by merge". That case is
# deliberately left to the merge itself rather than pre-empted here -- the
# merge names the offending paths, exits non-zero, and lands on the FAILED
# path below, which counts toward the stuck alert. One loud, specific failure
# beats a blanket refusal that could not say what was wrong.
dirty="$(git -C "$ORCH_HOME" status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
    blocking="$(printf '%s\n' "$dirty" | head -n 5)"
    extra=$(($(printf '%s\n' "$dirty" | wc -l) - 5))
    echo "orch-pull: REFUSED -- working tree at $ORCH_HOME has modified tracked files, skipping fetch:" >&2
    echo "$blocking" >&2
    if [ "$extra" -gt 0 ]; then
        echo "  ... and $extra more" >&2
    fi
    record_outcome "REFUSED: dirty tracked files" fail
    exit 1
fi

# Guard 3 -- resolve the base ref exactly as core.base_ref() does
# (orch/core.py:403-413). Never hardcode origin/main: a gitea remote or a
# master-default repo resolves differently, and a hardcoded ref would pull
# against a base the alert does not measure.
base="$(git -C "$ORCH_HOME" symbolic-ref -q --short refs/remotes/origin/HEAD)"
if [ -z "$base" ]; then
    for candidate in origin/main origin/master; do
        if git -C "$ORCH_HOME" rev-parse --verify -q "$candidate" >/dev/null 2>&1; then
            base="$candidate"
            break
        fi
    done
fi
if [ -z "$base" ]; then
    echo "orch-pull: FAILED -- could not resolve a base ref (no origin/HEAD, no origin/main, no origin/master)" >&2
    record_outcome "FAILED: could not resolve base ref" fail
    exit 1
fi

# Fetch. This alone already restores the alert's accuracy (orch/feed.py:457
# measures HEAD..<base> against whatever was last fetched).
if ! git -C "$ORCH_HOME" fetch origin; then
    echo "orch-pull: FAILED -- git fetch origin did not complete" >&2
    record_outcome "FAILED: git fetch origin did not complete" fail
    exit 1
fi

# Guard 4 (skip path) -- nothing to do. Same expression as orch/feed.py:457.
behind="$(git -C "$ORCH_HOME" rev-list --count "HEAD..$base" 2>/dev/null)"
if [ -z "$behind" ]; then
    echo "orch-pull: FAILED -- could not compute behind count for HEAD..$base" >&2
    record_outcome "FAILED: could not compute behind count" fail
    exit 1
fi
if [ "$behind" -eq 0 ]; then
    echo "orch-pull: up to date with $base, nothing to do"
    record_outcome "up to date, nothing to do" reset
    exit 0
fi

# Guard 5 -- take the tick lock, and HOLD it across the merge.
#
# Opening with the shell's ">>" append redirection (never ">") does not
# truncate the file, matching orch/server.py:187's deliberate "a+".
#
# The lock is held until this script exits, NOT released before the merge.
# Releasing it first would leave a window where a tick starts between the
# test and the merge, which is the exact interleaving the guard exists to
# prevent. Holding it means a tick that wants to start during the merge
# takes the same non-blocking path it already takes when another tick is
# running (orch/tick.py:575-580 logs "tick already running, skip", and
# orch/server.py:187-194 silently carries the event forward). Both are
# existing, exercised code paths, so the merge borrows a skip the tick
# already knows how to handle.
#
# The merge is a fast-forward of a clean tree, so it is short. The fetch
# above already happened and is kept either way.
exec {lock_fd}>>"$ORCH_HOME/.tick.lock"
if ! flock -n "$lock_fd"; then
    echo "orch-pull: SKIPPED -- .tick.lock held, a tick is running, will retry next interval"
    record_outcome "SKIPPED: tick lock held" skip
    exit 0
fi

# Fast-forward or nothing. No reset, no force, no clobber. This merge fires
# deploy/post-merge, which rebuilds the widget and restarts orch-web.
if git -C "$ORCH_HOME" merge --ff-only "$base"; then
    echo "orch-pull: fast-forwarded to $base ($behind commit(s))"
    record_outcome "fast-forwarded to $base" reset
    exit 0
else
    echo "orch-pull: FAILED -- fast-forward merge to $base did not apply" >&2
    record_outcome "FAILED: fast-forward merge did not apply" fail
    exit 1
fi
