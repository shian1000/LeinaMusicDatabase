-- Latin forms of names written in another alphabet ("Кино" -> "Kino"),
-- typed by the owner (or a reviewed correction from the MusicDatabaseApp
-- phone client, which shows them as "Кино (Kino)"; app handoff
-- docs/handoffs/musicdatabase-latin-names.md). Free text, no scheme imposed;
-- NULL = none yet. songs.album gets none: the phone reads the album's form
-- through songs.album_id. Search and matching count them as one more name
-- (utils/database/latin_names.py).
ALTER TABLE songs ADD COLUMN title_latin VARCHAR;
ALTER TABLE artists ADD COLUMN name_latin VARCHAR;
ALTER TABLE albums ADD COLUMN title_latin VARCHAR;
