# Handoff for the MusicDatabaseApp agent: languages are ISO 639-3 codes

Written by the MusicDatabase agent (2026-11-25), at the owner's request. **Status:** done in the
app the same day (MusicDatabaseApp `3cf3212`, its dictionaries holding every code below); the
sharing copy with codes went out right after. An older app shows "pol" where it showed "Polish".

## What changed in music.db

`songs.language` holds **ISO 639-3 codes** now, not English names (owner's decision,
2026-11-25: one code per language). Same column, no migration, same separator:

- One language: `pol`. Several, in order, the main one first: `eng / jpn` (split on `/` and trim,
  as before).
- `zxx` = no words, what was `Instrumental` (and the one `No Language`).
- `und` = not known, what was `Unknown` (the imports' placeholder; ignore it as before).
- NULL = not set, as before.
- Always lowercase, three letters. Macrolanguages are stored as the macrolanguage: `zho`
  (Chinese, with Cantonese `yue` apart), `ara`, `fas`, `mon`, `nor`, `est`, `lav`, `kur`, `yid`,
  `hbs` (Serbo-Croatian, with `hrv` / `srp` / `bos` / `cnr` apart).
- ISO has no code for three of the owner's languages, so they use its local-use range
  (`qaa`–`qtz`): `qiv` = invented language (Björk, Adiemus), `qpl` = pseudo-Latin (Era, Vangelis),
  `qpd` = Podlachian. No other program knows these; they need names in the app's dictionaries
  like any other code.

Every stored value was converted on 2026-11-25 (6,773 songs). Every later write goes through
MusicDatabase's `clean_language()`, so only codes reach the column.

## What the app should do

1. **Show each code's name in the app's language.** In Polish, the Polish name; in English, the
   English name. Each app language needs a dictionary code → name (the table below holds every
   code music.db uses today). A code missing from the dictionary is shown as the code itself.
   Several languages: the names joined as now ("Angielski / Japoński").
2. **Search by language** finds a song by its code **and** by the language's name in the app's
   language: in Polish, both `jpn` and `japoński` find the Japanese songs. In English, `jpn` and
   `Japanese`. Matching suggestion: case-insensitive, diacritics folded (`japonski` too), a part of
   the name enough (`jap`), against each of the song's codes. Use the same dictionary.
3. **Listening stats "Języki"**: group by code, show the name. Skip `und` instead of `Unknown`.
4. **Places that compare the old names**, found by grep (2026-11-25), to switch to codes:
   - `lib/src/data/lyrics.dart`: `'Instrumental'` → `'zxx'`.
   - `lib/src/data/song_radio.dart`, `listening_stats.dart`, `song_catalog.dart`: `'Unknown'` →
     `'und'`.
   - `lib/src/data/latin_names.dart`: `nonLatinLanguages` by names → the codes (`alt`, `ara`, `hye`,
     `grc`, `bel`, `bul`, `yue`, `zho`, `chu`, `kat`, `ell`, `heb`, `hin`, `kan`, `kas`, `kaz`,
     `kor`, `mkd`, `mon`, `fas`, `pan`, `rus`, `rue`, `srp`, `tat`, `tyv`, `ukr`, `yid`, and `jpn`).
   - The saved setting "Języki wyłączone z transliteracji" (`theme_controller.dart`) holds names:
     convert them once with the table below, or the owner's choice is lost.
   - Saved queues (`queue_session.dart`) and anything else cached with a track's language: read an
     old name as its code.
   - The lyrics transliteration (`offersLyricsTransliteration(language)`) if it keys on names.
5. **Field corrections of `("songs", "language")`**: send codes (`jpn`, `eng / jpn`). A language
   picker with the app's names is the friendly way. MusicDatabase still accepts English names and
   ISO 639-1 / -2 codes there (`Polish`, `pl`, `pol` are all stored as `pol`), and compares a
   correction's old and new value as codes, so a correction made from an older copy (old value
   "Polish") is still matched.

## The codes in music.db today (2026-11-25)

Names to put into the app's dictionaries. The English names are the ones MusicDatabase shows. The
Polish ones are a suggestion from pycountry's Polish translation, tidied by hand. Ask the owner if
any should read differently. The number is how many songs have the code.

| Code | English | Polski | Songs |
|---|---|---|---|
| `alt` | Altai | Ałtajski | 1 |
| `ara` | Arabic | Arabski | 7 |
| `bel` | Belarusian | Białoruski | 161 |
| `bos` | Bosnian | Bośniacki | 23 |
| `bre` | Breton | Bretoński | 1 |
| `bul` | Bulgarian | Bułgarski | 22 |
| `cat` | Catalan | Kataloński | 4 |
| `ces` | Czech | Czeski | 28 |
| `chu` | Church Slavonic | Cerkiewnosłowiański | 1 |
| `ckm` | Croatian (Čakavian) | Chorwacki (czakawski) | 1 |
| `crh` | Crimean Tatar | Krymskotatarski | 1 |
| `csb` | Kashubian | Kaszubski | 12 |
| `cym` | Welsh | Walijski | 1 |
| `dan` | Danish | Duński | 1 |
| `deu` | German | Niemiecki | 66 |
| `dua` | Duala | Duala | 2 |
| `ell` | Greek | Grecki | 2 |
| `eng` | English | Angielski | 3463 |
| `est` | Estonian | Estoński | 6 |
| `fao` | Faroese | Farerski | 1 |
| `fas` | Persian | Perski | 5 |
| `fil` | Filipino | Filipiński | 1 |
| `fin` | Finnish | Fiński | 16 |
| `fra` | French | Francuski | 40 |
| `fro` | Old French | Starofrancuski | 1 |
| `gaa` | Ga | Ga | 1 |
| `gcf` | Guadeloupean Creole | Kreolski gwadelupski | 1 |
| `gla` | Scottish Gaelic | Szkocki gaelicki | 1 |
| `gle` | Irish | Irlandzki | 1 |
| `grc` | Ancient Greek | Grecki starożytny | 1 |
| `hbs` | Serbo-Croatian | Serbsko-chorwacki | 37 |
| `heb` | Hebrew | Hebrajski | 2 |
| `hin` | Hindi | Hindi | 9 |
| `hrv` | Croatian | Chorwacki | 87 |
| `hsb` | Upper Sorbian | Górnołużycki | 8 |
| `hun` | Hungarian | Węgierski | 9 |
| `hye` | Armenian | Ormiański | 1 |
| `isl` | Icelandic | Islandzki | 8 |
| `isv` | Interslavic | Międzysłowiański | 1 |
| `ita` | Italian | Włoski | 21 |
| `jam` | Jamaican Patois | Jamajski patois | 2 |
| `jpn` | Japanese | Japoński | 221 |
| `kan` | Kannada | Kannada | 1 |
| `kas` | Kashmiri | Kaszmirski | 3 |
| `kat` | Georgian | Gruziński | 2 |
| `kaz` | Kazakh | Kazachski | 5 |
| `kea` | Cape Verdean Creole | Kreolski Zielonego Przylądka | 1 |
| `kjv` | Croatian (Kajkavian) | Chorwacki (kajkawski) | 9 |
| `kor` | Korean | Koreański | 8 |
| `kur` | Kurdish | Kurdyjski | 1 |
| `lat` | Latin | Łaciński | 5 |
| `lav` | Latvian | Łotewski | 1 |
| `lit` | Lithuanian | Litewski | 74 |
| `mkd` | Macedonian | Macedoński | 13 |
| `mon` | Mongolian | Mongolski | 7 |
| `mri` | Maori | Maoryski | 1 |
| `nds` | Low German | Dolnoniemiecki | 1 |
| `nld` | Dutch | Niderlandzki | 1 |
| `non` | Old Norse | Staronordyjski | 1 |
| `nor` | Norwegian | Norweski | 5 |
| `pan` | Punjabi | Pendżabski | 2 |
| `pcm` | Nigerian Pidgin | Pidżyn nigeryjski | 1 |
| `pol` | Polish | Polski | 1164 |
| `por` | Portuguese | Portugalski | 21 |
| `qiv` | Invented | Wymyślony | 5 |
| `qpd` | Podlachian | Podlaski | 4 |
| `qpl` | Pseudo-Latin | Pseudołacina | 3 |
| `rom` | Romani | Romski | 2 |
| `ron` | Romanian | Rumuński | 39 |
| `rue` | Rusyn | Rusiński | 1 |
| `rus` | Russian | Rosyjski | 207 |
| `sco` | Scots | Scots | 3 |
| `shi` | Tachelhit | Tachelhit | 1 |
| `slk` | Slovak | Słowacki | 13 |
| `slv` | Slovenian | Słoweński | 25 |
| `sot` | Sotho | Sotho | 1 |
| `spa` | Spanish | Hiszpański | 51 |
| `srp` | Serbian | Serbski | 37 |
| `sux` | Sumerian | Sumeryjski | 1 |
| `swe` | Swedish | Szwedzki | 10 |
| `szl` | Silesian | Śląski | 2 |
| `tat` | Tatar | Tatarski | 1 |
| `tgl` | Tagalog | Tagalski | 3 |
| `tur` | Turkish | Turecki | 12 |
| `twi` | Twi | Twi | 2 |
| `tyv` | Tuvan | Tuwiński | 1 |
| `ukr` | Ukrainian | Ukraiński | 210 |
| `und` | Unknown | Nieznany | 10 |
| `vie` | Vietnamese | Wietnamski | 1 |
| `wol` | Wolof | Wolof | 1 |
| `yid` | Yiddish | Jidysz | 5 |
| `yor` | Yoruba | Joruba | 1 |
| `yue` | Cantonese | Kantoński | 1 |
| `zho` | Chinese | Chiński | 5 |
| `zul` | Zulu | Zuluski | 3 |
| `zxx` | Instrumental | Instrumentalny (bez słów) | 558 |

Other ISO 639-3 codes can appear later (a lookup finds a new language). The app should show them
as the code until its dictionaries have them. The full list with English names is ISO's
`iso-639-3.tab` (SIL). Polish names for most codes are in the Debian `iso-codes` translations,
which pycountry ships.

To turn the old values the app may still hold into codes, use the English column above plus these
old names: `Instrumental` / `No Language` → `zxx`, `Unknown` → `und`, `Attic Greek` → `grc`,
`Ruthenian` → `rue`, `Berber` → `shi`, `English (Jamaican)` / `Jamaican Patois` → `jam`.
