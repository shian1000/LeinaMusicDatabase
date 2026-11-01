# ADR-0005: Names are cleaned in one place, by switches the same everywhere, in one order, keeping what a title says

- Status: Accepted (built 2026-11-01)
- Date: 2026-11-01

## Context

Every source of names cleaned them its own way: the YouTube playlist import ran about ten title
functions of its own, the mp3 import a different three, Clean names its own passes in the menu,
Leina Radio's song requests almost nothing. The same rule was spelt several times (featured-artist
words in 9 regexes, brackets in 6), the mp3 import kept "(Official Video)" the YouTube import cut,
and a name cleaned at import met the same name stored uncleaned - so comparisons depended on where
a name came from. Cleaning also threw information away: a year in a title, the featured artists
(cut along with a junk bracket, or left in the title), where a song is from.

Measured on the catalog (6,373 songs) and the library's tags (4,281 files), several cleanups
were safe for one source and broke real names in another ("White Queen" by Queen -> "White",
"ДОБРОГО ВЕЧОРА (WHERE ARE YOU FROM?)" -> "ДОБРОГО ВЕЧОРА", "Take The "A" Train" losing its
quotes, ":/" -> "/").

## Decision

1. **One package does all of it**: `src/utils/common/sanitize/` - `text.py` the functions (pure
   text), `pipeline.py` which step runs when, `settings.py` the owner's switches. Every source calls
   it (`sanitize_raw()` before a whole text is split into artist and title, `sanitize_field()` /
   `sanitize_metadata()` after); none cleans on its own. Comparison keys (`normalize()`,
   `name_key()`, `_title_core()`, `duplicate_key()`) are not steps and never depend on the settings.
2. **A switch is the same for every source** (the owner's choice: "so I know it works the same
   everywhere"). Settings → "Name cleanup" has 11 switches; where a step runs follows only from what
   it works on (a whole video title / file name, or one field), never from a choice per source. So
   every step must be safe for every name it can see: measured on the catalog and the library
   before it is turned on everywhere, and narrowed (not limited to one source) when it breaks a
   real name. A name typed by hand is never changed. In the database a step that changes shared or
   stored data only offers its change (Clean names' checkbox lists: look-alike letters, featured
   artists).
3. **One order, "extract first, then remove"**, which is also the menu's order: characters (HTML
   entities, wrong encodings, file names in titles, look-alike letters) → what the title says,
   kept with the song (featured artists, the year, the artist repeated in it) → removing (download
   sites, junk, quote marks) → tidying (spaces, a dangling separator). A step that keeps something
   runs before any step that could cut it.
4. **What a title says is kept, not dropped.** A song is read into a `SongEntry`
   (`src/utils/common/song_entry.py`) that remembers its source, the raw text, each field before
   the cleanup and every piece a step cut; hints (a year, a remaster, a work it is from, a track
   number, the featured artists) are read from those pieces. The entry lives **in memory only**
   (the owner's choice, no table) and is passed as an argument to what needs it right after:
   `run_import_batch()` returns the songs paired with their entries (`ImportResult`), and the end
   of an import offers filling in albums and years for exactly those songs, the years step taking a
   title's year into its review. A hint is never written without the owner's yes.
5. **Featured artists leave titles**: "Song ft. X" becomes "Song" with X credited as a featured
   artist (`additional_song_artists`), for every import and, through a checkbox list in Clean
   names, for the catalog. How it is shown is a display choice; the data says what is what.
   Matching follows: `find_existing_song()` reads a stored title without its credits and tries
   the songs already crediting the entry's guests first, so "No Scrubs" + Joanna Jones finds her
   cover, not the original.

## Consequences

- Turning a switch on reaches stored names at the next Clean names; nothing silently differs
  between an import and the database.
- A new cleanup is a step in `sanitize/pipeline.py` (with its function in `text.py`), a switch or a
  place in an existing one, a label in `constants.SANITIZE_SWITCH_LABELS`, and a measurement on the
  catalog before it is on; `tests/test_name_cleanup_characterization.py` pins what each source
  produces, every change on purpose marked "Changed:".
- Hints exist only during the run that read the song: a song stored earlier has none, and a hint
  is lost if the program is closed before the step that would use it.
- Leina Radio needs no change: the schema and stored values keep their meaning; featured artists
  were already in `additional_song_artists`, which it reads.

## Alternatives considered

- **Switches per source** (built first, then replaced): each step on or off separately for mp3,
  YouTube, requests and the database. Rejected by the owner - it lets a name and the same name
  stored be cleaned differently, and the menu said little about what a step does.
- **Storing each song's reading in a table** (`song_sources`) so a later lookup could use it.
  Rejected by the owner: the entry stays in memory and is handed on in arguments.
- **One pass "until nothing changes"** instead of a fixed order with a step listed twice where
  needed: on the catalog the two gave the same result; the fixed order is easier to reason about.
- **Leaving each source's own cleanup and only sharing the regexes**: kept the "same name, two
  spellings" problem and the lost information.
