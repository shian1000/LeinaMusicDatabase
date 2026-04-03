
from typing import Any, Optional
import inspect
from pathlib import Path


def _find_project_root() -> Path:
	for _ in range(6):
		p = Path("")
		if (p / "main.py").exists() or (p / ".git").exists() or (p / "pyproject.toml").exists() or (p / "setup.py").exists():
			return p
		if p.parent == p:
			break
		p = p.parent
	return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _find_project_root()
DEBUG_ENABLED = (PROJECT_ROOT / ".debug").exists()


def slog(var: Any, name: Optional[str] = None) -> None:
	if not DEBUG_ENABLED:
		return

	if name is None:
		try:
			frame = inspect.currentframe()
			if frame is not None:
				caller = frame.f_back
				if caller is not None:
					for var_name, val in caller.f_locals.items():
						if val is var:
							name = var_name
							break
		finally:
			try:
				del frame
			except Exception:
				pass

	if name is None:
		name = "<unknown>"

	print(f"***Debug*** {name} ({type(var).__name__}): {repr(var)}")

def mlog(message: str):
	if not DEBUG_ENABLED:
		return
		
	print("***Debug*** ", message)