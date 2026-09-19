# Artboards

`orch_Operator_UX.pdf` — the Operator UX artifact, vendored so it can be
diffed, versioned and read by a spawned agent. Provenance:
<https://claude.ai/code/artifact/32d8b13a-5a82-45d2-9e8e-0fff7372fa52>
(not publicly viewable — an unauthenticated fetch returns HTTP 403, see #237).

The in-repo copy is the authority. The URL is where it came from.

## Which artboards are current

The PDF predates the 2026-09-14 operator verdicts and was never revised
after them, so it carries a rejected design alongside the current one with
nothing marking the difference. Read it with this table.

| page | artboard | status |
|---|---|---|
| 1 | machine page, issues nested under repos (`Main`) | **current** |
| 2 | machine page, NEEDS YOU slice (`Flat`) | **CUT** — operator verdict 2026-09-14 on #150 |
| 3 | repo page — WHAT CHANGED, ACTIVE/INACTIVE, JOURNAL + input box | current |
| 4 | issue page — STATE, ACTIONS, SESSIONS, RUN LOG | current |

## Page 2 is cut

> "We do not need the needs you section. It is adding complexity."
> — operator verdict, 2026-09-14, on #150

Recorded with its reasoning at `../UX-REDESIGN.md:251-263`: the machine page
already shows every issue nested under its repo, so a counter pointing at
that same work adds a layer without adding a fact.

What survives is the row *vocabulary*, as row content rather than a section
(`../UX-REDESIGN.md:267`): `dead mid-flight`, `died before starting`,
`contended`, `waiting on merge`. Per-issue attention lives on the issue row —
its state chip and its escalation — nested under the repo that owns it.

**The precedent this sets:** a new "needs the operator" surface belongs on
the issue row where the work already is, not in a separate top-of-page list.
Relevant to #224.

## Refreshing this copy

The artifact and this copy can drift in either direction. If the artifact is
revised, re-export and update the table above in the same commit — a refresh
that leaves the status column stale reintroduces the problem this file exists
to solve (#237).
