-- A translated cover: one with words of its own in another language than the
-- original's (Mistmorn's Ukrainian "Дарма" of American Football's "Never Meant").
-- 1 = translated, NULL = it sings the original's words, or isn't a cover. Such a
-- song's lyrics and language are never looked up as the original's; a cover that
-- isn't translated takes its original's language when music.db has it. Written by
-- utils/database/covers.py, kept out of the public copy for now
-- (docs/handoffs/translated-covers-for-musicdatabaseapp.md).
ALTER TABLE songs ADD COLUMN cover_translated INTEGER;
