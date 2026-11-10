"""The app's own small JSON files - settings, start-up state, remembered
answers (ignore lists): read with a fallback, written whole."""
import json

from utils.common.debug import flog


def read_json(path, default, what: str | None = None):
    """The file's value; `default` when it's missing, unreadable or not of
    `default`'s type (a JSON object for {}, a list for []). With `what`, an
    unreadable file is logged under that name."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        problem = str(exc)
    else:
        if isinstance(value, type(default)):
            return value
        problem = f"not a JSON {type(default).__name__}"
    if what:
        flog(f"{what} ({path}) can't be read ({problem}), using the default")
    return default


def write_json(path, value, atomic: bool = False) -> None:
    """`value` as indented UTF-8 JSON, the folder made when missing.
    `atomic`: through a temporary file, for one read at every start - a write
    cut short must not leave half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, ensure_ascii=False)
    if not atomic:
        path.write_text(text, encoding="utf-8")
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
