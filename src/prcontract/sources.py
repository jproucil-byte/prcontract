"""Where a PullRequest comes from: a local git checkout or the GitHub API."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .github import GitHubClient
from .model import Commit, FileChange, PullRequest


class SourceError(Exception):
    """The change set could not be determined."""


# -- local git ----------------------------------------------------------------


def _git(cwd: str, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise SourceError("git is not installed or not on PATH") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise SourceError(f"git {' '.join(args[:2])} failed: {detail}")
    return proc.stdout


def _ref_exists(cwd: str, ref: str) -> bool:
    try:
        _git(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return True
    except SourceError:
        return False


def detect_base(cwd: str = ".") -> str:
    try:
        head = _git(cwd, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
        if head.startswith("refs/remotes/"):
            return head[len("refs/remotes/") :]
    except SourceError:
        pass
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if _ref_exists(cwd, candidate):
            return candidate
    raise SourceError("could not detect the base branch; pass --base <ref>")


def parse_numstat_z(raw: str) -> list[FileChange]:
    """Parse ``git diff --numstat -z -M``; renames arrive as ``a\\td\\t\\0old\\0new\\0``."""
    parts = raw.split("\0")
    files: list[FileChange] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        if not entry:
            i += 1
            continue
        added, deleted, path = entry.split("\t", 2)
        if path == "":
            path = parts[i + 2]
            i += 3
        else:
            i += 1
        files.append(
            FileChange(
                path=path,
                additions=0 if added == "-" else int(added),  # "-" marks binary files
                deletions=0 if deleted == "-" else int(deleted),
            )
        )
    return files


def parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, message = chunk.partition("\x1f")
        commits.append(Commit(sha=sha.strip(), message=message.strip()))
    return commits


def from_git(base: str | None, head: str = "HEAD", cwd: str = ".") -> PullRequest:
    try:
        _git(cwd, "rev-parse", "--is-inside-work-tree")
    except SourceError as exc:
        raise SourceError(f"{Path(cwd).resolve()} is not a git repository") from exc
    base = base or detect_base(cwd)
    for ref in (base, head):
        if not _ref_exists(cwd, ref):
            raise SourceError(
                f"unknown git ref {ref!r}. In CI, fetch full history (actions/checkout fetch-depth: 0) "
                "or pass --base."
            )
    try:
        diff = _git(cwd, "diff", "--numstat", "-z", "-M", f"{base}...{head}")
    except SourceError as exc:
        raise SourceError(f"{exc} (no common history between {base} and {head}? try fetch-depth: 0)") from exc
    log = _git(cwd, "log", "--format=%H%x1f%B%x1e", f"{base}..{head}")
    return PullRequest(files=parse_numstat_z(diff), commits=parse_log(log))


# -- GitHub API -----------------------------------------------------------------


def from_api(client: GitHubClient, repo: str, number: int) -> PullRequest:
    data = client.get_pr(repo, number)
    files = [
        FileChange(f["filename"], int(f.get("additions", 0)), int(f.get("deletions", 0)))
        for f in client.list_pr_files(repo, number)
    ]
    commits = [Commit(c["sha"], (c.get("commit") or {}).get("message", "")) for c in client.list_pr_commits(repo, number)]
    return PullRequest(
        title=data.get("title") or "",
        body=data.get("body") or "",
        number=number,
        author=(data.get("user") or {}).get("login"),
        repo=repo,
        files=files,
        commits=commits,
    )


def read_event(path: str | Path, fallback_repo: str | None = None) -> tuple[str, int]:
    """Return (``owner/name``, PR number) from a ``pull_request[_target]`` event payload."""
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceError(f"cannot read event payload {path}: {exc}") from exc
    pull = event.get("pull_request")
    if not isinstance(pull, dict) or "number" not in pull:
        raise SourceError("event payload has no pull_request; run on pull_request or pull_request_target")
    repo = (event.get("repository") or {}).get("full_name") or fallback_repo
    if not repo:
        raise SourceError("cannot determine the repository (GITHUB_REPOSITORY is not set)")
    return repo, int(pull["number"])


def parse_pr_ref(ref: str) -> tuple[str, int]:
    """Parse ``owner/repo#123`` or a pull request URL."""
    ref = ref.strip()
    if "/pull/" in ref:
        tail = ref.split("github.com/", 1)[-1]
        owner_repo, _, number = tail.partition("/pull/")
        number = number.split("/")[0].split("?")[0].split("#")[0]
        parts = owner_repo.strip("/").split("/")
        if len(parts) == 2 and number.isdigit():
            return "/".join(parts), int(number)
    elif "#" in ref:
        repo, _, number = ref.partition("#")
        if repo.count("/") == 1 and number.isdigit():
            return repo, int(number)
    raise SourceError(f"cannot parse {ref!r}; expected owner/repo#123 or a pull request URL")
