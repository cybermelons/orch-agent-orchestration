"""Seed rows for the decisions ledger (orch#368).

`JOURNAL_ROOT` is `~/orch/state`, which is gitignored -- the ledger file
itself is never versioned, same as every other record under state/. So the
seed cannot be a one-time write into a file somebody committed; it has to be
a replayable script, which is what this is. Run it on a fresh checkout and
the ledger comes back.

Idempotent by id: a row whose id is already the newest in the ledger is
skipped, so re-running adds nothing. That matters because the ledger is
append-only -- without the check, three runs would leave three copies of
every ruling and the newest-wins read would still be correct but the file
would grow without bound.

WHAT IS IN HERE, and what deliberately is not. Scope is orch#368's own: the
2026-09-16 operator rulings (fresh, reasoning linked to a live issue) plus
the entries `docs/DECISIONS.md` already tags `[operator]`. Entries tagged
`[unattributed]` or `[delegated: ...]` are NOT seeded: backfilling those
would mean inventing attribution, which is worse than leaving them
narrative. `decided_by` here is only ever `operator` -- every row below is
one the operator actually decided.

The `[operator sign-off]` / `[operator-prompted]` / `[operator-accepted]`
variants in DECISIONS.md collapse to `operator` here, with the variant kept
in the question text where it changes the meaning (a designer surfaced the
question and the operator accepted, rather than the operator raising it).
The distinction is real but it is not a different decider.

The "2.0 milestone split" is named in orch#368's body as one of the day's
rulings and is deliberately ABSENT: per orch#367 the four milestone labels
exist nowhere on the forge, in the restructure doc, or in the journal beyond
a single standing-order row. No ruling was found, so no row was written. A
missing row is fine; a fabricated one would defeat the point of the ledger.
"""

from . import core

# (id, question, ruling, ref, supersedes)
# decided_by is "operator" for every row -- see the module docstring.
SEED = [
    # --- 2026-09-16 rulings, from issue comments -------------------------
    ("pr-body-encode-not-write",
     "Should PR bodies be prose, or encoded in a denser notation?",
     "Encode where the content is decidable: \"why write when we can encode "
     "in languages that reduce token usage\".",
     "#363", None),
    ("pr-body-compression-non-lever",
     "Is building a PR-body compression schema worth it?",
     "No. PR-body authoring is 0.01% of total spend -- not a lever. Not "
     "building the schema.",
     "#363", None),
    ("journal-compression-agent-audience",
     "Should journal notes and PR bodies stay exempt from compression "
     "because they are a written record?",
     "No -- the exemption is inverted. Only agents read them, so they should "
     "compress hardest.",
     "#268", None),
    ("pr-body-notation-per-thing",
     "Prose by default in PRs, or pick a notation per content type?",
     "Pick per thing: examples and prose by default, a denser notation only "
     "where prose would be longer. Use very short language.",
     "#268", None),
    # The CLI wrapper was ruled twice. Both rows are kept, and the second
    # supersedes the first, because that is exactly the shape this ledger
    # exists to make legible: the first ruling deferred a subquestion, the
    # second answered it after #358 and #346 landed.
    ("orch-cli-issue-wrapper",
     "Should the orch CLI wrap gh/tea issue management, and is the "
     "HTTP-only split deliberate?",
     "Build the light wrapper -- a thin passthrough so agents stop picking "
     "gh vs tea by hand. The HTTP-only split is an oversight, not a design: "
     "build the CLI forms. Scope is create/comment/close/label; merge "
     "excluded.",
     "#223", "orch-cli-issue-wrapper-partial"),
    ("orch-cli-issue-wrapper-partial",
     "Should the orch CLI wrap gh/tea issue management?",
     "Yes, a light wrapper: \"i want the cli so then you stop doing 'gh issue "
     "file xyz' and it'll just call the right one\". The HTTP-only-split "
     "subquestion was left open by this ruling and answered later.",
     "#223", None),
    ("widget-verb-cut",
     "Should the widget keep the assign / unclaim / kill / set-inactive "
     "buttons?",
     "No -- cut them. \"I only ever want to see the issue, PR, or send a "
     "note. I haven't used kill either.\"",
     "#358", None),
    ("api-for-agent-widget-for-operator",
     "Should the widget render controls from the full /act verb table?",
     "No. \"The API is for the agent. I, the operator, only want few "
     "actions.\" Two surfaces, deliberately different sizes: the agent verb "
     "table stays full, the widget stays a curated allow-list.",
     "#358", None),
    ("auto-land-posture",
     "Should auto-land pause for operator review?",
     "No -- \"the point of auto-land is that it doesn't pause on me. I'll "
     "check back and stop/pause myself. I'll use orch to watch.\"",
     "#262", None),
    ("auto-land-hold-mechanism",
     "How does one issue opt out of a repo's auto-land default?",
     "auto-land just lands; to hold something, file it and set "
     "`no-auto-land`.",
     "#262", None),
    ("dashboard-op-role-framing",
     "What is dashboard-op, if not an auto-waking digest role?",
     "The set of skills that agent can do to manage orch -- the agent the "
     "operator talks to about orch, who can run the skills.",
     "#262", None),
    # --- the ten ranked audit items (#262 §10) ---------------------------
    ("dashboard-op-auto-wake-delete",
     "Keep dashboard-op's automatic wake? (ranked audit item 1)",
     "Delete the automatic wake. Keep the role for operator-invoked "
     "questions only.",
     "#262", None),
    ("condition-5-gate-on-auto-land",
     "Gate wake condition 5's REVIEW disjunct? (ranked audit item 2)",
     "Do -- gate it on auto_land.",
     "#299", None),
    ("two-level-reentry-defer",
     "Let tick spawn issue-orch directly on conditions 5/7? (ranked audit "
     "item 3)",
     "Defer until measured -- the swallowed-stderr defect is fixed first.",
     "#262", None),
    ("trim-wake-payloads",
     "Trim wake payload size? (ranked audit item 4)",
     "Do.",
     "#262", None),
    ("delete-legacy-ack-reader",
     "Delete the legacy last_ack() reader? (ranked audit item 5)",
     "Do.",
     "#262", None),
    ("agent-ready-axis-collapse",
     "Keep or collapse the `agent-ready` label axis? (ranked audit item 6)",
     "Collapse -- the label carries information for about two minutes; "
     "replaced by `agent-working` union the live ledger key.",
     "#346", None),
    ("delete-transitions-endpoint",
     "Delete the transitions endpoint and its helpers? (ranked audit item 7)",
     "Delete -- operator confirms nothing curls it.",
     "#262", None),
    ("tui-keep",
     "Delete the 1,209-line TUI? (ranked audit item 8)",
     "Keep -- \"nice to have later, but ultimately just a wrapper for the "
     "CLI\". Filed as an issue rather than cut.",
     "#262", None),
    ("pure-deletions-audit-items-9-10",
     "Execute the pure deletions and fix the condition-count bookkeeping? "
     "(ranked audit items 9 and 10)",
     "Do both.",
     "#262", None),
    # --- docs/DECISIONS.md section A, [operator] only --------------------
    ("four-levels",
     "Why four levels (tick, dashboard-op, repo-orch, issue-orch, worker)?",
     "Context isolation, and each level is its own conversational surface.",
     "#8", None),
    ("orchestrators-dont-understand-repos",
     "Where does the level boundary sit?",
     "\"Orchestrators move things along. They do not understand repos.\"",
     "#8", None),
    ("repo-orch-kept",
     "Should repo-orch, cut in a design pass, be restored?",
     "Restored -- the cut was mechanically right and philosophically wrong. "
     "repo-orch is where repo understanding lives.",
     "#8", None),
    ("repo-management-is-a-skill",
     "Does repo-orch hold repo knowledge itself, or run a skill?",
     "It runs a repo-management skill and only holds that skill's context. "
     "orch stays mechanics-only.",
     "#8", None),
    ("one-lease-scheme",
     "One lease scheme, or two (worker versus orchestrator role)? "
     "[operator-prompted; reversal executed by Fable]",
     "One. \"A worker is an agent spawned from an orchestrator, either in an "
     "issue or repo.\" Zero code branches on worker-vs-role.",
     "#6", None),
    ("disk-as-state",
     "Where does durable state live?",
     "Never in a session. Derived facts are re-read from the world every "
     "tick; records may lie, and nothing gates on them.",
     "#6", None),
    ("journal-on-issue",
     "Where does the issue journal live?",
     "On the GitHub issue, and only there. No local mirror, ever.",
     "#8", None),
    ("push-per-commit",
     "What is the commit and push discipline? [operator-accepted design]",
     "A WIP commit per logical step, and a best-effort `git push -u` after "
     "each commit.",
     "#8", None),
    ("auto-land-no-ci-accepted",
     "Is auto-land safe on a repo with no CI, where an empty rollup reads "
     "green? [operator sign-off]",
     "Accepted. \"No-CI PRs are the common case here, and "
     "self-merge-unchecked is fine until it actually causes a problem.\"",
     "#7", None),
    ("claude-not-happy",
     "Should spawned agents resolve the `happy` shell alias? "
     "[operator sign-off]",
     "No -- spawned agents run plain Claude Code, as-is.",
     "#6", None),
    ("keep-forever-append-only",
     "What is the retention policy for dead-run records?",
     "There is none. Nothing about a dead run is ever destroyed: no pruning, "
     "no rotation.",
     "#6", None),
    ("design-v2-replaces-design",
     "The old DESIGN.md described a system that no longer existed -- keep "
     "both?",
     "No. V2 took the name outright.",
     "#8", None),
    ("s1-live-window-tradeoff",
     "Accept a 120s live-window false-positive cost for session liveness? "
     "[operator sign-off]",
     "Accepted -- a dead session can read live in the widget for up to two "
     "minutes.",
     "#8", None),
    ("agent-surface-equals-operator-surface",
     "Should agents get the same actions the operator has?",
     "Yes -- \"whatever i can do i want agents to be able to do\". spawn.py "
     "became the full CLI, one implementation behind CLI and HTTP. `/act` "
     "stays a closed set only because the web surface is tailnet-reachable.",
     "#8", None),
    ("auto-review-config-shape-original",
     "Where does the per-repo auto-review default live, and is it a role?",
     "In `<checkout>/.orch.toml` (versioned with the repo, so it travels "
     "with a clone), with per-issue labels on top. It is a subagent, not a "
     "role.",
     "#26", None),
    # A15 carries three separate operator amendments over the original row
    # above; each gets its own row with its own honest ref rather than one
    # row conflating them under a single (partly wrong) ref. See A15 in
    # docs/DECISIONS.md for the full text.
    ("auto-review-config-shape",
     "Where does the per-repo auto-land default live? "
     "[storage location superseded by orch#297, 2026-09-16]",
     "The per-repo default lives in that repo's own entry in the single "
     "`ORCH_HOME/orch.json` (machine-local), migrated one-time from "
     "`.orch.toml`, which is never deleted.",
     "#297", "auto-review-config-shape-original"),
    ("auto-review-one-flag-not-two",
     "Is there a separate auto-review flag alongside auto-land? "
     "[superseded by the operator, 2026-09-14 -- no ref cited]",
     "No. There is one flag, `auto-land` -- no separate `auto-review` flag; "
     "the per-repo default and the per-issue `auto-review`/`no-auto-review` "
     "labels are superseded.",
     "", "auto-review-config-shape-original"),
    ("blocking-finding-revokes-auto-land",
     "What does a blocking review finding do to an issue? "
     "[corrected by the operator, 2026-09-14 -- no ref cited; corrects an "
     "earlier same-day paragraph that was never itself seeded, hence no "
     "supersedes]",
     "It revokes `auto-land` (the per-issue label only) and leaves the work "
     "state at `REVIEW` -- re-examined every tick by condition 5, merged by "
     "nobody because the flag is absent. No new label, no new work state, "
     "no new store. (Not `agent-stuck`: that earlier same-day wording was "
     "withdrawn same-day; `agent-stuck` means only the write-off case.)",
     "", None),
    ("blocking-finding-holds-merge-derived",
     "What does a blocking review finding do to an issue? "
     "[ruling orch#148 option B, wired by orch#408, 2026-09-15]",
     "It holds the merge, not by writing any label. `core.merge_pr` calls "
     "`core.review_blocks_merge`, which reads the PR's LAST "
     "`orch:review:v1` block fresh on every merge attempt and refuses iff "
     "it carries a `fix-before-merge` item. `auto-land` is left untouched; "
     "the work state stays `REVIEW`. The hold lifts only on a fresh clean "
     "re-review posting a new block -- pushing a fix alone does not lift "
     "it, and there is no operator un-write. An unreadable PR also blocks, "
     "rather than failing open, so the hold cannot evaporate on a forge "
     "outage.",
     "#408", "blocking-finding-revokes-auto-land"),
    # --- docs/DECISIONS.md section D, [operator] only ---------------------
    ("escalation-object-removed",
     "Should escalation be a durable object (row, dismiss store, "
     "file-as-issue verb), or just a status change on the issue already "
     "being worked?",
     "Removed entirely: when an agent hits a wall, the issue it is already "
     "working changes status, and that is the whole mechanism -- no "
     "`escalated` journal row closed by a `filed`/`addressed`/`retracted` "
     "marker, no browser-local dismiss store, no file-as-issue verb.",
     "#198", None),
]


def seed(rows=SEED):
    """Append every row not already in the ledger. Returns the ids written.

    Idempotent by id against the RESOLVED view: if `decisions_read()` already
    returns a row under this id, it is skipped. A superseding row is written
    under its own id, so `orch-cli-issue-wrapper` and the partial ruling it
    supersedes are two rows and both survive a re-run.
    """
    existing = core.decisions_read()
    written = []
    for id, question, ruling, ref, supersedes in rows:
        if id in existing:
            continue
        core.decisions_append(id, question, ruling, "operator", ref,
                              supersedes=supersedes)
        written.append(id)
    return written


if __name__ == "__main__":
    ids = seed()
    print(f"seeded {len(ids)} ruling(s)"
          + ("" if not ids else ": " + ", ".join(ids)))
