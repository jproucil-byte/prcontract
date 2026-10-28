import subprocess
from pathlib import Path

import pytest

from prcontract.config import merge
from prcontract.checks import Context
from prcontract.model import Commit, FileChange, PullRequest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with a `main` base commit; callers add their own branch."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "app.py").write_text("def f():\n    return 1\n")
    (root / "tests" / "test_app.py").write_text("def test_f():\n    pass\n")
    (root / "README.md").write_text("hello\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    return root


@pytest.fixture
def ctx() -> Context:
    return Context(config=merge({}))


def make_pr(
    *,
    title: str = "Fix the thing",
    body: str | None = "",
    files: list[tuple[str, int, int]] | None = None,
    commits: list[str] | None = None,
    author: str | None = "alice",
    number: int | None = 5,
    repo: str | None = "o/r",
) -> PullRequest:
    return PullRequest(
        title=title,
        body=body,
        number=number,
        author=author,
        repo=repo,
        files=[FileChange(p, a, d) for p, a, d in (files or [])],
        commits=[Commit(f"{i:040x}", m) for i, m in enumerate(commits or [])],
    )
