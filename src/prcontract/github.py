"""Minimal GitHub REST client built on urllib (no third-party dependencies).

The transport is injectable so every code path can be tested without a network.
The token is only ever sent to ``api_url``: pagination links that point to any
other origin are refused.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

DEFAULT_API_URL = "https://api.github.com"

# transport(method, url, headers, body) -> (status, response headers, body bytes)
Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, dict, bytes]"]


class GitHubError(Exception):
    """Any failure talking to the GitHub API."""


def urllib_transport(method: str, url: str, headers: dict, data: bytes | None):
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except urllib.error.URLError as exc:
        raise GitHubError(f"network error: {exc.reason}") from exc


_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _NEXT.search(link_header)
    return match.group(1) if match else None


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        api_url: str = DEFAULT_API_URL,
        transport: Transport | None = None,
    ) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.transport = transport or urllib_transport

    # -- low level ---------------------------------------------------------

    def _request(self, method: str, target: str, params: dict | None = None, body: Any = None):
        if target.startswith("http"):
            if not target.startswith(self.api_url + "/"):
                raise GitHubError("refusing to follow a link outside the API origin")
            url = target
        else:
            url = self.api_url + target
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "prcontract",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, resp_headers, raw = self.transport(method, url, headers, data)
        lowered = {str(k).lower(): v for k, v in resp_headers.items()}
        if status >= 400:
            message = ""
            try:
                message = json.loads(raw).get("message", "")
            except (ValueError, AttributeError):
                pass
            hint = ""
            if status in (401, 403, 404) and not self.token:
                hint = " (no token supplied; set GITHUB_TOKEN)"
            elif status == 403 and lowered.get("x-ratelimit-remaining") == "0":
                hint = " (rate limit exceeded)"
            raise GitHubError(f"{method} {target} -> HTTP {status}: {message or 'error'}{hint}")
        return (json.loads(raw) if raw else None), lowered

    def get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params)[0]

    def paginate(self, path: str, params: dict | None = None, limit: int | None = None) -> list:
        items: list = []
        target: str | None = path
        query: dict | None = {"per_page": 100, **(params or {})}
        while target:
            data, headers = self._request("GET", target, query)
            items.extend(data)
            if limit is not None and len(items) >= limit:
                return items[:limit]
            target = parse_next_link(headers.get("link"))
            query = None  # the next link already carries its query string
        return items

    # -- pull requests -----------------------------------------------------

    def get_pr(self, repo: str, number: int) -> dict:
        return self.get(f"/repos/{repo}/pulls/{number}")

    def list_pr_files(self, repo: str, number: int, limit: int | None = None) -> list:
        return self.paginate(f"/repos/{repo}/pulls/{number}/files", limit=limit)

    def list_pr_commits(self, repo: str, number: int) -> list:
        return self.paginate(f"/repos/{repo}/pulls/{number}/commits")

    def list_open_prs(self, repo: str, limit: int | None = None) -> list:
        return self.paginate(
            f"/repos/{repo}/pulls",
            {"state": "open", "sort": "created", "direction": "desc"},
            limit=limit,
        )

    # -- comments ----------------------------------------------------------

    def upsert_comment(self, repo: str, number: int, marker: str, body: str) -> str:
        """Create or update the single comment that contains ``marker``."""
        comments = self.paginate(f"/repos/{repo}/issues/{number}/comments")
        for comment in comments:
            if marker in (comment.get("body") or ""):
                self._request("PATCH", f"/repos/{repo}/issues/comments/{comment['id']}", body={"body": body})
                return "updated"
        self._request("POST", f"/repos/{repo}/issues/{number}/comments", body={"body": body})
        return "created"
