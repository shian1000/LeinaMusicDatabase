from typing import Callable, Dict, Optional
from src.debug import slog
import questionary


def execute_menu_item(prompt: str, action_map: Dict[str, Callable[[], None]], exit_label: str = "Back") -> None:

    choices = list(action_map.keys()) + [exit_label]

    while True:
        choice: Optional[str] = questionary.select(prompt, choices=choices).ask()

        if choice not in choices or choice == exit_label:
            break

        action = action_map.get(choice)
        if action:
            action()

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