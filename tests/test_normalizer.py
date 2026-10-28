"""Apostrophes in utils/common/normalizer.py: normalize() and
normalize_apostrophes() read the same set of apostrophe-like characters."""
import pytest

from utils.common.normalizer import normalize, normalize_apostrophes


@pytest.mark.parametrize("apostrophe", ["'", "’", "ʹ", "ʽ", "＇"])
def test_every_apostrophe_is_one_for_both_normalizers(apostrophe):
    # COM-30: two lists - normalize() turned ʹ / ʽ into a space, normalize_apostrophes() left ＇
    assert normalize(f"Don{apostrophe}t Stop") == "dont stop"
    assert normalize(f"Don{apostrophe}t Stop", strip_apostrophe=False) == "don t stop"
    assert normalize_apostrophes(f"Don{apostrophe}t") == "Don't"
