"""Lyrics in Latin letters, line by line, for Leina Radio's "Transliteruj tekst"
(stored in lyrics.db's lyrics_latin by lyrics_store.refresh_lyrics_latin(),
sent with GET /lyrics/<song id>).

Each line goes through romanize.suggest_latin(), the same rules as the Latin
forms of names; the Cyrillic system (and Japanese / Chinese) is settled once
for the whole text, so a Ukrainian line without і isn't read as Russian. A
text where any line can't be read (kanji the dictionary lacks, Hebrew, ...)
gets no Latin form at all - better none than a mixed one. Ukrainian lyrics
also get romanize.ukrainian_slavic() as the alternative LYRICS_LATIN_UK_SLAVIC.
"""
import re
import unicodedata

from config.constants import LYRICS_LATIN_DEFAULT, LYRICS_LATIN_UK_SLAVIC
from utils.common.romanize import (
    _han_system,
    _only_latin_letters,
    cyrillic_language,
    suggest_latin,
    ukrainian_slavic,
)
from utils.common.script_utils import scripts_in

# cyrillic_language()'s / _han_system()'s codes as songs.language writes them
_LANGUAGE_OF_SYSTEM = {"be": "bel", "uk": "ukr", "bg": "bul", "mn": "mon", "sr": "srp", "ru": "rus",
                       "ja": "jpn", "zh": "zho", "yue": "yue"}
# An LRC line's time stamps (and the space after them), kept as they are
_LRC_PREFIX = re.compile(r"^(\s*(?:\[[^\]]*\]\s*)*)(.*)$")
# Hangul fillers: invisible, but letters to Unicode - pasted as "blank" lines
# or after a word ("right nowㅤ"), they'd count as text no one can read
_HANGUL_FILLERS = re.compile("[\u115f\u1160\u3164\uffa0]")


def _system_language(text: str, language: str | None, origin: str | None) -> tuple[str, str | None]:
    """(songs.language with the whole text's systems added, the Cyrillic code or None)."""
    scripts = scripts_in(text)
    codes, cyrillic = [language] if language else [], None
    if "CYRILLIC" in scripts:
        cyrillic = cyrillic_language(text, language, origin)
        codes.append(_LANGUAGE_OF_SYSTEM[cyrillic])
    if "KANA" in scripts or "CJK" in scripts:
        codes.append(_LANGUAGE_OF_SYSTEM[_han_system(text, language, origin)[0]])
    return " / ".join(codes), cyrillic


def _lines(text: str | None, convert, lrc: bool) -> str | None:
    """`text` with each line's words converted, or None when a line can't be."""
    if text is None:
        return None
    out = []
    for line in text.split("\n"):
        prefix, words = _LRC_PREFIX.match(line).groups() if lrc else ("", line)
        words = _HANGUL_FILLERS.sub("", words)
        if not _only_latin_letters(words):
            words = convert(words)
            if words is None or not _only_latin_letters(words):
                return None
        out.append(prefix + words)
    return "\n".join(out)


def transliterate_lyrics(plain: str | None, synced: str | None, language: str | None = None,
                         origin: str | None = None) -> dict:
    """{method: (plain, synced)} - LYRICS_LATIN_DEFAULT always (both None when
    the text is all Latin already or can't be done), LYRICS_LATIN_UK_SLAVIC
    for Ukrainian lyrics. `language` is songs.language, `origin` the artist's."""
    plain = unicodedata.normalize("NFKC", plain) if plain else None
    synced = unicodedata.normalize("NFKC", synced) if synced else None
    whole = _HANGUL_FILLERS.sub("", "\n".join(t for t in (plain, synced) if t))
    if not whole or _only_latin_letters(whole):
        return {LYRICS_LATIN_DEFAULT: (None, None)}
    hint, cyrillic = _system_language(whole, language, origin)

    def default(words: str) -> str | None:
        return suggest_latin(words, hint)

    methods = {LYRICS_LATIN_DEFAULT: default}
    if cyrillic == "uk":
        methods[LYRICS_LATIN_UK_SLAVIC] = ukrainian_slavic
    result = {}
    for method, convert in methods.items():
        latin_plain, latin_synced = _lines(plain, convert, False), _lines(synced, convert, True)
        # half done is no transliteration
        if (plain and latin_plain is None) or (synced and latin_synced is None):
            latin_plain = latin_synced = None
        if method == LYRICS_LATIN_DEFAULT or latin_plain or latin_synced:
            result[method] = (latin_plain, latin_synced)
    return result
