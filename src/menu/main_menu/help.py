"""Main menu -> Help: short texts for a newcomer, one topic at a time, laid
out by hand within 100 columns (a test checks). Keep the
menu paths here in step with the menus (a test checks the topics' paths
against the real menu labels)."""
from utils.ui.menu_utils import clear_screen, execute_menu_item

BOLD, RESET = "\033[1m", "\033[0m"

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
Settings - lookup modules and their order, matching thresholds, and Server: the database folder,
  backups, this PC's folders and network, indexing the music library, sharing with phones, and the
  public copy on GitHub.
Help - this.

MusicDatabase starts every day with a backup of the databases, then indexes the music library
and refreshes the copy the phones download.
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
MusicDatabase backs the databases up once a day when it starts (and before every database
upgrade); Settings -> Server -> "Back up database now" takes a snapshot before something big.
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
    "Installing and more help": """
Installing MusicDatabase on a new PC (Python, the venv, the first start) is described in README.md
in the program's folder, also on GitHub: https://github.com/shian1000/LeinaMusicDatabase
Machine settings live in .env next to it (described in .env.example); the app writes the folders
and the network interface there itself (Settings -> Server).
""",
}


def _show(title: str) -> None:
    clear_screen()
    print(f"{BOLD}{title}{RESET}\n")
    print(TOPICS[title].strip("\n"))
    print()


def help_menu() -> None:
    action_map = {title: (lambda title=title: _show(title)) for title in TOPICS}
    execute_menu_item("Help - pick a topic", action_map, exit_label="Back")
