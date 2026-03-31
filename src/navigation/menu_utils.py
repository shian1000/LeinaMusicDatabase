from typing import Callable, Dict, Optional
from src.debug import slog
import questionary
import os
from pick import pick
from upath import UPath

def clear_screen():
    # For Windows
    if os.name == 'nt':
        _ = os.system('cls')
    # For macOS and Linux (here, os.name is 'posix')
    else:
        _ = os.system('clear')


def execute_menu_item(prompt: str, action_map: Dict[str, Callable[[], None]], exit_label: str = "Back") -> None:

    choices = list(action_map.keys()) + [exit_label]

    robicdalej = True

    while robicdalej:
        choice: Optional[str] = questionary.select(prompt, choices=choices).ask()

        if choice not in choices or choice == exit_label:
            clear_screen()
            break

        action = action_map.get(choice)
        if action:
            biteme = action()
            if biteme == "Back":
                robicdalej = False
                break




def open_file_browser_terminal(current_path):
    if not os.path.isdir(current_path):
        current_path = os.getcwd()

    current_path = os.path.abspath(current_path)
    
    subdirs = [
        d for d in os.listdir(current_path) 
        if os.path.isdir(os.path.join(current_path, d))
    ]
    
    options = ["[SELECT THIS DIRECTORY]", "<= [Go Back]"] + subdirs
    title = f"Pick a destination.\nCurrent Path: {current_path}"

    option, index = pick(options, title)
    
    if option == "[SELECT THIS DIRECTORY]":
        return UPath(current_path)
    
    elif option == "<= [Go Back]":
        parent_dir = os.path.dirname(current_path)
        return UPath(open_file_browser_terminal(parent_dir))
    
    else:
        new_path = os.path.join(current_path, option)
        return UPath(open_file_browser_terminal(new_path))

    



def open_file_browser():
    """Open a native file chooser and return the selected path or None.

    Uses tkinter.filedialog.askopenfilename. This function logs debug
    information using `slog` from `src.debug` when available.
    """
    try:
        # Local import so we don't force tkinter when the module is imported
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        # If tkinter not available (headless env), log and return None
        slog(e, "open_file_browser_import_error")
        
        return None

    try:
        root = tk.Tk()
        root.withdraw()
        # On some platforms this helps bring the dialog to the front
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        path = filedialog.askopenfilename()
        root.destroy()
        
        slog(path, "open_file_browser_selected")
        
        return path or None

    except Exception as e:
        slog(e, "open_file_browser_error")
        
        return None