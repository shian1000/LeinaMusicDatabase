"""MDW's settings: wired/.env (see .env.example), then the command line."""

import os
from dataclasses import dataclass
from pathlib import Path

from musicdatabase_core.server_rules import REQUEST_SOCKET_TIMEOUT

WIRED_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = WIRED_DIR / ".env"
DEFAULT_PORT = 8002
DEFAULT_CATALOG_REPO = "shian1000/LeinaMusicDatabase"
SYNC_ADDON = "sync"


@dataclass(frozen=True)
class Config:
    catalog_dir: Path
    users_dir: Path
    inbox_dir: Path
    tls_dir: Path
    archive_dir: Path
    addresses: tuple[str, ...] = ()
    port: int = DEFAULT_PORT
    tls: bool = True
    # What the Host header may name besides an IP address and localhost
    server_names: frozenset[str] = frozenset()
    request_timeout: float = REQUEST_SOCKET_TIMEOUT
    catalog_repo: str = DEFAULT_CATALOG_REPO
    catalog_updates: bool = True
    addons: frozenset[str] = frozenset()


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _words(value: str) -> list[str]:
    return value.replace(",", " ").split()


def load_config(args=None) -> Config:
    env = {**read_env_file(), **{k: v for k, v in os.environ.items() if k.startswith("WIRED_")}}
    data_dir = Path(env.get("WIRED_DATA_DIR") or WIRED_DIR / "data")

    def option(name):
        return getattr(args, name, None) if args is not None else None

    return Config(
        catalog_dir=Path(option("catalog") or data_dir / "catalog"),
        users_dir=Path(option("users") or data_dir / "users"),
        inbox_dir=Path(option("inbox") or data_dir / "inbox"),
        tls_dir=Path(option("tls_dir") or data_dir / "tls"),
        archive_dir=data_dir / "archive",
        addresses=tuple(option("address") or _words(env.get("WIRED_ADDRESSES", ""))),
        port=option("port") or int(env.get("WIRED_PORT") or DEFAULT_PORT),
        tls=not option("no_tls") and env.get("WIRED_TLS", "").lower() != "off",
        server_names=frozenset(name.lower() for name in _words(env.get("WIRED_SERVER_NAMES", ""))),
        request_timeout=option("request_timeout") or REQUEST_SOCKET_TIMEOUT,
        catalog_repo=env.get("WIRED_CATALOG_REPO") or DEFAULT_CATALOG_REPO,
        catalog_updates=not option("no_catalog_updates") and env.get("WIRED_CATALOG_UPDATES", "").lower() != "off",
        addons=frozenset(option("addon") or _words(env.get("WIRED_ADDONS", ""))),
    )
