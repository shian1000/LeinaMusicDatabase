"""The first-start question (main.py): a fresh install has no music.db /
tag.db - download the public catalog or start empty. Logic:
utils/database/first_run.py."""

import questionary

from utils.database import first_run
from utils.database.first_run import (
    CatalogDownloadError,
    create_empty_databases,
    download_public_catalog,
    fetch_catalog_info,
    missing_databases,
)

DOWNLOAD = "download"
EMPTY = "empty"
QUIT = "quit"


def ensure_databases() -> bool:
    """True when music.db and tag.db are there (now). False when the user
    chose to quit - main.py stops then, and asks again next time."""
    missing = missing_databases()
    if not missing:
        return True
    if "music.db" not in missing:
        # Only tag.db gone: an empty one - the owner's tags wouldn't fit these songs
        create_empty_databases()
        print(f"Created an empty tag.db in {first_run.database_dir()}.")
        return True
    print(f"There's no database in {first_run.database_dir()} yet.")
    print(f"The public catalog is the songs and artists the project's owner collected, from {first_run.catalog_base_url()}.")
    print("You can extend it from there, or start with an empty database and import your own music.\n")
    while True:
        choice = questionary.select(
            "How do you want to start?",
            choices=[
                questionary.Choice("Download the public catalog", value=DOWNLOAD),
                questionary.Choice("Start with an empty database", value=EMPTY),
                questionary.Choice("Quit (ask again next time)", value=QUIT),
            ],
        ).ask()
        if choice in (None, QUIT):
            return False
        if choice == EMPTY:
            create_empty_databases()
            print(f"Empty music.db and tag.db created in {first_run.database_dir()}.")
            return True
        if _download():
            return True


def _download() -> bool:
    try:
        print("Checking the public catalog...")
        info = fetch_catalog_info()
        print(f"Downloading {info.total_bytes / 1024 / 1024:.1f} MiB "
              f"(published {info.published_at or 'at an unknown time'})...")
        written = download_public_catalog(info=info)
    except CatalogDownloadError as exc:
        print(f"\033[91mThe download failed: {exc}\033[0m")
        print("Nothing was saved. Try again, start empty, or quit and try later.\n")
        return False
    for path in written:
        print(f"  {path}")
    print("Public catalog downloaded and checked against its version.json.\n")
    return True
