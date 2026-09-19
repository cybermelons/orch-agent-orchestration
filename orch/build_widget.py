#!/usr/bin/env python3
"""Bake status.json into widget.html. Run by the tick after each pulse.

Splicing JSON into a <script> block is where this goes wrong: a "</script>"
inside any string would close the block early. json.dumps handles quoting;
the escapes below handle the HTML-parser cases it does not know about.

ORCH_HOME is defined once, in orch.core -- this module imports it rather
than deriving its own fallback (see orch/core.py:27). Left alone, importing
core.ORCH_HOME here would be WORSE than two disagreeing definitions: a
worktree build with ORCH_HOME unset would silently write into the LIVE
checkout every time (that is the bug issue #57 fixes). The guard below
refuses instead: it computes HERE, the checkout this module actually runs
from, and only writes when the resolved output path is inside HERE.
"""
import json
import os
import sys
from pathlib import Path

from orch.core import ORCH_HOME

HERE = Path(__file__).resolve().parent.parent
tpl = ORCH_HOME / "widget.tpl.html"
src = ORCH_HOME / "public" / "status.json"
out = ORCH_HOME / "public" / "widget.html"

try:
    out_resolved = out.resolve()
except OSError as e:
    sys.exit("cannot resolve output path %s: %s" % (out, e))
if HERE not in out_resolved.parents:
    sys.exit(
        "refusing to write %s: ORCH_HOME (%s) is outside the checkout this "
        "module runs from (%s). Set ORCH_HOME to the checkout you want to "
        "build in." % (out_resolved, ORCH_HOME, HERE)
    )

try:
    with open(src) as f:
        data = json.load(f)
except Exception as e:
    sys.exit("cannot read %s: %s" % (src, e))

with open(tpl) as f:
    html = f.read()

if "__SEED__" not in html:
    sys.exit("template has no __SEED__ placeholder")

# The baked seed is not status.json verbatim: the page renders only 5
# unattached_sessions (widget.tpl.html otherSessions()), but the feed can
# carry hundreds -- 421 measured, each with a `file` key that is TUI-only
# (orch/tui.py session_file() tails a transcript by that path; the widget
# never reads it). Baking the full list bloats the page with bytes no
# browser will ever use. status.json itself is left untouched -- the TUI
# reads the same file and lists every entry (orch/tui_model.py) -- this
# trim applies only to the in-memory copy baked into the HTML. Do not
# "helpfully" move this into feed.py: that would delete the full list the
# TUI depends on.
BAKED_SESSIONS = 5

baked = dict(data)
sessions = data.get("unattached_sessions")
if isinstance(sessions, list):
    baked["unattached_total"] = len(sessions)
    baked["unattached_sessions"] = [
        {
            "project": s.get("project"),
            "path": s.get("path"),
            "id": s.get("id"),
            "idle_min": s.get("idle_min"),
        }
        for s in sessions[:BAKED_SESSIONS]
    ]
else:
    baked["unattached_total"] = 0

seed = json.dumps(baked, separators=(",", ":"))
# Neutralize sequences an HTML parser would act on inside <script>.
seed = seed.replace("</", "<\\/").replace("<!--", "<\\!--")
seed = seed.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

html = html.replace("__SEED__", seed, 1)

out.parent.mkdir(parents=True, exist_ok=True)
# Per-pid tmp name, not a shared "widget.html.tmp". Two builds can now run at
# once -- the server builds one at startup, the ticker's tick builds on its own
# cadence, and they are separate services with no ordering between them. On a
# shared tmp path their writes interleave and both rename, publishing a
# half-written page. The rename itself is atomic, so whichever lands last wins
# and is whole.
tmp = out.with_suffix(out.suffix + ".tmp.%d" % os.getpid())
try:
    with open(tmp, "w") as f:
        f.write(html)
    tmp.replace(out)
except BaseException:
    tmp.unlink(missing_ok=True)   # a crash must not leave tmp.<pid> litter
    raise

size = len(html.encode("utf8"))
# No cap. The removed CAP (256 KiB) had no recorded justification anywhere in
# the tree -- introduced in f302d54 with no comment, unmentioned in DESIGN.md,
# README.md and docs/UX-REDESIGN.md -- and was not a platform, browser or
# measured limit. The page is served from 127.0.0.1:18803 to one operator, so
# there is no budget to hold it against. Operator verdict, 2026-09-16.
#
# The size is still printed: it is a real fact about the artifact and the
# build is where it is cheapest to observe. It just does not gate anything.
print("built %s (%d bytes)" % (out, size))
