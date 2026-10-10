# ADR-0003: The MusicDatabaseApp phone client may fill missing data locally; MusicDatabase's values win, and phone-found data will come back for review

- Status: Accepted
- Date: 2026-10-10

## Context

MusicDatabaseApp (separate Flutter repo, `../MusicDatabaseApp`) is the first mobile client. It
downloads `music.db` and `tag.db` from the sharing server (`src/utils/database/sharing_server.py`,
see [`runbooks/database.md`](../runbooks/database.md)) and reads them directly. It does not call
`src/utils/*`.

Until now this repo's docs treated mobile clients as hypothetical or read-only. The owner has
changed the app's role (its own record: `../MusicDatabaseApp/docs/decisions/0003-app-supports-musicdatabase.md`):
the phone must keep working without the server, and should *support* MusicDatabase, not only
display it.

The first concrete case is YouTube links. At the time of writing, of 5331 songs 1825 have
`songs.youtube_video_id`, 5 are `"N/A"` (`NO_VIDEO_SENTINEL`) and 3501 have nothing.

## Decision

1. **MusicDatabase stays the source of truth.** When `music.db` has a value, the app uses it as is.
2. **The phone may fill a missing value itself**, with deliberately simplified ("lite") logic of
   its own. The result lives only in the phone's own `local.db` and is dropped once a downloaded
   `music.db` has its own value. Nothing the phone derives is written into `music.db`/`tag.db`.
3. **That logic is not a port and isn't kept in sync.** For YouTube, the phone's matcher is
   inspired by `score_result()` but simpler (no yt-dlp metadata such as `track` or tags). Changing
   `score_result()` doesn't oblige a change in the app, and the app's matcher isn't a reason to
   simplify ours.
4. **The app relies on the shared files' shape**, beyond the sharing-server contract (port 8002,
   file names): the `songs` / `artists` / `additional_song_artists` schema, and how it reads
   `songs.youtube_video_id` — a video id = linked; `"N/A"` = confirmed not on YouTube, the app
   won't search; NULL or `""` = unknown, the phone may search. A schema change or a change to
   those values (especially the sentinel) must be coordinated with the app.
5. **Phone-found data will be sent back to MusicDatabase and reviewed here before it is
   accepted.** That is the intended direction only: transport and review flow aren't designed,
   and nothing is sent back today. When it is designed, it gets its own ADR (here and in the app
   repo), and the intake follows [ADR-0001](0001-ui-independent-business-logic.md): a sibling
   package of `src/menu/` calling `src/utils/*`, with the accept/reject step reusing the
   batch-review shape described in `AGENTS.md` → Coding conventions.

## Consequences

- The phone and MusicDatabase can disagree (e.g. a different video for the same song) until
  MusicDatabase has its own value. That's accepted.
- Migrations touching `songs`, `artists` or `additional_song_artists`, and any change to what
  `youtube_video_id` may contain, need a check against the app before landing.
- Backfilling `youtube_video_id` here (planned separately) shrinks the set of songs the phone has
  to guess for; it doesn't change this decision.

## Alternatives considered

- **Phone stays read-only and waits for MusicDatabase to fill gaps.** Rejected by the owner: the
  phone must be useful without the server, and 3501 songs have no link yet.
- **Phone calls an API on this machine to run the full matcher.** Rejected for now: needs the
  server reachable, which is exactly what the phone must not depend on. An API may still come
  with the review intake.
