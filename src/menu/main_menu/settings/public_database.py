"""Settings -> Server -> "Public database on GitHub": publishing the public
copy of the catalog for Leina Radio phones without the sharing server
(utils/database/public_database.py)."""

import questionary

from config.constants import PUBLIC_DB_RELEASE_TAG
from settings import Settings
from utils.common.env_file import read_env_value, save_env_value
from utils.database import public_database
from utils.database.moods import apply_global_mood_changes_quietly
from utils.ui.prompts import ask_yes_no

_COLORS = {public_database.PUBLISHED: "92", public_database.UNCHANGED: "92",
           public_database.SKIPPED: "93", public_database.FAILED: "91"}


def _print_result(result) -> None:
    print(f"\033[{_COLORS.get(result.status, '0')}m{result.status.capitalize()}: {result.message}\033[0m")
    if result.url:
        print(f"Release: {result.url}")


def _status_lines() -> list[str]:
    repo = Settings.github_db_repo or "(not set)"
    token = "set" if Settings.github_db_token else "not set"
    lines = [f"Repository: {repo}, token: {token}",
             f"Daily publish (after the daily backup, only when the catalog changed): "
             f"{'on' if public_database.daily_publish_enabled() else 'off'}"]
    last = public_database.last_result()
    lines.append(f"Last publish: {last['at']} - {last['status']}: {last['message']}" if last
                 else "Last publish: never")
    url = public_database.download_url("version.json")
    if url:
        lines.append(f"Leina Radio reads: {url}")
    return lines


def public_database_menu():
    while True:
        print()
        for line in _status_lines():
            print(line)
        daily = "Turn the daily publish off" if public_database.daily_publish_enabled() else "Turn the daily publish on"
        action = questionary.select("Public database on GitHub:", choices=[
            "Publish now", daily, "Set the repository", "Enter the token", "How to set it up", "Back",
        ]).ask()
        if action in (None, "Back"):
            return
        if action == "Publish now":
            publish_now()
        elif action == daily:
            public_database.set_daily_publish(not public_database.daily_publish_enabled())
        elif action == "Set the repository":
            _set_repository()
        elif action == "Enter the token":
            _enter_token()
        else:
            print_setup_steps()


def publish_now() -> None:
    missing = public_database.missing_setup()
    if missing:
        print(f"\033[93mNot set up yet: {' and '.join(missing)} missing.\033[0m")
        print_setup_steps()
        return
    applied = apply_global_mood_changes_quietly()
    if applied:
        print(f"Applied {len(applied)} change(s) to global moods from Leina Radio.")
    print(f"Building and checking the public copy, then publishing to {Settings.github_db_repo}...")
    result = public_database.publish()
    if result.status == public_database.UNCHANGED:
        _print_result(result)
        if not ask_yes_no("Publish it anyway?"):
            return
        result = public_database.publish(force=True)
    _print_result(result)


def _set_repository() -> None:
    current = read_env_value("GITHUB_DB_REPO")
    typed = questionary.text("GitHub repository (owner/repo):", default=current).ask()
    if typed is None:
        return
    value = typed.strip().strip("/")
    if value and value.count("/") != 1:
        print("\033[91mExpected owner/repo.\033[0m")
        return
    save_env_value("GITHUB_DB_REPO", value)
    Settings.github_db_repo = value
    print(f"GITHUB_DB_REPO {'set to ' + value if value else 'cleared'} in .env.")


def _enter_token() -> None:
    typed = questionary.password("Paste the token (hidden; empty = remove it):").ask()
    if typed is None:
        return
    value = typed.strip()
    save_env_value("GITHUB_DB_TOKEN", value)
    Settings.github_db_token = value
    print(f"GITHUB_DB_TOKEN {'saved' if value else 'removed'} in .env.")


def print_setup_steps() -> None:
    repo = Settings.github_db_repo or "<owner>/<repo>"
    print(f"""
How to set up publishing (once):
 1. On github.com: your picture -> Settings -> Developer settings -> Personal access tokens
    -> Fine-grained tokens -> Generate new token.
 2. Token name: e.g. "MusicDatabase public database". Expiration: up to a year (put a reminder
    in your calendar - when it expires, publishing says "GitHub refused the token").
 3. Repository access: "Only select repositories" -> {repo}.
 4. Permissions -> Repository permissions -> Contents: "Read and write" (Metadata: read-only is
    added by itself). Nothing else.
 5. Generate token, copy it, then here: "Enter the token" (or put GITHUB_DB_TOKEN=... in .env).
 6. "Set the repository" to {repo} (GITHUB_DB_REPO in .env), then "Publish now".
The first publish creates the release with the tag "{PUBLIC_DB_RELEASE_TAG}"; later ones replace its files.
""")
