# MusicDatabaseWired's dependencies

Every third-party package here was approved by the owner (2026-11-06), and nothing else may be
imported or installed ([ADR-0006](../docs/decisions/0006-lite-sharing-server.md)). Adding or upgrading
one is the owner's decision: a new package, a new transitive one or a new version comes with a line
here and an edit of `requirements.txt` and of `DIRECT_IMPORTS` in `tests/test_wired_imports.py`.

`requirements.txt` pins all of them, and installing with `--no-deps` means nothing arrives unlisted.

| Package | Version | Licence | Needed by | Why |
|---|---|---|---|---|
| `fastapi` | 0.162.2 | MIT | MDW | routes. The standard library has no ASGI framework. |
| `pydantic` | 2.14.3 | MIT | MDW, fastapi | checking request bodies |
| `uvicorn` | 0.54.0 | BSD-3-Clause | MDW | the HTTP server, with TLS |
| `questionary` | 2.1.1 | MIT | MDW | the menu, the same as MDHS's |
| `starlette` | 1.10.0 | BSD-3-Clause | MDW, fastapi | HTTP underneath FastAPI |
| `anyio` | 4.16.0 | MIT | starlette | async running |
| `idna` | 3.20 | BSD-3-Clause | anyio | international domain names |
| `opentelemetry-api` | 1.45.2 | Apache-2.0 | fastapi | required by FastAPI; switched off, see below |
| `h11` | 0.16.0 | MIT | uvicorn | the HTTP/1.1 protocol |
| `click` | 8.5.0 | BSD-3-Clause | uvicorn | uvicorn's own command line |
| `pydantic_core` | 2.50.4 | MIT | pydantic | pydantic's core; compiled (Rust) |
| `annotated-types` | 0.8.0 | MIT | pydantic | type constraints |
| `typing-inspection` | 0.4.5 | MIT | fastapi, pydantic | reading type hints |
| `annotated-doc` | 0.0.5 | MIT | fastapi | documenting parameters |
| `typing_extensions` | 4.16.0 | PSF-2.0 | several | newer typing features |
| `prompt_toolkit` | 3.0.52 | BSD-3-Clause | questionary | terminal input; the same version as MDHS |
| `wcwidth` | 0.2.14 | MIT | prompt_toolkit | text width; the same version as MDHS, pure Python (0.9 is compiled) |

## Decisions

- **The newest FastAPI, with its telemetry off** (owner, 2026-11-08). Since 0.142.0 it requires
  `opentelemetry-api`. That package alone can't send anything: it has no network code (read 2026-11-08)
  and does nothing without an SDK. No SDK or exporter is installed, `tests/test_wired_imports.py` keeps
  them out, and `app.py` turns telemetry off, so no `OTEL_*` or `FASTAPI_OTEL_*` variable can enable it.
- **The sync bodies aren't described in Pydantic.** Their rules are the core's `parse_*()` functions,
  shared with MDHS. A second copy here would drift, and sync must store unknown fields verbatim.
  Pydantic checks what MDW alone takes: the login body and the rows of a request.
