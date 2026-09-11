import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.youtube.transliteration import (
    belarusian_to_latin,
    latin_to_belarusian,
    is_transliterable,
    transliterate,
)


# Real songs in the DB: both directions verified against the user-supplied
# Belarusian Cyrillic <-> Lacinka table (Wikipedia's "Transliteracja tekstu
# z cyrylicy na łacinkę").
def test_belarusian_to_latin_adzinoctva():
    assert belarusian_to_latin("Адзіноцтва") == "Adzinoctva"


def test_belarusian_to_latin_iholki():
    assert belarusian_to_latin("Іголкі") == "Ihołki"


def test_latin_to_belarusian_round_trips():
    assert latin_to_belarusian("Adzinoctva") == "Адзіноцтва"
    assert latin_to_belarusian("Ihołki") == "Іголкі"


# Real case: "некаторымі" transliterated to "ńiekatorymi" (wrong) before this
# fix — н/с/ц/ж/ч/ш only soften before an explicit ь; a bare following
# я/е/ё/ю carries the palatalization on its own via the vowel's own "i-"
# glide, so the consonant stays in its normal form. л is the one exception:
# its hard/soft split (ł vs l) is always marked, even with no ь, so it keeps
# softening before a bare iotated vowel too.
def test_belarusian_to_latin_soft_consonant_needs_explicit_soft_sign():
    assert belarusian_to_latin("не") == "nie"
    assert belarusian_to_latin("нь") == "ń"
    assert belarusian_to_latin("н") == "n"
    assert belarusian_to_latin("некаторымі") == "niekatorymi"


def test_belarusian_to_latin_l_softens_before_bare_iotated_vowel():
    assert belarusian_to_latin("ля") == "la"
    assert belarusian_to_latin("ль") == "l"
    assert belarusian_to_latin("л") == "ł"


# Real case: "смерць" -> "smierć" (wrong, missing the leading ś) before this
# fix — softness cascades backward through a whole consonant cluster, not
# just from a directly-adjacent ь/vowel. с here is two letters back from the
# iotated "е" (across the unmarked "м"), the same shape as "сне" -> "śnie".
def test_belarusian_to_latin_softness_cascades_across_consonant_cluster():
    assert belarusian_to_latin("сне") == "śnie"
    assert belarusian_to_latin("смерць") == "śmierć"


# Real case: "большасці" -> "bolšaści" and "жыццё" -> "žyćcio" both confirm
# і triggers the same cascade as я/е/ё/ю, and that a same-letter cluster
# ("цц") isn't special-cased — only the member touching the trigger vowel
# stays plain.
def test_belarusian_to_latin_cascade_triggered_by_i_and_same_letter_cluster():
    assert belarusian_to_latin("большасці") == "bolšaści"
    assert belarusian_to_latin("жыццё") == "žyćcio"


# Real case: "Іголкі" -> "Iholki" (wrong, lost the "ł") if the cascade rule
# were applied to л the same way as с/н/ц — л sits in the "лк" cluster,
# which (like "сн" in "сне") ends right before "і", but л's own hard/soft
# split is always determined locally by its own immediate next letter (к,
# a consonant here), never by cascading in from elsewhere in its cluster.
def test_belarusian_to_latin_l_does_not_cascade_from_cluster():
    assert belarusian_to_latin("Іголкі") == "Ihołki"


# Real case: "з'ява" — a hard apostrophe keeps the preceding consonant
# hard and blocks the cascade entirely, rather than triggering it the way ь
# does (it exists specifically to prevent palatalization, the opposite of
# ь's role).
def test_belarusian_to_latin_apostrophe_blocks_cascade():
    assert belarusian_to_latin("з'ява") == "zjava"


# Real case: an isolated soft marker (nothing follows at all — end of
# string, or the word ends there) can only have come from a real ь, so
# reversing it must reinsert one.
def test_latin_to_belarusian_isolated_soft_marker_reinserts_soft_sign():
    assert latin_to_belarusian("ń") == "нь"
    assert latin_to_belarusian("l") == "ль"
    assert latin_to_belarusian("nie") == "не"
    assert latin_to_belarusian("la") == "ля"


# Real case: "śnie" (-> "сне", no ь) was wrongly reversed to "сьне" by an
# earlier "always add ь for a soft marker" version of this fix. "ś" here is
# с regressively assimilating the softness of the following (itself soft)
# н — a cluster effect, not a real ь — and is indistinguishable from a real
# ь on the Latin side except by checking what follows: a soft marker
# directly before *another letter* (vowel or consonant) never gets ь
# reinserted, only one before non-letter/end-of-string does. "bolšaści" and
# "žyćcio" cover the same rule for ś/ć before a consonant with no "l"
# involved at all, confirming it's not л-specific.
def test_latin_to_belarusian_soft_marker_before_another_letter_stays_bare():
    assert latin_to_belarusian("śnie") == "сне"
    assert latin_to_belarusian("bolšaści") == "болшасці"
    assert latin_to_belarusian("žyćcio") == "жыццё"


# End-to-end regression texts: locks in the full, human-verified output for
# both transliteration directions so a future change to the letter tables
# or the softening rules gets caught here, not just on the small isolated
# examples above. Source: Wikipedia (Cyrillic paragraph on Belarusian
# Lacinka usage) and a real Belarusian song's Lacinka lyrics, both checked
# character-by-character against the user-supplied transliteration table.
_CYRILLIC_PARAGRAPH = (
    "У XXI ст. у сучаснай форме ўжываецца некаторымі пісьменнікамі[1], "
    "некаторымі вытворцамі ў дызайне ўпакоўкі прадукцыі, карыстальнікамі "
    "інтэрнэту. Ёсць праграмы-«лацінізатары» тэкстаў, дзякуючы якім, "
    "напрыклад, анлайн-газету «Наша Ніва» можна чытаць і на беларускай "
    "лацінцы. Нярэдка лацінкай перадаюць тэксты каталіцкага зместу — "
    "напрыклад, на памятных дошках у некаторых касцёлах, напрыклад, у "
    "Чырвоным касцёле."
)
_LACINKA_PARAGRAPH_EXPECTED = (
    "U XXI st. u sučasnaj formie ŭžyvajecca niekatorymi piśmieńnikami[1], "
    "niekatorymi vytvorcami ŭ dyzajnie ŭpakoŭki pradukcyi, karystalnikami "
    "internetu. Jość prahramy-«łacinizatary» tekstaŭ, dziakujučy jakim, "
    "naprykład, anłajn-hazietu «Naša Niva» možna čytać i na biełaruskaj "
    "łacincy. Niaredka łacinkaj pieradajuć teksty katałickaha zmiestu — "
    "naprykład, na pamiatnych doškach u niekatorych kaściołach, naprykład, "
    "u Čyrvonym kaściole."
)


def test_belarusian_to_latin_full_paragraph_regression():
    assert belarusian_to_latin(_CYRILLIC_PARAGRAPH) == _LACINKA_PARAGRAPH_EXPECTED


_LACINKA_SONG = """Dla hienerała kraina - vajna.
Dla niefarmała kraina - čuma.
Dla radykała kraina - turma.
Ale dla bolšaści krainy niama.
Dla pansłavista kraina - adna.
Dla satanista kraina - truna.
Dla hitarysta kraina - struna.
Ale dla bolšaści krainy niama.
Viarni žyćcio svajoj krainie,
Jana ŭ śnie chałodnym hinie!
Viarni žyćcio svajoj ziamli,
Jaje sivyja śniahi zamiali
Dla nielehała kraina - zima
Dla admirała kraina - karma
Dla vykidały kraina - karčma
Ale dla bolšaści krainy niama.
Viarni žyćcio svajoj krainie,
Jana ŭ śnie chałodnym hinie!
Viarni žyćcio svajoj ziamli,
Jaje sivyja śniahi zamiali
Viarni žyćcio svajoj krainie,
Jana ŭ śnie chałodnym hinie!
Viarni žyćcio svajoj ziamli,
Masty spali i ziamlu asviatli.
Masty spali i ziamlu asviatli."""

_CYRILLIC_SONG_EXPECTED = """Для генэрала краіна - вайна.
Для нефармала краіна - чума.
Для радыкала краіна - турма.
Але для болшасці краіны няма.
Для панславіста краіна - адна.
Для сатаніста краіна - труна.
Для гітарыста краіна - струна.
Але для болшасці краіны няма.
Вярні жыццё сваёй краіне,
Яна ў сне халодным гіне!
Вярні жыццё сваёй зямлі,
Яе сівыя снягі замялі
Для нелегала краіна - зіма
Для адмірала краіна - карма
Для выкідалы краіна - карчма
Але для болшасці краіны няма.
Вярні жыццё сваёй краіне,
Яна ў сне халодным гіне!
Вярні жыццё сваёй зямлі,
Яе сівыя снягі замялі
Вярні жыццё сваёй краіне,
Яна ў сне халодным гіне!
Вярні жыццё сваёй зямлі,
Масты спалі і зямлю асвятлі.
Масты спалі і зямлю асвятлі."""


def test_latin_to_belarusian_full_song_regression():
    assert latin_to_belarusian(_LACINKA_SONG) == _CYRILLIC_SONG_EXPECTED


def test_transliterate_picks_direction_from_script():
    # Cyrillic input -> Latin
    assert transliterate("Адзіноцтва", "Belarusian") == "Adzinoctva"
    # Latin input -> Cyrillic
    assert transliterate("Ihołki", "Belarusian") == "Іголкі"


def test_transliterate_unknown_language_returns_none():
    assert transliterate("Adzinoctva", "English") is None
    assert transliterate("Adzinoctva", None) is None


def test_is_transliterable_case_insensitive():
    assert is_transliterable("Belarusian")
    assert is_transliterable("belarusian")
    assert not is_transliterable("Polish")
    assert not is_transliterable(None)
