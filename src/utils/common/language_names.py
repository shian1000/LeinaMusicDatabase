"""
A song's language as stored in songs.language: English names, several joined
by " / " ("English / Japanese"), one spelling per language - Leina Radio counts
its listening stats and searches by the value as it is.

clean_language() is what every write goes through (Song's validator in
datatables.py): each part looked up by language_key() in LANGUAGE_SYNONYMS,
a lowercase ISO 639 code ("eng", "pl" - mp3 TLAN tags) by its name, any other
name kept as written (capitalised when typed all lowercase - the owner's own
names, "Croatian (Kajkavian)", "Interslavic", stay). No database here.

language_of_text() reads the language of a text (lyrics) with langdetect,
narrowed by the alphabet and the letters (the owner's rules, 2026-11-19):
langdetect knows no Belarusian, Serbian, Montenegrin, Mongolian or Yiddish, and
mixes up Ukrainian and Russian - the letters only one alphabet has decide
(romanize.cyrillic_language(), ŭ, ś), and a few languages it doesn't know at all
come from their letters or words (LANGUAGE_MARKS). Each line counts once (a chorus
sung ten times doesn't outweigh the verses), the stanzas vote (an intro in another
language doesn't decide), and a text holding a translation isn't read
(looks_like_translation()). song_language() adds what the song's names, its
artist's other songs and origin tell. Invented languages are never read: no rule
tells them (owner, 2026-11-20).
"""

import re
import unicodedata
from functools import lru_cache

from config.constants import (KAJKAVIAN, LANGUAGE_CODE_NAMES, LANGUAGE_CREDIT_WORDS, LANGUAGE_DETECT_MIN_LETTERS,
                              LANGUAGE_INLINE_TRANSLATION_MARK, LANGUAGE_INLINE_TRANSLATION_SHARE,
                              LANGUAGE_INSTRUMENTAL, LANGUAGE_MARKS, LANGUAGE_NEIGHBOURS, LANGUAGE_PROOF_WORDS,
                              LANGUAGE_RARELY_RIGHT,
                              LANGUAGE_ROMAJI_MISREADINGS, LANGUAGE_ROMAJI_ONLY_MISREADINGS, LANGUAGE_ROMAJI_SHARE,
                              LANGUAGE_SECOND_SCRIPT_SHARE, LANGUAGE_SEPARATOR, LANGUAGE_STANZA_LINES,
                              LANGUAGE_STANZA_MIN_LETTERS, LANGUAGE_STANZA_SURE, LANGUAGE_SYNONYMS,
                              LANGUAGE_TRANSLATION_ALTERNATION, LANGUAGE_TRANSLATION_HEAD_LINES,
                              LANGUAGE_TRANSLATION_MIN_LINES, LANGUAGE_TRANSLATION_WORDS, LANGUAGE_UNKNOWN,
                              LYRICS_OTHER_SCRIPT_SHARE, SERBIAN_EKAVIAN_WORDS, SERBO_CROATIAN, YIDDISH_MARKS,
                              YUGOSLAV_COUNTRIES, YUGOSLAVIA_LAST_YEAR)
from utils.common.normalizer import composed, normalize
from utils.common.origin_names import clean_origin
from utils.common.romanize import cyrillic_language

_SEPARATORS = re.compile(r"\s*[/,;]\s*")
# Only lowercase, as tags and lookups write codes: the owner's "Ga" stays Ga, not Irish
_CODE = re.compile(r"[a-z]{2,3}")
_PYCOUNTRY_SUFFIX = re.compile(r"\s*\((macrolanguage|\d{4}-)\)$")


def language_key(name: str | None) -> str:
    """"Croatian (Čakavian)" -> "croatiancakavian"."""
    return normalize(name or "").replace(" ", "")


@lru_cache(maxsize=512)
def code_name(code: str) -> str | None:
    """The language an ISO 639-1 / -2 / -3 code names, None when it's none."""
    if code in LANGUAGE_CODE_NAMES:
        return LANGUAGE_CODE_NAMES[code]
    import pycountry  # loads its tables on first use, only codes need them
    for field in ("alpha_2", "alpha_3", "bibliographic"):
        found = pycountry.languages.get(**{field: code})
        if found is not None:
            return _PYCOUNTRY_SUFFIX.sub("", found.name)
    return None


def canonical_language(name: str) -> str:
    """The spelling one language is stored under."""
    name = " ".join(composed(name).split())
    if _CODE.fullmatch(name):
        name = code_name(name) or name
    key = language_key(name)
    if key in LANGUAGE_SYNONYMS:
        return LANGUAGE_SYNONYMS[key]
    return name[:1].upper() + name[1:] if name.islower() else name


def clean_language(value) -> str | None:
    """As stored: each language in its one spelling, the order kept,
    repeats and an "Unknown" beside a known one dropped, joined by " / ";
    nothing = None."""
    if value is None:
        return None
    languages, seen = [], set()
    for part in _SEPARATORS.split(str(value)):
        if not part.strip():
            continue
        language = canonical_language(part)
        if language_key(language) not in seen:
            seen.add(language_key(language))
            languages.append(language)
    if len(languages) > 1:
        languages = [language for language in languages if language != LANGUAGE_UNKNOWN] or languages
    return LANGUAGE_SEPARATOR.join(languages) or None


# Which langdetect codes each script may be (the alphabet narrows the field); a script missing
# here is read by langdetect without narrowing
_SCRIPT_CODES = {
    "CYRILLIC": ("ru", "uk", "bg", "mk"),
    "ARABIC": ("ar", "fa", "ur"),
    "DEVANAGARI": ("hi", "mr", "ne"),
    "GREEK": ("el",),
}
_SCRIPT_LANGUAGES = {"HANGUL": "Korean", "KANA": "Japanese", "CJK": "Chinese", "GEORGIAN": "Georgian",
                     "ARMENIAN": "Armenian", "THAI": "Thai"}
_CYRILLIC_CODES = {"be": "Belarusian", "uk": "Ukrainian", "ru": "Russian", "sr": "Serbian"}
# A syllable or an aksara counts as the two letters it is in Latin (Bloodywood's Hindi lyrics fell under the second
# script's share otherwise)
_SYLLABIC = ("HANGUL", "KANA", "CJK", "DEVANAGARI", "GURMUKHI", "BENGALI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM",
             "GUJARATI", "THAI")
_LACINKA_MIN_LETTERS = 3  # č / š / ž at least this many times
_VIETNAMESE_LETTERS = set("ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")


def _script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "").split(" ")[0]
    return {"HIRAGANA": "KANA", "KATAKANA": "KANA"}.get(name, name)


def _lyrics_only(text: str) -> str:
    """Without the lines crediting the authors ("Lời: ...", "Writer(s): ...")
    and, when LANGUAGE_INLINE_TRANSLATION_SHARE of the lines hold one, each
    line's translation after LANGUAGE_INLINE_TRANSLATION_MARK."""
    lines = [line for line in text.splitlines() if not _CREDIT_LINE.match(line.strip().lower())]
    sung = [line for line in lines if line.strip()]
    if sung and sum(LANGUAGE_INLINE_TRANSLATION_MARK in line for line in sung) >= len(sung) * LANGUAGE_INLINE_TRANSLATION_SHARE:
        lines = [line.split(LANGUAGE_INLINE_TRANSLATION_MARK, 1)[0] for line in lines]
    return "\n".join(lines)


_CREDIT_LINE = re.compile(r"^(?:%s)\s*(?:\(s\))?\s*[:\-–]" % "|".join(
    re.escape(word) for word in sorted(LANGUAGE_CREDIT_WORDS, key=len, reverse=True)))


def _detect(text: str, codes=None) -> tuple[str, float] | None:
    """langdetect's best code of `codes` (any when None) and its probability."""
    from langdetect import DetectorFactory, LangDetectException, detect_langs
    DetectorFactory.seed = 0  # the same text, the same answer
    try:
        found = [(f.lang.split("-")[0], f.prob) for f in detect_langs(text)]
    except LangDetectException:
        return None
    found = [(code, prob) for code, prob in found if codes is None or code in codes]
    return found[0] if found else None


def _read_latin(text: str) -> tuple[str, float] | None:
    """Letters first: ŭ is Belarusian (Lacinka); ś beside č / š / ž is
    Belarusian (with ł) or Montenegrin (with đ) - langdetect knows neither.
    Only when č / š / ž come several times and there's no ą / ę / ż: a Polish
    text may hold a name with one ("Taco Hemingway - Italodisco")."""
    lower = text.lower()
    letters = set(lower)
    if "ŭ" in letters:
        return "Belarusian", 1.0
    found = _detect(text)
    marked = _marked_language(lower, code_name(found[0]) if found else None)
    if marked:
        return marked
    slavic = sum(lower.count(ch) for ch in "čšž") >= _LACINKA_MIN_LETTERS and not letters & set("ąęż")
    if "ś" in letters and slavic:
        if "ł" in letters:
            return "Belarusian", 1.0
        if "đ" in letters:
            return "Montenegrin", 1.0
        return (code_name(found[0]) or found[0], min(found[1], 0.5)) if found else None
    if found is None:
        return None
    name = code_name(found[0])
    if name == "Vietnamese" and not letters & _VIETNAMESE_LETTERS:
        return name, min(found[1], 0.5)
    return (name, found[1]) if name else None


def _marked_language(lower: str, reading: str | None) -> tuple[str, float] | None:
    """A language langdetect doesn't know, read as its neighbour (LANGUAGE_MARKS)."""
    for language, (neighbour, marks, needed, sure) in LANGUAGE_MARKS.items():
        if neighbour is not None and reading != neighbour:
            continue
        if isinstance(marks, str):
            count = sum(lower.count(ch) for ch in marks)
        elif isinstance(marks, re.Pattern):
            count = len(marks.findall(lower))
        else:
            count = _word_count(lower, marks)
        if count >= needed:
            return language, sure
    return None


def _word_count(lower: str, words) -> int:
    return sum(word in words for word in re.findall(r"\w+", lower))


def _proven_by_words(language: str, text: str) -> bool:
    """Does `text` hold enough of LANGUAGE_PROOF_WORDS' words of `language` (any language not listed does)?"""
    words, needed = LANGUAGE_PROOF_WORDS.get(language, ((), 0))
    return _word_count(text.lower(), words) >= needed


def _read_script(script: str, text: str) -> tuple[str, float] | None:
    """The language of the part of a text in one script."""
    if script == "LATIN":
        return _read_latin(text)
    if script == "CYRILLIC":
        letters = set(text.lower())
        if letters & set("өү"):  # Kazakh has them too, with ә ғ қ ң
            return ("Kazakh" if letters & set("әғқң") else "Mongolian"), 1.0
        if letters & set("ѓќѕ"):
            return "Macedonian", 1.0
        found = _detect(text, _SCRIPT_CODES["CYRILLIC"])
        code = cyrillic_language(text, None, None)  # the letters only one alphabet has
        if code == "sr" and found is not None and found[0] == "mk" and not letters & set("ђћ"):
            return "Macedonian", found[1]  # ј љ њ џ: Serbian and Macedonian alike
        if found is None or found[0] in ("ru", "uk") or code in ("be", "sr"):
            return _CYRILLIC_CODES[code], found[1] if found else 0.5
        return code_name(found[0]), found[1]
    if script == "HEBREW":
        return ("Yiddish" if any(mark in text for mark in YIDDISH_MARKS) else "Hebrew"), 1.0
    if script in _SCRIPT_LANGUAGES:
        return _SCRIPT_LANGUAGES[script], 1.0
    found = _detect(text, _SCRIPT_CODES.get(script))
    return (code_name(found[0]), found[1]) if found and code_name(found[0]) else None


def language_of_text(text: str | None) -> tuple[str, float] | None:
    """(language as stored, how sure) of a text (lyrics); None when it has
    too few letters, holds a translation or nothing can tell. Its alphabet
    narrows which languages it may be - each script with
    LANGUAGE_SECOND_SCRIPT_SHARE of the letters is read on its own, the most
    written first ("Korean / English"); kana beside kanji is Japanese. Each
    line counts once, and a Latin or Cyrillic part is also read stanza by
    stanza (_stanza_readings()). The probability is the main script's."""
    text = _LATIN_I_IN_CYRILLIC.sub("і", _lyrics_only(text or ""))
    if looks_like_translation(text):
        return None
    stanzas = _stanzas(text)
    unique = "\n".join(line for stanza in stanzas for line in stanza)
    counts = _letter_counts(unique)
    sure = True
    if sum(counts.values()) < LANGUAGE_DETECT_MIN_LETTERS:  # a few lines sung over and over: never sure
        counts, unique, stanzas, sure = _letter_counts(text), text, [], False
    total = sum(counts.values())
    if total < LANGUAGE_DETECT_MIN_LETTERS:
        return None
    text = unique
    readings = []
    for script, count in sorted(counts.items(), key=lambda item: -item[1]):
        if count / total < LANGUAGE_SECOND_SCRIPT_SHARE and readings:
            break
        reading = _read_script(script, _in_script(text, script))
        if reading:
            readings += _stanza_readings(script, reading, stanzas)
    if not readings:
        return None
    sure = sure and _proven_by_words(readings[0][0], text)
    probability = readings[0][1] if sure else min(readings[0][1], 0.5)
    return clean_language(LANGUAGE_SEPARATOR.join(name for name, _ in readings)), round(probability, 3)


# Ukrainian / Belarusian typed with a Latin "i" (SUNSAY's "свое iм'я")
_LATIN_I_IN_CYRILLIC = re.compile(r"(?<=[\u0400-\u04ff'’])[iI]|[iI](?=[\u0400-\u04ff])")


def _letter_counts(text: str) -> dict:
    """{script: letters}, a syllable counting as two; kanji go with kana."""
    counts: dict = {}
    for ch in text:
        script = _script(ch)
        if script:
            counts[script] = counts.get(script, 0) + (2 if script in _SYLLABIC else 1)
    if "KANA" in counts:  # kanji are Japanese too
        counts["KANA"] += counts.pop("CJK", 0)
    return counts


def _in_script(text: str, script: str) -> str:
    """`text` with the letters of other scripts blanked (kanji kept beside kana)."""
    return "".join(ch if _script(ch) in (script, None) or (script == "KANA" and _script(ch) == "CJK")
                   else " " for ch in text)


def _stanzas(text: str) -> list[list[str]]:
    """The text's stanzas (blank lines between; LANGUAGE_STANZA_LINES lines a
    piece when it has none), each line only where it first comes."""
    stanzas, stanza, seen = [], [], set()
    for line in text.splitlines():
        key = " ".join(line.lower().split())
        if not key:
            if stanza:
                stanzas.append(stanza)
            stanza = []
        elif key not in seen:
            seen.add(key)
            stanza.append(line.strip())
    if stanza:
        stanzas.append(stanza)
    if len(stanzas) == 1:
        lines = stanzas[0]
        stanzas = [lines[i:i + LANGUAGE_STANZA_LINES] for i in range(0, len(lines), LANGUAGE_STANZA_LINES)]
    return stanzas


def _stanza_readings(script: str, reading: tuple[str, float], stanzas) -> list[tuple[str, float]]:
    """`reading` of a script's part, checked against its stanzas' votes (their
    letters): a language most of them are in replaces it - not sure then -, a
    second one with LANGUAGE_SECOND_SCRIPT_SHARE of them is added. Not for a
    language told by its letters alone (LANGUAGE_MARKS), whose stanzas may lack them."""
    if script not in ("LATIN", "CYRILLIC") or reading[0] in LANGUAGE_MARKS:
        return [reading]
    votes: dict = {}
    for stanza in stanzas:
        part = _in_script("\n".join(stanza), script)
        letters = sum(1 for ch in part if _script(ch) == script)
        if letters < LANGUAGE_STANZA_MIN_LETTERS:
            continue
        found = _read_script(script, part)
        if not found or not found[0] or found[1] < LANGUAGE_STANZA_SURE or not _proven(script, found[0], part):
            continue
        language = reading[0] if _neighbours(found[0], reading[0]) else found[0]
        votes[language] = votes.get(language, 0) + letters
    total = sum(votes.values())
    if not total:
        return [reading]
    top = max(votes, key=votes.get)
    main = reading if top == reading[0] or votes[top] * 2 <= total else (top, min(reading[1], 0.5))
    return [main] + [(language, main[1]) for language, letters in sorted(votes.items(), key=lambda item: -item[1])
                     if language != main[0] and letters / total >= LANGUAGE_SECOND_SCRIPT_SHARE]


def _neighbours(language: str, other: str) -> bool:
    return any(language in group and other in group for group in LANGUAGE_NEIGHBOURS)


# The letter sets a Cyrillic stanza must show to vote for its language: a short Belarusian
# stanza without і or ў ("Гэта мой колер, не выходзь з дому") reads as Russian
_CYRILLIC_PROOF = {
    "Belarusian": lambda letters: "ў" in letters or ("і" in letters and bool(letters & set("ыэ"))),
    "Ukrainian": lambda letters: bool(letters & set("їєґ")) or ("і" in letters and "и" in letters
                                                                 and not letters & set("ыэ")),
    "Russian": lambda letters: bool(letters & set("ыэъё")) and bool(letters & set("иъщ"))
    and not letters & set("іўїє"),
}


def _proven(script: str, language: str, text: str) -> bool:
    """May a stanza in `script` read as `language` vote? A Cyrillic one only
    with the letters only that language has (_CYRILLIC_PROOF); other Cyrillic
    languages never vote."""
    if script != "CYRILLIC":
        return True
    proof = _CYRILLIC_PROOF.get(language)
    return bool(proof and proof(set(text.lower())))


_TRANSLATION_HEAD = re.compile(r"[(\[](?:%s)[)\]]|^(?:%s)\s*:" % (("|".join(map(re.escape, LANGUAGE_TRANSLATION_WORDS)),) * 2))


def looks_like_translation(text: str | None) -> bool:
    """Lyrics that are a translation or hold one line by line: a translation
    word in brackets or before ":" in the first lines ("Ева (перевод)"), or
    LANGUAGE_TRANSLATION_ALTERNATION of the lines changing script - in Latin,
    plain ASCII against accented letters (English under Vietnamese)."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if any(_TRANSLATION_HEAD.search(line.lower()) for line in lines[:LANGUAGE_TRANSLATION_HEAD_LINES]):
        return True
    kinds = [kind for kind in map(_line_kind, lines) if kind]
    if len(kinds) < LANGUAGE_TRANSLATION_MIN_LINES or len(set(kinds)) != 2:
        return False
    changes = sum(a != b for a, b in zip(kinds, kinds[1:]))
    return changes / (len(kinds) - 1) >= LANGUAGE_TRANSLATION_ALTERNATION


def _line_kind(line: str) -> str | None:
    """A line's main script (kanji counted as kana); a Latin line with
    accented letters is "LATIN+"."""
    counts: dict = {}
    for ch in line:
        script = _script(ch)
        if script:
            script = "KANA" if script == "CJK" else script
            counts[script] = counts.get(script, 0) + 1
    if not counts:
        return None
    script = max(counts, key=counts.get)
    return "LATIN+" if script == "LATIN" and any(ch.isalpha() and not ch.isascii() for ch in line) else script


_ROMAJI_WORD = re.compile(r"(?:(?:[kstpc](?=[kstpc]))?(?:ky|gy|sh|ch|ts|ny|hy|my|ry|by|py|[kgszjtdnhbpmrwyf])?[aiueo]"
                          r"|n)+")


def _romaji_share(text: str) -> float:
    words = [word for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 1]
    return sum(bool(_ROMAJI_WORD.fullmatch(word)) for word in words) / len(words) if words else 0.0


# The languages each script writes - a title wholly in one, with lyrics read as another
# language, may hold a translation (LRCLIB's and Genius's English of Ado's "ビバリウム")
_SCRIPT_WRITES = {
    "CYRILLIC": {"Russian", "Ukrainian", "Belarusian", "Bulgarian", "Macedonian", "Serbian", "Mongolian", "Kazakh",
                 "Church Slavonic", "Ruthenian", "Montenegrin", SERBO_CROATIAN, "Tuvan", "Kyrgyz", "Tatar"},
    "KANA": {"Japanese"}, "CJK": {"Japanese", "Chinese", "Cantonese"}, "HANGUL": {"Korean"},
    "ARABIC": {"Arabic", "Persian", "Urdu", "Kashmiri", "Berber"}, "HEBREW": {"Hebrew", "Yiddish"},
    "GREEK": {"Greek", "Attic Greek"}, "DEVANAGARI": {"Hindi", "Marathi", "Nepali", "Sanskrit"},
    "GEORGIAN": {"Georgian"}, "ARMENIAN": {"Armenian"}, "THAI": {"Thai"},
}


# Cyrillic letters a title in that language can't have (Сергій Бабкін - "ДАЛІ МИ САМІ" isn't Russian)
_CYRILLIC_FOREIGN = {"Russian": set("іїєґў"), "Ukrainian": set("ыэъёў"), "Belarusian": set("иїєґщъ")}


def may_be_translation(title: str | None, language: str | None) -> bool:
    """A title written wholly in a script none of `language`'s parts is
    written in, or in Cyrillic letters none of them has: the lyrics read
    may be a translation of the song."""
    scripts = {_script(ch) for ch in title or ""} - {None}
    if not scripts or "LATIN" in scripts or not language:
        return False
    parts = set(language.split(LANGUAGE_SEPARATOR))
    written = set().union(*(_SCRIPT_WRITES.get(script, set()) for script in scripts))
    if written and not written & parts:
        return True
    letters = set(title.lower())
    return all(letters & _CYRILLIC_FOREIGN.get(part, set()) for part in parts)


def written_otherwise(text: str | None, language: str | None) -> bool:
    """A text mostly (LYRICS_OTHER_SCRIPT_SHARE of its letters) in a script none
    of `language`'s parts is written in: Cyrillic lyrics of a Polish song, likely
    another song's. Never Latin - romanized lyrics are common."""
    if not text or language in (None, "", LANGUAGE_UNKNOWN, LANGUAGE_INSTRUMENTAL):
        return False
    counts = {}
    for script in filter(None, map(_script, text)):
        counts[script] = counts.get(script, 0) + 1
    if "KANA" in counts:  # kanji are Japanese too
        counts["KANA"] += counts.pop("CJK", 0)
    if not counts:
        return False
    script, count = max(counts.items(), key=lambda item: item[1])
    if script == "LATIN" or script not in _SCRIPT_WRITES or count / sum(counts.values()) <= LYRICS_OTHER_SCRIPT_SHARE:
        return False
    return not _SCRIPT_WRITES[script] & set(language.split(LANGUAGE_SEPARATOR))


_YUGOSLAV_READINGS = {"Croatian", "Serbian", "Bosnian", "Montenegrin", SERBO_CROATIAN}
_COUNTRY_OF_LANGUAGE = {"Croatian": "Croatia", "Bosnian": "Bosnia", "Serbian": "Serbia", "Montenegrin": "Montenegro"}


def is_yugoslav(language: str | None) -> bool:
    return language in _YUGOSLAV_READINGS


def yugoslav_language(text: str, year: int | None, origin: str | None, artist_languages) -> str | None:
    """The owner's rule for a Serbian / Croatian / Bosnian reading (YUGOSLAV_COUNTRIES);
    None when neither the artist's country nor the year tells - the owner waits
    until the artist's origin is known rather than guess (2026-11-19)."""
    lower = (text or "").lower()
    if set(lower) & set("śź") or "с\u0301" in lower or "з\u0301" in lower:
        return "Montenegrin"
    origin = clean_origin(origin) or ""
    country = next((key for key in YUGOSLAV_COUNTRIES if origin.startswith(key)), None)
    if country is None:
        others = [_COUNTRY_OF_LANGUAGE[part] for value in artist_languages
                  for part in value.split(LANGUAGE_SEPARATOR) if part in _COUNTRY_OF_LANGUAGE]
        country = max(set(others), key=others.count) if others else None
    if country is None and set(re.findall(r"\w+", lower)) & set(SERBIAN_EKAVIAN_WORDS):
        country = "Serbia"
    if country is None:
        return SERBO_CROATIAN if year and year <= YUGOSLAVIA_LAST_YEAR else None
    language, independent_from = YUGOSLAV_COUNTRIES[country]
    return SERBO_CROATIAN if year and year < independent_from else language


def song_language(text: str | None, title: str = "", artist: str = "", origin: str | None = None,
                  artist_languages=(), year: int | None = None) -> tuple[str, float, bool] | None:
    """language_of_text() with what the song tells (the owner's rules,
    2026-11-19): a title or artist in kana / kanji, or an artist singing
    mostly Japanese, makes a romaji misreading Japanese; a Serbian / Croatian /
    Bosnian reading goes by the song's year and the artist's country
    (yugoslav_language(); `artist_languages` = the stored values of its other
    songs) - the third value True when they can't tell, so it needs the
    artist's origin; a reading in a rarely right language, or one the title's
    script doesn't write (may_be_translation()), is never sure."""
    reading = language_of_text(text)
    if reading is None:
        return None
    language, probability = reading
    names = f"{title} {artist}"
    japanese = [value for value in artist_languages if "Japanese" in value.split(LANGUAGE_SEPARATOR)]
    if language in LANGUAGE_ROMAJI_MISREADINGS and (any(_script(ch) in ("KANA", "CJK") for ch in names)
                                                    or len(japanese) * 2 > len(artist_languages) > 0):
        return "Japanese", probability, False
    if language in LANGUAGE_ROMAJI_ONLY_MISREADINGS and _romaji_share(text) >= LANGUAGE_ROMAJI_SHARE:
        return "Japanese", min(probability, 0.5), False
    if language == "Slovenian" and (clean_origin(origin) or "").startswith("Croatia"):
        language, probability = KAJKAVIAN, min(probability, 0.5)
    elif language == "Slovenian" and not _proven_by_words(language, _lyrics_only(text)):
        language = SERBO_CROATIAN  # Serbian read as Slovenian (Milan Stanković - Ovo je Balkan)
    needs_origin = False
    if is_yugoslav(language):
        decided = yugoslav_language(text, year, origin, artist_languages)
        language, needs_origin = decided or language, decided is None
    if language in LANGUAGE_RARELY_RIGHT or may_be_translation(title, language):
        probability = min(probability, 0.5)
    return language, probability, needs_origin
