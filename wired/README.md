# MusicDatabaseWired

A server for [Leina Player](https://github.com/shian1000/LeinaPlayer) that runs on its own. For now it
does one thing: a logged-in phone hands over a request and the server checks it. It needs nothing from
MusicDatabaseHomeServer, the rest of this repository. Syncing users' data and serving the music catalog
are an add-on, off by default (`WIRED_ADDONS=sync`).

It's early: the plan and what's still missing are in
[ADR-0006](../docs/decisions/0006-lite-sharing-server.md). Don't open it to the internet yet.

## Install

Python 3.13 and `openssl`, on Linux. No other version of Python has been tried.

```bash
cd wired
python3 -m venv venv
venv/bin/pip install --no-deps -r requirements.txt
cp .env.example .env
```

Set `WIRED_ADDRESSES` in `.env`. It never listens anywhere you didn't name.

## Run

```bash
venv/bin/python mdw.py serve
```

It prints a certificate code, which you compare once in Leina Player when you add the server.

```bash
venv/bin/python mdw.py menu
```

The menu makes users, changes passwords, logs phones out, shows the requests and the server's state. It only changes
files the server reads, so it can be used while the server runs.

## What's different from the home server

- No login, no requests: a request without an account is refused.
- A request arrives in `inbox/new/` and is checked at once. A correct one is kept in `inbox/valid/` as a
  new file made from the checked rows. A kind this version doesn't know waits in `inbox/held/` until the
  menu checks it again, and a failed one goes to `inbox/rejected/`. Only the server's account can open
  them, so it won't start on a drive that can't keep permissions, such as exFAT.
- No syncing, music catalog, streaming or global moods without an add-on. A phone asking for those is told the server doesn't offer them.

Data lives in `data/` (`catalog/`, `users/`, `inbox/`, `tls/`, `archive/`), or wherever `WIRED_DATA_DIR`
points. Back up `users/`: nothing else holds it.

## Dependencies

Only the packages in [DEPENDENCIES.md](DEPENDENCIES.md), each pinned, and a test fails on any other
import. From the repository root, with the home server's venv for pytest:

```bash
CONTRACT_SERVER=mdw venv/bin/python -m pytest tests/contract
```

```bash
venv/bin/python -m pytest tests/test_wired.py tests/test_wired_vetting.py tests/test_wired_imports.py
```
