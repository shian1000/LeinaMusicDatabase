import requests

from utils.common.debug import flog, slog
from utils.common.normalizer import normalize
from utils.common.request_pacing import RequestPacer
from utils.common.text_utils import is_blacklisted_album, is_various_artists_compilation, similarity
from utils.common.version_names import read_version
from utils.database.song_kinds import sure_version
from utils.discoveries.discovery_result import DiscoveryResult, GenreDiscoveryResult
from config.constants import (
    ALBUM_TYPE_EP,
    HTTP_LOOKUP_TIMEOUT,
    ITUNES_GENRE_NAMES,
    ITUNES_SEARCH_COUNTRY,
    ITUNES_SEARCH_LIMIT,
    ITUNES_SEARCH_MIN_INTERVAL,
    ITUNES_SEARCH_URL,
    SINGLES_ALBUM,
    SPELLING_CHECK_THRESHOLD,
)

# Apple's public iTunes Search API: JSON, no key, no browser.
MODULE_NAME = "iTunes fetcher (Curl)"

# Apple appends this to the name of a release that only ever came out as a
# single (no parent album) -- e.g. "Say So (feat. Nicki Minaj) - Single".
SINGLE_TITLE_SUFFIX = " - single"
# ... and this to an EP's ("Lost and Found - EP"), which isn't part of its name
EP_TITLE_SUFFIX = " - ep"

_pacer = RequestPacer(ITUNES_SEARCH_MIN_INTERVAL)

# One store genre per track, broad ("Alternative", "Pop"): the owner reviews it
GENRE_NEVER_SURE = True


def _credits(track_artist: str | None, artist: str) -> bool:
    """Whether iTunes' artist field names ours, letters folded: Apple spells
    "Ørganek" "Organek"."""
    return normalize(artist) in normalize(track_artist or "")


def _find_track(results: list[dict], artist: str, song: str) -> DiscoveryResult | None:
    """The result that fits best, or None. An equally good title on a real
    album beats a single; a "(Live ...)" / remix... version counts only for a
    song that is one.
    No year: iTunes takes no part in Years, and its releaseDate is often a
    reissue's. The disc only on a release of several (songs.disc_number)."""
    song_query = song.lower()
    best = None
    best_rank = None

    for result in results:
        album = result.get("collectionName")
        track_title = result.get("trackName")
        track_artist = result.get("artistName")
        if result.get("kind") != "song" or not (album and track_title and track_artist):
            continue
        if not _credits(track_artist, artist):
            slog(f"Artist mismatch: expected '{artist}', found '{track_artist}'")
            continue

        # Apple bakes "(feat. X)" credits into the title, so compare the part
        # before the parenthetical -- the same convention
        # discover_album_name() uses to clean the query itself.
        track_title_cln = track_title.split("(")[0].strip()
        if similarity(song_query, track_title_cln.lower()) <= SPELLING_CHECK_THRESHOLD:
            continue
        # A live / remix... version's album isn't the song's: Maanam's
        # "Cykady Na Cykladach (Live 1985)" on "Totalski No Problemski"
        if (reading := read_version(track_title)) and sure_version(reading) and not read_version(song):
            continue

        # A single-only release reports no real album -- the same
        # SINGLES_ALBUM sentinel wikipedia_fetcher uses for this case.
        album_type = None
        if album.lower().endswith(SINGLE_TITLE_SUFFIX):
            album = SINGLES_ALBUM
        elif album.lower().endswith(EP_TITLE_SUFFIX):
            album, album_type = album[:-len(EP_TITLE_SUFFIX)].strip(), ALBUM_TYPE_EP
        if is_blacklisted_album(album, artist):
            continue
        if is_various_artists_compilation(album, result.get("collectionArtistName")):
            flog(f"[iTunes] '{album}' is a various-artists compilation, skipping it")
            continue

        rank = (similarity(song_query, track_title.lower()), album != SINGLES_ALBUM)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            several_discs = isinstance(result.get("discCount"), int) and result["discCount"] > 1
            collection_id = result.get("collectionId")
            best = DiscoveryResult(
                album=album, matched_title=track_title_cln, matched_artist=track_artist,
                track_number=result.get("trackNumber"),
                disc_number=result.get("discNumber") if several_discs else None,
                album_type=album_type,
                album_ids={"itunes_collection_id": collection_id} if collection_id else {},
            )

    return best


def _search(artist: str, song: str) -> list[dict]:
    """The songs iTunes finds for "artist song"; raises when it can't be asked."""
    _pacer.wait()
    response = requests.get(
        ITUNES_SEARCH_URL,
        params={
            "term": f"{artist} {song}",
            "entity": "song",
            "country": ITUNES_SEARCH_COUNTRY,
            "limit": ITUNES_SEARCH_LIMIT,
        },
        timeout=HTTP_LOOKUP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("results", [])


def get_album_name(artist: str, song: str) -> DiscoveryResult | None:
    try:
        results = _search(artist, song)
    except requests.RequestException as exc:
        flog(f"[iTunes] Search failed for {artist} - {song}: {exc}")
        return None

    match = _find_track(results, artist, song)
    if not match:
        flog("[iTunes] No matching song in search results")
        return None

    slog(f"Matched: {artist} - {match.matched_title} ({match.album})")
    return match


def get_genre(artist: str, title: str, songs=None) -> GenreDiscoveryResult | None:
    """The store genre of the track titled most like the song (its version
    only for a song that is one), read through ITUNES_GENRE_NAMES. Raises
    when iTunes can't be asked."""
    best, best_score = None, SPELLING_CHECK_THRESHOLD
    for result in _search(artist, title):
        track_title = (result.get("trackName") or "").split("(")[0].strip()
        if result.get("kind") != "song" or not (track_title and result.get("primaryGenreName")):
            continue
        if not _credits(result.get("artistName"), artist):
            continue
        if (reading := read_version(result["trackName"])) and sure_version(reading) and not read_version(title):
            continue
        score = similarity(title.lower(), track_title.lower())
        if score > best_score:
            best, best_score = result, score
    if best is None:
        flog(f"[iTunes] no track with a genre for '{artist} - {title}'")
        return None
    genre = best["primaryGenreName"]
    names = ITUNES_GENRE_NAMES.get(genre, genre.replace("/", ","))
    return GenreDiscoveryResult([name.strip() for name in names.split(",")],
                                matched_title=best["trackName"].split("(")[0].strip(),
                                matched_artist=best.get("artistName"), source_ref=best.get("trackViewUrl"),
                                never_sure=True)
