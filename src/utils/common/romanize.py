"""Readable Latin forms of names in other alphabets, offered as suggestions
for the owner's Latin forms (utils/database/latin_names.py) - never stored
without the owner accepting or editing them.

Unlike script_utils.matching_key() (a lossy key for comparisons), this aims
at what a person would write: English-style romanization for Russian
("Алёна" -> "Alyona", "Жуки" -> "Zhuki"), the Ukrainian national system
("Океан Ельзи" -> "Okean Elzy"), MusicDatabase's own Lacinka for Belarusian
(utils/youtube/transliteration.py), Serbian/Macedonian Latin, Greek ELOT,
Georgian national, Korean Revised (without sound changes) and Hepburn for
kana. Kanji/hanzi, Arabic and other scripts have no automatic reading
here: suggest_latin() gives None and the owner types the form.
"""
import re
import unicodedata

from utils.common.script_utils import scripts_in
from utils.youtube.transliteration import belarusian_to_latin

# ---- Cyrillic ---------------------------------------------------------------

_RU = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
    # letters of other Cyrillic alphabets, should one turn up in Russian-ish text
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "w", "ө": "ö", "ү": "ü",
}

# Ukrainian national system (2010): the y- forms only at the start of a word
_UK = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu",
    "я": "ia", "'": "", "ʼ": "", "’": "",
}
_UK_WORD_START = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}

# Serbian/Macedonian Latin alphabet
_SR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e", "ж": "ž",
    "з": "z", "и": "i", "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n",
    "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "ć", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "č", "џ": "dž", "ш": "š", "ѓ": "gj", "ќ": "kj",
    "ѕ": "dz",
}

_RU_VOWELS = set("аеёиоуыэюяъь")
_CYRILLIC_LETTER = re.compile(r"[Ѐ-ӿ]")


def _russian(text: str) -> str:
    def convert(word: str) -> str:
        out, lower = [], word.lower()
        for i, ch in enumerate(word):
            low = lower[i]
            prev = lower[i - 1] if i else ""
            if low == "е" and (not prev or prev in _RU_VOWELS):
                latin = "ye"  # Елена -> Yelena, Моё -> Moyo
            elif low in "иы" and lower[i + 1:] == "й":
                latin = ""  # -ий / -ый at the end of a word: Чайковский -> Chaykovsky
            elif low == "й" and prev in "иы" and i == len(word) - 1:
                latin = "y"
            else:
                latin = _RU.get(low)
            out.append(_cased(ch, latin, word) if latin is not None else ch)
        return "".join(out)
    return _by_word(text, convert)


def _ukrainian(text: str) -> str:
    def convert(word: str) -> str:
        out, lower = [], word.lower()
        for i, ch in enumerate(word):
            low = lower[i]
            if low == "г" and i and lower[i - 1] == "з":
                latin = "gh"  # зг -> zgh, so it isn't read as "zh"
            elif not i or not lower[:i].strip("'ʼ’"):
                latin = _UK_WORD_START.get(low, _UK.get(low))
            else:
                latin = _UK.get(low)
            out.append(_cased(ch, latin, word) if latin is not None else ch)
        return "".join(out)
    return _by_word(text, convert)


def _serbian(text: str) -> str:
    return _by_word(text, lambda word: "".join(
        _cased(ch, _SR[ch.lower()], word) if ch.lower() in _SR else ch for ch in word))


# Spellings only Belarusian has: Russian writes жи/ши/чи/же/ше/че/це
_BELARUSIAN_PAIRS = ("жы", "шы", "чы", "жэ", "шэ", "чэ", "цэ")


def _cyrillic_language(text: str, language: str | None, origin: str | None) -> str:
    """"be" / "uk" / "sr" / "ru": the language field first, then letters
    only one alphabet has, then the artist's origin. Belarusian has і with
    ы/э/ё, Ukrainian і with и/щ/apostrophe; Russian has neither і nor ў."""
    language = (language or "").lower()
    for key, code in (("belarus", "be"), ("ukrain", "uk"), ("russia", "ru"), ("serbia", "sr"),
                      ("macedon", "sr"), ("bosnia", "sr"), ("montenegr", "sr")):
        if key in language:
            return code
    lower = text.lower()
    letters = set(lower)
    if "ў" in letters or any(pair in lower for pair in _BELARUSIAN_PAIRS):
        return "be"
    if letters & set("ђјљњћџѓќѕ"):
        return "sr"
    if letters & set("їєґ"):
        return "uk"
    if "і" in letters and letters & set("ыэё"):
        return "be"
    if "і" in letters and letters & set("ищ'ʼ’"):
        return "uk"
    origin = (origin or "").lower()
    for key, code in (("belarus", "be"), ("ukrain", "uk"), ("serbia", "sr"), ("macedon", "sr")):
        if origin.startswith(key):
            return code
    # і alone: Belarusian or Ukrainian - the library has far more Ukrainian
    return "uk" if "і" in letters else "ru"


def _cyrillic(text: str, language: str | None, origin: str | None) -> str:
    code = _cyrillic_language(text, language, origin)
    if code == "be":
        result = belarusian_to_latin(text)
        # The Belarusian alphabet lacks и, щ, ъ - a Russian name by a
        # Belarusian band: fall back to Russian for those words
        return _by_word(result, lambda w: _russian(w) if _CYRILLIC_LETTER.search(w) else w)
    if code == "uk":
        return _ukrainian(text)
    if code == "sr":
        return _serbian(text)
    return _russian(text)


# ---- Greek ------------------------------------------------------------------

_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o",
}
# Pairs read as one sound; (at the start of a word, elsewhere)
_GREEK_PAIRS = {
    "ου": ("ou", "ou"), "αι": ("ai", "ai"), "ει": ("ei", "ei"), "οι": ("oi", "oi"),
    "αυ": ("av", "av"), "ευ": ("ev", "ev"), "μπ": ("b", "mb"), "ντ": ("d", "nd"),
    "γκ": ("g", "ng"), "γγ": ("ng", "ng"),
}


def _greek(text: str) -> str:
    def convert(word: str) -> str:
        # accents and diaereses off, the case kept
        plain = "".join(c for c in unicodedata.normalize("NFD", word) if not unicodedata.combining(c))
        lower, out, i = plain.lower(), [], 0
        while i < len(plain):
            pair = _GREEK_PAIRS.get(lower[i:i + 2])
            if pair:
                out.append(_cased(plain[i], pair[0] if i == 0 else pair[1], plain))
                i += 2
                continue
            latin = _GREEK.get(lower[i])
            out.append(_cased(plain[i], latin, plain) if latin is not None else plain[i])
            i += 1
        return "".join(out)
    return _by_word(text, convert)


# ---- Georgian (national system, 2002) -------------------------------------

_GEORGIAN = {
    "ა": "a", "ბ": "b", "გ": "g", "დ": "d", "ე": "e", "ვ": "v", "ზ": "z", "თ": "t",
    "ი": "i", "კ": "k", "ლ": "l", "მ": "m", "ნ": "n", "ო": "o", "პ": "p", "ჟ": "zh",
    "რ": "r", "ს": "s", "ტ": "t", "უ": "u", "ფ": "p", "ქ": "k", "ღ": "gh", "ყ": "q",
    "შ": "sh", "ჩ": "ch", "ც": "ts", "ძ": "dz", "წ": "ts", "ჭ": "ch", "ხ": "kh", "ჯ": "j",
    "ჰ": "h",
}


# ---- Korean (Revised Romanization, letter by letter) ----------------------

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


# ---- Japanese kana (Hepburn) ----------------------------------------------

_KANA = dict(zip(
    "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわゐゑをん"
    "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ",
    ["a", "i", "u", "e", "o", "ka", "ki", "ku", "ke", "ko", "sa", "shi", "su", "se", "so",
     "ta", "chi", "tsu", "te", "to", "na", "ni", "nu", "ne", "no", "ha", "hi", "fu", "he", "ho",
     "ma", "mi", "mu", "me", "mo", "ya", "yu", "yo", "ra", "ri", "ru", "re", "ro", "wa", "i", "e",
     "o", "n", "ga", "gi", "gu", "ge", "go", "za", "ji", "zu", "ze", "zo", "da", "ji", "zu", "de",
     "do", "ba", "bi", "bu", "be", "bo", "pa", "pi", "pu", "pe", "po", "vu"],
))
_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_SMALL_VOWELS = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゎ": "a"}
_JAPANESE_PUNCTUATION = {"、": ", ", "。": ". ", "「": '"', "」": '"', "『": '"', "』": '"',
                         "・": " ", "〜": "~", "！": "!", "？": "?"}


def _kana(text: str) -> str:
    out = []
    double_next = False
    for ch in text:
        if "ァ" <= ch <= "ヶ":  # katakana -> hiragana
            ch = chr(ord(ch) - 0x60)
        if ch in ("っ",):
            double_next = True
            continue
        if ch == "ー":  # long vowel mark: left out, as in "ramen"
            continue
        if ch in _SMALL_Y and out and out[-1].endswith("i") and len(out[-1]) > 1:
            prev = out[-1][:-1]
            out[-1] = prev + ("" if prev in ("sh", "ch", "j") else "y") + _SMALL_Y[ch]
            continue
        if ch in _SMALL_VOWELS and out and out[-1][-1:] in "aiueo" and len(out[-1]) > 1:
            out[-1] = out[-1][:-1] + _SMALL_VOWELS[ch]  # ファ -> fa, ティ -> ti
            continue
        latin = _KANA.get(ch) or _SMALL_Y.get(ch) or _SMALL_VOWELS.get(ch)
        if latin is None:
            out.append(_JAPANESE_PUNCTUATION.get(ch, ch))
            double_next = False
            continue
        if double_next:
            latin = ("t" if latin.startswith("ch") else latin[0]) + latin
            double_next = False
        out.append(latin)
    return re.sub(r" {2,}", " ", "".join(out)).strip()


# ---- helpers ----------------------------------------------------------------

def _by_word(text: str, convert) -> str:
    return re.sub(r"[^\W\d_]+(?:['ʼ’][^\W\d_]+)*", lambda m: convert(m.group()), text)


def _cased(source: str, latin: str, word: str) -> str:
    """`latin` in the case of the letter it replaces: "Ж" -> "Zh", but
    "ZH" inside a word written in capitals ("ЖУКИ" -> "ZHUKI")."""
    if not source.isupper():
        return latin
    letters = [c for c in word if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return latin.upper()
    return latin[:1].upper() + latin[1:]


def _uncased(text: str, convert_char) -> str:
    """For scripts without capitals (Georgian, Hangul): letter by letter,
    then the first letter of the whole text capitalized."""
    result = "".join(convert_char(ch) for ch in text)
    return re.sub(r"[a-z]", lambda m: m.group().upper(), result, count=1)


def _only_latin_letters(text: str) -> bool:
    return all(not ch.isalpha() or unicodedata.name(ch, "").startswith("LATIN") for ch in text)


def suggest_latin(text: str | None, language: str | None = None, origin: str | None = None) -> str | None:
    """A Latin form to offer for `text`, or None: already Latin, or a script
    (kanji, Arabic, Hebrew, ...) with no automatic reading here.
    `language` (songs.language, e.g. "Ukrainian") and `origin`
    (artists.origin) pick the Cyrillic system when the letters don't."""
    if not text or _only_latin_letters(text):
        return None
    text = unicodedata.normalize("NFKC", text)
    scripts = scripts_in(text)
    if "CYRILLIC" in scripts:
        text = _cyrillic(text, language, origin)
    if "GREEK" in scripts:
        text = _greek(text)
    if "GEORGIAN" in scripts:
        text = _uncased(text, lambda ch: _GEORGIAN.get(ch, ch))
    if "HANGUL" in scripts:
        text = _uncased(text, lambda ch: _hangul(ch) or ch)
    if "KANA" in scripts:
        text = _kana(text)
        text = re.sub(r"[a-z]", lambda m: m.group().upper(), text, count=1)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text if text and _only_latin_letters(text) else None
