"""Readable Latin forms of names in other alphabets, offered as suggestions
for the owner's Latin forms (utils/database/latin_names.py) - never stored
without the owner accepting or editing them.

Unlike script_utils.matching_key() (a lossy key for comparisons), this aims
at what a person would write: English-style romanization for Russian
("Алёна" -> "Alyona", "Жуки" -> "Zhuki"), the Ukrainian national system
("Океан Ельзи" -> "Okean Elzy"), the Bulgarian streamlined system ("Щурците"
-> "Shturtsite"), MusicDatabase's own Lacinka for Belarusian
(utils/youtube/transliteration.py), Serbian/Macedonian Latin, Greek ELOT,
Georgian national, Korean Revised (without sound changes), Hepburn for
Japanese (cutlet: kanji too, by the dictionary), pinyin for Mandarin
(pypinyin) and Jyutping for Cantonese (ToJyutping), told apart by kana, the
song's language or the artist's origin. Arabic, Hebrew and other scripts
have no automatic reading here:
suggest_latin() gives None and the owner types the form.
"""
import re
import unicodedata
from functools import lru_cache

from utils.common.script_utils import hangul_syllable, scripts_in
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

# Bulgarian streamlined system (2009): щ -> sht, ъ -> a, a final -ия -> -ia
_BG = {**_RU, "е": "e", "й": "y", "х": "h", "щ": "sht", "ъ": "a", "ь": "y"}

# Mongolian (MNS 5217:2012), also for Tuvan, which writes the same ө ү and ң
_MN = {**_RU, "ж": "j", "й": "i", "х": "kh", "щ": "sh", "ъ": "i", "ь": "i",
       "ө": "ö", "ү": "ü", "ң": "ng"}

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


def _bulgarian(text: str) -> str:
    def convert(word: str) -> str:
        lower = word.lower()
        out = [_cased(ch, _BG[lower[i]], word) if lower[i] in _BG else ch for i, ch in enumerate(word)]
        if lower.endswith("ия"):  # България -> Balgaria
            out[-1] = _cased(word[-1], "a", word)
        return "".join(out)
    return _by_word(text, convert)


def _mongolian(text: str) -> str:
    def convert(word: str) -> str:
        out, lower = [], word.lower()
        for i, ch in enumerate(word):
            low = lower[i]
            if low == "е" and (not i or lower[i - 1] in _RU_VOWELS | set("өү")):
                latin = "ye"  # Ер -> Yer, but Tuvan мен -> men
            else:
                latin = _MN.get(low)
            out.append(_cased(ch, latin, word) if latin is not None else ch)
        return "".join(out)
    return _by_word(text, convert)


def _serbian(text: str) -> str:
    return _by_word(text, lambda word: "".join(
        _cased(ch, _SR[ch.lower()], word) if ch.lower() in _SR else ch for ch in word))


# Spellings only Belarusian has: Russian writes жи/ши/чи/же/ше/че/це
_BELARUSIAN_PAIRS = ("жы", "шы", "чы", "жэ", "шэ", "чэ", "цэ")


_CYRILLIC_BY_NAME = (("belarus", "be"), ("ukrain", "uk"), ("russia", "ru"), ("bulgar", "bg"), ("mongol", "mn"),
                     ("tuva", "mn"), ("serbia", "sr"),
                     ("macedon", "sr"), ("bosnia", "sr"), ("montenegr", "sr"))


def _cyrillic_system(text: str, language: str | None, origin: str | None) -> tuple[str, str]:
    """(code, what settled it): the language field first ("language"), then
    letters only one alphabet has ("letters"), then the artist's origin
    ("origin"), else a guess ("guess"). Belarusian has і with ы/э/ё,
    Ukrainian і with и/щ/apostrophe; Russian has neither і nor ў. Bulgarian
    has no letter of its own, so only its name tells it."""
    language = (language or "").lower()
    for key, code in _CYRILLIC_BY_NAME:
        if key in language:
            return code, "language"
    lower = text.lower()
    letters = set(lower)
    if "ў" in letters or any(pair in lower for pair in _BELARUSIAN_PAIRS):
        return "be", "letters"
    if letters & set("ђјљњћџѓќѕ"):
        return "sr", "letters"
    if letters & set("їєґ"):
        return "uk", "letters"
    if "і" in letters and letters & set("ыэё"):
        return "be", "letters"
    if "і" in letters and letters & set("ищ'ʼ’"):
        return "uk", "letters"
    origin = (origin or "").lower()
    for key, code in _CYRILLIC_BY_NAME:
        if origin.startswith(key):
            return code, "origin"
    # і alone: Belarusian or Ukrainian - the library has far more Ukrainian
    return ("uk" if "і" in letters else "ru"), "guess"


def cyrillic_language(text: str, language: str | None, origin: str | None) -> str:
    """"be" / "uk" / "bg" / "mn" / "sr" / "ru" - _cyrillic_system()'s code."""
    return _cyrillic_system(text, language, origin)[0]


def _cyrillic(text: str, language: str | None, origin: str | None) -> str:
    code = cyrillic_language(text, language, origin)
    if code == "be":
        result = belarusian_to_latin(text)
        # The Belarusian alphabet lacks и, щ, ъ - a Russian name by a
        # Belarusian band: fall back to Russian for those words
        return _by_word(result, lambda w: _russian(w) if _CYRILLIC_LETTER.search(w) else w)
    if code == "uk":
        # э, ы, ё, ъ aren't Ukrainian: a Russian word of a Ukrainian singer
        # ("Небо - это Я", Sofia Rotaru) is read as Russian
        return _by_word(_ukrainian(text), lambda w: _russian(w) if _CYRILLIC_LETTER.search(w) else w)
    if code == "bg":
        return _bulgarian(text)
    if code == "mn":
        return _mongolian(text)
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

# hangul_syllable() from script_utils, the same for matching and for showing


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
_JAPANESE_PUNCTUATION = {"、": ", ", "。": ". ", "「": ' "', "」": '" ', "『": ' "', "』": '" ', "【": " [", "】": "] ",
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


# ---- Japanese and Chinese ------------------------------------------------------

# A run of kanji / hanzi or kana, read as one: cutlet (MeCab with UniDic) for
# Japanese - word breaks, long vowels, loanwords as spelt ("ファンタジー" ->
# "Fantasy") -, pypinyin for Mandarin. Both load a dictionary, so only when
# first needed.
_HAN_OR_KANA_RUN = re.compile(r"[\u3005\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_SMALL_KANA = set("ゃゅょぁぃぅぇぉゎっャュョァィゥェォヮッー")


# Loanwords the dictionary spells as the wrong English word, by its own word
# split ("サウンド" + "トラック" -> "sound truck"), and words it splits in two
_JAPANESE_LOANWORDS = {"トラック": "track", "ブルース": "blues", "アニメ": "anime", "テレ": "tele"}
_JAPANESE_JOINS = {"sound track": "soundtrack", "tele caster": "telecaster"}


@lru_cache(maxsize=1)
def _cutlet():
    import cutlet
    katsu = cutlet.Cutlet()
    for word, latin in _JAPANESE_LOANWORDS.items():
        katsu.add_exception(word, latin)
    return katsu


def _japanese_run(run: str) -> str:
    katsu = _cutlet()
    # A name UniDic doesn't know comes back as single kana ("きゃりーぱみゅぱみゅ"
    # -> "kya ri- pa mi"): kana are then read letter by letter
    if all(_KANA_SCRIPT.match(ch) for ch in run) and any(
            w.surface[0] in _SMALL_KANA for w in katsu.tagger(run)):
        return _kana(run)
    latin = katsu.romaji(run)
    for split, joined in _JAPANESE_JOINS.items():
        latin = re.sub(split, joined, latin, flags=re.IGNORECASE)
    return latin


def _chinese_run(run: str) -> str:
    from pypinyin import lazy_pinyin
    return " ".join(syllable.capitalize() for syllable in lazy_pinyin(run))


def _cantonese_run(run: str) -> str:
    """Jyutping without the tone numbers: "海闊天空" -> "Hoi Fut Tin Hung"."""
    import ToJyutping
    return " ".join(jyutping.rstrip("0123456789").capitalize() if jyutping else char
                    for char, jyutping in ToJyutping.get_jyutping_list(run))


_KANA_SCRIPT = re.compile(r"[\u3040-\u30ff]")


def _han_system(text: str, language: str | None, origin: str | None) -> tuple[str, str] | None:
    """("ja" / "zh" / "yue", what settled it) for a name in kanji / hanzi or
    kana: kana = Japanese ("alphabet" when there's no kanji, else "letters"),
    then the song's language, the artist's origin, else Japanese as a guess
    (most of the library's)."""
    if _KANA_SCRIPT.search(text):
        return "ja", "letters" if re.search(r"[\u3400-\u9fff]", text) else "alphabet"
    language = (language or "").lower()
    if "cantonese" in language:
        return "yue", "language"
    if "japan" in language:
        return "ja", "language"
    if "chinese" in language or "mandarin" in language:
        return "zh", "language"
    origin = (origin or "").lower()
    if origin.startswith("japan"):
        return "ja", "origin"
    if origin.startswith(("china", "taiwan")):
        return "zh", "origin"
    if origin.startswith(("hong kong", "macau")):
        return "yue", "origin"
    if not _in_japanese_charset(text):
        return "zh", "letters"  # a simplified character Japanese doesn't write: 图, 伦
    return "ja", "guess"


def _in_japanese_charset(text: str) -> bool:
    try:
        text.encode("shift_jis")
    except UnicodeEncodeError:
        return False
    return True


def _han(text: str, language: str | None, origin: str | None) -> str:
    system = _han_system(text, language, origin)
    if system[0] == "ja":
        text = text.replace("・", " ")
    read = {"ja": _japanese_run, "zh": _chinese_run, "yue": _cantonese_run}[system[0]]
    def reading(m) -> str:
        latin = read(m.group())
        # capitalized only at the start of the name (cutlet capitalizes each run);
        # a space between a reading and a Latin word next to it: "Adoの歌" -> "Ado no uta"
        starts = not any(ch.isalnum() for ch in text[:m.start()])
        latin = latin[:1].upper() + latin[1:] if starts else latin[:1].lower() + latin[1:]
        before = " " if m.start() and text[m.start() - 1].isalnum() else ""
        after = " " if m.end() < len(text) and text[m.end()].isalnum() else ""
        return before + latin + after
    return _HAN_OR_KANA_RUN.sub(reading, text)


# ---- Arabic --------------------------------------------------------------------

# Arabic writes few vowels: mishkal puts the short ones in first (by its
# dictionary, in Modern Standard Arabic - a dialect's title comes out less
# well), then the letters are read English-style ("حبيبي" -> "Habibi"), each
# word in its pausal form, without the case ending.
_ARABIC = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "aa", "ٱ": "", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h",
    "خ": "kh", "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t",
    "ظ": "z", "ع": "'", "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h",
    "و": "w", "ي": "y", "ى": "a", "ة": "a", "ء": "'", "ؤ": "'", "ئ": "'",
}
_FATHA, _DAMMA, _KASRA, _SUKUN, _SHADDA = "\u064e", "\u064f", "\u0650", "\u0652", "\u0651"
_SHORT = {_FATHA: "a", _DAMMA: "u", _KASRA: "i"}
_TANWEEN = "\u064b\u064c\u064d"
_ARABIC_WORD = re.compile(r"[\u0621-\u0652\u0670]+")


@lru_cache(maxsize=1)
def _mishkal():
    import mishkal.tashkeel
    return mishkal.tashkeel.TashkeelClass()


def _arabic_word(word: str) -> str:
    word = re.sub(f"[{_TANWEEN}]", "", word).rstrip(_FATHA + _DAMMA + _KASRA + _SUKUN)  # pausal
    article = ""
    letters_only = "".join(ch for ch in word if ch in _ARABIC or ch == "ٱ")
    if letters_only[:2] in ("ال", "ٱل") and len(letters_only) > 3:
        article, word = "al-", word[word.index("ل") + 1:].lstrip(_SUKUN + _FATHA)
    # (letter, its marks) pairs
    letters = []
    for ch in word:
        if ch in _ARABIC:
            letters.append([ch, ""])
        elif letters:
            letters[-1][1] += ch
    out = []
    for i, (ch, marks) in enumerate(letters):
        vowel = next((_SHORT[m] for m in marks if m in _SHORT), "")
        after_vowel = bool(out) and out[-1][-1:] in ("a", "i", "u")
        if ch in "أإآ" and vowel:
            latin = ""  # the hamza's own vowel says it
        elif ch == "ا" and i:
            latin = "" if after_vowel else "a"  # a long a
        elif ch in "وي" and not vowel and i and not (after_vowel and ch == {"u": "و", "i": "ي"}.get(out[-1][-1:])):
            # no vowel of its own after a consonant: a long u / i ("حبيبي" -> "habibi")
            latin = ("u" if ch == "و" else "i") if not after_vowel else _ARABIC[ch]
        elif ch in "وي" and not vowel and after_vowel and ch == {"u": "و", "i": "ي"}.get(out[-1][-1:]):
            latin = ""  # u + و, i + ي: the vowel made long
        else:
            latin = _ARABIC[ch]
        if ch == "ي" and not vowel and i == len(letters) - 1 and out and out[-1][-1:] in ("a", "u"):
            out[-1], latin = out[-1][:-1], "i"  # a final -i, whatever vowel was guessed before it
        if _SHADDA in marks:
            if article and not i:
                article = "a" + latin + "-"  # a sun letter takes the article's l: "ash-shams"
            else:
                latin += latin  # a doubled consonant
        out.append(latin + vowel + ("a" if "\u0670" in marks else ""))
    latin = re.sub(r"(.)\1{2,}", r"\1\1", "".join(out)).replace("aa", "a")
    return article + (latin if article else latin.strip("'"))


def _persian(text: str, language: str | None) -> bool:
    """Persian / Urdu, which mishkal doesn't read: by the language, or a letter
    Arabic doesn't have (پ چ ژ گ ک ی)."""
    return any(name in (language or "").lower() for name in ("persian", "farsi", "urdu", "pashto", "dari")) \
        or bool(set(text) & set("پچژگکیۀ"))


def _arabic(text: str) -> str:
    vowelled = _mishkal().tashkeel(text).strip()
    return re.sub(r"\b\w", lambda m: m.group().upper(),
                  _ARABIC_WORD.sub(lambda m: _arabic_word(m.group()), vowelled), count=1)


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
    the first converted letter capitalized - not a Latin one before it
    ("John Park(존박)" stays "John")."""
    out, first = [], True
    for ch in text:
        latin = convert_char(ch)
        if first and latin != ch:
            latin, first = latin[:1].upper() + latin[1:], False
        out.append(latin)
    return "".join(out)


def _only_latin_letters(text: str) -> bool:
    return all(not ch.isalpha() or unicodedata.name(ch, "").startswith("LATIN") for ch in text)


def suggest_latin(text: str | None, language: str | None = None, origin: str | None = None) -> str | None:
    """A Latin form to offer for `text`, or None: already Latin, or a script
    (kanji, Arabic, Hebrew, ...) with no automatic reading here.
    `language` (songs.language, e.g. "Ukrainian") and `origin`
    (artists.origin) pick the Cyrillic system when the letters don't."""
    if not text or _only_latin_letters(text):
        return None
    original = text = unicodedata.normalize("NFKC", text)
    scripts = scripts_in(text)
    if "CYRILLIC" in scripts:
        text = _cyrillic(text, language, origin)
    if "GREEK" in scripts:
        text = _greek(text)
    if "GEORGIAN" in scripts:
        text = _uncased(text, lambda ch: _GEORGIAN.get(ch, ch))
    if "HANGUL" in scripts:
        text = _uncased(text, lambda ch: hangul_syllable(ch) or ch)
    if "KANA" in scripts or "CJK" in scripts:
        text = "".join(_JAPANESE_PUNCTUATION.get(ch, ch) for ch in _han(text, language, origin))
    if "ARABIC" in scripts and not _persian(text, language):
        text = _arabic(text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if "?" in text and "?" not in original:
        return None  # a character the dictionary couldn't read
    return text if text and _only_latin_letters(text) else None


_CYRILLIC_SYSTEMS = {"be": "Belarusian Lacinka", "uk": "Ukrainian", "bg": "Bulgarian", "mn": "Mongolian/Tuvan",
                     "sr": "Serbian/Macedonian Latin", "ru": "Russian"}
_ONE_SYSTEM = (("GREEK", "Greek"), ("GEORGIAN", "Georgian"), ("HANGUL", "Korean"))
_HAN_SYSTEMS = {"ja": "Japanese (Hepburn)", "zh": "Chinese (pinyin)", "yue": "Cantonese (Jyutping)"}


def latin_basis(text: str | None, language: str | None = None, origin: str | None = None) -> tuple[str, str] | None:
    """(system, what settled it) behind suggest_latin(text, ...): "language" /
    "letters" / "origin" / "guess" for Cyrillic (_cyrillic_system()) and
    kanji / hanzi (_han_system()), "vowels" for Arabic (they're guessed),
    "alphabet" for a script with one system here. None when it suggests
    nothing."""
    if suggest_latin(text, language, origin) is None:
        return None
    scripts = scripts_in(unicodedata.normalize("NFKC", text))
    if "CYRILLIC" in scripts:
        code, basis = _cyrillic_system(text, language, origin)
        return _CYRILLIC_SYSTEMS[code], basis
    if "KANA" in scripts or "CJK" in scripts:
        code, basis = _han_system(text, language, origin)
        return _HAN_SYSTEMS[code], basis
    if "ARABIC" in scripts:
        return "Arabic", "vowels"  # mishkal's guess of the unwritten vowels
    return next(name for script, name in _ONE_SYSTEM if script in scripts), "alphabet"
