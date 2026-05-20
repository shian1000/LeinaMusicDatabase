"""Debug utilities for logging and exception handling.

This module provides debugging functions that can log to console and/or file.
Debug output can be controlled via a `.debug` file in the project root.
"""

from typing import Any, Optional
import inspect
from pathlib import Path
import linecache
import ast
import sys
import traceback
from datetime import datetime


def _find_project_root() -> Path:
    """
    Find the project root directory by looking for marker files.
    
    Searches upward from the current file location for:
    - main.py (Python project entry point)
    - .git (Git repository)
    - pyproject.toml (Python project config)
    
    Returns:
        Path: Project root directory, or current file's parent if not found
    """
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / "main.py").exists() or (p / ".git").exists() or (p / "pyproject.toml").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return p


PROJECT_ROOT = _find_project_root()
DEBUG_ENABLED = (PROJECT_ROOT / ".debug").exists()

DEBUG_TO_FILE_ENABLED = True
DEBUG_LOG_FILE = PROJECT_ROOT / "debug.log"


def _handle_exception(exc_type, exc_value, exc_tb) -> None:
    """
    Global exception handler for uncaught exceptions.
    
    Logs exception traceback to console and file. KeyboardInterrupt
    is not logged (allows normal Ctrl+C handling).
    
    Args:
        exc_type: Exception type
        exc_value: Exception value/message
        exc_tb: Exception traceback object
    """
    # Let KeyboardInterrupt pass through normally (Ctrl+C shouldn't be logged as a crash)
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text = "".join(tb_lines)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"***Debug*** [CRASH @ {timestamp}]\n{tb_text}"

    print(message, file=sys.stderr)

    if DEBUG_TO_FILE_ENABLED:
        _write_to_log(message)


sys.excepthook = _handle_exception


def _write_to_log(message: str) -> None:
    """
    Write a debug message to the log file.
    
    Safely appends message to debug log file. Silently fails if file
    cannot be written.
    
    Args:
        message: Message text to log
    """
    try:
        with DEBUG_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


def slog(var: Any = None, name: Optional[str] = None) -> None:
    """
    Log a variable's value with type and source location information.
    
    Smart logging function that automatically extracts the variable name
    from the calling code and includes file/line information. Works only
    if DEBUG_ENABLED or DEBUG_TO_FILE_ENABLED is True.
    
    Args:
        var: Variable to log (can be any type)
        name: Optional custom name for the variable (auto-detected if None)
    
    Example:
        >>> x = 42
        >>> slog(x)  # Logs: [filename:lineno] x (int): 42
    """
    if not DEBUG_ENABLED and not DEBUG_TO_FILE_ENABLED:
        return

    file_info = "unknown:0"

    try:
        frame = inspect.currentframe()
        if frame is not None:
            caller = frame.f_back
            if caller is not None:
                filename = Path(caller.f_code.co_filename).name
                lineno = caller.f_lineno
                file_info = f"{filename}:{lineno}"

                # Try to extract variable name from source code
                if name is None:
                    try:
                        full_path = caller.f_code.co_filename
                        call_line = linecache.getline(full_path, lineno).strip()
                        tree = ast.parse(call_line, mode='eval')
                        call_node = tree.body
                        if isinstance(call_node, ast.Call) and call_node.args:
                            name = ast.unparse(call_node.args[0])
                    except Exception:
                        pass
    finally:
        del frame

    if name is None:
        name = "<unknown>"

    message = f"***Debug*** [{file_info}] {name} ({type(var).__name__}): {repr(var)}"

    if DEBUG_ENABLED:
        print(message)

    if DEBUG_TO_FILE_ENABLED:
        _write_to_log(message)


def mlog(message: str) -> None:
    """
    Log a simple message string.
    
    Logs a plain text message to console. Works only if DEBUG_ENABLED is True.
    
    Args:
        message: Message text to log
    
    Example:
        >>> mlog("Application started")  # Logs if debug enabled
    """
    if not DEBUG_ENABLED:
        return
    print(f"***Debug*** {message}")
