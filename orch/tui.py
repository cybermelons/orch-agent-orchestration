#!/usr/bin/env python3
"""orch terminal UI, in the shape of tig.

Full-screen, keyboard driven, curses. It reads the feed FROM DISK, never over
HTTP, because the whole reason this exists is to still work when orch-web is
down (#86 left the dashboard dark and nobody noticed for hours).

Two hard architectural rules live here:

1. No feed-shape logic. Every row on screen comes from orch.tui_model, which is
   the pure, unit-tested half. If this file ever reaches into the feed dict to
   derive something, the web view and the terminal view have started to drift
   and the bug will only show up in one of them.

2. No timer, no thread, no polling. The tick is the origin of all state; a TUI
   refreshing on its own cadence would be a second, competing source of truth
   and would make "what did it say when it broke" unanswerable. getch() blocks,
   and the ONLY refresh is the `r` key.

Actions (orch.tui_act) are the one exception to "no HTTP", and only in one
direction: they POST to the server's existing /act, on a keypress, never on a
cadence. The feed is still read from disk, which is why the tree still renders
when the server is down — and when it is, the action keys are shown DISABLED
with the reason rather than hidden (#66).
"""
import curses
import json
import sys
from pathlib import Path

from orch.core import ORCH_HOME
from orch import tui_act as A
from orch import tui_model as M

DEFAULT_FEED = ORCH_HOME / "public" / "status.json"

# The operator actions. Each is a key, the server-side verb it posts, and the
# one-line description shown in `?` and in the footer.
#
# They are ALWAYS listed, even when they cannot run, per #66: an action the UI
# hides is one the operator cannot learn exists, and a capability they cannot
# see is indistinguishable from one that is broken. When the server is down
# they are shown WITH THE REASON instead, and pressing one repeats the reason.
ACTIONS = (
    ("n", "nudge", "nudge the selected repo's agent"),
    ("K", "kill", "kill the selected issue's session"),
    ("M", "merge", "merge the selected issue's PR"),
    ("T", "tick", "run a tick"),
    ("R", "review_now", "add auto-land (review, then merge)"),
    ("A", "assign", "label the selected issue agent-ready"),
)
ACTION_KEYS = {k: verb for k, verb, _ in ACTIONS}

# Mirrors server.ACTIONS' required-argument tuples. Kept here only to decide
# whether the SELECTED ROW can supply them; the server validates for real.
ACTION_ARGS = {
    "nudge": ("repo",),
    "kill": ("repo", "issue"),
    "merge": ("repo", "issue", "pr"),
    "tick": (),
    "review_now": ("repo", "issue"),
    "assign": ("repo", "issue"),
}

DESTRUCTIVE = ("kill", "merge")

KEYMAP = (
    ("j / Down", "move down"),
    ("k / Up", "move up"),
    ("Enter / Right", "expand or collapse the selected row"),
    ("q", "collapse / go up a level; quit at the top"),
    ("Q", "quit immediately"),
    ("r", "re-read the feed from disk"),
    ("t", "tail the selected session's transcript"),
    ("g / G", "jump to top / bottom"),
    ("PgUp / PgDn", "page up / down"),
    ("?", "this help"),
)

TRANSCRIPTS = Path.home() / ".claude" / "projects"


# --- drawing primitives ---------------------------------------------------------

def put(win, y, x, text, attr=0):
    """Write one clipped line. Every string on screen goes through here.

    Two separate hazards, both of which produce a traceback in a small terminal
    and neither of which is a bug in the caller: a line longer than the window,
    and curses' refusal to write the bottom-right cell (it would scroll). The
    clip handles the first; the try handles the second.
    """
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    room = w - x
    if room <= 0:
        return
    try:
        win.addnstr(y, x, str(text), room, attr)
    except curses.error:
        pass


def fill(win, y, attr):
    """Paint a whole row's background, so a selected line reads as a bar."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h:
        return
    try:
        win.addnstr(y, 0, " " * w, w, attr)
    except curses.error:
        pass


# --- transcript reading ---------------------------------------------------------

def session_file(row):
    """The transcript path behind a row, or None.

    unattached_sessions[] entries carry `file` directly. Ledger entries
    (agent_sessions[]) do not carry a path at all, which is why this can
    legitimately return None and the caller must say so rather than fail.

    Issue rows carry theirs one level down, in issue["sessions"][]. Falling
    through to that is not a convenience: an issue row is the row an operator
    actually presses `t` on -- they select a BLOCKED issue to find out what the
    agent was doing when it stuck -- and without this the only tailable rows
    are the unattached sessions, which by definition belong to no issue.
    Prefer the live/current session; a stopped issue still has its last one.
    """
    pay = row.payload or {}
    p = pay.get("file")
    if p:
        return str(p)
    sessions = [s for s in (pay.get("sessions") or []) if isinstance(s, dict) and s.get("file")]
    if not sessions:
        return None
    for s in sessions:
        if s.get("current") or s.get("live"):
            return str(s["file"])
    return str(sessions[0]["file"])


def row_slug(row):
    """The repo SLUG behind a row, or "".

    The slug is the repo BASENAME (core.repo_path_for matches repos.txt by
    basename), not the owner/name string — posting "cybermelons/orch" where
    the server wants "orch" fails the repos.txt lookup, not the SLUG regex, so
    it would come back as a confusing "not a watched repo".

    Issue payloads do not carry the slug: tui_model builds an issue row's
    payload from the issue dict alone. The slug IS in the row key, which
    tui_model composes as "issue.<slug>.<n>" / "repo.<slug>" / "awaiting.
    <slug>.<n>", so the key is the reachable source and this reads it there
    rather than re-deriving it from the feed (which is the drift rule at the
    top of this file).
    """
    payload = row.payload or {}
    slug = payload.get("slug")
    if slug:
        return str(slug)
    parts = (row.key or "").split(".")
    if len(parts) >= 2 and parts[0] in ("issue", "repo", "awaiting", "labels",
                                        "unconsidered"):
        return parts[1]
    return ""


def row_args(row, verb):
    """(args, None) or (None, why-this-row-cannot).

    "Why not" is a first-class return: a key that silently does nothing is the
    failure #66 is about, so every impossible combination has to be able to
    explain itself on the status line.
    """
    need = ACTION_ARGS.get(verb, ())
    if not need:
        return {}, None
    if row is None:
        return None, "no row selected"
    payload = row.payload or {}
    args = {}

    slug = row_slug(row)
    if not slug:
        return None, "%s needs a repo — select a repo or an issue row" % verb
    args["repo"] = slug

    if "issue" in need:
        num = payload.get("issue")
        if num is None or not str(num).isdigit():
            return None, "%s needs an issue — select an issue row" % verb
        args["issue"] = int(num)

    if "pr" in need:
        pr = payload.get("pr")
        if pr is None or not str(pr).isdigit():
            return None, "this issue has no PR to merge"
        args["pr"] = int(pr)

    if verb == "kill":
        sessions = payload.get("sessions")
        if not (isinstance(sessions, list) and sessions):
            return None, "no session recorded on this issue — nothing to kill"

    return args, None


def action_summary(verb, args):
    """The sentence the confirmation prompt asks. Names the exact target."""
    if verb == "tick":
        return "run a tick now?"
    repo = args.get("repo", "?")
    if verb == "nudge":
        return "nudge %s?" % repo
    if verb == "kill":
        return "KILL the running session for %s#%s?" % (repo, args.get("issue"))
    if verb == "merge":
        return "MERGE PR %s for %s#%s?" % (args.get("pr"), repo, args.get("issue"))
    if verb == "review_now":
        return "add auto-land for %s#%s (review, then merge)?" % (
            repo, args.get("issue"))
    return "%s %s?" % (verb, args)


def read_tail(path):
    """(lines, None) or (None, error). Same guard and parse as server.a_tail.

    The guard is duplicated rather than imported because importing the server
    pulls in its tick thread's module-level setup; the rule itself is the same
    one, and it is the rule that matters: only ever a transcript under the
    projects tree.
    """
    f = Path(path)
    if not str(f).startswith(str(TRANSCRIPTS)) or f.suffix != ".jsonl":
        return None, "not a session transcript"
    try:
        raw = f.read_text(errors="replace")
    except OSError as e:
        return None, f"cannot read {f}: {e.strerror or e}"
    out = []
    for line in raw.splitlines()[-400:]:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        c = d.get("message", {}).get("content", "")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        if not c:
            continue
        out.append(f"--- {d['type']} ---")
        # The pager can scroll, unlike the web tail, so keep whole paragraphs
        # and let wrapping happen at the window edge instead of truncating.
        out.extend(str(c)[:600].splitlines() or [""])
        out.append("")
    if not out:
        return None, "no text turns in transcript"
    return out, None


# --- the app --------------------------------------------------------------------

class Tui:
    def __init__(self, feed_path):
        self.feed_path = feed_path
        self.feed = None
        self.error = None
        self.roots = []
        self.open = set()       # keys of expanded rows
        self.sel = 0            # index into the flattened, visible row list
        self.top = 0            # first visible row, for scrolling
        self.msg = ""
        # Availability of the ACTIONS, decided the one way the web page decides
        # it: a probe POST. Not a guess from a config value — the only thing
        # that matters is whether the server answers right now.
        self.can_act = False
        self.why_not = "not probed yet"
        self.reload()
        # Zone A first: it is the one zone that means someone has to act.
        if self.roots:
            self.open.add(self.roots[0].key)

    # --- state ---

    def reload(self):
        self.feed, self.error = M.load_feed(self.feed_path)
        # A missing feed is a normal state, not a crash: tree() on None still
        # yields the three zone headers, so the UI keeps its shape and `r` can
        # retry once the server or the tick comes back.
        self.roots = M.tree(self.feed)
        self.sel = min(self.sel, max(0, len(self.visible()) - 1))
        # On startup and on every `r` — NOT on a cadence. There is still no
        # timer and no thread in this file; this runs on the keypress that
        # already reloads, so the two views of the world are taken together.
        self.can_act, self.why_not = A.probe()

    def visible(self):
        """Flatten the tree to the rows currently on screen, in order."""
        out = []

        def walk(rows):
            for r in rows:
                out.append(r)
                if r.children and r.key in self.open:
                    walk(r.children)

        walk(self.roots)
        return out

    def current(self):
        rows = self.visible()
        return rows[self.sel] if 0 <= self.sel < len(rows) else None

    # --- drawing ---

    def draw(self, scr):
        scr.erase()
        h, w = scr.getmaxyx()
        rows = self.visible()

        trust = M.trust_line(self.feed)
        # Reverse video when behind: this is the line that says whether to
        # believe anything else on the screen, and a quiet version of it is
        # worse than none.
        loud = "BEHIND" in trust or self.feed is None
        put(scr, 0, 0, trust.ljust(max(0, w)),
            curses.A_REVERSE if loud else curses.A_BOLD)

        body_top, body_h = 1, max(0, h - 2)
        if self.error:
            put(scr, 1, 0, f"feed error: {self.error}  —  press r to retry",
                curses.A_BOLD)
            body_top, body_h = 2, max(0, h - 3)

        # Keep the selection inside the viewport without ever scrolling past
        # the ends, which is what makes g/G and PgDn safe on a 10-line window.
        if body_h > 0:
            if self.sel < self.top:
                self.top = self.sel
            elif self.sel >= self.top + body_h:
                self.top = self.sel - body_h + 1
            self.top = max(0, min(self.top, max(0, len(rows) - body_h)))

        for n in range(body_h):
            i = self.top + n
            if i >= len(rows):
                break
            r = rows[i]
            y = body_top + n
            attr = self._attr(r)
            if i == self.sel:
                attr = curses.A_REVERSE
                fill(scr, y, attr)
            marker = " "
            if r.children:
                marker = "-" if r.key in self.open else "+"
            put(scr, y, 0, f"{marker} {'  ' * r.depth}{r.text}", attr)

        # The action keys are named in the footer whether or not they work; a
        # disabled marker is the point, so the operator can see the capability
        # and ask why rather than never learn it exists (#66).
        acts = "".join(k for k, _, _ in ACTIONS)
        acts = f"{acts} act" if self.can_act else f"{acts} act(off)"
        foot = self.msg or \
            f"j/k move  Enter expand  t tail  r refresh  {acts}  ? help  q back/quit"
        put(scr, h - 1, 0, foot.ljust(max(0, w)), curses.A_REVERSE)
        scr.noutrefresh()
        curses.doupdate()

    def _attr(self, r):
        if r.kind == "zone":
            return curses.A_BOLD
        if r.kind in ("alert", "awaiting"):
            return curses.A_BOLD
        return curses.A_NORMAL

    # --- keys ---

    def move(self, delta):
        rows = self.visible()
        if rows:
            self.sel = max(0, min(len(rows) - 1, self.sel + delta))

    def expand(self):
        r = self.current()
        if r and r.children:
            self.open.add(r.key)
        elif r:
            self.msg = "nothing to expand"

    def collapse(self):
        """Close the selected row, else jump to and close its parent.

        This is the `q` half of tig's model: q walks you back OUT of the tree
        one level at a time, and only quits once there is no level left.
        Returns False when it could not go up, i.e. time to quit.
        """
        r = self.current()
        if r is None:
            return False
        if r.children and r.key in self.open:
            self.open.discard(r.key)
            return True
        parent = self._parent_of(r.key)
        if parent is None:
            return False
        self.open.discard(parent.key)
        rows = self.visible()
        if parent in rows:
            self.sel = rows.index(parent)
        return True

    def _parent_of(self, key, rows=None, parent=None):
        for r in (self.roots if rows is None else rows):
            if r.key == key:
                return parent
            found = self._parent_of(key, r.children, r)
            if found is not None or any(c.key == key for c in r.children):
                return found if found is not None else r
        return None

    def tail(self, scr):
        r = self.current()
        if r is None:
            return
        path = session_file(r)
        if not path:
            # Deliberately specific: the ledger genuinely has no path, and an
            # operator should learn that rather than think the tail is broken.
            self.msg = "no transcript path on this row"
            return
        lines, err = read_tail(path)
        if err:
            self.msg = err
            return
        Pager(f"tail  {Path(path).name}", lines).run(scr)
        self.msg = ""

    # --- actions ---

    def confirm(self, scr, question, destructive=False):
        """Block on y/n. Only `y` is yes; everything else, including Escape and
        a resize, cancels. Drawn as the footer line so it degrades in a narrow
        window the same way every other line does — through put()'s clip.
        """
        h, w = scr.getmaxyx()
        prompt = ("%s%s  [y/N]" % ("DESTRUCTIVE: " if destructive else "", question))
        put(scr, h - 1, 0, prompt.ljust(max(0, w)), curses.A_REVERSE | curses.A_BOLD)
        scr.noutrefresh()
        curses.doupdate()
        try:
            ch = scr.getch()
        except KeyboardInterrupt:
            return False
        return ch in (ord("y"), ord("Y"))

    def act(self, scr, verb):
        """One keypress -> at most one POST. Availability, then applicability,
        then confirmation, and only then the network."""
        if not self.can_act:
            # Shown and disabled, never hidden: the key is in the help and in
            # the footer, and pressing it explains itself (#66).
            self.msg = "%s unavailable: %s" % (verb, self.why_not)
            return
        args, why = row_args(self.current(), verb)
        if why:
            self.msg = why
            return
        if not self.confirm(scr, action_summary(verb, args), verb in DESTRUCTIVE):
            self.msg = "%s cancelled — nothing was sent" % verb
            return
        ok, out = A.post(verb, **args)
        # The feed the action just changed is stale the moment it returns, so
        # take the same path `r` takes rather than leaving a screen that
        # disagrees with what the operator was told.
        self.reload()
        # A failure must not read like a success at a glance, hence the prefix.
        self.msg = ("%s: %s" % (verb, out or "done")) if ok \
            else ("FAILED %s: %s" % (verb, out or "no reason given"))

    def help(self, scr):
        lines = ["KEYS", ""]
        lines += [f"  {k:<16}{d}" for k, d in KEYMAP]
        lines += ["", "PAGER (t)", ""]
        lines += [f"  {k:<16}{d}" for k, d in
                  (("j / k", "scroll"), ("PgUp / PgDn", "page"),
                   ("g / G", "top / bottom"), ("q", "back to the tree"))]
        if self.can_act:
            head, tag = "ACTIONS", ""
        else:
            head = f"ACTIONS — UNAVAILABLE ({self.why_not})"
            tag = "  [unavailable]"
        lines += ["", head, ""]
        # Listed either way. An unavailable action keeps its line and gains a
        # reason; it is never dropped from this list (#66).
        lines += [f"  {k:<16}{d}{tag}" for k, _, d in ACTIONS]
        lines += ["", "  Every action confirms first; only y proceeds.",
                  "  Actions post to orch-web; the feed is still read from disk."]
        lines += ["", f"feed: {self.feed_path}",
                  "", "This view never auto-refreshes. Press r."]
        Pager("help", lines).run(scr)

    # --- loop ---

    def run(self, scr):
        curses.curs_set(0)
        scr.keypad(True)
        while True:
            self.draw(scr)
            try:
                ch = scr.getch()   # blocking on purpose: no timer, ever
            except KeyboardInterrupt:
                return
            self.msg = ""
            h, _ = scr.getmaxyx()
            page = max(1, h - 3)
            rows = self.visible()

            if ch == curses.KEY_RESIZE:
                continue
            elif ch in (ord("j"), curses.KEY_DOWN):
                self.move(1)
            elif ch in (ord("k"), curses.KEY_UP):
                self.move(-1)
            elif ch in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord("l")):
                self.expand()
            elif ch == ord("q"):
                if not self.collapse():
                    return
            elif ch == ord("Q"):
                return
            elif ch == ord("r"):
                self.reload()
                self.msg = f"reloaded {self.feed_path}" if self.can_act else \
                    f"reloaded; actions unavailable: {self.why_not}"
            elif ch == ord("t"):
                self.tail(scr)
            elif ch == ord("?"):
                self.help(scr)
            elif ch == ord("g"):
                self.sel = 0
            elif ch == ord("G"):
                self.sel = max(0, len(rows) - 1)
            elif ch == curses.KEY_NPAGE:
                self.move(page)
            elif ch == curses.KEY_PPAGE:
                self.move(-page)
            elif ch in (curses.KEY_LEFT, ord("h")):
                self.collapse()
            elif 0 <= ch < 128 and chr(ch) in ACTION_KEYS:
                self.act(scr, ACTION_KEYS[chr(ch)])


class Pager:
    """A scrollable full-screen text view. Used for the help and the tail."""

    def __init__(self, title, lines):
        self.title = title
        self.lines = lines or ["(empty)"]
        self.top = 0

    def run(self, scr):
        while True:
            h, w = scr.getmaxyx()
            body = max(0, h - 2)
            self.top = max(0, min(self.top, max(0, len(self.lines) - body)))
            scr.erase()
            put(scr, 0, 0, self.title.ljust(max(0, w)), curses.A_REVERSE)
            for n in range(body):
                i = self.top + n
                if i >= len(self.lines):
                    break
                put(scr, 1 + n, 0, self.lines[i])
            put(scr, h - 1, 0,
                "j/k scroll  PgUp/PgDn page  g/G top/bottom  q back".ljust(max(0, w)),
                curses.A_REVERSE)
            scr.noutrefresh()
            curses.doupdate()
            try:
                ch = scr.getch()
            except KeyboardInterrupt:
                return
            if ch in (ord("q"), 27):
                return
            elif ch in (ord("j"), curses.KEY_DOWN):
                self.top += 1
            elif ch in (ord("k"), curses.KEY_UP):
                self.top -= 1
            elif ch == curses.KEY_NPAGE:
                self.top += max(1, body)
            elif ch == curses.KEY_PPAGE:
                self.top -= max(1, body)
            elif ch == ord("g"):
                self.top = 0
            elif ch == ord("G"):
                self.top = len(self.lines)
            self.top = max(0, self.top)


USAGE = """usage: python3 -m orch.tui [--feed PATH]

The orch terminal view: keyboard driven, reads the feed from disk so it works
when orch-web is stopped. It never auto-refreshes; press r.

Operator actions (n K M T R) post to orch-web on 127.0.0.1:$ORCH_PORT and each
one confirms first. When the server is down they are listed as unavailable with
the reason, never hidden. Press ? for the list.

  --feed PATH   status.json to read (default: %s)
  -h, --help    this message
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    feed = DEFAULT_FEED
    while argv:
        a = argv.pop(0)
        if a in ("-h", "--help"):
            print(USAGE % DEFAULT_FEED)
            return 0
        if a == "--feed":
            if not argv:
                print("--feed needs a path", file=sys.stderr)
                return 2
            feed = Path(argv.pop(0))
        elif a.startswith("--feed="):
            feed = Path(a.split("=", 1)[1])
        else:
            print(f"unknown argument: {a}", file=sys.stderr)
            print(USAGE % DEFAULT_FEED, file=sys.stderr)
            return 2

    app = Tui(feed)
    try:
        # wrapper restores the terminal on ANY exit path, including a
        # traceback. Without it a crash leaves the operator with no echo and
        # no cursor, which is a worse failure than the one that caused it.
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        return 130
    except curses.error as e:
        print(f"terminal error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
