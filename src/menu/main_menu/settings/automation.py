"""Settings -> "Automation": how often the automation timer (auto_resolve_requests.py)
runs each task, and which rules it settles Leina Radio's requests and the
clean-ups' proposals by on its own (utils/common/automation_settings.py). What
no rule that is on settles waits for "Resolve new requests" or the clean-up
step itself; the owner's answers go into the decision log."""

import questionary

from config.constants import (AUTOMATION_INTERVALS, AUTOMATION_QUESTIONS, AUTOMATION_RULE_LABELS, FACT_NUMBER,
                              FACT_OPERATORS, FACT_YES_NO, INBOX_AUTO_TICK)
from utils.common import automation_settings as automation
from utils.common.decision_log import LOG_PATH
from utils.common.output import error, say
from utils.ui.prompts import ask_yes_no, choose, tick_items


def _interval_label(key: str) -> str:
    return f"{AUTOMATION_INTERVALS[key][0]} {automation.minutes_text(automation.interval_minutes(key))}"


def _set_interval(key: str) -> None:
    label, default, minimum = AUTOMATION_INTERVALS[key]
    typed = questionary.text(f"{label} how many minutes? (at least {minimum}, default {default})",
                             default=str(automation.interval_minutes(key))).ask()
    if not typed:
        say("Cancelled, nothing changed.")
        return
    try:
        automation.save_interval(key, int(typed))
    except ValueError:
        error(f"'{typed}' isn't a number of minutes of at least {minimum}.")
        return
    say(f"{_interval_label(key)} - from the timer's next run, no restart needed.")


def _set_rules() -> None:
    picked = tick_items("Rules the automation settles things by on its own (space toggles, enter saves):",
                        list(AUTOMATION_RULE_LABELS), AUTOMATION_RULE_LABELS.get, checked=automation.rule_on)
    if picked is None:
        say("Cancelled, nothing changed.")
        return
    if set(picked) == {key for key in AUTOMATION_RULE_LABELS if automation.rule_on(key)}:
        say("Nothing changed.")
        return
    automation.save_rules_on(picked)
    say(f"{len(picked)} of {len(AUTOMATION_RULE_LABELS)} rule(s) on. What no rule settles waits for you: "
        'Leina Radio\'s requests in "Resolve new requests", the rest in the clean-up steps themselves.')


# ==================== the owner's own rules ====================

def _value_text(value) -> str:
    return ("yes" if value else "no") if isinstance(value, bool) else str(value)


def _condition_text(question: str, condition: dict) -> str:
    fact = AUTOMATION_QUESTIONS.get(question, ("", {}))[1].get(condition.get("fact"))
    return f"{fact[0] if fact else condition.get('fact')} {condition.get('op')} {_value_text(condition.get('value'))}"


def _rule_text(rule: dict) -> str:
    question = rule.get("question")
    blocks = " and ".join(_condition_text(question, c) for c in rule.get("conditions") or []) or "(no blocks yet)"
    asked = AUTOMATION_QUESTIONS.get(question, (f"{question} (no longer asked)",))[0]
    return f"[{'ON ' if rule.get('on', True) else 'OFF'}] {rule.get('name')} - {asked}: if {blocks}"


def _ask_condition(question: str) -> dict | None:
    """One block: a fact of `question`, how to compare it and with what; None when cancelled."""
    facts = AUTOMATION_QUESTIONS[question][1]
    fact = choose("If this fact:", [questionary.Choice(title=label, value=key) for key, (label, _) in facts.items()])
    if fact is None:
        return None
    kind = facts[fact][1]
    if kind == FACT_YES_NO:
        value = choose("is:", [questionary.Choice(title="yes", value=True), questionary.Choice(title="no", value=False)])
        return None if value is None else {"fact": fact, "op": "is", "value": value}
    op = choose("compared how:", list(FACT_OPERATORS[kind]))
    if op is None:
        return None
    if kind == FACT_NUMBER:
        typed = questionary.text(f"{facts[fact][0]} {op}", validate=lambda text: _is_number(text)
                                 or "a number, e.g. 0.9").ask()
        return None if typed is None else {"fact": fact, "op": op, "value": float(typed.replace(",", "."))}
    typed = questionary.text(f"{facts[fact][0]} {op}", validate=lambda text: bool(text.strip()) or "type a text").ask()
    return None if typed is None else {"fact": fact, "op": op, "value": typed.strip()}


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False


def _add_blocks(rule: dict) -> None:
    """Blocks one after another until "Done"; all of them must hold."""
    while True:
        if rule["conditions"]:
            say(f"  {_rule_text(rule)}")
        condition = _ask_condition(rule["question"])
        if condition is None:
            return
        rule["conditions"].append(condition)
        if not ask_yes_no("Add another block (all of them must hold)?"):
            return


def _new_rule() -> dict | None:
    question = choose("Which question does it answer?",
                      [questionary.Choice(title=label, value=key) for key, (label, _) in AUTOMATION_QUESTIONS.items()])
    if question is None:
        return None
    name = (questionary.text("The rule's name:").ask() or "").strip()
    if not name:
        return None
    rule = {"name": name, "question": question, "conditions": [], "on": True}
    _add_blocks(rule)
    return rule if rule["conditions"] else None


def _edit_rule(rules: list, rule: dict) -> None:
    while True:
        say(_rule_text(rule))
        switch = "Turn it off" if rule.get("on", True) else "Turn it on"
        action = choose("What now?", [switch, "Add a block", "Remove a block", "Rename", "Delete"])
        if action is None:
            return
        if action == switch:
            rule["on"] = not rule.get("on", True)
        elif action == "Add a block":
            if rule.get("question") not in AUTOMATION_QUESTIONS:
                error("That question isn't asked any more - only deleting is left.")
                continue
            _add_blocks(rule)
        elif action == "Remove a block":
            conditions = rule.get("conditions") or []
            picked = choose("Remove which?", [questionary.Choice(title=_condition_text(rule["question"], c), value=i)
                                              for i, c in enumerate(conditions)])
            if picked is not None:
                del conditions[picked]
        elif action == "Rename":
            name = (questionary.text("New name:", default=rule.get("name") or "").ask() or "").strip()
            if name:
                rule["name"] = name
        elif ask_yes_no(f'Delete the rule "{rule.get("name")}"?'):
            rules.remove(rule)
            automation.save_user_rules(rules)
            return
        automation.save_user_rules(rules)


def _my_rules() -> None:
    say("Your own rules: when the automation asks a question nobody is there to answer, it takes every item")
    say("one of your rules (that is on) holds for - all its blocks. The facts of each question are also")
    say(f"logged with your answers in {LOG_PATH}, so you can see which values you always accept.\n")
    while True:
        rules = automation.user_rules()
        picked = choose("My rules:", [*(questionary.Choice(title=_rule_text(rule), value=i)
                                        for i, rule in enumerate(rules)),
                                      questionary.Choice(title="Add a rule", value="add")])
        if picked is None:
            return
        if picked == "add":
            rule = _new_rule()
            if rule is None:
                say("Nothing added.")
                continue
            automation.save_user_rules([*rules, rule])
            say(f"Added: {_rule_text(rule)}")
        else:
            _edit_rule(rules, rules[picked])


def automation_menu() -> None:
    say(f"The automation timer wakes every {INBOX_AUTO_TICK} while MusicDatabase is closed and runs what is due.")
    say(f"Your answers in reviews and questions are logged in {LOG_PATH} - the material for new rules.\n")
    while True:
        rules_on = sum(automation.rule_on(key) for key in AUTOMATION_RULE_LABELS)
        options = {_interval_label(key): key for key in AUTOMATION_INTERVALS}
        rules = f"Built-in rules ({rules_on} of {len(AUTOMATION_RULE_LABELS)} on)"
        mine = f"My rules ({len(automation.user_rules())})"
        picked = choose("Automation:", [*options, rules, mine])
        if picked is None:
            return
        if picked == rules:
            _set_rules()
        elif picked == mine:
            _my_rules()
        else:
            _set_interval(options[picked])
