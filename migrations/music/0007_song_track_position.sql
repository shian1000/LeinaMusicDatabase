-- A song's position on its album. NULL = unknown (disc_number NULL also
-- means disc 1). Nothing fills them yet - meant for fetchers (iTunes
-- trackNumber/discNumber, Deezer track_position/disk_number, MusicBrainz).
ALTER TABLE songs ADD COLUMN track_number INTEGER;
ALTER TABLE songs ADD COLUMN disc_number INTEGER;
