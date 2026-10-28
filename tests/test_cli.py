import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import git
from fakes import FakeTransport

import prcontract.cli as cli
from prcontract.cli import main
from prcontract.github import GitHubClient
from prcontract.sources import SourceError, from_git, parse_numstat_z

GOOD_BODY = """## What
Adds a function g that callers can use.

## Why
Needed by the new feature module.

## Testing
Ran the test-suite locally.

AI assistance: none
"""


def branch(repo: Path, message: str = "Add g\n\nCloses #7", with_tests: bool = True) -> None:
    git(repo, "checkout", "-q", "-b", "feature")
    with open(repo / "src" / "app.py", "a") as handle:
        handle.write("def g():\n    return 2\n")
    if with_tests:
        with open(repo / "tests" / "test_app.py", "a") as handle:
            handle.write("def test_g():\n    pass\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def run(repo: Path, monkeypatch, capsys, *argv: str):
    monkeypatch.chdir(repo)
    code = main(["check", "--base", "main", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def write_body(repo: Path, text: str = GOOD_BODY) -> str:
    path = repo / "body.md"
    path.write_text(text)
    return str(path)


def test_clean_pr_passes(repo, monkeypatch, capsys):
    branch(repo)
    code, out, _ = run(repo, monkeypatch, capsys, "--body-file", write_body(repo), "--format", "json")
    payload = json.loads(out)
    assert code == 0
    assert payload["summary"]["fail"] == 0 and payload["summary"]["warn"] == 0
    statuses = {r["check"]: r["status"] for r in payload["results"]}
    assert statuses["issue-link"] == "pass" and statuses["duplicates"] == "skipped"


def test_failing_pr_exits_1(repo, monkeypatch, capsys):
    branch(repo, message="Add g")
    code, out, _ = run(repo, monkeypatch, capsys, "--body-file", write_body(repo))
    assert code == 1
    assert "[FAIL] issue-link" in out


def test_fail_on_levels(repo, monkeypatch, capsys):
    branch(repo, with_tests=False)  # tests-with-code is only a warning
    body = write_body(repo)
    assert run(repo, monkeypatch, capsys, "--body-file", body)[0] == 0
    assert run(repo, monkeypatch, capsys, "--body-file", body, "--fail-on", "warn")[0] == 1
    git(repo, "commit", "-q", "--amend", "-m", "no issue link")
    assert run(repo, monkeypatch, capsys, "--body-file", body, "--fail-on", "never")[0] == 0


def test_missing_body_skips_description_checks(repo, monkeypatch, capsys):
    branch(repo)
    code, out, _ = run(repo, monkeypatch, capsys, "--format", "json")
    statuses = {r["check"]: r["status"] for r in json.loads(out)["results"]}
    assert statuses["pr-sections"] == statuses["ai-disclosure"] == "skipped"
    assert code == 0


def test_body_from_stdin_and_inline(repo, monkeypatch, capsys):
    branch(repo)
    assert run(repo, monkeypatch, capsys, "--body", GOOD_BODY)[0] == 0
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(GOOD_BODY))
    assert run(repo, monkeypatch, capsys, "--body-file", "-")[0] == 0


def test_exempt_title(repo, monkeypatch, capsys):
    branch(repo, message="update docs")
    body = write_body(repo)
    assert run(repo, monkeypatch, capsys, "--body-file", body)[0] == 1
    assert run(repo, monkeypatch, capsys, "--body-file", body, "--title", "docs: update")[0] == 0


def test_config_file_in_cwd_is_used(repo, monkeypatch, capsys):
    branch(repo)
    (repo / ".prcontract.toml").write_text("[diff-size]\nmax_files = 1\n")
    code, out, _ = run(repo, monkeypatch, capsys, "--body-file", write_body(repo))
    assert code == 1 and "Too large" in out


def test_run_executes_configured_commands(repo, monkeypatch, capsys):
    branch(repo)
    failing = f"{sys.executable} -c 'import sys; sys.exit(1)'"
    (repo / "prc.toml").write_text(f'[commands]\nrun = ["{failing}"]\n')
    body = write_body(repo)
    code, out, _ = run(repo, monkeypatch, capsys, "--config", "prc.toml", "--body-file", body)
    assert code == 0 and "[skip] commands" in out  # never runs without --run
    code, out, _ = run(repo, monkeypatch, capsys, "--config", "prc.toml", "--body-file", body, "--run")
    assert code == 1 and "[FAIL] commands" in out


def test_markdown_format_has_marker(repo, monkeypatch, capsys):
    branch(repo)
    _, out, _ = run(repo, monkeypatch, capsys, "--body-file", write_body(repo), "--format", "markdown")
    assert out.startswith("<!-- prcontract -->") and "| ✅ | `diff-size` |" in out


# -- error handling ------------------------------------------------------------


def test_usage_and_runtime_errors_exit_2(repo, tmp_path, monkeypatch, capsys):
    branch(repo)
    assert run(repo, monkeypatch, capsys, "--comment")[0] == 2
    assert run(repo, monkeypatch, capsys, "--body", "a", "--body-file", "b")[0] == 2
    assert run(repo, monkeypatch, capsys, "--body-file", str(tmp_path / "nope"))[0] == 2
    assert run(repo, monkeypatch, capsys, "--config", "missing.toml")[0] == 2
    code, _, err = run(repo, monkeypatch, capsys, "--pr", "o/r#1", "--run")
    assert code == 2 and "local checkouts" in err
    assert run(repo, monkeypatch, capsys, "--github", "--pr", "o/r#1")[0] == 2

    monkeypatch.chdir(repo)
    assert main(["check", "--base", "does-not-exist"]) == 2
    assert "unknown git ref" in capsys.readouterr().err

    monkeypatch.chdir(tmp_path)
    assert main(["check", "--base", "main"]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_github_flag_needs_event(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert main(["check", "--github"]) == 2
    assert "GITHUB_EVENT_PATH" in capsys.readouterr().err


# -- GitHub modes (fake transport) --------------------------------------------


def github_routes(comment_status=201):
    return {
        ("GET", "/repos/o/r/pulls/5"): (200, {}, {"title": "Fix g", "body": GOOD_BODY, "user": {"login": "alice"}}),
        ("GET", "/repos/o/r/pulls/5/files?per_page=100"): (
            200, {}, [{"filename": "src/app.py", "additions": 2, "deletions": 0}, {"filename": "tests/test_app.py", "additions": 2, "deletions": 0}],
        ),
        ("GET", "/repos/o/r/pulls/5/commits?per_page=100"): (200, {}, [{"sha": "a" * 40, "commit": {"message": "Add g\n\nCloses #7"}}]),
        ("GET", "/repos/o/r/pulls?per_page=100&state=open&sort=created&direction=desc"): (200, {}, []),
        ("GET", "/repos/o/r/issues/5/comments?per_page=100"): (200, {}, []),
        ("POST", "/repos/o/r/issues/5/comments"): (comment_status, {}, {"message": "Resource not accessible by integration"} if comment_status >= 400 else {}),
    }


@pytest.fixture
def github_env(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 5}, "repository": {"full_name": "o/r"}}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    return summary


def install_transport(monkeypatch, routes) -> FakeTransport:
    transport = FakeTransport(routes)
    monkeypatch.setattr(cli, "GitHubClient", lambda token, api_url: GitHubClient(token, api_url, transport=transport))
    return transport


def test_github_mode_comments_and_writes_summary(github_env, monkeypatch, capsys):
    transport = install_transport(monkeypatch, github_routes())
    code = main(["check", "--github", "--comment"])
    assert code == 0, capsys.readouterr().out
    posted = transport.requests("POST")
    assert len(posted) == 1 and posted[0]["body"]["body"].startswith("<!-- prcontract -->")
    assert posted[0]["headers"]["Authorization"] == "Bearer tok"
    assert "PR contract" in github_env.read_text()


def test_github_mode_comment_failure_only_warns(github_env, monkeypatch, capsys):
    install_transport(monkeypatch, github_routes(comment_status=403))
    code = main(["check", "--github", "--comment"])
    captured = capsys.readouterr()
    assert code == 0
    assert "could not post the PR comment" in captured.err
    assert "403" in captured.err and "tok" not in captured.err


def test_pr_mode_uses_ref_and_reports_failures(monkeypatch, capsys):
    routes = github_routes()
    routes[("GET", "/repos/o/r/pulls/5")] = (200, {}, {"title": "Fix g", "body": "", "user": {"login": "alice"}})
    install_transport(monkeypatch, routes)
    code = main(["check", "--pr", "https://github.com/o/r/pull/5", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    failed = {r["check"] for r in payload["results"] if r["status"] == "fail"}
    assert "ai-disclosure" in failed
    assert next(r for r in payload["results"] if r["check"] == "duplicates")["status"] == "pass"


# -- init / disclosure ----------------------------------------------------------


def test_init_writes_and_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / ".prcontract.toml").read_text().startswith("# prcontract configuration.")
    assert main(["init"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert main(["init", "--force"]) == 0


def test_disclosure_block_is_accepted_by_the_checker(tmp_path, monkeypatch, capsys):
    from conftest import make_pr
    from prcontract.checks import Context, check_ai_disclosure
    from prcontract.config import merge

    monkeypatch.chdir(tmp_path)
    assert main(["disclosure", "--tool", "Claude Code", "--tool", "Copilot", "--reviewed", "--tested", "pytest -q"]) == 0
    block = capsys.readouterr().out
    assert "AI assistance: Claude Code, Copilot" in block and "Testing: ran `pytest -q` locally." in block
    ctx = Context(config=merge({}))
    assert check_ai_disclosure(make_pr(body=block), merge({}).section("ai-disclosure"), ctx).ok
    assert main(["disclosure", "--none"]) == 0
    assert "AI assistance: none" in capsys.readouterr().out
    assert main(["disclosure"]) == 2
    assert main(["disclosure", "--none", "--tool", "x"]) == 2


# -- git parsing ----------------------------------------------------------------


def test_rename_binary_and_delete_are_parsed(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    git(repo, "mv", "src/app.py", "src/renamed.py")
    (repo / "logo.png").write_bytes(bytes(range(256)) * 4)
    git(repo, "rm", "-q", "README.md")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "reshuffle")
    pr = from_git("main", "HEAD", str(repo))
    paths = {f.path: f.lines for f in pr.files}
    assert paths["src/renamed.py"] == 0  # pure rename, tracked under its new name
    assert paths["logo.png"] == 0  # binary: git reports "-"
    assert paths["README.md"] == 1
    assert pr.commits[0].message == "reshuffle"


def test_paths_with_spaces_and_unicode(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "src" / "my file ěščř.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "unicode")
    assert [f.path for f in from_git("main", "HEAD", str(repo)).files] == ["src/my file ěščř.py"]


def test_numstat_parser_edge_cases():
    raw = "3\t1\ta.py\0-\t-\tb.bin\0" + "0\t0\t\0old.py\0new.py\0"
    files = parse_numstat_z(raw)
    assert [(f.path, f.additions, f.deletions) for f in files] == [("a.py", 3, 1), ("b.bin", 0, 0), ("new.py", 0, 0)]
    assert parse_numstat_z("") == []


def test_base_detection(repo):
    from prcontract.sources import detect_base

    assert detect_base(str(repo)) == "main"
    git(repo, "branch", "-m", "main", "trunk")
    with pytest.raises(SourceError, match="could not detect"):
        detect_base(str(repo))


def test_commit_messages_are_split_correctly(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    for i in range(3):
        (repo / f"f{i}.txt").write_text(str(i))
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"commit {i}\n\nbody {i}\n\nCloses #{i}")
    commits = from_git("main", "HEAD", str(repo)).commits
    assert [c.message.splitlines()[0] for c in commits] == ["commit 2", "commit 1", "commit 0"]
    assert all(c.message.endswith(f"Closes #{n}") for c, n in zip(commits, (2, 1, 0)))


def test_module_entry_point(repo):
    result = subprocess.run([sys.executable, "-m", "prcontract", "--version"], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.startswith("prcontract ")
