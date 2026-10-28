"""Duplicate songs: two rows of one artist that are the same recording, told
apart only by spelling, a featured-artist credit or a junk annotation
("Savana (feat. Darko Rundek)" / "Savana feat. Darko Rundek", "APT." / "APT
feat. Bruno Mars", "Sorcererz" / "Sorcererz (Visualiser)"). Other brackets
are kept, since a remix, cover, live or acoustic version is a song of its own.

merge_songs() is the one way to fold a duplicate into the song kept: nothing
of the dropped row is lost (its empty-in-the-kept fields, its extra artists),
and every reference to its id follows (song_references.record_song_merge()).
Menu: Manage database -> Resolve duplicates -> "Resolve duplicated songs"
(utils/ui/song_duplicate_review.py); "Resolve duplicated artists" folds the
songs whose titles only differ in spelling without asking."""

import re
from collections import defaultdict

from config.constants import ADDITIONAL_ARTIST_ROLE_FEAT, DUPLICATE_TITLE_EXTRA_JUNK, DUPLICATE_VERSION_WORDS, TITLE_JUNK_SUFFIXES
from utils.common.normalizer import normalize
from utils.common.text_utils import strip_trailing_feat, strip_trailing_junk_suffix
from utils.database.datatables import Artist, Song
from utils.database.genres import keep_genre_on_merge
from utils.database.latin_names import keep_latin_on_merge
from utils.database.artist_splitting import propose_artist_split
from utils.database.song_artists import add_additional_artist, additional_artist_role, additional_artists, song_all_artists
from utils.database.song_references import record_song_merge

# Filled from the dropped song when the kept one has nothing there
_FILLED_FIELDS = ("album", "year", "language", "youtube_video_id", "spotify_url", "music_video_youtube_id",
                  "track_number", "disc_number")
_BRACKET_RE = re.compile(r"\s*[(\[]([^)\]]*)[)\]]")
_FEAT_WORD_RE = re.compile(r"^\s*(?:feat\.?|ft\.?|featuring|with)\s", re.IGNORECASE)
_JUNK = {*TITLE_JUNK_SUFFIXES, *DUPLICATE_TITLE_EXTRA_JUNK}


def duplicate_key(title: str) -> str:
    """The title without featured-artist credits (bracketed or not) and
    junk annotations, normalized - other brackets kept."""
    def drop(match):
        content = re.sub(r"\s+", " ", match.group(1)).strip().lower()
        return "" if _FEAT_WORD_RE.match(content + " ") or content in _JUNK else match.group(0)

    title = _BRACKET_RE.sub(drop, title or "")
    return normalize(strip_trailing_junk_suffix(strip_trailing_feat(title)))


def clean_title(title: str) -> str:
    """The title without its junk-only brackets ("Sorcererz (Visualiser)" ->
    "Sorcererz"); featured-artist credits stay - they say who sings."""
    def drop(match):
        return "" if re.sub(r"\s+", " ", match.group(1)).strip().lower() in _JUNK else match.group(0)

    cleaned = strip_trailing_junk_suffix(_BRACKET_RE.sub(drop, title or "")).strip()
    return cleaned or title


_FEAT_CREDIT_RE = re.compile(r"(?:^|\s)(?:feat\.?|ft\.?|featuring)\s+(.+)$", re.IGNORECASE)


def feat_credits(title: str) -> list[str]:
    """The featured artists a title credits, bracketed or not: "Hollywood
    feat. Snoop Dogg & Jamie Principle (Visualiser)" -> ["Snoop Dogg",
    "Jamie Principle"]."""
    credits = []
    for match in _BRACKET_RE.finditer(title or ""):
        if _FEAT_WORD_RE.match(match.group(1).strip() + " "):
            credits.append(match.group(1).strip())
    bare = _FEAT_CREDIT_RE.search(_BRACKET_RE.sub("", title or ""))
    if bare:
        credits.append(bare.group(0).strip())
    names = []
    for credit in credits:
        split = propose_artist_split(f"X {credit}")
        names += split.feat if split else []
    return names


# An unbracketed credit up to the next bracket: "(Remix)" after it stays
_UNBRACKETED_FEAT_RE = re.compile(r"(?:^|\s)(?:feat\.?|ft\.?|featuring)\s+[^()\[\]]+", re.IGNORECASE)


def split_feat_credits(title: str) -> tuple[str, list[str]]:
    """(title without its featured-artist credits, the artists they name):
    "Billionaire ft. Bruno Mars" -> ("Billionaire", ["Bruno Mars"]). A title
    that is nothing but a credit, or names nobody, comes back unchanged."""
    names = feat_credits(title)
    if not names:
        return title, []
    bare = _BRACKET_RE.sub(lambda m: "" if _FEAT_WORD_RE.match(m.group(1).strip() + " ") else m.group(0), title)
    bare = " ".join(_UNBRACKETED_FEAT_RE.sub(" ", bare).split())
    return (bare, names) if bare else (title, [])


def missing_feat_artists(group: list) -> list[str]:
    """The featured artists the other titles of a duplicate group credit and
    the kept song (`group[0]`) has neither in its title nor among its
    artists - to offer crediting them before its title wins."""
    kept, *dropped = group
    known = {normalize(a.name) for a in song_all_artists(kept)} | {normalize(n) for n in feat_credits(kept.title)}
    missing = []
    for song in dropped:
        for name in feat_credits(song.title):
            if normalize(name) not in known:
                known.add(normalize(name))
                missing.append(name)
    return missing


def credit_feat_artists(session, song: Song, names: list) -> None:
    """Credit each name as a featured artist of `song`: the artist of that
    name if the database has one, else a new artist. Caller commits."""
    from utils.discoveries.import_engine import find_matching_artist

    for name in names:
        artist = find_matching_artist(name) or Artist(name=name)
        if artist.id is None:
            session.add(artist)
        try:
            add_additional_artist(song, artist, ADDITIONAL_ARTIST_ROLE_FEAT)
        except ValueError:
            pass  # already credited


def spelling_key(title: str) -> str:
    """Only spelling (case, punctuation, apostrophes) differs between titles
    with the same key: those are duplicates for certain."""
    return normalize(title)


def _filled(song: Song) -> int:
    return sum(1 for f in (*_FILLED_FIELDS, "genre", "title_latin") if getattr(song, f))


def song_to_keep(group: list) -> Song:
    """The song with the most data filled in, the oldest on a tie."""
    return max(group, key=lambda s: (_filled(s), -s.id))


def looks_like_versions(group: list) -> bool:
    """A title in the group naming a version ("remix", "live", ...) the
    others don't - worth a second look before merging."""
    words = [set(normalize(s.title).split()) & DUPLICATE_VERSION_WORDS for s in group]
    return any(w != words[0] for w in words)


def find_duplicate_groups(session, key=duplicate_key) -> list[list]:
    """Groups of two or more songs of one artist and kind with the same
    `key` (duplicate_key / spelling_key), the song to keep first; by artist."""
    groups = defaultdict(list)
    for song in session.query(Song).order_by(Song.id).all():
        groups[(song.artist_id, song.kind, key(song.title))].append(song)
    found = []
    for group in groups.values():
        if len(group) > 1:
            kept = song_to_keep(group)
            found.append([kept, *[s for s in group if s is not kept]])
    return sorted(found, key=lambda g: ((g[0].artist.name or "").lower(), g[0].title.lower()))


def merge_songs(session, kept: Song, dropped: Song) -> None:
    """Fold `dropped` into `kept` (whose title loses junk-only brackets,
    clean_title()): its fields the kept song lacks, its Latin
    form and genre, its extra artists; references to its id move to `kept`
    (tags, playlists, library files, once the caller commits and runs
    song_references.apply_song_id_changes())."""
    kept.title = clean_title(kept.title)
    for field in _FILLED_FIELDS:
        if not getattr(kept, field) and getattr(dropped, field):
            setattr(kept, field, getattr(dropped, field))
    keep_latin_on_merge(kept, dropped)
    keep_genre_on_merge(kept, dropped)
    for artist in additional_artists(dropped):
        try:
            add_additional_artist(kept, artist, additional_artist_role(dropped, artist))
        except ValueError:
            pass  # already credited on the kept song
    record_song_merge(session, dropped.id, kept.id)
    session.delete(dropped)
