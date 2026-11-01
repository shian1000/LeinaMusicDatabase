"""Regex pieces several title rules share - one spelling each, so a new
"featuring" word or bracket pair is added once. Pieces only, no logic and
no imports from the app: normalizer, text_utils and sanitize all build
their patterns from these."""
import re

# A featured-artist credit's word ("feat.", "ft", "featuring"); with "with"
# too where it stands inside a bracket ("(with B)")
FEAT_WORD = r"(?:feat\.?|ft\.?|featuring)"
FEAT_OR_WITH_WORD = r"(?:feat\.?|ft\.?|featuring|with)"

# One "(...)" or "[...]" group, no nesting, its content captured
BRACKETED = r"[(\[]([^)\]]*)[)\]]"

# Bracket pairs YouTube titles use like "()" / "[]": the CJK lenticular
# "【】" ("【Kan/Rom/Eng Lyrics】") and the fullwidth "（）" / "［］" of CJK
# input methods ("Workin’ Hard（Official Video）")
YOUTUBE_BRACKET_PAIRS = {"(": ")", "[": "]", "【": "】", "（": "）", "［": "］"}
_OPENING = "".join(YOUTUBE_BRACKET_PAIRS)
_CLOSING = "".join(YOUTUBE_BRACKET_PAIRS.values())
# One group in any of those pairs, no nesting, its content captured
YOUTUBE_BRACKETED = f"[{re.escape(_OPENING)}]([^{re.escape(_OPENING + _CLOSING)}]*)[{re.escape(_CLOSING)}]"

# A release year
YEAR = r"(?:19|20)\d{2}"
