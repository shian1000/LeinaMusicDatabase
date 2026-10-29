"""
Songs Leina Radio asked to have added (the `song_requests` table of a request
file, app handoff docs/handoffs/musicdatabase-song-requests.md, answer
docs/handoffs/song-requests-for-musicdatabaseapp.md): a track the phone
played that music.db doesn't have - a YouTube video, a Spotify track or a file
on the phone - by its title and artist as the phone showed them. Nothing here
asks anything (ADR-0001); the review is utils/ui/inbox_request_review.py.

Adding a song is never automatic (the owner's decision, 2026-10-18): the
owner adds it (through the import, run_import_batch(), which looks it up like
any other import), skips it for later, or rejects it. A rejected song goes to
music.db's rejected_song_requests (migration 0015), which the phone reads, so
it doesn't ask for that song again.

Cases: one per song, by normalized artist + title, from every report of
every file. Settled without asking (the automatic pass too):
- the song is in the database already (the phone's music.db was older, or
  the phone didn't recognise it) - LocalSongMatcher, or its YouTube video is
  a song's link;
- it was rejected before (a rejected_song_requests row with the same artist +
  title, YouTube video or Spotify track).

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
The review offers it first, and the "already in the database" check uses it
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
    ARTIST_JOINING_WORDS,
    SONG_REQUEST_COLON_ARTIST_MAX_WORDS,
    SONG_REQUEST_CREDIT_SPLIT_MAX_WORDS,
    SONG_REQUEST_LONE_TITLE_ARTIST_MIN_CHARS,
    SONG_REQUEST_TITLE_ARTIST_MAX_WORDS,
)
from utils.common.debug import mlog
from utils.common.normalizer import name_key, normalize
from utils.database.datatables import AdditionalSongArtist, RejectedSongRequest, Song
from utils.database.latin_names import artist_match_synonyms
from utils.database.inbox_requests import (
    DECIDED,
    OPEN,
    RequestFile,
    SongRequestReport,
    now_iso,
    parse_time,
    save_decision,
)
from utils.database.song_duplicates import split_feat_credits

# Settled statuses of their own
ALREADY_IN_DATABASE = "already in the database"
REJECTED_BEFORE = "rejected before"

# Where a suggestion comes from (shown in the review)
SUGGESTED_FROM_TITLE = "the title starts with the artist"
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
    return f"{normalize(artist or '')}|{normalize(title or '')}"


@dataclass
class SongRequestCase:
    """Every request for one song (same artist and title)."""
    reports: list
    status: str = OPEN
    existing: Song | None = None  # the song it already is, for ALREADY_IN_DATABASE
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

    def metadata(self, artist: str | None = None, title: str | None = None) -> dict:
        """An import_engine metadata dict (run_import_batch()); `artist` /
        `title` as the owner corrected them, else the request's. A
        featured-artist credit leaves the title for `feat_artists`, which the
        import credits on the new song ("Billionaire ft. Bruno Mars" ->
        "Billionaire", Bruno Mars as feat)."""
        title, feat_artists = split_feat_credits(title if title is not None else self.latest.title or "")
        return {
            "artist_name": artist if artist is not None else self.latest.artist,
            "title": title or None,
            "feat_artists": feat_artists,
            "album": self._first("album"),
            "year": self._first("year"),
            "language": "Unknown",
            "origin": None,
            "youtube_video_id": self.youtube_video_ids[0] if self.youtube_video_ids else None,
            "duration_s": self._first("duration_s"),
        }


def _rejected_rows(session) -> tuple[set, set, set]:
    """(artist+title keys, YouTube ids, Spotify ids) of rejected_song_requests."""
    keys, videos, tracks = set(), set(), set()
    for row in session.query(RejectedSongRequest).all():
        keys.add(song_key(row.artist, row.title))
        if row.youtube_video_id:
            videos.add(row.youtube_video_id)
        if row.spotify_track_id:
            tracks.add(row.spotify_track_id)
    return keys, videos, tracks


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
    with known artists (_artists_at_start()). None otherwise - a
    known artist's channel uploading another artist's song is left as
    asked."""
    parts = _split_title(title or "")
    if parts is None:
        at_start = _artists_at_start(artist, title or "", matcher)
        if at_start is not None:
            return at_start
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
    other_names = (artist_match_synonyms(known) or "").split(",")
    return known if name_key(artist) in {name_key(n) for n in other_names} else None


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
    names = [known.name, getattr(known, "name_latin", None), *(artist_match_synonyms(known) or "").split(",")]
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
    from utils.youtube.import_from_playlist import GENERIC_TOPIC_CHANNELS
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
                artist, title = metadata["artist_name"], metadata["title"]
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
        song = session.query(Song).filter(Song.youtube_video_id == video_id).first()
        if song is not None:
            return song
    metadata = case.metadata()
    for suggestion in case.suggestions:
        song = matcher.match(case.metadata(*suggestion))
        if song is not None:
            return song
    if is_generic_channel_artist(metadata["artist_name"]):
        return None
    if not metadata["artist_name"]:
        return None
    return matcher.match(metadata)


def collect_song_request_cases(session, requests: list[RequestFile], matcher=None) -> list[SongRequestCase]:
    """Group every supported file's song requests by song and settle what
    needs no answer. Order: first report first. `matcher`: a
    LocalSongMatcher to use (tests); made here otherwise."""
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
    rejected = _rejected_rows(session)
    for key, case in cases.items():
        case.suggested = suggested_artist_title(case.latest.artist, case.latest.title, matcher)
        case.suggested_from = SUGGESTED_FROM_TITLE if case.suggested else None
        if _settle(session, key, case, rejected, matcher):
            continue
        # Only now, the description fetch being slow
        credits = _description_credits(case)
        if credits:
            case.suggested, *case.also_suggested = description_suggestions(
                session, credits, case.latest.title, matcher)
            case.suggested_from = SUGGESTED_FROM_DESCRIPTION
            _settle(session, key, case, rejected, matcher)
            continue
        reading = channel_suggestion(case)
        if reading:
            case.suggested, case.also_suggested = (_database_spelling(reading[0], matcher), reading[1]), []
            case.suggested_from = SUGGESTED_FROM_CHANNEL
            _settle(session, key, case, rejected, matcher)
    return list(cases.values())


def _settle(session, key: str, case: SongRequestCase, rejected: tuple, matcher) -> bool:
    """Settle the case if it was rejected before or is in the database
    already (by any of its suggestions too); whether it was."""
    rejected_keys, rejected_videos, rejected_tracks = rejected
    if (key in rejected_keys or any(song_key(*s) in rejected_keys for s in case.suggestions)
            or set(case.youtube_video_ids) & rejected_videos
            or set(case.spotify_track_ids) & rejected_tracks):
        case.status = REJECTED_BEFORE
        return True
    case.existing = _song_in_database(session, case, matcher)
    if case.existing is not None:
        case.status = ALREADY_IN_DATABASE
        return True
    return False


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


def mark_song_added(case: SongRequestCase, artist: str | None, title: str) -> None:
    """The song went through the import (added, or found there after all),
    as `artist` - `title`."""
    _decided(case, ADD, added_as={"artist": artist, "title": title})


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

