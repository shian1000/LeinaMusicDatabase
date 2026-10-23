"""
Looking up a song's genres on MusicBrainz, for Fill missing data -> Genres
(utils/ui/genre_review.py). Stores nothing; the values come back already in
the database's spelling (utils/database/genres.py).

MusicBrainz has genres on recordings, release groups and artists. Most
recordings have none, so the most specific level that has any wins
(GenreResult.level):
1. song - the genre tags of the recordings with the song's title by its
   artist, taken straight from the search results (a search result carries
   all its tags; only those MusicBrainz lists as genres count). Live
   recordings and videos only when there's nothing else.
2. album - the release group of the song's album (else its single, else its
   first album), one lookup, cached per run.
3. artist - the artist's genres, one lookup, cached per run (also found by
   an artist search when no recording matches).
Each level's votes are summed per genre; the most voted come first, at most
GENRE_MAX_PER_SONG, only those with GENRE_MIN_VOTE_SHARE of the top's votes.
"""

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from config.constants import (
    GENRE_LEVEL_ALBUM,
    GENRE_LEVEL_ARTIST,
    GENRE_LEVEL_SONG,
    GENRE_LOOKUP_MISSES_FILE,
    GENRE_MAX_PER_SONG,
    GENRE_MIN_VOTE_SHARE,
    MUSICBRAINZ_API_LIMIT,
    MUSICBRAINZ_WS_ROOT,
    SINGLES_ALBUM,
)
from utils.common.debug import flog, slog
from utils.common.musicbrainz_client import lucene_quote, mb_get
from utils.common.normalizer import name_key, normalize
from utils.database.genres import canonical_genre, genre_key, is_known_genre
from utils.database.latin_names import artist_match_synonyms, song_titles



@dataclass
class GenreResult:
    genres: str  # as stored: "synth-pop, new wave"
    level: str  # GENRE_LEVEL_SONG / _ALBUM / _ARTIST
    source_url: str


class GenreLookup:
    """One per run: caches release groups' and artists' genres, so the songs
    of one album or artist cost one request between them."""

    def __init__(self, known: dict | None = None):
        self.known = known or {}  # genres.known_genre_spellings()
        self._release_groups: dict = {}
        self._artists: dict = {}
        self._artist_searches: dict = {}

    # ---------- the levels ----------

    def lookup(self, song) -> GenreResult | None:
        names = _artist_names(song)
        # A well-known song's results are mostly live recordings and
        # compilations, so its album (when known) is searched for first
        album = song.album if song.album and song.album != SINGLES_ALBUM else None
        recordings = []
        for title in song_titles(song):
            for release in ([album, None] if album else [None]):
                recordings = [r for r in self._search_recordings(title, song.artist.name, release)
                              if _title_matches(r, song) and _credited_artist(r, names)]
                if recordings:
                    break
            if recordings:
                break
        studio = [r for r in recordings if not _is_live(r)] or recordings

        votes = Counter()
        for recording in studio:
            for tag in recording.get("tags") or []:
                if is_known_genre(tag.get("name") or ""):
                    votes[tag["name"]] += max(int(tag.get("count") or 0), 1)
        genres = self.pick(votes)
        if genres:
            return GenreResult(genres, GENRE_LEVEL_SONG, f"https://musicbrainz.org/recording/{studio[0]['id']}")

        release_group = _pick_release_group(studio, song)
        if release_group:
            genres = self.pick(self._entity_genres("release-group", release_group, self._release_groups))
            if genres:
                return GenreResult(genres, GENRE_LEVEL_ALBUM, f"https://musicbrainz.org/release-group/{release_group}")

        artist_id = next((a for a in (_credited_artist(r, names) for r in studio) if a), None) \
            or self._search_artist(song.artist.name, names)
        if artist_id:
            genres = self.pick(self._entity_genres("artist", artist_id, self._artists))
            if genres:
                return GenreResult(genres, GENRE_LEVEL_ARTIST, f"https://musicbrainz.org/artist/{artist_id}")

        flog(f"[Genres] nothing on MusicBrainz for '{song.artist.name} - {song.title}' "
             f"({len(recordings)} matching recording(s))")
        return None

    def pick(self, votes: Counter) -> str | None:
        """The most voted genres in the database's spelling."""
        summed = Counter()
        spelling = {}
        for name, count in votes.items():
            genre = canonical_genre(name, self.known)
            summed[genre_key(genre)] += count
            spelling.setdefault(genre_key(genre), genre)
        if not summed:
            return None
        ranked = summed.most_common()
        floor = ranked[0][1] * GENRE_MIN_VOTE_SHARE
        picked = [spelling[key] for key, count in ranked if count >= floor][:GENRE_MAX_PER_SONG]
        return ", ".join(picked)

    # ---------- requests ----------

    def _search_recordings(self, title: str, artist: str, release: str | None = None) -> list:
        query = f'recording:{lucene_quote(title)} AND artist:{lucene_quote(artist)} AND video:false'
        if release:
            query += f' AND release:{lucene_quote(release)}'
        data = _get_json(f"{MUSICBRAINZ_WS_ROOT}recording/", {"query": query, "limit": MUSICBRAINZ_API_LIMIT})
        return (data or {}).get("recordings") or []

    def _search_artist(self, name: str, names: set) -> str | None:
        key = name_key(name)
        if key not in self._artist_searches:
            data = _get_json(f"{MUSICBRAINZ_WS_ROOT}artist/", {"query": f'artist:{lucene_quote(name)}', "limit": 5})
            found = [a for a in (data or {}).get("artists") or []
                     if name_key(a.get("name")) in names
                     or any(name_key(alias.get("name")) in names for alias in a.get("aliases") or [])]
            # Two artists of that name: can't tell which one it is
            self._artist_searches[key] = found[0]["id"] if len(found) == 1 else None
        return self._artist_searches[key]

    def _entity_genres(self, entity: str, mbid: str, cache: dict) -> Counter:
        if mbid not in cache:
            data = _get_json(f"{MUSICBRAINZ_WS_ROOT}{entity}/{mbid}", {"inc": "genres"})
            cache[mbid] = Counter({g["name"]: max(int(g.get("count") or 0), 1)
                                   for g in (data or {}).get("genres") or [] if g.get("name")})
        return cache[mbid]


def _get_json(url: str, params: dict) -> dict | None:
    response = mb_get(url, {**params, "fmt": "json"})
    if response is None:
        flog(f"[Genres] MusicBrainz didn't answer {url} {params}")
        return None
    try:
        return response.json()
    except ValueError:
        slog(f"[Genres] not JSON from {url}")
        return None


def _artist_names(song) -> set:
    names = {name_key(song.artist.name)}
    names.update(name_key(s) for s in (artist_match_synonyms(song.artist) or "").split(",") if s.strip())
    return names - {""}


def _title_matches(recording: dict, song) -> bool:
    return normalize(recording.get("title")) in {normalize(t) for t in song_titles(song)}


def _credited_artist(recording: dict, names: set) -> str | None:
    """The MusicBrainz id of the song's artist among the recording's credits."""
    for credit in recording.get("artist-credit") or []:
        artist = credit.get("artist") or {}
        if name_key(artist.get("name")) in names or name_key(credit.get("name")) in names:
            return artist.get("id")
    return None


def _is_live(recording: dict) -> bool:
    return bool(recording.get("video")) or "live" in (recording.get("disambiguation") or "").lower()


def _pick_release_group(recordings: list, song) -> str | None:
    """The song's album among the recordings' release groups, else its
    single, else the first studio album."""
    groups = []
    for recording in recordings:
        for release in recording.get("releases") or []:
            group = release.get("release-group") or {}
            if group.get("id") and not group.get("secondary-types"):
                groups.append((group, release.get("title")))
    album = normalize(song.album) if song.album and song.album != SINGLES_ALBUM else None
    titles = {normalize(t) for t in song_titles(song)}
    for wanted in (
        lambda g, t: album is not None and album in (normalize(g.get("title")), normalize(t)),
        lambda g, t: g.get("primary-type") == "Single" and normalize(g.get("title") or t) in titles,
        lambda g, t: g.get("primary-type") == "Album",
    ):
        for group, release_title in groups:
            if wanted(group, release_title):
                return group["id"]
    return None


# ==================== songs MusicBrainz had nothing for ====================

def _misses_path() -> Path:
    return GENRE_LOOKUP_MISSES_FILE


def load_misses() -> set:
    try:
        return set(json.loads(_misses_path().read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def save_misses(song_ids: set) -> None:
    path = _misses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(song_ids)))
