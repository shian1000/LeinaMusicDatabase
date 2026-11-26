# Handoff for the MusicDatabaseApp agent: lyrics in Latin letters

Written by the MusicDatabase agent (2026-11-26), answering the app's
`docs/handoffs/musicdatabase-lyrics-transliteration.md`. Built as asked, plus
one addition: alternative methods per language (so far Ukrainian's Slavic
alphabet).

## What the server sends

The same `GET /lyrics/<songs.id>` (no token, same 200 / 404 / 503). Next to
the usual fields, when MusicDatabase has a Latin form:

```json
{"song_id": 4, "plain": "Люди\nБільшість", "synced": "[00:01.00] Люди",
 "instrumental": false, "source": "lrclib_fetcher",
 "plain_latin": "Liudy\nBilshist", "synced_latin": "[00:01.00] Liudy",
 "latin_source": "romanize",
 "latin_alternatives": {"uk-slavic": {"plain": "Liudy\nBiľšisť",
                                      "synced": "[00:01.00] Liudy",
                                      "source": "romanize"}}}
```

- `plain_latin` / `synced_latin` / `latin_source`: as the app asked - line for
  line, the same time stamps, only letters of other alphabets changed. Left
  out when the text is Latin already, when any line can't be read (kanji the
  dictionary lacks, Hebrew, Kazakh, Devanagari...), and never without the
  text it stands for. Either of the two may be missing on its own (a synced
  text with no plain one).
- `latin_alternatives`: `{method: {"plain", "synced", "source"}}`, only
  methods that apply to the song. So far one:
  - `uk-slavic`: Ukrainian lyrics in the owner's Slavic alphabet - Belarusian
    Lacinka's letters (č š ž, ch, c, ja/je/ju/ji, ń ś ź ć) with Slovak's soft
    ď ť ľ, и = y, і = i, no ł: a hard л is l, a soft one ľ before a consonant
    or the end. Before a vowel softness is an i- for every consonant: ьо ->
    io, a consonant + я/ю/є -> ia/iu/ie, + ї -> i ("більшість, люди, Львів,
    сьогодні, пісня, день" -> "biľšisť, liudy, Ľviv, siohodni, pisnia, deń").
  The default `plain_latin` of Ukrainian lyrics stays the national system
  (2010) MusicDatabase uses for names; the phone calls it "Narodowa".
- The scheme is chosen once for the whole text (songs.language, the artist's
  origin, the letters), not line by line.
- Unknown methods may come later; ignore them.

## Freshness

Worked out before every copy for the phone (`share_databases()`), so a new
text, a changed language or origin shows up with the next copy, not at once.
A song whose lyrics were just added may have no Latin form until then.
