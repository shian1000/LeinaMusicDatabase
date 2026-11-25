from config.constants import LANGUAGE_UNKNOWN, NO_VIDEO_SENTINEL
from settings import Settings
from utils.common.language_names import language_code
from utils.discoveries.discovery_result import LanguageDiscoveryResult
from utils.youtube.manage_youtube_playlists import audio_languages_via_api

# The audio language the uploader set on the song's own video
# (songs.youtube_video_id; snippet.defaultAudioLanguage of the YouTube Data
# API's videos.list, 50 videos a request, one quota unit), a BCP-47 code
# ("en-US" -> English, "zxx" = no words -> Instrumental, "und" = nothing).
# Only with the YOUTUBE_API_KEY the link checks use - no other key, never the
# OAuth login; without it the module answers nothing.
# Measured 2026-11-20 on 500 songs with a known language and 431 with neither
# lyrics nor a language: about a third of the videos carry it, and ~94% of
# those agreed with the stored language - wrong ones were a Ukrainian song
# marked "ru", Polish and Arabic ones "en", a Norwegian one "el", an English
# one "ja". So it's last (after the lyrics and the sites that say it outright)
# and never sure (never_sure): every answer goes to the owner's review list.
MODULE_NAME = "YouTube audio language (Curl + API)"
BY_SONG = True  # reads the song's own link, whatever its names: asked once a song

_codes = {}  # video id -> its code, "" when it has none
_failed = set()  # in a request that failed: raising until the next prepare()


def _video_id(song) -> str | None:
    video_id = getattr(song, "youtube_video_id", None)
    return video_id if video_id and video_id != NO_VIDEO_SENTINEL else None


def _ask(video_ids) -> None:
    ids = [video_id for video_id in dict.fromkeys(video_ids) if video_id not in _codes]
    if not ids:
        return
    found = audio_languages_via_api(ids)
    if found is None:
        _failed.update(ids)
    else:
        _codes.update({video_id: found.get(video_id, "") for video_id in ids})


def prepare(songs) -> None:
    """Ask about the videos of every song about to be looked up at once."""
    _failed.clear()
    if Settings.youtube_api_key:
        _ask(filter(None, map(_video_id, songs)))


def language_of_code(code: str) -> str | None:
    """The stored language a BCP-47 code names ("pt-BR" -> "por"), None
    for "und" and codes naming none."""
    found = language_code(code.split("-")[0].lower()) if code else None
    return None if found in (None, LANGUAGE_UNKNOWN) else found


def get_language(artist: str, title: str, songs=None) -> LanguageDiscoveryResult | None:
    video_id = _video_id(songs[0]) if songs and Settings.youtube_api_key else None
    if video_id is None:
        return None
    if video_id not in _codes and video_id not in _failed:
        _ask([video_id])
    if video_id in _failed:
        raise RuntimeError("YouTube's Data API couldn't be asked")
    language = language_of_code(_codes[video_id])
    if language is None:
        return None
    return LanguageDiscoveryResult(language, from_song=True, never_sure=True, source_ref=video_id)
