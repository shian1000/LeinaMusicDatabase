"""Two artist rows that are one artist, and songs whose artist and title were
swapped - found among the songs of one album title in "Resolve albums with
several artists" (utils/ui/album_resolution_review.py), where they're most of
what makes a title look shared: "björk" next to "Вjörk" (Cyrillic В),
"Men I Trust" next to "Man I Trust", "Renegade - Styx" next to Styx's own
songs; swapped songs also across the whole catalog (find_swapped_in_catalog(),
Manage database -> "Find songs with artist and title swapped",
utils/ui/swapped_song_review.py) - and artist rows left without any song
(find_songless_artists(), "Remove artists without songs"). Pure lookups plus
the repairs the reviews need (fix_swapped_song(), remove_songless_artist());
merging two artists stays database_management.merge_artists_in_db()."""

import re
from collections import Counter
from difflib import SequenceMatcher
from itertools import combinations

from config.constants import (
    SAME_ARTIST_SIMILARITY_THRESHOLD, SONGLESS_ARTIST_IGNORE_FILE, SPELLING_CHECK_THRESHOLD, SWAPPED_SONG_IGNORE_FILE)
from utils.common.json_file import read_json, write_json
from utils.common.normalizer import name_key, normalize
from utils.common.script_utils import matching_key, scripts_in
from utils.common.text_utils import resembles_across_scripts, similarity
from utils.database.albums import delete_empty_album
from utils.database.artist_reassignment import (
    artists_named, move_swapped_song, remove_unused_artist, swap_names_of_artist)
from utils.common.artist_credits import propose_artist_split
from utils.database.datatables import AdditionalSongArtist, Album, Artist, Song
from utils.database.latin_names import artist_synonyms
from utils.database.song_artists import count_artist_songs, song_all_artists

# Cyrillic letters drawn like Latin ones, for a name typed with one of them by
# mistake ("Вjörk" - Cyrillic В - is "Bjork"). Only applied to a name mixing
# both alphabets; an all-Cyrillic name is a real spelling, see matching_key().
_CYRILLIC_LOOKALIKES = str.maketrans("АВЕКМНОРСТХаеорсухіјѕ", "ABEKMHOPCTXaeopcyxijs")
# A first name in front ("Agnieszka Chylińska" / "Chylińska") is one word;
# below this length a contained name proves nothing.
_MIN_CONTAINED_NAME = 4
# What follows a name when the longer one is a duo, not a spelling ("sanah i
# Igor Herbut", "Dawid Podsiadło i Kaśka Sochacka")
_JOINING_WORDS = {"i", "and", "&", "x", "feat", "feat.", "ft", "ft.", "featuring", "with", "oraz", "y", "et", "und", "vs", "vs."}


def _fix_lookalikes(name: str) -> str:
    return name.translate(_CYRILLIC_LOOKALIKES) if {"LATIN", "CYRILLIC"} <= scripts_in(name) else name


def _names_match(a: str, b: str) -> bool:
    """One name, spelled differently: case/punctuation ("P.O.D." / "POD"),
    another script ("Aimyon" / "あいみょん"), a look-alike letter ("björk" /
    "Вjörk") or a typo ("Man I Trust" / "Men I Trust")."""
    if not a or not b:
        return False
    a, b = _fix_lookalikes(a), _fix_lookalikes(b)
    if normalize(a) == normalize(b) or (name_key(a) and name_key(a) == name_key(b)):
        return True
    if resembles_across_scripts(a, b):
        return True
    return similarity(a, b) >= SAME_ARTIST_SIMILARITY_THRESHOLD


def _contains_as_spelling(longer: str, shorter: str) -> bool:
    """The longer name is the shorter one plus only a first name in front
    ("Agnieszka Chylińska"), a bracket, or the name in another script
    ("Mariya Takeuchi 竹内 まりや", "Group (wokal: X"). Not "Fisz Emade
    Tworzywo" - another act - nor a duo ("sanah i Igor Herbut")."""
    shorter_norm, longer_norm = normalize(shorter), normalize(longer)
    if len(shorter_norm) < _MIN_CONTAINED_NAME or shorter_norm == longer_norm:
        return False
    match = re.search(rf"(?<!\w){re.escape(shorter_norm)}(?!\w)", longer_norm)
    if not match:
        return False
    before, after = longer_norm[:match.start()].split(), longer_norm[match.end():].split()
    if any(w in _JOINING_WORDS for w in before[-1:] + after[:1]):
        return False
    if not after and len(before) == 1:
        return True
    # Whatever's added is bracketed or in another script: same artist, annotated
    extra = longer.replace(shorter, "", 1) if shorter in longer else ""
    extra = extra.strip(" -/")
    return bool(extra) and (extra.startswith("(") or "LATIN" not in scripts_in(extra))


def _split_names(name: str) -> list[str] | None:
    proposal = propose_artist_split(name)
    return proposal.names if proposal else None


def artists_look_same(a: Artist, b: Artist) -> bool:
    """Do two artist rows look like one artist under two spellings?

    A joined name ("Purple Disco Machine, Sophie and the Giants") is several
    artists - a job for "Split joined artist names", not a merge - unless
    every part is this same artist in another script ("Molchat Doma / Молчат
    Дома", "Молчат Дома (Molchat Doma)")."""
    if a.id == b.id:
        return False
    names_a, names_b = [a.name, *artist_synonyms(a)], [b.name, *artist_synonyms(b)]
    if any(_names_match(x, y) for x in names_a for y in names_b):
        return True

    parts_a, parts_b = _split_names(a.name), _split_names(b.name)
    if parts_a or parts_b:
        for parts, other in ((parts_a, b.name), (parts_b, a.name)):
            if parts and all(_names_match(part, other) for part in parts):
                return True
        return False

    longer, shorter = (a.name, b.name) if len(a.name) >= len(b.name) else (b.name, a.name)
    return _contains_as_spelling(longer, shorter)


def find_same_artist_pairs(songs: list[Song]) -> list[tuple[Artist, Artist]]:
    """Pairs of the songs' primary artists that look like one artist, each
    pair once, artists in order of first appearance."""
    artists = list({song.artist.id: song.artist for song in songs}.values())
    return [(a, b) for a, b in combinations(artists, 2) if artists_look_same(a, b)]


def find_swapped_songs(songs: list[Song]) -> list[tuple[Song, Artist]]:
    """Songs whose *title* is another of these songs' artists ("Renegade -
    Styx" next to Styx's songs): artist and title were entered backwards.
    Returns (song, the artist its title names). Only within `songs`, and not
    when the song's own artist has more songs than that one ("Natalia
    Przybysz - Dzieci malarzy" is the real song, "Dzieci Malarzy - Natalia
    Przybysz" the mix-up). A
    title that merely names a band ("Alma - Perfect" next to the band
    Perfect) can still be flagged; the user decides."""
    artists = {song.artist.id: song.artist for song in songs}
    flagged = []
    for song in songs:
        for artist in artists.values():
            if artist.id == song.artist.id or not _names_match(song.title, artist.name):
                continue
            if _names_match(song.artist.name, artist.name) or len(song.artist.songs) > len(artist.songs):
                continue
            # "Renegade - Styx" and "Styx - Renegade" both look swapped when each
            # artist has one song: they're one song entered twice, flag one of them
            if any(other.artist.id == artist.id and flagged_artist.id == song.artist.id
                   for other, flagged_artist in flagged):
                continue
            flagged.append((song, artist))
            break
    return flagged


def _name_keys(name: str) -> set[str]:
    name = _fix_lookalikes(name or "")
    return {key for key in (normalize(name), name_key(name)) if key}


def find_swapped_in_catalog(session) -> list[tuple[Song, Artist, bool]]:
    """The whole catalog's songs that may be stored "Title - Artist": the
    title is another artist's name (or synonym, any spelling _names_match()'s
    exact keys catch) who has songs - a row left without any proves nothing -
    and the song's own artist has no other song - the rule
    of may_be_swapped(), so "Ed Sheeran - Perfect" next to the band Perfect
    isn't asked about. Returns (song, the artist its title names, whether
    that artist already has a song titled like this song's artist - the
    song entered both ways, nearly surely swapped). Songs remembered as not
    swapped (SWAPPED_SONG_IGNORE_FILE) are left out. Exact keys only: fuzzy
    matching every title against every artist is too slow for the catalog."""
    by_key = {}
    for artist in session.query(Artist).all():
        for name in (artist.name, *artist_synonyms(artist)):
            for key in _name_keys(name):
                by_key.setdefault(key, {})[artist.id] = artist
    credits = Counter(artist_id for artist_id, in session.query(Song.artist_id))
    credits.update(artist_id for artist_id, in session.query(AdditionalSongArtist.artist_id))
    ignored = read_ids(SWAPPED_SONG_IGNORE_FILE)

    flagged = []
    for song in session.query(Song).order_by(Song.id).all():
        own = song.artist
        if (own is None or song.id in ignored or credits[own.id] != 1 or song.additional_artist_links
                or normalize(song.title or "") == normalize(own.name or "")):
            continue
        named = {a.id: a for key in _name_keys(song.title) for a in by_key.get(key, {}).values()
                 if a.id != own.id and credits[a.id] and not _names_match(own.name, a.name)}
        if not named:
            continue
        artist = max(named.values(), key=lambda a: credits[a.id])
        both_ways = any(_names_match(other.title, own.name) for other in artist.songs)
        flagged.append((song, artist, both_ways))
    return flagged


def find_songless_artists(session) -> list[tuple[Artist, Artist | None]]:
    """Artist rows nothing uses: no song credits them, none is a cover of
    them (those stay, is_unused()) and no song is on an album of theirs -
    leftovers of deleted songs, songs credited to another artist with the
    old row kept, and older fixes. Returns (artist, the artist with songs it
    looks like - artists_look_same() - to merge it into, or None). Artists
    remembered as kept (SONGLESS_ARTIST_IGNORE_FILE) are left out."""
    used = {artist_id for artist_id, in session.query(Song.artist_id)}
    used |= {artist_id for artist_id, in session.query(Song.covered_artist_id)}
    used |= {artist_id for artist_id, in session.query(AdditionalSongArtist.artist_id)}
    used |= {artist_id for artist_id, in session.query(Album.artist_id).join(Song, Song.album_id == Album.id)}
    kept = read_ids(SONGLESS_ARTIST_IGNORE_FILE)
    artists = session.query(Artist).order_by(Artist.id).all()
    songless = [a for a in artists if a.id not in used and a.id not in kept]
    if not songless:
        return []
    rough = {a.id: _rough_names(a) for a in artists}
    with_songs = [a for a in artists if a.id in used]

    def look_alike(artist):
        # artists_look_same() costs ~2 ms a pair: only for the pairs a word or
        # SequenceMatcher's upper bound (below any threshold it uses) lets through
        words, names = rough[artist.id]
        return next((other for other in with_songs
                     if (words & rough[other.id][0]
                         or any(_may_reach(x, y) for x in names for y in rough[other.id][1]))
                     and artists_look_same(artist, other)), None)

    return [(artist, look_alike(artist)) for artist in songless]


def _may_reach(a: str, b: str) -> bool:
    """Can similarity(a, b) reach SPELLING_CHECK_THRESHOLD? Length first
    (real_quick_ratio() without building a matcher), then quick_ratio()."""
    return (2 * min(len(a), len(b)) / (len(a) + len(b)) >= SPELLING_CHECK_THRESHOLD
            and SequenceMatcher(None, a, b).quick_ratio() >= SPELLING_CHECK_THRESHOLD)


def _rough_names(artist: Artist) -> tuple[set[str], list[str]]:
    """The artist's names (and synonyms) lowercased and romanized, and their words."""
    names = [n for n in (artist.name, *artist_synonyms(artist)) if n]
    forms = [n.lower() for n in names] + [matching_key(n) for n in names]
    return {w for n in names for w in matching_key(n).split()}, [f for f in forms if f]


def remove_songless_artist(session, artist: Artist, heir: Artist | None) -> None:
    """Delete an artist find_songless_artists() found, with its albums (no
    song is on them); merged into `heir` instead when given, its names kept
    as heir's synonyms. Caller commits."""
    for album in list(artist.albums):
        delete_empty_album(session, album)
    if heir is not None:
        remove_unused_artist(session, artist, heir, keep_names=True)
    else:
        session.delete(artist)
        session.flush()


def read_ids(path) -> set[int]:
    """The ids an ignore list (SWAPPED_SONG_IGNORE_FILE,
    SONGLESS_ARTIST_IGNORE_FILE) holds."""
    return {i for i in read_json(path, []) if isinstance(i, int)}


def remember_ids(path, ids: list[int]) -> None:
    write_json(path, sorted(read_ids(path) | set(ids)))


def may_be_swapped(session, song: Song) -> bool:
    """Could this song's artist really be its title? Not when the artist has
    another song in the database, which makes it a known artist, nor for a
    song crediting several artists or one named like its artist."""
    return (count_artist_songs(session, song.artist_id) == 1
            and len(song_all_artists(song)) == 1
            and normalize(song.title) != normalize(song.artist.name))


def swap_song_names(session, song: Song) -> None:
    """Swap the title and artist name of a song may_be_swapped() let through,
    so its artist row has no other song: credited to the artist named like its
    title when there is one, else the row is simply renamed. Caller commits."""
    twins = artists_named(session, song.title, exclude=song.artist)
    if twins:
        move_swapped_song(session, song, max(twins, key=lambda twin: count_artist_songs(session, twin.id)))
    else:
        swap_names_of_artist(song)
    session.flush()


def fix_swapped_song(session, song: Song, artist: Artist) -> Artist | None:
    """Make `song` the existing `artist`'s, with its old artist name as the
    title ("Renegade - Styx" -> "Styx - Renegade"). Unlike renaming the
    artist row, this doesn't make a second Styx. The old artist is deleted
    when it has no song left; returns it if so (already deleted), else None."""
    _, removed = move_swapped_song(session, song, artist)
    return removed
