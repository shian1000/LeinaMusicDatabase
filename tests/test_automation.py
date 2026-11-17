"""Settings -> "Automation" (utils/common/automation_settings.py): the intervals
with their defaults and minimums, when a task is due (with the timer's slack),
the rules turned off; and the decision log (utils/common/decision_log.py) every
checkbox list and yes/no question of utils/ui/prompts.py appends to, under the
step it was asked in. Temp files only (conftest.py)."""
import json
from datetime import datetime, timedelta

import pytest
import questionary

from config.constants import AUTOMATION_INTERVALS, AUTOMATION_RULE_LABELS
from utils.common import automation_settings as automation
from utils.common import decision_log
from utils.ui import prompts


@pytest.fixture(autouse=True)
def fresh_files():
    paths = (automation.CONFIG_PATH, automation.STATE_PATH, automation.MISSES_PATH, decision_log.LOG_PATH)
    for path in paths:
        path.unlink(missing_ok=True)
    yield
    # Every other test runs the defaults
    for path in paths:
        path.unlink(missing_ok=True)


def _logged() -> list:
    return [json.loads(line) for line in decision_log.LOG_PATH.read_text().splitlines()]


def test_intervals_default_save_and_refuse_below_their_minimum():
    assert automation.interval_minutes("requests") == AUTOMATION_INTERVALS["requests"][1]
    automation.save_interval("requests", 30)
    assert automation.interval_minutes("requests") == 30
    with pytest.raises(ValueError):
        automation.save_interval("github", 5)
    assert automation.interval_minutes("github") == AUTOMATION_INTERVALS["github"][1]
    assert automation.minutes_text(90) == "1 h 30 min" and automation.minutes_text(15) == "15 min"


def test_a_task_is_due_once_its_interval_is_up_give_or_take_the_slack():
    now = datetime.now().astimezone()
    assert automation.due("requests")  # never run
    automation.mark_run("requests")
    assert not automation.due("requests")
    last = (now - timedelta(minutes=14)).isoformat()
    assert automation.due("requests", last, now)  # the timer started a little early
    assert not automation.due("requests", (now - timedelta(minutes=10)).isoformat(), now)
    assert automation.due("requests", "not a time", now)


def test_rules_keep_their_default_until_switched():
    from config.constants import AUTOMATION_RULES_ON_BY_DEFAULT

    assert {key for key in AUTOMATION_RULE_LABELS if automation.rule_on(key)} == AUTOMATION_RULES_ON_BY_DEFAULT
    automation.save_rules_on(["covers"])
    assert automation.rule_on("covers") and not automation.rule_on("dead_link_same_video")


def test_unattended_lists_take_what_their_rule_allows_and_ask_nothing(monkeypatch):
    monkeypatch.setattr(questionary, "checkbox", lambda *a, **k: pytest.fail("asked"))
    monkeypatch.setattr(questionary, "confirm", lambda *a, **k: pytest.fail("asked"))
    items = ["sure", "guess"]
    automation.save_rules_on(["covers"])
    with prompts.unattended() as taken, decision_log.step("Find covers"):
        picked = prompts.tick_items("Which?", items, str, checked=lambda item: item == "sure", rule="covers")
        assert picked == ["sure"] and prompts.decided(items, picked) == ["sure"]
        # A rule that is off, or none at all: nothing taken
        assert prompts.tick_items("Which?", items, str, checked=True, rule="versions") == []
        assert prompts.tick_items("Which?", items, str, checked=True) == []
        assert prompts.ask_yes_no("Rename?", rule="covers") is True
        assert prompts.ask_yes_no("Remember the rest?") is False
        assert prompts.choose("Pick:", ["a"]) is None and prompts.type_each(items, str, "?", print) == 0
    assert taken == [("Find covers", "sure"), ("Find covers", "Rename?")]
    assert prompts.decided(items, picked) == items  # answered by someone: all of them decided
    assert not decision_log.LOG_PATH.exists()  # nobody answered anything


def test_ticks_and_yes_no_answers_are_logged_under_their_step(monkeypatch):
    class Answer:
        def __init__(self, value):
            self.value = value

        def ask(self):
            return self.value

    monkeypatch.setattr(questionary, "checkbox", lambda question, choices: Answer([1]))
    monkeypatch.setattr(questionary, "confirm", lambda message, default: Answer(False))
    with decision_log.step("Find covers"):
        picked = prompts.tick_items("Which songs?", ["a", "b"], lambda item: [("", item), ("", "!")],
                                    checked=lambda item: item == "a")
        assert prompts.ask_yes_no("Name them now?") is False
    assert picked == ["b"]
    tick, yes_no = _logged()
    assert tick["step"] == "Find covers" and tick["question"] == "Which songs?"
    assert tick["items"] == [{"label": "a!", "proposed": True, "picked": False},
                             {"label": "b!", "proposed": False, "picked": True}]
    assert yes_no == {**yes_no, "kind": "yes_no", "question": "Name them now?", "answer": False}


def test_a_cancelled_question_isnt_logged(monkeypatch):
    monkeypatch.setattr(questionary, "checkbox", lambda question, choices: type("A", (), {"ask": lambda self: None})())
    assert prompts.tick_items("Which?", ["a"], str) is None
    assert not decision_log.LOG_PATH.exists()


def test_unattended_steps_skip_the_ones_turned_off_and_drop_a_failed_steps_items(monkeypatch):
    import menu.main_menu  # noqa: F401 - before the menu package (circular import)
    manage = __import__("importlib").import_module("menu.main_menu.enter_database.manage_database")

    automation.save_rules_on(["covers", "versions"])
    ran = []

    def step(name, fail=False):
        def run():
            ran.append(name)
            prompts.tick_items("?", [name], str, checked=True, rule=name)
            if fail:
                raise RuntimeError("broken")
        return run

    monkeypatch.setattr(manage, "RUN_ALL_STEPS", {"covers": step("covers", fail=True), "versions": step("versions"),
                                                  "projects": step("projects")})
    monkeypatch.setattr(manage, "_undo_unsaved_changes", lambda: None)
    monkeypatch.setattr(manage, "steps_turned_off", lambda: {"projects"})
    assert manage.run_unattended_steps({"covers", "versions", "projects"}) == [("Find versions", "versions")]
    assert ran == ["covers", "versions"]


def test_lookup_misses_are_left_out_only_for_a_while():
    automation.remember_misses("albums", [1, 2])
    assert automation.recent_misses("albums") == {1, 2} and automation.recent_misses("years") == set()
    old = (datetime.now().astimezone() - timedelta(days=60)).isoformat()
    automation.MISSES_PATH.write_text(json.dumps({"albums": {"1": old, "2": "garbage"}}))
    assert automation.recent_misses("albums") == set()


# ==================== the owner's own rules ====================

def _rule(question, *conditions, on=True, name="mine"):
    return {"name": name, "question": question, "on": on,
            "conditions": [{"fact": f, "op": op, "value": v} for f, op, v in conditions]}


@pytest.mark.parametrize("condition, facts, holds", [
    (("similarity", ">=", 0.9), {"similarity": 0.95}, True),
    (("similarity", ">=", 0.9), {"similarity": 0.85}, False),
    (("similarity", "<=", 0.9), {"similarity": None}, False),  # unknown never holds
    (("similarity", "=", 1), {}, False),
    (("certain", "is", True), {"certain": True}, True),
    (("certain", "is", False), {"certain": True}, False),
    (("kind", "is", "Live"), {"kind": "live"}, True),
    (("kind", "contains", "mix"), {"kind": "remix"}, True),
    (("kind", "doesn't contain", "mix"), {"kind": "remix"}, False),
    (("kind", "is not", "live"), {"kind": "remix"}, True),
])
def test_a_block_compares_its_fact_by_kind(condition, facts, holds):
    fact, op, value = condition
    assert automation.condition_holds({"fact": fact, "op": op, "value": value}, facts) is holds


def test_a_rule_holds_when_it_is_on_for_that_question_and_every_block_holds():
    automation.save_user_rules([
        _rule("spell_check_title", ("similarity", ">=", 0.9), ("same_when_normalized", "is", True), name="close"),
        _rule("spell_check_title", ("similarity", ">=", 0.5), on=False, name="off"),
        _rule("spell_check_title", name="empty"),
    ])
    both = {"similarity": 0.95, "same_when_normalized": True}
    assert automation.user_rule_taking("spell_check_title", both) == "close"
    assert automation.user_rule_taking("spell_check_artist", both) is None
    assert automation.user_rule_taking("spell_check_title", {"similarity": 0.95, "same_when_normalized": False}) is None


def test_unattended_the_owners_rule_takes_items_the_built_in_one_doesnt(monkeypatch):
    automation.save_user_rules([_rule("covers", ("covered_artist_exists", "is", True), name="known artist")])
    items = [{"name": "sure", "known": True}, {"name": "guess", "known": True}, {"name": "unknown", "known": False}]
    with prompts.unattended() as taken, decision_log.step("Find covers"):
        picked = prompts.tick_items("?", items, lambda i: i["name"], checked=lambda i: i["name"] == "sure",
                                    rule="covers", facts=lambda i: {"covered_artist_exists": i["known"]})
        assert prompts.ask_yes_no("Rename?", rule="covers", facts={"covered_artist_exists": True}) is True
    assert [i["name"] for i in picked] == ["sure", "guess"]  # the built-in rule "covers" is off
    assert taken == [("Find covers", "sure  [rule: known artist]"), ("Find covers", "guess  [rule: known artist]"),
                     ("Find covers", "Rename?  [rule: known artist]")]


def test_the_facts_are_logged_with_the_owners_answer(monkeypatch):
    class Answer:
        def ask(self):
            return [0]

    monkeypatch.setattr(questionary, "checkbox", lambda question, choices: Answer())
    prompts.tick_items("Which?", ["a", "b"], str, rule="covers", facts=lambda item: {"certain": item == "a"})
    [entry] = _logged()
    assert entry["rule"] == "covers"
    assert [(i["picked"], i["facts"]) for i in entry["items"]] == [(True, {"certain": True}),
                                                                    (False, {"certain": False})]


def test_every_questions_facts_have_a_known_kind():
    from config.constants import AUTOMATION_QUESTIONS, FACT_OPERATORS

    for question, (_label, facts) in AUTOMATION_QUESTIONS.items():
        assert facts and all(kind in FACT_OPERATORS for _label, kind in facts.values())


def test_the_rule_builder_saves_a_rule_of_blocks(monkeypatch):
    from menu.main_menu.settings import automation as menu

    picks = iter(["spell_check_title", "similarity", ">=", "same_when_normalized", True])
    monkeypatch.setattr(menu, "choose", lambda question, choices: next(picks))
    texts = iter(["Close spellings", "0,9"])
    monkeypatch.setattr(menu.questionary, "text", lambda *a, **k: type("A", (), {"ask": lambda self: next(texts)})())
    answers = iter([True, False])
    monkeypatch.setattr(menu, "ask_yes_no", lambda question: next(answers))

    rule = menu._new_rule()

    assert rule == _rule("spell_check_title", ("similarity", ">=", 0.9), ("same_when_normalized", "is", True),
                         name="Close spellings")
    assert "similarity" not in menu._rule_text(rule) and "if old and new title alike" in menu._rule_text(rule)


# ==================== giving way to the app ====================

def test_an_app_waiting_to_start_is_seen_and_gets_the_lock_once_the_run_ends(tmp_path, monkeypatch):
    import threading
    from utils.database import app_lock

    monkeypatch.setattr(app_lock, "LOCK_FILE", tmp_path / ".musicdatabase.lock")
    monkeypatch.setattr(app_lock, "WAITING_FILE", tmp_path / ".musicdatabase.waiting")
    monkeypatch.setattr(app_lock, "_held", [])
    run = app_lock.try_exclusive_lock()
    assert not app_lock.app_waiting()
    waiting = threading.Event()
    app = threading.Thread(target=app_lock.hold_app_lock, kwargs={"on_wait": waiting.set})
    app.start()
    assert waiting.wait(5)
    for _ in range(100):  # the app takes the waiting lock right after saying so
        if app_lock.app_waiting():
            break
        threading.Event().wait(0.01)
    assert app_lock.app_waiting()
    run.close()
    app.join(5)
    assert not app.is_alive() and not app_lock.app_waiting()
    for handle in app_lock._held:
        handle.close()


def test_an_unattended_loop_stops_when_its_time_is_up_or_the_app_waits(monkeypatch):
    from utils.database import app_lock

    waiting = {"now": False}
    monkeypatch.setattr(app_lock, "app_waiting", lambda: waiting["now"])
    assert not prompts.stop_requested()  # someone is there: only Ctrl+C stops
    with prompts.unattended():
        assert not prompts.stop_requested()
        with prompts.time_limit(0):
            assert prompts.stop_requested()
        waiting["now"] = True
        assert prompts.stop_requested() and prompts.app_is_waiting()
    waiting["now"] = True
    assert not prompts.stop_requested()


def test_unattended_steps_stop_before_the_next_step_when_the_app_waits(monkeypatch):
    import menu.main_menu  # noqa: F401 - before the menu package (circular import)
    manage = __import__("importlib").import_module("menu.main_menu.enter_database.manage_database")
    ran = []
    monkeypatch.setattr(manage, "RUN_ALL_STEPS", {"covers": lambda: ran.append("covers"),
                                                  "versions": lambda: ran.append("versions")})
    monkeypatch.setattr(manage, "steps_turned_off", lambda: set())
    monkeypatch.setattr(manage, "app_is_waiting", lambda: bool(ran))
    manage.run_unattended_steps({"covers", "versions"})
    assert ran == ["covers"]
