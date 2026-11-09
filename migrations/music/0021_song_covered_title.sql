-- The original song's title when a cover's own differs ("Дарма" = "Never Meant"),
-- or when there's no artist to name: Христина Соловій's "Українська лють" is shown
-- "(Bella Ciao cover)", a traditional song. songs.covered_artist_id stays artists
-- only. Written by utils/database/covers.py, read by the MusicDatabaseApp phone
-- client (docs/handoffs/cover-songs-for-musicdatabaseapp.md).
ALTER TABLE songs ADD COLUMN covered_title TEXT;
