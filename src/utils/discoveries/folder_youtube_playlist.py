"""Users -> Enter user database -> "Create playlist from folder and compare
with YT playlist": the folder's mp3s (folder_playlist.build_folder_playlist())
paired with the videos of a YouTube playlist that should hold the same songs,
so every track gets the playlist's link. Pairs come from the same database
song (each video's parsed "Artist - Title" matched like a file), from a
song's own link, or from resembling artist + title; the user pairs the rest
by hand, from the unpaired ones only. A video left without a file can become
a track of its own, matched to the database like a file. Nothing is written
here either."""

from dataclasses import dataclass

from config.constants import SPELLING_CHECK_THRESHOLD
from utils.common.normalizer import name_key, normalize
from utils.common.text_utils import scaled_similarity_threshold, similarity, title_resembles
from utils.discoveries.folder_playlist import FolderPlaylist, FolderTrack, assign_song, match_track
from utils.discoveries.import_engine import LocalSongMatcher
from utils.youtube.import_from_playlist import is_placeholder_title, playlist_item_metadata

# Pairs found through the database outrank any by name
_SAME_SONG_SCORE = 10.0


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
        metadata = playlist_item_metadata(item)
        if not metadata.get("artist_name") and not metadata.get("title"):
            continue
        song = matcher.match_exact(metadata) if metadata.get("title") else None
        duration = item.get("duration")
        entries.append(YouTubeEntry(video_id=item["video_id"], label=title, channel=item.get("channel") or "",
                                    metadata=metadata, song=song,
                                    duration_s=round(duration) if duration else None))
    return entries


def _artists_resemble(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if name_key(a) == name_key(b) or na in nb or nb in na:  # "A" vs "A feat. B"
        return True
    return similarity(na, nb) >= scaled_similarity_threshold(na, nb, SPELLING_CHECK_THRESHOLD)


def pair_score(track: FolderTrack, entry: YouTubeEntry) -> float | None:
    """How surely `track` is `entry`'s video; None = not without the user."""
    if track.song_id is not None and entry.song is not None and track.song_id == entry.song.id:
        return _SAME_SONG_SCORE
    if track.youtube_video_id and track.youtube_video_id == entry.video_id:
        return _SAME_SONG_SCORE
    title, artist = track.title or "", track.artist or ""
    entry_title, entry_artist = entry.metadata.get("title") or "", entry.metadata.get("artist_name") or ""
    if title and entry_title and title_resembles(title, entry_title) and _artists_resemble(artist, entry_artist):
        return 1 + similarity(normalize(title), normalize(entry_title)) \
            + similarity(normalize(artist), normalize(entry_artist))
    # The video's title parsed wrong (swapped, no separator): both still in it
    raw = normalize(f"{entry.label} {entry.channel}")
    if normalize(title) and normalize(artist) and normalize(title) in raw and normalize(artist) in raw:
        return 1.0
    return None


def link_track(track: FolderTrack, entry: YouTubeEntry, matcher: LocalSongMatcher | None = None) -> None:
    """Pair `track` with `entry`: the video becomes its link, and a song the
    video matched while the file didn't is suggested for the file - with
    how well the file's tags fit it, given the `matcher`."""
    track.youtube_link = track.youtube_video_id = entry.video_id
    if track.song_id is None and entry.song is not None:
        track.similar_song = entry.song
        if matcher is not None:
            track.scores[entry.song.id] = matcher.match_score(
                {"artist_name": track.artist, "title": track.title}, entry.song)
    if track.duration_s is None:
        track.duration_s = entry.duration_s


def pair_automatically(tracks: list, entries: list) -> list[tuple]:
    """(track, entry) pairs found without the user, best first; each track
    and video in at most one."""
    scored = []
    for t, track in enumerate(tracks):
        for e, entry in enumerate(entries):
            score = pair_score(track, entry)
            if score is not None:
                scored.append((score, t, e))
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
    wanted = normalize(f"{track.artist or ''} {track.title or ''}")
    return sorted(entries, key=lambda e: -similarity(wanted, normalize(f"{e.channel} {e.label}")))


def search_entries(entries: list, query: str) -> list:
    """The videos whose title or channel holds every word of `query`
    (normalized, so case and diacritics don't matter), in their order."""
    words = normalize(query).split()
    if not words:
        return []
    return [e for e in entries if all(w in normalize(f"{e.label} {e.channel or ''}") for w in words)]


def youtube_only_track(playlist: FolderPlaylist, matcher: LocalSongMatcher, entry: YouTubeEntry,
                       min_artist_similarity: float = 1.1, best_guess_min_score: float = 1.1,
                       auto_accept_score: float = 1.1) -> FolderTrack:
    """A track for a video no file was paired with, matched to the
    database like a file (folder_playlist.match_track())."""
    track = FolderTrack(file=None, title=entry.metadata.get("title") or entry.label,
                        artist=entry.metadata.get("artist_name"), duration_s=entry.duration_s)
    track.youtube_link = track.youtube_video_id = entry.video_id
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
