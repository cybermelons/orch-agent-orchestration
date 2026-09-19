#!/usr/bin/env python3
"""Mechanical docs-vs-code conformance check.

Reads FILES ONLY (source text + markdown) and parses with `re`. Does NOT
import orch.core or orch.tick, and does NOT need ORCH_HOME or any env setup
-- that keeps this fast and side-effect free, unlike test_core.py which
spins up a real ORCH_HOME. If a doc drifts from the code it describes (a
condition renumbered, a state renamed, a constant's default changed) this
catches it without a human re-reading both sides by eye.

Run: python3 -m orch.test_conformance   (from the repo root)
"""
import re
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

pass_n = 0
fail_n = 0


def check(name, expected, actual):
    global pass_n, fail_n
    if expected == actual:
        pass_n += 1
    else:
        fail_n += 1
        print(f"FAIL {name}: want [{expected}] got [{actual}]")


tick_src = (ROOT / "orch" / "tick.py").read_text()
core_src = (ROOT / "orch" / "core.py").read_text()


def _require(pattern, src, what, flags=re.S):
    """Match or die, naming the symbol. This module reads source TEXT, so a
    rename or a reformat makes a pattern stop matching -- and the failure mode
    that matters is not the crash, it is an empty set reaching an assertion
    that then PASSES while checking nothing. Fail loudly at the parse instead."""
    m = re.search(pattern, src, flags)
    if m is None:
        sys.exit(f"FATAL: {what} no longer parses -- a rename would silently "
                 f"empty this assertion. Fix the pattern in "
                 f"orch/test_conformance.py; do not delete the check.")
    return m

# === A. Condition accounting =================================================
# No NEVER_SPAWN tuple here on purpose. Adding one would make a FOURTH place
# condition membership is recorded (docstring, SPAWN_CONDS, the cond==2
# branch, and the tuple) -- exactly the drift this module exists to catch.
# So the never-spawn set is read out of the existing prose instead.

emitted = sorted(int(n) for n in re.findall(r'"cond":\s*(\d+)', tick_src))

m = _require(r'SPAWN_CONDS\s*=\s*\(([^)]*)\)', tick_src, "tick.py SPAWN_CONDS")
SPAWN_CONDS = tuple(int(n) for n in re.findall(r'\d+', m.group(1)))

m = _require(r'def route\(.*?\n(    """.*?""")', tick_src, "tick.py route() docstring")
route_doc = m.group(1)
docstring_excluded = {int(n) for n in
                       re.findall(r'^\s+(\d+)\. ', route_doc, re.M)}

conditional_spawn = {2}
check("cond_2_conditional_spawn_branch_exists", True, "cond == 2" in tick_src)

check("conditions_are_uniquely_numbered",
      list(range(1, len(set(emitted)) + 1)), sorted(set(emitted)))
check("conditions_are_uniquely_numbered_no_dupes", len(emitted), len(set(emitted)))

unaccounted = set(emitted) - (set(SPAWN_CONDS) | conditional_spawn | docstring_excluded)
check("every_condition_is_accounted_for", set(), unaccounted)

check("no_condition_is_both_spawning_and_excluded",
      set(), set(SPAWN_CONDS) & docstring_excluded)

real_condition_count = len(set(emitted))

# === B. State parity =========================================================

def _func_body(src, name):
    m = re.search(rf'\ndef {name}\(.*?(?=\ndef |\Z)', src, re.S)
    return m.group(0)

work_state_body = _func_body(core_src, "work_state")
issue_state_body = _func_body(core_src, "issue_state")

def _returned_states(body):
    out = set(re.findall(r'return\s+"([A-Z]+)"', body))
    out |= set(re.findall(r'"([A-Z]{4,})"', body))
    return out

code_states = _returned_states(work_state_body) | _returned_states(issue_state_body)

states_md = (ROOT / "STATES.md").read_text()

missing_from_docs = {s for s in code_states
                      if not re.search(rf'\b{s}\b', states_md)}
check("STATES_md_names_every_code_state", set(), missing_from_docs)

# Reverse direction: the spec asks that STATES.md name "no other" state. That
# is NOT mechanically decidable here and this check does not pretend to it.
# The code has no state enum -- work_state and issue_state return bare string
# literals -- so there is no closed set to diff against, and STATES.md is
# prose carrying many other all-caps words (MERGED, OPEN, README, AND, OR,
# PR) that are not state names. A blanket all-caps scan false-fires on every
# one of them; MERGED in particular is legitimate prose about a *PR* state.
#
# So this asserts the WEAKER, TRUE thing and is named for it: a short list of
# plausible-but-wrong state names must not be presented as state values. It
# is a tripwire, not a parity check. Four words is its whole reach, and that
# is the honest claim -- an assertion whose NAME overpromises its coverage is
# how a green check comes to mean less than a reader thinks.
#
# The real fix is upstream: give core.py a WORK_STATES/ISSUE_STATES tuple and
# the full reverse direction becomes a one-line set comparison. Out of scope
# here (that is a code change to the state functions, not a doc assertion).
bogus_states = {"STUCK", "DONE", "FAILED", "WAITING"}
present_bogus = {s for s in bogus_states
                  if re.search(rf'(?:work_state|issue_state).{{0,120}}\b{s}\b',
                               states_md, re.S)}
check("STATES_md_presents_no_tripwire_nonstate_as_a_state_value",
      set(), present_bogus)

# === C. Constants -- CODE default only, never deployment =====================
# These three are env-overridable, so this module compares docs against the
# CODE DEFAULT only. It has no deployment reach -- it runs in a worktree and
# cannot see the systemd unit, which may set a different value at runtime.
# Passing here says nothing about what is actually deployed.

def _const(src, name):
    m = re.search(rf'{name}\s*=\s*int\(os\.environ\.get\("(\w+)",\s*"(\d+)"\)\)', src)
    return m.group(1), m.group(2)  # (env var name, default literal)

nudge_env, nudge_default = _const(core_src, "NUDGE_IDLE_MINS")
ack_env, ack_default = _const(core_src, "ACK_TTL_MINS")
cap_env, cap_default = _const(core_src, "IN_FLIGHT_CAP")

check("in_flight_cap_reads_its_documented_env_var", "ORCH_IN_FLIGHT_CAP", cap_env)

repo_orch_doc = (ROOT / "agents" / "repo-orch.md").read_text()
check("repo_orch_doc_mentions_in_flight_cap_env_var", True,
      "ORCH_IN_FLIGHT_CAP" in repo_orch_doc)

# orch#318: the hold verb must be the label PAIR-WRITE (add no-auto-land,
# then remove auto-land), not the single `--remove-label auto-land` that is
# a no-op against a repo-default auto-land. Slice out the "Your hold verb"
# SECTION by heading -- searching the whole doc would pass even if the
# add-label line only appeared in some unrelated paragraph, checking nothing.
m = _require(r'\n## Your hold verb[^\n]*\n(.*?)(?=\n## )', repo_orch_doc,
             "repo-orch.md 'Your hold verb' section")
hold_verb_section = m.group(1)
check("repo_orch_hold_verb_adds_no_auto_land", True,
      "--add-label no-auto-land" in hold_verb_section)
check("repo_orch_hold_verb_removes_auto_land", True,
      "--remove-label auto-land" in hold_verb_section)
# The add is the half that can fail, and it fails on exactly the case this
# verb exists for: a repo whose default is ON that never had the label
# written. _set_auto_land creates the label and retries once rather than
# aborting there, so the doc must teach the same recovery -- a chain that
# gives up on the first failed add leaves a posted hold comment with no
# labels behind it.
check("repo_orch_hold_verb_creates_label_on_failure", True,
      "gh label create no-auto-land" in hold_verb_section)
check("repo_orch_doc_drops_old_no_auto_land_prohibition", False,
      "never write `no-auto-land`" in repo_orch_doc)

# orch#408 supersedes orch#372 HERE, and only here. orch#372 required the
# label PAIR-WRITE at this call site, because the pair WAS the blocking-
# finding hold and a single `--remove-label auto-land` was a no-op against a
# repo-default auto-land. Ruling orch#148 option B cut that mechanism: a
# `fix-before-merge` item in the PR's latest `orch:review:v1` block now holds
# the merge, derived fresh by core.review_blocks_merge on every
# core.merge_pr call, with NO label written and no state stored.
#
# So these checks invert rather than disappear. The failure they now guard
# against is the reverse of orch#372's: a doc that still teaches an agent to
# write labels would have it perform a pair-write that no longer gates
# anything, while believing the PR is held. The pair-write must be GONE from
# this section, and the derived hold must be TAUGHT in its place.
#
# orch#372's checks at the repo-orch.md call site above are untouched and
# still required: repo-orch's hold verb is cross-issue entanglement, a
# different mechanism that merely shares a spelling. Ruling 6 did not reach
# it. Do not "make these consistent" by deleting those.
landing_doc = (ROOT / "agents" / "skills" / "issue-landing" / "SKILL.md").read_text()
# Anchored on the section's SUBJECT ("Blocking finding →") rather than its
# full sentence: the old pattern matched one exact English wording, so a
# cosmetic rewording of the heading would hard-fail the run (_require exits)
# for a change that broke nothing. The arrow plus the bold marker is the
# stable part -- it is the section's identity, not its prose.
m = _require(r'\n\*\*Blocking finding →[^\n]*\n(.*?)(?=\n\*\*Hold decided\*\*)',
             landing_doc, "issue-landing SKILL.md blocking-finding derived-hold section")
landing_hold_section = m.group(1)
# The pair-write is gone as the hold: no label writes taught in this section.
check("landing_hold_drops_add_no_auto_land", False,
      "--add-label no-auto-land" in landing_hold_section)
check("landing_hold_drops_remove_auto_land", False,
      "--remove-label auto-land" in landing_hold_section)
check("landing_hold_drops_label_create", False,
      "gh label create no-auto-land" in landing_hold_section)
# ...and the derivation is taught in its place. Without this half, a section
# that merely deleted the pair-write would pass while teaching no hold at all
# -- the silent-no-op class orch#148 was filed about, sign flipped.
check("landing_hold_names_derivation_fn", True,
      "review_blocks_merge" in landing_hold_section)
check("landing_hold_names_the_block", True,
      "orch:review:v1" in landing_hold_section)
check("landing_hold_states_no_labels_written", True,
      "Write no labels" in landing_hold_section)
# orch#408 review, finding 5: the three checks above are bare token tests, so
# a section gutted to one sentence naming all three tokens would pass them
# while teaching an agent nothing it can act on. These two pin the
# OPERATIONAL content instead -- the act to perform, and how the hold ends.
# Both are things an agent gets wrong by omission, not by contradiction:
# a section that never says "post the comment" leaves the hold unwritten,
# and one that never says how it lifts leaves a fixed PR held forever.
check("landing_hold_teaches_posting_the_comment", True,
      "pr comment" in landing_hold_section)
check("landing_hold_teaches_how_the_hold_lifts", True,
      "re-review" in landing_hold_section)
# The prose that regenerates the bug: a model in which the repo default is
# untouchable makes the single-command spelling look correct.
check("landing_revoke_drops_repo_default_untouchable_prose", False,
      "never the repo default" in landing_doc)

# === D. Doc set + condition-count numerals ====================================

def _doc_set():
    paths = [ROOT / n for n in ("DESIGN.md", "STATES.md", "README.md", "DEPLOY.md")]
    for d in (ROOT / "docs", ROOT / "agents", ROOT / "agents" / "skills"):
        if d.is_dir():
            paths += sorted(p for p in d.glob("*.md")
                             if d.name != "docs" or p.parent.name not in ("artboards", "reports"))
    skill = Path.home() / ".claude" / "skills" / "orch" / "SKILL.md"
    if skill.exists():
        paths.append(skill)
    return [p for p in paths if not p.name.startswith("tmp_")]

DOC_SET = _doc_set()


def _label(path):
    """Display path relative to repo root, or as-is for the out-of-tree
    runtime skill file (~/.claude/skills/orch/SKILL.md)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)

NUMERAL = {"six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
bad_count_hits = []
# The SOURCE files count too, and they are the load-bearing ones: tick.py's
# own "=== the N conditions ===" banner is what a reader of the router trusts,
# and PR f01e1b8 added a ninth condition without touching it. Scanning only
# markdown would have missed the instance closest to the code. feed.py is in
# the set because it carries the claim twice in comments (feed.py:134,828).
#
# Three phrasings, because a narrower regex silently under-reports and an
# assertion that misses most of what it claims to check is worse than none:
#
#   1. "<numeral> conditions"       -- the plain form
#   2. "<numeral> wake conditions"  -- README.md:242, docs/DECISIONS.md:383
#      insert a word, so \s+ alone walks straight past them
#   3. "no <ordinal>"               -- DESIGN.md's "Why these seven and no
#      eighth" states the count as a closed-set bound. It is the same claim
#      in ordinal clothing, and it is the one the code actually outgrew.
#
# Measured: the plain form alone found 8 sites; all three find 14. The
# operator's own §1 item 7 in docs/RESTRUCTURE-2026-09-16.md independently
# lists the same wider set, which is how the gap surfaced.
COUNT_PATTERNS = (
    r'\b(six|seven|eight|nine|ten)\s+(?:wake\s+)?conditions\b',
    r'\bno\s+(sixth|seventh|eighth|ninth|tenth)\b',
)
ORDINAL = {"sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
for path in DOC_SET + [ROOT / "orch" / n for n in ("tick.py", "core.py", "feed.py")]:
    text = path.read_text()
    for pat in COUNT_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            word = m.group(1).lower()
            if word in ORDINAL:
                # "no eighth" asserts the set STOPS at seven, i.e. claims a
                # count of ordinal-minus-one. Correct iff that equals reality.
                claimed = ORDINAL[word] - 1
            else:
                claimed = NUMERAL[word]
            if claimed != real_condition_count:
                line = text.count("\n", 0, m.start()) + 1
                bad_count_hits.append(f"{_label(path)}:{line} ({word})")

# KNOWN_COUNT_GAP: file/numeral pairs that say six or seven, and they are NOT
# stale numerals to find-replace. Each remaining entry is a cross-reference to
# a DESIGN.md section title, so its repair is prose work in the citing file,
# not a numeral edit here.
#
# The DESIGN.md entries are GONE as of orch#360, which was the root of the
# whole gap. DESIGN.md:682 used to head a list that enumerated seven
# conditions and stopped, and the paragraph below it was titled "Why these
# seven and no eighth" and argued the set was CLOSED at seven -- while
# conditions 8 and 9 (the set-level consolidation wake and the orphaned PR)
# appeared nowhere in the doc, and condition 8 was in SPAWN_CONDS the whole
# time. #360 documented 8 and 9 and retitled that paragraph "What earns a
# condition", which states a bar for admitting a new condition and no total.
# The heading is now "The wake conditions". That is why the fix retired seven
# sites at once rather than correcting seven numerals: there is no longer a
# count in that prose to drift.
#
# Listed explicitly so the exception is reviewable and so the assertion still
# bites on any NEW wrong count: delete an entry here once its file stops
# citing a numbered section title, and the check goes green on its own.
# Keyed by (file, numeral) and NOT by line number: a line-keyed exception
# breaks the moment anyone edits above it, and a check that false-fires on an
# unrelated edit is one people start ignoring. The count per file is carried
# so ADDING a ninth "six conditions" to DECISIONS.md still fails.
# ELEVEN sites, measured with all three phrasings. An earlier draft of this
# check used only "<numeral> conditions" and recorded 8 -- it was under-
# reporting by more than half, and the exception list inherited that blind
# spot. The operator's own audit (docs/RESTRUCTURE-2026-09-16.md section 1
# item 7) independently lists the wider set and calls the count "four-way
# drifted", which is how the gap surfaced. Kept as measured, not as hoped.
#
# Was NINETEEN until orch#348 removed the ("README.md", "six") entry, then
# EIGHTEEN until orch#360 removed both DESIGN.md entries ("seven" x3,
# "eighth" x4). README.md:242 was the one site that stated the count as a
# bare fact about the tick rather than as a cross-reference to a DESIGN.md
# section title, so it could be corrected to nine on its own without making
# any heading lie about its own list.
#
# The eleven that remain are all cross-references, and four of them quote the
# title "The six conditions" -- a heading DESIGN.md has not carried for two
# renames now (it read "The seven conditions", and since #360 reads "The wake
# conditions"). They are stale citations in the CITING files, so retiring them
# means rewriting those references, not editing a numeral. DECISIONS.md's
# "seventh" entries are the same shape: they cite a closed-set argument that
# #360 replaced with a bar.
KNOWN_COUNT_GAP = {
    ("docs/DECISIONS.md", "six"): 4,
    ("docs/DECISIONS.md", "seventh"): 3,
    ("docs/UX-REDESIGN.md", "six"): 1,
    ("agents/dashboard-op.md", "six"): 1,
    ("orch/feed.py", "six"): 2,
}
seen_gap = {}
new_count_hits = []
for hit in bad_count_hits:
    loc, word = hit.rsplit(" (", 1)
    key = (loc.rsplit(":", 1)[0], word.rstrip(")"))
    if key in KNOWN_COUNT_GAP:
        seen_gap[key] = seen_gap.get(key, 0) + 1
        if seen_gap[key] <= KNOWN_COUNT_GAP[key]:
            continue
    new_count_hits.append(hit)
shrunk = {k: (v, seen_gap.get(k, 0)) for k, v in KNOWN_COUNT_GAP.items()
          if seen_gap.get(k, 0) < v}
if shrunk:
    print(f"info: KNOWN_COUNT_GAP over-counts (expected, actual), lower or "
          f"delete these entries: {shrunk}")

check("docs_do_not_claim_a_wrong_condition_count", [], new_count_hits)

# === E. Label set ==============================================================

m = _require(r'ORCH_LABEL_NAMES\s*=\s*frozenset\(\{([^}]*)\}\)', core_src, "core.py ORCH_LABEL_NAMES")
label_symbols = [s.strip() for s in m.group(1).split(",") if s.strip()]
label_values = []
for sym in label_symbols:
    lm = re.search(rf'^{sym}\s*=\s*"([^"]*)"', core_src, re.M)
    label_values.append(lm.group(1))

doc_text_all = "\n".join(p.read_text() for p in DOC_SET)
labels_nowhere = [lbl for lbl in label_values if lbl not in doc_text_all]
check("every_orch_label_is_named_in_the_runtime_docs", [], labels_nowhere)

# orch#404: `gh label create --description` 422s past 100 chars, and
# ensure_labels feeds ORCH_LABELS' descriptions straight into that call for
# EVERY watched repo -- not just the create-on-demand retry path the issue
# was filed against. A description that regresses past the cap here breaks
# every future watch_repo silently (label creation just reports the name as
# missing; nothing surfaces the 422 body), so the ceiling is asserted at the
# source table rather than only at today's known-bad string.
m = _require(r'ORCH_LABELS = \((.*?)\n\)\n', core_src, "core.py ORCH_LABELS")
GH_LABEL_DESC_MAX = 100
desc_too_long = []
for name_m in re.finditer(r'\(L_\w+,\s*((?:"[^"]*"\s*)+)\)', m.group(1)):
    desc = "".join(re.findall(r'"([^"]*)"', name_m.group(1)))
    if len(desc) > GH_LABEL_DESC_MAX:
        desc_too_long.append(f"{len(desc)} chars: {desc!r}")
check("every_orch_label_description_fits_gh_100_char_cap", [], desc_too_long)

# orch#390: the claim path (issue-orch.md "## Claiming") must teach
# add-then-verify-then-remove, never `gh issue edit --add-label X
# --remove-label Y` in one call. That combined form is not atomic -- if X
# does not exist on the forge yet, gh exits 1 AND still applies the
# --remove-label, so a failed claim silently un-readies the issue. Sliced by
# heading exactly like the hold-verb section above, so a rewrite elsewhere
# in the file can't make this pass by accident.
issue_orch_doc = (ROOT / "agents" / "issue-orch.md").read_text()
m = _require(r'\n## Claiming\n(.*?)(?=\n## )', issue_orch_doc,
             "issue-orch.md 'Claiming' section")
claiming_section = m.group(1)
check("claiming_section_has_no_combined_add_remove_call", False,
      "--add-label agent-working --remove-label agent-ready" in claiming_section)
check("claiming_section_adds_before_removing", True,
      "--add-label agent-working" in claiming_section
      and "--remove-label agent-ready" in claiming_section)

# === F. Verb / action parity claim ===========================================

spawn_src = (ROOT / "orch" / "spawn.py").read_text()
server_src = (ROOT / "orch" / "server.py").read_text()

# Both patterns are anchored at start-of-line (re.M), because `\w` does not
# anchor the LEFT edge of a name: before orch#223 anchored this, the helper
# dict `_ISSUE_SUBVERBS = {` matched `VERBS\s*=\s*\{` -- its name merely ENDS
# with "VERBS" -- and being non-greedy the regex took that first match, so the
# parse silently returned 3 verbs instead of 16. The vacuity floor below is
# what caught it, but a floor only catches a parse that gets SMALLER; anchor
# here so the next `*VERBS`-suffixed helper cannot shadow the real table.
VERBS = set(re.findall(r'"([\w-]+)":',
                       _require(r'^VERBS\s*=\s*\{(.*?)\n\}', spawn_src, "spawn.py VERBS",
                                re.S | re.M).group(1)))
ACTIONS = set(re.findall(r'"(\w+)":',
                         _require(r'^ACTIONS\s*=\s*\{(.*?)\n\}', server_src, "server.py ACTIONS",
                                  re.S | re.M).group(1)))

# Guard every parsed set against emptiness. This module reads source TEXT, so
# a rename or a reformat makes a regex match nothing -- and an assertion over
# an empty set PASSES, silently, forever. That failure is worse than a false
# alarm: the check keeps reporting green while checking nothing. Sizes are
# lower bounds, not exact, so ordinary growth does not trip them.
vacuous = [name for name, size, floor in (
    ("emitted conds", len(emitted), 9),
    ("SPAWN_CONDS", len(SPAWN_CONDS), 4),
    # 3, not 4: orch#387 moved condition 9 out of the never-spawns set (its
    # closed half now spawns on the slug), leaving {3, 4, 6}. This is a
    # vacuity floor, not an accounting check -- the real accounting lives in
    # every_condition_is_accounted_for, which stays green either way.
    ("docstring_excluded", len(docstring_excluded), 3),
    ("code_states", len(code_states), 8),
    ("label_values", len(label_values), 8),
    ("VERBS", len(VERBS), 11),
    ("ACTIONS", len(ACTIONS), 20),
) if size < floor]
check("parses_are_not_vacuous", [], vacuous)

print(f"info: {len(VERBS)} CLI verbs vs {len(ACTIONS)} HTTP actions")

# A doc that QUOTES the false claim in order to report it as drift is not
# making the claim. docs/RESTRUCTURE-2026-09-16.md:522 is exactly that: it
# cites the SKILL.md line to say the runtime skill contradicts the README.
# Failing on it would punish a doc for documenting the bug correctly -- and
# would make every future drift report a build break. Heuristic: a match
# wrapped in quotes, or on a line that also names the file it is citing, is
# a citation. Deliberately crude; the cost of a wrong guess is one line in a
# report either way, not a bad merge.
parity_claim_hits = []
for path in DOC_SET:
    text = path.read_text()
    for m in re.finditer(r"every\s+`?/?act`?\s+(http\s+)?action\s+has\s+a\s+cli", text, re.I):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        full_line = text[line_start:line_end if line_end != -1 else len(text)]
        quoted = '"' in full_line or "SKILL.md" in full_line or "README.md" in full_line
        if quoted:
            continue
        parity_claim_hits.append(f"{_label(path)}:{text.count(chr(10), 0, m.start()) + 1}")

# Split by reach, deliberately. The gate asserts only over files THIS repo
# can fix; a claim in ~/.claude/skills/orch/SKILL.md is the operator's file,
# outside the checkout and not tracked here, so failing the build on it would
# make the gate unpassable from inside the repo -- red forever, and a gate
# nobody can green is a gate everybody learns to ignore. Dropping the runtime
# docs from the scan instead would discard the very input the audit says to
# check FIRST. So: in-tree claims fail, out-of-tree claims are REPORTED loudly
# and left to the operator.
in_tree_parity = [h for h in parity_claim_hits if not h.startswith("/")]
out_of_tree_parity = [h for h in parity_claim_hits if h.startswith("/")]
if out_of_tree_parity:
    print(f"info: parity claim still live in runtime docs outside this repo "
          f"(operator's to fix): {out_of_tree_parity}")

check("no_in_tree_doc_claims_cli_action_parity", True,
      not in_tree_parity or VERBS == ACTIONS)

# === G. Cited paths exist =====================================================
# Each entry here is a path a doc cites that is deliberately absent from a
# checkout, explicit and reviewable rather than a silent skip buried in the
# regex:
#  - .orch.toml: KNOWN-obsolete (core.py itself calls it obsolete, see
#    _migrate_orch_toml_keys).
#  - public/status.json, public/widget.html: runtime-generated, gitignored
#    (commit 6d81986 "stop tracking runtime files") -- built by a deploy,
#    never checked in.
#  - state/tick-spawns.json, state/tick-dash-wake.json: under ORCH_HOME's
#    state/ dir, also gitignored -- written at runtime, not shipped.
#  - ops-console/DEPLOY.md: a deliberate CROSS-REPO citation. DEPLOY.md:7
#    says "Compare `ops-console/DEPLOY.md`, which uses the same timer-pull
#    pattern" -- it names a file in a different repo on purpose, so it is
#    not drift and there is nothing in this tree to point it at.
#  - docs/WIZARD-PROGRESS.md: deleted by orch#348 (audit item 9, a finished
#    scratchpad whose decisions live in agents/repo-orch.md and
#    agents/REPO-SKILL-INTERFACE.md). The one surviving citation is
#    docs/RESTRUCTURE-2026-09-16.md:351, the dated audit report that ORDERED
#    the deletion -- it is a historical record of a decision, not a pointer
#    a reader is meant to follow, and rewriting it to cite a file it never
#    named would falsify the record. A report that lists a file as a
#    deletion target is correct precisely BECAUSE the file is now gone.
KNOWN_ABSENT = {
    ".orch.toml",
    "public/status.json", "public/widget.html",
    "state/tick-spawns.json", "state/tick-dash-wake.json",
    "ops-console/DEPLOY.md",
    "docs/WIZARD-PROGRESS.md",
}

# REACH, measured, so nobody reads this assertion as more than it is: of 303
# path-like backtick tokens in the doc set, 194 (64%) are bare filenames with
# no slash and are SKIPPED; 99 are checked. The skip is deliberate -- bare
# names like `core.py`, `tick.py`, `feed.py` are real files under orch/, not
# at repo root, so checking them against the root would false-fail nearly all
# of them, and resolving them by basename anywhere in the tree leaves 16
# unresolved (runtime output, external CLAUDE.md/AGENTS.md, design artboards)
# whose exception list would be larger than the drift it catches.
#
# So this catches a wrong PATH, not a wrong FILENAME. The two real hits it
# found (docs/RESTRUCTURE-2026-09-16.md:304,472, skill files cited without
# their agents/skills/ prefix) are exactly that shape, which is the common
# drift. A typo'd bare filename is out of reach and stays out until the docs
# cite paths rather than names.
missing_paths = []
path_re = re.compile(r'`([a-zA-Z0-9_./-]+\.(?:py|md|json|html|tpl|toml|service|sh))`')
for path in DOC_SET:
    text = path.read_text()
    for m in path_re.finditer(text):
        tok = m.group(1)
        if "/" not in tok:
            continue  # bare filename, too noisy to check reliably
        if " " in tok or "*" in tok or "<" in tok or ">" in tok:
            continue
        if tok.startswith(("/", "~", "http", "./")) or "://" in tok:
            continue  # "./x" is cwd-relative prose, not a repo-root path
        if tok.startswith("ORCH_HOME/"):
            continue  # ORCH_HOME is an env var (default ~/orch), not a literal dir
        if tok in KNOWN_ABSENT:
            continue
        if not (ROOT / tok).exists():
            line = text.count("\n", 0, m.start()) + 1
            missing_paths.append(f"{_label(path)}:{line} `{tok}`")

check("paths_cited_in_docs_exist", [], missing_paths)

# === H. Payload budget =======================================================
# orch#416. The agent-facing read surface has a size budget, asserted here
# because nothing else does. #344 landed the cheap per-repo read and pointed
# repo-orch's brief at it, and status.json still GREW from 236 KB to 264 KB
# afterwards -- the fix added an alternative without shrinking the default,
# and the green test suite said nothing because no test measured bytes.
#
# Every repo-orch wake may read this file, and context loading is 90.9% of
# spend (docs/RESTRUCTURE-2026-09-16.md section 12), so its size IS a cost
# number. A generous ceiling that still fails loudly on an unbounded list is
# worth more than a tight one that gets raised on every breach.
# Built from the tree rather than read from public/status.json: orch#421's
# review caught that an `if _status.exists()` guard made this a SILENT NO-OP
# on any machine where the tick has not run yet (fresh clone, clean CI) --
# contributing to neither pass_n nor fail_n, which reproduces for the new gate
# the exact "green suite said nothing" failure the budget exists to prevent.
# Building the feed here always produces a number, so the check always runs.
_budget_kb = 150
try:
    sys.path.insert(0, str(ROOT))
    from orch import feed as _feed
    _kb = len(json.dumps(_feed.build(), separators=(",", ":"))) / 1024
    check("status_json_under_budget", "under %d KB" % _budget_kb,
          "under %d KB" % _budget_kb if _kb < _budget_kb else "%.0f KB" % _kb)
except Exception as _e:  # noqa: BLE001 -- a build failure must FAIL, not skip
    check("status_json_under_budget", "under %d KB" % _budget_kb,
          "could not build the feed: %s" % type(_e).__name__)

print(f"passed={pass_n} failed={fail_n}")
sys.exit(0 if fail_n == 0 else 1)
