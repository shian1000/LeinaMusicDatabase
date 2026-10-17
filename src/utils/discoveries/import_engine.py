import re
import time

from utils.database.datatables import Song, Artist
from utils.database.database_sessions import open_and_set_global_database_sessions

from utils.common.debug import slog, mlog
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
from utils.database.datatables import artist_categories
from utils.database.database_management import add_db_entry
from utils.common.text_utils import normalize, check_spelling, similarity, are_artists_entries_similar, scaled_similarity_threshold, remove_brackets, strip_trailing_junk_suffix, strip_track_number_prefix, similarity_at_least, strip_trailing_feat
from utils.database.artist_splitting import propose_artist_split
from utils.database.latin_names import artist_match_synonyms, song_titles
from utils.database.song_artists import song_all_artists
from utils.database.song_kinds import (
    is_full_album,
    set_song_kind,
    songs_for_entry,
    strip_full_album_marker,
    title_has_full_album_marker,
)
from config.constants import SONG_KIND_FULL_ALBUM
from config.constants import NAMELESS_AUTO_ACCEPT_MIN_CHARS, NAMELESS_AUTO_ACCEPT_MIN_WORDS, FILE_MATCH_SCORE_TITLE_WEIGHT, FILE_TITLE_PREFIX_MIN_LENGTH, FILE_TITLE_SIMILARITY_THRESHOLD, SPELLING_CHECK_THRESHOLD, TITLE_AMPERSAND_WORDS
from utils.common import spellcheck_cache
from utils.common.normalizer import name_key
from utils.common.musicbrainz_client import MBStats
from utils.ui.artist_split_review import review_artist_splits
from utils.ui.album_resolution_review import offer_album_resolution
from rich import print
import questionary
from utils.ui.prompts import ask_yes_no
from sqlalchemy import func

# OPTIMIZATION: Cache for spell check results to avoid repeated API calls
# Key: (artist, title) tuple, Value: spell_check_result dict
_SPELL_CHECK_CACHE = {}

# Sentinel stored in artist_cache while an ambiguous artist match is waiting
# for the user to review it after the batch, so repeat occurrences of the
# same artist name reuse the pending state instead of re-querying MusicBrainz
# and queueing a duplicate question.
_PENDING_ARTIST = object()

def find_similar_artist(metadata: dict) -> list:
    """
    Look up whether MusicBrainz's corrected spelling for this artist matches
    one or more artists already in the database.

    Pure lookup - does NOT ask the user. Returns the list of matching Artist
    rows (possibly empty); the caller queues the conflict and lets the user
    resolve it once the whole import batch is done, instead of interrupting
    the import for every ambiguous artist name.
    """
    new_artist_name = metadata["artist_name"]
    slog(metadata)

    slog(f"Couldn't find artist {new_artist_name}, trying spellcheck")

    # OPTIMIZATION: Use title for spell check context
    spell_check_cache_key = (new_artist_name, metadata.get("title", ""))

    if spell_check_cache_key in _SPELL_CHECK_CACHE:
        spell_check_result = _SPELL_CHECK_CACHE[spell_check_cache_key]
        slog(f"[CACHE HIT] Artist spell check cached", priority=1)
    else:
        spell_check_result = check_spelling(new_artist_name, metadata["title"])
        _SPELL_CHECK_CACHE[spell_check_cache_key] = spell_check_result
        slog(f"[API CALL] Artist spell check performed and cached", priority=1)

    slog(new_artist_name)
    slog(metadata["title"])
    slog(spell_check_result)

    if not spell_check_result.get("found"):
        slog(f"No MusicBrainz match for {new_artist_name}, skipping spellcheck correction", priority=1)
        return []

    corrected_spelling = spell_check_result["corrected_artist"]
    slog(corrected_spelling)

    similarity_percent = similarity(new_artist_name, corrected_spelling)
    slog(new_artist_name)
    slog(corrected_spelling)
    slog(similarity_percent)

    if not (similarity_percent > scaled_similarity_threshold(new_artist_name, corrected_spelling, SPELLING_CHECK_THRESHOLD)):
        return []

    existing_artists = get_artists_from_db_session(artist_categories[0], corrected_spelling)
    if not existing_artists:
        return []

    similar_artists = []
    for art in existing_artists:
        if are_artists_entries_similar(art, corrected_spelling):
            slog(art)
            slog(art.name)
            slog(art.id)
            similar_artists.append(art)
        else:
            slog(f"{art} is not similar to {corrected_spelling}, skipping", priority=1)

    return similar_artists

def find_similar_song(metadata: dict, artist_obj: Artist) -> Song:
    """
    Check if a similar song already exists for the artist and return it.

    OPTIMIZATION: Check local database FIRST before expensive API call.
    Only call check_spelling() if no similar songs found locally.

    Returns the matching Song if one is found, otherwise None. Does NOT ask
    the user - callers are expected to queue the conflict and let the user
    resolve it once the whole import batch is done, instead of interrupting
    the import for every match.
    """
    new_artist_name = metadata["artist_name"]
    new_title = metadata["title"]
    slog(f"Couldn't find song {new_artist_name} - {new_title}, trying spellcheck")

    # OPTIMIZATION STEP 1: Check local database for similar songs FIRST (instant)
    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)

    # Try to find similar songs using local similarity comparison (no API call).
    # Bracketed annotations like "(prod. X)" or "(feat. Y)" are stripped first
    # so two different songs that share a producer/feature credit don't score
    # as similar just because of that shared suffix (e.g. "Adieu (prod. Rumak)"
    # vs "Nostalgia (prod. Rumak)").
    new_title_core = remove_brackets(new_title)
    for ex_son in existing_artists_songs:
        sim_percent = similarity(new_title_core, remove_brackets(ex_son.title))

        if sim_percent > SPELLING_CHECK_THRESHOLD:
            slog(f"      [LOCAL MATCH] Similar song found, deferring decision to caller", priority=1)
            return ex_son

    slog(f"      [NO LOCAL MATCH] No similar songs found in database, trying API spell check", priority=1)

    # OPTIMIZATION STEP 2: Only call expensive API if no local match found
    cache_key = (new_artist_name, new_title)
    if cache_key in _SPELL_CHECK_CACHE:
        spell_check_result = _SPELL_CHECK_CACHE[cache_key]
        slog(f"      [CACHE HIT] Spell check result retrieved from cache", priority=1)
    else:
        spell_check_result = check_spelling(new_artist_name, new_title)
        _SPELL_CHECK_CACHE[cache_key] = spell_check_result
        slog(f"      [API CALL] Spell check performed and cached", priority=1)

    slog(spell_check_result)

    if not spell_check_result.get("found"):
        slog("      [NO MB MATCH] Spell check found no match, treating as new song", priority=1)
        return None

    corrected_spelling = spell_check_result["corrected_title"]
    slog(corrected_spelling)

    similarity_percent = similarity(new_title, corrected_spelling)
    slog(similarity_percent)

    if(similarity_percent > SPELLING_CHECK_THRESHOLD):
        # Search DB again with corrected spelling
        for ex_son in existing_artists_songs:
            if normalize(corrected_spelling) in normalize(ex_son.title):
                slog(f"      [SPELLCHECK MATCH] Similar song found, deferring decision to caller", priority=1)
                return ex_son

    return None

def find_matching_artist(name: str) -> Artist | None:
    """
    Look up whether `name` matches an artist already in the database - exact
    match (case-insensitive) first, falling back to local fuzzy similarity
    against candidates of a comparable name length.

    Pure read-only lookup - never calls out to MusicBrainz and never creates
    anything. Shared by resolve_artist() (deciding whether a parsed artist
    name already exists) and the YouTube playlist importer's artist/title
    swap detection (checking whether a parsed *title* is actually an
    existing artist's name).
    """
    artist, _ = _match_artist_locally(name)
    return artist


def _match_artist_locally(name: str) -> tuple:
    """Does the exact/fuzzy local lookup for find_matching_artist(), also
    returning whether the broad (pre-similarity-filter) search found any
    candidates at all.

    resolve_artist() needs that second bit to preserve its original
    behavior: it only falls back to a MusicBrainz spelling-correction lookup
    when there were truly zero local candidates for the name, not when there
    were candidates that just didn't pass the similarity threshold.
    """
    if not name:
        return None, False

    music_session, _ = open_and_set_global_database_sessions()
    candidates = music_session.query(Artist).filter(
        func.lower(Artist.name) == name.lower()
    ).all()
    if candidates:
        return candidates[0], True
    # The owner's Latin form of a name in another alphabet: "Kino" is "Кино"
    candidates = music_session.query(Artist).filter(
        func.lower(Artist.name_latin) == name.lower()
    ).order_by(Artist.id).all()
    if candidates:
        return candidates[0], True

    # The search finds artists by their synonyms too ("I nad kropką" is
    # Konkret's), so they're compared like the name.
    def known_names(artist):
        synonyms = (artist_match_synonyms(artist) or "").split(",")
        return [n for n in (artist.name, *(s.strip() for s in synonyms)) if n]

    search = normalize(name)
    key = name_key(name)
    existing_artists = get_artists_from_db_session(artist_categories[0], search)
    if not existing_artists and search.startswith("the "):
        # The search is substring-based, so "The Caesars" never finds
        # "Caesars": only the same name without its "The" is taken then
        same = [a for a in get_artists_from_db_session(artist_categories[0], search[4:])
                if any(name_key(n) == key for n in known_names(a))]
        return (same[0], True) if same else (None, False)
    if not existing_artists:
        return None, False

    # "Caesars" is "The Caesars" (similarity rates the pair too low)
    same = [a for a in existing_artists if any(name_key(n) == key for n in known_names(a))]
    if same:
        return same[0], True

    # Pre-filter: only check artists that are somewhat similar in length -
    # avoids expensive similarity calculations on obviously different names.

    name_length = len(name)
    filtered_candidates = [
        a for a in existing_artists
        if any(abs(len(n) - name_length) <= max(name_length * 0.5, 3) for n in known_names(a))  # Allow 50% length variance
    ]
    filtered_candidates = (filtered_candidates or existing_artists)[:50]  # Cap at 50 candidates

    for similar_artist in filtered_candidates:
        for known in known_names(similar_artist):
            if similarity(name, known) > scaled_similarity_threshold(name, known, SPELLING_CHECK_THRESHOLD):
                return similar_artist, True

    return None, True


def resolve_artist(metadata: dict, artist_cache: dict = None, pending_conflicts: list = None) -> tuple:
    """
    Resolve or create an artist entry with optimized lookup.

    Uses a cache to avoid redundant database queries and similarity calculations.
    Implements smart filtering to stop early once a good match is found.

    Args:
        metadata: Metadata dict with 'artist_name' and 'origin'
        artist_cache: Optional dict to cache lookups {normalized_name: artist_obj}
        pending_conflicts: list to queue ambiguous-artist conflicts onto instead
            of asking immediately (see resolve_song for the same pattern)

    Returns:
        (artist_obj, is_pending):
        - (artist, False): artist resolved (existing or newly created).
        - (None, True): an ambiguous MusicBrainz-corrected match was found.
          The conflict was appended to pending_conflicts and artist_cache
          remembers the pending state (_PENDING_ARTIST), so the caller should
          defer this file (and its song) until the batch is reviewed.
    """
    if artist_cache is None:
        artist_cache = {}
    if pending_conflicts is None:
        pending_conflicts = []

    new_artist_name = metadata["artist_name"]
    new_artist_origin = metadata.get("origin")
    normalized_name = normalize(new_artist_name)

    # Check cache first - this is the fastest path for repeated artists
    if normalized_name in artist_cache:
        cached_value = artist_cache[normalized_name]
        if cached_value is _PENDING_ARTIST:
            slog(f"    [CACHE HIT] Artist resolution already deferred", priority=1)
            return None, True
        slog(f"    [CACHE HIT] Artist found in cache", priority=1)
        return cached_value, False

    # Optimization 1/2/3: exact match, then local fuzzy match against
    # length-similar candidates (see _match_artist_locally()).
    existing_artist, had_local_candidates = _match_artist_locally(new_artist_name)

    if existing_artist:
        print(f"Found existing artist match for [blue]{existing_artist.name}[/blue]")
    elif not had_local_candidates:
        # No local candidates at all - try spell check
        similar_artists = find_similar_artist(metadata)

        if similar_artists:
            print(f"Found similar artist [blue]{similar_artists[0].name}[/blue] but you were trying to add [green]{new_artist_name}[/green] - review deferred until the import finishes")
            slog(f"    [DEFERRED] Similar artist conflict queued for later resolution", priority=1)
            pending_conflicts.append({
                "metadata": metadata,
                "normalized_name": normalized_name,
                "candidate_artists": similar_artists,
            })
            artist_cache[normalized_name] = _PENDING_ARTIST
            return None, True

    new_artist_obj = None
    if not existing_artist:
        new_artist_obj = Artist()
        new_artist_obj.name = new_artist_name
        if new_artist_origin:
            new_artist_obj.origin = new_artist_origin
        add_db_entry(new_artist_obj)
    else:
        new_artist_obj = existing_artist
        print(f"Found existing artist ({new_artist_obj.name})")

    # Cache the result for future lookups in this import batch
    artist_cache[normalized_name] = new_artist_obj

    return new_artist_obj, False

def create_song_entry(metadata: dict, artist_obj: Artist) -> Song:
    new_song_obj = Song()
    new_song_obj.artist_id = artist_obj.id
    new_song_obj.title = metadata["title"]
    new_song_obj.album = metadata.get("album")
    try:
        new_song_obj.year = int(metadata["year"])
    except (ValueError, TypeError):
        new_song_obj.year = None
    new_song_obj.language = metadata.get("language")
    # Only present for entries sourced from a YouTube playlist import - mp3
    # imports never carry this key, so this is a no-op (None) for them.
    new_song_obj.youtube_video_id = metadata.get("youtube_video_id")
    # "Migration (Full Album)": a whole album, filed under "Migration" when
    # it came with no album. Length alone isn't enough (mixes, live sets) -
    # those are left to Manage database -> "Find full albums".
    if title_has_full_album_marker(new_song_obj.title):
        set_song_kind([new_song_obj], SONG_KIND_FULL_ALBUM)
    add_db_entry(new_song_obj)
    return new_song_obj

def _backfill_youtube_link(metadata: dict, existing_song: Song) -> None:
    """If this metadata entry came with a YouTube video id (i.e. it's from a
    playlist import) and the matched existing song doesn't have one stored
    yet, save it - the song attribute is already tracked by the open
    session, so it rides along on the next commit without an explicit
    add_db_entry() call.
    """
    video_id = metadata.get("youtube_video_id")
    if video_id and not existing_song.youtube_video_id:
        existing_song.youtube_video_id = video_id
        print(f"Linked existing song [blue]{existing_song.title}[/blue] to YouTube video [green]{video_id}[/green]")

def find_existing_song(title: str, artist_songs: list, duration_s: int | None = None) -> Song | None:
    """The song among one artist's `artist_songs` that `title` already is:
    same normalized title (or the owner's Latin form of it: "Gruppa krovi"
    is "Группа крови"), else the first whose title contains it ("Song" vs
    "Song (Remastered)"). A title of punctuation only (":/") has to be
    spelt the same. Shared by resolve_song() and the cover import
    (utils/discoveries/mp3_covers.py). Full albums only for an entry that
    looks like one (`duration_s` = its length, if known) - see
    song_kinds.songs_for_entry()."""
    artist_songs = songs_for_entry(artist_songs, title, duration_s)
    key = normalize(title)
    if not key:
        # Only punctuation (":/", "?!"): normalize() leaves nothing, and ""
        # is in every title - only the very same characters are this song
        raw = " ".join((title or "").split()).casefold()
        return next((s for s in artist_songs if " ".join(s.title.split()).casefold() == raw), None) if raw else None
    exact = next((s for s in artist_songs if normalize(s.title) == key), None)
    if exact is None and key:
        exact = next((s for s in artist_songs if normalize(getattr(s, "title_latin", None) or "") == key), None)
    if exact is None and title_has_full_album_marker(title):
        # "Migration (Full Album)" is the full album "Migration"
        album_key = normalize(strip_full_album_marker(title))
        exact = next((s for s in artist_songs if is_full_album(s) and normalize(s.title) == album_key), None)
    if exact is not None:
        return exact
    return next((s for s in artist_songs if key in normalize(s.title)), None)


# What separates an artist from the title inside a tag's title field
_TITLE_ARTIST_SEPARATOR = re.compile(r"\s+[-–—]\s+")


def _title_core(title: str, drop_feat: bool = True) -> str:
    """A title without brackets, trailing junk ("Official Video", "HD"), a
    trailing "feat. X" (unless not `drop_feat`) or a leading track number
    ("T03: "), normalized: "Pay Your Respect (Official Lyric Video)" -> "pay
    your respect". Words standing for "&" are dropped too, since normalize()
    drops the "&" itself."""
    title = remove_brackets(title or "")
    if drop_feat:
        title = strip_trailing_feat(title)
    core = normalize(strip_track_number_prefix(strip_trailing_junk_suffix(title)))
    return " ".join(w for w in core.split() if w not in TITLE_AMPERSAND_WORDS) or core


def find_song_loosely(title: str, artist_songs: list, duration_s: int | None = None) -> Song | None:
    """find_existing_song(), else the song whose title is `title` without
    brackets, trailing junk or a track number on either side - first keeping
    a trailing "feat. X", then without it: "No Scrubs (Official Video)" is
    "No Scrubs", not the "No Scrubs ft. Joanna Jones (Cover)" beside it, but
    "Шкідлива звичка" still finds "Шкідлива звичка feat. YanGo"."""
    song = find_existing_song(title, artist_songs, duration_s)
    artist_songs = songs_for_entry(artist_songs, title, duration_s)
    for drop_feat in (False, True):
        if song is None and (core := _title_core(title, drop_feat)):
            song = next((s for s in artist_songs
                         if core in (_title_core(t, drop_feat) for t in song_titles(s))), None)
    return song


def is_distinctive_title(title: str) -> bool:
    """Is the title, without brackets / junk / a track number, long enough
    to name one song by itself (NAMELESS_AUTO_ACCEPT_MIN_WORDS and _CHARS)?
    "Dragostea Din Tei (Numa Numa) (Medieval Cover)" -> "dragostea din tei"
    is; "Intro" isn't."""
    core = _title_core(title)
    return len(core.split()) >= NAMELESS_AUTO_ACCEPT_MIN_WORDS and len(core) >= NAMELESS_AUTO_ACCEPT_MIN_CHARS


def title_after_artists(title: str, artist: str) -> str | None:
    """The title part of a tag's title field that starts with the artists
    themselves - "Христина Соловій & YanGo — Шкідлива звичка" for artist
    "Христина Соловій" -> "Шкідлива звичка" - or None: the part before the
    first " - " / " – " / " — " must hold the artist's whole name."""
    parts = _TITLE_ARTIST_SEPARATOR.split(title or "", maxsplit=1)
    wanted = normalize(artist)
    if len(parts) < 2 or not wanted or not parts[1].strip():
        return None
    return parts[1].strip() if f" {wanted} " in f" {normalize(parts[0])} " else None


class LocalSongMatcher:
    """mp3/import metadata -> the Song already in the database, looked up
    locally only (no MusicBrainz, nothing created). Caches each artist's
    songs, for a whole folder at a time. Shared by the cover import and
    playlists from folders.

    Looser than the import's own "already exists?" check, because a wrong
    "yes" here only mislabels one entry rather than skipping a new song: the
    artist may be the main one of a joined "A ft. B" name, and titles are
    also compared without brackets or trailing junk on either side - the
    files still carry what the import or the rubbish cleanup later removed
    from the database copy. When the artist lookup finds nothing (the shared
    artist search is substring-based, so a typo in the tag - "Liiquid
    Tension Experiment" - leaves it no candidates at all), the song is
    looked up by title instead, among songs whose artist resembles the
    tag's."""

    def __init__(self, session):
        self.session = session
        self._artists = {}
        self._songs = {}
        self._by_title = None
        self._scoring = None       # [(song, title forms)] for best_guesses()
        self._artist_forms = {}    # song id -> normalized _artist_names()

    def _artist(self, name: str) -> Artist | None:
        if name not in self._artists:
            artist = find_matching_artist(name)
            proposal = None if artist else propose_artist_split(name)
            if proposal and proposal.main:
                artist = find_matching_artist(proposal.main[0])
            self._artists[name] = artist
        return self._artists[name]

    def match(self, metadata: dict) -> Song | None:
        """The exact match, else the similar one - taken without asking, so
        without the guesses by title alone: a similar title could pick
        "Part 2" for a "Part 1" file, a file with no artist any "Intro"."""
        return self.match_exact(metadata) or self.match_similar(metadata, title_guesses=False)

    def match_exact(self, metadata: dict) -> Song | None:
        """The song by the tag's own artist (as the database knows it)."""
        name, title = metadata.get("artist_name"), metadata.get("title")
        if not name or not title:
            return None
        artist = self._artist(name)
        if artist is None:
            return None
        songs, duration = self._artist_songs(artist), metadata.get("duration_s")
        song = find_song_loosely(title, songs, duration)
        if song is None and (rest := title_after_artists(title, name)):
            song = find_song_loosely(rest, songs, duration)
        return song

    def _artist_songs(self, artist: Artist) -> list:
        if artist.id not in self._songs:
            self._songs[artist.id] = self.session.query(Song).filter(Song.artist_id == artist.id).all()
        return self._songs[artist.id]

    def match_confident(self, metadata: dict, min_artist_similarity: float) -> Song | None:
        """The one song with the file's title core whose artist's name (or
        Latin form, or a synonym) is at least `min_artist_similarity` like
        the tag artist the artist search didn't find - a misspelling sure
        enough to take without asking ("Liiquid tension experiment", 0.98).
        Short names need more, as everywhere (scaled_similarity_threshold()).
        Parts of joined names don't count: "College" is only suggested."""
        name, title = metadata.get("artist_name"), metadata.get("title")
        if not name or not title or self._artist(name) is not None:
            return None
        found = [s for s in self._songs_by_title_core(title, metadata.get("duration_s"))
                 if any(similarity(name, known) >= scaled_similarity_threshold(name, known, min_artist_similarity)
                        for known in _artist_names(s, split_parts=False))]
        return found[0] if len(found) == 1 else None

    def match_similar(self, metadata: dict, title_guesses: bool = True) -> Song | None:
        """For a file match_exact() found nothing for: the song its tags name
        with artist and title swapped ("N.I.B." by "Black Sabbath" tagged as
        "Black Sabbath" by "N.I.B."), else - the artist found - the one
        song of theirs with a similar title ("Losing TimeGrande Finale"),
        else - no artist found - the one song with its title
        (brackets/junk ignored) by an artist whose name, Latin form or synonym
        resembles the tag's; for a file with no artist, the one song with
        its title by anyone. None when there is none or more than one. A
        guess - the folder playlist asks the user about it."""
        name, title = metadata.get("artist_name"), metadata.get("title")
        if not title:
            return None
        duration_s = metadata.get("duration_s")
        if not name:
            found = self._songs_by_title_core(title, duration_s) if title_guesses else []
            if len(found) > 1:
                # The brackets tell them apart: "Dragostea Din Tei (Numa Numa)
                # (Medieval Cover)" is the one titled exactly so, not O-Zone's
                whole = normalize(title)
                found = [s for s in found if whole in map(normalize, song_titles(s))]
            return found[0] if len(found) == 1 else None
        swapped = self.match_exact({"artist_name": title, "title": name, "duration_s": duration_s})
        if swapped is not None:
            return swapped
        artist = self._artist(name)
        if artist is not None:
            if not title_guesses:
                return None
            return self._similar_title(artist, title, duration_s) or self._title_prefix(artist, title, duration_s)
        found = [s for s in self._songs_by_title_core(title, duration_s) if _artist_resembles(name, s)]
        return found[0] if len(found) == 1 else None

    def match_score(self, metadata: dict, song: Song) -> float:
        """0-1, how well the file's tags fit `song`: the title's similarity
        (whole and core, the better) and, when the file has an artist, the
        artist's (FILE_MATCH_SCORE_TITLE_WEIGHT : the rest) - read as tagged
        or with artist and title swapped, the better ("Ne Bucuram In Ciuda
        Lor" by "Carla's Dreams" tagged the other way round). Shown with every
        guess; best_guesses() ranks by it."""
        forms = self._title_forms(song)
        return max(self._score(*wanted, forms, song, 0.0) for wanted in self._readings(metadata))

    def best_guesses(self, metadata: dict, limit: int, min_score: float) -> list:
        """The `limit` songs scoring at least `min_score`, best first, as
        (song, score) - for a file nothing else found a song for. The title
        alone must reach `min_score` too, or a perfect artist would bring
        any of their songs over it ("Hymn to Virgil" for a Hozier file whose
        song isn't in the database)."""
        readings = [w for w in self._readings(metadata) if w[0]]
        if not readings:
            return []
        if self._scoring is None:
            self._scoring = [(song, self._title_forms(song)) for song in self.session.query(Song).all()]
        allowed = set(map(id, songs_for_entry([song for song, _ in self._scoring],
                                               metadata.get("title"), metadata.get("duration_s"))))
        scored = [(song, score) for song, forms in self._scoring
                  if id(song) in allowed
                  and (score := max(self._score(*w, forms, song, min_score) for w in readings)) >= min_score]
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        return scored[:limit]

    @classmethod
    def _readings(cls, metadata: dict) -> list:
        """The tags as written, and with artist and title swapped when both
        are there."""
        readings = [cls._wanted(metadata)]
        if metadata.get("artist_name") and metadata.get("title"):
            readings.append(cls._wanted({"title": metadata["artist_name"], "artist_name": metadata["title"]}))
        return readings

    @staticmethod
    def _wanted(metadata: dict) -> tuple:
        title = metadata.get("title") or ""
        forms = [f for f in {normalize(title), _title_core(title)} if f]
        return forms, normalize(metadata.get("artist_name") or "")

    @staticmethod
    def _title_forms(song: Song) -> list:
        return list({f for t in song_titles(song) for f in (normalize(t), _title_core(t)) if f})

    def _score(self, title_forms: list, artist: str, song_forms: list, song: Song, floor: float) -> float:
        if not title_forms:
            return 0.0
        weight = FILE_MATCH_SCORE_TITLE_WEIGHT if artist else 1.0
        title = max((similarity_at_least(a, b, floor) for a in title_forms for b in song_forms), default=0.0)
        if not artist:
            return title
        if title < floor:
            return 0.0
        if song.id not in self._artist_forms:
            self._artist_forms[song.id] = list({normalize(n) for n in _artist_names(song)} - {""})
        artist_score = max((similarity_at_least(artist, known, 0.0) for known in self._artist_forms[song.id]), default=0.0)
        return weight * title + (1 - weight) * artist_score

    def title_candidates(self, metadata: dict, limit: int) -> list:
        """For a file with no artist: the songs (any artist) with its title
        core when there are 2 to `limit` of them - match_similar() takes
        a single one - by artist; else []."""
        if metadata.get("artist_name") or not metadata.get("title"):
            return []
        found = self._songs_by_title_core(metadata["title"], metadata.get("duration_s"))
        if not 2 <= len(found) <= limit:
            return []
        return sorted(found, key=lambda s: ((s.artist.name or "").lower(), s.id))

    def _songs_by_title_core(self, title: str, duration_s: int | None = None) -> list:
        """Every song, any artist, whose title core is `title`'s (full
        albums only for an entry that looks like one)."""
        core = _title_core(title)
        if not core:
            return []
        if self._by_title is None:
            self._by_title = {}
            for song in self.session.query(Song).all():
                for song_core in {c for c in map(_title_core, song_titles(song)) if c}:
                    self._by_title.setdefault(song_core, []).append(song)
        return songs_for_entry(self._by_title.get(core, []), title, duration_s)

    def _similar_title(self, artist: Artist, title: str, duration_s: int | None = None) -> Song | None:
        """The one song of `artist` whose whole title, bracket text
        included, is at least FILE_TITLE_SIMILARITY_THRESHOLD similar to
        `title`."""
        wanted = normalize(title)
        found = [
            song for song in songs_for_entry(self._artist_songs(artist), title, duration_s)
            if any(similarity(wanted, known) >= scaled_similarity_threshold(wanted, known, FILE_TITLE_SIMILARITY_THRESHOLD)
                   for known in map(normalize, song_titles(song)))
        ]
        return found[0] if len(found) == 1 else None

    def _title_prefix(self, artist: Artist, title: str, duration_s: int | None = None) -> Song | None:
        """The song of `artist` whose title (core) the file's title starts
        with, as whole words - junk the cleanup lists don't know trailing it
        ("Tubthumping  Officiel official Soundtrack World Cup 98"). The
        longest such title wins ("Song 2" over "Song"); None on a tie."""
        wanted = _title_core(title)
        found = []
        for song in songs_for_entry(self._artist_songs(artist), title, duration_s):
            cores = [c for c in map(_title_core, song_titles(song))
                     if len(c) >= FILE_TITLE_PREFIX_MIN_LENGTH and wanted.startswith(c + " ")]
            if cores:
                found.append((max(map(len, cores)), song))
        if not found:
            return None
        longest = max(length for length, _ in found)
        best = [song for length, song in found if length == longest]
        return best[0] if len(best) == 1 else None


def _artist_names(song: Song, split_parts: bool = True):
    """The song's artists' names, Latin forms, synonyms and - with
    `split_parts` - the parts of joined names not split yet ("College" of
    "College & Electric Youth")."""
    for artist in song_all_artists(song):
        synonyms = (artist_match_synonyms(artist) or "").split(",")
        split = propose_artist_split(artist.name) if split_parts else None
        parts = [*split.main, *split.feat] if split else []
        yield from filter(None, (artist.name, *(s.strip() for s in synonyms), *parts))


def _artist_resembles(name: str, song: Song) -> bool:
    """Does `name` resemble one of _artist_names(song)?"""
    return any(similarity(name, known) > scaled_similarity_threshold(name, known, SPELLING_CHECK_THRESHOLD)
               for known in _artist_names(song))



def find_songs_by_title(session, title: str, limit: int = 15) -> list:
    """Songs of any artist whose title is `title`, for a file that gave no
    usable artist: same title without brackets/junk first, then titles
    containing it or contained in it (at least 4 characters, so "Ja" doesn't
    match half the library). Read-only."""
    core = _title_core(title)
    if not core:
        return []
    exact, partial = [], []
    for song in session.query(Song).all():
        candidates = [c for c in map(_title_core, song_titles(song)) if c]
        if core in candidates:
            exact.append(song)
        elif any(min(len(c), len(core)) >= 4 and (core in c or c in core) for c in candidates):
            partial.append(song)
    by_label = lambda s: (s.artist.name.lower(), s.title.lower())
    return (sorted(exact, key=by_label) + sorted(partial, key=by_label))[:limit]


def resolve_song(metadata: dict, artist_obj: Artist, pending_conflicts: list) -> tuple:
    """
    Resolve a song entry for the given artist.

    Returns (song, is_pending):
    - (song, False): song was created (or already existed - song is None then).
    - (None, True): a similar song was found. The conflict was appended to
      pending_conflicts instead of asking right away, so the caller should
      skip it for now and resolve it later, once the whole batch is done.
    """
    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)

    ex_son = find_existing_song(metadata["title"], existing_artists_songs, metadata.get("duration_s"))
    if ex_son is not None:
        print("Yes, there is a song like this already. Skipping")
        slog(f"    [SONG EXISTS] Exact match found", priority=1)
        _backfill_youtube_link(metadata, ex_son)
        return None, False

    slog("Couldn't find the song, trying spellchecking")
    similar_song = find_similar_song(metadata, artist_obj)

    if similar_song:
        print(f"Found similar song [blue]{similar_song.title}[/blue] by [cyan]{similar_song.artist.name}[/cyan] but you were trying to add [green]{metadata['title']}[/green] by [cyan]{artist_obj.name}[/cyan] - review deferred until the import finishes")
        slog(f"    [DEFERRED] Similar song conflict queued for later resolution", priority=1)
        pending_conflicts.append({
            "metadata": metadata,
            "artist_obj": artist_obj,
            "existing_song": similar_song,
        })
        return None, True

    new_song_obj = create_song_entry(metadata, artist_obj)
    return new_song_obj, False


def _entry_label(metadata: dict) -> str:
    return metadata.get("_label") or f"{metadata.get('artist_name')} - {metadata.get('title')}"


def run_import_batch(metadata_list: list, pre_skipped: list = None, late_entries=None) -> list:
    """Resolve/create DB entries for a batch of already-extracted metadata
    dicts, shared by every import source (mp3 tags, YouTube playlists, ...).

    Each metadata dict needs at least 'artist_name' and 'title'; 'album',
    'year', 'language', 'origin' and 'youtube_video_id' are optional. An
    optional '_label' is used for logging/skip messages instead of
    "<artist> - <title>" (handy when the source has a more natural label,
    like an mp3 filename).

    `pre_skipped` lets a caller fold skips it already made before calling
    this (e.g. an mp3 file with no usable artist/title at all) into the
    final skipped-songs summary, as a list of (label, reason) tuples.

    Ambiguous-artist and similar-song matches aren't resolved during the
    loop - they're queued and asked about in one batch after every entry has
    been processed, so the import doesn't keep stopping for input.

    `late_entries` is an optional callable for a caller that has its own
    questions to ask about some entries (e.g. the YouTube importer's
    artist/title swap review): it's called once the slow main loop is done,
    so the user isn't kept waiting for the batch to start, and returns
    (extra_metadata_list, extra_skipped) - processed exactly like
    `metadata_list` / `pre_skipped`, before any of the reviews below.
    """
    added_songs = []
    skipped_songs = []
    reason_for_skipping = []

    if pre_skipped:
        for label, reason in pre_skipped:
            skipped_songs.append(label)
            reason_for_skipping.append(reason)

    pending_song_conflicts = []
    pending_artist_conflicts = []
    # Entries whose artist resolution was deferred - their song still needs
    # resolving once the artist conflicts below are reviewed.
    deferred_entries = []

    # OPTIMIZATION: Cache for artists found in this import batch
    artist_cache = {}

    # Artists created by this batch are the only ones offered for splitting
    # at the end ("A feat. B" arriving as one new artist); anything that
    # already existed was matched as-is, so its name is a known artist.
    music_session, _ = open_and_set_global_database_sessions()
    max_artist_id_before = music_session.query(func.max(Artist.id)).scalar() or 0

    # Reset MusicBrainz counters so the summary at the end reflects this run only.
    MBStats.reset()

    added_count = 0
    skipped_count = len(skipped_songs)

    entry_timings = {}

    def process_entry(metadata: dict, idx: int, total: int) -> None:
        nonlocal added_count, skipped_count
        entry_start = time.time()
        label = _entry_label(metadata)
        mlog(f"\n[{idx}/{total}] Processing: {label}")

        if not metadata.get("artist_name") or not metadata.get("title"):
            skipped_count += 1
            skipped_songs.append(label)
            reason_for_skipping.append("Missing artist or title")
            return

        print(f"{metadata['artist_name']} - {metadata['title']}")

        artist_start = time.time()
        new_artist_obj, artist_is_pending = resolve_artist(metadata, artist_cache, pending_artist_conflicts)
        artist_time = time.time() - artist_start
        mlog(f"  └─ Artist resolution: {artist_time:.3f}s")

        if artist_is_pending:
            mlog(f"  └─ Artist resolution deferred: similar artist needs review")
            deferred_entries.append(metadata)
            entry_timings[label] = {'total': time.time() - entry_start, 'artist': artist_time, 'song': 0.0}
            print("##########")
            print()
            return

        song_start = time.time()
        new_song_obj, is_pending = resolve_song(metadata, new_artist_obj, pending_song_conflicts)
        song_time = time.time() - song_start
        mlog(f"  └─ Song resolution: {song_time:.3f}s")

        if is_pending:
            mlog(f"  └─ Song resolution deferred: similar song needs review")
        elif new_song_obj:
            added_count += 1
            added_songs.append(new_song_obj)
        else:
            skipped_count += 1
            skipped_songs.append(label)
            reason_for_skipping.append("Song already exists")

        entry_timings[label] = {'total': time.time() - entry_start, 'artist': artist_time, 'song': song_time}
        mlog(f"  ⏱️  TOTAL for this entry: {entry_timings[label]['total']:.3f}s")

        print("##########")
        print()

    for idx, metadata in enumerate(metadata_list, 1):
        process_entry(metadata, idx, len(metadata_list))

    if late_entries:
        extra_metadata_list, extra_skipped = late_entries()
        for label, reason in extra_skipped or []:
            skipped_count += 1
            skipped_songs.append(label)
            reason_for_skipping.append(reason)
        for idx, metadata in enumerate(extra_metadata_list or [], 1):
            process_entry(metadata, idx, len(extra_metadata_list))

    # Now that every entry has been processed, go through the ambiguous-artist
    # matches that were queued along the way and ask about each one in a
    # single batch, instead of interrupting the import every time one came
    # up. Each normalized artist name only appears once here even if several
    # entries shared it (see the artist_cache / _PENDING_ARTIST check in
    # resolve_artist).
    if pending_artist_conflicts:
        print("\n" + "="*70)
        print(f"REVIEW: {len(pending_artist_conflicts)} artist(s) found a similar match in the database")
        print("="*70)
        for conflict in pending_artist_conflicts:
            conflict_metadata = conflict["metadata"]
            normalized_name = conflict["normalized_name"]
            new_artist_name = conflict_metadata["artist_name"]

            resolved_artist = None
            for candidate in conflict["candidate_artists"]:
                print(f"Found similar artist [blue]{candidate.name}[/blue] but you were trying to add [green]{new_artist_name}[/green]")
                confirmation = ask_yes_no("Do you wish to use the artist already in the database?")
                if confirmation:
                    resolved_artist = candidate
                    print("Found existing artist")
                    break

            if resolved_artist is None:
                resolved_artist = Artist()
                resolved_artist.name = new_artist_name
                new_artist_origin = conflict_metadata.get("origin")
                if new_artist_origin:
                    resolved_artist.origin = new_artist_origin
                add_db_entry(resolved_artist)

            # Unblocks every entry that shared this artist name (they all hit
            # the artist_cache fast path in resolve_artist from here on).
            artist_cache[normalized_name] = resolved_artist
            print()

    # Every entry that got deferred because of an artist conflict can now
    # resolve its artist from the cache (instant - see above) and its song. A
    # similar song for one of these still gets queued below rather than asked
    # right away.
    for deferred_metadata in deferred_entries:
        deferred_artist_obj, _ = resolve_artist(deferred_metadata, artist_cache, pending_artist_conflicts)
        deferred_song_obj, deferred_is_pending = resolve_song(deferred_metadata, deferred_artist_obj, pending_song_conflicts)
        if deferred_is_pending:
            continue
        elif deferred_song_obj:
            added_count = added_count + 1
            added_songs.append(deferred_song_obj)
        else:
            skipped_count = skipped_count + 1
            skipped_songs.append(_entry_label(deferred_metadata))
            reason_for_skipping.append("Song already exists")

    # Finally, go through the similar-song matches that were queued along the
    # way (both from the main loop and from the deferred entries above) and
    # ask about each one in a single batch, instead of interrupting the
    # import every time one came up.
    if pending_song_conflicts:
        print("\n" + "="*70)
        print(f"REVIEW: {len(pending_song_conflicts)} song(s) found a similar match in the database")
        print("="*70)
        for conflict in pending_song_conflicts:
            conflict_metadata = conflict["metadata"]
            conflict_artist_obj = conflict["artist_obj"]
            existing_song = conflict["existing_song"]
            print(f"Found similar song [blue]{existing_song.title}[/blue] by [cyan]{existing_song.artist.name}[/cyan] but you were trying to add [green]{conflict_metadata['title']}[/green] by [cyan]{conflict_artist_obj.name}[/cyan]")
            confirmation = ask_yes_no("Do you wish to use the song already in the database?")
            if confirmation:
                skipped_count = skipped_count + 1
                skipped_songs.append(_entry_label(conflict_metadata))
                reason_for_skipping.append("Song already exists")
                _backfill_youtube_link(conflict_metadata, existing_song)
            else:
                new_song_obj = create_song_entry(conflict_metadata, conflict_artist_obj)
                added_count = added_count + 1
                added_songs.append(new_song_obj)
            print()

    new_artists = music_session.query(Artist).filter(Artist.id > max_artist_id_before).all()
    if new_artists:
        review_artist_splits(new_artists)

    # A new song whose album title another artist already uses was left
    # without an album row (utils/database/albums.py) - settle those now
    offer_album_resolution(added_songs)

    # Print timing summary
    print("\n" + "="*70)
    print("PERFORMANCE SUMMARY")
    print("="*70)
    if entry_timings:
        total_time_all = sum(t['total'] for t in entry_timings.values())
        avg_time = total_time_all / len(entry_timings)
        max_time = max(t['total'] for t in entry_timings.values())

        print(f"Total time for all entries: {total_time_all:.3f}s")
        print(f"Average time per entry: {avg_time:.3f}s")
        print(f"Slowest entry: {max_time:.3f}s\n")

        print("Per-entry breakdown:")
        for label, times in sorted(entry_timings.items(), key=lambda x: x[1]['total'], reverse=True):
            print(f"  {label}")
            print(f"    ├─ Artist:   {times['artist']:.3f}s")
            print(f"    ├─ Song:     {times['song']:.3f}s")
            print(f"    └─ TOTAL:    {times['total']:.3f}s")

    # OPTIMIZATION: Show cache statistics
    if _SPELL_CHECK_CACHE:
        print(f"\n💾 Spell Check Cache Statistics:")
        print(f"   Total cached entries: {len(_SPELL_CHECK_CACHE)}")
        cache_keys = list(_SPELL_CHECK_CACHE.keys())
        print(f"   Cache entries: {cache_keys[:5]}")
        if len(cache_keys) > 5:
            print(f"   ... and {len(cache_keys) - 5} more")

    # Persist the disk-backed spell-check cache and report MusicBrainz traffic.
    spellcheck_cache.save()
    print()
    print(MBStats.format_summary())

    print("="*70)

    print(f"Skipped {skipped_count} songs:")
    for s_s, reason in zip(skipped_songs, reason_for_skipping):
        print(f"{s_s} (reason - {reason})")
    print()
    print("##########")
    print()
    print()

    print(f"\nDone! Added {added_count}, updated 0.")
    sorted_songs = sorted(added_songs, key=lambda song: (song.artist.name, song.title))
    return sorted_songs
