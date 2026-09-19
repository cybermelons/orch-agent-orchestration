#!/usr/bin/env python3
"""The TUI's ONLY use of the network: posting operator actions to /act.

The feed is still read from disk (see tui_model.load_feed). This module exists
so the terminal view can DO something, and it is kept separate from tui.py for
two reasons: it is testable without a terminal, and it makes the boundary
obvious — if a second file in the TUI ever imports urllib, the "works when
orch-web is down" property has quietly been lost.

No verb is reimplemented here. Every action is a name in server.ACTIONS plus
that action's required arguments; this module only carries the JSON there and
the answer back. A dead server, a refused connection, a timeout or a non-JSON
reply all come back as (False, message) — never an exception, because the
caller is mid-curses and a traceback would wreck the terminal.
"""
import json
import os
import urllib.error
import urllib.request

TIMEOUT = 2.0   # short on purpose: the TUI must not hang on a dead server


def endpoint():
    """127.0.0.1 only, matching how server.py binds. ORCH_PORT is read at call
    time, not at import, so a test can point a child process at a dead port."""
    return "http://127.0.0.1:%s/act" % os.environ.get("ORCH_PORT", "18803")


def post(action, timeout=TIMEOUT, **args):
    """(ok, out). Never raises.

    `ok` is the server's own {"ok": ...} when it answered, and False for every
    transport failure. `out` is always a string an operator can read on the
    status line.
    """
    body = dict(args)
    body["action"] = action
    try:
        data = json.dumps(body).encode()
    except (TypeError, ValueError) as e:
        return False, "cannot encode action: %s" % e

    req = urllib.request.Request(
        endpoint(), data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        # A 400 is the server rejecting the ARGUMENTS, which is a different
        # problem from the server being gone, and the operator needs to see
        # which: one is "fix the row", the other is "start orch-web".
        try:
            d = json.loads(e.read() or b"{}")
        except Exception:
            d = {}
        return False, str(d.get("error") or d.get("out") or "server said %s" % e.code)
    except urllib.error.URLError as e:
        return False, "cannot reach orch-web: %s" % (getattr(e, "reason", None) or e)
    except OSError as e:
        return False, "cannot reach orch-web: %s" % (e.strerror or e)
    except Exception as e:                      # timeouts, truncated reads
        return False, "action failed: %s" % e

    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        return False, "bad reply from orch-web (not JSON)"
    if not isinstance(d, dict):
        return False, "bad reply from orch-web"
    return bool(d.get("ok")), str(d.get("out") or "")


def probe(timeout=TIMEOUT):
    """(available, reason). The same availability check the web page makes.

    Reachability is the whole question — if probe answers at all the server is
    up, and if it does not the actions are shown DISABLED with this reason
    (#66), never hidden.
    """
    ok, out = post("probe", timeout=timeout)
    if ok:
        return True, ""
    return False, out or "orch-web is not answering — start orch-web"
