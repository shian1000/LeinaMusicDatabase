# MusicDatabase

A terminal app for browsing and enriching a local music database (local files + external metadata
sources such as MusicBrainz, Wikipedia, iTunes, and Genius).

## Setup

```bash
git clone https://github.com/shian1000/MusicDatabase.git
cd MusicDatabase

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in the values described in .env.example
```

## Run

```bash
python main.py
```

> **Known gap:** a brand-new clone has no `src/database/*.db` files yet, and nothing currently
> creates them automatically on first run — see
> [`docs/runbooks/database.md`](docs/runbooks/database.md) → Known gap.

## Tests

```bash
python -m pytest
```

See [`tests/README.md`](tests/README.md) for what's covered.

## Contributing / working on this with an AI agent

Start at [`AGENTS.md`](AGENTS.md) — project layout, architecture boundaries, database safety
rules, and the standard commands for this repo. [`docs/index.md`](docs/index.md) is the wider
documentation entry point (architecture, data model, system registry, runbooks).
