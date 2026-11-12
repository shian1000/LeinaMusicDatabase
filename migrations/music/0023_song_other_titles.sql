-- Other titles of a song, ";"-separated: the title in other languages ("Херувимская
-- песнь" = "Hymn of the Cherubim"), a wrong title people know it by, a line of its
-- lyrics ("Lubiła tańczyć" for "Na jednej z dzikich plaż"). Search and matching
-- count each as one more title. Written by utils/database/other_titles.py, read by
-- the MusicDatabaseApp phone client (docs/handoffs/other-titles-for-musicdatabaseapp.md).
ALTER TABLE songs ADD COLUMN other_titles TEXT;
