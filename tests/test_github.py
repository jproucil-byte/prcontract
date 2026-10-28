import json
from pathlib import Path

import pytest
from fakes import FakeTransport

from prcontract.github import GitHubClient, GitHubError, parse_next_link
from prcontract.sources import SourceError, from_api, parse_pr_ref, read_event

OK = {}


def client(routes, token="tok"):
    transport = FakeTransport(routes)
    return GitHubClient(token, transport=transport), transport


def test_parse_next_link():
    header = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"'
    assert parse_next_link(header) == "https://api.github.com/x?page=2"
    assert parse_next_link('<https://a>; rel="prev"') is None
    assert parse_next_link(None) is None


def test_pagination_follows_next_and_sends_auth():
    next_url = "https://api.github.com/repos/o/r/pulls/1/files?per_page=100&page=2"
    c, t = client(
        {
            ("GET", "/repos/o/r/pulls/1/files?per_page=100"): (200, {"Link": f'<{next_url}>; rel="next"'}, [{"filename": "a"}]),
            ("GET", "/repos/o/r/pulls/1/files?per_page=100&page=2"): (200, OK, [{"filename": "b"}]),
        }
    )
    assert [f["filename"] for f in c.list_pr_files("o/r", 1)] == ["a", "b"]
    assert all(call["headers"]["Authorization"] == "Bearer tok" for call in t.calls)
    assert t.calls[0]["headers"]["Accept"] == "application/vnd.github+json"


def test_pagination_limit_stops_early():
    next_url = "https://api.github.com/x?page=2"
    c, t = client({("GET", "/x?per_page=100"): (200, {"link": f'<{next_url}>; rel="next"'}, [1, 2, 3])})
    assert c.paginate("/x", limit=2) == [1, 2]
    assert len(t.calls) == 1


def test_never_follows_links_to_another_origin():
    evil = "https://evil.example/steal"
    c, t = client({("GET", "/x?per_page=100"): (200, {"Link": f'<{evil}>; rel="next"'}, [1])})
    with pytest.raises(GitHubError, match="outside the API origin"):
        c.paginate("/x")
    assert len(t.calls) == 1  # the token was never sent to the other host


def test_no_token_means_no_auth_header():
    c, t = client({("GET", "/repos/o/r/pulls/1"): (200, OK, {"number": 1})}, token=None)
    c.get_pr("o/r", 1)
    assert "Authorization" not in t.calls[0]["headers"]


def test_error_messages_and_hints():
    c, _ = client({("GET", "/repos/o/r/pulls/1"): (404, OK, {"message": "Not Found"})}, token=None)
    with pytest.raises(GitHubError, match=r"HTTP 404: Not Found.*GITHUB_TOKEN"):
        c.get_pr("o/r", 1)
    limited, _ = client(
        {("GET", "/repos/o/r/pulls/1"): (403, {"X-RateLimit-Remaining": "0"}, {"message": "API rate limit exceeded"})}
    )
    with pytest.raises(GitHubError, match="rate limit exceeded"):
        limited.get_pr("o/r", 1)
    garbage, _ = client({("GET", "/repos/o/r/pulls/1"): (500, OK, b"<html>")})
    with pytest.raises(GitHubError, match="HTTP 500"):
        garbage.get_pr("o/r", 1)


MARKER = "<!-- prcontract -->"
COMMENTS = "/repos/o/r/issues/5/comments?per_page=100"


def test_upsert_creates_when_absent():
    c, t = client({("GET", COMMENTS): (200, OK, [{"id": 1, "body": "unrelated"}]), ("POST", "/repos/o/r/issues/5/comments"): (201, OK, {})})
    assert c.upsert_comment("o/r", 5, MARKER, f"{MARKER}\nbody") == "created"
    assert t.requests("POST")[0]["body"] == {"body": f"{MARKER}\nbody"}


def test_upsert_updates_existing_comment():
    c, t = client(
        {
            ("GET", COMMENTS): (200, OK, [{"id": 1, "body": "x"}, {"id": 42, "body": f"{MARKER}\nold"}]),
            ("PATCH", "/repos/o/r/issues/comments/42"): (200, OK, {}),
        }
    )
    assert c.upsert_comment("o/r", 5, MARKER, f"{MARKER}\nnew") == "updated"
    assert not t.requests("POST")


def test_from_api_builds_pull_request():
    c, _ = client(
        {
            ("GET", "/repos/o/r/pulls/5"): (200, OK, {"title": "T", "body": None, "user": {"login": "alice"}}),
            ("GET", "/repos/o/r/pulls/5/files?per_page=100"): (
                200, OK, [{"filename": "a.py", "additions": 3, "deletions": 1}, {"filename": "img.png"}],
            ),
            ("GET", "/repos/o/r/pulls/5/commits?per_page=100"): (
                200, OK, [{"sha": "abc", "commit": {"message": "msg\n\nCloses #1"}}],
            ),
        }
    )
    pr = from_api(c, "o/r", 5)
    assert (pr.title, pr.body, pr.author, pr.number, pr.repo) == ("T", "", "alice", 5, "o/r")
    assert [(f.path, f.lines) for f in pr.files] == [("a.py", 4), ("img.png", 0)]
    assert pr.commits[0].message.endswith("Closes #1")


def test_read_event(tmp_path: Path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}, "repository": {"full_name": "o/r"}}))
    assert read_event(event) == ("o/r", 7)

    event.write_text(json.dumps({"pull_request": {"number": 7}}))
    assert read_event(event, fallback_repo="x/y") == ("x/y", 7)
    with pytest.raises(SourceError, match="cannot determine the repository"):
        read_event(event)

    event.write_text(json.dumps({"issue": {"number": 7}}))
    with pytest.raises(SourceError, match="no pull_request"):
        read_event(event)
    with pytest.raises(SourceError, match="cannot read event payload"):
        read_event(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("owner/repo#123", ("owner/repo", 123)),
        ("https://github.com/owner/repo/pull/45", ("owner/repo", 45)),
        ("https://github.com/owner/repo/pull/45/files?x=1", ("owner/repo", 45)),
        ("github.com/owner/repo/pull/45#issuecomment-1", ("owner/repo", 45)),
    ],
)
def test_parse_pr_ref(ref, expected):
    assert parse_pr_ref(ref) == expected


@pytest.mark.parametrize("ref", ["123", "owner#1", "owner/repo#x", "a/b/c#1", "https://github.com/o/r/issues/3"])
def test_parse_pr_ref_rejects(ref):
    with pytest.raises(SourceError):
        parse_pr_ref(ref)
