import re

from utils.common.language_names import song_language
from utils.discoveries.discovery_result import LanguageDiscoveryResult
from utils.database.languages import words_basis
from utils.database.lyrics_store import lyrics_db_session, stored_lyrics
from config.constants import LANGUAGE_INSTRUMENTAL

# The song's own lyrics in lyrics.db (Fill missing data -> Lyrics), read by
# langdetect with the owner's rules (language_names.song_language(): the
# alphabet, the letters, the song's names, year and its artist's other songs
# and origin): no network, so it's asked
# for every song before the sites (LOCAL). A guess - its confidence decides
# whether it's saved at once (constants.LANGUAGE_DETECT_SURE).
MODULE_NAME = "Lyrics in the database (langdetect)"
LOCAL = True

_LRC_TIME = re.compile(r"\[\d+:\d+(?:[.:]\d+)?\]")


def get_language(artist: str, title: str, songs=None) -> LanguageDiscoveryResult | None:
    if not songs:
        return None
    with lyrics_db_session() as session:
        lyrics = stored_lyrics(session, songs[0].id)
    if lyrics is None:
        return None
    if lyrics.instrumental:
        return LanguageDiscoveryResult(LANGUAGE_INSTRUMENTAL, from_song=True)
    song = songs[0]
    year, origin, languages = words_basis(song)  # a cover's original's, when it sings its words
    found = song_language(lyrics.plain or _LRC_TIME.sub("", lyrics.synced or ""), song.title or "",
                          song.artist.name or "", origin, languages, year)
    if found is None:
        return None
    language, probability, needs_origin = found
    return LanguageDiscoveryResult(language, confidence=probability, from_song=True, source_ref=lyrics.source,
                                   needs_origin=needs_origin)
