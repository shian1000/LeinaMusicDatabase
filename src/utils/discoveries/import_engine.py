import time

from utils.database.datatables import Song, Artist
from utils.database.database_sessions import open_and_set_global_database_sessions

from utils.common.debug import slog, mlog
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
from utils.database.datatables import artist_categories
from utils.database.database_management import add_db_entry
from utils.common.text_utils import normalize, check_spelling, similarity, are_artists_entries_similar, scaled_similarity_threshold, remove_brackets
from config.constants import SPELLING_CHECK_THRESHOLD
from utils.common import spellcheck_cache
from utils.common.musicbrainz_client import MBStats
from rich import print
import questionary
from sqlalchemy import func

# OPTIMIZATION: Cache for spell check results to avoid repeated API calls
# Key: (artist, title) tuple, Value: spell_check_result dict
_SPELL_CHECK_CACHE = {}

# Sentinel stored in artist_cache while an ambiguous artist match is waiting
# for the user to review it after the batch, so repeat occurrences of the
# same artist name reuse the pending state instead of re-querying MusicBrainz
# and queueing a duplicate question.
_PENDING_ARTIST = object()

def find_similar_artist(metadata: dict) -> list:
    """
    Look up whether MusicBrainz's corrected spelling for this artist matches
    one or more artists already in the database.

    Pure lookup - does NOT ask the user. Returns the list of matching Artist
    rows (possibly empty); the caller queues the conflict and lets the user
    resolve it once the whole import batch is done, instead of interrupting
    the import for every ambiguous artist name.
    """
    new_artist_name = metadata["artist_name"]
    slog(metadata)

    slog(f"Couldn't find artist {new_artist_name}, trying spellcheck")

    # OPTIMIZATION: Use title for spell check context
    spell_check_cache_key = (new_artist_name, metadata.get("title", ""))

    if spell_check_cache_key in _SPELL_CHECK_CACHE:
        spell_check_result = _SPELL_CHECK_CACHE[spell_check_cache_key]
        slog(f"[CACHE HIT] Artist spell check cached", priority=1)
    else:
        spell_check_result = check_spelling(new_artist_name, metadata["title"])
        _SPELL_CHECK_CACHE[spell_check_cache_key] = spell_check_result
        slog(f"[API CALL] Artist spell check performed and cached", priority=1)

    slog(new_artist_name)
    slog(metadata["title"])
    slog(spell_check_result)

    if not spell_check_result.get("found"):
        slog(f"No MusicBrainz match for {new_artist_name}, skipping spellcheck correction", priority=1)
        return []

    corrected_spelling = spell_check_result["corrected_artist"]
    slog(corrected_spelling)

    similarity_percent = similarity(new_artist_name, corrected_spelling)
    slog(new_artist_name)
    slog(corrected_spelling)
    slog(similarity_percent)

    if not (similarity_percent > scaled_similarity_threshold(new_artist_name, corrected_spelling, SPELLING_CHECK_THRESHOLD)):
        return []

    existing_artists = get_artists_from_db_session(artist_categories[0], corrected_spelling)
    if not existing_artists:
        return []

    similar_artists = []
    for art in existing_artists:
        if are_artists_entries_similar(art, corrected_spelling):
            slog(art)
            slog(art.name)
            slog(art.id)
            similar_artists.append(art)
        else:
            slog(f"{art} is not similar to {corrected_spelling}, skipping", priority=1)

    return similar_artists

def find_similar_song(metadata: dict, artist_obj: Artist) -> Song:
    """
    Check if a similar song already exists for the artist and return it.

    OPTIMIZATION: Check local database FIRST before expensive API call.
    Only call check_spelling() if no similar songs found locally.

    Returns the matching Song if one is found, otherwise None. Does NOT ask
    the user - callers are expected to queue the conflict and let the user
    resolve it once the whole import batch is done, instead of interrupting
    the import for every match.
    """
    new_artist_name = metadata["artist_name"]
    new_title = metadata["title"]
    slog(f"Couldn't find song {new_artist_name} - {new_title}, trying spellcheck")

    # OPTIMIZATION STEP 1: Check local database for similar songs FIRST (instant)
    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)

    # Try to find similar songs using local similarity comparison (no API call).
    # Bracketed annotations like "(prod. X)" or "(feat. Y)" are stripped first
    # so two different songs that share a producer/feature credit don't score
    # as similar just because of that shared suffix (e.g. "Adieu (prod. Rumak)"
    # vs "Nostalgia (prod. Rumak)").
    new_title_core = remove_brackets(new_title)
    for ex_son in existing_artists_songs:
        sim_percent = similarity(new_title_core, remove_brackets(ex_son.title))

        if sim_percent > SPELLING_CHECK_THRESHOLD:
            slog(f"      [LOCAL MATCH] Similar song found, deferring decision to caller", priority=1)
            return ex_son

    slog(f"      [NO LOCAL MATCH] No similar songs found in database, trying API spell check", priority=1)

    # OPTIMIZATION STEP 2: Only call expensive API if no local match found
    cache_key = (new_artist_name, new_title)
    if cache_key in _SPELL_CHECK_CACHE:
        spell_check_result = _SPELL_CHECK_CACHE[cache_key]
        slog(f"      [CACHE HIT] Spell check result retrieved from cache", priority=1)
    else:
        spell_check_result = check_spelling(new_artist_name, new_title)
        _SPELL_CHECK_CACHE[cache_key] = spell_check_result
        slog(f"      [API CALL] Spell check performed and cached", priority=1)

    slog(spell_check_result)

    if not spell_check_result.get("found"):
        slog("      [NO MB MATCH] Spell check found no match, treating as new song", priority=1)
        return None

    corrected_spelling = spell_check_result["corrected_title"]
    slog(corrected_spelling)

    similarity_percent = similarity(new_title, corrected_spelling)
    slog(similarity_percent)

    if(similarity_percent > SPELLING_CHECK_THRESHOLD):
        # Search DB again with corrected spelling
        for ex_son in existing_artists_songs:
            if normalize(corrected_spelling) in normalize(ex_son.title):
                slog(f"      [SPELLCHECK MATCH] Similar song found, deferring decision to caller", priority=1)
                return ex_son

    return None

def resolve_artist(metadata: dict, artist_cache: dict = None, pending_conflicts: list = None) -> tuple:
    """
    Resolve or create an artist entry with optimized lookup.

    Uses a cache to avoid redundant database queries and similarity calculations.
    Implements smart filtering to stop early once a good match is found.

    Args:
        metadata: Metadata dict with 'artist_name' and 'origin'
        artist_cache: Optional dict to cache lookups {normalized_name: artist_obj}
        pending_conflicts: list to queue ambiguous-artist conflicts onto instead
            of asking immediately (see resolve_song for the same pattern)

    Returns:
        (artist_obj, is_pending):
        - (artist, False): artist resolved (existing or newly created).
        - (None, True): an ambiguous MusicBrainz-corrected match was found.
          The conflict was appended to pending_conflicts and artist_cache
          remembers the pending state (_PENDING_ARTIST), so the caller should
          defer this file (and its song) until the batch is reviewed.
    """
    if artist_cache is None:
        artist_cache = {}
    if pending_conflicts is None:
        pending_conflicts = []

    new_artist_name = metadata["artist_name"]
    new_artist_origin = metadata.get("origin")
    normalized_name = normalize(new_artist_name)

    # Check cache first - this is the fastest path for repeated artists
    if normalized_name in artist_cache:
        cached_value = artist_cache[normalized_name]
        if cached_value is _PENDING_ARTIST:
            slog(f"    [CACHE HIT] Artist resolution already deferred", priority=1)
            return None, True
        slog(f"    [CACHE HIT] Artist found in cache", priority=1)
        return cached_value, False

    existing_artist = None

    # Optimization 1: Try exact match first (normalized comparison)
    music_session, _ = open_and_set_global_database_sessions()
    candidates = music_session.query(Artist).filter(
        func.lower(Artist.name) == new_artist_name.lower()
    ).all()

    if candidates:
        existing_artist = candidates[0]
        print(f"Found exact match for artist [blue]{existing_artist.name}[/blue]")
    else:
        # Optimization 2: Get normalized matches, but LIMIT the results to reduce comparisons
        # Instead of potentially getting 1000s of artists, we get a reasonable subset
        existing_artists = get_artists_from_db_session(artist_categories[0], normalize(new_artist_name))

        # Optimization 3: Sort by name similarity to check most likely matches first
        if existing_artists:
            # Pre-filter: only check artists that are somewhat similar in length
            # This avoids expensive similarity calculations on obviously different names
            name_length = len(new_artist_name)
            filtered_candidates = [
                a for a in existing_artists
                if abs(len(a.name) - name_length) <= max(name_length * 0.5, 3)  # Allow 50% length variance
            ]

            # If we filtered too much, use all candidates
            if not filtered_candidates:
                filtered_candidates = existing_artists[:50]  # Cap at 50 candidates
            else:
                filtered_candidates = filtered_candidates[:50]  # Still cap at 50

            print(f"Checking {len(filtered_candidates)} candidate artists (out of {len(existing_artists)} total matches)")

            for similar_artist in filtered_candidates:
                print("Checking the similarity between the two artists")
                similarity_percent = similarity(new_artist_name, similar_artist.name)
                if(similarity_percent > scaled_similarity_threshold(new_artist_name, similar_artist.name, SPELLING_CHECK_THRESHOLD)):
                    existing_artist = similar_artist
                    print(f"Artists seem the same (Similarity is {similarity_percent})")
                    break  # Early exit - we found a good match
                else:
                    print(f"Artists are not the same (Similarity is {similarity_percent})")
        else:
            # No matches found, try spell check
            similar_artists = find_similar_artist(metadata)

            if similar_artists:
                print(f"Found similar artist [blue]{similar_artists[0].name}[/blue] but you were trying to add [green]{new_artist_name}[/green] - review deferred until the import finishes")
                slog(f"    [DEFERRED] Similar artist conflict queued for later resolution", priority=1)
                pending_conflicts.append({
                    "metadata": metadata,
                    "normalized_name": normalized_name,
                    "candidate_artists": similar_artists,
                })
                artist_cache[normalized_name] = _PENDING_ARTIST
                return None, True

    new_artist_obj = None
    if not existing_artist:
        new_artist_obj = Artist()
        new_artist_obj.name = new_artist_name
        if new_artist_origin:
            new_artist_obj.origin = new_artist_origin
        add_db_entry(new_artist_obj)
    else:
        new_artist_obj = existing_artist
        print(f"Found existing artist ({new_artist_obj.name})")

    # Cache the result for future lookups in this import batch
    artist_cache[normalized_name] = new_artist_obj

    return new_artist_obj, False

def create_song_entry(metadata: dict, artist_obj: Artist) -> Song:
    new_song_obj = Song()
    new_song_obj.artist_id = artist_obj.id
    new_song_obj.title = metadata["title"]
    new_song_obj.album = metadata.get("album")
    try:
        new_song_obj.year = int(metadata["year"])
    except (ValueError, TypeError):
        new_song_obj.year = None
    new_song_obj.language = metadata.get("language")
    # Only present for entries sourced from a YouTube playlist import - mp3
    # imports never carry this key, so this is a no-op (None) for them.
    new_song_obj.youtube_video_id = metadata.get("youtube_video_id")
    add_db_entry(new_song_obj)
    return new_song_obj

def _backfill_youtube_link(metadata: dict, existing_song: Song) -> None:
    """If this metadata entry came with a YouTube video id (i.e. it's from a
    playlist import) and the matched existing song doesn't have one stored
    yet, save it - the song attribute is already tracked by the open
    session, so it rides along on the next commit without an explicit
    add_db_entry() call.
    """
    video_id = metadata.get("youtube_video_id")
    if video_id and not existing_song.youtube_video_id:
        existing_song.youtube_video_id = video_id
        print(f"Linked existing song [blue]{existing_song.title}[/blue] to YouTube video [green]{video_id}[/green]")

def resolve_song(metadata: dict, artist_obj: Artist, pending_conflicts: list) -> tuple:
    """
    Resolve a song entry for the given artist.

    Returns (song, is_pending):
    - (song, False): song was created (or already existed - song is None then).
    - (None, True): a similar song was found. The conflict was appended to
      pending_conflicts instead of asking right away, so the caller should
      skip it for now and resolve it later, once the whole batch is done.
    """
    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)

    for ex_son in existing_artists_songs:
        if normalize(metadata["title"]) in normalize(ex_son.title):
            print("Yes, there is a song like this already. Skipping")
            slog(f"    [SONG EXISTS] Exact match found", priority=1)
            _backfill_youtube_link(metadata, ex_son)
            return None, False

    slog("Couldn't find the song, trying spellchecking")
    similar_song = find_similar_song(metadata, artist_obj)

    if similar_song:
        print(f"Found similar song [blue]{similar_song.title} by {similar_song.artist.name}[/blue] but you were trying to add [green]{metadata['title']}[/green] - review deferred until the import finishes")
        slog(f"    [DEFERRED] Similar song conflict queued for later resolution", priority=1)
        pending_conflicts.append({
            "metadata": metadata,
            "artist_obj": artist_obj,
            "existing_song": similar_song,
        })
        return None, True

    new_song_obj = create_song_entry(metadata, artist_obj)
    return new_song_obj, False


def _entry_label(metadata: dict) -> str:
    return metadata.get("_label") or f"{metadata.get('artist_name')} - {metadata.get('title')}"


def run_import_batch(metadata_list: list, pre_skipped: list = None) -> list:
    """Resolve/create DB entries for a batch of already-extracted metadata
    dicts, shared by every import source (mp3 tags, YouTube playlists, ...).

    Each metadata dict needs at least 'artist_name' and 'title'; 'album',
    'year', 'language', 'origin' and 'youtube_video_id' are optional. An
    optional '_label' is used for logging/skip messages instead of
    "<artist> - <title>" (handy when the source has a more natural label,
    like an mp3 filename).

    `pre_skipped` lets a caller fold skips it already made before calling
    this (e.g. an mp3 file with no usable artist/title at all) into the
    final skipped-songs summary, as a list of (label, reason) tuples.

    Ambiguous-artist and similar-song matches aren't resolved during the
    loop - they're queued and asked about in one batch after every entry has
    been processed, so the import doesn't keep stopping for input.
    """
    added_songs = []
    skipped_songs = []
    reason_for_skipping = []

    if pre_skipped:
        for label, reason in pre_skipped:
            skipped_songs.append(label)
            reason_for_skipping.append(reason)

    pending_song_conflicts = []
    pending_artist_conflicts = []
    # Entries whose artist resolution was deferred - their song still needs
    # resolving once the artist conflicts below are reviewed.
    deferred_entries = []

    # OPTIMIZATION: Cache for artists found in this import batch
    artist_cache = {}

    # Reset MusicBrainz counters so the summary at the end reflects this run only.
    MBStats.reset()

    added_count = 0
    skipped_count = len(skipped_songs)

    entry_timings = {}

    for idx, metadata in enumerate(metadata_list, 1):
        entry_start = time.time()
        label = _entry_label(metadata)
        mlog(f"\n[{idx}/{len(metadata_list)}] Processing: {label}")

        if not metadata.get("artist_name") or not metadata.get("title"):
            skipped_count += 1
            skipped_songs.append(label)
            reason_for_skipping.append("Missing artist or title")
            continue

        print(f"{metadata['artist_name']} - {metadata['title']}")

        artist_start = time.time()
        new_artist_obj, artist_is_pending = resolve_artist(metadata, artist_cache, pending_artist_conflicts)
        artist_time = time.time() - artist_start
        mlog(f"  └─ Artist resolution: {artist_time:.3f}s")

        if artist_is_pending:
            mlog(f"  └─ Artist resolution deferred: similar artist needs review")
            deferred_entries.append(metadata)
            entry_timings[label] = {'total': time.time() - entry_start, 'artist': artist_time, 'song': 0.0}
            print("##########")
            print()
            continue

        song_start = time.time()
        new_song_obj, is_pending = resolve_song(metadata, new_artist_obj, pending_song_conflicts)
        song_time = time.time() - song_start
        mlog(f"  └─ Song resolution: {song_time:.3f}s")

        if is_pending:
            mlog(f"  └─ Song resolution deferred: similar song needs review")
        elif new_song_obj:
            added_count += 1
            added_songs.append(new_song_obj)
        else:
            skipped_count += 1
            skipped_songs.append(label)
            reason_for_skipping.append("Song already exists")

        entry_timings[label] = {'total': time.time() - entry_start, 'artist': artist_time, 'song': song_time}
        mlog(f"  ⏱️  TOTAL for this entry: {entry_timings[label]['total']:.3f}s")

        print("##########")
        print()

    # Now that every entry has been processed, go through the ambiguous-artist
    # matches that were queued along the way and ask about each one in a
    # single batch, instead of interrupting the import every time one came
    # up. Each normalized artist name only appears once here even if several
    # entries shared it (see the artist_cache / _PENDING_ARTIST check in
    # resolve_artist).
    if pending_artist_conflicts:
        print("\n" + "="*70)
        print(f"REVIEW: {len(pending_artist_conflicts)} artist(s) found a similar match in the database")
        print("="*70)
        for conflict in pending_artist_conflicts:
            conflict_metadata = conflict["metadata"]
            normalized_name = conflict["normalized_name"]
            new_artist_name = conflict_metadata["artist_name"]

            resolved_artist = None
            for candidate in conflict["candidate_artists"]:
                print(f"Found similar artist [blue]{candidate.name}[/blue] but you were trying to add [green]{new_artist_name}[/green]")
                confirmation = questionary.confirm("Do you wish to use the artist already in the database?").ask()
                if confirmation:
                    resolved_artist = candidate
                    print("Found existing artist")
                    break

            if resolved_artist is None:
                resolved_artist = Artist()
                resolved_artist.name = new_artist_name
                new_artist_origin = conflict_metadata.get("origin")
                if new_artist_origin:
                    resolved_artist.origin = new_artist_origin
                add_db_entry(resolved_artist)

            # Unblocks every entry that shared this artist name (they all hit
            # the artist_cache fast path in resolve_artist from here on).
            artist_cache[normalized_name] = resolved_artist
            print()

    # Every entry that got deferred because of an artist conflict can now
    # resolve its artist from the cache (instant - see above) and its song. A
    # similar song for one of these still gets queued below rather than asked
    # right away.
    for deferred_metadata in deferred_entries:
        deferred_artist_obj, _ = resolve_artist(deferred_metadata, artist_cache, pending_artist_conflicts)
        deferred_song_obj, deferred_is_pending = resolve_song(deferred_metadata, deferred_artist_obj, pending_song_conflicts)
        if deferred_is_pending:
            continue
        elif deferred_song_obj:
            added_count = added_count + 1
            added_songs.append(deferred_song_obj)
        else:
            skipped_count = skipped_count + 1
            skipped_songs.append(_entry_label(deferred_metadata))
            reason_for_skipping.append("Song already exists")

    # Finally, go through the similar-song matches that were queued along the
    # way (both from the main loop and from the deferred entries above) and
    # ask about each one in a single batch, instead of interrupting the
    # import every time one came up.
    if pending_song_conflicts:
        print("\n" + "="*70)
        print(f"REVIEW: {len(pending_song_conflicts)} song(s) found a similar match in the database")
        print("="*70)
        for conflict in pending_song_conflicts:
            conflict_metadata = conflict["metadata"]
            conflict_artist_obj = conflict["artist_obj"]
            existing_song = conflict["existing_song"]
            print(f"Found similar song [blue]{existing_song.title} by {existing_song.artist.name}[/blue] but you were trying to add [green]{conflict_metadata['title']}[/green]")
            confirmation = questionary.confirm("Do you wish to use the song already in the database?").ask()
            if confirmation:
                skipped_count = skipped_count + 1
                skipped_songs.append(_entry_label(conflict_metadata))
                reason_for_skipping.append("Song already exists")
                _backfill_youtube_link(conflict_metadata, existing_song)
            else:
                new_song_obj = create_song_entry(conflict_metadata, conflict_artist_obj)
                added_count = added_count + 1
                added_songs.append(new_song_obj)
            print()

    # Print timing summary
    print("\n" + "="*70)
    print("PERFORMANCE SUMMARY")
    print("="*70)
    if entry_timings:
        total_time_all = sum(t['total'] for t in entry_timings.values())
        avg_time = total_time_all / len(entry_timings)
        max_time = max(t['total'] for t in entry_timings.values())

        print(f"Total time for all entries: {total_time_all:.3f}s")
        print(f"Average time per entry: {avg_time:.3f}s")
        print(f"Slowest entry: {max_time:.3f}s\n")

        print("Per-entry breakdown:")
        for label, times in sorted(entry_timings.items(), key=lambda x: x[1]['total'], reverse=True):
            print(f"  {label}")
            print(f"    ├─ Artist:   {times['artist']:.3f}s")
            print(f"    ├─ Song:     {times['song']:.3f}s")
            print(f"    └─ TOTAL:    {times['total']:.3f}s")

    # OPTIMIZATION: Show cache statistics
    if _SPELL_CHECK_CACHE:
        print(f"\n💾 Spell Check Cache Statistics:")
        print(f"   Total cached entries: {len(_SPELL_CHECK_CACHE)}")
        cache_keys = list(_SPELL_CHECK_CACHE.keys())
        print(f"   Cache entries: {cache_keys[:5]}")
        if len(cache_keys) > 5:
            print(f"   ... and {len(cache_keys) - 5} more")

    # Persist the disk-backed spell-check cache and report MusicBrainz traffic.
    spellcheck_cache.save()
    print()
    print(MBStats.format_summary())

    print("="*70)

    print(f"Skipped {skipped_count} songs:")
    for s_s, reason in zip(skipped_songs, reason_for_skipping):
        print(f"{s_s} (reason - {reason})")
    print()
    print("##########")
    print()
    print()

    print(f"\nDone! Added {added_count}, updated 0.")
    sorted_songs = sorted(added_songs, key=lambda song: (song.artist.name, song.title))
    return sorted_songs
