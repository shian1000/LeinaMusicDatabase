-- songs.nostalgic / melancholic / party: integer mood flags from the
-- baseline, never filled and no longer used (owner's decision, 2026-10-21) -
-- moods are tags.kind = 'mood' in tag.db now. Leina Radio never read them.
ALTER TABLE songs DROP COLUMN nostalgic;
ALTER TABLE songs DROP COLUMN melancholic;
ALTER TABLE songs DROP COLUMN party;
