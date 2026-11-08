"""MusicDatabaseWired: a sharing server for Leina Player that runs on its own.

    venv/bin/python mdw.py serve            run the server
    venv/bin/python mdw.py menu             users, requests, the server's state
    venv/bin/python mdw.py update-catalog   download a newer catalog (add-on "sync")
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]

from musicdatabase_wired.config import load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MusicDatabaseWired")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the server")
    for name in ("menu", "update-catalog"):
        commands.add_parser(name)
    serve.add_argument("--catalog", help="folder of music.db and tag.db")
    serve.add_argument("--users", help="folder of the users' accounts and data")
    serve.add_argument("--inbox", help="folder of the requests")
    serve.add_argument("--tls-dir", help="folder of the server's own certificate")
    serve.add_argument("--no-tls", action="store_true", help="plain HTTP, for trying it out on this machine")
    serve.add_argument("--address", action="append", help="an exact address to listen on; repeatable")
    serve.add_argument("--port", type=int)
    serve.add_argument("--request-timeout", type=float, help="seconds a connection may stay silent")
    serve.add_argument("--addon", action="append", help="an add-on to load; repeatable")
    serve.add_argument("--no-catalog-updates", action="store_true", help="don't look for a newer catalog")
    args = parser.parse_args()

    config = load_config(args if args.command == "serve" else None)
    if args.command == "serve":
        from musicdatabase_wired.server import run
        return run(config)
    if args.command == "menu":
        from musicdatabase_wired.menu import run_menu
        return run_menu(config)
    from musicdatabase_wired.addons.catalog import CatalogError, update_catalog
    try:
        print("Catalog updated." if update_catalog(config) else "The catalog is up to date.")
    except CatalogError as exc:
        print(f"Couldn't update the catalog: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
