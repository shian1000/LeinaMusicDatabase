"""Writing-system helpers for matching fetched metadata across alphabets.

The DB sometimes holds a Latin transliteration ("Tam, de ty ye", "Neformat")
of a title the metadata sources only know in its native script ("Там, де ти
є", "НєФормат"), or the other way round. Two narrow tools for that:

- `matching_key()` - a lossy, language-agnostic romanization of *both* sides
  of a comparison (Cyrillic incl. Ukrainian/Belarusian letters, Greek,
  Georgian, Korean Hangul, Japanese kana), so "Tam, de ty ye" and "Там де ти
  є" compare as near-equal. It's a key for similarity checks only - never
  store or display it.
- `cyrillic_search_variant()` - a rough Russian-style Latin -> Cyrillic
  spelling to retry a search with ("Neformat" -> "Неформат"); search engines
  tolerate the remaining differences ("НєФормат").

Kanji/hanzi and Arabic are left alone on purpose: they need dictionaries
(pykakasi, pypinyin) or vowel guessing, and no real case has needed them yet.
The precise Belarusian Cyrillic <-> Lacinka transliteration for YouTube
search lives separately in utils/youtube/transliteration.py.
"""
import unicodedata

from utils.common.normalizer import normalize

# ---- script detection ------------------------------------------------------

_SCRIPT_PREFIXES = {
    "LATIN": "LATIN",
    "CYRILLIC": "CYRILLIC",
    "GREEK": "GREEK",
    "GEORGIAN": "GEORGIAN",
    "HANGUL": "HANGUL",
    "HIRAGANA": "KANA",
    "KATAKANA": "KANA",
    "CJK": "CJK",
    "ARABIC": "ARABIC",
}


def _char_script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    return _SCRIPT_PREFIXES.get(name.split(" ")[0])


def scripts_in(text: str | None) -> set[str]:
    """The writing systems whose letters appear in `text`."""
    return {s for s in (_char_script(ch) for ch in (text or "")) if s}


def has_non_latin(text: str | None) -> bool:
    return bool(scripts_in(text) - {"LATIN"})


# ---- romanization for matching -------------------------------------------

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "є": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ў": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "u", "я": "a", "ʼ": "", "'": "",
}

_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "i", "φ": "f", "χ": "h", "ψ": "ps",
    "ω": "o",
}

_GEORGIAN = {
    "ა": "a", "ბ": "b", "გ": "g", "დ": "d", "ე": "e", "ვ": "v", "ზ": "z", "თ": "t",
    "ი": "i", "კ": "k", "ლ": "l", "მ": "m", "ნ": "n", "ო": "o", "პ": "p", "ჟ": "zh",
    "რ": "r", "ს": "s", "ტ": "t", "უ": "u", "ფ": "p", "ქ": "k", "ღ": "gh", "ყ": "q",
    "შ": "sh", "ჩ": "ch", "ც": "c", "ძ": "dz", "წ": "c", "ჭ": "ch", "ხ": "h", "ჯ": "j",
    "ჰ": "h",
}

# Hangul syllables decompose arithmetically (Unicode block U+AC00..U+D7A3)
_HANGUL_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj",
                    "ch", "k", "t", "p", "h"]
_HANGUL_VOWELS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo",
                  "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_HANGUL_FINALS = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l",
                  "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]


def _hangul(ch: str) -> str | None:
    code = ord(ch) - 0xAC00
    if not 0 <= code < 11172:
        return None
    initial, rest = divmod(code, 588)
    vowel, final = divmod(rest, 28)
    return _HANGUL_INITIALS[initial] + _HANGUL_VOWELS[vowel] + _HANGUL_FINALS[final]


# Hiragana; katakana is mapped onto it (same sounds, offset 0x60)
_KANA = dict(zip(
    "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
    "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽぁぃぅぇぉっゃゅょゎゔ",
    ["a", "i", "u", "e", "o", "ka", "ki", "ku", "ke", "ko", "sa", "shi", "su", "se", "so",
     "ta", "chi", "tsu", "te", "to", "na", "ni", "nu", "ne", "no", "ha", "hi", "fu", "he", "ho",
     "ma", "mi", "mu", "me", "mo", "ya", "yu", "yo", "ra", "ri", "ru", "re", "ro", "wa", "o", "n",
     "ga", "gi", "gu", "ge", "go", "za", "ji", "zu", "ze", "zo", "da", "ji", "zu", "de", "do",
     "ba", "bi", "bu", "be", "bo", "pa", "pi", "pu", "pe", "po", "a", "i", "u", "e", "o", "",
     "ya", "yu", "yo", "wa", "vu"],
))


def _kana(ch: str) -> str | None:
    if "ァ" <= ch <= "ヶ":  # katakana -> hiragana
        ch = chr(ord(ch) - 0x60)
    if ch == "ー":  # long-vowel mark: just drop it for matching
        return ""
    return _KANA.get(ch)


def _romanize_char(ch: str) -> str:
    low = ch.lower()
    for table in (_CYRILLIC, _GREEK, _GEORGIAN):
        if low in table:
            return table[low]
    converted = _kana(ch)
    return ch if converted is None else converted


def matching_key(text: str | None) -> str:
    """Lossy romanized form of `text` for similarity checks across scripts.

    Both sides of a comparison go through it, so it only has to be
    consistent, not linguistically right: the native side is romanized
    letter by letter, then both sides get the same folds (y/j -> i,
    kh -> h, ts -> c), since transliteration schemes disagree exactly there
    ("ty ye" vs "ти є")."""
    if not text:
        return ""
    # Hangul syllables first - NFKD would split them into jamo - then NFKD so
    # Greek/Cyrillic letters lose accents before the table lookup
    text = "".join(_hangul(ch) or ch for ch in text)
    decomposed = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    romanized = "".join(_romanize_char(ch) for ch in decomposed)
    key = normalize(romanized)
    for src, dst in (("kh", "h"), ("ts", "c"), ("y", "i"), ("j", "i")):
        key = key.replace(src, dst)
    return key


# ---- Latin -> Cyrillic for a search retry ---------------------------------

_LATIN_TO_CYRILLIC_MULTI = [
    ("shch", "щ"), ("zh", "ж"), ("kh", "х"), ("ch", "ч"), ("sh", "ш"), ("ts", "ц"),
    ("ya", "я"), ("yu", "ю"), ("ye", "е"), ("yo", "ё"), ("ja", "я"), ("ju", "ю"),
]
_LATIN_TO_CYRILLIC_SINGLE = {
    "a": "а", "b": "б", "c": "ц", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "z": "з",
}
_LATIN_VOWELS = set("aeiouy")


def latin_to_cyrillic(text: str) -> str:
    """Rough Russian-style Cyrillic spelling of a Latin transliteration.
    Only meant as a search query, never stored."""
    out = []
    lower = text.lower()
    i = 0
    while i < len(text):
        for latin, cyr in _LATIN_TO_CYRILLIC_MULTI:
            if lower.startswith(latin, i):
                out.append(cyr.upper() if text[i].isupper() else cyr)
                i += len(latin)
                break
        else:
            ch, low = text[i], lower[i]
            if low == "y":
                # after a consonant "y" is a vowel (ы), otherwise a glide (й)
                prev = lower[i - 1] if i else ""
                cyr = "ы" if prev.isalpha() and prev not in _LATIN_VOWELS else "й"
            else:
                cyr = _LATIN_TO_CYRILLIC_SINGLE.get(low)
            out.append(ch if cyr is None else (cyr.upper() if ch.isupper() else cyr))
            i += 1
    return "".join(out)


def cyrillic_search_variant(query: str, context_texts) -> str | None:
    """A Cyrillic spelling of a Latin-only `query` to retry a search with,
    when the rest of the entry (`context_texts`: its titles, artist names)
    is written in Cyrillic - a sign the query is a transliteration
    ("Neformat" next to the title "Восени"). None otherwise."""
    if not query or scripts_in(query) != {"LATIN"}:
        return None
    if not any("CYRILLIC" in scripts_in(t) for t in context_texts):
        return None
    variant = latin_to_cyrillic(query)
    return variant if variant != query else None
