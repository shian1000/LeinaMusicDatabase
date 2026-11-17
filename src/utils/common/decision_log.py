"""What the owner answered, as the material for new rules of the automatic
pass (Settings -> "Automation"): every checkbox list and yes/no question of
utils/ui/prompts.py and every answer of the request review, one JSON line
each in DECISION_LOG_FILE - when, in which step (the menu entry or the "Run
all" step it was asked in, `step()`), what was proposed and what was picked.
Only appended; the app never reads it back, and a failed write only logs."""
import json
from contextlib import contextmanager
from contextvars import ContextVar

from upath import UPath

from config.constants import DECISION_LOG_FILE
from musicdatabase_core.file_tools import now_iso
from utils.common.debug import flog

LOG_PATH = UPath(DECISION_LOG_FILE)

_step = ContextVar("decision_step", default=None)


@contextmanager
def step(name: str):
    """The answers given inside are logged under `name` (the innermost one)."""
    token = _step.set(name)
    try:
        yield
    finally:
        _step.reset(token)


def current_step() -> str | None:
    return _step.get()


def plain(label) -> str:
    """A label as text: prompt_toolkit (style, text) tokens joined."""
    return label if isinstance(label, str) else "".join(text for _style, text, *_ in label)


def record(kind: str, **fields) -> None:
    entry = {"at": now_iso(), "step": _step.get(), "kind": kind, **fields}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        flog(f"Decision log ({LOG_PATH}) not written: {exc}")
