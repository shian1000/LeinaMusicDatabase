from typing import Callable, Dict, Optional
import questionary
import os
from upath import UPath
from utils.debug import slog
import json

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


def execute_menu_item(prompt: str, action_map: Dict[str, Callable[[], None]], exit_label: str = "Back") -> None:
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
            if action_name == "Back":
                running = False
                break


def open_file_browser_terminal(current_path = os.getcwd()):
    if not os.path.isdir(current_path):
        current_path = os.getcwd()

    current_path = os.path.abspath(current_path)
    
    subdirs = [
        d for d in os.listdir(current_path) 
        if os.path.isdir(os.path.join(current_path, d))
    ]
    
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