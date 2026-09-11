"""Script transliteration for the alternate-alphabet YouTube search retry.

Some songs in the DB are only findable on YouTube under a title written in a
different script than the DB's `Song.title`/`Song.language` combination
suggests — an uploader may have romanized a native-script title, or vice
versa. Rather than hand-maintaining a synonym per song (see `Artist.synonyms`
in datatables.py), this generates the *other* script's spelling on the fly
for languages registered in TRANSLITERABLE_LANGUAGES, so a second search
attempt can be made with it. See manage_youtube_playlists.py for where the
retry is actually triggered.

Each language config exposes:
  - `native_script`: regex matching a character only found in that
    language's native (non-Latin) script — used to tell which direction to
    transliterate a given title in.
  - `to_latin` / `to_native`: the transliteration functions. `to_native` is
    None for languages that only make sense to transliterate one way (e.g.
    Japanese: romaji -> kana/kanji isn't well-defined, so only the
    native-script -> Latin direction is offered).
"""
import re
from dataclasses import dataclass
from typing import Callable, Optional

# ---------------------------------------------------------
# Belarusian (Cyrillic <-> Lacinka)
# ---------------------------------------------------------
# Follows the context-sensitive letter tables at
# https://pl.wikipedia.org/wiki/Transliteracja_tekstu_z_cyrylicy_na_łacinkę
# (Belarusian Instruction-2000-style Lacinka). Verified round-trip against
# "Адзіноцтва" <-> "Adzinoctva" and "Іголкі" <-> "Ihołki".

_BE_PLAIN_VOWELS = {"а": "a", "э": "e", "і": "i", "о": "o", "у": "u", "ы": "y"}
_BE_I_AFTER_SOFT = "ji"

# я/е/ё/ю: (word-start or after a vowel, after a consonant, after a soft
# sign/apostrophe, after a soft "л")
_BE_IOTATED_VOWELS = {
    "я": ("ja", "ia", "ja", "a"),
    "е": ("je", "ie", "je", "e"),
    "ё": ("jo", "io", "jo", "o"),
    "ю": ("ju", "iu", "ju", "u"),
}

_BE_CONSONANTS_NORMAL = {
    "б": "b", "в": "v", "г": "h", "д": "d", "ж": "ž", "з": "z", "й": "j",
    "к": "k", "л": "ł", "м": "m", "н": "n", "п": "p", "р": "r", "с": "s",
    "т": "t", "ў": "ŭ", "ф": "f", "х": "ch", "ц": "c", "ч": "č", "ш": "š",
}
# Only the letters whose soft form actually differs from the normal one.
# Softening is triggered by a following ь for all of these. `л` is the one
# exception that *also* softens before a bare я/е/ё/ю with no ь — unlike the
# others, Belarusian л has a genuinely obligatory, always-marked hard/soft
# split (ł vs l), so a following iotated vowel palatalizes it directly and
# reduces to its bare form in turn (see _BE_IOTATED_VOWELS' "after_l" slot,
# e.g. "поле" -> "pole", not "połe"). н/с/ц/ж/ч/ш soften only before an
# explicit ь — the iotated vowel's own "i-" glide (the "after_cons" slot,
# e.g. "ie") already carries the palatalization signal on its own, so the
# consonant itself stays in its normal form. Real case: "некаторымі" ->
# "niekatorymi", not "ńiekatorymi" — no ь anywhere, so н stays plain "n" and
# "е" alone renders "ie". See _be_soft_consonant_flags() for the further
# wrinkle: a consonant *earlier* in the same cluster (not directly touching
# the trigger) does mark itself soft.
_BE_CONSONANTS_SOFT = {
    "ж": "ż", "л": "l", "н": "ń", "с": "ś", "ц": "ć", "ч": "cz", "ш": "sz",
}

_BE_VOWELS = set(_BE_PLAIN_VOWELS) | set(_BE_IOTATED_VOWELS)

_BE_CYRILLIC_RE = re.compile(r"[а-яёіўʼ]", re.IGNORECASE)


def _be_soft_consonant_flags(lowered: list[str]) -> list[bool]:
    """For each position, whether the Belarusian consonant there (if any)
    renders in its soft Latin form (_BE_CONSONANTS_SOFT) rather than the
    normal one.

    Softening is a regressive assimilation that propagates backward through
    a whole run of consecutive consonants, triggered by я/е/ё/ю, і, or ь
    immediately after the run — but only the consonants that *don't*
    directly touch the trigger mark themselves soft:

    - Before я/е/ё/ю or і: the vowel's own spelling already carries the
      palatalization for the consonant directly touching it (see
      _BE_CONSONANTS_SOFT's docstring), so that one stays plain — but any
      *earlier* consonant in the same run has no such vowel spelling to
      lean on, so it must mark itself. Real cases: "сне" -> "śnie" (с, not
      touching "е", marks itself; н, touching it, stays plain), "смерць" ->
      "śmierć" (с marks itself two letters back from "е", across the
      unmarked "m"), "большасці" -> "bolšaści" (і triggers the same
      cascade as я/е/ё/ю; ц, touching it, stays plain "c").
    - Before ь: ь has no spelling of its own to carry anything, so the
      consonant directly touching it must mark itself soft too — same
      cascade rule, just with the final member included instead of
      excluded.
    - Before a plain vowel (а/э/о/у/ы), a hard apostrophe, or nothing (end
      of string/other text): no assimilation at all — a hard apostrophe
      exists specifically to keep the preceding consonant hard (real case:
      "з'ява", not "źjava"), so it blocks the cascade rather than
      triggering it the way ь does.

    A same-letter cluster is not special-cased: "жыццё" -> "žyćcio" falls
    out of the same rule (a run of two ц before ё — only the first, not
    touching the vowel, marks itself: "ć" then plain "c").

    л is excluded from all of the above and handled separately at the end:
    unlike н/с/ц/ж/ч/ш (whose Latin form only ever changes at the very
    letter that's directly softened — see _BE_CONSONANTS_SOFT's docstring),
    Belarusian л has an obligatory, always-marked hard/soft split (ł vs l)
    that's local to each л itself, not something that cascades in from
    elsewhere in its cluster. Real case: "Іголкі" -> "Ihołki" — л sits in
    the "лк" cluster, which (like "сн" in "сне") ends right before "і", but
    л stays hard because ITS OWN immediate next letter is к, a consonant —
    the cascade that would soften a leading с or н in the same position
    does not apply to л.
    """
    n = len(lowered)
    soft = [False] * n
    i = 0
    while i < n:
        if lowered[i] not in _BE_CONSONANTS_NORMAL:
            i += 1
            continue
        start = i
        while i < n and lowered[i] in _BE_CONSONANTS_NORMAL:
            i += 1
        end = i  # [start, end) is the maximal consonant run
        trigger = lowered[end] if end < n else ""
        if trigger == "ь":
            for pos in range(start, end):
                if lowered[pos] != "л":
                    soft[pos] = lowered[pos] in _BE_CONSONANTS_SOFT
        elif trigger in _BE_IOTATED_VOWELS or trigger == "і":
            for pos in range(start, end - 1):
                if lowered[pos] != "л":
                    soft[pos] = lowered[pos] in _BE_CONSONANTS_SOFT
        # else: plain vowel, hard apostrophe, or end of string/other text
        # — no assimilation at all, every consonant in the run stays plain.

        # л's own rule, independent of cluster/cascade: soft only when ITS
        # immediate next character is ь or a bare iotated vowel.
        for pos in range(start, end):
            if lowered[pos] == "л":
                nxt = lowered[pos + 1] if pos + 1 < n else ""
                soft[pos] = nxt == "ь" or nxt in _BE_IOTATED_VOWELS
    return soft


def belarusian_to_latin(text: str) -> str:
    lowered = [ch.lower() for ch in text]
    soft_flags = _be_soft_consonant_flags(lowered)
    result = []
    prev_cyr: Optional[str] = None  # lowercased previous Cyrillic letter, or None at a word boundary
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        lower = lowered[i]
        is_upper = ch.isupper()

        if lower == "ь":
            prev_cyr = "ь"
            i += 1
            continue
        if lower in ("'", "ʼ", "’"):
            # Same vowel-rendering context as ь (both give the full
            # ja/je/jo/ju glide) but *not* the same consonant-softening
            # context — see _be_soft_consonant_flags()'s apostrophe case.
            prev_cyr = "ь"
            i += 1
            continue

        if lower in _BE_PLAIN_VOWELS:
            out = _BE_I_AFTER_SOFT if (lower == "і" and prev_cyr == "ь") else _BE_PLAIN_VOWELS[lower]
            result.append(out.capitalize() if is_upper else out)
            prev_cyr = lower
            i += 1
            continue

        if lower in _BE_IOTATED_VOWELS:
            start_or_vowel, after_cons, after_soft, after_l = _BE_IOTATED_VOWELS[lower]
            if prev_cyr == "ь":
                out = after_soft
            elif prev_cyr == "л":
                out = after_l
            elif prev_cyr is None or prev_cyr in _BE_VOWELS:
                out = start_or_vowel
            else:
                out = after_cons
            result.append(out.capitalize() if is_upper else out)
            prev_cyr = lower
            i += 1
            continue

        if lower in _BE_CONSONANTS_NORMAL:
            out = _BE_CONSONANTS_SOFT[lower] if soft_flags[i] else _BE_CONSONANTS_NORMAL[lower]
            result.append(out.capitalize() if is_upper else out)
            prev_cyr = lower
            i += 1
            continue

        # Not a Belarusian letter — pass through, and reset word-boundary
        # context only if it's not a letter at all (keeps mid-word foreign
        # characters from being treated as a fresh word start).
        result.append(ch)
        prev_cyr = lower if lower.isalpha() else None
        i += 1

    return "".join(result)


_BE_LATIN_IOTATED = {
    "ja": "я", "ia": "я", "ji": "і",
    "je": "е", "ie": "е",
    "jo": "ё", "io": "ё",
    "ju": "ю", "iu": "ю",
}
_BE_LATIN_DIGRAPHS = {"ch": "х", "cz": "ч", "sz": "ш"}
# cz/sz are the *soft* forms of ч/ш — see _BE_LATIN_SOFT_MARKERS below for
# when they reinsert ь. "ch" (х) has no hard/soft distinction at all — it's
# never followed by ь on the reverse side.
_BE_LATIN_SOFT_DIGRAPHS = {"cz", "sz"}
_BE_LATIN_SINGLE = {
    "a": "а", "e": "э", "i": "і", "o": "о", "u": "у", "y": "ы",
    "b": "б", "v": "в", "h": "г", "d": "д", "z": "з", "j": "й", "k": "к",
    "m": "м", "p": "п", "r": "р", "t": "т", "f": "ф",
    "n": "н", "s": "с", "c": "ц",
    "ž": "ж", "š": "ш", "č": "ч", "ŭ": "ў",
}
# ń/ś/ć/ż are the soft forms of н/с/ц/ж. Whether they reinsert ь on reversal
# is NOT simply "always" — real case: "śnie" (-> "сне", no ь) vs an isolated
# word-final "ń" (-> "нь", ь reinserted). "ś" here doesn't come from a real
# ь at all — it's с regressively assimilating the softness of the following
# (itself soft) н, a cluster effect the forward direction doesn't model —
# while a soft marker with *nothing* after it (word-final, or before
# punctuation) can only have come from a real ь, since assimilation needs a
# following consonant to assimilate with. So: reinsert ь only when the soft
# marker isn't immediately followed by another letter at all; before any
# letter (vowel *or* consonant) it stays bare.
_BE_LATIN_SOFT_SINGLE = {"ń": "н", "ś": "с", "ć": "ц", "ż": "ж"}
_BE_LATIN_L = {"ł": "л", "l": "л"}
# Bare a/e/o/u right after a *soft* "l" only ever came from я/е/ё/ю (see the
# "Po spółgłosce ł" column) — plain а/э/о/у only ever pair with hard "ł".
_BE_AFTER_SOFT_L = {"a": "я", "e": "е", "o": "ё", "u": "ю"}
# "l" is the same "reinsert ь only at a genuine word boundary" case as
# ń/ś/ć/ż/cz/sz above — "l" before another consonant (e.g. "śpiewał"-style
# clusters) stays bare too, same as "ś" before "n" in "śnie" — *except* when
# followed by a vowel-starting character, where it's not a word boundary at
# all: that's the ordinary "l" + iotated-vowel-reduction case (see
# _BE_AFTER_SOFT_L), which needs no ь either but for a different reason (the
# vowel itself already carries the softness). Real cases: "ля" = "la" (l +
# vowel, reduction) vs "ль" = "l" alone at a word boundary (ь reinserted) vs
# "l" + consonant (bare, no ь — per the same logic as "śnie").
_BE_LATIN_VOWEL_STARTS = set("aeiouyj")


def _is_letter(ch: str) -> bool:
    return bool(ch) and ch.isalpha()


def latin_to_belarusian(text: str) -> str:
    result = []
    prev_soft_l = False
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2].lower()
        one = text[i].lower()
        is_upper = text[i].isupper()

        if two in _BE_LATIN_IOTATED:
            cyr = _BE_LATIN_IOTATED[two]
            result.append(cyr.upper() if is_upper else cyr)
            prev_soft_l = False
            i += 2
            continue
        if two in _BE_LATIN_DIGRAPHS:
            cyr = _BE_LATIN_DIGRAPHS[two]
            result.append(cyr.upper() if is_upper else cyr)
            if two in _BE_LATIN_SOFT_DIGRAPHS and not _is_letter(text[i + 2:i + 3].lower()):
                result.append("ь")
            prev_soft_l = False
            i += 2
            continue
        if one in _BE_LATIN_L:
            cyr = _BE_LATIN_L[one]
            result.append(cyr.upper() if is_upper else cyr)
            if one == "l":
                next_char = text[i + 1].lower() if i + 1 < n else ""
                if next_char in _BE_LATIN_VOWEL_STARTS:
                    prev_soft_l = True
                elif not _is_letter(next_char):
                    result.append("ь")
                    prev_soft_l = False
                else:
                    prev_soft_l = False  # before another consonant — bare, no ь (cluster case)
            else:
                prev_soft_l = False
            i += 1
            continue
        if prev_soft_l and one in _BE_AFTER_SOFT_L:
            cyr = _BE_AFTER_SOFT_L[one]
            result.append(cyr.upper() if is_upper else cyr)
            prev_soft_l = False
            i += 1
            continue
        if one in _BE_LATIN_SOFT_SINGLE:
            cyr = _BE_LATIN_SOFT_SINGLE[one]
            result.append(cyr.upper() if is_upper else cyr)
            next_char = text[i + 1].lower() if i + 1 < n else ""
            if not _is_letter(next_char):
                result.append("ь")
            prev_soft_l = False
            i += 1
            continue
        if one in _BE_LATIN_SINGLE:
            cyr = _BE_LATIN_SINGLE[one]
            result.append(cyr.upper() if is_upper else cyr)
            prev_soft_l = False
            i += 1
            continue

        result.append(text[i])
        prev_soft_l = False
        i += 1

    return "".join(result)


# ---------------------------------------------------------
# Language registry
# ---------------------------------------------------------

@dataclass(frozen=True)
class TransliterableLanguage:
    native_script: re.Pattern
    to_latin: Callable[[str], str]
    to_native: Optional[Callable[[str], str]]


TRANSLITERABLE_LANGUAGES: dict[str, TransliterableLanguage] = {
    "belarusian": TransliterableLanguage(
        native_script=_BE_CYRILLIC_RE,
        to_latin=belarusian_to_latin,
        to_native=latin_to_belarusian,
    ),
}


def is_transliterable(language: str | None) -> bool:
    return bool(language) and language.strip().lower() in TRANSLITERABLE_LANGUAGES


def transliterate(title: str, language: str | None) -> str | None:
    """Return `title` rewritten in the *other* script for a transliterable
    language, or None if the language isn't registered or the direction
    isn't supported (e.g. a native-script -> Latin-only language given
    Latin text).
    """
    if not is_transliterable(language):
        return None
    config = TRANSLITERABLE_LANGUAGES[language.strip().lower()]
    if config.native_script.search(title):
        return config.to_latin(title)
    if config.to_native is not None:
        return config.to_native(title)
    return None
