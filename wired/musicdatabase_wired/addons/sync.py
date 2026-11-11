"""The add-on "sync": users' data kept in step between their phones, and the music catalog with
its tags served to them. Not part of MDW's base, which only checks requests. It isn't loaded
unless WIRED_ADDONS names it. The rules behind each route are the shared core's."""

import os
import sqlite3
import threading
from collections.abc import Callable
from email.utils import formatdate
from http import HTTPStatus

from fastapi import Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from musicdatabase_core import account_auth, listen_sync, mood_sync, playlist_sync, user_covers
from musicdatabase_core.server_rules import (COVER_CACHE_CONTROL, GLOBAL_MOOD_COVER_MAX_BODY_BYTES,
                                             LISTEN_MAX_BODY_BYTES, plain_int)
from musicdatabase_core.sync_collections import COLLECTIONS, Collection

from ..config import Config
from ..http_tools import Refuse, _json, _text, query_int, read_body, read_json_body

SHARED_FILES = ("music.db", "tag.db")
ITEM_METHODS = ("GET", "PUT", "DELETE")
DOWNLOAD_CHUNK_BYTES = 64 * 1024


class CoverMissing(Exception):
    def __init__(self, cover: str):
        self.cover = cover


def add_routes(app, route: Callable, config: Config, authorize: Callable) -> None:
    users_dir = str(config.users_dir)
    music_db = str(config.catalog_dir / "music.db")
    # Storing a blob, checking a cover exists + the write naming it, and the
    # clean-up never interleave, so a clean-up can't remove a blob between
    # the check and the commit
    covers_lock = threading.Lock()

    def open_user_db(connect: Callable, username: str, log_name: str):
        try:
            return connect(os.path.join(users_dir, username, playlist_sync.USER_DB_FILENAME))
        except playlist_sync.NotMigratedError as exc:
            print(f"{log_name}: {exc}", flush=True)
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE, "this user's database isn't ready yet") from None
        except (OSError, sqlite3.Error) as exc:
            print(f"{log_name}: {exc}", flush=True)
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None

    def require_cover(folder: str, cover) -> None:
        if cover is playlist_sync.KEEP_COVER:
            return
        try:
            user_covers.require_blob(folder, cover)
        except user_covers.CoverMissingError as exc:
            raise CoverMissing(exc.cover) from None

    @app.exception_handler(CoverMissing)
    async def cover_missing(request: Request, exc: CoverMissing):
        return _json(HTTPStatus.BAD_REQUEST, {"error": "cover_missing", "cover": exc.cover})

    # ---- the catalog ----

    def shared_file(name: str):
        async def serve(request: Request):
            if not config.catalog_dir.is_dir():
                raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE)
            try:
                # Files are swapped in with os.replace(), so the opened file
                # stays one complete version even if it's replaced mid-download.
                file = await run_in_threadpool(open, config.catalog_dir / name, "rb")
            except FileNotFoundError:
                raise Refuse(HTTPStatus.NOT_FOUND) from None
            except OSError:
                raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
            status = os.fstat(file.fileno())
            headers = {"Content-Length": str(status.st_size), "Cache-Control": "no-store",
                       "Last-Modified": formatdate(status.st_mtime, usegmt=True)}
            if request.method == "HEAD":
                file.close()
                return Response(status_code=200, media_type="application/vnd.sqlite3", headers=headers)

            async def chunks():
                try:
                    while chunk := await run_in_threadpool(file.read, DOWNLOAD_CHUNK_BYTES):
                        yield chunk
                finally:
                    file.close()
            return StreamingResponse(chunks(), media_type="application/vnd.sqlite3", headers=headers)
        return serve

    for name in SHARED_FILES:
        route(f"/{name}", ("GET", "HEAD"), shared_file(name), others=HTTPStatus.NOT_FOUND)

    async def profile(request: Request, user: str):
        await run_in_threadpool(authorize, request, user, "another user")
        return _json(HTTPStatus.OK, {"username": user, "permissions": account_auth.user_permissions(users_dir, user)})

    # ---- the collections ----

    def collection_handler(spec: Collection):
        async def handle(request: Request, user: str, uid: str | None = None):
            method = request.method
            if (method == "GET") != (uid is None):
                raise Refuse(HTTPStatus.NOT_FOUND)
            # The token before the body: a stranger's upload is never read
            await run_in_threadpool(authorize, request, user, f"another user's {spec.label}")
            body = await read_json_body(request, spec.max_body) if method == "PUT" else None
            if uid is not None and not spec.uid_re.match(uid):
                raise Refuse(HTTPStatus.BAD_REQUEST, f"not a uid of {spec.label}")
            if method == "GET":
                since = query_int(request, "since", default=0)
            elif method == "PUT":
                try:
                    parsed = spec.parse(body)
                    base_revision = playlist_sync.parse_base_revision(body.get("base_revision"))
                    if body.get("uid", uid) != uid:
                        raise spec.bad_error("uid: differs from the one in the path")
                except spec.bad_error as exc:
                    raise Refuse(HTTPStatus.BAD_REQUEST, str(exc)) from None
            else:
                base_revision = query_int(request, "base_revision")
                deleted_at = query_int(request, "deleted_at")

            def work() -> Response:
                folder = os.path.join(users_dir, user)
                # Before the transaction: it reads another file
                checked_to = spec.resolve(parsed, music_db) if method == "PUT" and spec.resolve else None
                conn = open_user_db(spec.connect, user, f"{spec.label} of {user}")
                try:
                    if method == "GET":
                        return _json(HTTPStatus.OK, spec.changes_since(conn, since))
                    with covers_lock:
                        if method == "PUT":
                            if spec.uses_covers:
                                require_cover(folder, parsed["cover"])
                            applied, stored = spec.store(conn, folder, user, uid, parsed, base_revision, checked_to)
                        else:
                            applied, stored, *_ = spec.delete(conn, folder, uid, base_revision, deleted_at)
                        if spec.uses_covers:
                            user_covers.clean_up_quietly(conn, folder, user)
                except spec.bad_error as exc:
                    raise Refuse(HTTPStatus.BAD_REQUEST, str(exc)) from None
                except (OSError, sqlite3.Error) as exc:
                    print(f"{spec.label} of {user}: {method} {uid} failed: {exc}", flush=True)
                    raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
                finally:
                    conn.close()
                return _json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

            return await run_in_threadpool(work)
        return handle

    for spec in COLLECTIONS:
        handler = collection_handler(spec)
        route(f"/users/{{user}}/{spec.path}", ITEM_METHODS, handler)
        route(f"/users/{{user}}/{spec.path}/{{uid}}", ITEM_METHODS, handler)

    async def listens(request: Request, user: str):
        await run_in_threadpool(authorize, request, user, "another user's listens")
        body = await read_json_body(request, LISTEN_MAX_BODY_BYTES) if request.method == "POST" else None
        try:
            if request.method == "GET":
                since = query_int(request, "since", default=0)
                limit = query_int(request, "limit", default=listen_sync.DEFAULT_LIMIT)
                if limit < 1:
                    raise listen_sync.BadListenError("limit: expected an integer >= 1")
            else:
                plays = listen_sync.parse_listens(body)
        except listen_sync.BadListenError as exc:
            raise Refuse(HTTPStatus.BAD_REQUEST, str(exc)) from None

        def work() -> Response:
            conn = open_user_db(listen_sync.connect, user, f"Listens of {user}")
            try:
                if request.method == "GET":
                    return _json(HTTPStatus.OK, listen_sync.changes_since(conn, since, limit))
                return _json(HTTPStatus.OK, {"revision": listen_sync.store_listens(conn, plays)["revision"]})
            except (OSError, sqlite3.Error) as exc:
                print(f"Listens of {user}: {request.method} failed: {exc}", flush=True)
                raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
            finally:
                conn.close()

        return await run_in_threadpool(work)

    # ---- covers ----

    async def cover_blob(request: Request, user: str, sha: str):
        await run_in_threadpool(authorize, request, user, "another user's covers")
        data = await read_body(request, user_covers.MAX_COVER_BYTES) if request.method == "PUT" else None
        if not user_covers.is_sha256(sha):
            raise Refuse(HTTPStatus.BAD_REQUEST, "expected the lowercase hex sha256 of the picture")
        folder = os.path.join(users_dir, user)

        def work() -> Response:
            if request.method == "GET":
                path = user_covers.blob_path(folder, sha)
                try:
                    picture = path.read_bytes() if path is not None else None
                except OSError:
                    picture = None  # removed by a clean-up just now
                if picture is None:
                    raise Refuse(HTTPStatus.NOT_FOUND)
                return Response(picture, media_type=user_covers.content_type_of(path),
                                headers={"Cache-Control": COVER_CACHE_CONTROL})
            try:
                with covers_lock:
                    created = user_covers.store_blob(folder, sha, data, request.headers.get("content-type"))
            except user_covers.HashMismatchError as exc:
                raise Refuse(HTTPStatus.BAD_REQUEST, str(exc)) from None
            except user_covers.UnsupportedImageError as exc:
                raise Refuse(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
            except OSError as exc:
                print(f"Storing cover {sha} of {user} failed: {exc}", flush=True)
                raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
            return _text(request, HTTPStatus.CREATED if created else HTTPStatus.OK,
                         "stored" if created else "already stored")

        return await run_in_threadpool(work)

    async def global_mood_covers(request: Request, user: str, mood: str | None = None):
        method = request.method
        if (method == "GET") != (mood is None):
            raise Refuse(HTTPStatus.NOT_FOUND)
        await run_in_threadpool(authorize, request, user, "another user's covers")
        body = await read_json_body(request, GLOBAL_MOOD_COVER_MAX_BODY_BYTES) if method == "PUT" else None
        try:
            if method == "GET":
                since = query_int(request, "since", default=0)
            else:
                if (plain_int(mood) or 0) < 1:
                    raise mood_sync.BadMoodError("expected /global-mood-covers/<mood id>")
                mood_id = int(mood)
                change = mood_sync.parse_global_cover(body)
                if body.get("mood_id", mood_id) != mood_id:
                    raise mood_sync.BadMoodError("mood_id: differs from the one in the path")
        except mood_sync.BadMoodError as exc:
            raise Refuse(HTTPStatus.BAD_REQUEST, str(exc)) from None
        folder = os.path.join(users_dir, user)

        def work() -> Response:
            conn = open_user_db(mood_sync.connect, user, f"Covers of {user}")
            try:
                if method == "GET":
                    return _json(HTTPStatus.OK, mood_sync.global_covers_since(conn, since))
                with covers_lock:
                    require_cover(folder, change["cover"])
                    applied, stored = mood_sync.store_global_cover(conn, mood_id, change["cover"],
                                                                   change["base_revision"], change["updated_at"])
                    user_covers.clean_up_quietly(conn, folder, user)
            except (OSError, sqlite3.Error) as exc:
                print(f"Covers of {user}: PUT global mood failed: {exc}", flush=True)
                raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
            finally:
                conn.close()
            return _json(HTTPStatus.OK if applied else HTTPStatus.CONFLICT, stored)

        return await run_in_threadpool(work)

    route("/users/{user}/listens", ("GET", "POST"), listens)
    route("/users/{user}/covers/{sha}", ("GET", "PUT"), cover_blob)
    route("/users/{user}/global-mood-covers", ("GET", "PUT"), global_mood_covers)
    route("/users/{user}/global-mood-covers/{mood}", ("GET", "PUT"), global_mood_covers)
    route("/users/{user}", ("GET",), profile)
