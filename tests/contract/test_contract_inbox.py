"""PUT /inbox/<name>: each answer here decides what the phone's outbox does with
the file (lib/src/data/outbox.dart in the app): 200 / 201 sent, 400 / 409 / 411 /
413 kept aside and never sent again, 401 log in again, 403 wait for that user."""

import pytest

from contract_helpers import answer_to_a_body_never_sent, request_file

NAME = "2026-11-06_10-00-00_{:03d}.db"


def test_a_signed_request_is_stored_and_sending_it_again_is_harmless(server, tmp_path):
    user, token = server.new_user()
    data = request_file(tmp_path)
    path = f"/inbox/{user}_{NAME.format(1)}"
    assert server.call("PUT", path, token, raw=data).status == 201
    assert server.call("PUT", path, token, raw=data).status == 200


def test_another_file_under_a_taken_name_is_a_409(server, tmp_path):
    user, token = server.new_user()
    path = f"/inbox/{user}_{NAME.format(2)}"
    assert server.call("PUT", path, token, raw=request_file(tmp_path, 1)).status == 201
    assert server.call("PUT", path, token, raw=request_file(tmp_path, 2)).status == 409


@pytest.mark.parametrize("name", ["notes.db", "2026-11-06_10-00-00.db", "../up.db", "%2e%2e%2fup.db",
                                  "2026-11-06_10-00-00_001.txt", "A_2026-11-06_10-00-00_001.db"])
def test_a_bad_name_is_a_400(server, tmp_path, name):
    _, token = server.new_user()
    assert server.call("PUT", f"/inbox/{name}", token, raw=request_file(tmp_path)).status == 400


def test_a_file_that_isnt_sqlite_is_a_400(server):
    user, token = server.new_user()
    assert server.call("PUT", f"/inbox/{user}_{NAME.format(3)}", token, raw=b"plain text").status == 400


def test_over_a_mebibyte_is_a_413_on_the_headers_alone(server):
    user, token = server.new_user()
    status = answer_to_a_body_never_sent(server, "PUT", f"/inbox/{user}_{NAME.format(4)}",
                                         {"Authorization": f"Bearer {token}"}, length=1024 * 1024 + 1)
    assert status == 413


def test_without_a_length_is_a_411(server):
    user, token = server.new_user()
    connection = server.connection()
    connection.putrequest("PUT", f"/inbox/{user}_{NAME.format(5)}")
    connection.putheader("Authorization", f"Bearer {token}")
    connection.endheaders()
    assert connection.getresponse().status == 411
    connection.close()


def test_a_signed_name_needs_that_users_token(server, tmp_path):
    user, _ = server.new_user()
    _, other_token = server.new_user()
    path = f"/inbox/{user}_{NAME.format(6)}"
    data = request_file(tmp_path)
    assert server.call("PUT", path, raw=data).status == 401
    assert server.call("PUT", path, "not-a-token", raw=data).status == 401
    assert server.call("PUT", path, other_token, raw=data).status == 403


def test_the_token_is_checked_before_the_body_is_read(server):
    user, _ = server.new_user()
    assert answer_to_a_body_never_sent(server, "PUT", f"/inbox/{user}_{NAME.format(7)}") == 401


def test_requests_can_never_be_downloaded(server, tmp_path):
    user, token = server.new_user()
    path = f"/inbox/{user}_{NAME.format(8)}"
    assert server.call("PUT", path, token, raw=request_file(tmp_path)).status == 201
    assert server.call("GET", path, token).status == 404


@pytest.mark.requires("unsigned_uploads")
def test_an_unsigned_request_is_taken_up_to_a_limit_with_retry_after(fresh_server, tmp_path):
    for number in range(30):
        path = f"/inbox/{NAME.format(number + 100)}"
        assert fresh_server.call("PUT", path, raw=request_file(tmp_path, number)).status == 201
    answer = fresh_server.call("PUT", f"/inbox/{NAME.format(200)}", raw=request_file(tmp_path, 200))
    assert answer.status == 429
    assert int(answer.headers["Retry-After"]) > 0
