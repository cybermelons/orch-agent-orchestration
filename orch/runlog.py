#!/usr/bin/env python3
"""orch#138 -- translate one `claude -p --output-format stream-json` event
into one prose log line.

Before this: a live session's run log held only the spawn banner until the
process exited. `claude -p` in plain text mode writes its final message once,
at exit -- there is nothing to tail while the agent is working, so a stuck
or slow session is indistinguishable from a healthy one until it's done.

Why translate instead of piping the raw stream to the log: a two-word prompt
("say ok") emitted roughly 25 KB of stream-json on this host, almost all of
it hook events echoing full skill text back verbatim. Writing that raw would
make the run log worse than the banner it replaces -- unreadable, and too
big to tail. This module is the narrow translator: one JSON event in, at
most one short prose line out. The run log is a prose surface that
`spawn.py tail` prints and that agent docs point readers to; the pump that
calls this (a later unit) owns the subprocess, the file writes, and the
loop. This module owns none of that -- no I/O, no imports beyond the
stdlib -- so it can be tested by passing dicts straight in.

ROBUSTNESS IS THE POINT: this runs inside a spawn. A malformed or
unexpected-shape event must degrade to None, never raise -- one bad event
must never take down the session that's producing it.

THE LINE SHAPE IS AN INTERFACE. orch#151 renders these rows in the
dashboard's RUN LOG section, so the format below is its input, not a
private detail. A line is:

    HH:MM:SS <kind padded to KIND_WIDTH> <body>
    00:12:09 tool      Bash: check worktree state
    00:12:11 tool   ok
    00:12:40 result    success, 4 turns, $0.08

Timestamp, then a fixed-width kind field, then the body -- so a reader can
split on the first two whitespace-delimited fields and treat the remainder
as prose. Kinds in use: assistant, tool, result, init, ratelimit, raw. The
`tool   ok` / `tool  ERR` variants are still kind `tool`, with the status
right-aligned inside the same KIND_WIDTH field. The field's last column is
always a space, so every kind's body starts at the same column and a named
result splits as ["tool", "ok", "Bash"] rather than fusing into "okBash".
Changing this shape means changing orch#151 with it.
"""
import json
import re
from datetime import datetime

# Truncation limit for a rendered body. Chosen for a terminal-width tail,
# not for any protocol reason -- bump freely if `spawn.py tail` output
# feels clipped.
MAX_BODY = 200

# Field width for the kind column. Kept a module constant, not a literal
# repeated at each call site, because the "tool    ok" / "tool   ERR"
# variants (translate() for type=="user") must land on the exact same
# width as the plain `"tool".ljust(KIND_WIDTH)` used elsewhere, or the
# body column drifts out of alignment between adjacent lines.
KIND_WIDTH = 10


def _now(now):
    return now if now is not None else datetime.now().strftime("%H:%M:%S")


def _collapse(text):
    # Newlines (and other whitespace runs) collapse to single spaces --
    # a log line is one line, always, or `spawn.py tail` breaks on it.
    return " ".join(str(text).split())


def _truncate(body):
    body = _collapse(body)
    if len(body) > MAX_BODY:
        return body[: MAX_BODY - 3] + "..."
    return body


def _line(now, kind_field, body):
    # kind_field is pre-padded by callers that need a non-default padded
    # form (the "tool   ok" / "tool  ERR" status variants); everyone
    # else passes a bare kind name and gets the standard ljust here.
    # rstrip: an empty body would otherwise leave the field's padding
    # dangling as trailing whitespace on the line.
    return f"{now} {kind_field}{_truncate(body)}".rstrip()


def _kind(name):
    return name.ljust(KIND_WIDTH)


def _translate_assistant(event, now):
    try:
        blocks = event["message"]["content"]
    except Exception:
        return None
    if not isinstance(blocks, list):
        return None

    # One event, one line: return the FIRST usable block, preferring a
    # text block over a tool_use block when a message holds both -- the
    # prose is the higher-value read of what the agent is doing.
    text_line = None
    tool_line = None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        try:
            btype = block.get("type")
        except Exception:
            continue

        if btype == "text" and text_line is None:
            text = block.get("text")
            if text:
                text_line = _line(now, _kind("assistant"), text)

        elif btype == "tool_use" and tool_line is None:
            name = block.get("name")
            if not name:
                continue
            inp = block.get("input")
            if not isinstance(inp, dict):
                inp = {}
            desc = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
            # "Strip trailing ': ' when desc is empty" -- i.e. never render
            # a dangling separator for a tool_use with no describable input.
            body = f"{name}: {desc}" if desc else name
            tool_line = _line(now, _kind("tool"), body)

    return text_line if text_line is not None else tool_line


def _translate_user(event, now):
    try:
        blocks = event["message"]["content"]
    except Exception:
        return None
    if not isinstance(blocks, list):
        return None

    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        is_error = bool(block.get("is_error"))
        # NEVER include the tool result body -- it can be enormous (a
        # file read, a command's full stdout). The status is the entire
        # value of this line.
        # Right-aligned inside KIND_WIDTH, but the final column stays a
        # SPACE so the body starts where every other kind's body starts.
        # `_kind()` gets that separator for free from ljust padding; this
        # path builds its field by hand, so it has to reserve it. Without
        # it a named result renders "tool    okBash" -- the status and the
        # body fuse into one whitespace-delimited token, and the line shape
        # this module documents as orch#151's input stops being parseable.
        status = "ERR" if is_error else "ok"
        kind_field = ("tool" + status.rjust(KIND_WIDTH - 5)).ljust(KIND_WIDTH)
        # Best-effort tool name: tool_result carries none directly in the
        # observed shape, so this stays empty unless a future shape adds
        # one -- degrade gracefully rather than guess.
        name = block.get("name") or ""
        return _line(now, kind_field, name)
    return None


def _translate_result(event, now):
    subtype = event.get("subtype")
    parts = []
    if subtype:
        parts.append(str(subtype))
    turns = event.get("num_turns")
    if isinstance(turns, (int, float)):
        parts.append(f"{turns} turns")
    cost = event.get("total_cost_usd")
    if isinstance(cost, (int, float)):
        parts.append(f"${cost:.2f}")
    return _line(now, _kind("result"), ", ".join(parts))


def _translate_system(event, now):
    subtype = event.get("subtype")
    if not isinstance(subtype, str):
        return None
    if subtype == "init":
        # Kept deliberately: a bad model string (see the model-hierarchy
        # gotcha -- an anthropic/-prefixed string kills the session after
        # a clean-looking spawn) becomes visible right here, in the one
        # line a supervisor tailing the run log actually sees.
        model = event.get("model", "")
        session_id = event.get("session_id", "")
        return _line(now, _kind("init"), f"model={model} session={session_id}")
    if subtype.startswith("hook"):
        # Pure noise: these echo full skill text back verbatim.
        return None
    return None


def _translate_rate_limit(event, now):
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    status = info.get("status")
    if status == "allowed":
        return None
    util = info.get("utilization", "")
    body = f"{status}" if not util else f"{status}, utilization={util}"
    return _line(now, _kind("ratelimit"), body)


def translate(event: dict, now: str = None) -> "str | None":
    """Turn one already-parsed stream-json event into one log line, or None.

    Never raises: an unexpected shape for a known type degrades to None via
    the per-type helper's own defensive checks, and an unknown/missing type
    falls through to the catch-all below. A bare except around everything
    would hide the same bugs this module exists to surface, so each helper
    guards its own field access instead.
    """
    if not isinstance(event, dict):
        return None

    etype = event.get("type")
    ts = _now(now)

    try:
        if etype == "assistant":
            return _translate_assistant(event, ts)
        if etype == "user":
            return _translate_user(event, ts)
        if etype == "result":
            return _translate_result(event, ts)
        if etype == "system":
            return _translate_system(event, ts)
        if etype == "rate_limit_event":
            return _translate_rate_limit(event, ts)
    except Exception:
        # Belt-and-suspenders: the helpers above are written to degrade to
        # None on malformed input by construction (isinstance checks before
        # every subscript), not by catching here. This backstop exists only
        # for the shape nobody has seen yet -- it must never be the primary
        # defense, or a real bug in a helper silently turns into "no log
        # line" instead of a visible failure during development.
        return None

    return None


def translate_line(raw: str, now: str = None) -> "str | None":
    """Parse one raw stdout line from `claude -p --output-format stream-json`
    and translate it. Never raises, never returns None on unparseable input:
    a non-JSON line from the CLI is usually an error itself, so it is high
    value and gets surfaced as-is (stripped, truncated) rather than dropped.
    """
    stripped = raw.strip() if isinstance(raw, str) else str(raw).strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except Exception:
        return _line(_now(now), _kind("raw"), stripped)

    return translate(event, now)


# === orch#151: parse() -- the read side of the format above =================
# core.py:2262-2264 records that readers split runs on the `=== ... ===`
# banner; this regex is that split point. Tolerant by design: a banner whose
# inner fields don't match still starts a new run (group() returns None for
# an unmatched optional group rather than raising), because getting the
# split right matters more than recovering every field.
_BANNER_RE = re.compile(
    r"^=== (?P<ts>\S+) pgid (?P<pgid>\S+) caveman=(?P<caveman>on|off) ===\s*$"
)

# Body line: "HH:MM:SS <kind>     <body>" -- first two whitespace-delimited
# fields, remainder is prose. Matches the docstring's own split description.
_BODY_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<rest>\S.*)$")

# The right-aligned "tool   ok" / "tool  ERR" status variants _translate_user
# writes by hand (not through _kind()'s ljust). Captured separately from the
# generic body split above so status lands in its own field instead of
# fusing into body's first word.
#
# The separator after the status is REQUIRED, and the status must end the
# word. Allowing it to match a prefix meant a tool whose name merely starts
# with those letters was shredded: "tool      okra: x" read back as
# status "ok" with body "ra: x", inventing a success result the write side
# never emitted and mangling the name. The write side always emits the
# status as its own word, so demanding that costs nothing.
_TOOL_STATUS_RE = re.compile(r"^tool\s+(?P<status>ok|ERR)(?:\s+(?P<body>.*))?$")


def _new_run(started=None, pgid=None, caveman=None):
    return {"started": started, "pgid": pgid, "caveman": caveman, "rows": []}


def _parse_banner(line):
    m = _BANNER_RE.match(line)
    if not m:
        # Tolerant per the brief: still a new run, just with nothing known.
        return _new_run()
    caveman_raw = m.group("caveman")
    caveman = {"on": True, "off": False}.get(caveman_raw)
    return _new_run(started=m.group("ts"), pgid=m.group("pgid"), caveman=caveman)


def _parse_row(line):
    m = _BODY_RE.match(line)
    if not m:
        # Doesn't fit the shape at all -- raw stderr passed through
        # untranslated, or anything else unrecognized. Never dropped.
        return {"time": None, "kind": "raw", "status": None, "body": line}

    time_field = m.group("time")
    rest = m.group("rest")

    # rest starts at "<kind><padding><body>". Split on the first run of
    # whitespace to get kind vs. remainder, same as the docstring's
    # "split on the first two whitespace-delimited fields" rule.
    parts = rest.split(None, 1)
    kind = parts[0]
    body = parts[1] if len(parts) > 1 else ""

    if kind == "tool":
        # Could be a plain "tool      Bash: ..." line (status None) or one
        # of the right-aligned "tool   ok" / "tool  ERR" status variants
        # (_translate_user builds these by hand: "tool   ok" or, with a
        # name, "tool   ok Bash"). Match against the ORIGINAL rest, not the
        # generic split() above, since that split already ate the
        # right-alignment whitespace that distinguishes the two shapes.
        sm = _TOOL_STATUS_RE.match(rest)
        if sm:
            # A bare "tool   ok" leaves the body group unparticipating; the
            # field stays a string so callers never have to test for None.
            return {"time": time_field, "kind": "tool",
                     "status": sm.group("status"), "body": sm.group("body") or ""}
        return {"time": time_field, "kind": "tool", "status": None, "body": body}

    return {"time": time_field, "kind": kind, "status": None, "body": body}


def parse(text, limit=None):
    """Parse a run-log file's full text into a list of run dicts, newest run
    first. Read side of the format `_line()` above writes -- see the module
    docstring for the shape. No I/O here: text in, list out, so this stays
    testable by passing strings directly.

    Never raises: a line that fits no known shape degrades to a `raw` row
    with the line verbatim as its body, exactly the robustness rule the
    write side holds -- a bad line must not break the page rendering it.
    """
    if not text:
        return []

    # Oldest-first while building (banners appear in file order); reversed
    # once at the end. Content before the first banner becomes a leading
    # run with started/pgid/caveman all None.
    runs = [_new_run()]

    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("==="):
            runs.append(_parse_banner(line))
            continue
        runs[-1]["rows"].append(_parse_row(line))

    # Drop the synthetic leading run if nothing landed in it and it never
    # got its own banner -- an empty placeholder run is not useful output.
    if len(runs) > 1 and not runs[0]["rows"] and runs[0]["started"] is None:
        runs.pop(0)

    runs.reverse()  # newest run first

    if limit is not None:
        remaining = limit
        capped = []
        for run in runs:
            if remaining <= 0:
                break
            rows = run["rows"][-remaining:] if remaining < len(run["rows"]) else run["rows"]
            if not rows:
                continue
            remaining -= len(rows)
            capped.append({**run, "rows": rows})
        runs = capped

    return runs
