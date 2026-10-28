"""The once-a-day "a newer yt-dlp is out" question (main.py, after the daily
backup). Logic: utils/youtube/ytdlp_update.py."""

import subprocess

from utils.ui.prompts import ask_yes_no

from config.constants import YTDLP_INSTALL_TIMEOUT
from utils.common.debug import flog
from utils.youtube.ytdlp_update import available_update, install_ytdlp


def offer_ytdlp_update() -> None:
    update = available_update()
    if update is None:
        return
    installed, latest = update
    print(f"A newer yt-dlp is out: {installed} -> {latest} (an old one gets blocked by YouTube sooner).")
    if not ask_yes_no(f"Update yt-dlp to {latest} now?"):
        return
    print("Updating yt-dlp...")
    try:
        changed = install_ytdlp(latest)
    except subprocess.CalledProcessError as exc:
        flog(f"yt-dlp update failed: {exc.stderr}")
        print(f"Updating yt-dlp failed - see debug.log. Still on {installed}.")
        return
    except subprocess.TimeoutExpired:
        flog(f"yt-dlp update: pip didn't finish in {YTDLP_INSTALL_TIMEOUT} s")
        print(f"Updating yt-dlp took too long and was stopped. Still on {installed}; it's offered again tomorrow.")
        return
    except OSError as exc:
        flog(f"yt-dlp update failed: {type(exc).__name__}: {exc}")
        print(f"Updating yt-dlp failed ({exc}). Still on {installed}.")
        return
    print(f"yt-dlp updated to {latest}. requirements.txt: {', '.join(changed) or 'no changes'}.")
