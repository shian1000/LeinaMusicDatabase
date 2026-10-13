"""The once-a-day "a newer yt-dlp is out" question (main.py, after the daily
backup). Logic: utils/youtube/ytdlp_update.py."""

import subprocess

import questionary

from utils.common.debug import slog
from utils.youtube.ytdlp_update import available_update, install_ytdlp


def offer_ytdlp_update() -> None:
    update = available_update()
    if update is None:
        return
    installed, latest = update
    print(f"A newer yt-dlp is out: {installed} -> {latest} (an old one gets blocked by YouTube sooner).")
    if not questionary.confirm(f"Update yt-dlp to {latest} now?", default=True).ask():
        return
    print("Updating yt-dlp...")
    try:
        changed = install_ytdlp(latest)
    except subprocess.CalledProcessError as exc:
        slog(f"yt-dlp update failed: {exc.stderr}", priority=3)
        print(f"Updating yt-dlp failed - see debug.log. Still on {installed}.")
        return
    except OSError as exc:
        print(f"Updating yt-dlp failed ({exc}). Still on {installed}.")
        return
    print(f"yt-dlp updated to {latest}. requirements.txt: {', '.join(changed) or 'no changes'}.")
