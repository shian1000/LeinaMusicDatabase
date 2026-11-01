"""utils/common/song_entry.py: a SongEntry goes wherever the import's metadata
dict went (same keys, same equality, written through keys or attributes),
and the three places that read a song - the YouTube playlist import, an
mp3's tags, a song request from Leina Radio - say where it came from and how
it was read."""
from pathlib import Path

import pytest

from utils.common import sanitize as s
from utils.common.song_entry import METADATA_KEYS, SongEntry
from utils.youtube.import_from_playlist import build_metadata_from_item

_METADATA = {"artist_name": "A", "title": "Song", "album": None, "year": None, "language": "Unknown",
             "origin": None, "youtube_video_id": "vid", "duration_s": 200, "_label": "A - Song"}


def test_an_entry_is_the_metadata_dict():
    entry = SongEntry(_METADATA, source=s.SOURCE_YOUTUBE)
    assert entry == _METADATA and isinstance(entry, dict)
    assert {**entry} == _METADATA and type({**entry}) is dict
    assert entry.get("feat_artists") is None and entry.feat_artists is None


def test_keys_and_attributes_are_the_same_values():
    entry = SongEntry(_METADATA)
    for attribute, key in METADATA_KEYS.items():
        assert getattr(entry, attribute) == entry.get(key)
    entry.artist, entry["title"] = "B", "Other"
    assert entry["artist_name"] == "B" and entry.title == "Other"
    entry.label = "B - Other"
    assert entry["_label"] == "B - Other"


def test_cleaning_keeps_the_entry():
    entry = SongEntry({"artist_name": "A &amp; B", "title": "Song (Official Video)", "album": None},
                      source=s.SOURCE_MP3, originals={"artist": "A &amp; B"})
    cleaned = s.sanitize_metadata(entry, s.SOURCE_MP3)
    assert cleaned is entry and cleaned.artist == "A & B" and cleaned.title == "Song"
    assert cleaned.originals == {"artist": "A &amp; B"} and cleaned.source == s.SOURCE_MP3


def test_a_youtube_video_says_how_it_was_read():
    entry = build_metadata_from_item({"channel": "Some Channel", "title": "Fujii Kaze - Workin’ Hard（Official Video）",
                                      "video_id": "vid", "duration": 250})
    assert (entry.artist, entry.title, entry.duration_s) == ("Fujii Kaze", "Workin’ Hard", 250)
    assert entry.source == s.SOURCE_YOUTUBE and entry.channel == "Some Channel"
    assert entry.raw_text == "Fujii Kaze - Workin’ Hard（Official Video）"
    assert entry.parsed_by == "split Artist - Title"
    assert entry.originals == {"artist": "Fujii Kaze", "title": "Workin’ Hard"}

    topic = build_metadata_from_item({"channel": "Oordaya - Topic", "title": "Mind Me (Official Audio)", "video_id": "v"})
    assert topic.parsed_by == "Topic channel" and topic.originals == {"artist": "Oordaya", "title": "Mind Me"}
    generic = build_metadata_from_item({"channel": "Release - Topic", "title": "Mind Me", "video_id": "v"})
    assert generic.artist is None and "artist" not in generic.originals


def test_an_mp3_keeps_its_tags_as_they_were(monkeypatch, tmp_path):
    from utils.discoveries import import_data_from_mp3_tags as mp3
    file = tmp_path / "Major Lazer - Lean On.mp3"
    monkeypatch.setattr(mp3, "extract_metadata_with_fallback", lambda f: {
        "artist_name": "Major Lazer &amp; DJ Snake", "title": "Lean On (Official Video)", "album": None,
        "year": None, "language": "Unknown", "origin": None})
    monkeypatch.setattr(mp3, "audio_duration", lambda f: 176)
    entry = mp3.read_file_metadata(file)
    assert (entry.artist, entry.title, entry.duration_s) == ("Major Lazer & DJ Snake", "Lean On", 176)
    assert entry.source == s.SOURCE_MP3 and entry.file == file and entry.raw_text == "Major Lazer - Lean On"
    assert entry.originals == {"artist": "Major Lazer &amp; DJ Snake", "title": "Lean On (Official Video)"}


def test_a_song_request_keeps_what_leina_radio_sent():
    from utils.database.inbox_requests import SongRequestReport
    from utils.database.inbox_song_requests import SongRequestCase
    report = SongRequestReport(file_name="a.db", title="Billionaire ft. Bruno Mars", artist="Travie McCoy",
                               album=None, year=None, duration_s=211, youtube_video_id="v", spotify_track_id=None,
                               source="youtube", requested_at="2026-11-01T10:00:00")
    entry = SongRequestCase([report]).metadata()
    assert (entry.artist, entry.title, entry.feat_artists) == ("Travie McCoy", "Billionaire", ["Bruno Mars"])
    assert entry.source == s.SOURCE_REQUEST and entry.raw_text == "Billionaire ft. Bruno Mars"
    assert entry.originals == {"artist": "Travie McCoy", "title": "Billionaire ft. Bruno Mars"}
    # The owner's correction is what becomes the song; what was sent stays
    corrected = SongRequestCase([report]).metadata("Travie McCoy", "Billionaire (Remix)")
    assert corrected.title == "Billionaire (Remix)" and corrected.originals["title"] == "Billionaire ft. Bruno Mars"


# --- what the cleanup cut, and what it says (real titles from the catalog) ---

from utils.common.song_entry import Hints, Removed, describe_cuts  # noqa: E402


def _read(title, source=s.SOURCE_YOUTUBE, artist="Artist"):
    entry = SongEntry({"artist_name": artist, "title": title}, source=source, originals={"title": title})
    return s.sanitize_metadata(entry, source)


def test_cuts_are_kept_and_the_title_is_the_same():
    entry = _read("Legenda (1991)")
    assert entry.title == "Legenda" and entry.removed == [Removed("title_year", "title", "(1991)")]
    entry = _read("Lumea Viseaza - 2002")
    assert entry.title == "Lumea Viseaza" and entry.removed == [Removed("title_year", "title", "- 2002")]
    assert _read("Song").removed == [] and not _read("Song").hints


def test_a_video_title_keeps_what_its_raw_cleanup_cut():
    entry = build_metadata_from_item({"channel": "c", "title": "OMNIMAR - The Matrix (Official Video) | darkTunes Music Group",
                                      "video_id": "v"})
    assert entry.title == "The Matrix"
    assert [(r.step, r.field, r.text) for r in entry.removed] == [
        ("pipe_suffix", None, "| darkTunes Music Group"), ("junk_brackets_anywhere", None, "(Official Video)")]
    assert entry.hints.official_video
    assert "cut: '| darkTunes Music Group', '(Official Video)'; hints: official video" in describe_cuts(entry)


def test_a_request_keeps_the_featured_artist_it_moved():
    entry = _read("Billionaire ft. Bruno Mars", s.SOURCE_REQUEST)
    assert entry.removed == [Removed(s.FEAT_TO_CREDITS, "title", "ft. Bruno Mars")]
    assert entry.hints.featured == ["Bruno Mars"]


@pytest.mark.parametrize("title, hints", [
    ("Legenda (1991)", Hints(year=1991)),
    ("ГГПТКН [театр PipinO, 2013]", Hints(year=2013)),
    ("PARACHUTE LIMIT／パラシュート・リミット [PV] (1988, HQ)", Hints(year=1988)),
    ("Bál ⎮ 2017", Hints(year=2017)),
    ("Dołek (2015 Remastered)", Hints(remaster=2015)),
    ("5:23 (2008 Digitally Remastered Version)", Hints(remaster=2008)),
    ("One Night in Bangkok (from “Chess”)", Hints()),  # not cut since 2026-11-01: the title keeps it
    ("Easy (Baby Driver Soundtrack)", Hints(from_work="Baby Driver")),
    ("Mimiga Village (Cave Story Remastered Soundtrack)", Hints(from_work="Cave Story")),
    ("Du ferme (Clip Officiel)", Hints(official_video=True)),
    ("Secret Youth - Music Video", Hints(official_video=True)),
    ("Закладка (Lyric Video Eng)", Hints()),  # a lyric video isn't the clip
    ("Look At Me Now ft. Lil Wayne, Busta Rhymes (Cover by Karmin)", Hints(featured=["Lil Wayne", "Busta Rhymes"])),
    ("Melt (feat Kilo Kish)", Hints(featured=["Kilo Kish"])),
    ("T03: Mimiga Town", Hints(track_number=3)),
    ("1979", Hints()), ("7 Rings", Hints()), ("2:00 AM", Hints()),
])
def test_hints(title, hints):
    assert _read(title).hints == hints


# --- copies and plain dicts (the consumers read attributes) ---

def test_copy_with_keeps_where_it_came_from():
    from utils.common.song_entry import as_entry
    entry = _read("Legenda (1991)")
    entry.parsed_by = "split Artist - Title"
    copy = entry.copy_with(title="Legenda", unsplit_title=None)
    assert copy is not entry and copy["title"] == "Legenda" and copy["unsplit_title"] is None
    assert (copy.source, copy.parsed_by, copy.removed, copy.hints.year) == (entry.source, entry.parsed_by,
                                                                           entry.removed, 1991)
    copy.removed.append(Removed("x", "title", "y"))
    assert len(entry.removed) == 1  # its own list
    with pytest.raises(AttributeError):
        entry.copy_with(titel="typo")
    assert as_entry(entry) is entry
    plain = {"artist_name": "A", "title": "T"}
    assert as_entry(plain) == plain and as_entry(plain).artist == "A" and as_entry(plain) is not plain


def test_the_matcher_and_the_engine_take_a_plain_dict_too():
    from types import SimpleNamespace
    from utils.discoveries.import_engine import LocalSongMatcher, _entry_label
    assert _entry_label({"artist_name": "A", "title": "T"}) == "A - T"
    assert LocalSongMatcher._wanted({"title": "Song (Official Video)", "artist_name": "A"})[1] == "a"
    assert LocalSongMatcher._wanted(SongEntry({"title": "Song"}))[1] == ""
