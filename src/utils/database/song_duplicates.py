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
songs whose titles only differ in spelling without asking. The same review
also offers, unticked, one title credited to two main artists that share
another artist or a link (find_cross_artist_groups()); the kept song's main
artist stays, the dropped one's isn't carried over. Groups the owner left
unticked there can be remembered (data/song_duplicate_ignore.json) and aren't
offered again until another song joins them."""

import json
import re
from collections import defaultdict
from utils.common.artist_credits import (
    BRACKET_RE as _BRACKET_RE,
    FEAT_WORD_RE as _FEAT_WORD_RE,
    feat_credits,
    split_feat_credits,
    strip_feat_credits,
)

from config.constants import (
    ADDITIONAL_VIDEO_SONG_MERGE,
    ADDITIONAL_ARTIST_ROLE_FEAT,
    DUPLICATE_TITLE_EXTRA_JUNK,
    DUPLICATE_VERSION_WORDS,
    MERGE_CANDIDATE_MIN_PART,
    MERGE_CANDIDATES_MAX,
    NO_VIDEO_SENTINEL,
    SINGLES_ALBUM,
    SONG_DUPLICATE_IGNORE_FILE,
    TITLE_JUNK_SUFFIXES,
)
from utils.common.normalizer import normalize
from utils.common.text_utils import strip_trailing_feat
from utils.common.sanitize.text import strip_trailing_junk_suffix
from utils.database.additional_videos import add_additional_video
from utils.database.datatables import Artist, Song
from utils.database.genres import keep_genre_on_merge
from utils.database.latin_names import keep_latin_on_merge
from utils.common.artist_credits import propose_artist_split
from utils.database.song_artists import add_additional_artist, additional_artist_role, additional_artists, song_all_artists
from utils.database.song_references import record_song_merge

# Filled from the dropped song when the kept one has nothing there
_FILLED_FIELDS = ("album", "year", "language", "youtube_video_id", "spotify_url", "music_video_youtube_id",
                  "track_number", "disc_number", "project_id", "covered_artist_id", "covered_title")
_IGNORE_PATH = SONG_DUPLICATE_IGNORE_FILE
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
        artist = find_matching_artist(name, session) or Artist(name=name)
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
    """Groups of two or more songs of one artist, kind and project with the
    same `key` (duplicate_key / spelling_key), the song to keep first; by
    artist. A project's recording isn't a duplicate of the studio song."""
    groups = defaultdict(list)
    for song in session.query(Song).order_by(Song.id).all():
        groups[(song.artist_id, song.kind, song.project_id, key(song.title))].append(song)
    found = []
    for group in groups.values():
        if len(group) > 1:
            kept = song_to_keep(group)
            found.append([kept, *[s for s in group if s is not kept]])
    return sorted(found, key=lambda g: ((g[0].artist.name or "").lower(), g[0].title.lower()))


def _artist_names(song: Song) -> set[str]:
    """Every artist a song names: its artists and its title's feat. credits."""
    return {normalize(a.name) for a in song_all_artists(song)} | {normalize(n) for n in feat_credits(song.title)}


def _links(song: Song) -> set[str]:
    video = song.youtube_video_id if song.youtube_video_id != NO_VIDEO_SENTINEL else None
    return {link for link in (video, song.spotify_url) if link}


def _same_recording(a: Song, b: Song) -> bool:
    """Two songs of the same title are one when they share an artist
    ("Hasiok x Maciej Maleńczuk" / "Yugopolis x Maciej Maleńczuk") or a link."""
    return bool(_artist_names(a) & _artist_names(b)) or bool(_links(a) & _links(b))


def _cross_artist_song_to_keep(group: list) -> Song:
    """The song already naming every artist the others name ("Philip Bailey
    x Phil Collins" over "Phil Collins"), so no credit is lost; else the one
    on a real album over a "Singles" one (an upload credited to whoever put
    it up: "Hasiok" vs "Yugopolis" on "The Best Of Yugopolis"); then the one
    with the most data."""
    everyone = set().union(*(_artist_names(s) for s in group))
    complete = [s for s in group if _artist_names(s) >= everyone] or group
    return max(complete, key=lambda s: (bool(s.album) and s.album != SINGLES_ALBUM, _filled(s), -s.id))


def find_cross_artist_groups(session, skip_ids=frozenset()) -> list[list]:
    """Groups of songs with the same duplicate_key, kind and project but different
    main artists, joined by a shared artist or link - one song credited to
    two artist rows, usually one of them wrong; the song to keep first
    (_cross_artist_song_to_keep()). Never merged without asking: a title
    alone is too common. `skip_ids`: songs
    already in a same-artist group."""
    buckets = defaultdict(list)
    for song in session.query(Song).order_by(Song.id).all():
        if song.id not in skip_ids:
            buckets[(song.kind, song.project_id, duplicate_key(song.title))].append(song)
    found = []
    for songs in buckets.values():
        if len({s.artist_id for s in songs}) < 2:
            continue
        # Connected components: A~B and B~C put all three in one group
        parent = list(range(len(songs)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(songs)):
            for j in range(i + 1, len(songs)):
                if songs[i].artist_id != songs[j].artist_id and _same_recording(songs[i], songs[j]):
                    parent[root(j)] = root(i)
        components = defaultdict(list)
        for i, song in enumerate(songs):
            components[root(i)].append(song)
        for group in components.values():
            if len(group) > 1:
                kept = _cross_artist_song_to_keep(group)
                found.append([kept, *[s for s in group if s is not kept]])
    return sorted(found, key=lambda g: ((g[0].artist.name or "").lower(), g[0].title.lower()))


def _contains_words(longer: str, shorter: str) -> bool:
    """Is `shorter` (normalized, at least MERGE_CANDIDATE_MIN_PART characters)
    a whole-word part of `longer`?"""
    return len(shorter) >= MERGE_CANDIDATE_MIN_PART and f" {shorter} " in f" {longer} "


def merge_candidates(session, song: Song, limit: int = MERGE_CANDIDATES_MAX) -> list:
    """Songs `song` may be a wrongly read duplicate of, for deleting it:
    merging keeps its playlists, moods and files (song_references.song_uses()),
    deleting loses them. Best first: the same title by anyone; the title
    with the artist read as part of it ("Svefn" - "g-englar" is
    "Svefn-G-Englar"); on the same album, a title containing the other's
    ("May I Stand Unshaken" / "Unshaken") or artist and title swapped ("The
    caesars" by "Jerk it out" / "Jerk It Out" by "Caesars"). Read-only."""
    title, artist = duplicate_key(song.title), normalize(song.artist.name if song.artist else "")
    joined = {duplicate_key(f"{song.artist.name}{sep}{song.title}") for sep in ("-", " ")} if artist else set()
    album = normalize(song.album or "") if song.album and song.album.strip() != SINGLES_ALBUM else ""
    ranked = []
    for other in session.query(Song).filter(Song.id != song.id).order_by(Song.id):
        if other.kind != song.kind or other.project_id != song.project_id:
            continue
        other_title = duplicate_key(other.title)
        if other_title and other_title == title:
            ranked.append((0, other.id, other))
        elif other_title and other_title in joined:
            ranked.append((1, other.id, other))
        elif album and normalize(other.album or "") == album:
            other_artist = normalize(other.artist.name if other.artist else "")
            if (_contains_words(other_title, title) or _contains_words(title, other_title)
                    or (artist and artist == other_title) or (other_artist and other_artist == title)):
                ranked.append((2, other.id, other))
    return [other for _, _, other in sorted(ranked, key=lambda r: r[:2])][:limit]


def load_not_duplicates() -> list[frozenset[int]]:
    """The groups of song ids the owner said aren't one song."""
    try:
        return [frozenset(ids) for ids in json.loads(_IGNORE_PATH.read_text(encoding="utf-8"))]
    except (FileNotFoundError, ValueError, TypeError):
        return []


def remember_not_duplicates(groups: list) -> None:
    """Remember these groups (lists of Song) as different songs."""
    known = set(load_not_duplicates()) | {frozenset(s.id for s in g) for g in groups}
    _IGNORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IGNORE_PATH.write_text(json.dumps(sorted(sorted(ids) for ids in known)), encoding="utf-8")


def is_remembered_not_duplicate(group: list, remembered: list) -> bool:
    """Whether every song of `group` was in one remembered group - a group a
    new song joined (or a merge changed) is asked about again."""
    ids = {s.id for s in group}
    return any(ids <= known for known in remembered)


def merge_songs(session, kept: Song, dropped: Song) -> None:
    """Fold `dropped` into `kept` (whose title loses junk-only brackets,
    clean_title()): its fields the kept song lacks, its Latin
    form and genre, its extra artists, its link as an additional video when the kept song
    has another one; references to its id move to `kept`
    (tags, playlists, library files, once the caller commits and runs
    song_references.apply_song_id_changes())."""
    kept.title = clean_title(kept.title)
    for field in _FILLED_FIELDS:
        if not getattr(kept, field) and getattr(dropped, field):
            setattr(kept, field, getattr(dropped, field))
    keep_latin_on_merge(kept, dropped)
    keep_genre_on_merge(kept, dropped)
    # Its own link still names this recording: kept for recognising the video
    if dropped.youtube_video_id and dropped.youtube_video_id != kept.youtube_video_id:
        video_id, dropped.youtube_video_id = dropped.youtube_video_id, None
        add_additional_video(session, kept, video_id, ADDITIONAL_VIDEO_SONG_MERGE)
    for artist in additional_artists(dropped):
        try:
            add_additional_artist(kept, artist, additional_artist_role(dropped, artist))
        except ValueError:
            pass  # already credited on the kept song
    record_song_merge(session, dropped.id, kept.id)
    session.delete(dropped)
