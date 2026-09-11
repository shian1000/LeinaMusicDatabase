# Documentation Index

Entry point for humans and AI agents browsing this repo's documentation. If you're an AI agent
looking for rules, commands, and safety boundaries, start at [`AGENTS.md`](../AGENTS.md) instead —
this index is the map of everything else.

## Start here

- [Architecture](architecture.md) — module layout, dependency direction, where a future API would
  sit.
- [Data model](data-model.md) — the two SQLite databases, their schemas, and identifier policy.
- [System registry](systems/index.md) — which subsystem owns which feature, and how they depend on
  each other.
- [Runbooks](runbooks/) — operational procedures. Currently just
  [database backup, restore, and migrations](runbooks/database.md).

## Durable records

- [`agent-notes/`](agent-notes/) — deep, subsystem-specific rationale, tradeoffs, and gotchas for
  AI agents. One file per subsystem; indexed under "Topic notes" in `AGENTS.md`. Current behavior
  belongs in `architecture.md` / `data-model.md` / the system registry; *why* it works that way, or
  a bug it fixes, belongs here.
- [`runbooks/`](runbooks/) — step-by-step operational procedures (backup/restore, adding a
  migration). How to *do* something safely, as opposed to `agent-notes/`'s *why it works this way*.
- [`decisions/`](decisions/) — architectural decision records (ADRs), for choices that affect
  several systems or would be expensive to reverse. One so far:
  [ADR-0001](decisions/0001-ui-independent-business-logic.md) (keep business logic out of
  `src/menu/`; add a future API as a sibling, not a rewrite of it). Don't write one for an
  ordinary refactor.

## Temporary / point-in-time records

Not used yet. If a future task needs a `plans/` (proposed work not yet done) or `reviews/`
(point-in-time investigation whose conclusions stay useful) entry, create the directory then — once
work ships, fold the stable result into the pages above rather than leaving it stranded in a plan.
