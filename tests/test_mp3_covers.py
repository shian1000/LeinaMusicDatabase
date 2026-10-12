"""Cover import from embedded mp3 pictures (utils/discoveries/mp3_covers.py):
picking the APIC frame, skipping video frames, voting per album, re-encoding,
and re-runs updating rather than duplicating. Synthetic images and ID3-only
files; the DB is in-memory SQLite."""

import io
import sys
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from utils.database import albums as al
from utils.database.datatables import AlbumCover, Artist, Base, Song
from utils.discoveries import mp3_covers as mc


def _image(width, height, seed=0, fmt="JPEG"):
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    # A pattern that differs per seed, so different seeds hash apart
    for i in range(8):
        box = [(i * width) // 8, 0, ((i + 1) * width) // 8, height]
        shade = 255 if (seed >> i) & 1 else 0
        draw.rectangle(box, fill=(shade, 255 - shade, (i * 30) % 256))
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def _mp3(path: Path, *pictures):
    path.write_bytes(b"")
    tags = ID3()
    for picture_type, data in pictures:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=picture_type, desc=str(picture_type), data=data))
    tags.save(path)
    return path


def test_pick_picture_prefers_front_cover_then_largest(tmp_path):
    small_front, big_back = _image(100, 100), _image(800, 800, seed=3)
    tags = ID3(_mp3(tmp_path / "a.mp3", (4, big_back), (3, small_front)))
    assert mc.pick_picture(tags).data == small_front
    tags = ID3(_mp3(tmp_path / "b.mp3", (0, small_front), (4, big_back)))
    assert mc.pick_picture(tags).data == big_back


def test_video_frames_are_not_square():
    assert mc.is_square(500, 500) and mc.is_square(640, 600)
    assert not mc.is_square(1280, 720) and not mc.is_square(640, 480)


def _cover(seed, size, file="x.mp3"):
    image = Image.open(io.BytesIO(_image(size, size, seed)))
    return mc.EmbeddedCover(file=Path(file), album_id=1, width=size, height=size,
                            size=size * size, dhash=mc.difference_hash(image))


def test_same_picture_at_other_sizes_votes_together():
    covers = [_cover(1, 300, "a"), _cover(1, 1200, "b"), _cover(2, 640, "c"), _cover(2, 600, "d"),
              _cover(2, 300, "e")]
    choice = mc.choose_cover(covers)
    assert [n for n, _ in choice.pictures] == [3, 2]
    assert choice.winner.file == Path("c")  # largest copy of the majority picture


def test_tie_goes_to_the_larger_picture():
    choice = mc.choose_cover([_cover(1, 300, "a"), _cover(2, 800, "b")])
    assert choice.winner.file == Path("b")


def test_encode_cover_caps_size_and_converts_to_jpeg():
    jpeg, width, height = mc.encode_cover(_image(1200, 1200, fmt="PNG"))
    assert (width, height) == (600, 600)
    assert Image.open(io.BytesIO(jpeg)).format == "JPEG"
    _, width, height = mc.encode_cover(_image(300, 300))
    assert (width, height) == (300, 300)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    al.register_album_sync(factory)
    s = factory()
    yield s
    s.close()


def _match_by_title(session):
    def match(self, metadata):
        return session.query(Song).filter(Song.title == metadata["title"]).first()
    return match


def test_import_writes_one_cover_per_album_and_reruns_update(tmp_path, session, monkeypatch):
    artist = Artist(name="Hey")
    session.add(artist)
    songs = {t: Song(title=t, artist=artist, album="Karma") for t in ("A", "B", "C")}
    songs["Solo"] = Song(title="Solo", artist=artist, album="Other")
    songs["Bare"] = Song(title="Bare", artist=artist)
    session.add_all(songs.values())
    session.commit()

    music = tmp_path / "music"
    music.mkdir()
    majority = _image(500, 500, seed=5)
    _mp3(music / "A.mp3", (3, majority))
    _mp3(music / "B.mp3", (3, _image(1000, 1000, seed=5)))
    _mp3(music / "C.mp3", (3, _image(400, 400, seed=170)))
    _mp3(music / "Solo.mp3", (3, _image(1280, 720, seed=1)))  # video frame
    _mp3(music / "Bare.mp3", (3, majority))                   # song without album
    _mp3(music / "Unknown.mp3", (3, majority))                # no such song
    _mp3(music / "Empty.mp3")

    monkeypatch.setattr(mc, "read_file_metadata", lambda f: {"artist_name": "Hey", "title": f.stem})
    monkeypatch.setattr(mc.LocalSongMatcher, "match", _match_by_title(session))
    covers = tmp_path / "covers"

    report = mc.import_covers_from_mp3(session, music, covers)
    album_id = songs["A"].album_id
    assert (report.files, report.no_image, report.not_square, report.no_album) == (7, 1, 1, 1)
    assert report.unmatched == ["Unknown.mp3"]
    assert report.albums_written == 1
    assert [a.id for a, _ in report.disagreements] == [album_id]

    stored = Image.open(covers / f"{album_id}.jpg")
    assert stored.size == (600, 600)  # B's 1000px copy of the majority picture, scaled down
    row = session.get(AlbumCover, (album_id, "file"))
    assert (row.path, row.width, row.status) == (f"{album_id}.jpg", 600, "found")
    assert row.bytes == (covers / f"{album_id}.jpg").stat().st_size

    mc.import_covers_from_mp3(session, music, covers)
    assert session.query(AlbumCover).count() == 1
    assert sorted(p.name for p in covers.iterdir()) == [f"{album_id}.jpg"]
