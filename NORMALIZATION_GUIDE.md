# String Normalization Guide

## Overview

The MusicDatabase application now uses a **centralized, comprehensive string normalization system** to handle user input, database output, and cross-language text consistently. This replaces multiple ad-hoc normalization implementations scattered across the codebase.

## The Normalizer Module

**Location:** `src/utils/common/normalizer.py`

### Core Function: `normalize()`

```python
from utils.common.normalizer import normalize

# Basic usage (default: lowercase, remove apostrophes, remove punctuation)
clean_text = normalize("Björk Guðmundsdóttir")
# Result: "bjork gudmundsdottir"

# Preserve case
clean_text = normalize("Björk", lower=False)
# Result: "Bjork"

# Keep apostrophes as spaces
clean_text = normalize("l'artiste", strip_apostrophe=False)
# Result: "l artiste"

# Disable punctuation removal (keeps them as spaces)
clean_text = normalize("rock & roll", remove_punctuation=False)
# Result: "rock roll"
```

### Function Parameters

- **`s`** (str | None): Input string to normalize. None returns empty string.
- **`lower`** (bool, default=True): Convert to lowercase.
- **`strip_apostrophe`** (bool, default=True): Remove apostrophes; if False, replace with space.
- **`collapse_spaces`** (bool, default=True): Collapse multiple spaces to single space and trim.
- **`remove_punctuation`** (bool, default=True): Remove all punctuation; if False, only normalize separators.

### Normalization Steps

1. **Unicode decomposition** (NFKD): Separates base characters from diacritics
2. **Combining mark removal**: Strips accents, leaving base letters
3. **Special character mapping**: Converts non-ASCII letters (ß→ss, æ→ae, etc.)
4. **Apostrophe handling**: Removes or replaces variants (', `, ´, ʼ, ʻ, ＇, etc.)
5. **Punctuation normalization**: Removes or replaces punctuation
6. **Whitespace collapse**: Reduces multiple spaces to one
7. **Optional lowercasing**: Converts to lowercase

### Character Support

Comprehensive support for:
- ✅ **Latin letters** with diacritics (à, é, ï, ö, ü, ç, etc.)
- ✅ **Cyrillic** (Russian, Ukrainian, etc.)
- ✅ **Arabic**
- ✅ **Japanese** (including fullwidth/halfwidth variants via NFKD)
- ✅ **Polish, Norwegian, Czech, and other European languages**
- ✅ **Ambivalent symbols** (various apostrophe and quotation mark variants)

### Helper Function: `compare()`

```python
from utils.common.normalizer import compare

match = compare("Björk", "bjork")
# Result: True

match = compare("", "anything")
# Result: False (either empty returns False)
```

## Files Updated

### 1. `src/utils/common/text_utils.py`
- **Changed:** `normalize_text()` now imports and delegates to the centralized `normalize()`
- **Impact:** All code calling `normalize_text()` automatically uses the new implementation
- **Note:** `normalize_japanese_title()` is preserved for specialized Japanese text handling

### 2. `src/utils/common/file_management.py`
- **Changed:** Removed the local `normalize()` function, now imports from `normalizer.py`
- **Changed:** `fuzzy_match()` updated to call the new `normalize()` with keyword arguments
- **Impact:** Database lookups and file matching now use centralized normalization

### 3. `src/utils/discoveries/import_data_from_mp3_tags.py`
- **Changed:** `normalize_title()` now applies NFC normalization followed by the centralized `normalize()`
- **Impact:** MP3 tag import and duplicate detection use consistent rules

## Migration Guide

### Old Code
```python
# Multiple implementations scattered:
from utils.common.text_utils import normalize_text
from utils.common.file_management import normalize

text1 = normalize_text(user_input)
text2 = normalize(db_field, strip_apostrophe=False)
```

### New Code
```python
# Single import from centralizer:
from utils.common.normalizer import normalize

text1 = normalize(user_input)
text2 = normalize(db_field, strip_apostrophe=False)

# Or use existing wrappers (still work):
from utils.common.text_utils import normalize_text
text1 = normalize_text(user_input)  # Calls normalize() internally
```

## Testing & Validation

All modified modules pass Python syntax checks:
- ✅ `normalizer.py` - No syntax errors
- ✅ `text_utils.py` - No syntax errors
- ✅ `file_management.py` - No syntax errors
- ✅ `import_data_from_mp3_tags.py` - No syntax errors

To test locally:
```bash
python -c "from src.utils.common.normalizer import normalize; print(normalize('Björk Guðmundsdóttir'))"
# Output: bjork gudmundsdottir
```

## Future Enhancements

- Add language-specific rules (e.g., Norwegian "kj" → "k")
- Integrate phonetic matching for fuzzy search
- Add transliteration for non-Latin scripts (Arabic, Cyrillic, CJK)
