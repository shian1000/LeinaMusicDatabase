"""What the name cleanup of each source produces, pinned when it moved into
utils/common/sanitize.py: a change in any of these outputs is a behaviour
change to review, not a refactoring. The values were taken from the code as
it was on 2026-11-01; the ones changed on purpose since say so ("Changed:")."""
from pathlib import Path

import pytest

from utils.common.normalizer import extract_unknown_data
from utils.database.name_cleanup import junk_title_fix
from utils.common.artist_credits import split_feat_credits
from utils.discoveries.import_data_from_mp3_tags import _clean_fields
from utils.youtube.import_from_playlist import build_metadata_from_item



# (channel, video title, (artist, title)) - build_metadata_from_item()
YOUTUBE_CASES = [
    ("Some Channel", "Fujii Kaze - Workin’ Hard（Official Video）", ("Fujii Kaze", "Workin’ Hard")),
    ("Some Channel", "Dizzko Bizkit aka Mejsi - Bál ⎮ 2017", ("Dizzko Bizkit aka Mejsi", "Bál")),
    ("Some Channel", "T-Series - Criminal (Full Song) Ra.One", ("T-Series", "Criminal Ra.One")),
    ("Some Channel", "Justyna Steczkowska - pejzaz_bez_ciebie.mpg", ("Justyna Steczkowska", "Pejzaz Bez Ciebie")),
    ("GOKUMON(UchikubiGokumonDoukoukai)", "打首獄門同好会「なつのうた」", ("打首獄門同好会", "なつのうた")),
    ("Release - Topic", "Mind Me", (None, "Mind Me")),
    ("Oordaya - Topic", "Mind Me (Official Audio)", ("Oordaya", "Mind Me")),
    ("Culture Beat", "Mr. Vain (Original Radio Edit)", ("Culture Beat", "Mr. Vain")),
    ("Some Channel", "OMNIMAR - The Matrix (Official Video) | darkTunes Music Group", ("OMNIMAR", "The Matrix")),
    ("Some Channel", "Artist - Nocą (Official Audio) #Zostańwdomu #KulturalnaStrefa", ("Artist", "Nocą")),
    ("Some Channel", "Herr Mannelig (animation) - Żniwa - Polish version", ("Herr Mannelig", "Żniwa - Polish version")),
    ("Some Channel", "MADUAR - Do It HD", ("MADUAR", "Do It")),
    ("Some Channel", "Artist - Do It HD (Official Video)", ("Artist", "Do It")),
    ("Some Channel", "Artist - Song 4K", ("Artist", "Song")),
    ("Some Channel", "Artist - Bad Song Shd", ("Artist", "Bad Song Shd")),
    ("Some Channel", "Artist - HD", ("Artist", "HD")),
    ("VHS-Goldie", "Hank Williams Jr. - Mr. Weatherman - 1982", ("Hank Williams Jr.", "Mr. Weatherman")),
    ("Some Channel", "Artist - Song - 5000", ("Artist", "Song - 5000")),
    ("Interscope Records",
     "'City of Stars' (Duet ft. Ryan Gosling, Emma Stone) - La La Land Original Motion Picture Soundtrack",
     ("Ryan Gosling, Emma Stone", "City of Stars")),
    ("Attic Ocean", "Glow - Lyric Video (English / French)", ("Attic Ocean", "Glow")),
    ("Some Channel", "Madonna - Music", ("Madonna", "Music")),
    ("SIAMES", 'SIAMÉS "Summer Nights" [Official Animated Video]', ("SIAMES", "Summer Nights")),
    ("Ado", "Ado - 【Ado】 unravel 歌いました", ("Ado", "unravel 歌いました")),
    ("Some Channel", 'Night Club - "Need You Tonight" (INXS cover)', ("Night Club", "Need You Tonight (INXS cover)")),
    ("Some Channel", "Sad Story (Out of Luck)", ("Some Channel", "Sad Story (Out of Luck)")),
    ("Some Channel", "Artist - Sierpień (2022)", ("Artist", "Sierpień")),
    ("Some Channel", "DISCO BANIA - BRUNETKI I BLONDYNKI (reupload HD)", ("DISCO BANIA", "BRUNETKI I BLONDYNKI")),
    ("Sony Animation", "“Golden” Official Lyric Video | KPop Demon Hunters | Sony Animation",
     ("Sony Animation", "Golden")),
    # Changed 2026-11-05: a "|" before the only " - " lists the artists, no suffix to cut
    ("FDGREC", "MIUOSH | SMOLIK | NOSPR - Wizje feat. Piotr Rogucki", ("MIUOSH | SMOLIK | NOSPR", "Wizje")),
    ("Some Channel", "Artist - Song (Official Video) (HD)", ("Artist", "Song")),
    ("Some Channel", "Artist - Song (Official Video) Official Audio", ("Artist", "Song")),
    ("Some Channel", "Artist - Song (Remix)", ("Artist", "Song (Remix)")),
    # Changed 2026-11-01: a featured artist leaves the title for feat_artists
    ("Some Channel", "Artist - Song (feat. Other)", ("Artist", "Song")),
    ("Some Channel", "Artist - Song ft. Other (Official Video)", ("Artist", "Song")),
    # Changed 2026-11-01: the artist goes only at the start before a separator,
    # or alone in a bracket ("White Queen" by Queen stays)
    ("Some Channel", "Łona - Lona Song", ("Łona", "Lona Song")),
    ("Some Channel", "AC_DC-Thunderstruck", ("AC DC", "Thunderstruck")),
    ("Some Channel", "Artist – Song【Kan/Rom/Eng Lyrics】", ("Artist", "Song")),
    # Stacked junk: one pass of the junk steps leaves some of it - a loop
    # until nothing changes would strip more (a possible later change)
    ("Some Channel", "Artist - Song Lyrics (HD) Official Video", ("Artist", "Song Lyrics")),
    ("Some Channel", "Artist - Song - 1999 (HD)", ("Artist", "Song")),
    ("Some Channel", "Artist - Song Official Video (Lyrics) - 1999 (HD)", ("Artist", "Song Official Video")),
    # Changed 2026-11-01: a separator left dangling goes
    ("Some Channel", "Artist - (Official Video)", ("Some Channel", "Artist")),
    ("Some Channel", "", ("Some Channel", "")),
    # Changed 2026-11-01: only hashtags at the end, starting with a letter
    ("Some Channel", "James Gang - Funk #49", ("James Gang", "Funk #49")),
    # Changed 2026-11-01: "_" is a space only between words of letters
    ("Some Channel", "Artist - #ВІЛЬНА_КАСА #shorts", ("Artist", "#ВІЛЬНА_КАСА")),
    # Changed 2026-11-01: no separator left dangling by a junk phrase
    ("Some Channel", "Artist - Secret Youth - Music Video", ("Artist", "Secret Youth")),
    # Changed 2026-11-01: only a quoted title is unwrapped, quotes inside stay
    ("Some Channel", 'Duke Ellington - Take The "A" Train', ("Duke Ellington", 'Take The "A" Train')),
    # Changed 2026-11-05: a sure pair of single quotes around a title goes too
    ("Some Channel", "M.O.O.N. - 'Hydrogen' [Hotline Miami Soundtrack]", ("M.O.O.N.", "Hydrogen")),
    ("Some Channel", "Artist - 'Song' (INXS cover)", ("Artist", "Song (INXS cover)")),
    ("Some Channel", "Artist - ‘Don't Stop’", ("Artist", "Don't Stop")),
    # ... and apostrophes stay
    ("Some Channel", "Artist - Rock 'n' Roll", ("Artist", "Rock 'n' Roll")),
    ("Some Channel", "Artist - 'Bout Time 'n' Stuff", ("Artist", "'Bout Time 'n' Stuff")),
    ("Some Channel", "Artist - 'Bout Time", ("Artist", "'Bout Time")),
    ("Some Channel", "The Connells - ’74–’75", ("The Connells", "’74–’75")),
    ("Some Channel", "Artist - ‘Song’ DANCE VERSION", ("Artist", "‘Song’ DANCE VERSION")),
]


@pytest.mark.parametrize("channel, title, expected", YOUTUBE_CASES)
def test_youtube_import_cleanup(channel, title, expected):
    metadata = build_metadata_from_item({"channel": channel, "title": title, "video_id": "vid"})
    assert (metadata["artist_name"], metadata["title"]) == expected


# (tags read, after _clean_fields()) - the mp3 import
MP3_CASES = [
    ({"artist_name": "Major Lazer &amp; DJ Snake", "title": "Lean On", "album": None},
     {"artist_name": "Major Lazer & DJ Snake", "title": "Lean On", "album": None}),
    ({"artist_name": "Kult", "title": "Ma³pa i ja", "album": "Wspania³oœæ"},
     {"artist_name": "Kult", "title": "Małpa i ja", "album": "Wspaniałość"}),
    # No sure sign: left for Clean names
    ({"artist_name": "Mêskie Granie", "title": "Song", "album": None},
     {"artist_name": "Mêskie Granie", "title": "Song", "album": None}),
    ({"artist_name": "y2meta.com", "title": "David Dean Burkhart Playlist", "album": "y2meta.com - Album"},
     {"artist_name": None, "title": "David Dean Burkhart Playlist", "album": "Album"}),
    ({"artist_name": "Artist", "title": "Song (greenmp3.com)", "album": ""},
     {"artist_name": "Artist", "title": "Song", "album": ""}),
    # Changed 2026-11-04: a YouTube channel's " - Topic" goes from an artist
    # (no artist of the catalog had one), a generic channel's name stays
    ({"artist_name": "Igo - Topic", "title": "Better Be Alive", "album": None},
     {"artist_name": "Igo", "title": "Better Be Alive", "album": None}),
    ({"artist_name": "Release - Topic", "title": "Mind Me", "album": None},
     {"artist_name": "Release - Topic", "title": "Mind Me", "album": None}),
    # Changed 2026-11-04: "Zespół" goes from before an artist (a YouTube channel's
    # "Zespół Hurt"; no artist of the catalog had one), unless it's part of the name
    ({"artist_name": "Zespół Hurt", "title": "Załoga G", "album": None},
     {"artist_name": "Hurt", "title": "Załoga G", "album": None}),
    ({"artist_name": "Zespół Pieśni i Tańca Śląsk", "title": "Song", "album": None},
     {"artist_name": "Zespół Pieśni i Tańca Śląsk", "title": "Song", "album": None}),
    # Changed 2026-11-01: a junk bracket goes from mp3 tags too
    # ("junk_brackets_anywhere"), it used to stay
    ({"artist_name": " Artist ", "title": "Song  Title (Official Video)", "album": None},
     {"artist_name": "Artist", "title": "Song Title", "album": None}),
    # Changed 2026-11-01: invisible characters go from mp3 tags too
    ({"artist_name": "Artist", "title": "Song\u200bTitle", "album": None},
     {"artist_name": "Artist", "title": "SongTitle", "album": None}),
    # Changed 2026-11-01: only an entity ending in ";" ("Kushi&#3" is a title)
    ({"artist_name": "Kushi", "title": "Kushi&#3", "album": "A &amp B"},
     {"artist_name": "Kushi", "title": "Kushi&#3", "album": "A &amp B"}),
    # Changed 2026-11-01: a year after a dash goes from mp3 tags too
    ({"artist_name": "Artist", "title": "Lumea Viseaza - 2002", "album": None},
     {"artist_name": "Artist", "title": "Lumea Viseaza", "album": None}),
    ({"artist_name": None, "title": None, "album": None},
     {"artist_name": None, "title": None, "album": None}),
]


@pytest.mark.parametrize("tags, expected", MP3_CASES)
def test_mp3_tag_cleanup(tags, expected):
    assert _clean_fields(dict(tags)) == expected


# (file name stem, (artist, title)) - extract_unknown_data()
FILE_NAME_CASES = [
    ("y2meta.com - Artist - Song", ("Artist", "Song")),
    ("Artist - Song (greenmp3.com)", ("Artist", "Song")),
    ("Artist - Song-AzEBH6DZJVk-160k-1645418932557", ("Artist", "Song")),
    ("Svefn-g-englar", (None, None)),
    ("Y2Mate.is - Artist - Song", ("Artist", "Song")),
    ("y2meta.com", (None, None)),
]


@pytest.mark.parametrize("stem, expected", FILE_NAME_CASES)
def test_file_name_cleanup(stem, expected):
    assert extract_unknown_data(Path(f"{stem}.mp3")) == expected


# (stored title, after Clean names' strip_trailing_junk()); an empty result
# is never saved (seek_nonsense_names()), so the fix is None then
STORED_JUNK_CASES = [
    ("Lot 《Official Music Video\n》", "Lot"),
    ("Song Music Video Official Audio", "Song"),
    ("Song 《Official Music Video》", "Song"),
    ("Song (Official Video) Official Audio", "Song"),
    ("Sad Story (Out of Luck)", "Sad Story (Out of Luck)"),
    ("Official Video", ""),
    ("(Official Video)", ""),
    ("Song [Lyrics]", "Song [Lyrics]"),
    ("Song (Live)", "Song (Live)"),
    ("Song HD", "Song"),
    # Changed 2026-11-01: no separator left dangling
    ("Secret Youth - Music Video", "Secret Youth"),
]


@pytest.mark.parametrize("title, expected", STORED_JUNK_CASES)
def test_clean_names_junk_cleanup(title, expected):
    assert junk_title_fix(title) == (expected if expected and expected != title else None)


# (requested title, (title, featured artists)) - a song request's metadata()
REQUEST_FEAT_CASES = [
    ("Billionaire ft. Bruno Mars", ("Billionaire", ["Bruno Mars"])),
    ("Song (feat. A & B) (Remix)", ("Song (Remix)", ["A", "B"])),
    ("Song", ("Song", [])),
    ("ft. Bruno Mars", ("ft. Bruno Mars", [])),
]


@pytest.mark.parametrize("title, expected", REQUEST_FEAT_CASES)
def test_song_request_feat_split(title, expected):
    assert split_feat_credits(title) == expected
