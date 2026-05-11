from typing import Any, Optional
import inspect
from pathlib import Path
import linecache
import ast


def _find_project_root() -> Path:
    # Start at the directory of the current file
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

import sys
import traceback
from datetime import datetime

def _handle_exception(exc_type, exc_value, exc_tb) -> None:
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
    try:
        with DEBUG_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass



def slog(var: Any = None, name: Optional[str] = None) -> None:
    if not DEBUG_ENABLED:
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
    print(message)

    if DEBUG_TO_FILE_ENABLED:
        _write_to_log(message)

def mlog(message: str):
    if not DEBUG_ENABLED:
        return
    print(f"***Debug*** {message}")