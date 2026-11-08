"""MDW's base: a phone logs in and hands over a request, and the request gets checked. Anything
more is an add-on (addons/), loaded only when WIRED_ADDONS names it. What Leina Player relies on
is tests/contract/.

A handler takes the raw Request, since FastAPI reads a declared body before anything else runs:
the token must be checked first, so a stranger's upload is never read."""

from collections.abc import Callable
from http import HTTPStatus

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from musicdatabase_core import account_auth
from musicdatabase_core.inbox_files import DEFAULT_MAX_REQUEST_BYTES
from musicdatabase_core.request_format import REQUEST_NAME_RE, SQLITE_HEADER
from musicdatabase_core.server_rules import LOGIN_MAX_BODY_BYTES, LoginLimiter

from . import request_folders
from .config import SYNC_ADDON, Config
from .http_tools import HostCheck, Refuse, _json, _text, bearer_token, read_body

ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
# FastAPI requires opentelemetry-api; set here, so no environment variable can turn it on
NO_TELEMETRY = {"tracing": False, "metrics": False, "logs": False, "operation_spans": False, "auto_configure": False}

INBOX_ANSWERS = {
    request_folders.STORED: (HTTPStatus.CREATED, "stored"),
    request_folders.NOTHING_TO_DO: (HTTPStatus.CREATED, "nothing left to do"),
    request_folders.ALREADY_HERE: (HTTPStatus.OK, "already received"),
    request_folders.NAME_TAKEN: (HTTPStatus.CONFLICT, "another request has this name"),
    request_folders.FULL: (HTTPStatus.INSUFFICIENT_STORAGE, "inbox full"),
}


class LoginBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    username: str
    password: str
    # Optional, so old phones log in as before; a bad value is just left out
    device: object = None


def create_app(config: Config) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, telemetry=NO_TELEMETRY)
    app.add_middleware(HostCheck, names=config.server_names)
    limiter = LoginLimiter()
    requests = request_folders.RequestFolders(config.inbox_dir, config.catalog_dir / "music.db")
    users_dir = str(config.users_dir)
    def data_available() -> bool:
        return config.users_dir.is_dir()

    def sender_of(request: Request) -> str:
        if not data_available():
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE)
        sender = account_auth.token_user(users_dir, bearer_token(request))
        if sender is None:
            raise Refuse(HTTPStatus.UNAUTHORIZED, "log in again")
        return sender

    def authorize(request: Request, username: str, forbidden: str) -> None:
        if sender_of(request) != username:
            raise Refuse(HTTPStatus.FORBIDDEN, forbidden)

    # ---- errors ----

    @app.exception_handler(Refuse)
    async def refused(request: Request, exc: Refuse):
        return _text(request, exc.status, exc.detail, exc.headers)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _text(request, HTTPStatus(exc.status_code))

    @app.exception_handler(ClientDisconnect)
    async def gone(request: Request, exc: ClientDisconnect):
        # The phone gave up mid-upload; nobody reads this
        return _text(request, HTTPStatus.BAD_REQUEST)

    def route(path: str, methods: tuple, handler: Callable, others: HTTPStatus = HTTPStatus.METHOD_NOT_ALLOWED):
        async def endpoint(request: Request):
            if request.method not in methods:
                headers = {"Allow": ", ".join(methods)} if others == HTTPStatus.METHOD_NOT_ALLOWED else None
                raise Refuse(others, headers=headers)
            return await handler(request, **request.path_params)
        app.router.add_route(path, endpoint, methods=ALL_METHODS)

    # ---- logging in ----

    async def login(request: Request):
        data = await read_body(request, LOGIN_MAX_BODY_BYTES)
        try:
            body = LoginBody.model_validate_json(data)
        except ValidationError:
            raise Refuse(HTTPStatus.BAD_REQUEST, 'expected JSON {"username", "password"}') from None
        if not data_available():
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE)
        address = request.client.host
        keys = [("address", address), ("user", body.username.strip().lower())]
        wait = limiter.retry_after(keys)
        if wait:
            raise Refuse(HTTPStatus.TOO_MANY_REQUESTS, "too many failed logins", {"Retry-After": str(wait)})

        def check():
            name = account_auth.check_login(users_dir, body.username, body.password)
            if name is None:
                return None
            limiter.succeeded(keys[1:])
            device = account_auth.clean_device_name(body.device)
            token = account_auth.issue_token(users_dir, name, device)
            print(f"User {name} logged in from {address}{f' ({device})' if device else ''}", flush=True)
            return {"username": name, "token": token, "permissions": account_auth.user_permissions(users_dir, name)}

        try:
            answer = await run_in_threadpool(check)
        except OSError as exc:
            print(f"Storing a token failed: {exc}", flush=True)
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
        if answer is None:
            limiter.failed(keys)
            print(f"Failed login from {address}", flush=True)
            raise Refuse(HTTPStatus.UNAUTHORIZED, "wrong username or password")
        return _json(HTTPStatus.OK, answer)

    async def logout(request: Request):
        token = bearer_token(request)
        if token and data_available():
            try:
                await run_in_threadpool(account_auth.revoke_token, users_dir, token)
            except OSError as exc:
                print(f"Revoking a token failed: {exc}", flush=True)
        # Best effort, and an unknown token says nothing about the others
        return _text(request, HTTPStatus.NO_CONTENT)

    route("/login", ("POST",), login, others=HTTPStatus.NOT_FOUND)
    route("/logout", ("POST",), logout, others=HTTPStatus.NOT_FOUND)

    # ---- the inbox ----

    async def put_request(request: Request, name: str):
        match = REQUEST_NAME_RE.match(name)
        if match is None:
            raise Refuse(HTTPStatus.BAD_REQUEST, "not a request file name")
        if not data_available():
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE)
        # Before the body: a bad token's upload is never read. Open to the internet,
        # so unlike MDHS's server this one takes no request without a login.
        sender = await run_in_threadpool(sender_of, request)
        if match.group("user") is None:
            raise Refuse(HTTPStatus.UNAUTHORIZED, "log in first")
        if match.group("user") != sender:
            raise Refuse(HTTPStatus.FORBIDDEN, "the file name names another user")
        data = await read_body(request, DEFAULT_MAX_REQUEST_BYTES)
        if not data.startswith(SQLITE_HEADER):
            raise Refuse(HTTPStatus.BAD_REQUEST, "not an SQLite file")

        try:
            received = await run_in_threadpool(requests.receive, name, data, sender)
        except OSError as exc:
            print(f"Storing request {name} failed: {exc}", flush=True)
            raise Refuse(HTTPStatus.SERVICE_UNAVAILABLE) from None
        if received.outcome == request_folders.REFUSED:
            print(f"Refused request {name}: {received.reason}", flush=True)
            raise Refuse(HTTPStatus.BAD_REQUEST, received.reason)
        if received.outcome == request_folders.STORED:
            print(f"Received request {name} ({len(data)} bytes){f': held, {received.reason}' if received.reason else ''}",
                  flush=True)
        status, detail = INBOX_ANSWERS[received.outcome]
        raise Refuse(status, detail)

    route("/inbox/{name:path}", ("PUT",), put_request, others=HTTPStatus.NOT_FOUND)
    if SYNC_ADDON in config.addons:
        from .addons.sync import add_routes
        add_routes(app, route, config, authorize)
    return app
