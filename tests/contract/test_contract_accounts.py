"""Logging in, logging out, and who a token is. Failed logins count per address
and lock it, so those tests get a server of their own."""

import pytest

from contract_helpers import PASSWORD, answer_to_a_body_never_sent


def test_login_answers_the_normalized_name_a_token_and_the_permissions(server):
    name = server.add_user(permissions=("edit_global_moods",))
    answer = server.call("POST", "/login", payload={"username": f"  {name.upper()} ", "password": PASSWORD,
                                                    "device": "Pixel 7a"})
    assert answer.status == 200
    assert answer.json["username"] == name
    assert isinstance(answer.json["token"], str) and answer.json["token"]
    assert answer.json["permissions"] == ["edit_global_moods"]
    assert answer.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("device", [None, 5, "x" * 500])
def test_login_takes_any_device_value(server, device):
    name = server.add_user()
    answer = server.call("POST", "/login", payload={"username": name, "password": PASSWORD, "device": device})
    assert answer.status == 200


@pytest.mark.parametrize("username, password", [("nobody-here", PASSWORD), ("known", "wrong-pass")])
def test_a_wrong_login_is_a_401_that_never_says_which(fresh_server, username, password):
    fresh_server.add_user("known")
    assert fresh_server.call("POST", "/login", payload={"username": username, "password": password}).status == 401


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"username": "a"}'])
def test_a_bad_login_body_is_refused(fresh_server, raw):
    assert fresh_server.call("POST", "/login", raw=raw).status == 400


def test_login_without_a_length_is_a_411(fresh_server):
    connection = fresh_server.connection()
    connection.putrequest("POST", "/login")
    connection.endheaders()
    assert connection.getresponse().status == 411
    connection.close()


def test_logout_always_answers_204(server):
    assert server.call("POST", "/logout", "not-a-token").status == 204
    assert server.call("POST", "/logout").status == 204


def test_repeated_failures_lock_out_with_retry_after(fresh_server):
    """The phone shows the wait from Retry-After, in minutes."""
    name = fresh_server.add_user()
    for _ in range(5):
        assert fresh_server.call("POST", "/login", payload={"username": name, "password": "wrong-pass"}).status == 401
    answer = fresh_server.call("POST", "/login", payload={"username": name, "password": PASSWORD})
    assert answer.status == 429
    assert int(answer.headers["Retry-After"]) > 0


@pytest.mark.requires("sync")
def test_logout_revokes_the_token_and_always_answers_204(server):
    name, token = server.new_user()
    assert server.call("GET", f"/users/{name}", token).status == 200
    assert server.call("POST", "/logout", token).status == 204
    assert server.call("GET", f"/users/{name}", token).status == 401
    assert server.call("POST", "/logout", token).status == 204
    assert server.call("POST", "/logout").status == 204


@pytest.mark.requires("sync")
def test_profile_answers_the_current_permissions(server):
    name, token = server.new_user(permissions=("edit_global_moods",))
    answer = server.call("GET", f"/users/{name}", token)
    assert answer.status == 200
    assert answer.json == {"username": name, "permissions": ["edit_global_moods"]}
    (server.users / name / "account.json").write_text(
        (server.users / name / "account.json").read_text().replace('"edit_global_moods"', ""))
    assert server.call("GET", f"/users/{name}", token).json["permissions"] == []


@pytest.mark.requires("sync")
def test_a_token_works_only_for_its_user(server):
    name, token = server.new_user()
    other, other_token = server.new_user()
    assert server.call("GET", f"/users/{name}").status == 401
    assert server.call("GET", f"/users/{name}", "not-a-token").status == 401
    assert server.call("GET", f"/users/{name}", other_token).status == 403
    assert server.call("GET", f"/users/{other}", other_token).status == 200


@pytest.mark.requires("sync")
def test_a_token_is_checked_before_the_body_is_read(server):
    name, token = server.new_user()
    other, _ = server.new_user()
    path = f"/users/{other}/playlists/a1"
    assert answer_to_a_body_never_sent(server, "PUT", path) == 401
    assert answer_to_a_body_never_sent(server, "PUT", path, {"Authorization": "Bearer not-a-token"}) == 401
    assert answer_to_a_body_never_sent(server, "PUT", path, {"Authorization": f"Bearer {token}"}) == 403
