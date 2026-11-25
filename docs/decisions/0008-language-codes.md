# ADR-0008: A song's language is stored as ISO 639-3 codes, one code per language

- Status: Accepted (built and applied to the catalog 2026-11-25)
- Date: 2026-11-25

## Context

`songs.language` held English names ("Polish", "English / Japanese", "Instrumental", "Unknown"),
kept in one spelling each by `clean_language()`. Leina Radio reads the value as it is: its stats
"Języki", its search by language and its "languages left out of transliteration" all compared
those English strings. So a Polish-language app showed English names and could only find them by
typing the English name. Names also drifted: typos and other spellings had to be listed in
`LANGUAGE_SYNONYMS`, and the owner's own names ("Croatian (Kajkavian)", "Pseudo-Latin") were
spellings no other program knows.

## Decision

1. **The stored value is ISO 639-3 codes** (owner, 2026-11-25), several joined by " / " in order,
   the main language first (`eng / jpn`); the column, its separator and NULL = not set are
   unchanged. ISO 639-3 because it is the only part of ISO 639 with a code for every language the
   catalog has (Kashubian `csb`, Upper Sorbian `hsb`, Silesian `szl`, Kajkavian `kjv`, Čakavian
   `ckm`, Interslavic `isv`), all three lowercase letters. ISO 639-1 (`en`, `pl`) covers only about
   two thirds of them.
2. **One code per language.** A macrolanguage is stored as the macrolanguage when the catalog
   doesn't tell its members apart (`zho`, `ara`, `fas`, `mon`, `nor`, `est`, `lav`, `kur`, `yid`);
   its members' codes are read as it (`LANGUAGE_CODE_ALIASES`: `cmn` → `zho`, `nob` → `nor`).
   Languages the catalog does tell apart keep their own codes (Cantonese `yue`, Serbo-Croatian
   `hbs` beside `hrv` / `srp` / `bos` / `cnr`).
3. **ISO's special codes for what isn't a language:** `zxx` (no linguistic content) = no words,
   what was "Instrumental"; `und` (undetermined) = the imports' placeholder, what was "Unknown".
4. **ISO's local-use range (`qaa`–`qtz`) for the owner's languages ISO has no code for:** `qiv`
   invented, `qpl` pseudo-Latin, `qpd` Podlachian (`constants.LANGUAGE_NAMES`). The owner's case
   by case calls: "Berber" (Hindi Zahra) is Tachelhit `shi`, "Attic Greek" `grc`, "Ruthenian"
   (Hrdza) Rusyn `rue`, "English (Jamaican)" Jamaican Patois `jam`, "No Language" `zxx`.
5. **Codes everywhere inside MusicDatabase, names only at the edges.** The detection rules
   (`language_names.py`, the constants they use, `romanize.py`, `transliteration.py`) work in
   codes too. A name is only read (`language_code()`: what is typed, an mp3 tag, a lookup's answer,
   a correction from Leina Radio - English names, ISO 639-1 / -2 / -3 codes) or shown
   (`language_label()`: "jpn (Japanese)"). Every write still goes through `clean_language()`
   (`Song`'s validator), so only codes are stored. A name with no code is kept as written rather
   than lost.
6. **Each client shows a code in its own language.** Leina Radio keeps a code → name dictionary
   per app language and searches by code or by the localized name
   ([handoff](../handoffs/language-codes-for-musicdatabaseapp.md)). Done in the app the same day.

## Consequences

- The catalog was converted once through the ORM, after a backup and with the app lock held
  exclusively: 124 values, 6,773 songs. Manage database → "Unify language and country names"
  converts any row stored differently later.
- `songs.language`'s values are an interface: a new language needs no change in MusicDatabase,
  but Leina Radio shows a code it has no name for as the code until its dictionaries get one. A
  new local-use code needs a name on both sides.
- Leina Radio's corrections are compared as codes (`inbox_field_corrections.same_value(...,
  column)`), so a correction made from an older copy holding names still matches.
- Code that compares a language compares codes ("bel", never "Belarusian"). `romanize.py` can't
  import `language_names.py` (that one imports it), so it splits the value itself.

## Alternatives considered

- **ISO 639-1 with 639-3 only where 639-1 has none** (BCP 47's rule): shorter codes for the common
  languages, but two lengths in one column, and the owner asked for one consistent kind.
- **Keeping English names and translating them on the phone:** names drift and need a synonym list,
  and the owner's own names would be one more thing for every client to know.
- **Codes only in storage, names inside the detection rules:** fewer lines changed, but every rule
  reading a stored value would need a conversion first. Two vocabularies in one module were judged
  the riskier choice.
- **Individual languages instead of macrolanguages** (`cmn` for Chinese, `khk` for Mongolian): the
  catalog doesn't know which member a song is in. A guess would be stored as fact.
