---
name: issue-naming
description: "repo-orch's display-name cache: write a short name for every open issue that lacks one, right after consolidate. Read on every wake, after the consolidate stage."
---

# Issue naming — a short label, cached once

For each open issue with no entry in `state/repos/<slug>/names.json`, write
a one-to-three-word display name capturing what the issue is *about*. Skip
any issue already named — the cache is write-once (below).

## Storage

`state/repos/<slug>/names.json`. A **cache, not state**: delete it, nothing
breaks — names regenerate next wake, an unnamed row falls back to a
truncated title. Nothing derives from it, nothing gates on it.

Shape — each entry carries the name AND the title as read at naming time,
not the flat `{"289": "..."}` string the issue body sketched (Staleness
explains why):

```json
{
  "283": {"name": "widget tooltips", "title": "add tooltips to the 12 existing widget.tpl.html buttons"},
  "284": {"name": "widget new controls", "title": "add three new controls to widget.tpl.html"}
}
```

One call per wake writes every unnamed issue at once. Quote each argument
whole — the name and title both contain spaces, and the parse splits on
`=`, so `<n>=<name>=<title>` must arrive as one shell word:

```bash
~/orch/spawn.py names orch \
  "283=widget tooltips=add tooltips to the 12 existing widget.tpl.html buttons" \
  "284=widget new controls=add three new controls to widget.tpl.html"
```

The title field is optional (`<n>=<name>` alone stores `""`), but write it:
it is what makes staleness visible. The command prints `added <n>=...` or
`skipped <n> (already named)` per entry — journal what it reports.

## Write-once, and why

A name, once written, does not change. `names_write` (`orch/core.py`) never
overwrites an existing key. On a page whose whole point is watching rows
move, a label that mutates between paints is a defect. **Changing a name
means deleting its entry** — the only escape hatch.

## Staleness

Write-once means a retitled issue keeps its old name. Storing only the name
would make that drift invisible. So each entry also carries the title as it
read at naming time; the consumer compares it against the live title and
marks a drifted row. Precedent: orch#280, a landed-row test that passed
while the feature wrote to a key nobody read — nothing made the mismatch
visible. Never build a cache whose staleness is invisible.

## What makes a good name

One to three words, lowercase, no punctuation. Name the **subject**, not
the verb: `widget tooltips`, not `add tooltips to the widget`. Must
disambiguate against siblings — #283 and #284 both touch `widget.tpl.html`;
both named `widget fix` would be worse than useless. The title renders
beside the name, so the name never has to carry the whole meaning.

- `widget tooltips`, not `widget fix` (doesn't survive #284 existing)
- `ordinal priority labels`, not `priority stuff` (too vague)
- `config format collision` (#230/#212), not `fix the bug`
- `deploy pipeline dead` (#250), not `investigate the deployment issue` (a sentence, not a name)

### Prefer the concrete instance over the category

Operator ruling, 2026-09-17: *"for short descriptions try to make them use
examples to illustrate best instead of the generic description."*

A name built from abstract nouns names a topic the reader must already know.
A name carrying the specific thing — the file, the number, the symbol, the
literal value in the issue — can be pictured by someone who has never opened
it. When the issue body contains a concrete particular, **use it**.

Measured on the live feed 2026-09-17:

- `batch 4 episodes` works — because `ep22-ep26` is in the title beside it.
- `derived hold unwired` (#408) does not. Three abstract nouns; nothing to
  picture. `review finding no-op` names the same issue by the thing that
  actually misbehaves.
- `verify-tier gate` does not. `merge instant journal` (#280) does not —
  `journals nothing at merge` says the same thing and can be read cold.

The test: **could a reader who has not opened the issue picture what it is
about?** If the name only makes sense once you already know, it is a topic
label, not a name.

This does not license sentences or verbs-as-names — one to three words still
holds, and `deploy pipeline dead` is still better than `investigate the
deployment issue`. It narrows *which* nouns: pick the ones with a referent.

Where an issue genuinely has no concrete particular — a research question, a
policy decision — an abstract name is correct and no example should be
invented to satisfy this rule. A fabricated specific is worse than a vague
name, because it will be read as fact.

## Red line

Naming touches **no issue**: no title edit, no label, no comment. The red
lines in `repo-management/SKILL.md` and `repo-orch.md` — each file's own
"Red lines" section — stay exactly as they are; this skill adds a file
write, nothing else.

## Cost and consumer

Adds no reads: consolidate already loads every open issue via `World.load`
(`World.load` in `orch/core.py`, which fetches `gh issue list --json
number,title,labels,createdAt,updatedAt,comments`). Naming is
judgment over that already-loaded set, plus one `names` call.

`feed.py` reads the file and puts `display_name` on the issue row; the
renderer shows it beside the official title. That's **#289's job, not this
skill's** — this skill only writes the cache. A missing file and a missing
key both mean "fall back to a truncated title"; a stored `title` differing
from the live title is how a stale name is detected.
