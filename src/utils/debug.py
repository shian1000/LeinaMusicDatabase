from typing import Any, Optional
import inspect
from pathlib import Path

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

def slog(var: Any, name: Optional[str] = None) -> None:
    if not DEBUG_ENABLED:
        return

    file_info = "unknown:0"
    
    try:
        frame = inspect.currentframe()
        if frame is not None:
            caller = frame.f_back
            if caller is not None:
                # 1. Get location info
                filename = Path(caller.f_code.co_filename).name
                lineno = caller.f_lineno
                file_info = f"{filename}:{lineno}"

                # 2. Try to find the variable name if not provided
                if name is None:
                    for var_name, val in caller.f_locals.items():
                        if val is var:
                            name = var_name
                            break
    finally:
        # Crucial to prevent reference cycles in the garbage collector
        del frame

    if name is None:
        name = "<unknown>"

    print(f"***Debug*** [{file_info}] {name} ({type(var).__name__}): {repr(var)}")

def mlog(message: str):
    if not DEBUG_ENABLED:
        return
    print(f"***Debug*** {message}")