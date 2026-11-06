"""The synced collections: the same three calls and answers for each, plus what's
particular to it. A key the phone doesn't read today may matter to a newer app, so
what's stored verbatim must come back verbatim - Pydantic would drop it by default."""

import pytest

from contract_helpers import answer_to_a_body_never_sent

UID = "8f0c1a2b-0000-4000-8000-000000000001"


def playlist(base_revision=0, updated_at=1000, **extra):
    return {"name": "Mix", "source": "youtube", "source_id": "PL1", "source_owner": "Someone",
            "created_at": 900, "updated_at": updated_at, "base_revision": base_revision,
            "tracks": [{"song_id": 2, "title": "merged", "artist": "A", "youtube_video_id": "v",
                        "spotify_track_id": None, "youtube_removed": False, "duration_s": 215},
                       {"song_id": 5, "title": "deleted"}, {"song_id": 4, "title": "kept"}], **extra}


def mood(base_revision=0, updated_at=1000, **extra):
    return {"name": "Siłownia", "created_at": 900, "updated_at": updated_at, "base_revision": base_revision,
            "song_ids": [2, 5, 4], **extra}


def folder(base_revision=0, updated_at=1000, **extra):
    return {"name": "Do auta", "created_at": 900, "updated_at": updated_at, "base_revision": base_revision,
            "deleted": False, "items": [{"type": "playlist", "uid": "a1c4"}, {"type": "global_mood", "id": 5},
                                        {"type": "future_kind", "anything": [1, {"x": "ą"}]}], **extra}


def saved_song(base_revision=0, updated_at=1000, **extra):
    return {"list": "liked", "added_at": updated_at, "updated_at": updated_at, "deleted": False,
            "base_revision": base_revision,
            "song": {"song_id": 4, "title": "Группа крови", "artist": "Кино", "video_id": "abcdefghijk"}, **extra}


def later_link(base_revision=0, updated_at=1000, **extra):
    return {"url": "https://youtu.be/abcdefghijk", "service": "youtube", "kind": "Film z YouTube", "person": "Ola",
            "saved_at": 900, "opened_at": None, "title": None, "artist": None, "updated_at": updated_at,
            "base_revision": base_revision, **extra}


COLLECTIONS = {
    "playlists": ("playlists", playlist),
    "moods": ("moods", mood),
    "folders": ("folders", folder),
    "saved-songs": ("songs", saved_song),
    "later-links": ("links", later_link),
}


@pytest.fixture(params=COLLECTIONS)
def collection(request, server):
    name, token = server.new_user()
    list_key, body = COLLECTIONS[request.param]
    return server, f"/users/{name}/{request.param}", token, list_key, body


def test_an_empty_collection(collection):
    server, base, token, list_key, _ = collection
    answer = server.call("GET", f"{base}?since=0", token)
    assert answer.status == 200
    assert answer.json == {"revision": 0, list_key: []}
    assert answer.headers["Cache-Control"] == "no-store"


def test_put_answers_the_stored_item_and_get_lists_it_since_a_revision(collection):
    server, base, token, list_key, body = collection
    answer = server.call("PUT", f"{base}/{UID}", token, body())
    assert answer.status == 200
    stored = answer.json
    assert stored["uid"] == UID and stored["revision"] == 1 and stored["updated_at"] == 1000
    assert server.call("GET", f"{base}?since=0", token).json == {"revision": 1, list_key: [stored]}
    assert server.call("GET", f"{base}?since=1", token).json == {"revision": 1, list_key: []}


def test_a_stale_put_is_a_409_with_the_stored_item(collection):
    """The phone takes a 409's body as the newer version."""
    server, base, token, _, body = collection
    stored = server.call("PUT", f"{base}/{UID}", token, body()).json
    answer = server.call("PUT", f"{base}/{UID}", token, body(base_revision=0, updated_at=500))
    assert answer.status == 409
    assert answer.json == stored


def test_a_newer_put_on_the_current_revision_replaces_it(collection):
    server, base, token, list_key, body = collection
    server.call("PUT", f"{base}/{UID}", token, body())
    answer = server.call("PUT", f"{base}/{UID}", token, body(base_revision=1, updated_at=2000))
    assert answer.status == 200
    assert answer.json["revision"] == 2 and answer.json["updated_at"] == 2000
    assert server.call("GET", f"{base}?since=1", token).json == {"revision": 2, list_key: [answer.json]}


def test_delete_answers_a_marker_and_deleting_again_is_done(collection):
    server, base, token, list_key, body = collection
    server.call("PUT", f"{base}/{UID}", token, body())
    answer = server.call("DELETE", f"{base}/{UID}?base_revision=1&deleted_at=5000", token)
    assert answer.status == 200
    marker = answer.json
    assert marker["uid"] == UID and marker["revision"] == 2 and marker["deleted"] is True
    assert marker["updated_at"] == 5000
    assert server.call("GET", f"{base}?since=1", token).json == {"revision": 2, list_key: [marker]}
    assert server.call("DELETE", f"{base}/{UID}?base_revision=0&deleted_at=1", token).json == marker


@pytest.mark.parametrize("query", ["", "?base_revision=0", "?deleted_at=1", "?base_revision=x&deleted_at=1"])
def test_delete_needs_both_numbers(collection, query):
    server, base, token, _, _ = collection
    assert server.call("DELETE", f"{base}/{UID}{query}", token).status == 400


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"{}"])
def test_a_bad_body_is_a_400(collection, raw):
    server, base, token, _, _ = collection
    answer = server.call("PUT", f"{base}/{UID}", token, raw=raw, headers={"Content-Type": "application/json"})
    assert answer.status == 400


def test_a_negative_base_revision_is_a_400(collection):
    server, base, token, _, body = collection
    assert server.call("PUT", f"{base}/{UID}", token, body(base_revision=-1)).status == 400


@pytest.mark.parametrize("query", ["?since=x", "?since=-1"])
def test_a_bad_since_is_a_400(collection, query):
    server, base, token, _, _ = collection
    assert server.call("GET", f"{base}{query}", token).status == 400


def test_without_a_length_is_a_411(collection):
    server, base, token, _, _ = collection
    connection = server.connection()
    connection.putrequest("PUT", f"{base}/{UID}")
    connection.putheader("Authorization", f"Bearer {token}")
    connection.endheaders()
    assert connection.getresponse().status == 411
    connection.close()


def test_only_its_methods_reach_it_with_allow(collection):
    server, base, token, _, _ = collection
    answer = server.call("POST", f"{base}/{UID}", token, raw=b"")
    assert answer.status == 405
    assert answer.headers["Allow"] == "GET, PUT, DELETE"


def test_tokens_are_checked_on_every_method(collection):
    server, base, _, _, body = collection
    _, other_token = server.new_user()
    for token, status in ((None, 401), ("not-a-token", 401), (other_token, 403)):
        assert server.call("GET", base, token).status == status
        assert server.call("PUT", f"{base}/{UID}", token, body()).status == status
        assert server.call("DELETE", f"{base}/{UID}?base_revision=0&deleted_at=1", token).status == status


def test_another_user_never_sees_it(server):
    name, token = server.new_user()
    other, other_token = server.new_user()
    server.call("PUT", f"/users/{name}/playlists/{UID}", token, playlist())
    assert server.call("GET", f"/users/{other}/playlists", other_token).json == {"revision": 0, "playlists": []}


# ---- what's particular to each ----

@pytest.fixture
def user(server):
    name, token = server.new_user()
    return server, name, token


def test_a_playlist_keeps_every_field_and_resolves_its_song_ids(user):
    server, name, token = user
    stored = server.call("PUT", f"/users/{name}/playlists/{UID}", token, playlist()).json
    assert [(t["song_id"], t["title"]) for t in stored["tracks"]] == [(4, "merged"), (None, "deleted"), (4, "kept")]
    first = stored["tracks"][0]
    assert first["artist"] == "A" and first["youtube_video_id"] == "v" and first["duration_s"] == 215
    assert first["youtube_removed"] is False and first["added"] is False
    assert (stored["name"], stored["source"], stored["source_id"], stored["source_owner"]) == \
        ("Mix", "youtube", "PL1", "Someone")
    assert stored["created_at"] == 900 and stored["cover"] is None and stored["excluded_source_tracks"] == []


def test_a_song_newer_than_the_shared_copy_keeps_its_id(user):
    """Added after the copy was shared, sent back by a phone before the next share."""
    server, name, token = user
    body = playlist(tracks=[{"song_id": 999, "title": "new"}, {"song_id": 3, "title": "known"}])
    stored = server.call("PUT", f"/users/{name}/playlists/{UID}", token, body).json
    assert [t["song_id"] for t in stored["tracks"]] == [999, 3]


def test_a_playlist_put_without_excluded_tracks_keeps_them(user):
    server, name, token = user
    path = f"/users/{name}/playlists/{UID}"
    server.call("PUT", path, token, playlist(excluded_source_tracks=["vX"]))
    stored = server.call("PUT", path, token, playlist(base_revision=1, updated_at=2000)).json
    assert stored["excluded_source_tracks"] == ["vX"]


def test_a_mood_resolves_its_song_ids(user):
    server, name, token = user
    stored = server.call("PUT", f"/users/{name}/moods/{UID}", token, mood()).json
    assert stored["song_ids"] == [4]
    assert stored["name"] == "Siłownia" and stored["deleted"] is False and stored["cover"] is None


def test_a_folder_keeps_items_of_kinds_it_doesnt_know(user):
    server, name, token = user
    body = folder()
    stored = server.call("PUT", f"/users/{name}/folders/{UID}", token, body).json
    assert stored["items"] == body["items"]


def test_a_folder_uid_is_limited(user):
    server, name, token = user
    assert server.call("PUT", f"/users/{name}/folders/{'a' * 65}", token, folder()).status == 400
    assert server.call("PUT", f"/users/{name}/folders/a_b", token, folder()).status == 400


def test_saved_items_come_back_with_keys_nobody_knows_yet(user):
    server, name, token = user
    body = saved_song(future_key={"a": [1, 2]})
    body["song"]["future_field"] = "ą"
    stored = server.call("PUT", f"/users/{name}/saved-songs/{UID}", token, body).json
    assert stored["future_key"] == {"a": [1, 2]}
    assert stored["song"] == body["song"]
    assert server.call("GET", f"/users/{name}/saved-songs", token).json["songs"] == [stored]


def test_a_later_link_round_trip(user):
    server, name, token = user
    body = later_link()
    stored = server.call("PUT", f"/users/{name}/later-links/{UID}", token, body).json
    expected = {key: value for key, value in body.items() if key != "base_revision"}
    assert stored == {**expected, "uid": UID, "revision": 1, "deleted": False}


def test_deleting_a_saved_item_never_seen_stores_the_marker(user):
    server, name, token = user
    answer = server.call("DELETE", f"/users/{name}/later-links/never-seen?base_revision=0&deleted_at=2000", token)
    assert answer.json == {"uid": "never-seen", "revision": 1, "updated_at": 2000, "deleted": True}


@pytest.mark.parametrize("path, body", [("saved-songs", {"base_revision": 0, "updated_at": 1, "list": "liked"}),
                                        ("later-links", {"base_revision": 0, "updated_at": 1, "saved_at": 1})])
def test_a_saved_item_needs_its_own_fields(user, path, body):
    server, name, token = user
    assert server.call("PUT", f"/users/{name}/{path}/{UID}", token, body).status == 400


# ---- listens ----

def listen(uid, listened_ms=183400, **extra):
    return {"uid": uid, "started_at": 1791839796631, "listened_ms": listened_ms, "duration_ms": 214000,
            "title": "Gruppa krovi", "artist": "Kino", "album": None, "song_id": 4,
            "youtube_video_id": "abc123", "spotify_track_id": None, "source": "youtube",
            "playlist_uid": None, "playlist_name": None, "mood_uid": None, "global_mood_id": 5,
            "mood_name": "Do biegania", **extra}


def test_listens_round_trip_in_pages(user):
    server, name, token = user
    path = f"/users/{name}/listens"
    assert server.call("GET", path, token).json == {"revision": 0, "listens": [], "more": False}
    plays = [listen("a"), listen("b"), listen("c")]
    answer = server.call("POST", path, token, {"listens": plays})
    assert answer.status == 200 and answer.json == {"revision": 3}
    assert server.call("GET", f"{path}?since=0", token).json == {
        "revision": 3, "more": False, "listens": [{**p, "revision": i} for i, p in enumerate(plays, 1)]}
    assert server.call("GET", f"{path}?since=1&limit=1", token).json == {
        "revision": 2, "more": True, "listens": [{**plays[1], "revision": 2}]}


def test_a_listen_only_gets_longer(user):
    server, name, token = user
    path = f"/users/{name}/listens"
    server.call("POST", path, token, {"listens": [listen("a", listened_ms=100)]})
    assert server.call("POST", path, token, {"listens": [listen("a", listened_ms=200)]}).json == {"revision": 2}
    assert server.call("POST", path, token, {"listens": [listen("a", listened_ms=5)]}).json == {"revision": 2}


def test_one_bad_listen_refuses_the_batch(user):
    server, name, token = user
    path = f"/users/{name}/listens"
    answer = server.call("POST", path, token, {"listens": [listen("a"), listen("b", listened_ms=-1)]})
    assert answer.status == 400
    assert server.call("GET", path, token).json["listens"] == []


def test_listens_answer_only_their_methods(user):
    server, name, token = user
    answer = server.call("PUT", f"/users/{name}/listens", token, {"listens": []})
    assert answer.status == 405 and answer.headers["Allow"] == "GET, POST"


# ---- limits ----

@pytest.mark.parametrize("path, limit", [("folders/a1", 1024 * 1024), ("saved-songs/a1", 64 * 1024),
                                         ("later-links/a1", 64 * 1024), ("playlists/a1", 8 * 1024 * 1024),
                                         ("moods/a1", 8 * 1024 * 1024)])
def test_a_body_over_its_limit_is_a_413_on_the_headers_alone(user, path, limit):
    server, name, token = user
    status = answer_to_a_body_never_sent(server, "PUT", f"/users/{name}/{path}",
                                         {"Authorization": f"Bearer {token}"}, length=limit + 1)
    assert status == 413


def test_a_user_db_not_migrated_yet_is_a_503(user):
    """The server never migrates; the phone tries again later."""
    server, name, token = user
    (server.users / name / "user.db").unlink()
    assert server.call("GET", f"/users/{name}/playlists", token).status == 503
