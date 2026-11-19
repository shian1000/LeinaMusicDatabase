"""
An artist's origin as stored in artists.origin: "Country - Region - City", the
country in one English form (the owner, 2026-11-19 - translated for display
later, one form stored), first ("Paris - France" turned around too). Leina Radio takes the part before the first " - " as
the country (its stats, Wyszukaj's "Przeglądaj").

clean_origin() is what every write goes through (Artist's validator in
datatables.py): the country looked up by place_key() in ORIGIN_COUNTRY_SYNONYMS
and pycountry (ORIGIN_COUNTRY_NAMES where its name isn't the common English
one), the United Kingdom's countries as "United Kingdom - England", "City,
Country" turned around, several countries joined by " / ", the leading "#" of
older rows dropped, and what isn't a place (download sites' tags, "World")
cleared. A first part that isn't a country is kept as written ("Paris"). No
database here.
"""

import re
from functools import lru_cache

from config.constants import (ORIGIN_COUNTRY_NAMES, ORIGIN_COUNTRY_SEPARATOR, ORIGIN_COUNTRY_SYNONYMS,
                              ORIGIN_JUNK_NAMES, ORIGIN_JUNK_WORDS, ORIGIN_OTHER_COUNTRIES, ORIGIN_SEPARATOR,
                              UK_COUNTRIES)
from utils.common.normalizer import composed, normalize

_PART_SEPARATOR = re.compile(r"\s+-\s+")
_COUNTRIES_SEPARATOR = re.compile(r"\s+[/&]\s+|\s*/\s*")
_DOMAIN = re.compile(r"\.\w{2,4}\b")


def place_key(name: str | None) -> str:
    """"Bosna and Hercegovina" -> "bosnaandhercegovina"."""
    return normalize(name or "").replace(" ", "")


@lru_cache(maxsize=1024)
def country_name(name: str) -> str | None:
    """The stored form of a country's name, None when `name` isn't one."""
    key = place_key(name)
    if not key:
        return None
    if key in ORIGIN_COUNTRY_SYNONYMS:
        return ORIGIN_COUNTRY_SYNONYMS[key]
    for other in (*ORIGIN_OTHER_COUNTRIES, *UK_COUNTRIES):
        if key == place_key(other):
            return other
    if len(key) <= 3:  # pycountry takes codes too: "LA" (Los Angeles) isn't Laos - abbreviations only as synonyms
        return None
    import pycountry  # loads its tables on first use
    try:
        found = pycountry.countries.lookup(name.strip())
    except LookupError:
        return None
    return ORIGIN_COUNTRY_NAMES.get(found.alpha_2) or getattr(found, "common_name", None) or found.name


def is_junk(text: str) -> bool:
    """Not a place: a download site's or ripper's tag, a nickname, "World"."""
    lower = text.lower()
    return (not re.search(r"[^\W\d_]", lower) or bool(_DOMAIN.search(lower)) or lower.strip() in ORIGIN_JUNK_NAMES
            or any(word in lower for word in ORIGIN_JUNK_WORDS))


def _with_uk(country: str) -> list[str]:
    return ["United Kingdom", country] if country in UK_COUNTRIES else [country]


def clean_origin(value) -> str | None:
    """As stored (see the module docstring); nothing or junk = None."""
    if value is None:
        return None
    text = " ".join(composed(str(value)).split()).lstrip("#").strip()
    if not text or is_junk(text):
        return None
    if "," in text and ORIGIN_SEPARATOR not in text:  # "Melbourne, Australia", "Buffalo, New York, U.S."
        pieces = [piece.strip() for piece in text.split(",") if piece.strip()]
        if country_name(pieces[-1]):
            text = ORIGIN_SEPARATOR.join(reversed(pieces))
    parts = _PART_SEPARATOR.split(text)
    if len(parts) > 1 and not country_name(parts[0]) and country_name(parts[-1]):  # "Paris - France"
        parts = parts[::-1]
    # "Croatia / Macedonia" (two countries) or "Lithuania / Skuodas" (a country and its town)
    heads = [head.strip() for head in _COUNTRIES_SEPARATOR.split(parts[0]) if head.strip()]
    countries = [country_name(head) for head in heads]
    if countries and countries[0] and not all(countries):
        parts = [heads[0], *heads[1:], *parts[1:]]
        heads, countries = heads[:1], countries[:1]
    if all(countries) and countries:
        if len(countries) == 1:
            head = _with_uk(countries[0])
        else:
            head = [ORIGIN_COUNTRY_SEPARATOR.join(dict.fromkeys(
                "United Kingdom" if country in UK_COUNTRIES else country for country in countries))]
        rest = parts[1:]
        if head[0] == "United Kingdom" and rest and country_name(rest[0]) in UK_COUNTRIES:
            rest = [country_name(rest[0]), *rest[1:]]
            head = head[:1]
        parts = [*head, *rest]
    return ORIGIN_SEPARATOR.join(part.strip() for part in parts if part.strip()) or None
