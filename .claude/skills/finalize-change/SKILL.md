---
name: finalize-change
description: 'Use at the end of a coding task in the MusicDatabase repo to decide whether anything from this session is worth persisting for future AI agents, and if so, fold it into AGENTS.md or docs/agent-notes/ in place; also drafts a ready-to-use commit message for the user to review and commit themselves (the skill never runs git add/commit/push). Trigger this when the user says things like "finalize this change", "wrap this up", "document what we did for future agents", "update the guidelines", or invokes /finalize-change directly at the close of a task. Do not trigger it mid-task, for routine code review, or for every small commit — only when the user is closing out work and wants durable lessons captured, not a changelog of everything that happened.'
---

# Finalize Change

## Why this exists

This repo used to accumulate a new root-level markdown report every time an AI session finished
non-trivial work — `OPTIMIZATION_SUMMARY.md`, `PERFORMANCE_ANALYSIS.md`, `REFACTORING_NOTES.md`,
and so on. Five separate files ended up describing the same optimization effort, none of them
linked from anywhere, several contradicting the current code by the time anyone read them again.
They were eventually cleared out and folded into `AGENTS.md`.

This skill exists to stop that from happening again. The rule isn't "write down what changed" —
it's "keep exactly two places up to date, and only touch them when there's something durable to
say." Most changes don't clear that bar, and the right output of running this skill is often
touching nothing at all.

## The two places persistent knowledge can live

1. **`AGENTS.md`** (repo root) — the curated, always-read source of truth. Short, skimmable,
   edited in place. It should read the same whether it was last touched yesterday or a year ago;
   it is never a log of "what happened," only a description of "how things currently are."
2. **`docs/agent-notes/<topic>.md`** — one file per subsystem (e.g. `discovery-modules.md`,
   `normalization.md`, `import-pipeline.md`, `performance.md`), for the kind of deeper
   rationale/history/tradeoff detail that matters to an agent working in that specific area but
   would be dead weight in every agent's default context. `AGENTS.md` carries a one-line index
   under "Topic notes" pointing at each file that exists. Create the directory the first time you
   need it (`mkdir -p docs/agent-notes`) — it may not exist yet.

Both are living documents. Neither ever gets a second file for the same subject, and `AGENTS.md`
never gets a dated entry appended to the bottom — if you catch yourself about to add "## Update
2026-07-30" anywhere, stop; find the existing section this belongs to instead.

## Step 1 — Gather what actually happened

Run `git status` and `git diff` (and `git diff --cached` / `git log` for anything already
committed this session) to get the concrete, ground-truth set of files touched. Don't rely on
memory of the conversation for *what* changed — diffs don't lie about that.

Do rely on the conversation for *why*: the diff won't tell you about dead ends you hit, a
constraint you discovered the hard way, or a design tradeoff you talked through with the user.
That reasoning is exactly the kind of thing worth capturing, and it only exists in your context.

## Step 2 — Decide if anything clears the bar

Most tasks should end with "nothing to persist" — that's a healthy, expected outcome, not a
failure to find something to write. Ask, for each candidate fact: *would a future agent get this
right anyway just by reading the diff, the code, or the commit message?* If yes, skip it — that's
what `git log`/`git blame` are for, and duplicating them in prose just gives the next reader two
sources that can drift apart.

Worth persisting, roughly in order of how likely they are to matter:

- A **hidden constraint or gotcha** that isn't visible from reading the code casually — something
  that would silently break if a future agent didn't know about it (the way the numeric filename
  prefixes on the discovery fetchers silently control lookup order — that's the shape of thing
  that belongs here).
- A change to **setup, entry points, or how to run/verify** something.
- A **cross-cutting convention** now used across multiple modules (a shared helper that
  supersedes ad-hoc local versions, a new required pattern).
- A new **secret, credential, or sensitive path** introduced.
- Deeper **subsystem-specific rationale** — why an approach was chosen, what was tried and
  rejected, performance characteristics discovered — that's genuinely useful if someone works in
  that exact area again, but too in-the-weeds for the top-level file. This is `docs/agent-notes/`
  territory, not `AGENTS.md`.

Not worth persisting: ordinary bug fixes, small refactors, anything self-explanatory from the
diff. If the whole session was one of these, say so plainly and stop — don't strain to manufacture
a note.

## Step 3 — Revise in place, don't append

For anything that does clear the bar:

1. Figure out which of the two files it belongs in (top-level convention/gotcha → `AGENTS.md`;
   deep subsystem detail → `docs/agent-notes/<topic>.md`).
2. **Search that file first** for existing content on the same subject. If something's already
   there, edit it — fix what's now wrong, extend what's now incomplete, or fold in the new detail
   where it naturally reads. Treat this like tightening a paragraph, not like adding a footnote.
3. Only write a brand-new bullet/subsection when the subject genuinely isn't covered yet.
4. For `docs/agent-notes/`, check the "Topic notes" index in `AGENTS.md` before creating a file —
   if an existing topic file is a reasonable home for this (even if not a perfect match), amend
   that one rather than spinning up a narrower one. The value of the taxonomy comes from staying
   small; a topic file per session defeats the whole point. If you do create a new topic file, add
   its one-line pointer under "Topic notes" in `AGENTS.md` — that's the only case where
   `AGENTS.md` gets a new line rather than a revision.
5. Running this skill twice on the same underlying change should converge, not duplicate — the
   second pass should find its own earlier edit already covering the ground and leave it alone
   (or tighten it further), never add a second, near-identical bullet next to the first.
6. **Keep the indexes honest, mechanically.** This is a different, narrower check than steps 1–5
   above — not "is this worth persisting" but "did the session change the *shape* of something
   that's already indexed elsewhere, and did that index get updated." If the session:
   - added, removed, or renamed a subsystem, or changed which subsystems depend on which —
     `docs/systems/index.md` needs the matching row/edge;
   - added, removed, or renamed a top-level doc or doc category — `docs/index.md` needs updating;
   - added a reusable script under `tools/` — `tools/README.md` needs an entry for it (what it
     does, when to use it, invocation, what it may change, dry-run/backup behavior, limitations —
     see that file's existing entries for the shape);
   - made a choice that affects several systems or would be expensive to reverse (this is a higher
     bar than an ordinary AGENTS.md-worthy convention — see Step 2) — it may be worth a new ADR
     under `docs/decisions/`, indexed from `docs/index.md`. Don't write one for an ordinary
     refactor; when in doubt, this one's worth asking the user about rather than guessing.
   Each of these is a mechanical sync, not a new judgment call — if nothing changed shape, there's
   nothing to touch here.

## Step 4 — Report back

Since this skill writes directly (no draft-and-confirm step — the user reviews via normal `git
diff` afterward), give a short, concrete summary: which file(s) you touched and, in a sentence
each, what changed in them. If you decided nothing was worth persisting, say that explicitly and
briefly explain why (e.g. "straightforward bug fix, fully explained by the diff and commit
message") — an agent that silently does nothing is indistinguishable from one that forgot to run,
so always state the outcome.

## Step 5 — Draft a commit message (text only — never commit it yourself)

After Step 4, draft a ready-to-use commit message covering the session's actual changes (the same
`git status`/`git diff` ground truth from Step 1 — the whole diff, not just whatever this skill
itself edited in Steps 1–3) and put it in your chat response in a fenced code block, so the user
can copy it as-is, edit it, or ignore it.

**Never run `git add`, `git commit`, `git push`, or any other git write yourself as part of this
step** — `AGENTS.md` → Git policy already says not to commit unless explicitly asked, and drafting
a message is not that ask. This step produces text for the user to act on, nothing else.

Match this repo's actual commit style rather than a generic template — check recent history first
(`git log --format="%s%n%b%n---" -10`) if you haven't already this session. As of this writing that
style is a single terse subject line, imperative mood, occasionally multiple clauses joined by
`" - "` for a session that touched several things; no blank-line body, no Conventional Commits
prefix, no trailer. Follow that shape unless the user's own recent commits have visibly drifted
from it — don't impose a different convention (e.g. a multi-paragraph body, a `feat:`/`fix:`
prefix) just because it's more common elsewhere.

If `git status`/`git diff` shows changes this session didn't make — a real, recurring situation in
this repo, which the user often works in concurrently with the running app or another session —
**do not describe those changes as if this session made them.** Either scope the draft message to
only this session's own changes (preferred when the two are cleanly separable, e.g. by file), or
say plainly in your chat response (not inside the drafted message) that the working tree has
unrelated changes mixed in and the user should split them before committing.
