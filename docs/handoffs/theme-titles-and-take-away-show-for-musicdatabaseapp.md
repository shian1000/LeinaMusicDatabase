# Handoff for the MusicDatabaseApp agent: anime theme titles, Take Away Show

Written by the MusicDatabase agent (2026-11-19). The owner asked for this on the
MusicDatabase side first. Please build the app side below, and write back in
`docs/handoffs/musicdatabase-theme-titles-and-take-away-show.md` if something doesn't fit.

## Why

Two tracks of the playlist "Parsisiųsti (2018.02)" show on the Huawei as not in the database
("Utwory spoza bazy danych: 2 z 104"), though both songs are in music.db:

| Track on the phone (artist - title, video) | The song in music.db |
| --- | --- |
| Cowboy Bebop - The Real Folk Blues ending theme (`eyI635o2pmk`) | #230 SEATBELTS - The Real Folk Blues, album "Cowboy Beebop OST" |
| Blogothèque - The Dø I A Take Away Show (`qu577tNp1hA`) | #251 The Dø - A Take Away Show |

Neither video is linked to its song yet: the owner will link them **after** the app is checked,
so this test shows whether the names alone are now recognised.

## What changed in MusicDatabase

1. **Title junk:** `TITLE_JUNK_SUFFIXES` (`src/config/constants.py`) gained "opening theme",
   "ending theme", "opening song", "ending song", cut from the end of a title like "full song".
   A title that is nothing but such a phrase ("Ending Theme") now stays as it is
   (`strip_trailing_junk_suffix()`, `src/utils/common/sanitize/text.py`). With it, "Cowboy Bebop -
   The Real Folk Blues" reaches #230 through the soundtrack works rule you built yesterday
   ("Cowboy Bebop" is listed, with the alias "Cowboy Beebop" the album uses).
2. **Projects** (`src/utils/common/project_names.py`, your port: `project_names.dart`):
   - a capital "I" standing alone right before or after the project's spelling is a separator,
     as "|" is: "The Dø I A Take Away Show" -> artist "The Dø". Only next to the project - "I" is
     a word anywhere else ("Phoenix - I Wanna Be | A Take Away Show" keeps "I Wanna Be");
   - "show" / "shows" joined the whole-set words (concert, koncert, session, sesja): a title that
     is only the spelling "A Take Away Show" is the artist's whole set (`kind = 'full_album'`).
3. **Data in music.db** (no migration, rows only): a new project
   `Take Away Show` - aliases `A Take Away Show`, channels `Blogothèque, La Blogothèque`; #251 is
   now `The Dø - A Take Away Show`, project Take Away Show, `kind = 'full_album'`.

MusicDatabase's own matcher now finds both requests (checked on the real data): "Blogothèque -
The Dø I A Take Away Show" -> #251, "Cowboy Bebop - The Real Folk Blues ending theme" -> #230.
Tests: `tests/test_sanitize.py` (`test_an_anime_theme_named_by_its_place_in_the_show_is_junk`),
`tests/test_projects.py` (`test_a_take_away_show_with_an_i_typed_for_the_bar`).

## What the app should do

- Cut the same four phrases wherever the app cuts "full song" / "music video" from a title before
  matching, with the same "nothing left -> keep it" guard.
- Port both project-reading changes to `project_names.dart`.
- Then, on the Huawei with a fresh build and a freshly downloaded music.db: "Parsisiųsti
  (2018.02)" -> "Wyświetl utwory, których nie ma w bazie danych" should show 0 of 104.
