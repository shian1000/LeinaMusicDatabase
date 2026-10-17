"""Main menu -> Users: creating users, logging into a user's private
database, managing the users themselves (utils/database/users.py), and the
owner's view of moods and permissions (moods.py next to this file)."""

from pathlib import Path

import questionary
from utils.ui.prompts import ask_yes_no

from config.constants import FILE_PAIR_VIDEO_CHOICES_MAX, USER_PLAYLIST_SOURCE_FOLDER
from settings import settings
from utils.common.debug import slog
from utils.database.database_sessions import open_and_set_global_database_sessions
from utils.database.song_references import apply_song_id_changes
from utils.database.user_playlists import delete_playlist, list_playlists, playlist_songs, save_playlist
from utils.database.users import (
    InvalidUserError,
    UsersUnavailableError,
    change_password,
    create_user,
    delete_user,
    list_usernames,
    normalize_username,
    read_account,
    rename_user,
    users_available,
    verify_user,
)
from utils.database.datatables import Song
from menu.main_menu.users.moods import moods_menu, permissions_menu
from utils.database.song_artists import song_artist_label
from utils.discoveries.folder_playlist import add_song_for_file, assign_song, build_folder_playlist, song_for_file_plan
from utils.discoveries.folder_youtube_playlist import (
    link_track,
    pair_automatically,
    rank_entries_for,
    search_entries,
    set_tracks,
    youtube_entries,
    youtube_only_track,
)
from utils.discoveries.import_engine import LocalSongMatcher
from utils.discoveries.file_matching_settings import (
    load_auto_accept_artist_similarity,
    load_best_guess_min_score,
    load_closest_auto_accept_score,
)
from utils.discoveries.import_engine import find_songs_by_title
from utils.discoveries.mp3_utils import rename_to_song, song_file_name, write_song_tags
from utils.ui.song_colors import DETAIL_STYLE, as_ansi, colored_song, song_tokens
from utils.ui.display_utils import display_song_links, display_songs
from utils.ui.menu_utils import clear_screen, execute_menu_item, open_file_browser_terminal, search_and_pick_db_object
from utils.youtube.import_from_playlist import extract_playlist_id, get_playlist_items

RED = "\033[91m"
RESET = "\033[0m"


def _error(message) -> None:
    print(f"{RED}{message}{RESET}")


def _users_or_explain() -> list[str] | None:
    if not users_available():
        _error(f"The users folder {settings.users_dir} is on a drive that isn't mounted "
               "- plug in the external drive first.")
        return None
    names = list_usernames()
    if not names:
        print('No users yet - create one with "Create a new user".')
        return None
    return names


def _pick_user(question: str) -> str | None:
    names = _users_or_explain()
    if not names:
        return None
    return questionary.select(question, choices=names).ask()


def _ask_new_password(question: str = "New password:") -> str | None:
    password = questionary.password(question).ask()
    if password is None:
        return None
    if questionary.password("Repeat the password:").ask() != password:
        _error("The passwords don't match.")
        return None
    return password


# --------------------
# Enter user database
# --------------------

# Per-file choices for a file with no song in the database
_UNMATCHED_BY_TITLE = "Search by title (any artist)"
_UNMATCHED_SEARCH = "Search the database"
_UNMATCHED_ADD = "Add it to the database as a new song"
_UNMATCHED_KEEP = "Keep it in the playlist without a song"
_UNMATCHED_LEAVE_OUT = "Leave it out of the playlist"
_UNMATCHED_STOP = "Stop (don't save the playlist)"
_CONFIRM_SONG = "Yes, this is the song"
_SEARCH_INSTEAD = "No - search the database by hand"
_SIMILAR_OTHER = "No - show the other options"
_NONE_OF_THESE = "None of these - show the other options"
_BACK_TO_OPTIONS = "Back to the options for this file"


def _video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _default_title(track) -> str:
    return track.title or (track.file.stem if track.file else "")


def _song_tokens(song, score: float | None = None) -> list:
    """The song's coloured label as questionary tokens (song_colors): artist,
    title, album and year, and how well the file's tags fit it ("(87%)")."""
    details = ", ".join(str(v) for v in (song.album, song.year) if v)
    tokens = song_tokens(song_artist_label(song), song.title, details)
    if score is not None:
        tokens += [("", "  "), (DETAIL_STYLE, f"({score:.0%})")]
    return tokens


def _song_choice_label(song) -> str:
    """The coloured label for print()."""
    return as_ansi(_song_tokens(song))


def _scored_tokens(track, song) -> list:
    return _song_tokens(song, track.scores.get(song.id))


def _song_choices(songs, track=None) -> list:
    return [questionary.Choice(title=_scored_tokens(track, s) if track else _song_tokens(s), value=s) for s in songs]


def _pick_by_title(music_session, track):
    """Songs of any artist with this title: the user picks one, then has the
    final say - confirm it, or search the database by hand instead."""
    query = questionary.text("Title to look for:", default=_default_title(track)).ask()
    if not query:
        return None
    songs = find_songs_by_title(music_session, query)
    if not songs:
        print("No song with that title in the database.")
        if ask_yes_no("Search the database by hand instead?"):
            return _pick_by_search()
        return None

    choices = _song_choices(songs)
    picked = questionary.select("Which song is this file?",
                                choices=choices + [_SEARCH_INSTEAD, _BACK_TO_OPTIONS]).ask()
    if picked is None or picked == _BACK_TO_OPTIONS:
        return None
    if picked == _SEARCH_INSTEAD:
        return _pick_by_search()

    print(f"  File:  {track.file.name}" if track.file else f"  Video: {_video_url(track.youtube_video_id)}")
    print(f"  Song:  {_song_choice_label(picked)}")
    verdict = questionary.select("Is this the right song?",
                                 choices=[_CONFIRM_SONG, _SEARCH_INSTEAD, _BACK_TO_OPTIONS]).ask()
    if verdict == _CONFIRM_SONG:
        return picked
    if verdict == _SEARCH_INSTEAD:
        return _pick_by_search()
    return None


def _pick_by_search():
    song = search_and_pick_db_object("Song", pick_question="Which song is this file?")
    if song is None:
        print("Nothing picked.")
        return None
    print(f"  Song:  {_song_choice_label(song)}")
    if not ask_yes_no("Use this song?"):
        return None
    return song


def _review_unmatched_files(music_session, folder: Path, playlist) -> None:
    """One file at a time: its name, folder and what was read from it, then
    the options for it: a song picked or added for it goes into its track, or
    it stays in the playlist without a song, or is left out of it. A file
    still in `playlist.unmatched` afterwards (Stop) means don't save."""
    unmatched = playlist.unmatched
    total = len(unmatched)
    previous = None
    for i, track in enumerate(list(unmatched), 1):
        # One file per screen; the decision about the one before, which the
        # clearing would wipe, goes on top
        clear_screen()
        print(f"Files without a song in the database ({total} of {len(playlist.tracks)})")
        if previous is not None:
            print(f"  Previous: {_track_source(previous)}  ->  {_review_outcome(previous, playlist)}")
        previous = track
        if track.file is None:
            print(f"\n[{i}/{total}] (no file) {_video_url(track.youtube_video_id)}")
        else:
            print(f"\n[{i}/{total}] {track.file.name}")
            print(f"  Folder: {track.file.parent.relative_to(folder) if track.file.parent != folder else '.'}")
        print(f"  Read as: {colored_song(track.artist, track.title)}")
        if track.similar_song is not None:
            print(f"  Similar song in the database: {as_ansi(_scored_tokens(track, track.similar_song))}")
            verdict = questionary.select("Is this the right song?", choices=[_CONFIRM_SONG, _SIMILAR_OTHER]).ask()
            if verdict is None:
                return
            if verdict == _CONFIRM_SONG:
                _pick_for_track(track, track.similar_song, unmatched)
                continue
        if track.title_candidates:
            print("  Songs with this title in the database:")
            choices = _song_choices(track.title_candidates, track)
            picked = questionary.select("Which song is this file?", choices=choices + [_NONE_OF_THESE]).ask()
            if picked is None:
                return
            if picked != _NONE_OF_THESE:
                _pick_for_track(track, picked, unmatched)
                continue
        if track.best_guesses:
            # Only the closest by score - often none of them, so that's the default
            print("  Closest songs in the database:")
            choices = _song_choices([s for s, _ in track.best_guesses], track)
            picked = questionary.select("Is one of these the song?", choices=choices + [_NONE_OF_THESE],
                                        default=_NONE_OF_THESE).ask()
            if picked is None:
                return
            if picked != _NONE_OF_THESE:
                _pick_for_track(track, picked, unmatched)
                continue
        while True:
            choice = questionary.select(
                "What should happen with this file?",
                choices=[_UNMATCHED_BY_TITLE, _UNMATCHED_SEARCH, _UNMATCHED_ADD, _UNMATCHED_KEEP,
                         _UNMATCHED_LEAVE_OUT, _UNMATCHED_STOP],
            ).ask()
            if choice is None or choice == _UNMATCHED_STOP:
                return
            if choice == _UNMATCHED_KEEP:
                unmatched.remove(track)
                print("  -> kept without a song")
                break
            if choice == _UNMATCHED_LEAVE_OUT:
                unmatched.remove(track)
                playlist.tracks.remove(track)
                print("  -> left out of the playlist" + (" (the file itself stays)" if track.file else ""))
                break
            if choice == _UNMATCHED_ADD:
                song = _add_song_for_file(music_session, track)
            elif choice == _UNMATCHED_BY_TITLE:
                song = _pick_by_title(music_session, track)
            else:
                song = _pick_by_search()
            if song is not None:
                _pick_for_track(track, song, unmatched)
                break


def _add_song_for_file(music_session, track):
    """Ask for the new song's artist and title (the tags' as defaults), show
    whether the artist / song is already there, and add it once confirmed."""
    artist_name = (questionary.text("Artist:", default=track.artist or "").ask() or "").strip()
    title = (questionary.text("Title:", default=_default_title(track)).ask() or "").strip()
    if not artist_name or not title:
        print("An artist and a title are both needed - nothing added.")
        return None
    artist, existing = song_for_file_plan(music_session, artist_name, title)
    if existing is not None:
        print(f"  Already in the database: {_song_choice_label(existing)}")
        if ask_yes_no("Use this song?"):
            return existing
    print(f"  Artist: {artist.name} (already in the database)" if artist else f"  Artist: {artist_name} (new)")
    print(f"  Title:  {title}")
    if not ask_yes_no("Add this song to the database?" if existing is None else "Add it as a new song instead?"):
        return None
    song = add_song_for_file(music_session, artist.name if artist else artist_name, title,
                             even_if_similar=existing is not None)
    print(f"Added song '{song.title}' with id {song.id}.")
    return song


def _track_source(track) -> str:
    return track.file.name if track.file else _video_url(track.youtube_video_id)


def _review_outcome(track, playlist) -> str:
    """What became of a reviewed file, for the line on top of the next one."""
    if any(t is track for t in playlist.unmatched):
        return "not decided"
    if not any(t is track for t in playlist.tracks):
        return "left out of the playlist"
    if track.song_id is None:
        return "kept without a song"
    return colored_song(track.artist, track.title)


def _pick_for_track(track, song, unmatched: list) -> None:
    assign_song(track, song)
    track.picked = True
    unmatched.remove(track)
    print(f"  -> {_song_choice_label(song)}")


def _offer_file_fix(music_session, tracks: list) -> None:
    """After a playlist is saved: the files whose song the user confirmed or
    picked had tags the database didn't recognise - offer to rename them to
    "Artist - Title.mp3" and copy the database's data into their tags."""
    if not tracks:
        return
    print(f"\n{len(tracks)} file(s) whose tags don't match their song in the database:")
    songs = {id(t): music_session.get(Song, t.song_id) for t in tracks}
    for track in tracks:
        print(f"  {track.file.name}  ->  {song_file_name(songs[id(track)], track.file.suffix)}")
    if not ask_yes_no("Fix the names and tags of these mp3 files?"):
        return
    failed = []
    for track in tracks:
        song = songs[id(track)]
        try:
            write_song_tags(track.file, song)
            track.file = rename_to_song(track.file, song)
        except Exception as exc:
            failed.append((track.file, exc))
    print(f"Fixed {len(tracks) - len(failed)} file(s).")
    for file, exc in failed:
        _error(f"  Couldn't fix {file}: {exc}")


def _offer_tag_update(music_session, tracks: list) -> None:
    """After a playlist is saved: copy the database's title/artist/album/year
    into each file's tags, if the user wants."""
    if not tracks:
        return
    if not ask_yes_no(f"Update the mp3 tags of these {len(tracks)} file(s) with the data from the database?"):
        return
    changed, failed = 0, []
    for track in tracks:
        song = music_session.get(Song, track.song_id)
        try:
            changed += write_song_tags(track.file, song)
        except Exception as exc:
            failed.append((track.file, exc))
    print(f"Tags updated in {changed} file(s); {len(tracks) - changed - len(failed)} already matched.")
    for file, exc in failed:
        _error(f"  Couldn't write {file}: {exc}")


def _create_playlist_from_folder(username: str) -> None:
    folder = open_file_browser_terminal(settings.export_dir)
    if not folder:
        return
    folder = Path(str(folder))
    name = questionary.text("Playlist name:", default=folder.name).ask()
    if not name:
        print("Cancelled.")
        return

    music_session, _ = open_and_set_global_database_sessions()
    print(f"Matching the mp3 files in {folder} with the database...")
    playlist = build_folder_playlist(music_session, folder, load_auto_accept_artist_similarity(),
                                     load_best_guess_min_score(), load_closest_auto_accept_score())
    if not playlist.tracks:
        print("No mp3 files in that folder.")
        return
    _finish_folder_playlist(music_session, username, name, folder, playlist)


def _finish_folder_playlist(music_session, username: str, name: str, folder: Path, playlist) -> None:
    """The files (and videos) without a song in the database, then saving
    the playlist and offering to fix the files' names and tags."""
    if playlist.auto_accepted:
        print(f"\n{len(playlist.auto_accepted)} file(s) matched automatically though the artist is spelt differently:")
        for track, tag_artist in playlist.auto_accepted:
            print(f"  {_track_source(track)}  ({tag_artist})  ->  {colored_song(track.artist, track.title)}")
    if playlist.auto_accepted_scored:
        print(f"\n{len(playlist.auto_accepted_scored)} file(s) auto-accepted, their closest song scoring "
              f"at least {load_closest_auto_accept_score():.0%}:")
        for track, (tag_artist, tag_title), score in playlist.auto_accepted_scored:
            print(f"  {_track_source(track)}")
            print(f"    {colored_song(tag_artist, tag_title)}  ->  "
                  f"{as_ansi(_song_tokens(music_session.get(Song, track.song_id), score))}")

    if playlist.unmatched:
        if playlist.auto_accepted or playlist.auto_accepted_scored:
            # The review clears the screen for every file: read these first
            questionary.press_any_key_to_continue(
                f"Press any key to go through the {len(playlist.unmatched)} file(s) without a song...").ask()
        _review_unmatched_files(music_session, folder, playlist)
        if playlist.unmatched:
            # Stopped before every file was decided on
            print(f"\n{len(playlist.unmatched)} file(s) not decided on - playlist not saved.")
            return
    if not playlist.tracks:
        print("Every file was left out - playlist not saved.")
        return

    playlist_id = save_playlist(username, name, playlist.tracks,
                                source=USER_PLAYLIST_SOURCE_FOLDER, source_id=str(folder))
    without_song = sum(1 for t in playlist.tracks if t.song_id is None)
    print(f"Saved playlist '{name}' (#{playlist_id}) with {len(playlist.tracks)} track(s)"
          + (f", {without_song} of them without a song in the database." if without_song else "."))
    files = [t for t in playlist.tracks if t.file is not None]
    _offer_file_fix(music_session, [t for t in files if t.picked])
    _offer_tag_update(music_session, [t for t in files if t.song_id is not None and not t.picked])


# Choices when pairing the folder with a YouTube playlist
_PAIR_NO_LINK = "Keep it without a YouTube link"
_PAIR_SEARCH = "Search the videos by title..."
_PAIR_LEAVE_OUT = "Leave it out of the playlist"
_PAIR_STOP = "Stop (don't save the playlist)"
_VIDEO_ADD = "Add it to the playlist without a file"
_VIDEO_LEAVE_OUT = "Leave it out of the playlist"


def _entry_label(entry) -> str:
    channel = f"  [{entry.channel}]" if entry.channel and entry.channel.casefold() not in entry.label.casefold() else ""
    return f"{entry.label}{channel}"


def _pick_video(unpaired: list):
    """The video for a file, or one of the _PAIR_* choices: the most alike
    unpaired videos (FILE_PAIR_VIDEO_CHOICES_MAX, so the list fits the
    terminal), and when there are more, a search through all of them."""
    limit = FILE_PAIR_VIDEO_CHOICES_MAX
    shown, search = unpaired[:limit], [_PAIR_SEARCH] if len(unpaired) > limit else []
    while True:
        if search:
            print(f"  {len(shown)} of the {len(unpaired)} unpaired videos shown - search for the others.")
        picked = questionary.select(
            "Which video of the YouTube playlist is this file?" if unpaired
            else "No unpaired video left in the YouTube playlist - what should happen with this file?",
            choices=[questionary.Choice(title=_entry_label(e), value=e) for e in shown]
            + search + [_PAIR_NO_LINK, _PAIR_LEAVE_OUT, _PAIR_STOP],
        ).ask()
        if picked != _PAIR_SEARCH:
            return picked
        query = questionary.text("Part of the video's title or channel:").ask() or ""
        found = search_entries(unpaired, query)
        if not found:
            print(f"  No unpaired video with \"{query}\".")
        shown = found[:limit] or unpaired[:limit]


def _pair_files_by_hand(folder: Path, files: list, entries: list) -> tuple | None:
    """Each file no video was paired with: the user picks one of the videos
    still unpaired, keeps it without a link, or leaves it out. Returns the
    (pairs, files left out), None on Stop."""
    pairs, left_out = [], []
    for i, track in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {track.file.name}")
        print(f"  Folder: {track.file.parent.relative_to(folder) if track.file.parent != folder else '.'}")
        print(f"  Read as: {colored_song(track.artist, track.title)}")
        unpaired = rank_entries_for(track, [e for e in entries if all(e is not p for _, p in pairs)])
        picked = _pick_video(unpaired)
        if picked is None or picked == _PAIR_STOP:
            return None
        if picked == _PAIR_LEAVE_OUT:
            left_out.append(track)
            print("  -> left out of the playlist (the file itself stays)")
        elif picked == _PAIR_NO_LINK:
            print("  -> kept without a YouTube link")
        else:
            pairs.append((track, picked))
            print(f"  -> {_video_url(picked.video_id)}")
    return pairs, left_out


def _review_unpaired_videos(entries: list) -> list | None:
    """Each video no file was paired with: add it as a track of its own or
    leave it out. Returns the videos to add, None on Stop."""
    added = []
    for i, entry in enumerate(entries, 1):
        print(f"\n[{i}/{len(entries)}] {_entry_label(entry)}")
        print(f"  {_video_url(entry.video_id)}")
        print(f"  Read as: {colored_song(entry.metadata.get('artist_name'), entry.metadata.get('title'))}")
        choice = questionary.select("No file is this video - what should happen with it?",
                                    choices=[_VIDEO_ADD, _VIDEO_LEAVE_OUT, _PAIR_STOP]).ask()
        if choice is None or choice == _PAIR_STOP:
            return None
        if choice == _VIDEO_ADD:
            added.append(entry)
            print("  -> added without a file")
        else:
            print("  -> left out of the playlist")
    return added


def _create_playlist_from_folder_and_youtube(username: str) -> None:
    """Like "Create playlist from folder", with every file paired with a
    video of a YouTube playlist holding the same songs, whose link the track
    takes. The playlist follows the YouTube playlist's order; files without
    a video come after it, in folder order."""
    folder = open_file_browser_terminal(settings.export_dir)
    if not folder:
        return
    folder = Path(str(folder))
    playlist_input = questionary.text("YouTube playlist (link or id):").ask()
    if not playlist_input:
        print("Cancelled.")
        return
    if not extract_playlist_id(playlist_input):
        _error(f"Couldn't find a playlist id in '{playlist_input}'.")
        return
    name = questionary.text("Playlist name:", default=folder.name).ask()
    if not name:
        print("Cancelled.")
        return

    music_session, _ = open_and_set_global_database_sessions()
    min_similarity, min_score = load_auto_accept_artist_similarity(), load_best_guess_min_score()
    sure_score = load_closest_auto_accept_score()
    print(f"Matching the mp3 files in {folder} with the database...")
    playlist = build_folder_playlist(music_session, folder, min_similarity, min_score, sure_score)
    if not playlist.tracks:
        print("No mp3 files in that folder.")
        return
    items = get_playlist_items(playlist_input)
    if not items:
        _error("No videos found in that YouTube playlist.")
        return
    matcher = LocalSongMatcher(music_session)
    print("Reading the videos' artists and titles...")
    entries = youtube_entries(matcher, items)
    if len(entries) < len(items):
        print(f"{len(items) - len(entries)} video(s) of the YouTube playlist skipped: deleted, private, "
              "or with neither an artist nor a title to read.")

    pairs = pair_automatically(playlist.tracks, entries)
    paired_files = {id(t) for t, _ in pairs}
    unpaired_files = [t for t in playlist.tracks if id(t) not in paired_files]
    print(f"\nPaired {len(pairs)} of {len(playlist.tracks)} file(s) with the {len(entries)} video(s) automatically.")
    left_out = []
    if unpaired_files:
        print(f"{len(unpaired_files)} file(s) without a video:")
        result = _pair_files_by_hand(folder, unpaired_files, [e for e in entries if all(e is not p for _, p in pairs)])
        if result is None:
            print("Playlist not saved.")
            return
        pairs += result[0]
        left_out = result[1]
    paired_entries = {id(e) for _, e in pairs}
    unpaired_entries = [e for e in entries if id(e) not in paired_entries]
    added = []
    if unpaired_entries:
        print(f"\n{len(unpaired_entries)} video(s) of the YouTube playlist without a file:")
        added = _review_unpaired_videos(unpaired_entries)
        if added is None:
            print("Playlist not saved.")
            return

    for track, entry in pairs:
        link_track(track, entry, matcher)
    by_entry = {id(e): t for t, e in pairs}
    by_entry.update({id(e): youtube_only_track(playlist, matcher, e, min_similarity, min_score, sure_score) for e in added})
    linked = {id(t) for t in by_entry.values()}
    gone = {id(t) for t in left_out}
    set_tracks(playlist, [by_entry[id(e)] for e in entries if id(e) in by_entry]
               + [t for t in playlist.tracks if id(t) not in linked and id(t) not in gone])
    if not playlist.tracks:
        print("Everything was left out - playlist not saved.")
        return
    without_link = sum(1 for t in playlist.tracks if t.youtube_link is None)
    print(f"\n{len(playlist.tracks)} track(s), {len(playlist.tracks) - without_link} with a link from the YouTube playlist."
          + (f" {len(added)} of them without a file." if added else ""))
    _finish_folder_playlist(music_session, username, name, folder, playlist)


_BACK = "Back"


def _show_playlist(username: str, playlist_id: int, show) -> None:
    """One playlist through a song table (display_songs / display_song_links);
    tracks with no song in the database can't be in it, so they're listed
    underneath with the title/artist the playlist kept."""
    music_session, _ = open_and_set_global_database_sessions()
    songs, missing = playlist_songs(music_session, username, playlist_id)
    show(songs)
    if missing:
        print(f"\n{len(missing)} track(s) not in the database:")
        for track in missing:
            video = f"  https://www.youtube.com/watch?v={track.youtube_video_id}" if track.youtube_video_id else ""
            print(f"  {track.position + 1}. {track.artist or '?'} - {track.title}{video}")


def _delete_playlist(username: str, playlist_id: int, name: str) -> str | None:
    """Returns _BACK once deleted, which closes the playlist's menu."""
    print(f"This deletes '{name}' here and in {username}'s Leina Radio (at its next sync).")
    if not ask_yes_no("Delete it?"):
        return None
    archived = delete_playlist(username, playlist_id)
    print(f"Deleted '{name}'." + (f" A copy is kept in {archived}" if archived else ""))
    return _BACK


def _playlist_menu(username: str, playlist_id: int, name: str) -> None:
    action_map = {
        "Show playlist contents": lambda: _show_playlist(username, playlist_id, display_songs),
        "Show playlist links": lambda: _show_playlist(username, playlist_id, display_song_links),
        "Delete playlist": lambda: _delete_playlist(username, playlist_id, name),
    }
    execute_menu_item(f"Playlist: {name}", action_map, exit_label=_BACK)


def _show_playlists(username: str) -> None:
    """Pick a playlist, then what to do with it; Back returns to the list."""
    while True:
        playlists = list_playlists(username)
        if not playlists:
            print("No playlists yet.")
            return
        choices = [
            questionary.Choice(
                title=f"{name} - {tracks} track(s)" + (f", {missing} not in the database" if missing else "")
                + (", cover" if cover else ""),
                value=(playlist_id, name),
            )
            for playlist_id, name, tracks, missing, cover in playlists
        ]
        picked = questionary.select("Playlists:", choices=choices + [_BACK]).ask()
        if picked is None or picked == _BACK:
            return
        _playlist_menu(username, *picked)


def enter_user_database() -> None:
    if not _users_or_explain():
        return
    username = questionary.text("Username:").ask()
    if not username:
        return
    password = questionary.password("Password:").ask()
    if password is None or not verify_user(username, password):
        _error("Wrong username or password.")
        return
    username = username.strip().lower()

    # Catch up on songs merged or deleted while this user's DB was out of reach
    try:
        apply_song_id_changes(force=True)
    except Exception as exc:
        slog(f"Updating song references failed: {exc}", priority=3)

    action_map = {
        "Create playlist from folder": lambda: _create_playlist_from_folder(username),
        "Create playlist from folder and compare with YT playlist":
            lambda: _create_playlist_from_folder_and_youtube(username),
        "Show playlists": lambda: _show_playlists(username),
    }
    execute_menu_item(f"User database: {username}", action_map, exit_label="Log out")


# --------------------
# Managing users
# --------------------

def create_new_user() -> None:
    """Make a user folder with an empty private user.db (playlists, private
    tags) and a password."""
    if not users_available():
        _error(f"The users folder {settings.users_dir} is on a drive that isn't mounted "
               "- plug in the external drive first.")
        return
    existing = list_usernames()
    print(f"Users are kept in {settings.users_dir}")
    print(f"Existing users: {', '.join(existing) if existing else 'none yet'}\n")

    username = questionary.text("Username (lowercase letters, digits, '-' or '_'):").ask()
    if not username:
        print("Cancelled.")
        return
    try:
        username = normalize_username(username)
    except InvalidUserError as exc:
        _error(exc)
        return
    if username in existing:
        _error(f"User '{username}' already exists.")
        return

    password = _ask_new_password("Password:")
    if password is None:
        print("Nothing created.")
        return
    try:
        folder = create_user(username, password)
    except (InvalidUserError, UsersUnavailableError) as exc:
        _error(exc)
        return
    print(f"Created user '{username}': {folder}")


def list_all_users() -> None:
    names = _users_or_explain()
    if not names:
        return
    print(f"Users in {settings.users_dir}:")
    for name in names:
        created = read_account(name).get("created_at", "?")
        print(f"  {name}  (created {created})")


def rename_user_menu() -> None:
    old = _pick_user("Which user should be renamed?")
    if not old:
        return
    new = questionary.text("New username (lowercase letters, digits, '-' or '_'):").ask()
    if not new:
        print("Cancelled.")
        return
    try:
        folder = rename_user(old, new)
    except (InvalidUserError, UsersUnavailableError, OSError) as exc:
        _error(exc)
        return
    print(f"Renamed '{old}' to '{folder.name}'.")


def change_password_menu() -> None:
    username = _pick_user("Whose password should be changed?")
    if not username:
        return
    password = _ask_new_password()
    if password is None:
        print("Password not changed.")
        return
    try:
        change_password(username, password)
    except (InvalidUserError, UsersUnavailableError) as exc:
        _error(exc)
        return
    print(f"Password of '{username}' changed.")


def delete_user_menu() -> None:
    username = _pick_user("Which user should be deleted?")
    if not username:
        return
    print(f"This removes '{username}' with all their playlists and private tags.")
    print("Their folder is moved to the archive, not erased, so it can be put back by hand.")
    typed = questionary.text(f"Type '{username}' to confirm:").ask()
    if typed != username:
        print("Cancelled.")
        return
    try:
        archived = delete_user(username)
    except (InvalidUserError, UsersUnavailableError, OSError) as exc:
        _error(exc)
        return
    print(f"Deleted '{username}'. Their data is now in {archived}")


def users_menu() -> None:
    action_map = {
        "Create a new user": create_new_user,
        "Enter user database": enter_user_database,
        "List all users": list_all_users,
        "Rename user": rename_user_menu,
        "Change password": change_password_menu,
        "Delete user": delete_user_menu,
        "Moods": moods_menu,
        "Permissions": permissions_menu,
    }
    execute_menu_item("Users", action_map, exit_label="Back")
