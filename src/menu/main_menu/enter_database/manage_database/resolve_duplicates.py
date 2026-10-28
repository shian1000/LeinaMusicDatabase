from utils.database.music_db_manager import Song, Artist
from sqlalchemy import text
from utils.database.song_artists import merge_artist_synonyms, reassign_additional_artist_links
from utils.database.albums import reassign_artist_albums
from utils.database.latin_names import artist_match_synonyms
from utils.database.song_duplicates import find_duplicate_groups, merge_songs, spelling_key
from utils.database.database_sessions import open_and_set_global_database_sessions, submit_global_database_session
from utils.youtube.manage_youtube_playlists import _parse_synonyms
from utils.common.normalizer import normalize, normalize_apostrophes
from utils.common.script_utils import fix_mixed_script_words

def remove_duplicate_songs(report_none: bool = True) -> int:
    """Fold the songs of one artist (and kind) whose titles differ only in
    spelling - case, punctuation, apostrophes - into one, without asking:
    those are duplicates for certain. Nothing is lost (song_duplicates.
    merge_songs()); near duplicates ("feat.", "(Visualiser)") are for
    "Resolve duplicated songs", which asks. A full album and its title track
    share a title but aren't duplicates (different kind). Returns how many
    songs were merged away."""
    session, _ = open_and_set_global_database_sessions()
    groups = find_duplicate_groups(session, spelling_key)
    if not groups:
        if report_none:
            print("No duplicate songs found.")
        return 0

    print(f"Found {sum(len(g) - 1 for g in groups)} duplicate song(s):")
    for kept, *dropped in groups:
        for song in dropped:
            print(f"  - [{song.id}] {song.artist.name} — {song.title}  ->  kept [{kept.id}] {kept.title}")
            merge_songs(session, kept, song)
    submit_global_database_session()
    merged = sum(len(g) - 1 for g in groups)
    print(f"Merged {merged} duplicate song(s).")
    return merged


def _artist_key(name: str) -> str:
    return normalize_apostrophes(fix_mixed_script_words(name)).lower()


def remove_duplicate_artists(report_none: bool = True) -> int:
    """Merge artists whose names differ only in case, apostrophe or a
    look-alike letter, or match another artist's synonym, without asking.
    Returns how many artists were removed."""
    session, _ = open_and_set_global_database_sessions()

    all_artists = session.query(Artist).order_by(Artist.name).all()

    seen = {}
    duplicates = []

    for artist in all_artists:
        # Only case, the apostrophe variant ("Carla's" / "Carla’s") and a
        # look-alike letter from the other alphabet ("Вjörk" / "björk") are
        # ignored - this merges without asking, and a full normalize() would
        # also fold real pairs together ("Bôa" / "BoA", "!!!" / "(((())))").
        key = _artist_key(artist.name)
        key_origin = artist.origin
        if key in seen:
            seen_origin = seen[key].origin
            # normalize(), not .lower(): the same place gets spelled with and
            # without diacritics ("Ukraine - Kyïv" / "Ukraine - Kyiv").
            if (key_origin is None or seen_origin is None or
                    normalize(key_origin) == normalize(seen_origin)):
                duplicates.append((seen[key], artist))
        else:
            seen[key] = artist

    # An artist whose plain name exactly matches a *different* artist's known
    # synonym (the artists table's `synonyms` column) is a duplicate too,
    # even though the two names don't look alike at all - e.g. a leftover
    # "akuteofficial" artist row alongside the real "Akute" entry that lists
    # it as a synonym. The artist carrying the synonym is treated as
    # canonical (kept); the plain-name match is merged away.
    already_flagged_ids = {original.id for original, _ in duplicates} | {duplicate.id for _, duplicate in duplicates}
    for artist in all_artists:
        if artist.id in already_flagged_ids:
            continue
        for synonym in _parse_synonyms(artist_match_synonyms(artist)):
            match = seen.get(_artist_key(synonym.strip()))
            if match and match.id != artist.id and match.id not in already_flagged_ids:
                duplicates.append((artist, match))
                already_flagged_ids.add(artist.id)
                already_flagged_ids.add(match.id)
                break

    if not duplicates:
        if report_none:
            print("No duplicate artists found.")
        return 0

    print(f"Found {len(duplicates)} duplicate(s):")

    for original, duplicate in duplicates:
        print(f"  - Keeping [{original.id}] '{original.name}', removing [{duplicate.id}]")

        session.execute(
            text("UPDATE songs SET artist_id = :original_id WHERE artist_id = :duplicate_id"),
            {"original_id": original.id, "duplicate_id": duplicate.id}
        )
        reassign_additional_artist_links(session, duplicate.id, original.id)
        reassign_artist_albums(session, duplicate.id, original.id)
        merge_artist_synonyms(original, duplicate)

        songs = session.query(Song).filter(Song.artist_id == original.id).all()
        for song in songs:
            print(f"    -> Reassigned song '{song.title}' to artist [{original.id}]")

        session.execute(
            text("DELETE FROM artists WHERE id = :id"),
            {"id": duplicate.id}
        )

    # The raw UPDATE/DELETE bypassed the songs and artists this session
    # holds: the commit expires them, so they're read again
    submit_global_database_session()
    print(f"\nRemoved {len(duplicates)} duplicate artist(s).")
    return len(duplicates)


def resolve_duplicated_artists():
    # Merging artists can make two songs of the merged artist duplicates, and
    # merging songs can leave artists alike, so the pair repeats - but only
    # while a round still merged something. Both passes merge without asking,
    # so a repeated round never asks about anything again.
    report_none = True
    while remove_duplicate_songs(report_none) + remove_duplicate_artists(report_none):
        report_none = False
