"""
The few GitHub REST calls the public database needs (public_database.py):
find or create the release of one tag, read an asset, upload / rename /
delete assets. The token goes only into the Authorization header - never
into a message, an exception or the log.
"""

from dataclasses import dataclass
from pathlib import Path

import requests

from config.constants import GITHUB_API_TIMEOUT, GITHUB_API_URL, GITHUB_UPLOAD_TIMEOUT

_API_VERSION = "2022-11-28"


class GithubError(Exception):
    """A GitHub call failed: no network, a bad token, a missing repo, ..."""


@dataclass
class Release:
    id: int
    upload_url: str  # without the {?name,label} template
    html_url: str
    assets: dict  # name -> asset JSON


class GithubReleaseClient:
    def __init__(self, repo: str, token: str, api_url: str = GITHUB_API_URL, session=None):
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        self.session = session or requests.Session()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    def _request(self, method: str, url: str, *, expected=(200,), timeout=GITHUB_API_TIMEOUT, **kwargs):
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            # The exception's text names only the URL, never the headers
            raise GithubError(f"can't reach GitHub ({type(exc).__name__})") from None
        if response.status_code not in expected:
            raise GithubError(_explain(response.status_code, self.repo, _message(response)))
        return response

    def _release(self, response) -> Release:
        data = _json(response)
        try:
            return Release(id=data["id"], upload_url=data["upload_url"].split("{", 1)[0],
                           html_url=data.get("html_url", ""),
                           assets={a["name"]: a for a in data.get("assets", [])})
        except (KeyError, TypeError, AttributeError) as exc:
            raise GithubError(f"GitHub answered with a release it doesn't describe ({type(exc).__name__}: {exc})") \
                from None

    def get_release(self, tag: str) -> Release | None:
        response = self._request("GET", f"{self.api_url}/repos/{self.repo}/releases/tags/{tag}", expected=(200, 404))
        if response.status_code == 404:
            return None
        return self._release(response)

    def create_release(self, tag: str, name: str, body: str) -> Release:
        response = self._request("POST", f"{self.api_url}/repos/{self.repo}/releases", expected=(201,), json={
            "tag_name": tag, "name": name, "body": body, "make_latest": "false",
        })
        return self._release(response)

    def download_asset(self, asset: dict) -> bytes:
        response = self._request("GET", f"{self.api_url}/repos/{self.repo}/releases/assets/{asset['id']}",
                                 headers={"Accept": "application/octet-stream"})
        return response.content

    def upload_asset(self, release: Release, name: str, path: Path, content_type: str) -> dict:
        with open(path, "rb") as f:
            response = self._request("POST", release.upload_url, expected=(201,), timeout=GITHUB_UPLOAD_TIMEOUT,
                                     params={"name": name}, data=f,
                                     headers={"Content-Type": content_type,
                                              "Content-Length": str(path.stat().st_size)})
        asset = _asset(response)
        release.assets[asset["name"]] = asset
        return asset

    def rename_asset(self, release: Release, asset: dict, name: str) -> dict:
        response = self._request("PATCH", f"{self.api_url}/repos/{self.repo}/releases/assets/{asset['id']}",
                                 json={"name": name})
        release.assets.pop(asset["name"], None)
        renamed = _asset(response)
        release.assets[renamed["name"]] = renamed
        return renamed

    def delete_asset(self, release: Release, name: str) -> None:
        asset = release.assets.get(name)
        if asset is None:
            return
        self._request("DELETE", f"{self.api_url}/repos/{self.repo}/releases/assets/{asset['id']}",
                      expected=(204, 404))
        release.assets.pop(name, None)


def _json(response):
    """The answer's JSON; GithubError when it isn't JSON (a proxy's error page)."""
    try:
        return response.json()
    except ValueError:
        raise GithubError(f"GitHub's answer isn't JSON (HTTP {response.status_code})") from None


def _asset(response) -> dict:
    asset = _json(response)
    if not isinstance(asset, dict) or "name" not in asset:
        raise GithubError("GitHub answered with an asset it doesn't describe")
    return asset


def _message(response) -> str:
    try:
        return str(response.json().get("message", ""))[:200]
    except ValueError:
        return ""


def _explain(status: int, repo: str, message: str) -> str:
    if status == 401:
        return "GitHub refused the token (401) - expired or mistyped? Make a new one (see the setup steps)"
    if status == 403:
        return (f"the token may not write to {repo} (403{': ' + message if message else ''}) - it needs "
                "Contents: read and write on that repository")
    if status == 404:
        return f"GitHub doesn't show {repo} to this token (404) - check GITHUB_DB_REPO and the token's repository"
    return f"GitHub answered {status}{': ' + message if message else ''}"
