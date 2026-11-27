"""Main menu -> Help: short texts for a newcomer, one topic at a time, within
100 columns (a test checks). Keep the
menu paths here in step with the menus (a test checks the topics' paths
against the real menu labels)."""
import textwrap

import questionary

from utils.ui.menu_utils import clear_screen, execute_menu_item
from utils.common.output import BOLD, RESET
from utils.ui.prompts import choose


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
    ("requests", "Plain web requests: MusicBrainz (spelling, genres and languages), Wikipedia, song lyrics (LRCLIB, Genius, Musixmatch, LyricsTranslate, Tekstowo, Fajnanuta, Cuspajz), "
                 "a song's language on Musixmatch, the public catalog's GitHub release, the check for a newer yt-dlp, and the iTunes "
                 "and YouTube Music album searches."),
    ("beautifulsoup4", "Reads the web pages the lookups get back (Wikipedia, Spotify, Google, "
                       "Genius)."),
    ("selenium", "Drives a hidden Chrome/Chromium for the lookups that only work in a real browser "
                 "(Google, Genius, Spotify)."),
    ("yt-dlp", "YouTube: searching videos for songs, whether a video still plays, importing playlists "
               "and downloading a song's audio. Its extras - deno and yt-dlp-ejs (YouTube's JavaScript "
               "checks), brotli, pycryptodomex, websockets - are what YouTube needs today."),
    ("google-api-python-client, google-auth, google-auth-oauthlib",
     "The YouTube Data API: creating YouTube playlists from songs, reading private playlists, and "
     "checking many videos at once with an API key (whether they play, their lengths, their audio "
     "language)."),
    ("langdetect, pycountry", "Guessing a song's language from its lyrics, and the language's name."),
    ("cutlet, fugashi, unidic-lite, pypinyin, ToJyutping", "Suggesting Latin forms of Japanese names "
                                                           "(Hepburn, from a Japanese dictionary), Mandarin "
                                                           "ones (pinyin) and Cantonese ones (Jyutping)."),
    ("mishkal", "Puts the unwritten short vowels into Arabic names, so their Latin form can be "
                "suggested (with the PyArabic, qalsadi, ... packages it needs)."),
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
        ("Waiting for the automation to stop (it finishes the song it's on, then stops)",
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
        (switches.YTDLP_UPDATE, first),
        ("Publish to GitHub (when set up)", None),
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
            optional.append(f"{number:>3}. [{'ON ' if on else 'OFF'}] {what} - once its interval is up "
                            f'(Settings -> "Automation")')
        else:
            always.append(textwrap.fill(f"{number:>3}. {what} - {detail}", width=100, subsequent_indent="     "))
    return "\n".join(always + [""] + optional + [
        "",
        "The numbers are the order they run in. With the daily backup off, every start warns once the",
        "newest backup is more than 7 days old. The GitHub publish is also switched in Settings ->",
        'Server -> "Public database on GitHub".',
    ])


_BACKGROUND_SERVICES = """
Settings -> Server -> "Setup this PC for database sharing" installs four systemd user units in
~/.config/systemd/user/ (user units: no root, they run as you). They're only there once you said
yes to installing the server.

musicdatabase-share.service + musicdatabase-share.timer - the server for Leina Radio
  The timer starts the service a minute after your user's systemd starts (at login, or at boot
  with lingering on). It serves only music.db, tag.db and files.db from the sharing folder, your
  music library's files, logging in, syncing users' playlists, moods and saved songs, and takes
  requests from Leina Radio into the sharing folder's Inbox/. It listens on one port (8002 unless
  you chose another) and, unless you turned it off, serves HTTPS with a certificate it makes for
  itself on its first start - Leina Radio compares its code once. It listens only on the
  addresses you chose: the home network interface, the VPN's (any provider's interface you
  picked, or Tailscale's address; none unless you chose one) and any other address you added -
  never on all of them unless you added 0.0.0.0 yourself.
  An address that isn't there yet is tried again every 10 seconds. While the sharing folder (or
  its external drive) is missing it keeps running and answers 503 (unavailable), so Leina Radio
  just tries again later. If it stops, systemd starts it again after 10 seconds.

musicdatabase-inbox.service + musicdatabase-inbox.timer - the automation
  The timer runs auto_resolve_requests.py 5 minutes after your systemd starts and then every 5
  minutes; each run does only what Settings -> "Automation" makes due (new requests and the
  clean-ups every 15 minutes, the GitHub publish once a day, unless you set other times there).
  The clean-ups are the steps of "Run all clean-ups and lookups in order" that need no internet,
  the lookups (on their own interval) spell check, albums, years, genres and lyrics: with nobody
  to ask, each list takes only what it ticks at first, and only when you turned its rule on there
  (all off at first) or one of your own rules there holds for it (if similarity >= 0.9 and ...);
  the rest waits for your next Run all. A song the timer's lookups found
  nothing for is left out of them for two weeks (your own runs still try it). It looks at new
  requests from Leina Radio and settles only what its rules allow (each one can be turned off
  there): a dead YouTube link is replaced when Leina Radio's suggestion and its own search found
  the same video and it plays here, a found link is saved by the same rule, and a correction is
  saved when a user with that permission sent it and nothing about it needs a judgement. Your own
  rules there may take more - a song to add too (if MusicBrainz agrees >= 0.99 and ...); a few
  changes no rule may make (a merge, a song that is gone, a video found bad). Everything else is
  left for Enter database -> Manage database -> "Resolve new requests", with a note why. It never
  runs while MusicDatabase is open - started during a run, MusicDatabase asks it to stop and
  waits for the song it's on - nor while the databases wait for an upgrade. Each lookup also
  stops after a few minutes (Settings -> "Automation"), keeping what it found. What it changed is
  shown at the next start (unless Start-up tasks hide the messages) and in "Resolve new
  requests"; its output is in the journal. After a change it refreshes the copy for Leina Radio.
  Your own answers (in the request review and in every checkbox list or yes/no question) go into
  data/decision_log.jsonl, the material for new rules.

When they run: systemd runs a user's units only while that user is logged in - with lingering on
(loginctl enable-linger, offered by the setup) from boot, logged in or not.

Checking, stopping, removing (in a terminal):
  systemctl --user status musicdatabase-share.service       is the server running?
  systemctl --user list-timers 'musicdatabase-*'            when the timers run next
  journalctl --user -u musicdatabase-share -u musicdatabase-inbox    what they printed
  systemctl --user stop musicdatabase-share.service         stop the server until the next login
  systemctl --user disable --now musicdatabase-share.timer musicdatabase-share.service
      musicdatabase-inbox.timer musicdatabase-inbox.service       turn them off for good
  then delete the four files in ~/.config/systemd/user/ and run: systemctl --user daemon-reload
Running the setup again reinstalls them with the current settings; it also prints these commands.
"""


def show_unit_files() -> None:
    """The four units as installed, or what the setup would write - read-only."""
    from utils.database.sharing import unit_files

    for unit in unit_files():
        if unit.installed and unit.outdated:
            state = "installed - shown as it is on disk; the setup would now write it differently"
        elif unit.installed:
            state = "installed - shown as it is on disk"
        else:
            state = "NOT installed - what the setup would write now, with the current settings"
        print(f"{BOLD}{unit.path}{RESET} ({state})")
        print(unit.text.rstrip("\n"))
        print()


# Options shown under a topic's text, besides going back
TOPIC_ACTIONS = {"Background services (Linux)": {"Show the full unit files": show_unit_files}}


# A topic is its text, or a function making it (for what depends on the settings)

TOPICS = {
    "What MusicDatabase is for": """
MusicDatabase is a tool for managing a music database. It stores artists, songs, albums,
years, genres, moods (tags), YouTube and Spotify links, and where each song's file is in your
music library. It fills in what's missing from MusicBrainz, Wikipedia, iTunes, Genius, Spotify and
YouTube, and helps keep it clean.

Leina Radio is a music player for Android which uses this database. The app works on music.db,
tag.db and files.db which it can take from the server or from the GitHub repository. It can also
play music stored on the server. The app can also send requests for database correction or report
new findings - you can accept these here. MusicDatabase's values always win; Leina Radio only fills
gaps for itself.
""",
    "Day to day: the main menus": """
Enter database - the catalog itself:
  - Fetch songs / Fetch artists: search, then act on what you found (edit, tags, links, genre,
    change artist, make a YouTube playlist, remove...).
  - Download YT song: save a YouTube video's audio into the import/ folder.
  - Manage database: adding songs in bulk, filling in data, requests from Leina Radio, clean-ups.
  - Show whole database: artists, songs, or songs with their tags.
Users - the accounts Leina Radio logs in with: create, rename, passwords, permissions, their
  playlists, and the moods (global ones and each user's own). Accounts are created only here,
  in MusicDatabase.
Statistics - how often each lookup module (albums, years) found something.
Settings - lookup modules and their order, matching thresholds, which name clean-ups run
  ("Name cleanup"), the steps "Run all clean-ups and lookups in order" runs ("Run all: steps"),
  what the automation does by itself and how often ("Automation"), the start-up tasks, how much
  goes into debug.log ("Debug log": every step and call of an import from 1 up), and Server: the
  database folder, backups, this PC's folders and network, indexing the music library, sharing
  with Leina Radio, and the public copy on GitHub.
Help - this.

On the day's first start MusicDatabase backs the databases up, indexes the music library,
refreshes the copy Leina Radio downloads and offers a newer yt-dlp; it publishes the public copy to
GitHub (when set up) once Settings -> "Automation"'s interval is up; every start applies global
mood changes from Leina Radio and shows what the automatic request check did and how many requests
wait. Each can be switched off in Settings ->
"Start-up tasks" (all on by default).
""",
    "Adding songs": """
From mp3 files' tags - Enter database -> Manage database -> Fetch database data ->
  "Import data from mp3 tags": pick a folder (the browser starts in import/). Artist and title come
  from the tags, MusicBrainz checks the spelling, and questions (a possible duplicate, a swapped
  artist and title, ...) are asked together at the end.
From a YouTube playlist - same menu -> "Import data from YouTube playlist": paste the playlist's
  link. Artist and title are read from the video titles; the videos become the songs' YouTube links.
  At the end it offers to look for the new songs' files in the music library and link them.
A single YouTube video - Enter database -> "Download YT song" saves its audio into import/.
By hand - Enter database -> Manage database -> Manual management -> "Add song": title and artist
  are required, the rest optional; it is checked like an import (spelling, an existing artist or
  a similar song).
From Leina Radio - a song it played that MusicDatabase doesn't have is sent as a
  request: Enter database -> Manage database -> "Resolve new requests" (the number in brackets
  is how many wait). Leina Radio without a server sends a .zip package instead - "Import a request
  package" in the same menu.
While making a playlist from a folder - Users -> Enter user database -> "Create playlist from
  folder" offers to add a file nothing matched as a new song.

Each of these (mp3 tags, a YouTube playlist, Leina Radio's requests) ends by asking which missing
data to fill in for the imported songs - albums, then years (a year in a video's or file's title,
'Song (1991)', is offered too). Later: Manage database -> Fetch database data -> "Fill missing data"
(albums, years, genres, lyrics, languages for the whole catalog; Years also checks the years
already stored - one later than the song's first release on MusicBrainz is a reissue's or a
compilation's, offered to change in one list; a live version takes an album or year only from a
live release - '... (Live at ...)', 'Live in ...', a live album on MusicBrainz -, the rest is asked
at the end, unticked, since a lookup by artist and title finds the studio original's; the timer
never saves those), "Import covers from mp3 tags", and Settings ->
Server -> "Index music library" to link the files.
""",
    "Keeping the catalog clean": """
All in Enter database -> Manage database:
  - Run all clean-ups and lookups in order: the steps below that need no folder or pick, one after
    another - requests, clean names, split and merge artists, swapped artist and title, spell
    check, songs' duplicates, full albums, projects, covers, artists without songs, albums, their
    names, years, genres, lyrics, languages, Latin forms. A list first lets
    you leave some out this time; which ones are ticked there is set in Settings -> "Run all:
    steps". Ctrl+C stops the step running and asks whether to go on.
  - Resolve duplicates -> "Resolve duplicated artists": merges artists (and their songs) whose names
    differ only in case, apostrophes or look-alike letters, without asking. "Resolve duplicated
    songs": near duplicates ('feat.', '(Visualiser)') in one list to tick, and - unticked - one
    title credited to two different artists who share another artist or a link. "Resolve
    duplicated additional names of artists": first works out the artists' names in Latin letters
    (each transliteration system, Ukrainian's national and Slavic ones apart), then removes
    synonyms that repeat a name exactly; ones differing only in case or accents are listed to tick.
  - Merge artists / Divide artists: by hand, when one artist row is really two, or two are one.
  - Manual management -> "Edit artist" -> synonyms: the other names an artist goes by (a channel
    name, another spelling), which search, matching and Leina Radio use. Untick the wrong ones,
    type new ones separated by commas.
  - Split joined artist names: 'A & B' stored as one artist.
  - Find songs with artist and title swapped: 'Renegade - Styx' - a title that is another artist's
    name, by an artist with no other song - in one list to tick.
  - Remove artists without songs: artists left over from deleted songs or songs credited to another
    artist, in one list to tick; one spelt like an artist with songs is merged into it, or deleted
    as a misspelling. Artists only covered by a song stay.
  - Find full albums: songs that are a whole album in one recording.
  - Projects: the series recordings were made for (Tiny Desk, Like a Version): a song keeps its
    real artist and is shown "Project: Artist - Title". The dictionary, and stored songs to mark.
  - Find covers: songs whose titles say they're covers, marked with the artist covered and shown
    'Artist - Title (Original cover)'. For titles saying only 'cover' it asks whose song it is.
    Then it asks which are translated covers - words of their own in another language (also Song
    actions -> 'Set translated cover'): their lyrics are looked up only as their own, while a cover
    singing the original's words takes the original song's language.
  - Resolve albums with several artists: one album title shared by different artists.
  - Resolve soundtrack album names: one soundtrack spelt several ways, brought to 'Title OST'.
  - Soundtrack works: films, games, anime a song gets credited to with no 'OST' in the name
    ('Nana - Kuroi Namida'): the song is then found by its title among every artist's songs
    whose album names the work.
  - Fill in Latin forms of names in other alphabets: 'Кино' -> 'Kino' for Leina Radio.
  - Unify language and country names: one ISO 639-3 code per language ('Finnish' -> 'fin',
    'Farsi' -> 'fas', 'pl' -> 'pol') and one spelling per country in artists' origins, in
    English ('USA' -> 'United States', 'England - London' -> 'United Kingdom - England -
    London'), origins that aren't a place cleared, since Leina Radio counts and searches them by
    the exact value. Every one saved since
    is spelt that way already.
  - Fix names and tags for mp3 files that are in the database: rename and re-tag the files
    after their song.
  - Spell check existing data: artist and title spelling against MusicBrainz.
  - Clean names: invisible characters, double spaces, broken encoding, accented letters stored in
    two parts, look-alike letters, junk like 'Official Video' in titles, the song's own artists
    written into its title ('Artist / Title'), featured artists moved out of titles into the
    songs' artists ('Song ft. X', in a list to tick).
Which clean-ups run is chosen in Settings -> "Name cleanup", each one for everything at once - the
mp3 and YouTube imports, Leina Radio's requests and Clean names - so a new name and the same one
already stored are cleaned alike.
MusicDatabase backs the databases up once a day when it starts (unless switched off in Settings ->
"Start-up tasks") and always before a database upgrade; Settings -> Server -> "Back up database
now" takes a snapshot before something big.
""",
    "This PC as the server for Leina Radio": """
To let Leina Radio download the catalog from this PC (and log in, sync playlists, send requests,
play your music library's files), run Settings -> Server -> "Setup this PC for database sharing"
and answer yes to the step-by-step setup. It explains each setting (the sharing folder, an
external drive, the addresses to listen on, your music library) and shows the addresses the
server will listen on - Leina Radio must be able to reach one of them (the home network,
a VPN, ...). Then it checks the first copy of the databases, the first user, the server service
and lingering, and shows exactly what to type in Leina Radio (Ustawienia -> Serwer). Nothing is done
without asking; it's safe to stop and to run again.

The same settings one by one: Settings -> Server -> "Folders and network".
Without a server: Settings -> Server -> "Public database on GitHub" publishes a copy Leina Radio can
download from GitHub.
""",
    "What happens at start-up": _startup_text,
    "Libraries": _libraries_text,
    "Background services (Linux)": _BACKGROUND_SERVICES,
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
    actions = TOPIC_ACTIONS.get(title)
    while actions:
        picked = choose(title, list(actions))
        if picked is None:
            return
        actions[picked]()


def help_menu() -> None:
    action_map = {title: (lambda title=title: _show(title)) for title in TOPICS}
    execute_menu_item("Help - pick a topic", action_map, exit_label="Back")
