# LeinaMusicDatabase

MusicDatabase is a terminal app that keeps a catalog of music: songs, artists, albums, years,
genres, moods and YouTube links. It fills the gaps from MusicBrainz, Wikipedia, iTunes, Genius,
Spotify's and YouTube's public pages, imports your mp3 tags and YouTube playlists, and can index
your own mp3 collection.

It is also the server for **Leina Radio**, the Android player
([shian1000/LeinaPlayer](https://github.com/shian1000/LeinaPlayer)): the phone downloads the
catalog from your MusicDatabase, logs in as one of its users, syncs playlists, moods and
listening stats with it, plays files from your library and sends corrections back for you to
review.

You can start from the project owner's catalog (downloaded on the first run) and extend it, or
start empty.

## Requirements

- **Linux**: Debian 12 or 13, Ubuntu, Linux Mint or similar. (Windows isn't supported yet.)
- **Python 3.13 or newer.** Check with `python3 --version`. Both Debian releases work; only
  where Python 3.13 comes from differs, every later step is the same:
  - Debian 13, Ubuntu 25.04 and newer ship it — install `git` and `python3-venv`:

    ```bash
    sudo apt install git python3-venv
    ```

  - Debian 12 (Python 3.11), Ubuntu 24.04 and Linux Mint 22 (Python 3.12) need a newer Python
    next to the system one. The simplest way is [uv](https://docs.astral.sh/uv/), which installs
    Python into your home folder and leaves the system's alone:

    ```bash
    sudo apt install git curl
    ```

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    Open a new terminal (so `uv` is on your `PATH`), then:

    ```bash
    uv python install 3.13
    ```

    [pyenv](https://github.com/pyenv/pyenv) works too, if you already use it. Either way only
    the app's own virtual environment uses 3.13; the sharing server (below) runs on the system
    `python3`, and Debian 12's 3.11 is fine for it.
- Nothing needs compiling: every pinned package comes as a ready wheel, or is pure Python.
- **Optional — Google Chrome or Chromium** (not the snap package): "Fill missing data" (albums,
  years) reads Spotify, YouTube, Google, Genius and iTunes pages in a hidden browser. Without
  one, those lookups are skipped and the rest (MusicBrainz, Wikipedia, ...) still works. Selenium
  downloads the matching driver itself.
- **Optional — `xclip`** (X11) **or `wl-clipboard`** (Wayland), for "copy to clipboard". Without
  them the app asks the terminal to do it, which most terminals support.
- **Optional — a way for phones to reach the PC from outside your home network**, if you want
  that: any VPN works — the server listens on its interface (for Tailscale, its address is found
  by itself).

## Install

```bash
git clone https://github.com/shian1000/LeinaMusicDatabase.git
```

```bash
cd LeinaMusicDatabase
```

Make the virtual environment. With a system Python 3.13 (Debian 13, Ubuntu 25.04+):

```bash
python3 -m venv venv
```

With uv instead (Debian 12, Ubuntu 24.04, Mint 22):

```bash
uv venv --python 3.13 --seed venv
```

(With pyenv: `pyenv install 3.13`, then `"$(pyenv prefix 3.13)/bin/python" -m venv venv`.)

Then install the packages into it:

```bash
source venv/bin/activate
```

```bash
pip install -r requirements.txt
```

Settings for this computer (folders, network interface, optional keys) live in a file called
`.env`, which never leaves your computer. You don't have to create it by hand: the app writes the
folder settings itself (Settings → Server → Folders and network). For the optional keys below,
copy the example and fill in what you need:

```bash
cp .env.example .env
```

## First run

```bash
source venv/bin/activate
```

```bash
python main.py
```

The first time there's no database yet, so the app asks how to start:

- **Download the public catalog** — the project owner's songs, artists, albums and moods, from
  the GitHub release [`database`](https://github.com/shian1000/LeinaMusicDatabase/releases/tag/database)
  (about 1 MB). The files are checked against the release's `version.json` (size and SHA-256)
  before they're used. It's yours from then on: nothing is synced back to the owner.
- **Start with an empty database** — then import your own music (Enter database → Manage database → Fetch
  database data → "Import data from mp3 tags" or "Import data from YouTube playlist").

The databases are in `src/database/` (`music.db`, `tag.db`, later `files.db`), with daily backups
in `src/database/archive/`. The app moves through menus with the arrow keys and Enter.
**Help** in the main menu explains what each menu is for, every way to add songs, the clean-up
tools and where to set the PC up as Leina Radio's server.

On the day's first start the app backs the databases up, indexes your music library, refreshes
the copy for Leina Radio, publishes the public copy to GitHub (if you set that up) and offers a newer
yt-dlp; every start also applies phones' changes to global moods and shows what the automatic
request check did. Settings → Start-up tasks switches each of these off (all on by default; with
the daily backup off, every start warns once the newest backup is more than 7 days old).
Database upgrades, with their own backup right before, always run. Help → "What happens at start-up" lists every
step in order, with what's on now.

To start it later, run the two commands above, or double-click `MusicDatabase.sh` (it opens a
terminal for itself: your desktop's default one, GNOME Terminal, Konsole, Xfce Terminal or xterm).

## Serving Leina Radio phones

Your PC becomes the phones' server: it serves the catalog on port **8002**, only on the
addresses you choose — your home network's interface, a VPN's (any provider), and any other address of the PC you add. A
phone must be able to reach one of them; how (the home Wi-Fi, a VPN, ...) is up to you. It never
listens on every address unless you add `0.0.0.0` yourself: **no router port forwarding**,
nothing to open. Anyone on your home Wi-Fi can
download the catalog and your library's files (that needs no password); logging in, playlists
and everything personal need a user's password.

**The quick way:** Settings → Server → Setup this PC for database sharing, and answer yes to
the step-by-step setup. It goes through the settings below one at a time — what each is for,
whether it's required, its current value and a suggestion — and lists the addresses the server
will listen on. Nothing is done without asking: saving a value, creating the folder, restarting
the app (the settings are read at start), the first copy of the databases, the first user, the
server service, lingering — each is a yes/no question, and a "no" is listed at the end as not
done, with exactly what to type in the phone. You can stop at any step (what you saved stays)
and run it again any time — it starts from the saved values. The steps below are the same thing by hand.

1. **Pick the folders and the network.** Settings → Server → Folders and network:
   - **Sharing folder** — where the copies for the phones, the users and the phones' requests
     live, for example `~/MusicDatabase-Shared` (type the full path). The app offers to create it.
   - **External drive mount point** — leave it empty when the sharing folder is on your normal
     disk. Set it only if the folder is on an external drive: then nothing is written while the
     drive isn't plugged in.
   - **LAN interface** — "Pick a network interface" and choose the one on your home network
     (Wi-Fi or cable).
   - **VPN** — the VPN the server also listens on: its network interface by name, whatever the
     provider (e.g. `wg0` for WireGuard, `tun0` for OpenVPN), `tailscale` (its address is found
     by itself) or `off`. Empty means no VPN.
   - **Other addresses to listen on** — any other IPv4 address of this PC.

   The server needs at least one of these addresses, and a phone must be able to reach it.
   - **Music library** (optional) — your mp3 folder, so phones can play your files.

   Restart the app (Exit, then `python main.py`): these are read at start.
2. **Turn sharing on.** Settings → Server → Setup this PC for database sharing, answering no to
   the step-by-step setup. It installs a
   small server as a systemd *user* service (`musicdatabase-share`, no root needed) that starts a
   minute after you log in, prints the addresses for the phone and offers to copy the databases
   right away. After that the copy is refreshed once a day when you start the app, or on demand
   with Settings → Server → Submit database for sharing.
3. **Create a user** for each person: Users → Create a new user. Users → Permissions grants
   extras (editing global moods from the phone, corrections saved without review).
4. **On the phone**, install Leina Radio
   ([latest release](https://github.com/shian1000/LeinaPlayer/releases/latest)). In
   *Ustawienia › Serwer* (Settings › Server) enter the addresses the setup printed:
   - *Adres serwera (sieć lokalna)* (local network): `http://<your PC's LAN address>:8002/`,
     e.g. `http://192.168.x.x:8002/`;
   - *Adres serwera (połączenie zdalne)* (remote), if the phone can reach the PC from elsewhere
     (e.g. over a VPN): `http://<that address>:8002/`.

   Then log in under *Ustawienia › Konto* (Account) with the user from step 3.

Good to know:

- **Away from home**: the phone needs some way to reach one of the addresses, e.g. a VPN between
  the phone and the PC: pick that VPN's interface under **VPN** (with Tailscale on both, the
  default already works). At home the phone uses the LAN address.
- **A firewall** (e.g. `ufw`) must allow TCP port 8002 from where the phone connects.
- **An always-on or headless server**: the sharing server is a systemd *user* service, and
  systemd runs a user's services only while that user is logged in — log out (or boot without
  logging in) and the phones can't reach it. On a PC that should serve the phones all the time,
  turn on *lingering* for the account that runs MusicDatabase, once:

  ```bash
  sudo loginctl enable-linger <user>
  ```

  It tells systemd to start that user's own services at boot and keep them running after
  logout, so the server comes up about a minute after boot with nobody logged in (nothing else
  about the account changes). `loginctl show-user <user> --property=Linger` shows it;
  `sudo loginctl disable-linger <user>` turns it off again. Not needed on a desktop you're
  logged into anyway.
- **What runs in the background**: Help → "Background services (Linux)" explains the four
  systemd user units the setup installs (`musicdatabase-share` — the server — and
  `musicdatabase-inbox` — the automatic request check every 15 minutes, each a `.service` plus a
  `.timer` in `~/.config/systemd/user/`), when they run and how to check, stop or remove them; its
  "Show the full unit files" prints them as installed, or what the setup would write.
- **Turning it off**: the setup prints the `systemctl --user` commands for that.
- After changing the folders or the addresses, run "Setup this PC for database sharing"
  again, so the server uses the new values.

More detail (what's served, the phone's requests, playlists sync, logging in):
[`docs/runbooks/database.md`](docs/runbooks/database.md) → "Sharing with the mobile app" and
the sections after it.

## Optional keys and accounts

Everything below is optional. Each goes into `.env` (see `.env.example` for the exact names) or
`.secrets/`, which are never committed. Keep them private.

| What | Used for | Without it |
| --- | --- | --- |
| `MUSIC_LIBRARY_DIR` (+ `SMB_USERNAME` / `SMB_PASSWORD` for an `smb://` share) | Indexing your mp3 collection (`files.db`), playing its files on the phone, playlists from a folder | No library features; the catalog works |
| `YOUTUBE_API_KEY` — a YouTube Data API v3 key from the [Google Cloud console](https://console.cloud.google.com/apis/credentials) | Checking whether YouTube videos still exist when settling the phones' reports, without YouTube's bot wall | yt-dlp checks alone, which YouTube sometimes blocks for a while |
| `.secrets/client_secret.json` — a Google Cloud OAuth client of type "Desktop app", with the YouTube Data API v3 enabled; the browser login then saves `.secrets/token.json` | Creating YouTube playlists from songs and importing *private* playlists | Public and unlisted playlists still import |
| `GITHUB_DB_REPO` + `GITHUB_DB_TOKEN` — your repository (`owner/repo`) and a fine-grained token with Contents: read and write on it only | Publishing your own public copy of the catalog as a GitHub release (Settings → Server → Public database on GitHub, which explains the token) | Nothing is published. A fresh install downloads the starting catalog from `GITHUB_DB_REPO`, or from this project when it's empty |

MusicBrainz, Wikipedia, iTunes, Genius and the Spotify/YouTube pages need no keys.

## Updating

```bash
git pull
```

```bash
source venv/bin/activate
```

```bash
pip install -r requirements.txt
```

The next start backs up the databases and applies any new schema changes by itself. If you run
the sharing server, restart it so it runs the new code:

```bash
systemctl --user restart musicdatabase-share
```

## Libraries

What each library the program uses itself is for (also in the app: Help → Libraries):

- **SQLAlchemy** — The databases: music.db, tag.db, files.db and each user's user.db are SQLite files read and written through it.
- **questionary** — The menus, lists to tick and questions in the terminal.
- **rich** — The coloured tables and summaries in the terminal (song lists, import results).
- **mutagen** — Reads and writes the tags of music files (artist, title, album, cover) for imports, the music library index and fixing file names and tags.
- **Pillow** — Opens the cover pictures found in music files, to save them and tell identical ones apart.
- **musicbrainzngs** — MusicBrainz searches for a song's recordings: its album and release year.
- **wikipedia** — Wikipedia lookups for a song's album and release year.
- **requests** — Plain web requests: MusicBrainz (spelling and genres), Wikipedia, lyrics for guessing a song's language, the public catalog's GitHub release and the check for a newer yt-dlp.
- **beautifulsoup4** — Reads the web pages the lookups get back (Wikipedia, iTunes, Spotify, Google, YouTube).
- **selenium** — Drives a hidden Chrome/Chromium for the lookups that only work in a real browser (Google, Genius, iTunes, Spotify, YouTube search).
- **yt-dlp** — YouTube: searching videos for songs, whether a video still plays, importing playlists and downloading a song's audio. Its extras - deno and yt-dlp-ejs (YouTube's JavaScript checks), brotli, pycryptodomex, websockets - are what YouTube needs today.
- **google-api-python-client, google-auth, google-auth-oauthlib** — The YouTube Data API: creating YouTube playlists from songs, reading private playlists, and checking many videos at once with an API key.
- **langdetect, pycountry** — Guessing a song's language from its lyrics, and the language's name.
- **RapidFuzz** — Fuzzy comparison of names when copying songs' files from the music library.
- **universal_pathlib, smbprotocol** — Paths that may be a local folder or an smb:// network share (the music library on a NAS).
- **python-dotenv** — Reads and writes this PC's settings in .env.
- **pyperclip** — Copies a song's artist and title to the clipboard while Clean names or filling in albums and years asks about it, ready to paste into a search.
- **packaging** — Compares version numbers for the yt-dlp update offer.
- **pytest** — Runs the tests (requirements-dev.txt; the app doesn't need it).

requirements.txt also pins the packages these need themselves, so every install gets the same versions.

## Tests

The test tools are kept apart from what the app needs, once:

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest
```

About 1,200 tests, all offline (nothing reaches the network or your own databases), under a
minute. [`tests/README.md`](tests/README.md) says what each file covers.

## Contributing / working on this with an AI agent

Start at [`AGENTS.md`](AGENTS.md) — project layout, architecture boundaries, database safety
rules, and the standard commands for this repo. [`docs/index.md`](docs/index.md) is the wider
documentation entry point (architecture, data model, system registry, runbooks).

## License

GPLv3 — see [`LICENSE`](LICENSE). It uses mutagen (GPLv2 or later), so the whole program stays
GPL-compatible; the phone app ([Leina Radio](https://github.com/shian1000/LeinaPlayer)) is GPLv3 too.
