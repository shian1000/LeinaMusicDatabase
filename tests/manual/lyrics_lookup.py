#!/usr/bin/env python3
"""Fill missing data -> Lyrics' lookup on random songs of a copy of the
catalog, against the real LRCLIB: how many are sure, for review, or not found.
Nothing is saved (the copy is thrown away). Usage: lyrics_lookup.py [count]"""

import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from _sandbox import enter_sandbox

enter_sandbox(verbosity=0)

from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.lyrics_store import lyrics_db_session, song_durations, songs_missing_lyrics
from utils.discoveries.discoveries_manager import discover_lyrics, load_discovery_modules
from utils.discoveries.discovery_settings import LYRICS

count = int(sys.argv[1]) if len(sys.argv) > 1 else 30
session, _ = open_and_set_global_database_sessions()
with lyrics_db_session() as lyrics_session:
    songs = random.sample(songs_missing_lyrics(session, lyrics_session), count)
durations = song_durations(session, songs)
modules = load_discovery_modules(LYRICS)
outcome = {"sure": [], "review": [], "none": []}
for song in songs:
    match = discover_lyrics(song, modules, durations.get(song.id))
    key = "none" if match is None else "sure" if match.sure else "review"
    outcome[key].append(f"{song.artist.name} - {song.title}"
                        + (f"  <->  {match.result.matched_artist} - {match.result.matched_title} "
                           f"({match.duration_s} / {match.found_duration_s})" if match else ""))
print()
for key, lines in outcome.items():
    print(f"{key}: {len(lines)}")
    for line in lines:
        print(f"  {line}")
