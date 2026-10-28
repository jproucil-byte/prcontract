"""Test doubles for the GitHub API."""

from __future__ import annotations

import json


class FakeTransport:
    """Serves canned responses keyed by (method, url relative to the API base)."""

    base = "https://api.github.com"

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, data):
        rel = url[len(self.base) :] if url.startswith(self.base) else url
        self.calls.append(
            {"method": method, "url": rel, "headers": headers, "body": json.loads(data) if data else None}
        )
        try:
            status, resp_headers, body = self.routes[(method, rel)]
        except KeyError:
            raise AssertionError(f"unexpected request {method} {rel}; known: {list(self.routes)}") from None
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return status, resp_headers, raw

    def requests(self, method: str) -> list[dict]:
        return [c for c in self.calls if c["method"] == method]


class FakeClient:
    """Stands in for GitHubClient in check-level tests."""

    def __init__(self, prs: list[dict], files: dict[int, list[str]]):
        self.prs = prs
        self.files = files

    def list_open_prs(self, repo, limit=None):
        return self.prs[:limit]

    def list_pr_files(self, repo, number, limit=None):
        return [{"filename": f} for f in self.files.get(number, [])]
