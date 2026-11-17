"""Settings -> "Automation": how often each automatic task runs
(constants.AUTOMATION_INTERVALS, in minutes) and which of the automation's
rules (constants.AUTOMATION_RULE_LABELS) are on - a rule never switched keeps
its default (AUTOMATION_RULES_ON_BY_DEFAULT). When each task last ran, and the
files it last saw, are kept apart in AUTOMATION_STATE_FILE, written by the
timer (auto_resolve_requests.py)."""
from datetime import datetime, timedelta

from upath import UPath

from config.constants import (AUTOMATION_CONFIG_FILE, AUTOMATION_INTERVALS, AUTOMATION_LOOKUP_RETRY_DAYS,
                              AUTOMATION_MISSES_FILE, AUTOMATION_RULE_LABELS, AUTOMATION_RULES_ON_BY_DEFAULT,
                              AUTOMATION_SLACK_SECONDS, AUTOMATION_STATE_FILE)
from musicdatabase_core.file_tools import now_iso
from utils.common.json_file import read_json, write_json
from utils.common.normalizer import normalize

CONFIG_PATH = UPath(AUTOMATION_CONFIG_FILE)
STATE_PATH = UPath(AUTOMATION_STATE_FILE)
MISSES_PATH = UPath(AUTOMATION_MISSES_FILE)


def _config() -> dict:
    return read_json(CONFIG_PATH, {}, "Automation config")


def _save_config(config: dict) -> None:
    write_json(CONFIG_PATH, config)


def interval_minutes(key: str) -> int:
    """The task's interval; its default when unset or below its minimum."""
    _label, default, minimum = AUTOMATION_INTERVALS[key]
    value = (_config().get("intervals") or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= minimum else default


def save_interval(key: str, minutes: int) -> None:
    if minutes < AUTOMATION_INTERVALS[key][2]:
        raise ValueError(f"{AUTOMATION_INTERVALS[key][0]}: at least {AUTOMATION_INTERVALS[key][2]} minutes")
    config = _config()
    config["intervals"] = {**(config.get("intervals") or {}), key: minutes}
    _save_config(config)


def minutes_text(minutes: int) -> str:
    """"15 min", "2 h", "1 h 30 min"."""
    hours, rest = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} h" if hours else "", f"{rest} min" if rest else "") if part)


def rule_on(key: str) -> bool:
    saved = (_config().get("rules") or {}).get(key)
    return saved if isinstance(saved, bool) else key in AUTOMATION_RULES_ON_BY_DEFAULT


def save_rules_on(keys) -> None:
    """Every rule's switch as now: on for `keys`, off for the rest."""
    config = _config()
    config["rules"] = {key: key in set(keys) for key in AUTOMATION_RULE_LABELS}
    _save_config(config)


def user_rules() -> list[dict]:
    """The owner's own rules (Settings -> "Automation" -> "My rules"), each
    {"name", "question" (a key of AUTOMATION_QUESTIONS), "conditions":
    [{"fact", "op", "value"}], "on"}, in their order."""
    saved = _config().get("user_rules", [])
    return [rule for rule in saved if isinstance(rule, dict)] if isinstance(saved, list) else []


def save_user_rules(rules: list[dict]) -> None:
    config = _config()
    config["user_rules"] = rules
    _save_config(config)


def condition_holds(condition: dict, facts: dict) -> bool:
    """One block: the item's fact compared with the condition's value. An
    unknown fact (None, or one the question doesn't give) never holds."""
    value, wanted, op = facts.get(condition.get("fact")), condition.get("value"), condition.get("op")
    if value is None or wanted is None:
        return False
    if isinstance(value, bool):  # before numbers: a bool is an int
        return op == "is" and value == wanted
    if isinstance(value, (int, float)):
        try:
            wanted = float(wanted)
        except (TypeError, ValueError):
            return False
        return {">=": value >= wanted, "<=": value <= wanted, "=": value == wanted, "!=": value != wanted}.get(op, False)
    text, wanted = normalize(str(value)), normalize(str(wanted))
    return {"is": text == wanted, "is not": text != wanted, "contains": wanted in text,
            "doesn't contain": wanted not in text}.get(op, False)


def has_user_rules(question: str) -> bool:
    """Whether a rule of the owner's that is on asks about `question` - the
    facts that cost a lookup are only gathered then."""
    return any(rule.get("on", True) and rule.get("question") == question for rule in user_rules())


def user_rule_taking(question: str, facts: dict) -> str | None:
    """The name of the first rule of the owner's that is on, asks about
    `question` and whose every block holds for `facts`; None when none does.
    A rule without blocks never holds."""
    for rule in user_rules():
        conditions = rule.get("conditions") or []
        if (rule.get("on", True) and rule.get("question") == question and conditions
                and all(isinstance(c, dict) and condition_holds(c, facts) for c in conditions)):
            return rule.get("name") or question
    return None


def due(key: str, last_at: str | None = None, now: datetime | None = None) -> bool:
    """Whether the task's interval is up since `last_at` (an ISO time with its
    offset; by default when the timer last ran it, mark_run()). Never run =
    due; an unreadable time too."""
    if last_at is None:
        last_at = read_json(STATE_PATH, {}).get(key)
    if not isinstance(last_at, str):
        return True
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return True
    now = now or datetime.now(last.tzinfo)
    return now - last >= timedelta(minutes=interval_minutes(key), seconds=-AUTOMATION_SLACK_SECONDS)


def _file_stamps(paths) -> list:
    return [[str(path), path.stat().st_mtime_ns if path.exists() else None] for path in paths]


def files_changed(key: str, paths) -> bool:
    """Whether any of `paths` changed (or appeared) since remember_files(key, paths)."""
    return read_json(STATE_PATH, {}).get(f"{key}_files") != _file_stamps(paths)


def mark_run(key: str, files=None) -> None:
    """Now as the task's last run; with `files`, also remember how they look now (files_changed())."""
    state = read_json(STATE_PATH, {})
    state[key] = now_iso()
    if files is not None:
        state[f"{key}_files"] = _file_stamps(files)
    write_json(STATE_PATH, state, atomic=True)


def recent_misses(kind: str) -> set[int]:
    """The songs the automation's `kind` lookup ("albums", "years", "genres",
    "lyrics") left without one within AUTOMATION_LOOKUP_RETRY_DAYS."""
    since = datetime.now().astimezone() - timedelta(days=AUTOMATION_LOOKUP_RETRY_DAYS)
    missed = set()
    for song_id, at in (read_json(MISSES_PATH, {}).get(kind) or {}).items():
        try:
            if datetime.fromisoformat(at) >= since:
                missed.add(int(song_id))
        except (TypeError, ValueError):
            continue
    return missed


def remember_misses(kind: str, song_ids) -> None:
    song_ids = list(song_ids)
    if not song_ids:
        return
    misses = read_json(MISSES_PATH, {})
    at = now_iso()
    misses[kind] = {**(misses.get(kind) or {}), **{str(song_id): at for song_id in song_ids}}
    write_json(MISSES_PATH, misses, atomic=True)
