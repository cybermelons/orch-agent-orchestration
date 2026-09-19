#!/usr/bin/env python3
"""Start the orch dashboard server and open it as a chromeless app window.

    ./run.py            start server + ticker (background), open app-mode window
    ./run.py --fg        keep the server in the foreground (Ctrl-C stops it)
    ./run.py --no-open   start the server only, do not open a window
    ./run.py --no-tick   dashboard only, no ticker (no token spend)

The server and the ticker are two processes (see orch/ticker.py). This starts
both, because a dev dashboard whose feed never updates looks broken rather
than looking switched off.

App-mode needs a Chromium browser (Chrome/Brave/Edge) actually installed -
it is a launch flag on the browser binary, not a bundled feature, and Safari
has no equivalent flag at all. Falls back to the OS default browser (a plain
tab, works everywhere) when none is found.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

# Invoked directly as `./run.py`, not `-m orch.run` -- Python only puts this
# file's own directory (orch/) on sys.path, so `orch` the package is not
# importable until the repo root is added. Must happen before the import.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from orch.core import ORCH_HOME  # noqa: E402

# Children run from the repo root, NOT from ORCH_HOME. The two are the same
# path on a normal checkout, but ORCH_HOME defaults to ~/orch and this file
# may live in a worktree -- and then `python -m orch.<mod>` with cwd=ORCH_HOME
# resolves a *different* checkout's package, or none at all ("No module named
# orch.ticker"). cwd only has to make the package importable; ORCH_HOME still
# travels in the child's env and is what picks the state directory.
PORT = int(os.environ.get("ORCH_PORT", "18803"))
URL = f"http://127.0.0.1:{PORT}"

CHROMIUM_APPS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def find_chromium():
    for p in CHROMIUM_APPS:
        if Path(p).exists():
            return p
    for name in ("google-chrome", "chromium", "brave-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def wait_up(url, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def open_window():
    chromium = find_chromium()
    if chromium:
        subprocess.Popen([chromium, f"--app={URL}",
                           "--window-size=1100,800"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"opened app window via {chromium}")
    else:
        webbrowser.open(URL)
        print("no Chromium browser found; opened a plain tab in your default browser")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fg", action="store_true", help="run server in foreground")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser window")
    ap.add_argument("--no-tick", action="store_true",
                    help="dashboard only, don't start the ticker")
    args = ap.parse_args()

    env = {**os.environ, "ORCH_HOME": str(ORCH_HOME), "ORCH_PORT": str(PORT)}
    server_cmd = [sys.executable, "-m", "orch.server"]

    ticker = None
    if not args.no_tick:
        ticker = subprocess.Popen(
            [sys.executable, "-m", "orch.ticker"], env=env, cwd=REPO_ROOT)
        print(f"orch ticker running, pid={ticker.pid}")

    if args.fg:
        # Open the window once the server is confirmed listening, in the
        # background, then block on the server itself.
        if not args.no_open:
            def opener():
                if wait_up(URL):
                    open_window()
            import threading
            threading.Thread(target=opener, daemon=True).start()
        # Run the server as a child and wait, rather than exec'ing it. An exec
        # replaces this process and abandons the ticker started above: Ctrl-C
        # would then stop the dashboard and leave an invisible ticker spending
        # tokens with no dashboard to show for it.
        srv = subprocess.Popen(server_cmd, env=env, cwd=REPO_ROOT)
        try:
            return srv.wait()
        except KeyboardInterrupt:
            return 0
        finally:
            for p in (srv, ticker):
                if p and p.poll() is None:
                    p.terminate()

    proc = subprocess.Popen(server_cmd, env=env, cwd=REPO_ROOT)
    print(f"orch server running in background, pid={proc.pid}")
    if not wait_up(URL):
        print("server did not come up in time", file=sys.stderr)
        return 1
    if not args.no_open:
        open_window()
    stop = f"kill {proc.pid}" + (f" {ticker.pid}" if ticker else "")
    print(f"dashboard: {URL}  (stop with: {stop})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
