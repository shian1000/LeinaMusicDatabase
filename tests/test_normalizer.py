"""utils/common/normalizer.py: normalize() and normalize_apostrophes() read
the same set of apostrophe-like characters; with_diacritics_of() adds only
accents; and_word_key() reads "&", "+" and "and" as one word."""
import pytest

from utils.common.normalizer import and_word_key, normalize, normalize_apostrophes, with_diacritics_of


@pytest.mark.parametrize("apostrophe", ["'", "’", "ʹ", "ʽ", "＇"])
def test_every_apostrophe_is_one_for_both_normalizers(apostrophe):
    # COM-30: two lists - normalize() turned ʹ / ʽ into a space, normalize_apostrophes() left ＇
    assert normalize(f"Don{apostrophe}t Stop") == "dont stop"
    assert normalize(f"Don{apostrophe}t Stop", strip_apostrophe=False) == "don t stop"
    assert normalize_apostrophes(f"Don{apostrophe}t") == "Don't"


@pytest.mark.parametrize("text, spelled, expected", [
    ("Plesem sama", "Plešem sama", "Plešem sama"),     # MusicBrainz's accent taken (2026-11-25)
    ("Plesem sama", "Plešem Sama", "Plešem sama"),     # only the accent, not MusicBrainz's case
    ("Ukanose", "Ūkanose", "Ūkanose"),
    ("Kese etikete", "Kese Etikete", None),            # a case change alone
    ("Plesem sama", "Plesem sama", None),
    ("Plésem", "Plešem", None),                         # another accent than ours
    ("Plesem sama", "Plešem samo", None),               # other letters
])
def test_with_diacritics_of_adds_only_accents(text, spelled, expected):
    assert with_diacritics_of(text, spelled) == expected


def test_and_word_key_reads_every_and_as_one():
    assert and_word_key("The Ball & Chain") == and_word_key("The Ball And Chain") == "the ball and chain"
    assert and_word_key("Florence + the Machine") == and_word_key("Florence and the Machine")
    # A whole word only: "C+C" and "R&B" are names
    assert and_word_key("C+C Music Factory") != and_word_key("C and C Music Factory")
    assert and_word_key("The Ball") != and_word_key("The Ball & Chain")
