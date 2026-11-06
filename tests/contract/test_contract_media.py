"""Cover pictures, global moods and the music library's files."""

import hashlib

import pytest

from contract_helpers import LIBRARY_FILE, answer_to_a_body_never_sent

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
UID = "8f0c1a2b-0000-4000-8000-000000000002"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def user(server):
    name, token = server.new_user()
    return server, name, token


def put_cover(server, name, token, data=PNG, content_type="image/png", digest=None):
    return server.call("PUT", f"/users/{name}/covers/{digest or sha(data)}", token, raw=data,
                       headers={"Content-Type": content_type})


@pytest.mark.parametrize("data, content_type", [(PNG, "image/png"), (JPEG, "image/jpeg")])
def test_a_cover_round_trip(user, data, content_type):
    server, name, token = user
    path = f"/users/{name}/covers/{sha(data)}"
    assert server.call("GET", path, token).status == 404
    assert put_cover(server, name, token, data, content_type).status == 201
    assert put_cover(server, name, token, data, content_type).status == 200
    answer = server.call("GET", path, token)
    assert answer.status == 200 and answer.body == data
    assert answer.headers["Content-Type"] == content_type
    assert "immutable" in answer.headers["Cache-Control"]


def test_bad_covers(user):
    server, name, token = user
    assert put_cover(server, name, token, digest=sha(b"other")).status == 400
    assert put_cover(server, name, token, digest=sha(PNG).upper()).status == 400
    assert put_cover(server, name, token, content_type="image/gif").status == 415
    assert put_cover(server, name, token, b"GIF89a", "image/png").status == 415
    assert put_cover(server, name, token, PNG, "image/jpeg").status == 415


def test_a_cover_over_its_limit_is_a_413_on_the_headers_alone(user):
    server, name, token = user
    status = answer_to_a_body_never_sent(server, "PUT", f"/users/{name}/covers/{sha(PNG)}",
                                         {"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
                                         length=4 * 1024 * 1024 + 1)
    assert status == 413


def test_covers_need_their_users_token(user):
    server, name, token = user
    _, other_token = server.new_user()
    put_cover(server, name, token)
    path = f"/users/{name}/covers/{sha(PNG)}"
    assert server.call("GET", path).status == 401
    assert server.call("GET", path, other_token).status == 403
    assert put_cover(server, name, None).status == 401
    assert put_cover(server, name, other_token).status == 403
    answer = server.call("DELETE", path, token)
    assert answer.status == 405 and answer.headers["Allow"] == "GET, PUT"


@pytest.mark.parametrize("collection, body", [
    ("playlists", {"name": "Mix", "created_at": 900, "updated_at": 1000, "base_revision": 0, "tracks": []}),
    ("moods", {"name": "Bieg", "created_at": 900, "updated_at": 1000, "base_revision": 0, "song_ids": []}),
])
def test_an_item_naming_a_cover_asks_for_the_picture_first(user, collection, body):
    """The phone uploads a picture only after this answer (putWithCover())."""
    server, name, token = user
    path = f"/users/{name}/{collection}/{UID}"
    named = {**body, "cover": sha(PNG)}
    answer = server.call("PUT", path, token, named)
    assert answer.status == 400
    assert answer.json["error"] == "cover_missing"

    assert put_cover(server, name, token).status == 201
    stored = server.call("PUT", path, token, named).json
    assert stored["cover"] == sha(PNG)
    # A PUT without the key keeps the stored one, null removes it
    kept = server.call("PUT", path, token, {**body, "base_revision": 1, "updated_at": 2000}).json
    assert kept["cover"] == sha(PNG)
    removed = server.call("PUT", path, token, {**body, "base_revision": 2, "updated_at": 3000, "cover": None}).json
    assert removed["cover"] is None


def test_a_bad_cover_hash_in_an_item_is_a_400(user):
    server, name, token = user
    body = {"name": "Mix", "created_at": 900, "updated_at": 1000, "base_revision": 0, "tracks": [],
            "cover": "not-a-hash"}
    assert server.call("PUT", f"/users/{name}/playlists/{UID}", token, body).status == 400


def test_global_mood_covers_sync_like_the_rest(user):
    server, name, token = user
    base = f"/users/{name}/global-mood-covers"
    assert server.call("GET", base, token).json == {"revision": 0, "covers": []}
    body = {"base_revision": 0, "updated_at": 1000, "cover": sha(PNG)}
    answer = server.call("PUT", f"{base}/1", token, body)
    assert answer.status == 400
    assert answer.json == {"error": "cover_missing", "cover": sha(PNG)}

    put_cover(server, name, token)
    stored = server.call("PUT", f"{base}/1", token, body)
    assert stored.status == 200
    assert stored.json == {"mood_id": 1, "revision": 1, "cover": sha(PNG), "updated_at": 1000}
    stale = server.call("PUT", f"{base}/1", token, {**body, "updated_at": 500, "cover": None})
    assert stale.status == 409 and stale.json == stored.json

    removed = server.call("PUT", f"{base}/1", token, {"base_revision": 1, "updated_at": 2000, "cover": None}).json
    assert removed["cover"] is None and removed["revision"] == 2
    assert server.call("GET", f"{base}?since=1", token).json == {"revision": 2, "covers": [removed]}
    assert server.call("PUT", f"{base}/1", token, {"base_revision": 0, "updated_at": 1}).status == 400


# ---- global moods ----

@pytest.fixture
def mood_editor(server):
    name, token = server.new_user(permissions=("edit_global_moods",))
    return server, name, token


@pytest.mark.requires("global_moods")
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_a_permitted_change_of_a_global_mood_is_accepted(mood_editor, method):
    server, _, token = mood_editor
    assert server.call(method, "/global-moods/1/songs/4", token, raw=b"").status == 202


@pytest.mark.requires("global_moods")
def test_a_global_mood_needs_the_permission(user):
    server, _, token = user
    answer = server.call("PUT", "/global-moods/1/songs/4", token, raw=b"")
    assert answer.status == 403
    assert answer.json == {"error": "permission", "permission": "edit_global_moods"}
    assert server.call("PUT", "/global-moods/1/songs/4", raw=b"").status == 401


@pytest.mark.requires("global_moods")
@pytest.mark.parametrize("path, answer", [("/global-moods/99/songs/4", {"error": "mood", "mood_id": 99}),
                                          ("/global-moods/2/songs/4", {"error": "mood", "mood_id": 2}),
                                          ("/global-moods/1/songs/5", {"error": "song", "song_id": 5})])
def test_a_global_mood_or_song_that_isnt_there(mood_editor, path, answer):
    server, _, token = mood_editor
    response = server.call("PUT", path, token, raw=b"")
    assert response.status == 404
    assert response.json == answer


@pytest.mark.requires("global_moods")
def test_a_merged_song_counts_as_the_kept_one(mood_editor):
    server, _, token = mood_editor
    assert server.call("PUT", "/global-moods/1/songs/2", token, raw=b"").status == 202


# ---- the library ----

@pytest.mark.requires("library")
def test_a_library_file_whole(server):
    answer = server.call("GET", "/library/1")
    assert answer.status == 200 and answer.body == LIBRARY_FILE
    assert answer.headers["Content-Type"] == "audio/mpeg"
    assert answer.headers["Accept-Ranges"] == "bytes"
    assert answer.headers["Cache-Control"] == "no-cache"
    assert int(answer.headers["Content-Length"]) == len(LIBRARY_FILE)


@pytest.mark.requires("library")
def test_a_library_head(server):
    answer = server.call("HEAD", "/library/1")
    assert answer.status == 200 and answer.body == b""
    assert int(answer.headers["Content-Length"]) == len(LIBRARY_FILE)


@pytest.mark.requires("library")
@pytest.mark.parametrize("header, first, last", [("bytes=10-19", 10, 19), ("bytes=1000-", 1000, len(LIBRARY_FILE) - 1),
                                                 ("bytes=-5", len(LIBRARY_FILE) - 5, len(LIBRARY_FILE) - 1)])
def test_a_library_range_seeks(server, header, first, last):
    """ServerAudioSource reads Content-Range as "bytes a-b/total"."""
    answer = server.call("GET", "/library/1", headers={"Range": header})
    assert answer.status == 206
    assert answer.body == LIBRARY_FILE[first:last + 1]
    assert answer.headers["Content-Range"] == f"bytes {first}-{last}/{len(LIBRARY_FILE)}"


@pytest.mark.requires("library")
def test_a_library_range_past_the_end_is_a_416(server):
    assert server.call("GET", "/library/1", headers={"Range": f"bytes={len(LIBRARY_FILE)}-"}).status == 416


@pytest.mark.requires("library")
def test_several_ranges_answer_the_whole_file(server):
    answer = server.call("GET", "/library/1", headers={"Range": "bytes=0-1,5-6"})
    assert answer.status == 200 and answer.body == LIBRARY_FILE


@pytest.mark.requires("library")
@pytest.mark.parametrize("path", ["/library/99", "/library/2", "/library/3", "/library/4", "/library/abc",
                                  "/library/0", "/library/song.mp3", "/library/"])
def test_only_an_indexed_audio_file_inside_the_library_is_served(server, path):
    assert server.call("GET", path).status == 404
