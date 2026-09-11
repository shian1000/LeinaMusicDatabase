# ADR-0001: Keep business logic independent of the terminal UI; add a future API as a sibling, not a refactor of it

- Status: Accepted
- Date: 2026-09-11

## Context

The app today is Python + SQLite + an interactive terminal menu (`src/menu/`), single user,
single machine, no server component. Android/iOS clients have been mentioned as a possible later
addition, but no API or mobile work has started, and none is scheduled.

The guide this project's documentation workflow is based on warns of a specific failure mode
(§13): *"the terminal UI becomes the application core, making mobile reuse expensive"* — i.e. SQL
and matching/business logic quietly accreting inside menu prompt handlers because it's the path of
least resistance for a single-interface app, until a second interface can't be added without
untangling it first.

A full layered rewrite (dedicated `domain/` / `application/` / `database/` / `tui/` / `api/`
packages, per the guide's suggested layout) was considered and rejected for now — see Alternatives
below. This ADR records the lighter-weight decision instead: a rule, enforced opportunistically,
not a restructuring.

## Decision

1. `src/menu/` stays presentation and workflow wiring only. It must not own SQL or
   matching/business logic — that already lives in `src/utils/*`, and it stays there. This
   restates as a recorded decision (with the *why*, above) what `AGENTS.md` → Architecture
   boundaries already states as a rule.
2. When API/mobile work actually starts, add it as a **new sibling package** (e.g. `src/api/`)
   that calls into the same `src/utils/*` orchestration layer `src/menu/` already calls into
   today — not a repurposing of `src/menu/` as a backend, and not a second copy of business logic
   inside the new package.
3. No dedicated `domain/`/`application/` layer split is happening now. `src/utils/*`'s existing
   split (`database/`, `discoveries/`, `youtube/`, `ui/`, `common/`) already gives the
   UI-independence property this decision cares about, for the app's current single-interface
   scale, without the churn and risk of a broader rewrite.
4. Enforcement is opportunistic: caught in review / by an agent reading `AGENTS.md`, not by a
   lint rule. There is currently nothing that mechanically stops `src/menu/` from importing
   SQLAlchemy directly.

## Consequences

- Mobile/API work later doesn't require first untangling SQL out of menu prompt handlers — the
  boundary already exists where it needs to.
- The cost is discipline without automated enforcement: a menu handler that inlines a DB query
  "just this once" violates the boundary silently. `AGENTS.md` is the front line for catching this
  in agent-assisted changes; there's no automated gate for it (see `docs/runbooks/` — none exists
  for this yet, and isn't proposed here).
- This decision does not resolve any existing debt inside `src/menu/` or `src/utils/*`; it only
  sets the rule going forward.

## Alternatives considered

- **Full `domain/`/`application/`/`database/`/`tui/`/`api/` restructuring now.** Rejected: large,
  risky refactor of a working single-developer app, with no concrete near-term payoff since no
  mobile work is scheduled. Revisit this decision (supersede it) if/when mobile work is actually
  about to start — see `docs/architecture.md` → "Where a future API would sit" for the checklist
  to work through first.
- **Building the API layer now, speculatively, alongside the menu.** Rejected: premature — no
  mobile client exists yet to justify it, and the guide this workflow follows is explicit that the
  boundary should be *designed* before the client is built, not built ahead of any client existing.
