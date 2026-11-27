# Handoff for the MusicDatabaseApp agent: names' Latin forms per method

Written by the MusicDatabase agent (2026-11-27). Today choosing
"Alternatywne metody transliteracji › Ukraiński: Słowiańska" changes only
lyrics: names have one Latin form, `artists.name_latin` (written by the
owner). music.db now also has every artist name in other alphabets worked
out per transliteration method, Ukrainian's two kept apart. With it the
Slavic choice can change artists' names too (later titles and albums, same
table). Nothing changes for the app until it reads the table.

## The table

Migration `0027_name_transliterations.sql`, in music.db (the sharing
server's copy and the public GitHub one alike):

```sql
CREATE TABLE name_transliterations (
	id          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
	table_name  TEXT NOT NULL,   -- 'artists' (later 'songs', 'albums')
	row_id      INTEGER NOT NULL, -- that row's id
	column_name TEXT NOT NULL,   -- 'name' ('title' for songs and albums)
	method      TEXT NOT NULL,   -- see below
	value       TEXT NOT NULL,   -- the Latin form, NFC
	source      TEXT NOT NULL,   -- 'romanize' or 'owner'
	UNIQUE (table_name, row_id, column_name, method)
);
```

An artist's forms:
`SELECT method, value FROM name_transliterations WHERE table_name = 'artists'
AND row_id = ? AND column_name = 'name'`.

## Methods

The same id as the lyrics' alternative where there is one:

| method | system |
|---|---|
| `uk-national` | Ukrainian, national system (2010) - what `plain_latin` of lyrics and the name_latin suggestions use; the phone's "Narodowa" |
| `uk-slavic` | Ukrainian in the owner's Slavic alphabet - the lyrics' `uk-slavic`; "Słowiańska" |
| `ru-english` | Russian, English-style ("Alyona", "Zhuki") |
| `be-lacinka` | Belarusian Łacinka |
| `bg-streamlined` | Bulgarian, streamlined system |
| `mn-mns` | Mongolian / Tuvan (MNS) |
| `sr-latin` | Serbian Latin (also Macedonian, Bosnian, Montenegrin Cyrillic) |
| `el-latin` | Greek |
| `ka-national` | Georgian, national system |
| `ko-revised` | Korean, Revised Romanization |
| `ja-hepburn` | Japanese, Hepburn |
| `zh-pinyin` | Mandarin, pinyin |
| `yue-jyutping` | Cantonese, Jyutping |

More may come. Ignore the ones you don't know.

## What's filled

- Every artist whose name has a non-Latin letter gets one row: the method of
  its system. The system is chosen like the name_latin suggestions: the
  artist's songs' most common language, then the name's letters (і ї є ґ:
  Ukrainian), then the artist's origin, else a guess.
- A Ukrainian artist gets two rows, `uk-national` and `uk-slavic`. When the
  name has a letter the Slavic alphabet doesn't cover, it gets only
  `uk-national`.
- No row when the name can't be read whole (kanji the dictionary lacks). None
  for Arabic either, since its vowels are only guessed.
- Today that's 293 rows for about 245 artists, 48 of them Ukrainian.
- Songs and albums have no rows yet.

## The values

- **`source = 'romanize'`**: worked out automatically and never checked by
  the owner. Japanese readings of kanji names in particular can be wrong.
- **`source = 'owner'`**: written by the owner. There are none yet, and
  nothing writes them so far.
- `artists.name_latin` stays the owner's chosen display form, unchanged.
  The rows are more ways of spelling the name, not a replacement.
- A sensible way to use them: when the user picked a method for a language
  and the artist has a row of that method, show it. Otherwise keep showing
  `name_latin`, as today.

## Freshness

- The automatic rows are worked out again before every copy for the phone,
  so a new artist or a changed origin or language shows up with the next
  copy.
- A renamed artist loses its automatic rows at once. The new name's rows
  come with the next copy. The owner's rows stay.
- A deleted artist's rows go with it. In a merge, the owner's rows of the
  merged-away artist move to the kept one, for methods the kept one has none
  of.
- `row_id` always names an existing row.

## On our side (nothing for you to do)

MusicDatabase's search and matching find an artist by these forms too, like
a synonym: the search, imports, YouTube scoring, the fetchers, the files
index and Leina Radio's field corrections.

The artists' `synonyms` lost their exact repeats of the name, of name_latin
or of an earlier synonym: 12 entries, such as "Okean Elzy" of Океан Ельзи,
which is still `name_latin`. A synonym is no longer needed for a Latin
spelling the table holds.
