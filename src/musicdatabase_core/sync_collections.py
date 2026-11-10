"""The collections a user's phones sync item by item - playlists, moods,
folders, saved songs and later links: the same GET / PUT / DELETE calls and
answers, the rules in each one's module. Both servers (sharing_server.py
and MDW's "sync" add-on) serve every collection from this list through one
handler, so a new collection is a row here."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from . import folder_sync, mood_sync, playlist_sync, saved_sync
from .server_rules import DEFAULT_MAX_PLAYLIST_BYTES, FOLDER_MAX_BODY_BYTES, SAVED_MAX_BODY_BYTES


@dataclass(frozen=True)
class Collection:
    path: str          # the URL segment: /users/<name>/<path>[/<uid>]
    label: str         # for messages and the server's log
    uid_re: re.Pattern
    max_body: int
    bad_error: type[Exception]
    connect: Callable
    changes_since: Callable
    parse: Callable
    store: Callable  # (conn, folder, username, uid, parsed, base revision, checked_to) -> (applied, stored)
    delete: Callable  # (conn, folder, uid, base revision, deleted_at) -> (applied, stored[, archived])
    resolve: Callable | None = None  # (parsed, music.db) -> checked_to, before the transaction
    uses_covers: bool = False
    counted: tuple[str, str] | None = None  # (stored's list key, its name) for the log


def _resolve_mood(mood: dict, music_db: str) -> int:
    mood["song_ids"], checked_to = mood_sync.resolve_song_ids(mood["song_ids"], music_db)
    return checked_to


def _saved_collection(collection: saved_sync.Collection) -> Collection:
    return Collection(
        collection.path, collection.path, saved_sync.UID_RE, SAVED_MAX_BODY_BYTES, saved_sync.BadItemError,
        saved_sync.connect,
        lambda conn, since: saved_sync.changes_since(conn, collection, since),
        lambda body: saved_sync.parse_item(collection, body),
        lambda conn, folder, username, uid, item, base, _: saved_sync.store_item(conn, collection, uid, item, base),
        lambda conn, folder, uid, base, at: saved_sync.delete_item(conn, collection, uid, base, at),
    )


COLLECTIONS = (
    Collection(
        "playlists", "playlists", playlist_sync.UID_RE, DEFAULT_MAX_PLAYLIST_BYTES, playlist_sync.BadPlaylistError,
        playlist_sync.connect, playlist_sync.changes_since, playlist_sync.parse_playlist,
        lambda conn, folder, username, uid, playlist, base, checked_to: playlist_sync.store_playlist(
            conn, username, uid, playlist, base, song_ids_checked_to=checked_to),
        lambda *args: playlist_sync.delete_playlist(*args),  # looked up per call: tests patch it
        lambda playlist, music_db: playlist_sync.resolve_song_ids(playlist["tracks"], music_db),
        uses_covers=True, counted=("tracks", "tracks"),
    ),
    Collection(
        "moods", "moods", playlist_sync.UID_RE, DEFAULT_MAX_PLAYLIST_BYTES, mood_sync.BadMoodError,
        mood_sync.connect, mood_sync.changes_since, mood_sync.parse_mood,
        lambda conn, folder, username, uid, mood, base, checked_to: mood_sync.store_mood(
            conn, folder, uid, mood, base, song_ids_checked_to=checked_to),
        lambda *args: mood_sync.delete_mood(*args),
        _resolve_mood,
        uses_covers=True, counted=("song_ids", "songs"),
    ),
    Collection(
        "folders", "folders", folder_sync.UID_RE, FOLDER_MAX_BODY_BYTES, folder_sync.BadFolderError,
        folder_sync.connect, folder_sync.changes_since, folder_sync.parse_folder,
        lambda conn, folder, username, uid, parsed, base, _: folder_sync.store_folder(conn, uid, parsed, base),
        lambda conn, folder, uid, base, at: folder_sync.delete_folder(conn, uid, base, at),
        counted=("items", "items"),
    ),
    _saved_collection(saved_sync.SAVED_SONGS),
    _saved_collection(saved_sync.LATER_LINKS),
)
BY_PATH = {c.path: c for c in COLLECTIONS}
