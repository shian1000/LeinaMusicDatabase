import socket
import time
from contextlib import contextmanager

import musicbrainzngs
from utils.common.debug import flog, slog
from utils.common.text_utils import is_blacklisted_album, title_resembles, similarity
from utils.common.normalizer import normalize
from utils.discoveries.discovery_result import AlbumTrack, DiscoveryResult, YearDiscoveryResult
import re
import requests
from difflib import SequenceMatcher
from utils.common.text_utils import check_spelling
from config.constants import MUSICBRAINZ_API_USER_AGENT, MUSICBRAINZ_VARIOUS_ARTISTS_ID
from utils.common.musicbrainz_client import lucene_quote

# Set up the user agent (required by MusicBrainz). Contact points at the project
# repo; see MUSICBRAINZ_API_USER_AGENT in config/constants.py.
musicbrainzngs.set_useragent("MusicDatabase", "1.0", "https://github.com/shian1000/LeinaMusicDatabase")

HEADERS = {"User-Agent": MUSICBRAINZ_API_USER_AGENT}
MODULE_NAME = "Musicbrainz fetcher"

# musicbrainzngs issues its HTTP requests via urllib with no per-call timeout,
# so a stalled connection (server accepts then never answers) blocks forever
# instead of raising. It retries internally on socket.timeout, so bounding the
# socket here turns a permanent hang into a handled NetworkError.
MUSICBRAINZ_SEARCH_TIMEOUT = 20


@contextmanager
def _bounded_socket_timeout(seconds: float):
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)

def _recording_title_artist(recording, artist: str) -> tuple[str, str]:
    """Best-effort extraction of what MusicBrainz recording a release came from.
    The artist is the credit closest to the queried one, not the first
    ("Ewan McGregor + José Feliciano + Jacek Koman" for José Feliciano)."""
    if not recording:
        return "", ""
    return recording.get("title", ""), _credited_name_like(recording, artist)

# Without a studio album, an EP beats a single and an earlier release beats a
# later one: a song's later "(Cinematic)"/"(Acoustic)" single tends to come
# first in search results ("Notion" -> "Notion (Cinematic)" instead of the
# "Futureproof" EP it came out on).
FALLBACK_RELEASE_TYPE_RANK = {"EP": 0, "Single": 1}


def _pick_release(recordings, artist: str | None = None) -> tuple[str | None, dict | None]:
    """(release title, its recording) for the best release of the search hits.

    The first Album in result order wins. Otherwise the best-ranked other
    release of the first recording that has any, by type then earliest date
    (undated last). Compilations, live albums, remixes, blacklisted titles,
    another artist's release and another song's single never count.
    """
    title, recording, _release = _pick_release_with_entry(recordings, artist)
    return title, recording


def _pick_release_with_entry(recordings, artist: str | None = None) -> tuple[str | None, dict | None, dict | None]:
    """_pick_release() plus the picked release entry itself (its id and the
    recording's place on it)."""
    for recording in recordings:
        for release in recording.get("release-list", []):
            if (_release_is_eligible(release, artist) and not _is_other_artists_release(release, recording)
                    and release.get("release-group", {}).get("primary-type", "") == "Album"):
                return _release_title(release), recording, release

    for recording in recordings:
        candidates = [release for release in recording.get("release-list", [])
                      if _release_is_eligible(release, artist) and not _is_other_artists_release(release, recording)
                      and not _is_other_songs_single(release, recording)]
        if candidates:
            best = min(candidates, key=lambda release: (
                FALLBACK_RELEASE_TYPE_RANK.get(release.get("release-group", {}).get("primary-type", ""),
                                               len(FALLBACK_RELEASE_TYPE_RANK)),
                release.get("date") or "9999",
            ))
            return _release_title(best), recording, best
    return None, None, None


def _as_int(value) -> int | None:
    """MusicBrainz's numbers come as text; a vinyl side's "A1" is no number."""
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isascii() and text.isdigit() else None


def _track_position(release) -> tuple[int | None, int | None]:
    """(track number, disc number) of the searched recording on a release of
    the search results, whose medium-list holds just that track. The disc is
    None on a one-disc release (songs.disc_number: NULL = 1)."""
    media = release.get("medium-list") or []
    several_discs = (_as_int(release.get("medium-count")) or len(media)) > 1
    for medium in media:
        for track in medium.get("track-list", []):
            return _as_int(track.get("number")), (_as_int(medium.get("position")) if several_discs else None)
    return None, None


def _tracks_loader(release):
    """A function fetching the release's whole tracklist (one more request),
    for the manager's album cache - None for a release that isn't an album or
    an EP, since a single's other tracks don't belong to the album we want."""
    release_id = release.get("id")
    if not release_id or release.get("release-group", {}).get("primary-type", "") not in ("Album", "EP"):
        return None

    def load() -> list[AlbumTrack]:
        try:
            with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
                response = musicbrainzngs.get_release_by_id(release_id, includes=["recordings", "artist-credits"])
        except musicbrainzngs.WebServiceError as e:
            flog(f"[{MODULE_NAME}] couldn't load the tracklist of release {release_id}: {e}")
            return []
        full = response.get("release", {})
        media = full.get("medium-list", [])
        tracks = []
        for medium in media:
            disc = _as_int(medium.get("position")) if len(media) > 1 else None
            for track in medium.get("track-list", []):
                recording = track.get("recording", {})
                title = track.get("title") or recording.get("title")
                if not title:
                    continue
                tracks.append(AlbumTrack(
                    title=title,
                    track_number=_as_int(track.get("number")) or _as_int(track.get("position")),
                    disc_number=disc,
                    artist=recording.get("artist-credit-phrase") or full.get("artist-credit-phrase"),
                ))
        return tracks

    return load


def _album_result(title: str, recording, release, artist: str) -> DiscoveryResult:
    matched_title, matched_artist = _recording_title_artist(recording, artist)
    track_number, disc_number = _track_position(release)
    return DiscoveryResult(album=title, matched_title=matched_title, matched_artist=matched_artist,
                           track_number=track_number, disc_number=disc_number,
                           load_tracks=_tracks_loader(release))


def _release_title(release) -> str:
    """The release group's title - the album as a whole - over this one
    edition's: one edition of Low Roar's "0" is entered as "O" and was listed
    first. Falls back to the release's own title."""
    return release.get("release-group", {}).get("title") or release.get("title", "")


def _artist_ids(credit) -> set:
    return {c["artist"]["id"] for c in credit or [] if isinstance(c, dict) and c.get("artist", {}).get("id")}


def _is_other_artists_release(release, recording) -> bool:
    """A release credited to someone else entirely: an Australian edition of
    Dido's "No Angel" has four LeAnn Rimes songs appended, and was picked as
    "Can't Fight the Moonlight"'s album. Compared by MusicBrainz artist id, so
    spellings don't matter. "Various Artists" (a soundtrack - "Moulin Rouge"
    for José Feliciano) and a release by any of the recording's own artists
    still count, and so does a release with no artist in the results."""
    release_ids = _artist_ids(release.get("artist-credit"))
    if not release_ids or MUSICBRAINZ_VARIOUS_ARTISTS_ID in release_ids:
        return False
    return not release_ids & _artist_ids(recording.get("artist-credit"))


def _is_other_songs_single(release, recording) -> bool:
    """A single named after a different song, with this one as its extra track:
    "Death by Rock and Roll" is also on the single "25". Not this song's album.
    An EP has its own name, so only singles are checked."""
    if release.get("release-group", {}).get("primary-type", "") != "Single":
        return False
    return not title_resembles(recording.get("title", ""), _release_title(release))


def _release_is_eligible(release, artist: str | None = None) -> bool:
    secondary_types = release.get("release-group", {}).get("secondary-type-list", [])
    if any(t in ("Compilation", "Live", "Remix", "DJ-mix") for t in secondary_types):
        return False
    return not is_blacklisted_album(_release_title(release), artist)


def _spell_checked(artist: str, song: str) -> tuple[str, str]:
    """(artist, song) as check_spelling() corrects them, unchanged when it
    found nothing."""
    spell_check_result = check_spelling(artist, song)
    slog(spell_check_result)
    if spell_check_result["found"]:
        return spell_check_result["corrected_artist"], spell_check_result["corrected_title"]
    return artist, song


def _search_album(artist: str, song: str) -> DiscoveryResult | None:
    """The album of the first 5 recording hits, then of the first 25 (a later
    hit's studio album can still beat an early hit's single)."""
    query = f'recording:{lucene_quote(song)} AND artist:{lucene_quote(artist)}'
    slog(query, priority=1)
    for limit in (5, 25):
        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_recordings(query=query, limit=limit)
        recordings = response.get("recording-list", [])
        result, result_recording, result_release = _pick_release_with_entry(recordings, artist)
        slog(result)
        if result:
            return _album_result(result, result_recording, result_release, artist)
    return None


def get_album_name(artist: str, song: str, delay: float = 1.0, spell_check = False) -> DiscoveryResult | None:
    """Album of (artist, song) from a MusicBrainz recordings search, or None
    when nothing is found or on error.

    Searches as given first. Only when that finds nothing, searches again with
    the spelling check_spelling() corrects (if it corrects anything).
    spell_check=True corrects the spelling before the first search instead.
    """
    slog(f"{artist} - {song}", priority=1)
    try:
        if spell_check:
            artist, song = _spell_checked(artist, song)
        result = _search_album(artist, song)
        if result is None and not spell_check:
            corrected = _spell_checked(artist, song)
            if corrected != (artist, song):
                artist, song = corrected
                slog(f"{artist} - {song}", priority=1)
                result = _search_album(artist, song)
        time.sleep(delay)
        return result

    except musicbrainzngs.WebServiceError as e:
        flog(f"[MusicBrainz] error for the album of '{artist} - {song}': {e}")
        return None


def _extract_year(date_str: str | None) -> int | None:
    """MusicBrainz dates come as 'YYYY', 'YYYY-MM', or 'YYYY-MM-DD' -- pull
    out just the leading year."""
    if not date_str:
        return None
    match = re.match(r"(\d{4})", date_str)
    return int(match.group(1)) if match else None


def _release_year_from_artist_recordings(artist: str, album: str, delay: float) -> YearDiscoveryResult | None:
    """Album-mode fallback: a release this artist has a recording on.

    A various-artists compilation ("TFF Rudolstadt 2011", a festival CD) is
    credited to "Various Artists", so the release-group search above, keyed on
    the artist, never sees it. Searching the artist's recordings on a release
    of that name does, and also proves the artist really is on it - unlike
    accepting any same-named "Various Artists" release."""
    query_str = f'release:{lucene_quote(album)} AND artist:{lucene_quote(artist)}'
    with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
        response = musicbrainzngs.search_recordings(query=query_str, limit=10)

    pairs = [
        (recording, release)
        for recording in response.get("recording-list", [])
        for release in recording.get("release-list", [])
    ]
    # Plain title first, then one that only adds a subtitle - the studio album
    # before its "(live at Cavatina Hall)" edition a year later
    for allow_subtitle in (False, True):
        for recording, release in pairs:
            title = release.get("title", "")
            if not title_resembles(album, title, allow_subtitle=allow_subtitle):
                continue
            year = _extract_year(release.get("date") or release.get("release-group", {}).get("first-release-date"))
            if year:
                time.sleep(delay)
                return YearDiscoveryResult(year=year, matched_title=title,
                                           matched_artist=_credited_name_like(recording, artist))
    return None


def _credited_name_like(recording, artist: str) -> str:
    """The recording's credited name closest to `artist`. The search already
    proved the artist is on it, but maybe only as a guest ("Miuosh x Zespół
    Śląsk feat. Ralph Kamiński") - reporting the first credit (Miuosh) would
    fail the manager's artist check."""
    names = [c["name"] if c.get("name") else c.get("artist", {}).get("name", "")
             for c in recording.get("artist-credit", []) if isinstance(c, dict)]
    names = [n for n in names if n]
    if not names:
        return ""
    return max(names, key=lambda n: similarity(normalize(artist), normalize(n)))


def get_release_year(artist: str, query: str, is_single: bool, delay: float = 1.0) -> YearDiscoveryResult | None:
    """Look up a release year.

    is_single=False: query is an album title - searched as a release group,
    since release groups (not recordings) are what carries an album's
    first-release-date.
    is_single=True: query is a song title - searched as a recording (same
    shape as get_album_name()), reading the date off the chosen release.
    """
    try:
        if is_single:
            query_str = f'recording:{lucene_quote(query)} AND artist:{lucene_quote(artist)}'
            with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
                response = musicbrainzngs.search_recordings(query=query_str, limit=10)
            recordings = response.get("recording-list", [])

            for recording in recordings:
                for release in recording.get("release-list", []):
                    release_group = release.get("release-group", {})
                    secondary_types = release_group.get("secondary-type-list", [])
                    if any(t in ("Compilation", "Live", "Remix", "DJ-mix") for t in secondary_types):
                        continue
                    if is_blacklisted_album(release.get("title", "")):
                        continue
                    year = _extract_year(release.get("date") or release_group.get("first-release-date"))
                    if year:
                        time.sleep(delay)
                        matched_title, matched_artist = _recording_title_artist(recording, artist)
                        return YearDiscoveryResult(year=year, matched_title=matched_title, matched_artist=matched_artist)
            return None

        query_str = f'releasegroup:{lucene_quote(query)} AND artist:{lucene_quote(artist)}'
        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_release_groups(query=query_str, limit=10)
        release_groups = response.get("release-group-list", [])

        # Skip anything that isn't the album we already have stored - a
        # compilation ranked first would otherwise be returned, rejected by the
        # manager's title check, and hide the real album further down. No album
        # blacklist here: the name is already in the DB, so a word like "pop" in
        # it ("SODA POP FANCLUB 4") says nothing. The plain title first, then
        # one that only adds a bracketed subtitle ("Berjozkele (Kołysanki ...)")
        # - so "AM" isn't dated by an "AM (Live)" ranked above it.
        ordered = (
            [g for g in release_groups if title_resembles(query, g.get("title", ""), allow_subtitle=False)]
            + [g for g in release_groups if not title_resembles(query, g.get("title", ""), allow_subtitle=False)
               and title_resembles(query, g.get("title", ""))]
        )
        for release_group in ordered:
            title = release_group.get("title", "")
            year = _extract_year(release_group.get("first-release-date"))
            if not year:
                continue
            time.sleep(delay)
            # The main artist's credited-as name ("Freeland"), not the artist
            # entity's own name ("Adam Freeland") - it's what the DB holds - and
            # not the whole credit phrase, whose guests ("Latto feat. LU KALA")
            # would fail the artist check
            try:
                credit = release_group["artist-credit"][0]
                matched_artist = credit.get("name") or credit["artist"]["name"]
            except (KeyError, IndexError, TypeError, AttributeError):
                matched_artist = ""
            return YearDiscoveryResult(year=year, matched_title=title, matched_artist=matched_artist)

        return _release_year_from_artist_recordings(artist, query, delay)

    except musicbrainzngs.WebServiceError as e:
        flog(f"[MusicBrainz] error for release year of '{artist} - {query}': {e}")
        return None