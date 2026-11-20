import socket
import time
from collections import defaultdict
from contextlib import contextmanager

import musicbrainzngs
from utils.common.debug import flog, slog
from utils.common.text_utils import is_blacklisted_album, title_resembles, similarity
from utils.common.normalizer import normalize
from utils.discoveries.discovery_result import AlbumTrack, DiscoveryResult, LanguageDiscoveryResult, YearDiscoveryResult
import re
from utils.common.text_utils import check_spelling
from config.constants import (
    ALBUM_TYPE_ALBUM,
    ALBUM_TYPE_EP,
    ALBUM_TYPE_SOUNDTRACK,
    MIN_PLAUSIBLE_RELEASE_YEAR,
    MUSICBRAINZ_REISSUE_AFTER_YEARS,
    MUSICBRAINZ_VARIOUS_ARTISTS_ID,
    MUSICBRAINZ_WS_ROOT,
)
from utils.common.language_names import clean_language, code_name
from utils.common.musicbrainz_client import lucene_quote, mb_get
from utils.common.version_names import read_version
from utils.database.song_kinds import sure_version

# Set up the user agent (required by MusicBrainz). Contact points at the project
# repo; see MUSICBRAINZ_API_USER_AGENT in config/constants.py.
musicbrainzngs.set_useragent("MusicDatabase", "1.0", "https://github.com/shian1000/LeinaMusicDatabase")

MODULE_NAME = "MusicBrainz fetcher (Curl)"

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


def _pick_release(recordings, artist: str | None = None, title: str | None = None) -> tuple[str | None, dict | None]:
    """(release title, its recording) for the best release of the search hits.

    The earliest Album wins (its release group's earliest year among the
    hits, result order on a tie), of a recording titled like
    `title` before one titled otherwise, the artist's own before a "Various
    Artists" one (a soundtrack). Otherwise the best-ranked other release of
    the first recording that has any, by type then earliest date (undated
    last). Compilations, live albums, remixes, blacklisted titles, another
    artist's release and another song's single never count; a reissue
    (_is_reissue()) only when nothing else is left.
    """
    release_title, recording, _release = _pick_release_with_entry(recordings, artist, title)
    return release_title, recording


def _pick_release_with_entry(recordings, artist: str | None = None,
                             title: str | None = None) -> tuple[str | None, dict | None, dict | None]:
    """_pick_release() plus the picked release entry itself (its id and the
    recording's place on it)."""
    first_appearances = _first_appearances(recordings)

    def counts(release, recording, reissues: bool) -> bool:
        return (_release_counts(release, recording, artist)
                and _is_reissue(release, recording, recordings, first_appearances) == reissues)

    # The earliest, not the first listed: Tajči's "Hajde da ludujemo" (1990)
    # was listed after "Bube u glavi" (1991), Josipa Lisac's "O jednoj
    # mladosti" (1973) after a 2018 studio session. A recording titled
    # otherwise ("Lipe cvatu, sve je isto k'o i lani") goes last, since the
    # manager drops its album anyway.
    def album_rank(pair):
        recording, release = pair
        return not _is_queried_song(recording, title), _release_group_year(recordings, release) or 9999

    # A reissue only when nothing else is left: Czesław Niemen's "Dziwny jest
    # ten świat" (1967) is among the hits only as its 1991 edition
    for reissues in (False, True):
        # The artist's own album before a "Various Artists" one: Daan's "Housewife"
        # lists the film soundtrack "Windkracht 10" ahead of "Victory"
        for various_artists in (False, True):
            albums = [(recording, release) for recording in recordings for release in recording.get("release-list", [])
                      if counts(release, recording, reissues)
                      and release.get("release-group", {}).get("primary-type", "") == "Album"
                      and _is_various_artists_release(release) == various_artists]
            if albums:
                recording, release = min(albums, key=album_rank)
                return _release_title(release), recording, release

        for recording in recordings:
            candidates = [release for release in recording.get("release-list", [])
                          if counts(release, recording, reissues) and not _is_other_songs_single(release, recording)]
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


def _release_group_year(recordings, release) -> int | None:
    """The earliest year of the search hits' releases of `release`'s release
    group - the picked one may be a later edition (a remaster). The search
    answer doesn't carry the group's own first-release-date."""
    group_id = release.get("release-group", {}).get("id")
    years = [_extract_year(other.get("date")) for recording in recordings
             for other in recording.get("release-list", [])
             if group_id and other.get("release-group", {}).get("id") == group_id]
    years = [year for year in years if year]
    return min(years) if years else _extract_year(release.get("date"))


def _album_type(release) -> str | None:
    """albums.type of a release, from its release group: only an album, an
    EP or a soundtrack (see constants.DISCOVERY_ALBUM_TYPES)."""
    release_group = release.get("release-group", {})
    if "Soundtrack" in release_group.get("secondary-type-list", []):
        return ALBUM_TYPE_SOUNDTRACK
    return {"Album": ALBUM_TYPE_ALBUM, "EP": ALBUM_TYPE_EP}.get(release_group.get("primary-type", ""))


def _album_result(title: str, recording, release, artist: str, recordings) -> DiscoveryResult:
    matched_title, matched_artist = _recording_title_artist(recording, artist)
    track_number, disc_number = _track_position(release)
    group_id = release.get("release-group", {}).get("id")
    # A reissue's year isn't the song's; Years then asks for the album's own.
    # Unsure is enough here: Czesław Niemen's "Dziwny jest ten świat" (1967)
    # is among the hits only as its 1991 edition, after a lone 1979 best-of
    reissue = _is_reissue(release, recording, recordings, _first_appearances(recordings, lone_compilations=True))
    return DiscoveryResult(album=title, matched_title=matched_title, matched_artist=matched_artist,
                           track_number=track_number, disc_number=disc_number,
                           load_tracks=_tracks_loader(release),
                           year=None if reissue else _release_group_year(recordings, release),
                           album_type=_album_type(release),
                           album_ids={"musicbrainz_release_group_id": group_id} if group_id else {})


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
    if not release_ids or _is_various_artists_release(release):
        return False
    return not release_ids & _artist_ids(recording.get("artist-credit"))


def _is_various_artists_release(release) -> bool:
    return MUSICBRAINZ_VARIOUS_ARTISTS_ID in _artist_ids(release.get("artist-credit"))


def _is_other_songs_single(release, recording) -> bool:
    """A single named after a different song, with this one as its extra track:
    "Death by Rock and Roll" is also on the single "25". Not this song's album.
    An EP has its own name, so only singles are checked."""
    if release.get("release-group", {}).get("primary-type", "") != "Single":
        return False
    return not title_resembles(recording.get("title", ""), _release_title(release))


def _release_year(release) -> int | None:
    return _extract_year(release.get("date") or release.get("release-group", {}).get("first-release-date"))


def _first_appearances(recordings, lone_compilations: bool = False) -> dict:
    """normalize(recording title) -> the earliest year a hit of that title is
    on any release at all (a compilation, someone else's): the song was out
    by then. Without `lone_compilations`, not a lone compilation more than
    MUSICBRAINZ_REISSUE_AFTER_YEARS before every other release, which
    MusicBrainz now and then dates wrong: Dido's box "Life for Rent / No
    Angel" is listed 1999, "White Flag" came out in 2003. A lone earlier EP
    or album always counts (Denis & Denis's 1988 EP)."""
    dated = defaultdict(list)
    for recording in recordings:
        for release in recording.get("release-list", []):
            year = _release_year(release)
            if year and year >= MIN_PLAUSIBLE_RELEASE_YEAR:
                compilation = "Compilation" in release.get("release-group", {}).get("secondary-type-list", [])
                dated[normalize(recording.get("title", ""))].append((year, compilation))
    first = {}
    for key, releases in dated.items():
        releases.sort()
        (year, compilation), later = releases[0], releases[1:]
        if not lone_compilations and compilation and later and later[0][0] > year + MUSICBRAINZ_REISSUE_AFTER_YEARS:
            year = later[0][0]
        first[key] = year
    return first


def _is_reissue(release, recording, recordings, first_appearances) -> bool:
    """Did `release` come out more than MUSICBRAINZ_REISSUE_AFTER_YEARS after
    the recording first appeared anywhere? Then it's a reissue or a best-of
    nobody typed Compilation ("Program tvog kompjutera", 1995, for Denis &
    Denis - "Soba 23", on an EP of 1988), not where the song came out - or
    an album MusicBrainz lists only by a later edition. An undated one counts
    too once the song is known from dated ones: it may be anything (Maryla
    Rodowicz's undated "Wola 2", a re-recording, for "Sing Sing" of 1976).
    So does a "Various Artists" album (a soundtrack) of any later year: it
    collected a song already out ("Will & Grace: Let the Music Out!", 2004,
    for Dido's "White Flag" of 2003)."""
    first = first_appearances.get(normalize(recording.get("title", "")))
    if not first:
        return False
    year = _release_group_year(recordings, release)
    margin = 0 if _is_various_artists_release(release) else MUSICBRAINZ_REISSUE_AFTER_YEARS
    return not year or year > first + margin


def _is_queried_song(recording, title: str | None) -> bool:
    """Is the recording `title` itself - not a version of it ("Losing Grip
    (Demo)" is on a 2001 "Let Go: Demo Tracks", a year before "Let Go"), by
    its title or by its disambiguation ("live, 2003-02: Cynthia Woods Mitchell
    Pavilion" - 3 Doors Down's "Here Without You" on a rarities edition dated
    as the 2000 album it extends)?"""
    if title is None:
        return True
    recording_title = recording.get("title", "")
    if not read_version(title):
        for named in (recording_title, f"{recording_title} ({recording.get('disambiguation') or ''})"):
            if (reading := read_version(named)) and sure_version(reading):
                return False
    return title_resembles(title, recording_title, allow_subtitle=False)


def _release_counts(release, recording, artist: str | None) -> bool:
    """Can `release` be the song's album or give its year? Not a compilation
    or a live one, not someone else's."""
    return _release_is_eligible(release, artist) and not _is_other_artists_release(release, recording)


# Release kinds that are never a song's own album or release
_SKIPPED_SECONDARY_TYPES = ("Compilation", "Live", "Remix", "DJ-mix")


def _release_is_eligible(release, artist: str | None = None) -> bool:
    # Nobody's release: a concert recorded by a fan, Dido's "Life for Rent" pressed again
    if release.get("status") == "Bootleg":
        return False
    release_group = release.get("release-group", {})
    # An entry nobody typed says nothing about being an album, and is often an
    # untagged compilation ("Radio Woodstock 100.1 WDST 25th Anniversary - Disc 2"
    # as the album of "Breakfast at Tiffanys")
    if not release_group.get("primary-type"):
        return False
    secondary_types = release_group.get("secondary-type-list", [])
    if any(t in _SKIPPED_SECONDARY_TYPES for t in secondary_types):
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
    """The album of the first 5 recording hits, then of the first 25 unless
    the 5 gave a studio album that isn't a reissue: a later hit's album can
    still beat an early hit's single or reissue (Maryla Rodowicz's "Sing-Sing",
    1976, is the 22nd hit; the first 5 had only the undated "Wola 2")."""
    query = f'recording:{lucene_quote(song)} AND artist:{lucene_quote(artist)}'
    slog(query, priority=1)
    found = None
    for limit in (5, 25):
        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_recordings(query=query, limit=limit)
        recordings = response.get("recording-list", [])
        result, result_recording, result_release = _pick_release_with_entry(recordings, artist, song)
        slog(result)
        if result:
            found = _album_result(result, result_recording, result_release, artist, recordings)
            if result_release.get("release-group", {}).get("primary-type") == "Album" and not _is_reissue(
                    result_release, result_recording, recordings, _first_appearances(recordings)):
                return found
        if len(recordings) < limit:  # that was every hit
            break
    return found


def first_appearance_year(artist: str, title: str, raise_errors: bool = False) -> int | None:
    """The earliest year a recording of `title` by `artist` is on any release
    MusicBrainz knows (a compilation, someone else's), None when it knows
    none dated. The manager holds other modules' years against it
    (discoveries_manager.year_after_the_song()), Fetch database data ->
    "Check stored years" the stored ones (raise_errors: a failed request
    raises, so the song isn't taken as unknown). One request."""
    query = f'recording:{lucene_quote(title)} AND artist:{lucene_quote(artist)}'
    try:
        with _bounded_socket_timeout(MUSICBRAINZ_SEARCH_TIMEOUT):
            response = musicbrainzngs.search_recordings(query=query, limit=25)
    except musicbrainzngs.WebServiceError as e:
        flog(f"[MusicBrainz] error for the first release of '{artist} - {title}': {e}")
        if raise_errors:
            raise
        return None
    recordings = [r for r in response.get("recording-list", []) if _is_queried_song(r, title)]
    return min(_first_appearances(recordings).values(), default=None)


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
            year = _release_year(release)
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
            first_appearances = _first_appearances(recordings)

            # The same releases as for the album (get_album_name()), never a
            # reissue's year, the earliest of a recording titled like the song
            # first - not the first listed: Josipa Lisac's "O jednoj mladosti"
            # (1973) got a 2018 studio session's year
            dated = [(not _is_queried_song(recording, query), year, recording)
                     for recording in recordings for release in recording.get("release-list", [])
                     if _release_counts(release, recording, artist)
                     and not _is_reissue(release, recording, recordings, first_appearances)
                     and (year := _release_year(release))]
            if not dated:
                return None
            _, year, recording = min(dated, key=lambda entry: entry[:2])
            time.sleep(delay)
            matched_title, matched_artist = _recording_title_artist(recording, artist)
            return YearDiscoveryResult(year=year, matched_title=matched_title, matched_artist=matched_artist)

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


# ==================== languages ====================
# A recording's work (inc=work-rels) holds the language of its words ("zxx" =
# none). Not every recording is linked to its work, so up to this many of the
# ones titled like the song are looked up - through mb_get(), not
# musicbrainzngs (rate limit, timeout).
_LANGUAGE_RECORDINGS = 3


def _mb_json(url: str, params: dict) -> dict:
    """Raises when MusicBrainz can't be asked, so the song is asked again next time."""
    response = mb_get(url, {**params, "fmt": "json"})
    if response is None:
        raise ConnectionError(f"MusicBrainz didn't answer {url}")
    return response.json()


def _credit(recording: dict) -> str:
    return "".join(f"{c.get('name', '')}{c.get('joinphrase', '')}" for c in recording.get("artist-credit") or [])


def get_language(artist: str, title: str, songs=None) -> LanguageDiscoveryResult | None:
    query = f'recording:{lucene_quote(title)} AND artist:{lucene_quote(artist)} AND video:false'
    recordings = _mb_json(f"{MUSICBRAINZ_WS_ROOT}recording/", {"query": query, "limit": 10}).get("recordings") or []
    titled = [r for r in recordings if title_resembles(title, r.get("title") or "")][:_LANGUAGE_RECORDINGS]
    for recording in titled:
        data = _mb_json(f"{MUSICBRAINZ_WS_ROOT}recording/{recording['id']}", {"inc": "work-rels"})
        codes = [code for relation in data.get("relations") or []
                 for code in (relation.get("work") or {}).get("languages") or []]
        names = [name for name in (code_name(code) for code in dict.fromkeys(codes)) if name]
        if names:
            return LanguageDiscoveryResult(clean_language(" / ".join(names)), matched_title=recording.get("title"),
                                           matched_artist=_credit(recording), source_ref=recording["id"])
    flog(f"[{MODULE_NAME}] no work with a language for '{artist} - {title}'")
    return None
