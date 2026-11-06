"""Replaying music.db's song_id_changes log into a file that points at songs.id
(tag.db, files.db, a user.db), on a plain sqlite3 connection. The caller commits.
The rules and why: MDHS's utils/database/song_references.py."""

from . import mood_sync, playlist_sync, saved_sync


def cursor(conn):
    row = conn.execute("SELECT last_change_id FROM applied_song_changes WHERE id = 1").fetchone()
    return row[0] if row else None


def set_cursor(conn, change_id: int) -> None:
    conn.execute(
        "INSERT INTO applied_song_changes (id, last_change_id) VALUES (1, :c) "
        "ON CONFLICT (id) DO UPDATE SET last_change_id = :c",
        {"c": change_id},
    )


def replay(conn, changes: list, has_playlists: bool = False, has_tags: bool = True,
           has_files: bool = False, changed_playlists: set | None = None, changed_tags: set | None = None,
           auto_match_source: str | None = None) -> int:
    """Apply the log entries past this file's cursor. Returns how many rows
    changed; adds the playlists whose tracks changed to `changed_playlists`
    and the tags whose songs changed to `changed_tags`. Caller commits.
    auto_match_source is what a file's match_source becomes when its song is gone."""
    last = cursor(conn) or 0
    touched = 0
    for change_id, old_id, new_id in changes:
        if change_id <= last:
            continue
        params = {"old": old_id, "new": new_id, "auto": auto_match_source}
        if has_playlists:
            # Merged -> the kept song, deleted -> NULL (title/artist/video stay)
            if changed_playlists is not None:
                changed_playlists.update(r[0] for r in conn.execute(
                    "SELECT DISTINCT playlist_id FROM playlist_tracks WHERE song_id = :old", params))
            touched += conn.execute(
                "UPDATE playlist_tracks SET song_id = :new WHERE song_id = :old", params).rowcount
        if has_tags and changed_tags is not None:
            changed_tags.update(r[0] for r in conn.execute(
                "SELECT DISTINCT tag_id FROM song_tags WHERE song_id = :old", params))
        if new_id is not None:
            if has_tags:
                touched += conn.execute(
                    "UPDATE OR IGNORE song_tags SET song_id = :new WHERE song_id = :old", params).rowcount
                conn.execute("DELETE FROM song_tags WHERE song_id = :old", params)
            if has_files:
                touched += conn.execute(
                    "UPDATE files SET song_id = :new WHERE song_id = :old", params).rowcount
        else:
            if has_tags:
                touched += conn.execute("DELETE FROM song_tags WHERE song_id = :old", params).rowcount
            if has_files:
                # Even a hand-set match: its song is gone, so let a scan find another
                touched += conn.execute(
                    "UPDATE files SET song_id = NULL, match_source = :auto WHERE song_id = :old",
                    params).rowcount
        last = change_id
    set_cursor(conn, last)
    return touched


def _catch_up_server_playlists(conn, changes: list, changed_playlists: set) -> int:
    """Playlists the sharing server stored, resolved against the shared copy
    of music.db up to song_ids_checked_to: replay what came after that, up
    to this file's cursor, for their tracks only (what's past the cursor
    follows in replay() for every track)."""
    last = cursor(conn) or 0
    touched = 0
    pending = conn.execute(
        "SELECT id, song_ids_checked_to FROM playlists WHERE song_ids_checked_to IS NOT NULL").fetchall()
    for playlist_id, checked_to in pending:
        for change_id, old_id, new_id in changes:
            if checked_to < change_id <= last:
                rows = conn.execute(
                    "UPDATE playlist_tracks SET song_id = :new WHERE playlist_id = :p AND song_id = :old",
                    {"new": new_id, "old": old_id, "p": playlist_id}).rowcount
                if rows:
                    touched += rows
                    changed_playlists.add(playlist_id)
        # Only if the server hasn't stored it again meanwhile
        conn.execute("UPDATE playlists SET song_ids_checked_to = NULL WHERE id = :p "
                     "AND song_ids_checked_to = :c", {"p": playlist_id, "c": checked_to})
    return touched


def _catch_up_server_moods(conn, changes: list, changed_tags: set) -> int:
    """_catch_up_server_playlists() for moods: the songs of a mood the
    sharing server stored, checked up to song_ids_checked_to."""
    last = cursor(conn) or 0
    touched = 0
    pending = conn.execute(
        "SELECT id, song_ids_checked_to FROM tags WHERE song_ids_checked_to IS NOT NULL").fetchall()
    for tag_id, checked_to in pending:
        for change_id, old_id, new_id in changes:
            if checked_to < change_id <= last:
                params = {"new": new_id, "old": old_id, "t": tag_id}
                rows = 0
                if new_id is not None:
                    rows += conn.execute("UPDATE OR IGNORE song_tags SET song_id = :new "
                                         "WHERE tag_id = :t AND song_id = :old", params).rowcount
                rows += conn.execute("DELETE FROM song_tags WHERE tag_id = :t AND song_id = :old",
                                     params).rowcount
                if rows:
                    touched += rows
                    changed_tags.add(tag_id)
        conn.execute("UPDATE tags SET song_ids_checked_to = NULL WHERE id = :t "
                     "AND song_ids_checked_to = :c", {"t": tag_id, "c": checked_to})
    return touched


def replay_user_db(conn, changes: list) -> int:
    if not playlist_sync.is_migrated(conn):
        # Before the sync columns (migration not applied yet): tracks only
        return replay(conn, changes, has_playlists=True)
    has_moods = playlist_sync.is_migrated(conn, mood_sync.REQUIRED_MIGRATION)
    changed_playlists, changed_tags = set(), set()
    touched = _catch_up_server_playlists(conn, changes, changed_playlists)
    if has_moods:
        touched += _catch_up_server_moods(conn, changes, changed_tags)
    touched += replay(conn, changes, has_playlists=True, changed_playlists=changed_playlists,
                      changed_tags=changed_tags if has_moods else None)
    # A new revision, so the phones fetch the new song ids
    playlist_sync.mark_changed(conn, changed_playlists)
    if has_moods:
        mood_sync.mark_changed(conn, changed_tags)
    # The whole log, not past the cursor: see follow_song_merges()
    touched += saved_sync.follow_song_merges(conn, changes)
    return touched
