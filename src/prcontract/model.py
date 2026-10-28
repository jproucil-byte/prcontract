"""Plain data classes shared by every module."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileChange:
    path: str
    additions: int = 0
    deletions: int = 0

    @property
    def lines(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str


@dataclass
class PullRequest:
    """Everything the checks need to know about a (proposed) pull request.

    ``body`` is ``None`` when no description was supplied (local mode without
    ``--body-file``); checks that need a description are then skipped instead
    of failing. In GitHub mode an empty description is ``""`` and fails.
    """

    title: str = ""
    body: str | None = None
    number: int | None = None
    author: str | None = None
    repo: str | None = None  # "owner/name"
    files: list[FileChange] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)

    def text(self) -> str:
        """Description plus all commit messages, for reference scanning."""
        parts = [self.body or ""]
        parts.extend(c.message for c in self.commits)
        return "\n".join(parts)


@dataclass
class Outcome:
    """What a single check returns before severity is applied."""

    ok: bool = True
    message: str = ""
    details: list[str] = field(default_factory=list)
    skipped: bool = False


@dataclass(frozen=True)
class Result:
    """Final, severity-resolved result of one check."""

    check: str
    status: str  # "pass" | "fail" | "warn" | "skipped"
    message: str
    details: tuple[str, ...] = ()
