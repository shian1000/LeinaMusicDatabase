"""
Songs Leina Radio asked to have added (the `song_requests` table of a request
file, app handoff docs/handoffs/musicdatabase-song-requests.md, answer
docs/handoffs/song-requests-for-musicdatabaseapp.md): a track the phone
played that music.db doesn't have - a YouTube video, a Spotify track or a file
on the phone - by its title and artist as the phone showed them. Nothing here
asks anything (ADR-0001); the review is utils/ui/inbox_request_review.py.

Adding a song was never automatic (the owner's decision, 2026-10-18) until
the owner's own rules (2026-11-13): the automatic pass (inbox_auto.py) adds
one only when a rule of the owner's for "song_request" (Settings ->
Automation -> "My rules") holds for its facts (song_request_facts()). Else
the owner adds it (through the import, run_import_batch(), which looks it up
like any other import), skips it for later, or rejects it. A rejected song goes to
music.db's rejected_song_requests (migration 0015), which the phone reads, so
it doesn't ask for that song again. A song found in the database or added goes
to settled_song_requests (migration 0024) with the song's id, one row per way
the phone named it: Leina Radio recognises its track as that song from the
next copy on even when nothing else connects them (a file on the phone with
no video, names its matcher can't reconcile), and a later request matching a
row is settled as that song without matching again.

Cases: one per song, by normalized artist + title, from every report of
every file. Settled without asking (the automatic pass too):
- the song is in the database already (the phone's music.db was older, or
  the phone didn't recognise it) - LocalSongMatcher, or its YouTube video is
  a song's link; the request's videos become that song's (its link when it
  has none, else additional videos, as plan_video_links() allows), so Leina
  Radio recognises them from the next copy on (owner's choice, 2026-11-17:
  "Renata Przemyk - Aniolek" was settled so, its video kept nowhere, and
  the playlist holding it showed the song as not in the database);
- it was rejected before (a rejected_song_requests row with the same artist +
  title, YouTube video or Spotify track);
- it was settled before (a settled_song_requests row matching so) - as that
  song.

A request may carry a YouTube video's channel as the artist and the whole
video title as the title ("familyboombox" / "Бумбокс - Твiй на 100%"; Leina
Radio's YouTube search results did that), or a label's channel ("Fueled By
Ramen" / "Travie McCoy: Billionaire ft. Bruno Mars"). When the title is
"<artist> - <title>" or "<artist>: <title>" and the request's artist is none
the database knows (or none at all), the case suggests the split
(`suggested`), the artist from the title known to the database or not; when
the request's artist is a known one, only if the title names that same
artist (a synonym, as with familyboombox). A title naming no artist with a
channel that is one of a known artist's other names ("familyboombox" /
"Ангела (feat. Крихітка)") suggests that artist as the database spells it.
A title with no separator at all that starts with artists the database
knows by exactly that name, joined by "і" / "&" / "x" / ... ("фан Тіна
кароль" / "Бумбокс і Тіна Кароль Безодня" -> "Бумбокс & Тіна Кароль" /
"Безодня") suggests that split too; an artist the database doesn't know
counts only when the channel's name holds it (here "Тіна Кароль"), and at
least one must be known - where an unknown name ends is a guess otherwise.
A request with no artist at all (a file on the phone whose tags have none,
named "Title Artist") whose title ends with an artist the database knows by
exactly that name ("Arriving somewhere but not here Porcupine Tree") suggests
that split as well - only then, since a channel's own name is the likelier
artist. A whole title that is an artist the database knows by exactly that
name, with an artist it doesn't know, suggests the two swapped. The review offers it first, and the "already in the database" check uses it
too.

A YouTube request whose artist is a generic Topic channel ("Release" - YouTube
files an auto-generated upload there when it can't attach it to an artist's
channel) has the real artists only in the video's description, on a
"<Title> · <Artist> · <Artist 2>" line (Leina Radio sends just the bare
title). When nothing else settles such a case, the description is fetched
(yt-dlp) and each credited artist is suggested as the main one, the others
as featured in the title (`suggested`, then `also_suggested`): the one with
most songs in the database first, since the description's order isn't the
main artist first ("Śniadanie · Natalia Przybysz · Fisz Emade Tworzywo").
Each is spelt as the database spells it, and a name it doesn't know that is
known artists side by side is split into them ("Fisz Emade Tworzywo" ->
"Fisz / Emade / Tworzywo Sztuczne"). The rejected-before and "already in the
database" checks try every suggestion.

A YouTube request whose title is only an upload annotation ("Glow" /
"Lyric Video (English / French)": Leina Radio split "Glow - Lyric Video
(English / French)" as "Artist - Title", the artist being only the
channel, "Attic Ocean") gets the video's title and channel fetched (yt-dlp)
and read the way the playlist import reads them (build_metadata_from_item()),
suggesting "Attic Ocean" / "Glow". Also only when nothing else settles it.
"""

import re
from dataclasses import dataclass, field
from functools import cache

from config.constants import (
    ADDITIONAL_VIDEO_IMPORT,
    ARTIST_JOINING_WORDS,
    GENERIC_TOPIC_CHANNELS,
    SONG_REQUEST_COLON_ARTIST_MAX_WORDS,
    SONG_REQUEST_CREDIT_SPLIT_MAX_WORDS,
    SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS,
    SONG_REQUEST_TITLE_ARTIST_MAX_WORDS,
)
from utils.common.debug import flog, mlog
from utils.common.normalizer import name_key, normalize
from utils.common.version_names import read_version
from utils.database.additional_videos import apply_video_links, plan_video_links, song_for_video
from utils.database.datatables import AdditionalSongArtist, RejectedSongRequest, SettledSongRequest, Song
from utils.database.latin_names import artist_synonyms
from utils.database.inbox_requests import (
    DECIDED,
    OPEN,
    RequestFile,
    SongRequestReport,
    now_iso,
    parse_time,
    save_decision,
)
from utils.common.sanitize import SOURCE_REQUEST, sanitize_metadata
from utils.common.song_entry import SongEntry

# Settled statuses of their own
ALREADY_IN_DATABASE = "already in the database"
REJECTED_BEFORE = "rejected before"

# Where a suggestion comes from (shown in the review)
SUGGESTED_FROM_TITLE = "the title starts with the artist"
SUGGESTED_FROM_SWAP = "the title is a known artist's name"
SUGGESTED_FROM_DESCRIPTION = "credited in the YouTube video's description"
SUGGESTED_FROM_CHANNEL = "the YouTube video's title and channel"

# Answers
ADD = "added"
REJECT = "rejected"

# What separates the artist from the title inside a video's title
_TITLE_ARTIST_SEPARATOR = re.compile(r"\s+[-–—]\s+")
# ... or, when there's no dash, a colon right after the artist
_TITLE_ARTIST_COLON = re.compile(r":\s+")
# ... or, with neither, the words of a title starting with artists ("," apart)
_TITLE_WORD = re.compile(r"[^\s,]+|,")
_BRACKETED = re.compile(r"\([^)]*\)?|\[[^\]]*\]?")
_TITLE_ARTIST_JOINERS = {*ARTIST_JOINING_WORDS, "&", "x", ",", "+", "/", "vs", "vs."}

SOURCE_LABELS = {"youtube": "YouTube", "spotify": "Spotify", "phone_file": "a file on the phone"}


def song_key(artist: str | None, title: str | None) -> str:
    # A comparison key of the names as sent, never sanitize()d: it groups the
    # requests, finds rejected / settled_song_requests rows (stored as sent) and, as
    # "song:<key>", is the case id decisions are saved under - a cleanup
    # step turned on later must not change it
    return f"{normalize(artist or '')}|{normalize(title or '')}"


@dataclass
class SongRequestCase:
    """Every request for one song (same artist and title)."""
    reports: list
    status: str = OPEN
    existing: Song | None = None  # the song it already is, for ALREADY_IN_DATABASE
    videos_saved: tuple = (0, 0)  # (own links set, additional videos added) on `existing`
    auto_note: str | None = None  # why the automatic pass left it to the owner
    suggested: tuple | None = None  # (artist, title) read out of the title or the description, see the module docstring
    also_suggested: list = field(default_factory=list)  # the description's other readings, (artist, title) each
    suggested_from: str | None = None  # SUGGESTED_FROM_TITLE / _DESCRIPTION / _CHANNEL

    @property
    def suggestions(self) -> list[tuple]:
        return ([self.suggested] if self.suggested else []) + self.also_suggested

    @property
    def pending(self) -> bool:
        return self.status == OPEN

    @property
    def key(self) -> str:
        return f"song:{song_key(self.latest.artist, self.latest.title)}"

    @property
    def latest(self) -> SongRequestReport:
        """The newest report (its spelling is shown and suggested)."""
        def when(report):
            t = parse_time(report.requested_at)
            return t.timestamp() if t else float("-inf")
        return max(self.reports, key=when)

    def _first(self, attribute):
        """The newest report's value, else any report's."""
        for report in [self.latest, *self.reports]:
            value = getattr(report, attribute)
            if value not in (None, ""):
                return value
        return None

    @property
    def youtube_video_ids(self) -> list[str]:
        return list(dict.fromkeys(r.youtube_video_id for r in self.reports if r.youtube_video_id))

    @property
    def spotify_track_ids(self) -> list[str]:
        return list(dict.fromkeys(r.spotify_track_id for r in self.reports if r.spotify_track_id))

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(SOURCE_LABELS.get(r.source, r.source) for r in self.reports))

    def metadata(self, artist: str | None = None, title: str | None = None) -> SongEntry:
        """An import_engine metadata dict (run_import_batch()); `artist` /
        `title` as the owner corrected them, else the request's. A
        featured-artist credit leaves the title for `feat_artists`, which the
        import credits on the new song ("Billionaire ft. Bruno Mars" ->
        "Billionaire", Bruno Mars as feat) - sanitize's "request" steps
        (its feat_to_credits)."""
        latest = self.latest
        return sanitize_metadata(SongEntry({
            "artist_name": artist if artist is not None else latest.artist,
            "title": title if title is not None else latest.title or "",
            "feat_artists": [],  # filled from the title by the "request" cleanup
            "album": self._first("album"),
            "year": self._first("year"),
            "language": "Unknown",
            "origin": None,
            "youtube_video_id": self.youtube_video_ids[0] if self.youtube_video_ids else None,
            "duration_s": self._first("duration_s"),
        }, source=SOURCE_REQUEST, raw_text=latest.title, originals={
            field: value for field, value in (("artist", latest.artist), ("title", latest.title),
                                              ("album", latest.album)) if value is not None}), SOURCE_REQUEST)


def _request_rows(session, model) -> dict:
    """"name:<song_key()>", "video:<id>" and "spotify:<id>" of every row of
    rejected_song_requests / settled_song_requests (`model`) -> its song_id
    (None for a rejection); the oldest row of each stays."""
    rows = {}
    for row in session.query(model).order_by(model.id).all():
        song_id = getattr(row, "song_id", None)
        for key in _request_keys(row.artist, row.title, row.youtube_video_id, row.spotify_track_id):
            rows.setdefault(key, song_id)
    return rows


def _request_keys(artist, title, video_id=None, track_id=None) -> list[str]:
    return [f"name:{song_key(artist, title)}", *([f"video:{video_id}"] if video_id else []),
            *([f"spotify:{track_id}"] if track_id else [])]


def suggested_artist_title(artist: str | None, title: str | None, matcher) -> tuple | None:
    """(artist, title) when `title` is "<artist> - <title>" (or "<artist>:
    <title>", the artist part short) and `artist` is one the database doesn't
    know (a YouTube channel, a label) or the same artist as the title's:
    "familyboombox" (Бумбокс's synonym) / "Бумбокс - Твiй на 100%" ->
    ("Бумбокс", "Твiй на 100%"); "Fueled By Ramen" / "Travie McCoy:
    Billionaire ft. Bruno Mars" -> ("Travie McCoy", "Billionaire ft. Bruno
    Mars"). The title's artist is given as the database spells it when it
    knows it. A title with no artist in it keeps the title, with the artist
    as the database spells it when `artist` is one of its other names
    ("familyboombox" / "Ангела" -> ("Бумбокс", "Ангела")), unless it starts
    with known artists (_artists_at_start()), or - asked with no artist at
    all, a file on the phone named "Title Artist" - ends with one
    (_artist_at_end()), or is swapped with an unknown artist
    (_swapped_artist_title()). None otherwise - a
    known artist's channel uploading another artist's song is left as
    asked."""
    parts = _split_title(title or "")
    if parts is None:
        at_start = _artists_at_start(artist, title or "", matcher)
        if at_start is not None:
            return at_start
        at_end = None if artist else _artist_at_end(title or "", matcher)
        if at_end is not None:
            return at_end
        swapped = _swapped_artist_title(artist, title or "", matcher)
        if swapped is not None:
            return swapped
        known = _known_by_other_name(artist, matcher)
        return (known.name, title) if known is not None and title else None
    in_title = matcher.artist(parts[0])
    asked = matcher.artist(artist) if artist else None
    if asked is not None and (in_title is None or asked.id != in_title.id):
        return None
    return (in_title.name if in_title is not None else parts[0]), parts[1]


def _known_by_other_name(artist: str | None, matcher):
    """The database artist `artist` is a synonym or the Latin form of - not
    just its name spelt another way, and never a fuzzy guess - else None."""
    known = matcher.artist(artist) if artist else None
    if known is None or name_key(artist) == name_key(known.name):
        return None
    return known if name_key(artist) in {name_key(n) for n in artist_synonyms(known)} else None


def _artists_at_start(artist: str | None, title: str, matcher) -> tuple | None:
    """("A & B", title) when a title with no separator starts with artists
    joined by _TITLE_ARTIST_JOINERS, at least one known to the database by
    exactly that name (the rest may be in the channel's name `artist`):
    "фан Тіна кароль" / "Бумбокс і Тіна Кароль Безодня" -> ("Бумбокс & Тіна
    Кароль", "Безодня"). None when a joiner is followed by no artist it can
    tell ("Бумбокс і Тіна Кароль Безодня" with Тіна Кароль unknown to both),
    for one short lone artist, for nothing but a bracket left as the title,
    or when `artist` is another known artist."""
    words = list(_TITLE_WORD.finditer(title))
    names, known, i = [], [], 0
    while True:
        found = _artist_words(words, i, title, artist, matcher)
        if found is None:
            return None
        i, name, db_artist = found
        names.append(name)
        if db_artist is not None:
            known.append(db_artist)
        if words[i].group().lower() not in _TITLE_ARTIST_JOINERS:
            break
        i += 1
        if i >= len(words):
            return None
    if not known or (len(names) == 1 and len(name_key(names[0])) < SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS):
        return None
    asked = matcher.artist(artist) if artist else None
    if asked is not None and asked.id not in {a.id for a in known}:
        return None
    rest = title[words[i].start():].strip()
    # "Lykke Li (La Vie d'Adèle)" with the song's title as the artist: no title left
    if not _BRACKETED.sub("", rest).strip():
        return None
    return " & ".join(names), rest


def _artist_at_end(title: str, matcher) -> tuple | None:
    """(artist, title) when a title with no separator ends with an artist the
    database knows by exactly that name: "Arriving somewhere but not here
    Porcupine Tree" -> ("Porcupine Tree", "Arriving somewhere but not here").
    The longest such run of words, leaving a word for the title; None for a
    short lone name ("Air") or nothing but a bracket left."""
    words = list(_TITLE_WORD.finditer(title))
    for i in range(max(1, len(words) - SONG_REQUEST_TITLE_ARTIST_MAX_WORDS), len(words)):
        span = title[words[i].start():]
        db_artist = _known_exactly(span, matcher)
        if db_artist is None or len(name_key(span)) < SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS:
            continue
        rest = title[:words[i].start()].strip().rstrip(",").strip()
        if not _BRACKETED.sub("", rest).strip():
            return None
        return db_artist.name, rest
    return None


def _swapped_artist_title(artist: str | None, title: str, matcher) -> tuple | None:
    """(title as the database's artist, artist) when the whole title is an
    artist the database knows by exactly that name and `artist` is one it
    doesn't: "Chciałem być" / "Krzysztof Krawczyk" -> ("Krzysztof Krawczyk",
    "Chciałem być"). None for a short lone name ("Air"), which too many songs
    are called, and for a generic Topic channel, which is no song title."""
    if not artist or is_generic_channel_artist(artist) or matcher.artist(artist) is not None:
        return None
    if len(name_key(title)) < SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS:
        return None
    db_artist = _known_exactly(title, matcher)
    return (db_artist.name, artist) if db_artist is not None else None


def _artist_words(words: list, i: int, title: str, channel: str | None, matcher) -> tuple | None:
    """(index after, name, database artist or None) of the longest run of
    words from `i` that a database artist is named exactly, else that the
    channel's name holds - always leaving a word for the title."""
    spans = [title[words[i].start():words[j - 1].end()]
             for j in range(min(len(words) - 1, i + SONG_REQUEST_TITLE_ARTIST_MAX_WORDS), i, -1)
             if words[i].group().lower() not in _TITLE_ARTIST_JOINERS
             and words[j - 1].group().lower() not in _TITLE_ARTIST_JOINERS]
    for span in spans:
        db_artist = _known_exactly(span, matcher)
        if db_artist is not None:
            return i + len(_TITLE_WORD.findall(span)), db_artist.name, db_artist
    channel_words = f" {normalize(channel or '')} "
    for span in spans:
        if (len(name_key(span)) >= SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS
                and f" {normalize(span)} " in channel_words):
            return i + len(_TITLE_WORD.findall(span)), span, None
    return None


def _known_exactly(name: str, matcher):
    """The database artist named `name` (or with it as a synonym / Latin
    form), not a fuzzy guess nor a part of a joined name - else None."""
    known = matcher.artist(name)
    if known is None:
        return None
    names = [known.name, getattr(known, "name_latin", None), *artist_synonyms(known)]
    return known if name_key(name) in {name_key(n) for n in names if n} else None


def _split_title(title: str) -> tuple[str, str] | None:
    """(artist part, title part) of a video title, by " - " first, else by
    ": " after at most SONG_REQUEST_COLON_ARTIST_MAX_WORDS words."""
    parts = _TITLE_ARTIST_SEPARATOR.split(title, maxsplit=1)
    if len(parts) < 2:
        parts = _TITLE_ARTIST_COLON.split(title, maxsplit=1)
        if len(parts) < 2 or len(parts[0].split()) > SONG_REQUEST_COLON_ARTIST_MAX_WORDS:
            return None
    left, right = parts[0].strip(), parts[1].strip()
    return (left, right) if left and right else None


def fetch_video_description(video_id: str) -> str | None:
    """The video's description (yt-dlp), None when it can't be had."""
    from utils.youtube.import_from_playlist import _fetch_video_description
    return _fetch_video_description(video_id)


# video id -> topic_description_credits() of its description, for this run
_credits_by_video: dict = {}


def is_generic_channel_artist(artist: str | None) -> bool:
    """Whether a request's artist is a generic Topic channel ("Release",
    "Release - Topic"), not an artist."""
    base = re.sub(r"\s*-\s*topic$", "", artist or "", flags=re.IGNORECASE).strip().lower()
    return base in GENERIC_TOPIC_CHANNELS


def _description_credits(case: SongRequestCase) -> list[str] | None:
    """The artists the description of the case's YouTube video credits, for
    a request by a generic Topic channel - only when its credit line names
    the requested title."""
    if not is_generic_channel_artist(case.latest.artist):
        return None
    from utils.youtube.import_from_playlist import topic_description_credits
    for video_id in case.youtube_video_ids:
        if video_id not in _credits_by_video:
            description = fetch_video_description(video_id)
            _credits_by_video[video_id] = topic_description_credits(description) if description else None
        credits = _credits_by_video[video_id]
        if credits and normalize(credits[0]) == normalize(case.latest.title or ""):
            return credits[1]
    return None


# video id -> its (artist, title) read by build_metadata_from_item(), for this run
_channel_readings: dict = {}


def fetch_video_info(video_id: str) -> dict | None:
    """The video's yt-dlp metadata, None when it can't be had."""
    from utils.youtube.import_from_playlist import fetch_video_info as fetch
    return fetch(video_id)


def _junk_only_request(case: SongRequestCase) -> bool:
    """Whether the case's title (or the title its suggestion split off) is
    only an upload annotation ("Lyric Video (English / French)")."""
    from utils.youtube.import_from_playlist import is_junk_only_title
    titles = [case.latest.title, *(s[1] for s in case.suggestions)]
    return any(is_junk_only_title(t) for t in titles)


def channel_suggestion(case: SongRequestCase) -> tuple | None:
    """(artist, title) of the case's YouTube video as the playlist import
    reads its title and channel, for a request whose title is junk only -
    None otherwise, or when the video's data can't be had or gives no
    artist and title."""
    if not case.youtube_video_ids or not _junk_only_request(case):
        return None
    from utils.youtube.import_from_playlist import build_metadata_from_item, is_junk_only_title
    for video_id in case.youtube_video_ids:
        if video_id not in _channel_readings:
            info = fetch_video_info(video_id)
            reading = None
            if info:
                metadata = build_metadata_from_item(
                    {"title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
                     "video_id": video_id})
                artist, title = metadata.artist, metadata.title
                if artist and title and not is_junk_only_title(title):
                    reading = (artist, title)
            _channel_readings[video_id] = reading
        if _channel_readings[video_id]:
            return _channel_readings[video_id]
    return None


def description_suggestions(session, credits: list[str], title: str, matcher) -> list[tuple]:
    """(artist, title) with each credited artist as the main one and the
    others featured in the title, the one with most songs in the database
    first (ties in the description's order). Names as the database spells
    them (_database_spelling())."""
    names = [_database_spelling(name, matcher) for name in credits]
    counts = [_song_count(session, matcher.artist(name)) for name in names]
    suggestions = []
    for i in sorted(range(len(names)), key=lambda i: -counts[i]):
        others = [name for j, name in enumerate(names) if j != i]
        suggestions.append((names[i], f"{title} (feat. {', '.join(others)})" if others else title))
    return suggestions


def _database_spelling(name: str, matcher) -> str:
    """`name` as the database knows it, else as the known artists it is
    side by side ("Fisz Emade Tworzywo" -> "Fisz / Emade / Tworzywo
    Sztuczne"), else as it is."""
    known = _known_exactly(name, matcher)
    if known is not None:
        return known.name
    parts = _known_side_by_side(name, matcher)
    return " / ".join(a.name for a in parts) if parts else name


def _known_side_by_side(name: str, matcher) -> tuple | None:
    """The database artists (_known_exactly()) whose names, one after
    another, are all of `name`'s words - as many as possible, so "Fisz Emade
    Tworzywo" is Fisz, Emade and Tworzywo Sztuczne even though "Fisz Emade"
    is an artist too. None when its words aren't all such names."""
    words = name.split()
    if not 2 <= len(words) <= SONG_REQUEST_CREDIT_SPLIT_MAX_WORDS:
        return None

    @cache
    def most_parts(i: int) -> tuple | None:
        if i == len(words):
            return ()
        best = None
        for j in range(i + 1, len(words) + 1):
            if (i, j) == (0, len(words)):
                continue
            artist = _known_exactly(" ".join(words[i:j]), matcher)
            rest = most_parts(j) if artist is not None else None
            if rest is not None and (best is None or len(rest) + 1 > len(best)):
                best = (artist, *rest)
        return best

    return most_parts(0)


def _song_count(session, artist) -> int:
    """How many songs credit `artist` (main or additional); 0 for None."""
    if artist is None:
        return 0
    return (session.query(Song).filter(Song.artist_id == artist.id).count()
            + session.query(AdditionalSongArtist).filter(AdditionalSongArtist.artist_id == artist.id).count())


def _song_in_database(session, case: SongRequestCase, matcher) -> Song | None:
    for video_id in case.youtube_video_ids:
        song = song_for_video(session, video_id)
        if song is not None:
            return song
    metadata = case.metadata()
    for suggestion in case.suggestions:
        song = matcher.match(case.metadata(*suggestion))
        if song is not None:
            return song
    if is_generic_channel_artist(metadata.artist):
        return None
    if not metadata.artist:
        return None
    song = matcher.match(metadata)
    # The phone cut the artist off a video title at its first dash, so a
    # second one may start a description: "K. Pronko" / "Jestes Lekiem Na
    # Cale Zlo - a great Polish song". Not when it names a version ("- Live"),
    # a project or a cover, which the title alone would lose
    title_part = _TITLE_ARTIST_SEPARATOR.split(metadata.title, maxsplit=1)[0]
    if song is None and title_part != metadata.title and not (
            read_version(metadata.title) or metadata.project or metadata.is_cover):
        song = matcher.match(metadata.copy_with(title=title_part))
    if song is None:
        # Known artists written side by side, "Fisz Emade": the song of one
        # of them - when only one has it
        found = {s.id: s for a in _known_side_by_side(metadata.artist, matcher) or ()
                 if (s := matcher.match(metadata.copy_with(artist=a.name)))}
        song = next(iter(found.values())) if len(found) == 1 else None
    return song


def collect_song_request_cases(session, requests: list[RequestFile], matcher=None,
                               fetch: bool = True) -> list[SongRequestCase]:
    """Group every supported file's song requests by song and settle what
    needs no answer (the videos of songs found in the database saved on
    them, see _settle(); the caller commits). Order: first report first. `matcher`: a
    LocalSongMatcher to use (tests); made here otherwise. `fetch` = False
    asks YouTube nothing (a video's description or title and channel)."""
    cases: dict[str, SongRequestCase] = {}
    for request in requests:
        if not request.supported:
            continue
        for report in request.song_requests:
            key = song_key(report.artist, report.title)
            cases.setdefault(key, SongRequestCase([])).reports.append(report)
    if not cases:
        return []

    if matcher is None:
        from utils.discoveries.import_engine import LocalSongMatcher
        matcher = LocalSongMatcher(session)
    known = _request_rows(session, RejectedSongRequest), _request_rows(session, SettledSongRequest)
    for key, case in cases.items():
        case.suggested = suggested_artist_title(case.latest.artist, case.latest.title, matcher)
        if case.suggested:
            case.suggested_from = SUGGESTED_FROM_SWAP if case.suggested[1] == case.latest.artist else SUGGESTED_FROM_TITLE
        if _settle(session, case, known, matcher) or not fetch:
            continue
        # Only now, the description fetch being slow
        credits = _description_credits(case)
        if credits:
            case.suggested, *case.also_suggested = description_suggestions(
                session, credits, case.latest.title, matcher)
            case.suggested_from = SUGGESTED_FROM_DESCRIPTION
            _settle(session, case, known, matcher)
            continue
        reading = channel_suggestion(case)
        if reading:
            case.suggested, case.also_suggested = (_database_spelling(reading[0], matcher), reading[1]), []
            case.suggested_from = SUGGESTED_FROM_CHANNEL
            _settle(session, case, known, matcher)
    return list(cases.values())


def _settle(session, case: SongRequestCase, known: tuple, matcher) -> bool:
    """Settle the case if it was rejected before, settled before or is in
    the database already (by any of its suggestions too; it then goes to
    settled_song_requests and its videos are saved on that song, flushed -
    the caller commits); whether it was. `known`: _request_rows() of
    rejected_song_requests and settled_song_requests."""
    rejected, settled = known
    keys = [*(k for r in case.reports for k in _request_keys(r.artist, r.title, r.youtube_video_id,
                                                                r.spotify_track_id)),
            *(f"name:{song_key(*s)}" for s in case.suggestions)]
    if any(k in rejected for k in keys):
        case.status = REJECTED_BEFORE
        return True
    case.existing = (next((song for k in keys if k in settled and (song := session.get(Song, settled[k]))), None)
                     or _song_in_database(session, case, matcher))
    if case.existing is not None:
        case.status = ALREADY_IN_DATABASE
        record_settled_request(session, case, case.existing.id)
        plan = plan_video_links(session, [(case.existing.id, v, False) for v in case.youtube_video_ids])
        case.videos_saved = apply_video_links(session, plan.links, ADDITIONAL_VIDEO_IMPORT)
        return True
    return False


def record_settled_request(session, case: SongRequestCase, song_id: int) -> None:
    """The case is song `song_id`: in settled_song_requests, one row per
    distinct way the phone named it not there yet. The caller commits."""
    stored = {(r.artist, r.title, r.youtube_video_id, r.spotify_track_id): r
              for r in session.query(SettledSongRequest).filter(
                  SettledSongRequest.title.in_({r.title for r in case.reports}))}
    for report in case.reports:
        row = (report.artist, report.title, report.youtube_video_id, report.spotify_track_id)
        if row in stored:
            stored[row].song_id = song_id
            continue
        stored[row] = SettledSongRequest(artist=report.artist, title=report.title,
                                         youtube_video_id=report.youtube_video_id,
                                         spotify_track_id=report.spotify_track_id, song_id=song_id,
                                         settled_at=now_iso())
        session.add(stored[row])


SETTLED_REQUESTS_MIGRATION = "0024_settled_song_requests.sql"


def backfill_settled_requests(session, requests: list[RequestFile], decisions: dict, matcher=None) -> int:
    """settled_song_requests for requests settled before it existed
    (migration 0024; main.py runs it once, for Inbox/processed/): the cases
    collect_song_request_cases() settles now (YouTube not asked), and those
    added then - as the decision recorded it (its song_id, else the song its
    `added_as` names now). How many cases went in; the caller commits."""
    if matcher is None:
        from utils.discoveries.import_engine import LocalSongMatcher
        matcher = LocalSongMatcher(session, load_artists_once=True)
    cases = collect_song_request_cases(session, requests, matcher, fetch=False)
    settled = 0
    for case in cases:
        if case.status == ALREADY_IN_DATABASE:
            settled += 1
            continue
        added = (decisions.get(case.key) or {}).get("added_as") if case.pending else None
        if not added or not added.get("title"):
            continue
        song = (session.get(Song, added["song_id"]) if added.get("song_id")
                else matcher.match(case.metadata(added.get("artist"), added["title"])))
        if song is not None:
            record_settled_request(session, case, song.id)
            settled += 1
    return settled


def run_settled_requests_backfill() -> None:
    """Startup hook (main.py), run once right after migration 0024 lands."""
    from utils.database.inbox_requests import PROCESSED, _request_paths, inbox_available, load_decisions, read_request_file
    from utils.database.music_db_manager import get_music_session

    if not inbox_available():
        flog("settled_song_requests: the inbox isn't there (data drive), nothing backfilled")
        return
    session = get_music_session()
    try:
        requests = [read_request_file(path) for path in _request_paths(PROCESSED)]
        count = backfill_settled_requests(session, requests, load_decisions())
        session.commit()
        mlog(f"settled_song_requests: {count} song request(s) Leina Radio sent earlier recorded as their songs")
    except Exception as exc:
        session.rollback()
        flog(f"settled_song_requests: backfill failed: {exc!r}")
    finally:
        session.close()


def videos_saved_text(case: SongRequestCase) -> str:
    """What _settle() saved of the request's videos, as shown; "" for nothing."""
    main, added = case.videos_saved
    return ", ".join((["its video saved as the song's link"] if main else [])
                     + ([f"{added} video(s) saved as the song's additional videos"] if added else []))


def reject_song_request(session, case: SongRequestCase) -> None:
    """The owner doesn't want the song: published in rejected_song_requests,
    one row per distinct way the phone named it. The caller commits."""
    seen = set()
    for report in case.reports:
        row = (report.artist, report.title, report.youtube_video_id, report.spotify_track_id)
        if row in seen:
            continue
        seen.add(row)
        session.add(RejectedSongRequest(artist=report.artist, title=report.title,
                                        youtube_video_id=report.youtube_video_id,
                                        spotify_track_id=report.spotify_track_id, rejected_at=now_iso()))
    _decided(case, REJECT, session=session)


def mark_song_added(session, case: SongRequestCase, artist: str | None, title: str, song_id: int,
                    by: str | None = None) -> None:
    """The import made song `song_id` of the request (added it, or found it
    there after all), as `artist` - `title`; only then - one it left out
    stays open. Recorded in settled_song_requests; the decision is saved
    when `session` commits (the caller commits). `by` = "auto" for the
    automatic pass."""
    record_settled_request(session, case, song_id)
    _decided(case, ADD, session=session, added_as={"artist": artist, "title": title, "song_id": song_id},
             **({"by": by} if by else {}))


def song_request_facts(case: SongRequestCase, artist: str, title: str, senders: dict, matcher,
                       sender_may_add: bool) -> dict:
    """What the owner's rules for adding a requested song compare
    (constants.AUTOMATION_QUESTIONS["song_request"]), for the song as it
    would be added (`artist` - `title`). Asks MusicBrainz (check_spelling(),
    cached on disk)."""
    from utils.common.text_utils import check_spelling

    spelling = check_spelling(artist, title)
    found = bool(spelling.get("found"))
    guesses = matcher.best_guesses(case.metadata(artist, title), 1, 0.0)
    users = {senders.get(r.file_name) for r in case.reports} - {None}
    return {
        "musicbrainz_found": found,
        "musicbrainz_title_similarity": spelling.get("title_similarity") if found else None,
        "musicbrainz_artist_similarity": spelling.get("artist_similarity") if found else None,
        "musicbrainz_score": spelling.get("mb_score") if found else None,
        "artist_known": _known_exactly(artist, matcher) is not None,
        "closest_song_score": round(guesses[0][1], 3) if guesses else 0.0,
        "has_youtube_video": bool(case.youtube_video_ids),
        "names_from": case.suggested_from if case.suggested == (artist, title) else "the request",
        "requests": len(case.reports),
        "users": len(users),
        "logged_in": senders.get(case.latest.file_name) is not None,
        "sender_may_add_songs": sender_may_add,
    }


def _decided(case: SongRequestCase, decision: str, session=None, **extra) -> None:
    save_decision(case.key, {
        "decision": decision,
        "artist": case.latest.artist,
        "title": case.latest.title,
        "decided_at": now_iso(),
        "files": sorted({r.file_name for r in case.reports}),
        **extra,
    }, session)
    case.status = DECIDED
    mlog(f"Inbox request: song {case.latest.artist} - {case.latest.title} -> {decision}")

