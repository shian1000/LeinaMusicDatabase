"""Two artist rows that are one artist, and songs whose artist and title were
swapped - found among the songs of one album title in "Resolve albums with
several artists" (utils/ui/album_resolution_review.py), where they're most of
what makes a title look shared: "björk" next to "Вjörk" (Cyrillic В),
"Men I Trust" next to "Man I Trust", "Renegade - Styx" next to Styx's own
songs. Pure lookups plus the one repair the review needs (fix_swapped_song());
merging two artists stays database_management.merge_artists_in_db()."""

import re
from itertools import combinations

from config.constants import SAME_ARTIST_SIMILARITY_THRESHOLD
from utils.common.normalizer import name_key, normalize
from utils.common.script_utils import scripts_in
from utils.common.text_utils import resembles_across_scripts, similarity
from utils.database.albums import reassign_artist_albums
from utils.database.artist_splitting import propose_artist_split
from utils.database.datatables import Artist, Song
from utils.database.song_artists import count_artist_songs, reassign_additional_artist_links

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


def _synonyms(artist: Artist) -> list[str]:
    return [s.strip() for s in (artist.synonyms or "").split(",") if s.strip()]


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
    names_a, names_b = [a.name, *_synonyms(a)], [b.name, *_synonyms(b)]
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


def fix_swapped_song(session, song: Song, artist: Artist) -> Artist | None:
    """Make `song` the existing `artist`'s, with its old artist name as the
    title ("Renegade - Styx" -> "Styx - Renegade"). Unlike edit_songs'
    swap_title_and_artist() this doesn't rename the artist row, which would
    make a second Styx. The old artist is deleted when it has no song left;
    returns it if so (already deleted), else None."""
    old = song.artist
    song.title = old.name
    song.artist = artist
    session.flush()
    if count_artist_songs(session, old.id):
        return None
    # Nothing should point at it any more, but an album row or extra-artist
    # link may still; hand those over before the row goes
    reassign_additional_artist_links(session, old.id, artist.id)
    reassign_artist_albums(session, old.id, artist.id)
    session.delete(old)
    session.flush()
    return old
