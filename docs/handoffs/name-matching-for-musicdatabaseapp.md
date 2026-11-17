# Handoff for the MusicDatabaseApp agent: recognising YouTube tracks by name

Written by the MusicDatabase agent (2026-11-17). Not an answer to a request of yours. While
looking into the owner's playlist "Gitara (2018.01)", which Leina Radio imported from YouTube and
which showed 5 tracks as not in the database, it turned out that all 5 songs were in music.db.
Please make the app-side changes below, and write back in
`docs/handoffs/musicdatabase-name-matching.md` if something doesn't fit.

**Status (2026-11-17):** MusicDatabase's side is done (below). The app side is open.

## What happened

`PlaylistLibrary._youTubeTrack()` → `matchSong()` looks a video up by
`songs.youtube_video_id` / `additional_youtube_videos` first. If that fails, it tries
`SongCatalog.findByName()` on what `parseYouTubeTrack()` read, which is exact after
`normalizeForSearch()`. All five videos failed both lookups:

| Video (channel - title) | music.db song | Why the name lookup missed |
|---|---|---|
| thericeburner - "renata przemyk bo jesli tak ma byc" (`GdeKElHtF2E`) | #378 Renata Przemyk - Bo jeśli tak ma być | The channel was taken as the artist. The title starts with a known artist's name and has no separator |
| ilona117 - "Renata Przemyk- ''Aniolek''" (`0zpO1ShAT4M`) | #381 Renata Przemyk - Aniolek | A dash with no space before it, and quotes around the title |
| Peaky Blinders - "Wonderful Life (Smith & Burrows)" (`5BX3rtvVbF0`) | #207 Smith & Burrows - Wonderful Life | The real artist is in the bracket |
| Бумбокс - "вахтерам" (`0ruoL1aHs38`) | #385 Бумбокс - Вахтёрам | ё vs е, see 1. below. No song request was sent for it either |
| 〖 AnimeSongsLуrics 〗 - "[FULL] Naruto OP 3 -Kanashimi wo Yasashisa ni- ..." (`y2juBsz78gw`) | #3944 little by little - 悲しみをやさしさに | Romanized vs Japanese title. Nothing for the app to do |

## Done in MusicDatabase

- A song request settled as "already in the database", or an import that finds an existing song,
  now saves the request's video on that song. When the song has no link, the video becomes its
  `songs.youtube_video_id`. Otherwise it becomes an `additional_youtube_videos` row with the new
  `source` value `'import'`. Before this, the video was saved nowhere, so the phone never
  recognised it, and the request had been the only signal it sent.
- The five videos above are now saved on their songs (as links for #378 and #381, as additional
  videos for the other three). The phone sees this after the next sharing copy. Nothing changes
  in the table format or the API.

## What the app should do

1. **`normalizeForSearch()` must fold what Python's `normalize()` folds.**
   `lib/src/data/text_normalizer.dart` says it matches `normalize()`, but its `_accented` /
   `_plain` tables cover only Latin letters. Python drops the combining marks after NFKD, so it
   also folds these 74 Greek and Cyrillic letters (index i → index i):

   ```
   ΆΈΉΊΌΎΏΐΪΫάέήίΰϊϋόύώϓϔЀЁЃЇЌЍЎЙйѐёѓїќѝўѶѷӁӂӐӑӒӓӖӗӚӛӜӝӞӟӢӣӤӥӦӧӪӫӬӭӮӯӰӱӲӳӴӵӸӹ
   ΑΕΗΙΟΥΩιΙΥαεηιυιυουωΥΥЕЕГІКИУИиеегікиуѴѵЖжАаАаЕеӘәЖжЗзИиИиОоӨөЭэУуУуУуЧчЫы
   ```

   There is a 75th, U+037A ͺ, which `normalize()` turns into "". Both rows above are lower-cased
   afterwards as usual. These are the main cases: ё → е (Вахтёрам = вахтерам), й → и (Летний =
   летнии; MusicDatabase's song request keys are already spelt this way), ї → і, ў → у, and Greek
   accents. Generate the table the same way as the Latin one (from `unicodedata`, the code points
   0x370-0x52F). Then check everything that stores a key made by `normalizeForSearch()`
   (`album_covers.dart`'s cache keys, `songRequestKey`, search indexes, ...): a key that changes may
   need a one-time rebuild, or an old entry will never be hit again.

2. **`parseYouTubeTrack()`: two common upload shapes**, in the app's own simple way (the phone's
   matching stays simpler than MusicDatabase's on purpose, ADR-0003):
   - *The title starts with an artist the catalog knows*, while the parsed artist is only the
     channel: "renata przemyk bo jesli tak ma byc" by "thericeburner". Try the rest of the title
     ("bo jesli tak ma byc") by that artist. Only when the start of the title is a whole catalog
     artist name (normalized, at word boundaries) and the rest is not empty. Keep it cheap: one
     pass over the artist names, not a fuzzy search.
   - *"Artist- Title"* (a dash without a space before it) and *a title in quotes*
     (`''Aniolek''`, `"…"`, `„…”`): split on the dash and drop the quotes.
   - *Optional:* the artist in a trailing bracket of a channel-named video ("Wonderful Life (Smith &
     Burrows)" by "Peaky Blinders"). Try the bracket as the artist only when it is a catalog
     artist's name.

3. **Tests** for each example above (they are the owner's real data).

The video lookup still comes first. MusicDatabase's values always win: a name match never
overrides a song the video already names.
