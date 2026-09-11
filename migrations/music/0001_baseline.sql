-- Baseline: schema as of adopting the migration mechanism (2026-09).
-- Documentary only -- on an existing installation this is recorded as
-- already applied without being executed (see migrations.py). It exists so
-- a brand-new baseline schema is visible in one place, and so that
-- `migrations/music/` isn't empty for the next real migration to land next
-- to.
--
-- Matches src/utils/database/datatables.py at the time this file was
-- written. If they disagree later, trust datatables.py and treat this file
-- as a historical snapshot, not as an editable source of truth -- add a new
-- migration for any further change instead of editing this one.

CREATE TABLE "artists" (
	"id"	INTEGER NOT NULL,
	"name"	VARCHAR NOT NULL,
	"origin"	VARCHAR,
	"synonyms"	VARCHAR,
	PRIMARY KEY("id")
);

CREATE TABLE "songs" (
	"id"	INTEGER NOT NULL,
	"title"	VARCHAR NOT NULL,
	"album"	VARCHAR,
	"year"	INTEGER,
	"language"	VARCHAR,
	"artist_id"	INTEGER NOT NULL,
	"nostalgic"	INTEGER,
	"melancholic"	INTEGER,
	"party"	INTEGER,
	PRIMARY KEY("id"),
	FOREIGN KEY("artist_id") REFERENCES "artists"("id")
);
