-- The song's genre(s), read by the MusicDatabaseApp phone client's song radio
-- (app handoff docs/handoffs/musicdatabase-genres.md): comma-separated, the
-- main one first ("indie pop, synth-pop"), no commas inside a genre name;
-- NULL = none known. One spelling per genre across the database - every value
-- goes through utils/database/genres.py's clean_genres() (MusicBrainz's genre
-- names). A song-level column: a band's songs can be in different genres.
ALTER TABLE songs ADD COLUMN genre VARCHAR;
