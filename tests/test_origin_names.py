"""Artists' origins (artists.origin; utils/common/origin_names.py): one English
form per country on every ORM write (Artist's validator), the United Kingdom's
countries under it, "City, Country" turned around, what isn't a place cleared,
and Manage database -> "Unify language and country names" for older rows
(the checkbox list answered by the test). In-memory SQLite."""

import sys
from pathlib import Path

import pytest
import questionary
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from utils.common.origin_names import clean_origin
from utils.database.datatables import Artist, Base
from utils.database.languages import spelling_fixes
from utils.ui import language_review


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.mark.parametrize("typed, stored", [
    ("#Serbia - Belgrade", "Serbia - Belgrade"),  # the old "#" dropped
    ("Bosna and Herzegovina", "Bosnia and Herzegovina"),
    ("Bosna and Hercegovina & Poland", "Bosnia and Herzegovina / Poland"),
    ("Croatia / Macedonia", "Croatia / North Macedonia"),
    ("Lithuania / Skuodas", "Lithuania - Skuodas"),  # a country and its town
    ("#The Netherlands / The Hague", "Netherlands - The Hague"),
    ("USA - California - Los Angeles", "United States - California - Los Angeles"),
    ("England - London", "United Kingdom - England - London"),
    ("Scotland", "United Kingdom - Scotland"),
    ("UK", "United Kingdom"),
    ("England / France", "United Kingdom / France"),
    ("Buffalo, New York, U.S.", "United States - New York - Buffalo"),
    ("Melbourne, Australia", "Australia - Melbourne"),
    ("Paris - France", "France - Paris"),
    ("Austalia", "Australia"),  # typos found in the catalog
    ("Finish", "Finland"),
    ("Korea", "South Korea"),
    ("Turkey - Istanbul", "Turkey - Istanbul"),  # not pycountry's "Türkiye"
    ("Yugoslavia", "Yugoslavia"),
    ("Paris", "Paris"),  # not a country: kept as written
    ("Converted with ClipConverter.cc", None),
    ("zapraszam do chomika rbbt", None),
    ("7 7 6 6", None),
    ("World", None),
    ("  ", None),
    (None, None),
])
def test_one_english_form_per_country(typed, stored):
    assert clean_origin(typed) == stored


def test_every_orm_write_is_cleaned():
    assert Artist(name="Bijelo dugme", origin="#Bosna and Herzegovina - Sarajevo").origin == \
        "Bosnia and Herzegovina - Sarajevo"


def test_older_origins_are_unified_with_the_languages(session, monkeypatch):
    artist = Artist(name="A")
    session.add(artist)
    session.flush()
    for name, origin in (("B", "#Serbia - Belgrade"), ("C", "England"), ("D", "Converted with ClipConverter.cc"),
                         ("E", "Poland")):
        session.execute(text("INSERT INTO artists (name, origin) VALUES (:n, :o)"), {"n": name, "o": origin})
    assert [fix[0] for fix in spelling_fixes(session, "origin")] == [
        "#Serbia - Belgrade", "Converted with ClipConverter.cc", "England"]
    monkeypatch.setattr(language_review, "open_and_set_global_database_sessions", lambda: (session, None))
    monkeypatch.setattr(language_review, "submit_global_database_session", session.commit)
    monkeypatch.setattr(questionary, "checkbox", lambda _q, choices: type("Prompt", (), {
        "ask": lambda self: [choice.value for choice in choices]})())

    assert language_review.unify_language_and_country_names() == 3
    assert sorted((o or "") for (o,) in session.execute(text("SELECT origin FROM artists WHERE name != 'A'"))) == [
        "", "Poland", "Serbia - Belgrade", "United Kingdom - England"]
