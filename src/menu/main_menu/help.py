"""Main menu -> Help: short texts for a newcomer, one topic at a time, within
100 columns (a test checks). Keep the
menu paths here in step with the menus (a test checks the topics' paths
against the real menu labels)."""
import textwrap

from utils.ui.menu_utils import clear_screen, execute_menu_item

BOLD, RESET = "\033[1m", "\033[0m"

# What each library the program imports itself is for - the same text is in
# README.md -> Libraries (a test checks). Packages only these need aren't listed.
LIBRARIES = [
    ("SQLAlchemy", "The databases: music.db, tag.db, files.db and each user's user.db are SQLite files "
                   "read and written through it."),
    ("questionary", "The menus, lists to tick and questions in the terminal."),
    ("rich", "The coloured tables and summaries in the terminal (song lists, import results)."),
    ("mutagen", "Reads and writes the tags of music files (artist, title, album, cover) for imports, "
                "the music library index and fixing file names and tags."),
    ("Pillow", "Opens the cover pictures found in music files, to save them and tell identical "
               "ones apart."),
    ("musicbrainzngs", "MusicBrainz searches for a song's recordings: its album and release year."),
    ("wikipedia", "Wikipedia lookups for a song's album and release year."),
    ("requests", "Plain web requests: MusicBrainz (spelling and genres), Wikipedia, lyrics for guessing "
                 "a song's language, the public catalog's GitHub release and the check for a newer yt-dlp."),
    ("beautifulsoup4", "Reads the web pages the lookups get back (Wikipedia, iTunes, Spotify, Google, "
                       "YouTube)."),
    ("selenium", "Drives a hidden Chrome/Chromium for the lookups that only work in a real browser "
                 "(Google, Genius, iTunes, Spotify, YouTube search)."),
    ("yt-dlp", "YouTube: searching videos for songs, whether a video still plays, importing playlists "
               "and downloading a song's audio. Its extras - deno and yt-dlp-ejs (YouTube's JavaScript "
               "checks), brotli, pycryptodomex, websockets - are what YouTube needs today."),
    ("google-api-python-client, google-auth, google-auth-oauthlib",
     "The YouTube Data API: creating YouTube playlists from songs, reading private playlists, and "
     "checking many videos at once with an API key."),
    ("langdetect, pycountry", "Guessing a song's language from its lyrics, and the language's name."),
    ("RapidFuzz", "Fuzzy comparison of names when copying songs' files from the music library."),
    ("universal_pathlib, smbprotocol", "Paths that may be a local folder or an smb:// network share "
                                       "(the music library on a NAS)."),
    ("python-dotenv", "Reads and writes this PC's settings in .env."),
    ("pyperclip", "Copies a song's artist and title to the clipboard while Clean names or filling in albums "
                  "and years asks about it, ready to paste into a search."),
    ("packaging", "Compares version numbers for the yt-dlp update offer."),
    ("pytest", "Runs the tests (requirements-dev.txt; the app doesn't need it)."),
]
LIBRARIES_NOTE = ("requirements.txt also pins the packages these need themselves, so every install "
                  "gets the same versions.")


def _libraries_text() -> str:
    lines = []
    for name, text in LIBRARIES:
        lines.append(textwrap.fill(f"{name} - {text}", width=100, subsequent_indent="  "))
    lines += ["", textwrap.fill(LIBRARIES_NOTE, width=100)]
    return "\n".join(lines)


def _startup_text() -> str:
    """Every start-up step in order (utils/ui/startup_tasks.py), numbered
    across both lists, with each optional one's current switch."""
    from utils.common import startup_settings as switches
    from utils.database.public_database import daily_publish_enabled

    first = "the day's first start"
    # (always: why it can't be switched off, or optional: (label, on now, when)), in start-up order
    steps = [
        ("Waiting for the automatic request check to finish",
         "two programs writing one database at once could lose changes."),
        ("On a fresh install: the public catalog or an empty database", "nothing can run without one."),
        (switches.BACKUP, first),
        ("Database upgrades (migrations), with a backup right before",
         "the program expects the current layout of the databases."),
        ("Linking songs to albums right after the albums' upgrade", "part of that upgrade."),
        ("Keeping tags, playlists and the library index pointing at merged or deleted songs",
         "otherwise they'd point at songs that are gone."),
        (switches.MOOD_CHANGES, "every start"),
        (switches.LIBRARY_INDEX, first),
        (switches.SHARE_COPY, first),
        ("Daily publish to GitHub (when set up)", None),
        (switches.YTDLP_UPDATE, first),
        (switches.MESSAGES, "every start"),
        ("Continuing the sharing setup after its restart",
         "only after the setup restarted the program, which you agreed to."),
    ]
    always, optional = ["Always (and why they can't be switched off):"], [
        'Optional - Settings -> "Start-up tasks", now:']
    for number, (what, detail) in enumerate(steps, start=1):
        if isinstance(what, switches.StartupSwitch):
            on = switches.enabled(what)
            optional.append(f"{number:>3}. [{'ON ' if on else 'OFF'}] {what.label} - {detail}")
        elif detail is None:
            on = daily_publish_enabled()
            optional.append(f"{number:>3}. [{'ON ' if on else 'OFF'}] {what} - {first}")
        else:
            always.append(textwrap.fill(f"{number:>3}. {what} - {detail}", width=100, subsequent_indent="     "))
    return "\n".join(always + [""] + optional + [
        "",
        "The numbers are the order they run in. With the daily backup off, every start warns once the",
        "newest backup is more than 7 days old. The GitHub publish is also switched in Settings ->",
        'Server -> "Public database on GitHub".',
    ])


# A topic is its text, or a function making it (for what depends on the settings)

TOPICS = {
    "What MusicDatabase is for": """
MusicDatabase is a catalog of the music you listen to: artists, songs, albums, years, genres,
moods (tags), YouTube and Spotify links, and where each song's file is in your music library. It
fills in what's missing from MusicBrainz, Wikipedia, iTunes, Genius, Spotify and YouTube, and
helps keep it clean.

Leina Radio is its Android app. The phone downloads MusicDatabase's catalog (music.db, tag.db and
files.db) - from this PC when it's set up as a server, or from the public copy on GitHub - and plays
the songs from the phone's own files, this PC's music library or YouTube. What the phone finds or
corrects comes back to MusicDatabase as requests, and nothing changes in the catalog until you
accept them here. MusicDatabase's values always win; the phone only fills gaps for itself.
""",
    "Day to day: the main menus": """
Enter database - the catalog itself:
  - Fetch songs / Fetch artists: search, then act on what you found (edit, tags, links, genre,
    change artist, make a YouTube playlist, remove...).
  - Download YT song: save a YouTube video's audio into the import/ folder.
  - Manage database: adding songs in bulk, filling in data, requests from phones, clean-ups.
  - Show whole database: everything in one table.
Users - the accounts Leina Radio logs in with: create, rename, passwords, permissions, their
  playlists, and the moods (global ones and each user's own).
Statistics - how often each lookup module (albums, years) found something.
Settings - lookup modules and their order, matching thresholds, the start-up tasks, and Server:
  the database folder, backups, this PC's folders and network, indexing the music library, sharing
  with phones, and the public copy on GitHub.
Help - this.

On the day's first start MusicDatabase backs the databases up, indexes the music library,
refreshes the copy the phones download, publishes the public copy to GitHub (when set up) and
offers a newer yt-dlp; every start applies global mood changes from phones and shows what the
automatic request check did and how many requests wait. Each can be switched off in Settings ->
"Start-up tasks" (all on by default).
""",
    "Adding songs": """
From mp3 files' tags - Enter database -> Manage database -> Fetch database data ->
  "Import data from mp3 tags": pick a folder (the browser starts in import/). Artist and title come
  from the tags, MusicBrainz checks the spelling, and questions (a possible duplicate, a swapped
  artist and title, ...) are asked together at the end.
From a YouTube playlist - same menu -> "Import data from YouTube playlist": paste the playlist's
  link. Artist and title are read from the video titles; the videos become the songs' YouTube links.
A single YouTube video - Enter database -> "Download YT song" saves its audio into import/; then
  import that folder with "Import data from mp3 tags".
By hand - Enter database -> Manage database -> Manual management -> "Add song".
From Leina Radio - a song played on the phone that MusicDatabase doesn't have is sent as a
  request: Enter database -> Manage database -> "Resolve new requests" (the number in brackets
  is how many wait). A phone without a server sends a .zip package instead - "Import a request
  package" in the same menu.
While making a playlist from a folder - Users -> Enter user database -> "Create playlist from
  folder" offers to add a file nothing matched as a new song.

Afterwards: Manage database -> Fetch database data -> "Fill missing data" (albums, years, genres),
"Import covers from mp3 tags", and Settings -> Server -> "Index music library" to link the files.
""",
    "Keeping the catalog clean": """
All in Enter database -> Manage database:
  - Resolve duplicates -> "Resolve duplicated artists": merges artists (and their songs) whose names
    differ only in case, apostrophes or look-alike letters, without asking. "Resolve duplicated
    songs": near duplicates ('feat.', '(Visualiser)') in one list to tick.
  - Merge artists / Divide artists: by hand, when one artist row is really two, or two are one.
  - Split joined artist names: 'A & B' stored as one artist.
  - Find full albums: songs that are a whole album in one recording.
  - Resolve albums with several artists: one album title shared by different artists.
  - Fill in Latin forms of names in other alphabets: 'Кино' -> 'Kino' for Leina Radio.
  - Fix names and tags for mp3 files that are in the database: rename and re-tag the files
    after their song.
  - Spell check existing data: artist and title spelling against MusicBrainz.
  - Clean names: invisible characters, double spaces, broken encoding, look-alike letters, junk
    like 'Official Video' in titles.
MusicDatabase backs the databases up once a day when it starts (unless switched off in Settings ->
"Start-up tasks") and always before a database upgrade; Settings -> Server -> "Back up database
now" takes a snapshot before something big.
""",
    "This PC as the server for phones": """
To let Leina Radio download the catalog from this PC (and log in, sync playlists, send requests,
play your music library's files), run Settings -> Server -> "Setup this PC for database sharing"
and answer yes to the step-by-step setup. It explains each setting (the sharing folder, an
external drive, the addresses to listen on, your music library) and shows the addresses the
server will listen on - a phone must be able to reach one of them (the home network, a VPN, ...).
Then it checks the first copy of the databases, the first user, the server service and lingering,
and shows exactly what to type in the phone (Leina Radio -> Ustawienia -> Serwer). Nothing is done
without asking; it's safe to stop and to run again.

The same settings one by one: Settings -> Server -> "Folders and network".
Without a server: Settings -> Server -> "Public database on GitHub" publishes a copy phones can
download from GitHub.
""",
    "What happens at start-up": _startup_text,
    "Libraries": _libraries_text,
    "Installing and more help": """
Installing MusicDatabase on a new PC (Python, the venv, the first start) is described in README.md
in the program's folder, also on GitHub: https://github.com/shian1000/LeinaMusicDatabase
Machine settings live in .env next to it (described in .env.example); the app writes the folders
and the network interface there itself (Settings -> Server).
""",
}


def topic_text(title: str) -> str:
    topic = TOPICS[title]
    return topic() if callable(topic) else topic


def _show(title: str) -> None:
    clear_screen()
    print(f"{BOLD}{title}{RESET}\n")
    print(topic_text(title).strip("\n"))
    print()


def help_menu() -> None:
    action_map = {title: (lambda title=title: _show(title)) for title in TOPICS}
    execute_menu_item("Help - pick a topic", action_map, exit_label="Back")
