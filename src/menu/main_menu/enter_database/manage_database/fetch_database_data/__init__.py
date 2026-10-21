from utils.ui.menu_utils import execute_menu_item
from utils.common.debug import slog
from menu.main_menu.enter_database.manage_database.fetch_database_data.fill_missing_data import fill_missing_data
from utils.discoveries.import_data_from_mp3_tags import import_data_from_mp3_tags
from utils.youtube.import_from_playlist import import_data_from_youtube_playlist
from utils.ui.menu_utils import open_file_browser_terminal
from menu.song_actions.edit_songs import edit_songs_menu
from settings import settings
from utils.database.database_getter import get_songs_from_db_session
from utils.ui.display_utils import display_songs
import questionary
from utils.ui.prompts import ask_yes_no
from utils.database.database_sessions import submit_global_database_session, open_and_set_global_database_sessions
from utils.discoveries.mp3_covers import import_covers_from_mp3
from utils.database.albums import covers_dir

def _review_imported_songs(imported_songs):
    if imported_songs:
        print("displaying")
        display_songs(imported_songs)
        edit_songs_menu(imported_songs)
        decision = ask_yes_no("Do you want to do something with these songs?")
        if decision:
            from menu.song_actions import song_actions
            song_actions(imported_songs)
            submit_global_database_session()

def import_data():
    import_folder = settings.export_dir
    songs_path = open_file_browser_terminal(import_folder)
    if not songs_path:
        return
    imported_songs = import_data_from_mp3_tags(songs_path)
    _review_imported_songs(imported_songs)

def import_youtube_playlist():
    playlist_input = questionary.text("Paste the YouTube playlist URL (or just its ID):").ask()
    if not playlist_input:
        return
    imported_songs = import_data_from_youtube_playlist(playlist_input)
    _review_imported_songs(imported_songs)


def import_covers():
    default = settings.local_library_dir_str
    if default:
        choice = questionary.select(
            "Which folder's mp3 files should the covers come from?",
            choices=[default, "Pick another folder"],
        ).ask()
        if choice is None:
            return
        folder = default if choice == default else open_file_browser_terminal(default)
    else:
        folder = open_file_browser_terminal(settings.export_dir)
    if not folder:
        return

    music_session, _ = open_and_set_global_database_sessions()
    print(f"Reading embedded covers from {folder} ...")
    report = import_covers_from_mp3(
        music_session, folder, progress=lambda done, total: print(f"  {done}/{total} files"))

    print(f"\n{report.files} mp3 file(s): {report.no_image} without an image, "
          f"{report.not_square} with a non-square one (video frames, skipped), "
          f"{report.unreadable} unreadable.")
    print(f"{len(report.unmatched)} file(s) matched no song in the database; "
          f"{report.no_album} matched a song with no album yet.")
    for name in report.unmatched[:10]:
        print(f"    {name}")
    if len(report.unmatched) > 10:
        print(f"    ... and {len(report.unmatched) - 10} more (all in debug.log)")
    if report.no_album:
        print('  (Songs on album titles several artists share get covers once '
              '"Resolve albums with several artists" has settled them.)')
    print(f"Covers written for {report.albums_written} album(s), into {covers_dir()}")
    if report.disagreements:
        print(f"\n{len(report.disagreements)} album(s) have different pictures on different tracks - "
              "the one most tracks carry was used:")
        for album, choice in report.disagreements:
            pictures = ", ".join(
                f"{count}x {cover.width}x{cover.height} ({cover.file.name})" for count, cover in choice.pictures)
            print(f"  #{album.id} {album.artist.name if album.artist else 'Various artists'} - "
                  f"{album.title}: {pictures}")


def fetch_database_data():
    action_map = {
        "Fill missing data": fill_missing_data,
        "Import data from mp3 tags": import_data,
        "Import covers from mp3 tags": import_covers,
        "Import data from YouTube playlist": import_youtube_playlist,
    }

    slog(action_map)

    execute_menu_item("Manage database", action_map, exit_label="Back")