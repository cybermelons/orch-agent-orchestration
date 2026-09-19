#!/usr/bin/env python3
"""orch#138 -- drain a spawned `claude -p --output-format stream-json`
child's stdout into the run log, from a SEPARATE, DETACHED process.

Before this: the drain ran on a daemon thread inside the spawning process
(core.py's old `_pump`). That worked only as long as the spawner stayed
alive. tick.py / the CLI spawns, then exits seconds later; the daemon
thread dies with it, and everything the child prints after that point is
never written -- the run log reads blind for the rest of a long session
even though this whole feature exists to fix exactly that blindness.
Moving the drain into its own process means it keeps running after the
spawner is gone, for as long as the child's stdout pipe stays open.

THE SUBTLE CONSTRAINT: this process must NOT be in the claude child's
process group. orch's `alive()` check is `os.killpg(pgid, 0)` -- group
liveness, not single-pid liveness. If this pump shared the child's group,
the group would stay "alive" for as long as the pump keeps running, i.e.
forever after the child itself has exited (stdin closed, EOF pending on
nothing) -- every finished session would read as alive forever. Detaching
this process from that group is what the CALLER (core.py, a later unit)
must set up when it spawns this module (e.g. start_new_session or an
equivalent); nothing in this file can enforce or verify that from the
inside, so there is no code here for it -- it is a contract on the spawn
site, not on this loop.

This module owns the process, the file writes, and the loop. It reuses
`translate_line` from orch.runlog (pure, no I/O) rather than reimplementing
translation -- see that module's docstring for the line format contract.

ROBUSTNESS: this is observation only. Nothing here may affect the child in
any way -- a bug in this file must never be able to make the child block,
crash, or behave differently. Concretely: the pipe is drained even when
there is nowhere to log to (unopenable log path) or when a write fails, and
`main()` never lets an exception escape uncaught.
"""
import sys

from orch.runlog import translate_line


def _drain(stdin_buffer, log_path):
    # Open the log for append up front. If this fails there is nowhere to
    # log to, but the pipe must still be drained below so the child never
    # blocks on a full stdout buffer -- losing the log is acceptable, the
    # inverse (stalling the child because nobody is reading its stdout) is
    # not.
    try:
        append_f = open(log_path, "a")
    except Exception:
        append_f = None

    try:
        for raw_line in stdin_buffer:
            try:
                text = raw_line.decode("utf-8", "replace")
            except Exception:
                text = str(raw_line)

            if append_f is None:
                continue

            try:
                line = translate_line(text)
            except Exception:
                # Never let a translator bug swallow the line or, worse,
                # kill the drain: fall back to the raw text, truncated, so
                # there is still something to read.
                line = text.strip()[:200]

            if not line:
                continue

            try:
                append_f.write(line + "\n")
                # Flush after every single line. This is the whole point of
                # this module existing: an unflushed write here reproduces
                # the exact defect this issue fixes, one layer up -- a log
                # that reads blind until something exits.
                append_f.flush()
            except Exception:
                # A write/flush failure is an observation-surface problem,
                # not a reason to stop draining the pipe.
                pass
    finally:
        if append_f is not None:
            try:
                append_f.close()
            except Exception:
                pass


def main(argv):
    # Wrong arg count -> exit nonzero, no traceback. This is a detached
    # process with no terminal attached to a human by the time it would
    # matter, so a traceback would go nowhere useful anyway.
    if len(argv) != 2:
        return 1

    log_path = argv[1]

    try:
        _drain(sys.stdin.buffer, log_path)
    except Exception:
        # Nothing may raise out of main(): this process is pure observation
        # of the child's stdout pipe, and must never be able to affect the
        # child by dying loudly or unexpectedly.
        pass

    # EOF on stdin is the normal, expected termination -- the child closed
    # its stdout (it exited), there is nothing left to drain.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
