"""Song actions -> "Song or full album" and the song editor's "kind" entry:
mark rows that are really a whole album (songs.kind, utils/database/song_kinds.py)."""
import questionary

from config.constants import SONG_KIND_FULL_ALBUM, SONG_KIND_LABELS
from utils.database.database_sessions import submit_global_database_session
from utils.database.song_kinds import set_song_kind
from utils.ui.display_utils import display_songs


def set_kind_of_songs(songs_objects) -> None:
    choices = {
        "Full album (the whole album in one recording)": SONG_KIND_FULL_ALBUM,
        "Song": None,
    }
    cancel = "Cancel"
    display_songs(songs_objects)
    choice = questionary.select(
        f"What are these {len(songs_objects)} entries?", choices=[*choices, cancel]
    ).ask()
    if choice in (None, cancel):
        print("Aborted")
        return
    kind = choices[choice]
    filed = set_song_kind(songs_objects, kind)
    submit_global_database_session()
    print(f"Marked {len(songs_objects)} entry(ies) as {SONG_KIND_LABELS[kind]}.")
    for song in filed:
        print(f'  "{song.title}" had no album - filed under the album "{song.album}".')
