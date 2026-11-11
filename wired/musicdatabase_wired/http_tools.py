"""What the routes share: short answers, reading a body after the token, the Host check."""

import json
from http import HTTPStatus
from urllib.parse import parse_qs

from fastapi import Request, Response

from musicdatabase_core import server_rules
from musicdatabase_core.server_rules import host_allowed, plain_int


class Refuse(Exception):
    """Ends a request with a short text answer."""

    def __init__(self, status: HTTPStatus, detail: str | None = None, headers: dict | None = None):
        self.status, self.detail, self.headers = status, detail, headers


def _json(status: HTTPStatus, payload) -> Response:
    return Response(json.dumps(payload).encode(), status, media_type="application/json",
                    headers={"Cache-Control": "no-store"})


def _text(request: Request, status: HTTPStatus, detail: str | None = None, headers: dict | None = None) -> Response:
    all_headers = {"Cache-Control": "no-store", **(headers or {})}
    # Answered without reading the body (a bad token, too big): the connection
    # can't serve another request, and nothing waits for the rest of it
    if not getattr(request.state, "body_read", False):
        length = request.headers.get("content-length")
        if (length is not None and length.strip() != "0") or "transfer-encoding" in request.headers:
            all_headers["Connection"] = "close"
    if status == HTTPStatus.NO_CONTENT:
        return Response(status_code=status, headers=all_headers)
    body = f"{status.value} {status.phrase}{f' - {detail}' if detail else ''}\n"
    return Response(body, status, media_type="text/plain", headers=all_headers)


async def read_body(request: Request, limit: int) -> bytes:
    length = plain_int(request.headers.get("content-length"))
    if length is None:
        raise Refuse(HTTPStatus.LENGTH_REQUIRED)
    if length > limit:
        raise Refuse(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"at most {limit} bytes")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
    request.state.body_read = True
    return bytes(data)


async def read_json_body(request: Request, limit: int):
    try:
        return json.loads(await read_body(request, limit))
    except ValueError:
        raise Refuse(HTTPStatus.BAD_REQUEST, "not JSON") from None


def bearer_token(request: Request) -> str | None:
    return server_rules.bearer_token(request.headers.get("authorization"))


def query_int(request: Request, name: str, default: int | None = None) -> int:
    values = parse_qs(request.url.query).get(name)
    if not values:
        if default is None:
            raise Refuse(HTTPStatus.BAD_REQUEST, f"{name}: missing")
        return default
    value = plain_int(values[0])
    if value is None:
        raise Refuse(HTTPStatus.BAD_REQUEST, f"{name}: expected an integer >= 0")
    return value


class HostCheck:
    """A web page that points its own domain at this server (DNS rebinding) sends
    that domain, so a browser can't be used to read or write here."""

    def __init__(self, app, names: frozenset[str]):
        self.app, self.names = app, names

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            host = dict(scope["headers"]).get(b"host")
            if not host_allowed(host.decode("latin-1") if host is not None else None, set(self.names)):
                response = Response(b"421 Misdirected Request - unknown host name\n", 421, media_type="text/plain",
                                    headers={"Connection": "close", "Cache-Control": "no-store"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
