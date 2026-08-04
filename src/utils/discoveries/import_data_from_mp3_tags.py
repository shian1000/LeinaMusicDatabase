from pathlib import Path
import time

from utils.database.datatables import Song, Artist
from utils.database.database_sessions import get_global_database_sessions, open_and_set_global_database_sessions

from utils.common.debug import slog, mlog
from utils.discoveries.mp3_utils import extract_metadata_with_fallback, extract_unknown_data
from utils.database.database_getter import get_artists_from_db_session, get_songs_from_db_session
from utils.database.datatables import artist_categories
from utils.database.database_management import add_db_entry
from utils.common.text_utils import normalize, check_spelling, similarity, are_artists_entries_similar, scaled_similarity_threshold
from config.constants import SPELLING_CHECK_THRESHOLD
from rich import print
import questionary
from sqlalchemy import func

# OPTIMIZATION: Cache for spell check results to avoid repeated API calls
# Key: (artist, title) tuple, Value: spell_check_result dict
_SPELL_CHECK_CACHE = {}

def check_artist_spelling(metadata) -> Artist:

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

    corrected_spelling = spell_check_result["corrected_artist"]
    slog(corrected_spelling)

    similarity_percent = similarity(new_artist_name, corrected_spelling)
    slog(new_artist_name)
    slog(corrected_spelling)
    slog(similarity_percent)

    if(similarity_percent > scaled_similarity_threshold(new_artist_name, corrected_spelling, SPELLING_CHECK_THRESHOLD)):
        existing_artists = get_artists_from_db_session(artist_categories[0], corrected_spelling)
        if existing_artists:
            for art in existing_artists:
                if not (are_artists_entries_similar(art, corrected_spelling)):
                    slog(f"{art} is not similar to {corrected_spelling}, skipping", priority=1)
                else:
                    print(f"Found similar artist [blue]{art.name}[/blue] but you were trying to add [green]{new_artist_name}[/green]")
                    confirmation = questionary.confirm(f"Do you wish to use the artist already in the database?").ask()
                    if confirmation:
                        print("Found existing artist")
                        slog(art)
                        slog(art.name)
                        slog(art.id)
                        return art
            else:
                return None
            
def does_similar_song_exists(metadata: dict, artist_obj: Artist) -> bool:
    """
    Check if a similar song already exists for the artist.
    
    OPTIMIZATION: Check local database FIRST before expensive API call.
    Only call check_spelling() if no similar songs found locally.
    
    Returns True if user wants to use existing song, False otherwise.
    """
    new_artist_name = metadata["artist_name"]
    new_title = metadata["title"]
    slog(f"Couldn't find song {new_artist_name} - {new_title}, trying spellcheck")

    # OPTIMIZATION STEP 1: Check local database for similar songs FIRST (instant)
    local_check_start = time.time()
    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)
    local_check_time = time.time() - local_check_start
    slog(f"      [LOCAL DB] Retrieved {len(existing_artists_songs) if existing_artists_songs else 0} songs in {local_check_time:.4f}s", priority=1)
    
    # Try to find similar songs using local similarity comparison (no API call)
    for ex_son in existing_artists_songs:
        local_sim_start = time.time()
        sim_percent = similarity(new_title, ex_son.title)
        local_sim_time = time.time() - local_sim_start
        slog(f"      [LOCAL SIMILARITY] '{new_title}' vs '{ex_son.title}': {sim_percent:.2f} in {local_sim_time:.4f}s", priority=1)
        
        if sim_percent > SPELLING_CHECK_THRESHOLD:
            print(f"Found similar song [blue]{ex_son.title} by {ex_son.artist.name}[/blue] but you were trying to add [green]{new_title}[/green]")
            confirmation = questionary.confirm(f"Do you wish to use the song already in the database?").ask()
            if confirmation:
                slog(f"      [LOCAL MATCH] User confirmed using existing song", priority=1)
                return True
            else:
                slog(f"      [LOCAL MATCH] User declined, continuing", priority=1)
                return False
    
    slog(f"      [NO LOCAL MATCH] No similar songs found in database, trying API spell check", priority=1)
    
    # OPTIMIZATION STEP 2: Only call expensive API if no local match found
    cache_key = (new_artist_name, new_title)
    if cache_key in _SPELL_CHECK_CACHE:
        spell_check_result = _SPELL_CHECK_CACHE[cache_key]
        slog(f"      [CACHE HIT] Spell check result retrieved from cache", priority=1)
    else:
        spell_check_start = time.time()
        spell_check_result = check_spelling(new_artist_name, new_title)
        spell_check_time = time.time() - spell_check_start
        
        _SPELL_CHECK_CACHE[cache_key] = spell_check_result
        slog(f"      [API CALL] Spell check took {spell_check_time:.4f}s (cached for reuse)", priority=1)
    
    slog(spell_check_result)

    corrected_spelling = spell_check_result["corrected_title"]
    slog(corrected_spelling)

    sim_start = time.time()
    similarity_percent = similarity(new_title, corrected_spelling)
    sim_time = time.time() - sim_start
    slog(f"      [SIMILARITY] Computed in {sim_time:.4f}s", priority=1)
    slog(similarity_percent)

    if(similarity_percent > SPELLING_CHECK_THRESHOLD):
        # Search DB again with corrected spelling
        for ex_son in existing_artists_songs:
            if normalize(corrected_spelling) in normalize(ex_son.title):
                print(f"Found similar song [blue]{ex_son.title} by {ex_son.artist.name}[/blue] but you were trying to add [green]{new_title}[/green]")
                confirmation = questionary.confirm(f"Do you wish to use the song already in the database?").ask()
                if confirmation:
                    return True
                else:
                    return False
    
    return False

def resolve_artist(metadata: dict, artist_cache: dict = None) -> Artist:
    """
    Resolve or create an artist entry with optimized lookup.
    
    Uses a cache to avoid redundant database queries and similarity calculations.
    Implements smart filtering to stop early once a good match is found.
    
    Args:
        metadata: Metadata dict with 'artist_name' and 'origin'
        artist_cache: Optional dict to cache lookups {normalized_name: artist_obj}
    
    Returns:
        Artist object (existing or newly created)
    """
    if artist_cache is None:
        artist_cache = {}

    added_new_entry = False
    new_artist_name = metadata["artist_name"]
    new_artist_origin = metadata.get("origin")
    normalized_name = normalize(new_artist_name)

    # Check cache first - this is the fastest path for repeated artists
    cache_start = time.time()
    if normalized_name in artist_cache:
        cache_hit_time = time.time() - cache_start
        slog(f"    [CACHE HIT] Artist found in cache: {cache_hit_time:.4f}s", priority=1)
        return artist_cache[normalized_name]

    existing_artist = None
    
    # Optimization 1: Try exact match first (normalized comparison)
    exact_start = time.time()
    music_session, _ = open_and_set_global_database_sessions()
    candidates = music_session.query(Artist).filter(
        func.lower(Artist.name) == new_artist_name.lower()
    ).all()
    exact_time = time.time() - exact_start
    
    if candidates:
        existing_artist = candidates[0]
        slog(f"    [EXACT MATCH] Found in {exact_time:.4f}s: {existing_artist.name}", priority=1)
        print(f"Found exact match for artist [blue]{existing_artist.name}[/blue]")
    else:
        slog(f"    [NO EXACT MATCH] Searched in {exact_time:.4f}s", priority=1)
        # Optimization 2: Get normalized matches, but LIMIT the results to reduce comparisons
        # Instead of potentially getting 1000s of artists, we get a reasonable subset
        search_start = time.time()
        existing_artists = get_artists_from_db_session(artist_categories[0], normalize(new_artist_name))
        search_time = time.time() - search_start
        slog(f"    [DB SEARCH] Found {len(existing_artists) if existing_artists else 0} candidates in {search_time:.4f}s", priority=1)
        
        # Optimization 3: Sort by name similarity to check most likely matches first
        if existing_artists:
            # Pre-filter: only check artists that are somewhat similar in length
            # This avoids expensive similarity calculations on obviously different names
            filter_start = time.time()
            name_length = len(new_artist_name)
            filtered_candidates = [
                a for a in existing_artists 
                if abs(len(a.name) - name_length) <= max(name_length * 0.5, 3)  # Allow 50% length variance
            ]
            filter_time = time.time() - filter_start
            
            # If we filtered too much, use all candidates
            if not filtered_candidates:
                filtered_candidates = existing_artists[:50]  # Cap at 50 candidates
            else:
                filtered_candidates = filtered_candidates[:50]  # Still cap at 50
            
            slog(f"    [FILTERING] Reduced {len(existing_artists)} → {len(filtered_candidates)} candidates in {filter_time:.4f}s", priority=1)
            print(f"Checking {len(filtered_candidates)} candidate artists (out of {len(existing_artists)} total matches)")
            
            # TIMING: Similarity calculations
            similarity_start = time.time()
            for similar_artist in filtered_candidates:
                print("Checking the similarity between the two artists")
                similarity_percent = similarity(new_artist_name, similar_artist.name)
                slog(f"The first is {new_artist_name}", priority=1)
                slog(f"The second is {similar_artist.name}", priority=1)
                if(similarity_percent > scaled_similarity_threshold(new_artist_name, similar_artist.name, SPELLING_CHECK_THRESHOLD)):
                    existing_artist = similar_artist
                    print(f"Artists seem the same (Similarity is {similarity_percent})")
                    break  # Early exit - we found a good match
                else:
                    print(f"Artists are not the same (Similarity is {similarity_percent})")
            similarity_time = time.time() - similarity_start
            slog(f"    [SIMILARITY] Checked {len(filtered_candidates)} artists in {similarity_time:.4f}s", priority=1)
        else:
            # No matches found, try spell check
            spell_start = time.time()
            existing_artist = check_artist_spelling(metadata)
            spell_time = time.time() - spell_start
            slog(f"    [SPELL CHECK] Completed in {spell_time:.4f}s", priority=1)

    new_artist_obj = None
    if not existing_artist:
        create_start = time.time()
        new_artist_obj = Artist()
        new_artist_obj.name = new_artist_name
        if new_artist_origin:
            new_artist_obj.origin = new_artist_origin
        add_db_entry(new_artist_obj)
        create_time = time.time() - create_start
        slog(f"    [NEW ARTIST] Created and added in {create_time:.4f}s", priority=1)
        added_new_entry = True
    else:
        new_artist_obj = existing_artist
        print(f"Found existing artist ({new_artist_obj.name})")

    # Cache the result for future lookups in this import batch
    artist_cache[normalized_name] = new_artist_obj
    
    return new_artist_obj

def resolve_song(metadata: dict, artist_obj: Artist) -> Song:

    song_check_start = time.time()
    existing_artists_songs = get_songs_from_db_session(artist_categories[2], artist_obj.id)
    song_check_time = time.time() - song_check_start
    slog(f"    [SONG CHECK] Retrieved {len(existing_artists_songs) if existing_artists_songs else 0} existing songs in {song_check_time:.4f}s", priority=1)
    
    for ex_son in existing_artists_songs:
        if normalize(metadata["title"]) in normalize(ex_son.title):
            print("Yes, there is a song like this already. Skipping")
            slog(f"    [SONG EXISTS] Exact match found", priority=1)
            return
    
    slog("Couldn't find the song, trying spellchecking")
    spell_check_start = time.time()
    simlar_song_exists = does_similar_song_exists(metadata, artist_obj)
    spell_check_time = time.time() - spell_check_start
    slog(f"    [SPELL CHECK] Checked for similar song in {spell_check_time:.4f}s", priority=1)
    
    if simlar_song_exists:
        print("Skipping song")
        slog(f"    [SKIP] Similar song found", priority=1)
        return
    
    create_start = time.time()
    new_song_obj = Song()
    new_song_obj.artist_id = artist_obj.id
    new_song_obj.title = metadata["title"]
    new_song_obj.album = metadata["album"]
    try:
        new_song_obj.year = int(metadata["year"])
    except (ValueError, TypeError):
        new_song_obj.year = None
    new_song_obj.language = metadata["language"]
    add_db_entry(new_song_obj)
    create_time = time.time() - create_start
    slog(f"    [NEW SONG] Created in {create_time:.4f}s", priority=1)

    return new_song_obj

def import_data_from_mp3_tags(folder_path: str, mode: str = "skip") -> list:
    added_songs = []
    skipped_songs = []
    reason_for_skipping = []
    new_artists_added = []
    
    # OPTIMIZATION: Cache for artists found in this import batch
    # This avoids redundant database queries and similarity calculations
    artist_cache = {}

    folder = Path(folder_path).resolve()
    if not folder.exists():
        print(f"Folder {folder_path} does not exist.")
        return

    mp3_files = list(folder.rglob("*.mp3"))
    print(f"Found {len(mp3_files)} mp3 files.")

    added_count = 0
    skipped_count = 0
    updated_count = 0
    
    # TIMING: Track performance per file
    file_timings = {}

    for idx, file in enumerate(mp3_files, 1):
        song_start_time = time.time()
        file_name = file.name
        mlog(f"\n[{idx}/{len(mp3_files)}] Processing: {file_name}")
        
        # TIMING: Metadata extraction
        metadata_start = time.time()
        metadata = extract_metadata_with_fallback(file)
        metadata_time = time.time() - metadata_start
        mlog(f"  └─ Metadata extraction: {metadata_time:.3f}s")

        if metadata["artist_name"] in {"Unknown Artist", None, ""} or metadata["title"] in {"Unknown Title", None, ""}:
            print("Warning - title/artist tags missing. Trying to extract data from filename")
            artist, title = extract_unknown_data(file)
            if not artist:
                print("The filename didn't have a proper separator")
                print(metadata)
                print(file)
            else:
                metadata["artist_name"] = artist
                metadata["title"] = title

        if metadata["artist_name"] == "Unknown Artist" or not metadata["artist_name"]:
            print("Couldn't establish the artist and title")
            print("##########")
            print()
            skipped_count = skipped_count +1
            skipped_songs.append(f"{metadata["artist_name"]} - {metadata["title"]}")
            reason_for_skipping.append(f"Couldn't establish the artist and title (Filename: {file})")
            continue

        # TIMING: Artist resolution
        artist_start = time.time()
        new_artist_obj = resolve_artist(metadata, artist_cache)
        artist_time = time.time() - artist_start
        mlog(f"  └─ Artist resolution: {artist_time:.3f}s")

        # TIMING: Song resolution
        song_start = time.time()
        new_song_obj = resolve_song(metadata, new_artist_obj)
        song_time = time.time() - song_start
        mlog(f"  └─ Song resolution: {song_time:.3f}s")

        if new_song_obj:
            added_count = added_count +1
            added_songs.append(new_song_obj)
        else:
            skipped_count = skipped_count +1
            skipped_songs.append(f"{metadata["artist_name"]} - {metadata["title"]}")
            reason_for_skipping.append("Song already existss")
            print(metadata)

        total_time = time.time() - song_start_time
        file_timings[file_name] = {
            'total': total_time,
            'metadata': metadata_time,
            'artist': artist_time,
            'song': song_time
        }
        mlog(f"  ⏱️  TOTAL for this file: {total_time:.3f}s")

        print("##########")
        print()

        # submit_global_database_session()

    # Print timing summary
    print("\n" + "="*70)
    print("PERFORMANCE SUMMARY")
    print("="*70)
    if file_timings:
        total_time_all = sum(t['total'] for t in file_timings.values())
        avg_time = total_time_all / len(file_timings)
        max_time = max(f['total'] for f in file_timings.values())
        
        print(f"Total time for all files: {total_time_all:.3f}s")
        print(f"Average time per file: {avg_time:.3f}s")
        print(f"Slowest file: {max_time:.3f}s\n")
        
        print("Per-file breakdown:")
        for fname, times in sorted(file_timings.items(), key=lambda x: x[1]['total'], reverse=True):
            print(f"  {fname}")
            print(f"    ├─ Metadata: {times['metadata']:.3f}s")
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
    
    print("="*70)
    
    print(f"Skipped {skipped_count} songs:")
    for s_s, reason in zip(skipped_songs, reason_for_skipping):
        print(f"{s_s} (reason - {reason})")
    print()
    print("##########")
    print()
    print()

    print(f"\nDone! Added {added_count}, updated {updated_count}.")
    sorted_songs = sorted(added_songs, key=lambda song: (song.artist.name, song.title))
    return sorted_songs
