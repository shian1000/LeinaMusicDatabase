"""Featured artists move out of titles into the songs' credits (2026-11-01):
find_existing_song() reads a stored title without its "ft. X" and tries the
songs crediting the entry's guests first, the imports credit the guests to
the song they create or find, and Clean names moves the catalog's credits
(utils/database/name_cleanup.py feat_move_proposals() / apply_feat_moves())."""
from types import SimpleNamespace

from utils.common.song_entry import SongEntry
from utils.database.datatables import Artist, Song
from utils.database.song_artists import additional_artists
from utils.discoveries import import_engine as ie
from utils.youtube.import_from_playlist import build_metadata_from_item

from test_import_engine import _artist, _entry, env  # noqa: F401 - env is a fixture


def _song(title, *guests):
    links = [SimpleNamespace(artist=SimpleNamespace(name=g), role="feat") for g in guests]
    return SimpleNamespace(title=title, title_latin=None, kind=None, additional_artist_links=links)


def test_a_stored_title_is_read_without_its_credit():
    remix = _song("Song (feat. X) (Remix)")
    assert ie.find_existing_song("Song (Remix)", [remix]) is remix
    assert ie.find_existing_song("Song", [_song("Song ft. X")]).title == "Song ft. X"


def test_an_entrys_guest_finds_its_version_not_the_plain_song():
    original, cover = _song("No Scrubs"), _song("No Scrubs (Cover)", "Joanna Jones")
    old_cover = _song("No Scrubs ft. Joanna Jones (Cover)")
    assert ie.find_existing_song("No Scrubs", [original, cover], feat_artists=["Joanna Jones"]) is cover
    assert ie.find_song_loosely("No Scrubs", [original, old_cover], feat_artists=["Joanna Jones"]) is old_cover
    assert ie.find_existing_song("No Scrubs", [original, cover]) is original
    # The entry's own "ft." still tells it apart when it stayed in the title
    assert ie.find_existing_song("No Scrubs ft. Joanna Jones", [original]) is None


def test_a_video_title_gives_its_guest_as_a_featured_artist():
    entry = build_metadata_from_item({"channel": "c", "title": "Kygo - Firestone ft. Conrad Sewell (Official Video)",
                                      "video_id": "v"})
    assert (entry.artist, entry.title, entry.feat_artists) == ("Kygo", "Firestone", ["Conrad Sewell"])
    assert "ft. Conrad Sewell" in [piece.text for piece in entry.removed]


def test_the_import_credits_the_guest_to_a_new_song(env):
    session = env["session"]
    _artist(session, "Bruno Mars")
    [song] = ie.run_import_batch([SongEntry(_entry("Travie McCoy", "Billionaire", feat_artists=["Bruno Mars"]))])
    assert song.title == "Billionaire" and [a.name for a in additional_artists(song)] == ["Bruno Mars"]
    assert session.query(Artist).filter_by(name="Bruno Mars").count() == 1


def test_the_import_credits_the_guest_to_the_song_it_found(env):
    session = env["session"]
    kygo = _artist(session, "Kygo", "Firestone", "Stole The Show feat. Parson James", "Carry Me (Live)")

    added = ie.run_import_batch([
        SongEntry(_entry("Kygo", "Firestone", feat_artists=["Conrad Sewell"])),
        SongEntry(_entry("Kygo", "Stole The Show", feat_artists=["Parson James"])),
        SongEntry(_entry("Kygo", "Carry Me", feat_artists=["Julia Michaels"])),
    ])

    assert added == []
    songs = {s.title: s for s in session.query(Song).filter_by(artist_id=kygo.id)}
    assert [a.name for a in additional_artists(songs["Firestone"])] == ["Conrad Sewell"]
    assert [a.name for a in additional_artists(songs["Stole The Show feat. Parson James"])] == ["Parson James"]
    # Found only by containing the title: a "(Live)" one isn't credited
    assert additional_artists(songs["Carry Me (Live)"]) == []


def test_clean_names_moves_the_credits_out_of_the_titles(env):
    from utils.database import name_cleanup
    session = env["session"]
    _artist(session, "Akon")
    kardinal = _artist(session, "Kardinal Offishall", "Dangerous ft. Akon", "Plain Song")
    floyd = _artist(session, "Pink Floyd", "Hey Hey Rise Up (feat. Andriy Khlyvnyuk of Boombox)")
    songs = session.query(Song).filter(Song.artist_id.in_([kardinal.id, floyd.id])).order_by(Song.id).all()

    moves = name_cleanup.feat_move_proposals(session, songs)

    assert [(m.song.title, m.title, m.names, m.suspicious) for m in moves] == [
        ("Dangerous ft. Akon", "Dangerous", ["Akon"], False),
        ("Hey Hey Rise Up (feat. Andriy Khlyvnyuk of Boombox)", "Hey Hey Rise Up", ["Andriy Khlyvnyuk of Boombox"], True)]
    assert moves[0].known == {"Akon": True} and moves[1].known == {"Andriy Khlyvnyuk of Boombox": False}

    created = name_cleanup.apply_feat_moves(session, moves[:1])
    session.commit()
    dangerous = moves[0].song
    assert dangerous.title == "Dangerous" and [a.name for a in additional_artists(dangerous)] == ["Akon"]
    assert created == [] and session.query(Artist).filter_by(name="Akon").count() == 1
    assert moves[1].song.title == "Hey Hey Rise Up (feat. Andriy Khlyvnyuk of Boombox)"  # not ticked

    new = name_cleanup.apply_feat_moves(session, moves[1:])
    assert [a.name for a in new] == ["Andriy Khlyvnyuk of Boombox"]
