"""Users -> Enter user database -> "Create playlist from folder and compare
with YT playlist": the folder's mp3s (folder_playlist.build_folder_playlist())
paired with the videos of a YouTube playlist that should hold the same songs,
so every track gets the playlist's link. Pairs come from the same database
song (each video's parsed "Artist - Title" matched like a file), from a
song's own link, or from resembling artist + title (the file's tags, or its
name when that fits a video better); the user pairs the rest
by hand, from the unpaired ones only. A video left without a file can become
a track of its own, matched to the database like a file. Nothing is written
here either; video_links() says which videos the saved playlist may give
its songs (utils/database/additional_videos.py)."""

from dataclasses import dataclass

from config.constants import SPELLING_CHECK_THRESHOLD
from utils.common.normalizer import extract_unknown_data, name_key, normalize
from utils.common.sanitize import SOURCE_FILE_NAME, sanitize_raw
from utils.common.song_entry import as_entry
from utils.common.text_utils import scaled_similarity_threshold, similarity, title_resembles
from utils.discoveries.folder_playlist import FolderPlaylist, FolderTrack, assign_song, match_track, song_suggestions
from utils.discoveries.import_engine import LocalSongMatcher
from utils.youtube.import_from_playlist import is_placeholder_title, playlist_item_metadata

# Pairs found through the database outrank any by name
_SAME_SONG_SCORE = 10.0
# A file with no artist and a video with one, the same title: below any
# pair whose artists resemble too; only one video of the playlist may have
# it (pair_automatically()), which is what makes a short title enough
_TITLE_ONLY_SCORE = 1.5


@dataclass
class YouTubeEntry:
    video_id: str
    label: str           # the video's title as YouTube shows it
    channel: str
    metadata: dict       # import_from_playlist.playlist_item_metadata()
    song: object | None  # the database Song its artist/title match exactly
    duration_s: int | None = None


def youtube_entries(matcher: LocalSongMatcher, items: list) -> list[YouTubeEntry]:
    """The playlist's videos (import_from_playlist.get_playlist_items()), in
    order, without the deleted / private ones and those giving neither an
    artist nor a title (no title at all), which have nothing to pair."""
    entries = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not title or is_placeholder_title(title):
            continue
        metadata = as_entry(playlist_item_metadata(item))
        if not metadata.artist and not metadata.title:
            continue
        song = matcher.match_exact(metadata) if metadata.title else None
        duration = item.get("duration")
        entries.append(YouTubeEntry(video_id=item["video_id"], label=title, channel=item.get("channel") or "",
                                    metadata=metadata, song=song,
                                    duration_s=round(duration) if duration else None))
    return entries


def _artists_resemble(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    # "A" vs "A feat. B" - as whole words, so "Ka" isn't found in "Kayah"
    if name_key(a) == name_key(b) or f" {na} " in f" {nb} " or f" {nb} " in f" {na} ":
        return True
    return similarity(na, nb) >= scaled_similarity_threshold(na, nb, SPELLING_CHECK_THRESHOLD)


def pair_score(track: FolderTrack, entry: YouTubeEntry) -> float | None:
    """How surely `track` is `entry`'s video; None = not without the user."""
    if track.song_id is not None and entry.song is not None and track.song_id == entry.song.id:
        return _SAME_SONG_SCORE
    if track.youtube_video_id and track.youtube_video_id == entry.video_id:
        return _SAME_SONG_SCORE
    scores = [score for artist, title in _readings(track)
              if (score := _name_score(artist, title, entry)) is not None]
    return max(scores, default=None)


def _readings(track: FolderTrack) -> list[tuple]:
    """(artist, title) of `track` to pair it by: its tags (or its song's),
    then its file name, which often names the song when the tags don't
    ("Agnieszka Chylińska - Winna.mp3" tagged "Track 05")."""
    readings = [(track.artist, track.title)]
    if track.file is not None:
        artist, title = extract_unknown_data(track.file)
        name = (artist, title) if title else (None, sanitize_raw(track.file.stem, SOURCE_FILE_NAME).strip() or None)
        if name[1] and name not in readings:
            readings.append(name)
    return readings


def _name_score(artist: str | None, title: str | None, entry: YouTubeEntry) -> float | None:
    """pair_score() of one reading of a file by its names."""
    title, artist = title or "", artist or ""
    entry_title, entry_artist = entry.metadata.title or "", entry.metadata.artist or ""
    if title and entry_title and title_resembles(title, entry_title) and _artists_resemble(artist, entry_artist):
        return 1 + similarity(normalize(title), normalize(entry_title)) \
            + similarity(normalize(artist), normalize(entry_artist))
    # No artist in the file's tags or name, while the video has one (often
    # only its channel: "Czlowiek Wozkow" on "Voo Voo")
    if not artist and entry_artist and name_key(title) and name_key(title) == name_key(entry_title):
        return _TITLE_ONLY_SCORE
    # The video's title parsed wrong (swapped, no separator): both still in it
    raw = normalize(f"{entry.label} {entry.channel}")
    if normalize(title) and normalize(artist) and normalize(title) in raw and normalize(artist) in raw:
        return 1.0
    return None


def link_track(track: FolderTrack, entry: YouTubeEntry, matcher: LocalSongMatcher | None = None,
               by_hand: bool = False) -> None:
    """Pair `track` with `entry` (`by_hand`: the user picked the video): the
    video becomes its link, and a song the video matched while the file
    didn't is suggested for the file - with how well the file's tags fit
    it, given the `matcher`. A file with no artist takes the video's."""
    entry_artist = entry.metadata.artist
    if track.song_id is None and not track.artist and entry_artist:
        track.artist, track.artist_from_video = entry_artist, True
    track.link_sure = by_hand or pair_score(track, entry) == _SAME_SONG_SCORE
    track.link_song_id = entry.song.id if entry.song is not None else None
    track.youtube_link = track.youtube_video_id = entry.video_id
    if track.song_id is None and entry.song is not None:
        track.similar_song = entry.song
        if matcher is not None:
            track.scores[entry.song.id] = max(
                matcher.match_score({"artist_name": artist, "title": title}, entry.song)
                for artist, title in _readings(track))
    if track.duration_s is None:
        track.duration_s = entry.duration_s


def pair_automatically(tracks: list, entries: list) -> list[tuple]:
    """(track, entry) pairs found without the user, best first; each track
    and video in at most one."""
    scored = []
    for t, track in enumerate(tracks):
        found = [(score, t, e) for e, entry in enumerate(entries)
                 if (score := pair_score(track, entry)) is not None]
        # A title alone names no video when several have it
        title_only = [s for s in found if s[0] == _TITLE_ONLY_SCORE]
        if len(title_only) > 1:
            found = [s for s in found if s[0] != _TITLE_ONLY_SCORE]
        scored += found
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    pairs, used_tracks, used_entries = [], set(), set()
    for _, t, e in scored:
        if t in used_tracks or e in used_entries:
            continue
        used_tracks.add(t)
        used_entries.add(e)
        pairs.append((tracks[t], entries[e]))
    return pairs


def rank_entries_for(track: FolderTrack, entries: list) -> list:
    """The unpaired videos to offer for `track`, the most like it first."""
    wanted = [normalize(f"{artist or ''} {title or ''}") for artist, title in _readings(track)]
    return sorted(entries, key=lambda e: -max(similarity(w, normalize(f"{e.channel} {e.label}")) for w in wanted))


def search_entries(entries: list, query: str) -> list:
    """The videos whose title or channel holds every word of `query`
    (normalized, so case and diacritics don't matter), in their order."""
    words = normalize(query).split()
    if not words:
        return []
    return [e for e in entries if all(w in normalize(f"{e.label} {e.channel or ''}") for w in words)]


def swap_artist_and_title(matcher: LocalSongMatcher, entry: YouTubeEntry) -> None:
    """The user said `entry`'s video was read backwards ("RED HANDS - Walk
    off the Earth"): read it the other way, and match its song again."""
    entry.metadata = entry.metadata.copy_with(artist=entry.metadata.title, title=entry.metadata.artist,
                                              unsplit_title=None)
    entry.song = matcher.match_exact(entry.metadata)


def closest_songs(matcher: LocalSongMatcher, entry: YouTubeEntry, best_guess_min_score: float) -> list[tuple]:
    """(song, score) the review of a video added without a file will offer,
    to show while the user decides whether to add it."""
    metadata = entry.metadata
    if entry.song is not None or not metadata.title:
        return []
    similar, candidates, guesses = song_suggestions(matcher, metadata, best_guess_min_score)
    return [(song, matcher.match_score(metadata, song))
            for song in ([similar] if similar else candidates or [song for song, _ in guesses])]


def youtube_only_track(playlist: FolderPlaylist, matcher: LocalSongMatcher, entry: YouTubeEntry,
                       min_artist_similarity: float = 1.1, best_guess_min_score: float = 1.1,
                       auto_accept_score: float = 1.1) -> FolderTrack:
    """A track for a video no file was paired with, matched to the
    database like a file (folder_playlist.match_track())."""
    track = FolderTrack(file=None, title=entry.metadata.title or entry.label,
                        artist=entry.metadata.artist, duration_s=entry.duration_s)
    track.youtube_link = track.youtube_video_id = entry.video_id
    track.link_song_id = entry.song.id if entry.song is not None else None
    if entry.song is not None:
        assign_song(track, entry.song)
    else:
        match_track(playlist, matcher, track,
                    {**entry.metadata, "title": track.title, "_label": entry.label}, min_artist_similarity,
                    best_guess_min_score, auto_accept_score)
    return track


def set_tracks(playlist: FolderPlaylist, tracks: list) -> None:
    """Make `tracks` the playlist, in that order; the files left out of it
    drop out of its unmatched / auto-accepted lists too."""
    kept = {id(t) for t in tracks}
    playlist.tracks = list(tracks)
    playlist.unmatched = [t for t in playlist.unmatched if id(t) in kept]
    playlist.auto_accepted = [(t, a) for t, a in playlist.auto_accepted if id(t) in kept]
    playlist.auto_accepted_scored = [entry for entry in playlist.auto_accepted_scored if id(entry[0]) in kept]


def video_links(tracks: list) -> list[tuple]:
    """(song id, video id, sure) of every track with both a song and a video
    of the YouTube playlist, for additional_videos.plan_video_links(). Sure:
    the video itself matched that song, the pairing was certain, or, for a
    track with no file, the user picked its song (then the video is what
    they matched)."""
    return [(t.song_id, t.youtube_link,
             t.link_sure or t.link_song_id == t.song_id or (t.file is None and t.picked))
            for t in tracks if t.song_id is not None and t.youtube_link]
