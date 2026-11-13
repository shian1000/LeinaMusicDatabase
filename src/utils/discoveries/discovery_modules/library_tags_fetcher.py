import re
from pathlib import Path

from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError

from utils.common.debug import flog
from utils.database.files_index import song_file_paths
from utils.discoveries.discovery_result import LyricsDiscoveryResult

# The lyrics embedded in the song's own mp3 files (ID3 USLT frames), found
# through files.db - no network. A few percent of the library has them.
MODULE_NAME = "Library files (local)"

_LRC_STAMP = re.compile(r"^\s*(\[\d+:\d{1,2}(?:[.:]\d{1,3})?\])+")


def _embedded_lyrics(path: Path) -> str | None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return None
    except (MutagenError, OSError) as exc:
        flog(f"[Library lyrics] can't read the tags of {path}: {exc}")
        return None
    texts = [frame.text.strip() for frame in tags.getall("USLT") if frame.text and frame.text.strip()]
    return max(texts, key=len) if texts else None


def _as_result(text: str) -> LyricsDiscoveryResult:
    """Some tools write LRC into USLT: then it's the synced text, and its
    lines without the times the plain one."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and all(_LRC_STAMP.match(line) for line in lines):
        plain = "\n".join(_LRC_STAMP.sub("", line).strip() for line in text.splitlines())
        return LyricsDiscoveryResult(plain=plain.strip() or None, synced=text, from_song_file=True)
    return LyricsDiscoveryResult(plain=text, from_song_file=True)


def get_lyrics(artist: str, title: str, duration_s: float | None = None, songs=None) -> list[LyricsDiscoveryResult]:
    """The lyrics in the files indexed for these songs (files.db), not looked
    up by name: a file is only read once it's matched to its song."""
    paths = song_file_paths([song.id for song in songs or []])
    found = []
    for song_paths in paths.values():
        for path in song_paths:
            text = _embedded_lyrics(Path(path))
            if text:
                found.append(_as_result(text))
    return found
