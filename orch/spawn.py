#!/usr/bin/env python3
"""orch CLI — the agent's and operator's action surface. Stays named
spawn.py: DESIGN.md's Surfaces table already calls this file "agents'
and the operator's spawn verb", and every agent doc hands out the literal
path ~/orch/spawn.py. Agent surface must equal operator surface: server.py's
/act gives the operator 8 actions over HTTP, this file gives agents (and the
operator, from a terminal) the same verbs, one implementation each, shared
with server.py via core.py.

    echo "<brief>" | ~/orch/spawn.py <role> <scope...> [--fresh]
    ~/orch/spawn.py kill <key>
    ~/orch/spawn.py tick
    ~/orch/spawn.py status [key]
    ~/orch/spawn.py tail <key> [n]
    ~/orch/spawn.py watch <path>
    ~/orch/spawn.py unwatch <path>
    ~/orch/spawn.py nudge <slug> [text...]
    ~/orch/spawn.py ask <slug> <text...>
    ~/orch/spawn.py journal <scope> <target> <event> <note...>
    ~/orch/spawn.py issue create <slug> <title>
    ~/orch/spawn.py issue comment <slug> <n>
    ~/orch/spawn.py issue close <slug> <n>
    ~/orch/spawn.py issue label-add <slug> <n> <label>
    ~/orch/spawn.py issue label-remove <slug> <n> <label>

Security: not reachable from the web surface. Agents invoke this through
their own Bash tool inside their own permission envelope; the operator
invokes it from a terminal. Both already hold arbitrary shell — this CLI
adds capability to no principal that lacked it. /act stays a closed,
validated, shell-free set for the web surface; this file is not that.

Runnable both as `python3 -m orch.spawn` and as a direct script `./spawn.py`.
"""
import json
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ in (None, ""):
    # Direct-script execution: put the repo root on sys.path so `from orch
    # import core` resolves. Guarded so it's a no-op under `-m orch.spawn`,
    # where the package is already importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orch import core, tick

ARITY = {"dashboard-op": 0, "repo-orch": 1, "issue-orch": 2}

USAGE = """\
usage: echo "<brief>" | spawn.py <role> <scope...> [--fresh]

  spawn.py dashboard-op
  spawn.py repo-orch <slug>
  spawn.py issue-orch <slug> <n>

  --fresh                      # start cold: ignore the key's transcript and
                               # do not --resume. Use when you judge the prior
                               # conversation spent (context exhausted, or it
                               # died confused). Default resumes.

  spawn.py kill <key>          # e.g. issue-orch.slug.42
  spawn.py tick                # one pulse now
  spawn.py status [key|slug]   # public/status.json, one ledger row, or one repo's row
  spawn.py tail <key> [n]      # last n lines of that key's run log (default 40)
  spawn.py watch <path>        # add a git repo to repos.txt
  spawn.py unwatch <path>      # remove it
  spawn.py nudge <slug> [text...]        # spawn that repo's repo-orch with a nudge
  spawn.py ask <slug> <text...>  # leave a note in that repo's journal; spawns
                                # nothing, read on the repo-orch's next wake
  spawn.py merge <slug> <issue> <pr> [--by issue-orch|merge-blocked] [--review clean|restored-hold]
                                # land a REVIEW unit's PR; --by defaults to issue-orch
  spawn.py review <slug> <issue> <pr>
                                # does PR <pr> carry an orch:review:v1 block?
                                # prints it if so; else prints the reviewer
                                # contract (agents/reviewer.md + its inputs).
                                # Never dispatches a reviewer itself.

  spawn.py journal repo <slug> <event> <note...>   # append to that repo's journal
  spawn.py journal dashboard <event> <note...>     # append to the dashboard journal
  spawn.py journal dashboard handled --digest <d>  # the ack row
  spawn.py journal repo <slug> consolidated --covered <12,15,88> <note...>
                                # --covered lists every open issue this row
                                # considered, related or declined
  spawn.py journal repo <slug> consolidated --folds <284:212,...> <note...>
                                # --folds pairs <superseded>:<survivor> issue
                                # numbers, recording which direction won

  spawn.py names <slug> <n>=<name> [<n>=<name>=<title> ...]
                                # write-once issue display-name cache; an
                                # already-named issue is left alone. Optional
                                # <n>=<name>=<title> third field to store a
                                # title too (default "")

  spawn.py decision <id> <ruling> [--question Q] [--by WHO] [--ref R]
                    [--supersedes ID] [--scope S]
                                # append a row to the repo-wide decisions
                                # ledger; --by defaults to unattributed
                                # (operator|delegated:<who>|unattributed),
                                # --scope defaults to repo-wide
                                # (repo-wide|per-repo:<slug>|per-issue:<n>).
                                # Never gates or authorizes -- record only.

  spawn.py decisions [<id>]    # no id: list every resolved ruling, one per
                                # line; with id: print that row in full.
                                # Unknown id: "no record" and exit 0 -- a
                                # miss means "not settled, go re-derive", not
                                # failure. Never gates or authorizes.

  spawn.py issue create <slug> <title>   # body on stdin, optional
  spawn.py issue comment <slug> <n>      # body on stdin, required
  spawn.py issue close <slug> <n>
  spawn.py issue label-add <slug> <n> <label>
  spawn.py issue label-remove <slug> <n> <label>
                                # thin passthrough over RepoAdapter's argv
                                # tables; the caller names the action and
                                # orch picks gh or tea. A key the selected
                                # backend lacks errors with "not available
                                # on the <backend> backend" rather than
                                # falling back across tables.
"""


def cmd_spawn(argv):
    # --fresh anywhere in argv: start the key cold, ignoring any transcript.
    # Stripped before arity checking so scope stays purely positional.
    fresh = "--fresh" in argv
    argv = [a for a in argv if a != "--fresh"]
    if not argv or argv[0] not in ARITY:
        sys.stderr.write(USAGE)
        return 2
    role = argv[0]
    scope = tuple(argv[1:])
    if len(scope) != ARITY[role]:
        sys.stderr.write(USAGE)
        return 2

    if sys.stdin.isatty():
        sys.stderr.write("error: brief must be piped on stdin, not typed at a tty\n\n")
        sys.stderr.write(USAGE)
        return 2
    brief = sys.stdin.read()

    key = core.key_for(role, *scope)
    # The pgid spawn() returns is the pre-existing one when it refused, and a
    # new one when it launched. Comparing against the row we saw BEFORE the
    # call reports which happened without re-deriving liveness outside the
    # lock -- an alive() sampled here races the row spawn() takes the flock on,
    # and a session dying in that window misprints "already alive".
    before = core.ledger_read(key) or {}
    pgid = core.spawn(role, scope, brief, fresh=fresh)

    if pgid is not None and pgid == before.get("pgid"):
        print(f"{key} already alive, refusing to double-spawn: pgid={pgid}")
    else:
        print(f"{key} pgid={pgid}")
    return 0


def cmd_kill(argv):
    """kill <key> — idempotent: no row or already-dead exits 0 so a recovery
    script never fails because the wedged thing already died. Exit 2 only
    for a key that doesn't parse. Calls the same core.kill_key() the HTTP
    /act kill handler (server.a_kill) calls."""
    if len(argv) != 1:
        sys.stderr.write(USAGE)
        return 2
    key = argv[0]
    try:
        core._split_key(key)
    except ValueError as e:
        print(f"bad key {key!r}: {e}")
        return 2
    row = core.ledger_read(key)
    if not row or not core._pgid_alive(row.get("pgid")):
        print(f"{key}: no live session (nothing to kill)")
        return 0
    gid = row.get("pgid")
    core.kill_key(key)
    print(f"{key}: killed pgid {gid}")
    return 0


def cmd_tick(argv):
    """One pulse now. Calls tick.main() in-process — the same function
    orch.ticker's loop and the dashboard's /act tick both eventually invoke
    as a subprocess."""
    return tick.main()


def cmd_status(argv):
    """No key: public/status.json (the agent's READ surface, works with the
    server down). With a key: that key's ledger row, distilled. With a bare
    slug (not a valid key): that repo's row out of status.json's `repos`
    list — the cheap way to read just your own repo's facts instead of the
    whole file."""
    if len(argv) == 0:
        f = core.ORCH_HOME / "public" / "status.json"
        if not f.exists():
            print("no status.json yet (has the tick ever run?)")
            return 1
        print(f.read_text())
        return 0
    if len(argv) != 1:
        sys.stderr.write(USAGE)
        return 2
    key = argv[0]
    try:
        role_scope = core._split_key(key)
    except ValueError as e:
        f = core.ORCH_HOME / "public" / "status.json"
        if not f.exists():
            print("no status.json yet (has the tick ever run?)")
            return 1
        repos = json.loads(f.read_text()).get("repos", [])
        for repo in repos:
            if repo.get("slug") == key:
                for field, value in repo.items():
                    if field == "issues":
                        print(f"issues: {len(value)}")
                        for iss in value:
                            brief = iss.get("brief") or {}
                            text = brief.get("text") or ""
                            if len(text) > 80:
                                text = text[:80] + "...[truncated]"
                            print(
                                f"  #{iss.get('issue')} {iss.get('state')}/"
                                f"{iss.get('work_state')} labels={iss.get('labels')} "
                                f"ready={iss.get('ready')} startable={iss.get('startable')} "
                                f"branch={iss.get('branch')} pr={iss.get('pr')} "
                                f"orch_alive={iss.get('orch_alive')} "
                                f"idle_min={iss.get('idle_min')} "
                                f"sessions={len(iss.get('sessions') or [])} "
                                f"title={iss.get('title')!r}"
                            )
                            if text:
                                print(f"    brief: {text!r}")
                    elif field == "all_issues":
                        # orch#417: the full open set (repo-orch's
                        # consolidation input), separate from `issues` above
                        # (the rendered agent-ready/agent-working slice).
                        print(f"all_issues: {len(value)}")
                        for iss in value:
                            print(f"  #{iss.get('number')} {iss.get('state')} "
                                  f"labels={iss.get('labels')} title={iss.get('title')!r}")
                    else:
                        print(f"{field}: {value}")
                return 0
        print(f"bad key {key!r}: {e}; not a known repo slug either")
        return 2
    row = core.ledger_read(key)
    cwd = core.cwd_for(*role_scope)
    print(f"key: {key}")
    print(f"alive: {core.alive(key)}")
    print(f"cwd: {cwd}")
    print(f"prior_runs: {core.prior_runs(key)}")
    if row:
        print(f"log: {row.get('log')}")
        print(f"started: {row.get('started')}")
    rid = core.resume_id_for(key)
    if rid:
        print(f"resume: cd {cwd} && claude --resume {rid}")
    else:
        print("resume: no transcript found for this key")
    return 0


def cmd_tail(argv):
    """tail <key> [n] — last n lines of state/sessions/<key>.log (the run
    log: stdout/stderr of the spawned process). Deliberately DIFFERENT from
    HTTP a_tail, which takes a transcript *file path* and renders parsed
    conversation turns — run log vs conversation are different questions;
    both are worth having."""
    if len(argv) not in (1, 2):
        sys.stderr.write(USAGE)
        return 2
    key = argv[0]
    try:
        core._split_key(key)
    except ValueError as e:
        print(f"bad key {key!r}: {e}")
        return 2
    n = int(argv[1]) if len(argv) == 2 else 40
    f = core.ledger_log_path(key)
    if not f.exists():
        print(f"no log for {key}")
        return 1
    lines = f.read_text(errors="replace").splitlines()[-n:]
    print("\n".join(lines))
    return 0


def cmd_watch(argv):
    """watch <path> — same core.watch_repo() the HTTP /act watch handler
    calls."""
    if len(argv) != 1:
        sys.stderr.write(USAGE)
        return 2
    ok, out = core.watch_repo(argv[0])
    print(out)
    return 0 if ok else 2


def cmd_unwatch(argv):
    """unwatch <path> — same core.unwatch_repo() the HTTP /act unwatch
    handler calls."""
    if len(argv) != 1:
        sys.stderr.write(USAGE)
        return 2
    ok, out = core.unwatch_repo(argv[0])
    print(out)
    return 0 if ok else 2


def cmd_nudge(argv):
    """nudge <slug> [text...] — same core.nudge_repo() the HTTP /act nudge
    handler calls.

    Text may come as trailing argv or on stdin, matching how every other verb
    here takes a brief. Before orch#125's follow-up this verb took the slug
    and nothing else, so `echo "<instruction>" | spawn.py nudge <slug>` read
    as success and dropped the instruction on the floor."""
    if not argv:
        sys.stderr.write(USAGE)
        return 2
    repo = core.repo_path_for(argv[0])
    if not repo:
        print(f"not a watched repo: {argv[0]}")
        return 2
    text = " ".join(argv[1:]).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    result = core.nudge_repo(repo, text)
    print(result["out"])
    return 0 if result["ok"] else 2


def cmd_ask(argv):
    """ask <slug> <text...> — same core.ask_repo() the HTTP /act ask
    handler calls. Remaining argv joined with spaces is the free-form text.

    Leaves a note; it does NOT spawn. The text is appended to the repo
    journal as an operator row and the next repo-orch reads it on its
    natural wake (docs/UX-REDESIGN.md section 4.2/5). `nudge` is the verb
    that wakes one now -- reach for it when the note cannot wait."""
    if len(argv) < 2:
        sys.stderr.write(USAGE)
        return 2
    repo = core.repo_path_for(argv[0])
    if not repo:
        print(f"not a watched repo: {argv[0]}")
        return 2
    result = core.ask_repo(repo, " ".join(argv[1:]))
    print(result["out"])
    return 0 if result["ok"] else 2


def cmd_merge(argv):
    """merge <slug> <issue> <pr> [--by issue-orch|merge-blocked]
    [--review clean|restored-hold] — same core.merge_pr() the HTTP /act merge
    handler calls. `--by` defaults to "issue-orch" here (an agent is always
    the caller of this verb, unlike the HTTP path's "operator" default)."""
    by = "issue-orch"
    review = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--by" and i + 1 < len(argv):
            by = argv[i + 1]
            i += 2
        elif argv[i] == "--review" and i + 1 < len(argv):
            review = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1

    if len(rest) != 3:
        sys.stderr.write(USAGE)
        return 2
    if by not in ("issue-orch", "merge-blocked"):
        sys.stderr.write(USAGE)
        return 2
    if review is not None and review not in ("clean", "restored-hold"):
        sys.stderr.write(USAGE)
        return 2

    repo = core.repo_path_for(rest[0])
    if not repo:
        print(f"not a watched repo: {rest[0]}")
        return 2
    try:
        issue = int(rest[1])
        pr = int(rest[2])
    except ValueError:
        sys.stderr.write(USAGE)
        return 2

    result = core.merge_pr(repo, issue, pr, decided_by=by, review=review)
    print(result["out"])
    return 0 if result["ok"] else 2


def cmd_review(argv):
    """review <slug> <issue> <pr> — answers "has this PR been reviewed?" from
    anywhere: reads the PR's comments for an `orch:review:v1` block via
    core.review_items_for_pr and prints what it finds.

    This verb does NOT run a reviewer and cannot: dispatching the
    Agent-tool subagent that writes the block is the calling agent's own
    act, not something a CLI subprocess can do on its own. Do not "complete"
    this later by shelling out to a model here -- that would just be a
    second, unauthoritative implementation of agents/reviewer.md's contract.
    When there is no block, this hands the caller the contract instead:
    where reviewer.md lives and the two inputs it needs (issue body, diff),
    so any caller -- agent or human -- can run the review itself."""
    if len(argv) != 3:
        sys.stderr.write(USAGE)
        return 2
    repo = core.repo_path_for(argv[0])
    if not repo:
        print(f"not a watched repo: {argv[0]}")
        return 2
    try:
        issue = int(argv[1])
        pr = int(argv[2])
    except ValueError:
        sys.stderr.write(USAGE)
        return 2

    found = core.review_items_for_pr(repo, pr)
    if found is None:
        print(f"no orch:review:v1 block found on PR {pr} -- no reviewer has run for "
              f"issue {issue}.")
        print("Contract: ~/orch/agents/reviewer.md")
        print("Inputs it needs: the issue body and the diff.")
        return 2

    print(f"PR {pr} carries a review (sha={found['sha']})")
    for item in found["items"]:
        print(f"  {item['n']} {item['verdict']} {item['location']} {item['finding']}")
    return 0


def cmd_journal(argv):
    """journal <scope> <target> <event> <note...> — same core.journal_append()
    the tick and the agents' own decisions go through. Exists because the
    shell form the agent docs used to print cannot run inside an agent's
    envelope: `$(date ...)` is un-analysable shell, and `>>` to a path outside
    the session cwd is refused before any allow rule is read. core stamps the
    timestamp itself, so neither is needed.

    `journal issue` is deliberately absent: issue-orch journals through
    `gh issue comment`, which is a different surface, not this one."""
    # --digest <value> anywhere in argv: dashboard-op's ack row carries a
    # digest instead of a note. Stripped before arity checking so scope,
    # target and event stay purely positional.
    digest = None
    if "--digest" in argv:
        i = argv.index("--digest")
        if i + 1 >= len(argv):
            sys.stderr.write(USAGE)
            return 2
        digest = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    # --covered <value> anywhere in argv: comma-separated issue numbers a
    # `consolidated` row considered, both the ones it related and the ones
    # it read and declined. Recorded so a declined issue is distinguishable
    # from one the row never looked at. Same shape as --digest above,
    # stripped before arity checking so scope, target and event stay purely
    # positional. Unlike --ref, a malformed value here is rejected outright
    # (USAGE, return 2) rather than deferred to a downstream guard: a
    # silently-dropped number would become a permanently-uncovered issue.
    covered = None
    if "--covered" in argv:
        i = argv.index("--covered")
        if i + 1 >= len(argv):
            sys.stderr.write(USAGE)
            return 2
        raw = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
        covered = []
        if raw.strip():
            for part in raw.split(","):
                part = part.strip()
                if not part.isdigit():
                    sys.stderr.write(USAGE)
                    return 2
                covered.append(int(part, base=10))

    # --folds <value> anywhere in argv: comma-separated <superseded>:<survivor>
    # pairs recording which direction a fold verdict actuated. The colon
    # carries the direction; without it a later session cannot tell a fold
    # from a mistake. Same shape as --digest above, stripped before arity
    # checking so scope, target and event stay purely positional. Like
    # --covered, malformed is rejected outright (USAGE, return 2): a
    # silently-dropped fold row means an issue was closed with no record why.
    folds = None
    if "--folds" in argv:
        i = argv.index("--folds")
        if i + 1 >= len(argv):
            sys.stderr.write(USAGE)
            return 2
        raw = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
        folds = []
        if raw.strip():
            for part in raw.split(","):
                part = part.strip()
                if part.count(":") != 1:
                    sys.stderr.write(USAGE)
                    return 2
                superseded, survivor = part.split(":")
                if not superseded.isdigit() or not survivor.isdigit():
                    sys.stderr.write(USAGE)
                    return 2
                folds.append({
                    "superseded": int(superseded, base=10),
                    "survivor": int(survivor, base=10),
                })

    if not argv:
        sys.stderr.write(USAGE)
        return 2

    # Every flag this verb knows (--digest/--covered/--folds) was already
    # stripped above, so any token still starting with "-" here is a flag
    # the parser does not recognize. Without this guard it falls through to
    # the positional slots and corrupts the record instead of erroring: a
    # stray `--ref 999` gets absorbed into the note as literal prose, and a
    # stray `--grep` gets taken as the event itself. Checked once here
    # (before the scope branch) so it covers both the event slot and the
    # note/rest for repo and dashboard alike. A lone "-" or a negative
    # number like "-5" is not a flag and must not trip this.
    for token in argv:
        if token.startswith("-") and token != "-" and not token[1:].lstrip("-").isdigit():
            sys.stderr.write(USAGE)
            return 2

    scope = argv[0]
    if scope == "issue":
        print("no journal verb for scope 'issue': issue-orch journals through "
              "`gh issue comment <n> --repo <owner/name>`, not this CLI")
        return 2
    if scope not in ("repo", "dashboard"):
        print(f"unknown journal scope {scope!r}: valid scopes are repo, dashboard")
        return 2

    if scope == "repo":
        if len(argv) < 3:
            sys.stderr.write(USAGE)
            return 2
        slug, event, rest = argv[1], argv[2], argv[3:]
        actor, repo = "repo-orch", slug
    else:
        if len(argv) < 2:
            sys.stderr.write(USAGE)
            return 2
        event, rest = argv[1], argv[2:]
        actor, repo = "dashboard-op", None

    extra = {}
    if digest is not None:
        extra["digest"] = digest
    if covered is not None:
        extra["covered"] = covered
    if folds is not None:
        extra["folds"] = folds
    note = " ".join(rest)
    if note:
        extra["note"] = note
    if not extra:
        sys.stderr.write(USAGE)
        return 2

    core.journal_append(scope, repo, None, actor, event, extra)
    print(f"{core.journal_path(scope, repo)}: {actor} {event} "
          + " ".join(f"{k}={v!r}" for k, v in extra.items()))
    return 0


def cmd_decision(argv):
    """decision <id> <ruling> [--question Q] [--by WHO] [--ref R]
    [--supersedes ID] [--scope S] — append one row to the repo-wide decisions
    ledger (core.decisions_append). Every flag accepted here is stored in the
    row and comes back out of core.decisions_read(): orch#366 was a flag
    (`--covered`) parsed and validated but only ever honoured on one journal
    event, so a use on any other row was silently permanent. A flag this verb
    does not store is rejected here instead, not accepted and dropped.

    Does not gate or authorize anything (core.decisions_read's docstring,
    docs/RESTRUCTURE-2026-09-16.md §4) -- it only records a ruling."""
    # --question/--by/--ref/--supersedes/--scope anywhere in argv, same
    # strip-before-arity-check style as --digest/--covered in cmd_journal, so
    # <id> and <ruling> stay purely positional.
    question, by, ref, supersedes, scope = "", "unattributed", "", None, "repo-wide"
    for flag in ("--question", "--by", "--ref", "--supersedes", "--scope"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                sys.stderr.write(USAGE)
                return 2
            value = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
            if flag == "--question":
                question = value
            elif flag == "--by":
                by = value
            elif flag == "--ref":
                ref = value
            elif flag == "--supersedes":
                supersedes = value
            elif flag == "--scope":
                scope = value

    # Stripped up front, same as <id>/<ruling> below, so a whitespace-only
    # suffix (e.g. "delegated:   ") fails the emptiness guards instead of
    # passing them: those guards test truthiness of the suffix, and " " is
    # truthy.
    by, scope = by.strip(), scope.strip()

    if by != "unattributed" and by != "operator" and not by.startswith("delegated:"):
        sys.stderr.write(USAGE)
        return 2
    if by.startswith("delegated:") and not by[len("delegated:"):].strip():
        sys.stderr.write(USAGE)
        return 2

    if scope != "repo-wide" and not (
        scope.startswith("per-repo:") or scope.startswith("per-issue:")
    ):
        sys.stderr.write(USAGE)
        return 2
    if scope.startswith("per-issue:") and not scope[len("per-issue:"):].isdigit():
        sys.stderr.write(USAGE)
        return 2
    if scope.startswith("per-repo:") and not scope[len("per-repo:"):].strip():
        sys.stderr.write(USAGE)
        return 2

    if len(argv) != 2:
        sys.stderr.write(USAGE)
        return 2
    id, ruling = argv[0].strip(), argv[1].strip()
    if not id or not ruling:
        sys.stderr.write(USAGE)
        return 2
    if supersedes is not None:
        # `.strip() or None`: a whitespace-only --supersedes must resolve to
        # "supersedes nothing" (None), same as omitting the flag -- every
        # other row uses None for that, and "" would be a third state
        # decisions_read() can never look up (it skips empty ids).
        supersedes = supersedes.strip() or None

    core.decisions_append(id, question, ruling, by, ref,
                           supersedes=supersedes, scope=scope)
    print(f"{core.decisions_path()}: {id} ruling={ruling!r} by={by!r} "
          f"scope={scope!r}")
    return 0


def cmd_decisions(argv):
    """decisions [<id>] — the read side of the ledger cmd_decision writes.
    No argument: every resolved row (core.decisions_read()), one line each,
    sorted by id, marking supersession. With <id>: that row's fields in full.

    Unknown id prints a "no record" line and returns 0, NOT an error: a
    missing ruling means "not settled, go re-derive", the same safe-loud
    direction decisions_read()'s docstring describes -- returning nonzero
    here would make a lookup miss look like a failure and invert that.

    supersedes only points forward (new row -> old id), so a lookup of the
    OLD id would otherwise print a reversed ruling with nothing on screen
    saying so (orch#368 4th review finding 1). Both forms here do a reverse
    scan of rows.values() for anyone whose supersedes names this id and
    surface it -- no new storage, no second read of the file.

    Does not gate or authorize anything (core.decisions_read's docstring,
    docs/RESTRUCTURE-2026-09-16.md §4) -- it only reads rulings."""
    if len(argv) > 1:
        sys.stderr.write(USAGE)
        return 2
    rows = core.decisions_read()

    def supersededby(id):
        return sorted(r["id"] for r in rows.values() if r.get("supersedes") == id)

    if not argv:
        for id in sorted(rows):
            row = rows[id]
            sup = f" (supersedes {row.get('supersedes')})" if row.get("supersedes") else ""
            flag = " [superseded]" if supersededby(id) else ""
            scope = row.get("scope")
            scope_part = f" scope={scope!r}" if scope and scope != "repo-wide" else ""
            by = row.get("decided_by")
            by_part = f" by={by!r}" if by and by != "operator" else ""
            print(f"{id}: {row.get('ruling')!r} ref={row.get('ref')!r}"
                  f"{scope_part}{by_part}{sup}{flag}")
        return 0
    # Same strip as the writer, so a padded id looks up the row the writer
    # stored under the unpadded one.
    id = argv[0].strip()
    row = rows.get(id)
    if row is None:
        print(f"no record for {id!r} -- not settled, re-derive")
        return 0
    supersededby_ids = supersededby(id)
    if supersededby_ids:
        # Prominent, not buried: printed before the row's own fields so a
        # reader can't miss it and mistake this for a settled ruling.
        print(f"SUPERSEDED BY: {', '.join(supersededby_ids)}")
    for field in ("id", "question", "ruling", "decided_by", "at", "ref",
                  "scope", "supersedes"):
        print(f"{field}: {row.get(field)}")
    return 0


def cmd_names(argv):
    """names <slug> <n>=<name> [<n>=<name>=<title> ...] — write-once cache
    (core.names_write): an already-named issue is left alone, never
    overwritten. No gh/tea call; title is "" unless a 3rd `=`-field is given."""
    if len(argv) < 2:
        sys.stderr.write(USAGE)
        return 2
    slug = argv[0]
    pairs = {}
    for arg in argv[1:]:
        parts = arg.split("=", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            sys.stderr.write(f"names: malformed arg {arg!r} (want n=name)\n")
            sys.stderr.write(USAGE)
            return 2
        n, rest = parts
        if not (n.isascii() and n.isdigit()):
            sys.stderr.write(f"names: malformed issue number {arg!r}\n")
            sys.stderr.write(USAGE)
            return 2
        name, _, title = rest.partition("=")
        if not name:
            sys.stderr.write(f"names: empty name in {arg!r}\n")
            sys.stderr.write(USAGE)
            return 2
        pairs[n] = {"name": name, "title": title}

    added = core.names_write(slug, pairs)
    for n in pairs:
        if n in added:
            print(f"added {n}={pairs[n]['name']!r}")
        else:
            print(f"skipped {n} (already named)")
    return 0


def _repo_for(slug):
    """Shared watched-repo gate every issue-verb handler below opens with.
    Mandatory, not boilerplate: orch#19 landed a verb that skipped this and
    any SLUG-shaped string reached gh. Returns (repo_path, adapter) or
    (None, None) after printing the same "not a watched repo" message every
    other verb here prints."""
    repo = core.repo_path_for(slug)
    if not repo:
        print(f"not a watched repo: {slug}")
        return None, None
    return repo, core.adapter_for(repo)


def _issue_num(n):
    """Parse an issue number argv token, USAGE-and-2 on anything else --
    same discipline as cmd_merge's issue/pr parse. Callers MUST route every
    number through here: a bare int() raises ValueError and exits on a
    traceback, which is not the clean usage error every other verb gives and
    is indistinguishable from a backend failure to a caller reading exit
    codes.

    A leading `#` is accepted because `#284` is how every orch doc and issue
    body writes an issue number, so it is what an agent copies; rejecting the
    spelling the docs teach would be a papercut with no upside."""
    try:
        return int(str(n).lstrip("#"))
    except ValueError:
        sys.stderr.write(f"issue: {n!r} is not an issue number\n")
        sys.stderr.write(USAGE)
        return None


def _build_argv(be, verb, key, *args):
    """be.<key>(*args), catching the no-fallback AttributeError
    RepoAdapter.__getattr__ raises when the selected backend's table lacks
    the key (e.g. issue_close is tea-only -- see core.GH_ARGV/TEA_ARGV).
    The attribute access itself is what raises, so the getattr has to happen
    inside this try, not the call. Never adds a fallback across tables: that
    would silently run the wrong backend's command (core.py's block comment
    above RepoAdapter.__getattr__)."""
    try:
        builder = getattr(be, key)
        return builder(*args)
    except AttributeError:
        sys.stderr.write(f"issue {verb}: not available on the {be.backend} backend\n")
        return None


def cmd_issue_create(argv):
    """issue create <slug> <title> — body on stdin, optional (unlike
    comment, an empty body is allowed here)."""
    if len(argv) != 2:
        sys.stderr.write(USAGE)
        return 2
    slug, title = argv
    repo, be = _repo_for(slug)
    if not repo:
        return 2
    body = "" if sys.stdin.isatty() else sys.stdin.read()
    argvout = _build_argv(be, "create", "issue_create", title, body)
    if argvout is None:
        return 2
    ok, out = core._run(argvout, cwd=repo)
    print(out.strip())
    return 0 if ok else 2


def cmd_issue_comment(argv):
    """issue comment <slug> <n> — body on stdin, required."""
    if len(argv) != 2:
        sys.stderr.write(USAGE)
        return 2
    slug, n = argv
    n = _issue_num(n)
    if n is None:
        return 2
    repo, be = _repo_for(slug)
    if not repo:
        return 2
    body = "" if sys.stdin.isatty() else sys.stdin.read()
    if not body.strip():
        sys.stderr.write("issue comment: body must be piped on stdin and non-empty\n")
        return 2
    argvout = _build_argv(be, "comment", "issue_comment", n, body)
    if argvout is None:
        return 2
    ok, out = core._run(argvout, cwd=repo)
    print(out.strip())
    return 0 if ok else 2


def cmd_issue_close(argv):
    """issue close <slug> <n>."""
    if len(argv) != 2:
        sys.stderr.write(USAGE)
        return 2
    slug, n = argv
    n = _issue_num(n)
    if n is None:
        return 2
    repo, be = _repo_for(slug)
    if not repo:
        return 2
    argvout = _build_argv(be, "close", "issue_close", n)
    if argvout is None:
        return 2
    ok, out = core._run(argvout, cwd=repo)
    print(out.strip())
    return 0 if ok else 2


def cmd_issue_label_add(argv):
    """issue label-add <slug> <n> <label>. On tea, ensures the label is
    defined on the repo first (core.ensure_label_exists): `tea issue edit
    --add-labels` silently drops an undefined label while exiting 0
    (orch#278), so applying blind would report success on a no-op."""
    if len(argv) != 3:
        sys.stderr.write(USAGE)
        return 2
    slug, n, label = argv
    n = _issue_num(n)
    if n is None:
        return 2
    repo, be = _repo_for(slug)
    if not repo:
        return 2
    ok, msg = core.ensure_label_exists(be, label)
    if not ok:
        print(msg)
        return 2
    argvout = _build_argv(be, "label-add", "issue_edit_add_label", n, label)
    if argvout is None:
        return 2
    ok, out = core._run(argvout, cwd=repo)
    print(out.strip())
    return 0 if ok else 2


def cmd_issue_label_remove(argv):
    """issue label-remove <slug> <n> <label>."""
    if len(argv) != 3:
        sys.stderr.write(USAGE)
        return 2
    slug, n, label = argv
    n = _issue_num(n)
    if n is None:
        return 2
    repo, be = _repo_for(slug)
    if not repo:
        return 2
    argvout = _build_argv(be, "label-remove", "issue_edit_remove_label", n, label)
    if argvout is None:
        return 2
    ok, out = core._run(argvout, cwd=repo)
    print(out.strip())
    return 0 if ok else 2


_ISSUE_GROUP = {
    "create": cmd_issue_create, "comment": cmd_issue_comment,
    "close": cmd_issue_close, "label-add": cmd_issue_label_add,
    "label-remove": cmd_issue_label_remove,
}


def cmd_issue(argv):
    """issue <create|comment|close|label-add|label-remove> ... — the
    user-facing `spawn.py issue <verb>` group. Dispatches by subcommand word
    to the flat VERBS handlers above; exists only so the ruled two-word
    spelling works while VERBS itself stays flat (test_conformance.py's VERBS
    regex would otherwise pick up a nested dict's inner keys as if they were
    top-level verbs)."""
    if not argv or argv[0] not in _ISSUE_GROUP:
        sys.stderr.write(USAGE)
        return 2
    return _ISSUE_GROUP[argv[0]](argv[1:])


VERBS = {
    "kill": cmd_kill, "tick": cmd_tick, "status": cmd_status, "tail": cmd_tail,
    "watch": cmd_watch, "unwatch": cmd_unwatch, "nudge": cmd_nudge, "ask": cmd_ask,
    "journal": cmd_journal, "merge": cmd_merge, "review": cmd_review, "names": cmd_names,
    "decision": cmd_decision, "decisions": cmd_decisions,
    "issue": cmd_issue, "issue_create": cmd_issue_create,
    "issue_comment": cmd_issue_comment, "issue_close": cmd_issue_close,
    "label_add": cmd_issue_label_add, "label_remove": cmd_issue_label_remove,
}

# Every verb must be classified as spawning or not, because core's no-spawns
# denies are ENUMERATED from core.SPAWNING_VERBS: a new verb left unclassified
# would be allowed to issue-orch by default. Fail at import, where whoever
# added the verb is looking, rather than silently widening the envelope.
assert set(VERBS) == set(core.SPAWNING_VERBS) | set(core.NONSPAWNING_VERBS), (
    "spawn.py VERBS not partitioned by core.SPAWNING_VERBS / "
    "core.NONSPAWNING_VERBS; classify: "
    f"{sorted(set(VERBS) ^ (set(core.SPAWNING_VERBS) | set(core.NONSPAWNING_VERBS)))}"
)


def main(argv):
    if argv and argv[0] in VERBS:
        return VERBS[argv[0]](argv[1:])
    return cmd_spawn(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
