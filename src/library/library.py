from rich.console import Console
from rich.table import Table
from src.music_db_manager import get_music_session
from src.datatables import Song, Artist
from src.tag_db_manager import get_tag_session, Tag, SongTag
from sqlalchemy import func, or_
from src.debug import slog

VALID_CATEGORIES = {"title", "artist", "album", "year", "language", "origin", "tag"}

def display_songs():
    session = get_music_session()
    console = Console()

    table = Table(title="Music Library")
    table.add_column("Title", style="cyan")
    table.add_column("Artist", style="magenta")
    table.add_column("Album", style="green")
    table.add_column("Year", justify="right")
    table.add_column("Language")
    table.add_column("Nostalgic", justify="center")
    table.add_column("Melancholic", justify="center")
    table.add_column("Party", justify="center")

    songs = session.query(Song).join(Artist).order_by(Artist.name, Song.year).all()

    for song in songs:
        table.add_row(
            song.title,
            song.artist.name,
            song.album or "—",
            str(song.year) if song.year else "—",
            song.language or "—",
            "✓" if song.nostalgic else "✗",
            "✓" if song.melancholic else "✗",
            "✓" if song.party else "✗",
        )

    session.close()
    console.print(table)

def display_artists():
    session = get_music_session()
    console = Console()

    table = Table(title="🎤 Artists")
    table.add_column("Artist", style="magenta")
    table.add_column("Origin", style="yellow")
    table.add_column("Songs", justify="right", style="cyan")

    artists = session.query(Artist).order_by(Artist.name).all()

    for artist in artists:
        table.add_row(
            artist.name,
            artist.origin or "—",
            str(len(artist.songs))
        )

    session.close()
    console.print(table)

def display_songs_with_tags():
    music_session = get_music_session()
    tag_session = get_tag_session()
    console = Console()

    # Fetch all song tags and build a map: song_id -> [tag names]
    song_tags = tag_session.query(SongTag).all()
    tag_ids = {st.tag_id for st in song_tags}

    tags_by_id = {}
    if tag_ids:
        tags = tag_session.query(Tag).filter(Tag.id.in_(tag_ids)).all()
        tags_by_id = {tag.id: tag.name for tag in tags}

    song_tag_map = {}
    for st in song_tags:
        song_tag_map.setdefault(st.song_id, []).append(tags_by_id.get(st.tag_id, "?"))

    tag_session.close()

    # Build the table
    table = Table(title="🎵 Songs & Tags")
    table.add_column("Title", style="cyan")
    table.add_column("Artist", style="magenta")
    table.add_column("Album", style="green")
    table.add_column("Year", justify="right")
    table.add_column("Tags", style="yellow")

    songs = music_session.query(Song).join(Artist).order_by(Artist.name, Song.year).all()

    for song in songs:
        tag_list = song_tag_map.get(song.id, [])
        tag_str = ", ".join(sorted(tag_list)) if tag_list else "—"
        table.add_row(
            song.title,
            song.artist.name,
            song.album or "—",
            str(song.year) if song.year else "—",
            tag_str,
        )

    music_session.close()
    console.print(table)

def get_song_from_artist_and_name(category: str, query: str) -> list[tuple[str, str]]:
    """
    Search songs by category and query string.
    Valid categories: title, artist, album, year, language, origin, tag.
    Returns a list of (artist_name, song_title) tuples.
    """
    category = category.strip().lower()
    query = query.strip()

    if category not in VALID_CATEGORIES:
        print(f"Invalid category '{category}'. Valid options: {', '.join(sorted(VALID_CATEGORIES))}")
        return []

    music_session = get_music_session()
    tag_session = get_tag_session()
    console = Console()

    # Base query — always join Artist
    db_query = music_session.query(Song).join(Artist)

    # --- Filter by category ---
    if category == "title":
        db_query = db_query.filter(func.lower(Song.title).contains(query.lower()))

    elif category == "artist":
        db_query = db_query.filter(func.lower(Artist.name).contains(query.lower()))

    elif category == "album":
        db_query = db_query.filter(func.lower(Song.album).contains(query.lower()))

    elif category == "year":
        db_query = db_query.filter(Song.year == int(query))

    elif category == "language":
        db_query = db_query.filter(func.lower(Song.language).contains(query.lower()))

    elif category == "origin":
        db_query = db_query.filter(func.lower(Artist.origin).contains(query.lower()))

    elif category == "tag":
        # Look up matching tags in tag.db first
        matching_tags = (
            tag_session.query(Tag)
            .filter(func.lower(Tag.name).contains(query.lower()))
            .all()
        )
        if not matching_tags:
            print(f"No tags found matching '{query}'.")
            music_session.close()
            tag_session.close()
            return []

        tag_ids = [tag.id for tag in matching_tags]
        song_ids = [
            st.song_id for st in
            tag_session.query(SongTag).filter(SongTag.tag_id.in_(tag_ids)).all()
        ]
        if not song_ids:
            print(f"No songs found with tag matching '{query}'.")
            music_session.close()
            tag_session.close()
            return []

        db_query = db_query.filter(Song.id.in_(song_ids))

    songs = db_query.order_by(Artist.name, Song.year).all()

    if not songs:
        print(f"No results for {category}='{query}'.")
        music_session.close()
        tag_session.close()
        return []

    # Build tag map for results
    result_song_ids = {song.id for song in songs}
    song_tags = tag_session.query(SongTag).filter(SongTag.song_id.in_(result_song_ids)).all()
    tag_ids_needed = {st.tag_id for st in song_tags}
    tags_by_id = {}
    if tag_ids_needed:
        tags_by_id = {
            tag.id: tag.name
            for tag in tag_session.query(Tag).filter(Tag.id.in_(tag_ids_needed)).all()
        }
    song_tag_map = {}
    for st in song_tags:
        song_tag_map.setdefault(st.song_id, []).append(tags_by_id.get(st.tag_id, "?"))

    tag_session.close()

    # Print results
    table = Table(title=f"Results for {category}='{query}'")
    table.add_column("Title", style="cyan")
    table.add_column("Artist", style="magenta")
    table.add_column("Album", style="green")
    table.add_column("Year", justify="right")
    table.add_column("Language", style="white")
    table.add_column("Origin", style="white")
    table.add_column("Tags", style="yellow")

    results = []
    for song in songs:
        tag_str = ", ".join(sorted(song_tag_map.get(song.id, []))) or "—"
        table.add_row(
            song.title,
            song.artist.name,
            song.album or "—",
            str(song.year) if song.year else "—",
            song.language or "—",
            song.artist.origin or "—",
            tag_str,
        )
        results.append((song.artist.name, song.title))

    music_session.close()
    console.print(table)
    return results

def edit_song_entry(song_query: str, category: str, new_value: str):
    """
    Edit a song or its artist's fields.
    song_query format: "Artist - Song Title"
    Editable categories: title, album, year, language, origin.
    """

    old_value = None

    # --- Parse "Artist - Song Title" ---
    if " - " not in song_query:
        print("Invalid format. Use: 'Artist - Song Title'")
        return

    artist_name, song_title = [part.strip() for part in song_query.split(" - ", 1)]
    category = category.strip().lower()
    new_value = new_value.strip()

    if category not in VALID_CATEGORIES:
        print(f"Invalid category '{category}'. Editable options: {', '.join(sorted(VALID_CATEGORIES))}")
        return

    # --- Open session and find the song ---
    session = get_music_session()

    song = (
        session.query(Song)
        .join(Artist)
        .filter(
            func.lower(Song.title) == song_title.lower(),
            func.lower(Artist.name) == artist_name.lower(),
        )
        .first()
    )

    if not song:
        session.close()
        print(f"Song '{song_title}' by '{artist_name}' not found.")
        return

    # --- Apply the change ---
    if category == "title":
        old_value = song.title
        song.title = new_value

    if category == "artist":
        old_value = artist_name
        artist_name = new_value

    elif category == "album":
        old_value = song.album or "—"
        song.album = new_value

    elif category == "year":
        if not new_value.isdigit():
            session.close()
            print(f"Year must be a number, got '{new_value}'.")
            return
        old_value = str(song.year) if song.year else "—"
        song.year = int(new_value)

    elif category == "language":
        old_value = song.language or "—"
        song.language = new_value

    elif category == "origin":
        old_value = song.artist.origin or "—"
        song.artist.origin = new_value

    session.commit()
    session.close()

    if not old_value:
        old_value = "---"

    print(f"Updated {category} for '{song_title}' by '{artist_name}': '{old_value}' → '{new_value}'.")

def search_songs_from_name(query: str) -> list[tuple[str, str]]:
    """
    Progressively narrow down songs by query words.
    Returns a list of (artist_name, song_title) tuples.
    Format: "Artist - Song Title"
    """

    words = query.strip().split()
    if not words:
        print("Empty query.")
        return []
    
    slog(words)

    session = get_music_session()

    slog(session)

    # --- Step 1: fetch all songs matching the first word ---
    def build_filter(word):
        w = word.lower()
        return or_(
            func.lower(Song.title).contains(w),
            func.lower(Artist.name).contains(w),
            func.lower(Song.album).contains(w),
        )

    candidates = (
        session.query(Song)
        .join(Artist)
        .filter(build_filter(words[0]))
        .all()
    )

    slog(candidates)

    # --- Step 2: narrow down with each subsequent word ---
    for word in words[1:]:
        if len(candidates) <= 1:
            break

        w = word.lower()
        narrowed = [
            song for song in candidates
            if w in (song.title or "").lower()
            or w in (song.artist.name or "").lower()
            or w in (song.album or "").lower()
        ]

        # Only apply the narrowing if it didn't wipe out all results
        if narrowed:
            candidates = narrowed

    if not candidates:
        print(f"No songs found for query '{query}'.")
        return []

    results = [(song.artist.name, song.title) for song in candidates]

    session.close()

    if len(results) == 1:
        print(f"Found: {results[0][0]} - {results[0][1]}")
    else:
        print(f"Multiple matches found for '{query}':")
        for i, (artist, title) in enumerate(results, 1):
            print(f"  {i}. {artist} - {title}")

    return results