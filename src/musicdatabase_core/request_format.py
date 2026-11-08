"""What a request file from Leina Radio looks like from the outside."""

import re

# [<user>_]<YYYY-MM-DD>_<HH-MM-SS>_<number>.db - the phone's request file names.
# The user part is a username (account_auth.USERNAME_PATTERN, which may hold
# "_"); the date after it has a fixed shape, so the split stays unambiguous.
REQUEST_NAME_RE = re.compile(
    r"^(?:(?P<user>[a-z0-9][a-z0-9_-]{1,31})_)?\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{3,9}\.db$")
SQLITE_HEADER = b"SQLite format 3\x00"
# (table, column) of music.db a phone may send a correction for: MDHS's
# inbox_field_corrections.FIELDS (tests/test_wired_vetting.py checks they match)
CORRECTABLE_FIELDS = frozenset({
    ("songs", "title"), ("songs", "title_latin"), ("songs", "album"), ("songs", "language"), ("songs", "year"),
    ("songs", "track_number"), ("songs", "disc_number"), ("songs", "youtube_video_id"),
    ("songs", "music_video_youtube_id"), ("songs", "genre"), ("songs", "kind"), ("songs", "artist_id"),
    ("artists", "name"), ("artists", "name_latin"), ("artists", "origin"), ("artists", "synonyms"),
    ("albums", "title"), ("albums", "title_latin"), ("albums", "year"), ("albums", "type"),
})
