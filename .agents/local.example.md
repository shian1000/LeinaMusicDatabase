# Local Context

Copy this file to `.agents/local.md` (gitignored) and fill in values for your machine. Nothing
here should ever be a secret — credentials go in `.env` (see `AGENTS.md` → Secrets); this file is
for non-secret, machine-specific facts and per-developer workflow permissions.

- Operating system: `<e.g. Linux, macOS, Windows>`
- Python executable / venv activation command: `<e.g. source venv/bin/activate>`
- Local `music.db` / `tag.db` path (if not the default `src/database/`): `<value>`
- Test database: `<value, or "none — tests mock the DB layer">`
- Real MP3 import folder in use (if not the default `import/`): `<value>`
- Terminal / shell notes: `<value>`
- Optional tool paths (e.g. a non-default Chrome/chromedriver for the scraping fetchers): `<value>`

## Personal workflow switches

Unchecked means the default from `AGENTS.md` applies (ask first). Check a box only to grant that
permission standing across sessions on this machine — it is not authorization for anything broader.

- [ ] The AI may create Git commits when explicitly asked to finalize.
- [ ] The AI may reset the disposable test setup without asking again each time.
- [ ] The AI may run the full test suite (including live-network tests) without asking first.
