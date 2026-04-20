from typing import Callable, Dict, Optional
import questionary
import os
from upath import UPath
from utils.debug import slog
import json
from utils.database.database_getter import extract_db_object_info
import time
from utils.database.datatables import Artist, Song, artist_categories, song_categories
from utils.database.database_sessions import get_global_database_sessions

"""Here should fall the tools used to navigate menus - executing items, file browsing etc."""

RECENT_DIRS_FILE = UPath("recent_dirs.json")
MAX_RECENT_DIRS = 5

def load_recent_dirs() -> list[str]:
    if not RECENT_DIRS_FILE.exists():
        return []
    return json.loads(RECENT_DIRS_FILE.read_text())

def save_recent_dirs(new_dir: str):
    dirs = load_recent_dirs()
    if new_dir in dirs:
        dirs.remove(new_dir)
    dirs.insert(0, new_dir)
    dirs = dirs[:MAX_RECENT_DIRS]
    RECENT_DIRS_FILE.write_text(json.dumps(dirs, indent=2))

def clear_screen():
    if os.name == 'nt':
        _ = os.system('cls')
    else:
        _ = os.system('clear')


def execute_menu_item(prompt: str, action_map: Dict[str, Callable[[], None]], exit_label: str = "Back", one_time = False) -> None:
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


def open_file_browser_terminal(current_path = os.getcwd()):
    if not os.path.isdir(current_path):
        current_path = os.getcwd()

    current_path = os.path.abspath(current_path)
    
    subdirs = sorted([
        d for d in os.listdir(current_path) 
        if os.path.isdir(os.path.join(current_path, d)) and not d.startswith(".")
    ])
    
    options = ["[SELECT THIS DIRECTORY]", "<= [Go Back]"] + subdirs
    title = f"Pick a destination.\nCurrent Path: {current_path}"

    option = questionary.select(title, options).ask()
    
    if option == "[SELECT THIS DIRECTORY]":
        return UPath(current_path)
    
    elif option == "<= [Go Back]":
        parent_dir = os.path.dirname(current_path)
        return UPath(open_file_browser_terminal(parent_dir))
    
    else:
        new_path = os.path.join(current_path, option)
        return UPath(open_file_browser_terminal(new_path))


def open_file_browser_window():
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        print(e, "open_file_browser_import_error")
        
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
        
        print(path, "open_file_browser_selected")
        
        return path or None

    except Exception as e:
        print(e, "open_file_browser_error")
        
        return None

def pick_from_db_objects(entries_objects, question: str = "Pick one"):
    if isinstance(entries_objects[0], Artist):
        entries_names = extract_db_object_info(entries_objects, f"{artist_categories[0]}")
    else:
        entries_names = extract_db_object_info(entries_objects, f"{song_categories[1]}, {song_categories[0]}, {song_categories[2]}")
        entries_names = [(f"{song[0]} - {song[1]} ({song[2]})",) for song in entries_names]


    slog(entries_names)
    entries_names = [item for t in entries_names for item in t]
    selected_name = questionary.select(question, choices=entries_names).ask()
    selected_object = entries_objects[entries_names.index(selected_name)]
    return selected_object

def get_list_of_properties_from_db_object(db_object):
    properties_list = []
    if type(db_object) is Artist:
        properties_list.append(db_object.name)
        properties_list.append(db_object.origin)
    elif type(db_object) is Song:
        properties_list.append(db_object.title)
        properties_list.append(db_object.artist.name)
        properties_list.append(db_object.album)
        properties_list.append(db_object.year)
        properties_list.append(db_object.language)
        properties_list.append(db_object.artist.origin)
        properties_list.append("")
        properties_list.append("")
    else:
        print("No proper db_object")
        return None
    return properties_list