"""Menu utilities for navigating and executing menu items.

This module provides tools for creating interactive menus, file browsing,
and selecting from database objects.
"""

from typing import Callable, Dict, Optional
import questionary
import os
from upath import UPath
from utils.common.debug import slog
import json
from utils.database.database_getter import extract_db_object_info
from utils.database.datatables import Artist, Song, artist_categories, song_categories
from utils.database.database_getter import get_artists_from_db_session
from config.constants import (
    RECENT_DIRS_FILE,
    MAX_RECENT_DIRS,
    FILE_BROWSER_SELECT_OPTION,
    FILE_BROWSER_BACK_OPTION,
    DEFAULT_FILE_BROWSER_QUESTION,
    DEFAULT_MENU_PICK_QUESTION,
    DEFAULT_MENU_BACK_LABEL,
    DIALOG_NO_RESULT,
)

def load_recent_dirs() -> list[str]:
    """
    Load the list of recently used directories from file.
    
    Returns:
        list[str]: List of recent directory paths, empty if file doesn't exist
    """
    recent_file = UPath(RECENT_DIRS_FILE)
    if not recent_file.exists():
        return []
    return json.loads(recent_file.read_text())


def save_recent_dirs(new_dir: str) -> None:
    """
    Add a directory to the recent directories list and save to file.
    
    Maintains a list of the most recent directories used. If the directory
    already exists in the list, it is moved to the front.
    
    Args:
        new_dir: Directory path to add to recent list
    """
    dirs = load_recent_dirs()
    if new_dir in dirs:
        dirs.remove(new_dir)
    dirs.insert(0, new_dir)
    dirs = dirs[:MAX_RECENT_DIRS]
    recent_file = UPath(RECENT_DIRS_FILE)
    recent_file.write_text(json.dumps(dirs, indent=2))


def clear_screen() -> None:
    """Clear the terminal/console screen."""
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')



def execute_menu_item(
    prompt: str, 
    action_map: Dict[str, Callable[[], None]], 
    exit_label: str = "Back", 
    one_time: bool = False
) -> None:
    """
    Display a menu and execute the selected action.
    
    Args:
        prompt: Menu title displayed to user
        action_map: Dict mapping menu options to callable functions
        exit_label: Label for the exit option (default: "Back")
        one_time: If True, exit after first action (default: False)
    """
    clear_screen()
    choices = list(action_map.keys()) + [exit_label]
    running = True

    while running:
        choice: Optional[str] = questionary.select(prompt, choices=choices).ask()
        if choice not in choices or choice == exit_label:
            clear_screen()
            break

        action = action_map.get(choice)
        if action:
            action_name = action()
            if action_name == exit_label or one_time:
                running = False
                break


def open_file_browser_terminal(current_path: str = os.getcwd()) -> UPath:
    """
    Interactive file browser using terminal/console UI.
    
    Allows user to navigate the filesystem and select a directory using
    keyboard-based menu selection.
    
    Args:
        current_path: Starting directory path (defaults to current working directory)
    
    Returns:
        UPath: The selected directory path
    """
    if not os.path.isdir(current_path):
        current_path = os.getcwd()

    current_path = os.path.abspath(current_path)
    
    subdirs = sorted([
        d for d in os.listdir(current_path) 
        if os.path.isdir(os.path.join(current_path, d)) and not d.startswith(".")
    ])
    
    options = [FILE_BROWSER_SELECT_OPTION, FILE_BROWSER_BACK_OPTION] + subdirs
    title = f"{DEFAULT_FILE_BROWSER_QUESTION}\nCurrent Path: {current_path}"

    option = questionary.select(title, options).ask()
    
    if option == FILE_BROWSER_SELECT_OPTION:
        return UPath(current_path)
    
    elif option == FILE_BROWSER_BACK_OPTION:
        parent_dir = os.path.dirname(current_path)
        return UPath(open_file_browser_terminal(parent_dir))
    
    else:
        new_path = os.path.join(current_path, option)
        return UPath(open_file_browser_terminal(new_path))



def open_file_browser_window() -> Optional[str]:
    """
    Interactive file browser using GUI dialog.
    
    Uses tkinter to display a file selection dialog. Returns the selected
    file path or None if an error occurs or user cancels.
    
    Returns:
        Optional[str]: Path to selected file, or None if error/cancelled
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        slog(f"File browser import error: {e}")
        return None

    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        path = filedialog.askopenfilename()
        root.destroy()
        
        if path:
            slog(f"File browser selected: {path}")
        
        return path or None

    except Exception as e:
        slog(f"File browser error: {e}")
        return None


def pick_from_db_objects(
    entries_objects: list,
    question: str = DEFAULT_MENU_PICK_QUESTION,
    additional_info: Optional[list] = None,
    style = None,
    back_label: str = DEFAULT_MENU_BACK_LABEL
) -> Optional[object]:
    """
    Display interactive menu to select a database object.
    
    Presents a questionary menu allowing the user to select from a list of
    database objects (Songs or Artists). Displays relevant object properties
    and allows returning without selection.
    
    Args:
        entries_objects: List of Song or Artist objects to choose from
        question: Menu prompt text to display
        additional_info: Optional list of additional info to append to each item
        style: questionary style configuration
        back_label: Label for the back/cancel option
    
    Returns:
        Optional: Selected database object, or None if user selects back
    """
    if isinstance(entries_objects[0], Artist):
        entries_names = extract_db_object_info(entries_objects, f"{artist_categories[0]}")
    else:
        entries_names = extract_db_object_info(
            entries_objects,
            f"{song_categories[1]}, {song_categories[0]}, {song_categories[2]}"
        )
        entries_names = [(f"{song[0]} - {song[1]} ({song[2]})",) for song in entries_names]

    if additional_info:
        entries_names = [
            f"{name[0]} ({', '.join(info)})"
            for name, info in zip(entries_names, additional_info)
        ]
    else:
        entries_names = [item for t in entries_names for item in t]

    slog(entries_names)

    choices = [
        questionary.Choice(title=name, value=i)
        for i, name in enumerate(entries_names)
    ]
    choices.append(questionary.Choice(title=back_label, value=DIALOG_NO_RESULT))
    
    sel_obj_index = questionary.select(question, choices=choices, style=style).ask()
    slog(sel_obj_index)

    if sel_obj_index == DIALOG_NO_RESULT:
        return None
    
    return entries_objects[sel_obj_index]


def get_list_of_properties_from_db_object(db_object) -> Optional[list]:
    """
    Extract properties from a database object as a list.
    
    Converts a Song or Artist object into a list of its properties
    in a standard order.
    
    Args:
        db_object: Song or Artist database object
    
    Returns:
        Optional[list]: List of properties, or None if invalid object type
    """
    properties_list = []
    if isinstance(db_object, Artist):
        properties_list.append(db_object.name)
        properties_list.append(db_object.origin)
    elif isinstance(db_object, Song):
        properties_list.append(db_object.title)
        properties_list.append(db_object.artist.name)
        properties_list.append(db_object.album)
        properties_list.append(db_object.year)
        properties_list.append(db_object.language)
        properties_list.append(db_object.artist.origin)
        properties_list.append("")
        properties_list.append("")
    else:
        slog("Error: Invalid database object type")
        return None
    
    return properties_list


def ask_for_entires_list(
    mode: str = "Song",
    question: str = "What query do you wish to search for: ",
    allow_no_results: bool = False,
    allow_one_result: bool = True
) -> Optional[list]:
    """
    Prompt user for input and search for database objects.
    
    Asks the user to enter a search query and searches for matching
    database objects (currently Artists only).
    
    Args:
        mode: Object type to search for (default: "Song")
        question: Prompt text to display to user
        allow_no_results: If False, abort if no results found
        allow_one_result: If False, abort if only one result found
    
    Returns:
        Optional[list]: List of matching objects, or None if search aborted
    
    Raises:
        NotImplementedError: If mode is "Song" (not yet supported)
    """
    query = input(question)
    
    if mode == "Artist":
        artists_objects = get_artists_from_db_session(
            artist_categories[0],
            query,
            aggresive_search=True
        )
    else:
        slog("Search for songs is not yet implemented")
        return None

    if not allow_no_results and not artists_objects:
        slog("No such artist found. Aborting")
        return None
    
    if not allow_one_result and len(artists_objects) == 1:
        slog("Only one artist found. Aborting")
        return None
    
    return artists_objects
