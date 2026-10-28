"""The checks. Each is a pure function: (PullRequest, options, Context) -> Outcome.

Nothing here guesses who or what wrote a change. Every check verifies something
that can be verified: size, links, structure, consistency, paths, test results.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .github import GitHubClient, GitHubError
from .globs import match_any
from .model import Outcome, PullRequest, Result


@dataclass
class Context:
    config: Config
    client: GitHubClient | None = None
    run_commands: bool = False
    cwd: str = "."


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _strip_comments(text: str) -> str:
    return _HTML_COMMENT.sub("", text)


def _non_ignored(pr: PullRequest, ctx: Context):
    ignore = ctx.config.section("diff-size")["ignore"]
    return [f for f in pr.files if not match_any(f.path, ignore)]


# -- diff-size ------------------------------------------------------------------


def check_diff_size(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    counted = _non_ignored(pr, ctx)
    files = len(counted)
    lines = sum(f.lines for f in counted)
    problems = []
    if files > opts["max_files"]:
        problems.append(f"{files} files changed (limit {opts['max_files']})")
    if lines > opts["max_lines"]:
        problems.append(f"{lines} lines changed (limit {opts['max_lines']})")
    if not problems:
        return Outcome(True, f"{files} files, {lines} lines changed (limits {opts['max_files']} / {opts['max_lines']})")
    largest = sorted(counted, key=lambda f: f.lines, reverse=True)[:3]
    return Outcome(
        False,
        "Too large to review in one go: " + "; ".join(problems) + ". Split it into smaller, focused PRs.",
        [f"largest: {f.path} ({f.lines} lines)" for f in largest],
    )


# -- issue-link -----------------------------------------------------------------

_REPO = r"(?:[\w.-]+/[\w.-]+)?"
_ISSUE = rf"(?:{_REPO}#(\d+)|https?://github\.com/[\w.-]+/[\w.-]+/issues/(\d+))"
_CLOSING = re.compile(rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+{_ISSUE}", re.I)
_REFERENCE = re.compile(rf"\b(?:refs?|related to|part of|towards?|see)\b:?\s+{_ISSUE}", re.I)
_BARE_URL = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+/issues/(\d+)")


def find_issue_refs(text: str, allow_ref_only: bool = True) -> list[int]:
    """Issue numbers referenced in ``text``, sorted and de-duplicated."""
    text = _strip_comments(text)
    found = {int(a or b) for a, b in _CLOSING.findall(text)}
    if allow_ref_only:
        found |= {int(a or b) for a, b in _REFERENCE.findall(text)}
        found |= {int(n) for n in _BARE_URL.findall(text)}
    return sorted(found)


def check_issue_link(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    title = pr.title.strip().lower()
    for prefix in opts["exempt_title_prefixes"]:
        if re.match(rf"{re.escape(prefix.lower())}\b", title):
            return Outcome(True, f"exempt: title starts with {prefix!r}")
    refs = find_issue_refs(pr.text(), opts["allow_ref_only"])
    if refs:
        return Outcome(True, "Linked issue(s): " + ", ".join(f"#{n}" for n in refs))
    return Outcome(
        False,
        'No linked issue found. Find or open an issue first, then add a line such as "Closes #123" '
        "to the PR description or a commit message.",
    )


# -- pr-sections ----------------------------------------------------------------

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*", re.M)


def parse_sections(body: str) -> dict[str, str]:
    """Map lower-cased heading -> text below it (HTML comments removed)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in _strip_comments(body).splitlines():
        match = _HEADING.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = match.group(1).strip().rstrip(":").strip().lower()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def check_pr_sections(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    required = opts["required"]
    if not required:
        return Outcome(skipped=True, message="no sections configured")
    if pr.body is None:
        return Outcome(skipped=True, message="no PR description supplied (use --body-file)")
    sections = parse_sections(pr.body)
    problems = []
    for name in required:
        wanted = name.lower()
        key = next((k for k in sections if k == wanted or k.startswith(wanted + " ")), None)
        if key is None:
            problems.append(f"missing section: {name}")
            continue
        visible = _CHECKBOX.sub("", sections[key]).strip()
        if len(visible) < opts["min_chars"]:
            problems.append(f"section '{name}' is empty or shorter than {opts['min_chars']} characters")
    if problems:
        return Outcome(False, "The PR description is incomplete.", problems)
    return Outcome(True, "All required sections present: " + ", ".join(required))


# -- ai-disclosure --------------------------------------------------------------

_NONE_WORDS = {"none", "no", "n/a", "na", "not used", "no ai", "nothing", "-", "–"}


@dataclass
class Disclosure:
    value: str

    @property
    def is_none(self) -> bool:
        return self.value.strip().rstrip(".").lower() in _NONE_WORDS

    @property
    def is_placeholder(self) -> bool:
        v = self.value.strip()
        return v == "" or (v.startswith("<") and v.endswith(">")) or v.lower() in {"...", "tbd", "todo", "?"}


def parse_disclosure(body: str, marker: str) -> Disclosure | None:
    wanted = marker.lower()
    for line in _strip_comments(body).splitlines():
        clean = line.strip().lstrip("-*+•> \t").replace("**", "").replace("__", "").strip()
        if not clean.lower().startswith(wanted):
            continue
        rest = clean[len(wanted) :].lstrip()
        if rest == "":
            return Disclosure("")
        if rest[0] in ":–—-":
            return Disclosure(rest[1:].strip())
    return None


def ai_trailer_lines(pr: PullRequest, tools: list[str]) -> list[str]:
    hits = []
    for commit in pr.commits:
        for line in commit.message.splitlines():
            low = line.lower().strip()
            if not (low.startswith("co-authored-by:") or "generated with" in low or "generated by" in low):
                continue
            if any(t.lower() in low for t in tools):
                hits.append(f"{commit.sha[:7]}: {line.strip()}")
    return hits


def check_ai_disclosure(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    if pr.body is None:
        return Outcome(skipped=True, message="no PR description supplied (use --body-file)")
    marker = opts["marker"]
    disclosure = parse_disclosure(pr.body, marker)
    if disclosure is None:
        return Outcome(
            False,
            f'No disclosure line. Add "{marker}: none" or "{marker}: <tools you used>" to the PR description '
            "(run `prcontract disclosure` to generate a block).",
        )
    if disclosure.is_placeholder:
        return Outcome(False, f'The "{marker}" line is empty or still a placeholder. Say "none" or name the tools.')
    trailers = ai_trailer_lines(pr, opts["tools"])
    if disclosure.is_none and trailers:
        return Outcome(
            False,
            f'The description says "{marker}: none" but commit messages carry AI trailers. '
            "Correct one of them.",
            trailers[:5],
        )
    return Outcome(True, f"{marker}: {disclosure.value.strip() or 'none'}")


# -- tests-with-code ------------------------------------------------------------


def check_tests_with_code(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    paths = [f.path for f in pr.files]
    source = [
        p
        for p in paths
        if match_any(p, opts["source"]) and not match_any(p, opts["tests"]) and not match_any(p, opts["exempt"])
    ]
    if not source:
        return Outcome(True, "No source files changed that require tests")
    if any(match_any(p, opts["tests"]) for p in paths):
        return Outcome(True, f"{len(source)} source file(s) changed together with tests")
    shown = source[:5] + ([f"... and {len(source) - 5} more"] if len(source) > 5 else [])
    return Outcome(False, "Source files changed but no tests were added or updated.", shown)


# -- protected-paths ------------------------------------------------------------


def check_protected_paths(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    touched = [f.path for f in pr.files if match_any(f.path, opts["paths"])]
    if not touched:
        return Outcome(True, "No protected paths touched")
    allowed = {a.lower() for a in opts["allow_authors"]}
    if pr.author and pr.author.lower() in allowed:
        return Outcome(True, f"Protected paths touched by allowed author @{pr.author}")
    return Outcome(
        False,
        "Protected paths changed; a maintainer needs to make or approve these changes.",
        touched[:10],
    )


# -- duplicates -----------------------------------------------------------------


def check_duplicates(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    if ctx.client is None or not pr.repo:
        return Outcome(skipped=True, message="needs the GitHub API (use --pr, --github or --repo)")
    open_prs = [p for p in ctx.client.list_open_prs(pr.repo, limit=opts["max_open_prs"]) if p.get("number") != pr.number]
    mine_issues = set(find_issue_refs(pr.text(), True))
    mine_files = {f.path for f in _non_ignored(pr, ctx)}
    overlapping: list[str] = []
    for other in open_prs:
        number = other["number"]
        label = f"#{number} {other.get('title', '')!r} ({other.get('html_url', '')})"
        shared_issues = mine_issues & set(find_issue_refs(other.get("body") or "", True))
        if shared_issues:
            overlapping.append(f"{label}: same issue " + ", ".join(f"#{n}" for n in sorted(shared_issues)))
            continue
        if not mine_files:
            continue
        theirs = {f["filename"] for f in ctx.client.list_pr_files(pr.repo, number, limit=300)}
        shared = mine_files & theirs
        if len(shared) >= opts["min_shared_files"] and len(shared) / len(mine_files) >= opts["overlap"]:
            overlapping.append(f"{label}: touches {len(shared)} of the same {len(mine_files)} files")
    if overlapping:
        return Outcome(
            False,
            f"{len(overlapping)} open PR(s) overlap with this one. Check them before investing review time.",
            overlapping,
        )
    return Outcome(True, f"No overlap with {len(open_prs)} other open PR(s)")


# -- commands -------------------------------------------------------------------


def check_commands(pr: PullRequest, opts: dict[str, Any], ctx: Context) -> Outcome:
    commands = opts["run"]
    if not commands:
        return Outcome(skipped=True, message="no commands configured")
    if not ctx.run_commands:
        return Outcome(skipped=True, message="not run (use --run in a trusted local checkout)")
    failures: list[str] = []
    details: list[str] = []
    for command in commands:
        try:
            proc = subprocess.run(
                shlex.split(command, posix=(os.name != "nt")),
                cwd=ctx.cwd,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=opts["timeout"],
            )
        except FileNotFoundError:
            failures.append(f"`{command}`: command not found")
            continue
        except subprocess.TimeoutExpired:
            failures.append(f"`{command}`: timed out after {opts['timeout']}s")
            continue
        if proc.returncode != 0:
            failures.append(f"`{command}` exited with {proc.returncode}")
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
            details.extend(f"    {line}" for line in tail)
    if failures:
        return Outcome(False, "Required commands failed.", failures + details)
    return Outcome(True, f"All {len(commands)} command(s) passed")


CHECKS: dict[str, Callable[[PullRequest, dict[str, Any], Context], Outcome]] = {
    "diff-size": check_diff_size,
    "issue-link": check_issue_link,
    "pr-sections": check_pr_sections,
    "ai-disclosure": check_ai_disclosure,
    "tests-with-code": check_tests_with_code,
    "protected-paths": check_protected_paths,
    "duplicates": check_duplicates,
    "commands": check_commands,
}


def run_checks(pr: PullRequest, ctx: Context) -> list[Result]:
    results: list[Result] = []
    for name, check in CHECKS.items():
        severity = ctx.config.severity(name)
        if severity == "off":
            continue
        try:
            outcome = check(pr, ctx.config.section(name), ctx)
        except GitHubError as exc:
            # A flaky network must never fail somebody's pull request.
            outcome = Outcome(skipped=True, message=f"skipped: GitHub API error: {exc}")
        if outcome.skipped:
            status = "skipped"
        elif outcome.ok:
            status = "pass"
        else:
            status = "fail" if severity == "error" else "warn"
        results.append(Result(name, status, outcome.message, tuple(outcome.details)))
    return results
