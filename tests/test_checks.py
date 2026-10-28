import sys

import pytest
from conftest import make_pr
from fakes import FakeClient

from prcontract.checks import (
    CHECKS,
    Context,
    check_ai_disclosure,
    check_commands,
    check_diff_size,
    check_duplicates,
    check_issue_link,
    check_pr_sections,
    check_protected_paths,
    check_tests_with_code,
    find_issue_refs,
    parse_disclosure,
    parse_sections,
    run_checks,
)
from prcontract.config import merge
from prcontract.github import GitHubError


def opts(name, **override):
    section = merge({name: override}).section(name)
    return section


# -- diff-size -----------------------------------------------------------------


def test_diff_size_passes_and_fails(ctx):
    small = make_pr(files=[("a.py", 10, 5)])
    assert check_diff_size(small, opts("diff-size"), ctx).ok
    big = make_pr(files=[("a.py", 900, 200), ("b.py", 5, 5)])
    out = check_diff_size(big, opts("diff-size"), ctx)
    assert not out.ok and "1110 lines" in out.message
    assert out.details[0].startswith("largest: a.py")
    many = make_pr(files=[(f"f{i}.py", 1, 0) for i in range(31)])
    assert "31 files" in check_diff_size(many, opts("diff-size"), ctx).message


def test_diff_size_ignores_lockfiles(ctx):
    pr = make_pr(files=[("poetry.lock", 50000, 0), ("pkg/package-lock.json", 9000, 0), ("a.py", 3, 0)])
    out = check_diff_size(pr, opts("diff-size"), ctx)
    assert out.ok and "1 files, 3 lines" in out.message


# -- issue-link ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Closes #12", [12]),
        ("fixes: #7 and resolves #9", [7, 9]),
        ("Fix #3\nFixed other/repo#4", [3, 4]),
        ("Fixes https://github.com/o/r/issues/55", [55]),
        ("Refs #8", [8]),
        ("see #8", [8]),
        ("<!-- Closes #123 -->", []),  # untouched template placeholder
        ("issue number 12", []),
        ("prefix#12", []),
        ("", []),
    ],
)
def test_find_issue_refs(text, expected):
    assert find_issue_refs(text) == expected


def test_ref_only_can_be_disallowed():
    assert find_issue_refs("Refs #8", allow_ref_only=False) == []
    assert find_issue_refs("Closes #8", allow_ref_only=False) == [8]


def test_issue_link_check(ctx):
    o = opts("issue-link")
    assert check_issue_link(make_pr(body="Closes #1"), o, ctx).ok
    assert check_issue_link(make_pr(body="nothing", commits=["Fix\n\nFixes #2"]), o, ctx).ok
    failing = check_issue_link(make_pr(body="no link here"), o, ctx)
    assert not failing.ok and "Closes #123" in failing.message


def test_issue_link_title_exemptions(ctx):
    o = opts("issue-link")
    assert check_issue_link(make_pr(title="docs: fix typo", body=""), o, ctx).ok
    assert check_issue_link(make_pr(title="Docs(readme): x", body=""), o, ctx).ok
    assert not check_issue_link(make_pr(title="docsify support", body=""), o, ctx).ok


# -- pr-sections ---------------------------------------------------------------

GOOD_BODY = """## What
Adds a retry loop around the upload.

## Why
Uploads fail on flaky connections.

## Testing
Ran the integration suite twice.
"""


def test_sections_ok(ctx):
    assert check_pr_sections(make_pr(body=GOOD_BODY), opts("pr-sections"), ctx).ok


def test_sections_missing_and_template_placeholders(ctx):
    body = "## What\nreal text here ok\n\n## Why\n<!-- Closes #123 -->\n"
    out = check_pr_sections(make_pr(body=body), opts("pr-sections"), ctx)
    assert not out.ok
    assert "section 'Why' is empty" in out.details[0]
    assert "missing section: Testing" in out.details


def test_sections_checkbox_only_does_not_count(ctx):
    body = GOOD_BODY.replace("Ran the integration suite twice.", "- [ ] \n- [x] ")
    assert not check_pr_sections(make_pr(body=body), opts("pr-sections"), ctx).ok


def test_sections_prefix_match_and_body_none(ctx):
    body = GOOD_BODY.replace("## Testing", "## Testing done:")
    assert check_pr_sections(make_pr(body=body), opts("pr-sections"), ctx).ok
    assert check_pr_sections(make_pr(body=None), opts("pr-sections"), ctx).skipped
    assert check_pr_sections(make_pr(body=""), opts("pr-sections"), ctx).ok is False


def test_parse_sections_ignores_headings_inside_comments():
    sections = parse_sections("<!--\n## Hidden\nx\n-->\n## Real\nbody\n")
    assert list(sections) == ["real"]


# -- ai-disclosure -------------------------------------------------------------

CLAUDE_TRAILER = "Add g\n\nCo-authored-by: Claude <noreply@anthropic.com>"


def test_disclosure_parsing_variants():
    assert parse_disclosure("AI assistance: none", "AI assistance").value == "none"
    assert parse_disclosure("- **AI assistance:** Claude Code", "AI assistance").value == "Claude Code"
    assert parse_disclosure("> ai assistance - Copilot", "AI assistance").value == "Copilot"
    assert parse_disclosure("AI assistance", "AI assistance").value == ""
    assert parse_disclosure("AI assistance was used for tests", "AI assistance") is None
    assert parse_disclosure("<!-- AI assistance: none -->", "AI assistance") is None


def test_disclosure_missing_and_placeholder(ctx):
    o = opts("ai-disclosure")
    assert "No disclosure line" in check_ai_disclosure(make_pr(body="hello"), o, ctx).message
    out = check_ai_disclosure(make_pr(body="AI assistance: <none, or the tools you used>"), o, ctx)
    assert not out.ok and "placeholder" in out.message
    assert check_ai_disclosure(make_pr(body=None), o, ctx).skipped


def test_disclosure_none_is_fine_without_trailers(ctx):
    out = check_ai_disclosure(make_pr(body="AI assistance: None.", commits=["Plain commit"]), opts("ai-disclosure"), ctx)
    assert out.ok


def test_disclosure_none_contradicted_by_trailer(ctx):
    pr = make_pr(body="AI assistance: none", commits=[CLAUDE_TRAILER])
    out = check_ai_disclosure(pr, opts("ai-disclosure"), ctx)
    assert not out.ok and "Co-authored-by: Claude" in out.details[0]


def test_disclosure_declared_tool_with_trailer_is_consistent(ctx):
    pr = make_pr(body="AI assistance: Claude Code", commits=[CLAUDE_TRAILER])
    assert check_ai_disclosure(pr, opts("ai-disclosure"), ctx).ok


def test_generated_with_footer_counts_as_trailer(ctx):
    pr = make_pr(body="AI assistance: no", commits=["x\n\n🤖 Generated with Claude Code"])
    assert not check_ai_disclosure(pr, opts("ai-disclosure"), ctx).ok


def test_human_coauthor_is_not_ai(ctx):
    pr = make_pr(body="AI assistance: none", commits=["x\n\nCo-authored-by: Bob <bob@example.com>"])
    assert check_ai_disclosure(pr, opts("ai-disclosure"), ctx).ok


# -- tests-with-code / protected-paths -----------------------------------------


def test_tests_with_code(ctx):
    o = opts("tests-with-code")
    assert check_tests_with_code(make_pr(files=[("README.md", 1, 0)]), o, ctx).ok
    out = check_tests_with_code(make_pr(files=[("src/a.py", 1, 0)]), o, ctx)
    assert not out.ok and out.details == ["src/a.py"]
    both = make_pr(files=[("src/a.py", 1, 0), ("tests/test_a.py", 1, 0)])
    assert check_tests_with_code(both, o, ctx).ok
    exempt = opts("tests-with-code", exempt=["src/generated/**"])
    assert check_tests_with_code(make_pr(files=[("src/generated/x.py", 1, 0)]), exempt, ctx).ok


def test_tests_with_code_truncates_long_lists(ctx):
    pr = make_pr(files=[(f"src/m{i}.py", 1, 0) for i in range(8)])
    out = check_tests_with_code(pr, opts("tests-with-code"), ctx)
    assert len(out.details) == 6 and out.details[-1] == "... and 3 more"


def test_protected_paths(ctx):
    o = opts("protected-paths", allow_authors=["Maintainer"])
    touching = [("LICENSE", 1, 1), ("src/a.py", 1, 0)]
    assert check_protected_paths(make_pr(files=[("src/a.py", 1, 0)]), o, ctx).ok
    out = check_protected_paths(make_pr(files=touching, author="mallory"), o, ctx)
    assert not out.ok and out.details == ["LICENSE"]
    assert check_protected_paths(make_pr(files=touching, author="maintainer"), o, ctx).ok
    assert not check_protected_paths(make_pr(files=touching, author=None), o, ctx).ok


# -- duplicates ----------------------------------------------------------------


def dup_ctx(prs, files):
    return Context(config=merge({}), client=FakeClient(prs, files))


def test_duplicates_skipped_without_client(ctx):
    assert check_duplicates(make_pr(), opts("duplicates"), ctx).skipped


def test_duplicates_same_issue():
    other = {"number": 9, "title": "Same fix", "html_url": "https://x/9", "body": "Fixes #12"}
    pr = make_pr(body="Closes #12", files=[("a.py", 1, 0)])
    out = check_duplicates(pr, opts("duplicates"), dup_ctx([other], {}))
    assert not out.ok and "same issue #12" in out.details[0]


def test_duplicates_file_overlap_and_thresholds():
    others = [
        {"number": 9, "title": "A", "html_url": "u9", "body": ""},
        {"number": 10, "title": "B", "html_url": "u10", "body": ""},
    ]
    mine = [(f"src/f{i}.py", 1, 0) for i in range(4)]
    files = {
        9: ["src/f0.py", "src/f1.py", "src/f2.py", "docs/x.md"],  # 3 of 4 shared
        10: ["src/f0.py", "other.py"],  # only 1 shared: below min_shared_files
    }
    out = check_duplicates(make_pr(files=mine), opts("duplicates"), dup_ctx(others, files))
    assert not out.ok
    assert len(out.details) == 1 and "#9" in out.details[0] and "3 of the same 4" in out.details[0]


def test_duplicates_single_shared_file_is_not_enough():
    # 100 % of my one file is shared, but one common file alone is no evidence of duplicate work
    other = {"number": 9, "title": "A", "html_url": "u9", "body": ""}
    pr = make_pr(files=[("README.md", 1, 0)])
    assert check_duplicates(pr, opts("duplicates"), dup_ctx([other], {9: ["README.md"]})).ok
    lowered = opts("duplicates", min_shared_files=1)
    assert not check_duplicates(pr, lowered, dup_ctx([other], {9: ["README.md"]})).ok


def test_duplicates_ignores_itself_and_reports_clean():
    own = {"number": 5, "title": "me", "html_url": "u5", "body": "Closes #1"}
    out = check_duplicates(make_pr(body="Closes #1", files=[("a.py", 1, 0)]), opts("duplicates"), dup_ctx([own], {}))
    assert out.ok and "0 other" in out.message


def test_duplicates_without_known_files_only_uses_issue_links():
    other = {"number": 9, "title": "x", "html_url": "u", "body": ""}
    assert check_duplicates(make_pr(files=[]), opts("duplicates"), dup_ctx([other], {9: ["a"]})).ok


# -- commands ------------------------------------------------------------------


def test_commands_skipped_unless_enabled(ctx):
    assert check_commands(make_pr(), opts("commands"), ctx).skipped
    configured = opts("commands", run=["true"])
    assert "not run" in check_commands(make_pr(), configured, ctx).message


def test_commands_pass_fail_missing_and_timeout(tmp_path):
    ctx = Context(config=merge({}), run_commands=True, cwd=str(tmp_path))
    py = sys.executable

    ok = check_commands(make_pr(), opts("commands", run=[f"{py} -c 'print(1)'"]), ctx)
    assert ok.ok and "1 command" in ok.message

    bad = check_commands(
        make_pr(), opts("commands", run=[f"{py} -c 'import sys; print(\"boom\"); sys.exit(3)'"]), ctx
    )
    assert not bad.ok and "exited with 3" in bad.details[0] and any("boom" in d for d in bad.details)

    missing = check_commands(make_pr(), opts("commands", run=["definitely-not-a-command-xyz"]), ctx)
    assert not missing.ok and "command not found" in missing.details[0]

    slow = check_commands(
        make_pr(), opts("commands", run=[f"{py} -c 'import time; time.sleep(5)'"], timeout=1), ctx
    )
    assert not slow.ok and "timed out" in slow.details[0]


# -- runner --------------------------------------------------------------------


def test_run_checks_applies_severity():
    config = merge(
        {
            "issue-link": {"severity": "warn"},
            "pr-sections": {"severity": "off"},
            "duplicates": {"severity": "off"},
        }
    )
    results = run_checks(make_pr(body="x", files=[("a.py", 1, 0)]), Context(config=config))
    by_name = {r.check: r.status for r in results}
    assert by_name["issue-link"] == "warn"
    assert "pr-sections" not in by_name and "duplicates" not in by_name
    assert by_name["ai-disclosure"] == "fail"
    assert list(CHECKS) == [r.check for r in run_checks(make_pr(), Context(config=merge({})))]


def test_github_errors_skip_instead_of_failing(monkeypatch):
    def boom(pr, opts, ctx):
        raise GitHubError("HTTP 500")

    monkeypatch.setitem(CHECKS, "duplicates", boom)
    results = run_checks(make_pr(), Context(config=merge({})))
    dup = next(r for r in results if r.check == "duplicates")
    assert dup.status == "skipped" and "HTTP 500" in dup.message
