# Quick Reference: Metadata Fallback Implementation

## What Changed?

### New Helper Function
```
_extract_metadata_with_fallback(file: Path) -> dict
├─ Try ID3 tags first ✓
├─ If fails → Try filename parsing
├─ If fails → Return complete dict with defaults
└─ If all fail → Raise ValueError with clear message
```

### Better Error Handling
```
try:
    metadata = _extract_metadata_with_fallback(file)
    # Process normally...
except ValueError:
    # Skip with clear message about metadata extraction failure
except Exception:
    # Handle other unexpected errors
```

## Why This Approach?

✅ **Simple & Clear** — Single responsibility function
✅ **Logical Flow** — Try best method first, fall back gracefully
✅ **No Code Duplication** — Reuses existing extraction functions
✅ **Complete Data** — Always returns valid metadata dict
✅ **Good Logging** — Tracks what happened at each step
✅ **Testable** — 4 comprehensive unit tests
✅ **Backward Compatible** — Same public behavior

## Test Scenarios

| Scenario | Before | After |
|----------|--------|-------|
| Good ID3 tags | ✅ Works | ✅ Works (no change) |
| Bad ID3 tags | ❌ Crash | ✅ Fallback to filename |
| Corrupted file | ❌ Crash | ✅ Skips with message |

## User Impact

### Before
```
Error reading /music/file.mp3: [mutagen error details]
```

### After
```
⚠️  Could not read ID3 tags from file.mp3, attempting to extract from filename...
Checking: Artist Name - Song Title (lang: Unknown)
 -> Added new song.
```

Or if filename can't be parsed:
```
⚠️  Could not read ID3 tags from file.mp3, attempting to extract from filename...
❌ Skipping file.mp3: Could not extract metadata
```

## Code Metrics

- **14 tests total** (was 10, +4 new)
- **All passing** ✅ (0.58s)
- **~35 lines** of new code
- **0 breaking changes**
