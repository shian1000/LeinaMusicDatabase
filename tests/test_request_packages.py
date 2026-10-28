"""Request packages (utils/database/request_packages.py): a .zip of Leina
Radio's outbox files imported into the inbox's new/ - only what the sharing
server would take over HTTP, the whole package refused otherwise (folders,
"..", absolute paths, symlinks, bad names, non-SQLite, size and count caps
against zip bombs), a file already in the inbox skipped, and the typed sender
name never acting as an account (packages.json, RequestFile.sender, the
automatic pass's note, the review's file label)."""

import json
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from utils.database import inbox_requests as pr
from utils.database import request_packages as rp

SQLITE = b"SQLite format 3\x00" + b"\x00" * 100
NAME = "ania_2026-10-21_12-00-00_001.db"
ANON = "2026-10-21_12-00-01_002.db"


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    (inbox / "new").mkdir(parents=True)
    monkeypatch.setattr(pr, "INBOX_DIR", inbox)
    monkeypatch.setattr(pr, "inbox_available", lambda: True)
    return inbox


def _zip(tmp_path, entries, name="package.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for entry in entries:
            if isinstance(entry, zipfile.ZipInfo):
                z.writestr(entry, b"x")
            else:
                z.writestr(*entry)
    return path


def _request_file(path: Path, user_name=None) -> bytes:
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute("CREATE TABLE request (format INTEGER, created_at TEXT, user_name TEXT, app TEXT)")
        conn.execute("INSERT INTO request VALUES (1, '2026-10-21T12:00:00+02:00', ?, 'Leina Radio')", (user_name,))
    conn.close()
    return path.read_bytes()


def test_package_files_go_into_new_and_are_recorded(inbox, tmp_path):
    data = _request_file(tmp_path / "r.db", "ania")
    package = _zip(tmp_path, [(NAME, data), (ANON, SQLITE)])

    result = rp.import_package(package)

    assert sorted(result.added) == sorted([NAME, ANON]) and result.already_there == []
    assert (inbox / "new" / NAME).read_bytes() == data
    assert not list((inbox / "new").glob(".*"))
    recorded = json.loads((inbox / "packages.json").read_text())
    assert recorded[NAME]["sender"] == "ania" and recorded[NAME]["package"] == "package.zip"
    assert recorded[ANON]["sender"] is None


def test_an_imported_files_name_is_no_login(inbox, tmp_path):
    rp.import_package(_zip(tmp_path, [(NAME, _request_file(tmp_path / "r.db", "ania"))]))
    request = pr.read_request_file(inbox / "new" / NAME)
    assert request.error is None
    assert request.sender is None  # never ania's account, nor its permissions
    assert request.package["sender"] == "ania"
    assert pr.describe_files([NAME, "other_2026-10-21_12-00-00_003.db"]) == (
        f"{NAME} (from ania, imported from a package), other_2026-10-21_12-00-00_003.db")
    # A file sent over HTTP by a logged-in user still is
    (inbox / "new" / "other_2026-10-21_12-00-00_003.db").write_bytes(SQLITE)
    assert pr.read_request_file(inbox / "new" / "other_2026-10-21_12-00-00_003.db").sender == "other"


def test_the_automatic_pass_never_saves_a_packages_correction(inbox, tmp_path):
    from utils.database import inbox_auto as ia

    rp.import_package(_zip(tmp_path, [(NAME, SQLITE)]))

    class Case:
        class latest:
            file_name = NAME

    note = ia.field_correction_verdict(None, Case, None, False)
    assert note == "imported from a request package - its sender name isn't a login"


def test_a_package_imported_twice_skips_what_is_there(inbox, tmp_path):
    package = _zip(tmp_path, [(NAME, SQLITE)])
    rp.import_package(package)
    (inbox / "processed").mkdir()
    (inbox / "new" / NAME).rename(inbox / "processed" / NAME)

    result = rp.import_package(package)

    assert result.added == [] and result.already_there == [NAME]
    assert not (inbox / "new" / NAME).exists()


def test_a_file_rejected_as_unreadable_can_come_back_in_a_package(inbox, tmp_path):
    # SRV-35: the server took such a file again, a package skipped it as "already there"
    (inbox / "rejected").mkdir()
    (inbox / "rejected" / NAME).write_bytes(b"half a file")

    result = rp.import_package(_zip(tmp_path, [(NAME, SQLITE)]))

    assert result.added == [NAME] and result.already_there == []
    assert (inbox / "new" / NAME).read_bytes() == SQLITE


def _symlink(name):
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("entries, why", [
    ([("folder/" + NAME, SQLITE)], "in a folder"),
    ([(zipfile.ZipInfo("folder/"), None)], "in a folder"),
    ([("../" + NAME, SQLITE)], "in a folder"),
    ([("/" + NAME, SQLITE)], "in a folder"),
    ([("..x_2026-10-21_12-00-00_001.db", SQLITE)], "isn't a plain file name"),
    ([(_symlink(NAME), None)], "symbolic link"),
    ([("notes.txt", b"hi")], "isn't a Leina Radio request file name"),
    ([("Ania_2026-10-21_12-00-00_001.db", SQLITE)], "isn't a Leina Radio request file name"),
    ([(NAME, b"not sqlite at all")], "isn't an SQLite file"),
    ([(NAME, SQLITE), (NAME, SQLITE)], "twice"),
    ([(NAME, SQLITE + b"\x00" * (1024 * 1024))], "bigger than"),
    ([], "empty"),
])
def test_a_bad_entry_refuses_the_whole_package(inbox, tmp_path, entries, why):
    entries = [e[0] if isinstance(e, tuple) and isinstance(e[0], zipfile.ZipInfo) else e for e in entries]
    good = [] if why == "twice" or not entries else [(ANON, SQLITE)]
    package = _zip(tmp_path, good + entries)
    with pytest.raises(rp.PackageError, match=why):
        rp.import_package(package)
    assert list((inbox / "new").iterdir()) == []
    assert not (inbox / "packages.json").exists()


def test_not_a_zip_is_refused(inbox, tmp_path):
    path = tmp_path / "package.zip"
    path.write_bytes(SQLITE)
    with pytest.raises(rp.PackageError, match="isn't a .zip"):
        rp.import_package(path)


def test_caps_on_count_and_total_size(inbox, tmp_path, monkeypatch):
    names = [f"2026-10-21_12-00-00_{i:03d}.db" for i in range(3)]
    package = _zip(tmp_path, [(n, SQLITE) for n in names])
    monkeypatch.setattr(rp, "REQUEST_PACKAGE_MAX_FILES", 2)
    with pytest.raises(rp.PackageError, match="at most 2"):
        rp.import_package(package)
    monkeypatch.setattr(rp, "REQUEST_PACKAGE_MAX_FILES", 10)
    monkeypatch.setattr(rp, "REQUEST_PACKAGE_MAX_BYTES", len(SQLITE) * 2)
    with pytest.raises(rp.PackageError, match="together are bigger"):
        rp.import_package(package)


def test_a_lying_size_in_the_zip_is_caught_while_reading(inbox, tmp_path, monkeypatch):
    package = _zip(tmp_path, [(NAME, SQLITE + b"\x00" * 5000)])
    monkeypatch.setattr(rp, "PHONE_REQUEST_MAX_BYTES", 1000)
    real_check = rp._check_entry

    def trusting_check(info, seen):
        info.file_size = 10  # as a crafted zip would claim
        real_check(info, seen)

    monkeypatch.setattr(rp, "_check_entry", trusting_check)
    # zipfile stops at the claimed size, so the CRC fails - refused either way
    with pytest.raises(rp.PackageError, match="bigger than 1000|Bad CRC"):
        rp.read_package(package)


def test_a_full_inbox_takes_nothing(inbox, tmp_path, monkeypatch):
    (inbox / "review").mkdir()
    (inbox / "review" / ANON).write_bytes(SQLITE)
    monkeypatch.setattr(rp, "PHONE_INBOX_LIMIT_BYTES", len(SQLITE) + 10)
    with pytest.raises(rp.PackageError, match="inbox is full"):
        rp.import_package(_zip(tmp_path, [(NAME, SQLITE)]))
    assert list((inbox / "new").iterdir()) == []


def test_no_drive_no_import(inbox, tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "inbox_available", lambda: False)
    with pytest.raises(rp.PackageError, match="inbox isn't available"):
        rp.import_package(_zip(tmp_path, [(NAME, SQLITE)]))
