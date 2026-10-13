-- What a tag is for. 'mood' = a global mood ("Nastrój" on the phone): a set
-- of songs with the same feel, which every user sees and the phone plays
-- shuffled. 'marker' = everything else, in particular the technical
-- 'spellchecked' / 'album_checked' markers, which must never show up as
-- moods. The phone reads only kind = 'mood'. Later kinds (e.g. genre) are
-- added the same way. tags.name stays unique across kinds, so a mood can't
-- share its name with a marker (utils/database/moods.py refuses it).
ALTER TABLE tags ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'marker';
