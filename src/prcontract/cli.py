"""Command line interface: ``prcontract init | check | disclosure``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .checks import Context, run_checks
from .config import DEFAULT_CONFIG_NAME, DEFAULTS, ConfigError, load_config, starter_config
from .github import DEFAULT_API_URL, GitHubClient, GitHubError
from .report import MARKER, render, render_markdown
from .sources import SourceError, from_api, from_git, parse_pr_ref, read_event

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2


class UsageError(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prcontract",
        description="Deterministic pull-request contract checks: make a PR prove it is worth reviewing.",
    )
    parser.add_argument("--version", action="version", version=f"prcontract {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help=f"write a commented starter {DEFAULT_CONFIG_NAME}")
    init.add_argument("--path", default=DEFAULT_CONFIG_NAME)
    init.add_argument("--force", action="store_true", help="overwrite an existing file")

    check = sub.add_parser("check", help="run the checks against a branch or a GitHub pull request")
    check.add_argument("--config", help=f"config file (default: ./{DEFAULT_CONFIG_NAME} if present)")
    src = check.add_argument_group("where the change comes from (default: local git, base...HEAD)")
    src.add_argument("--base", help="base ref to diff against (default: origin's default branch)")
    src.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    src.add_argument("--pr", metavar="OWNER/REPO#N", help="check a GitHub pull request via the API")
    src.add_argument("--github", action="store_true", help="inside GitHub Actions: use the event payload")
    src.add_argument("--repo", metavar="OWNER/REPO", help="local mode: enables the duplicates check")
    desc = check.add_argument_group("pull request description (local mode)")
    desc.add_argument("--title")
    desc.add_argument("--body", help="PR description text")
    desc.add_argument("--body-file", help="read the PR description from a file ('-' for stdin)")
    desc.add_argument("--author", help="GitHub login, used by protected-paths allow_authors")
    check.add_argument("--run", action="store_true", help="execute [commands].run (trusted local checkouts only)")
    check.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    check.add_argument("--comment", action="store_true", help="post/update one sticky PR comment (needs --pr or --github)")
    check.add_argument("--summary", action="store_true", help="append Markdown to $GITHUB_STEP_SUMMARY")
    check.add_argument("--fail-on", choices=("error", "warn", "never"), default="error")
    check.add_argument("--api-url", help="GitHub API URL (default: $GITHUB_API_URL or api.github.com)")

    disc = sub.add_parser("disclosure", help="print an AI-disclosure block for your PR description")
    disc.add_argument("--config")
    disc.add_argument("--none", action="store_true", help="no AI assistance was used")
    disc.add_argument("--tool", action="append", default=[], metavar="NAME", help="tool you used (repeatable)")
    disc.add_argument("--reviewed", action="store_true", help="add a line saying you reviewed every changed line")
    disc.add_argument("--tested", metavar="COMMAND", help="command you ran to test the change")
    return parser


# -- commands -------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        raise UsageError(f"{path} already exists (use --force to overwrite)")
    path.write_text(starter_config(), encoding="utf-8")
    print(f"wrote {path}")
    return EXIT_OK


def _read_body(args: argparse.Namespace) -> str | None:
    if args.body is not None and args.body_file:
        raise UsageError("use either --body or --body-file, not both")
    if args.body is not None:
        return args.body
    if args.body_file:
        if args.body_file == "-":
            return sys.stdin.read()
        try:
            return Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read {args.body_file}: {exc}") from exc
    return None


def _exit_code(results, fail_on: str) -> int:
    if fail_on == "never":
        return EXIT_OK
    bad = {"fail"} if fail_on == "error" else {"fail", "warn"}
    return EXIT_FAIL if any(r.status in bad for r in results) else EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    api_mode = bool(args.github or args.pr)
    if args.github and args.pr:
        raise UsageError("use either --github or --pr, not both")
    if api_mode and (args.base or args.run or args.body is not None or args.body_file):
        raise UsageError("--base, --run and --body/--body-file only apply to local checkouts, not --pr/--github")
    if args.comment and not api_mode:
        raise UsageError("--comment needs --pr or --github")

    config = load_config(args.config)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    api_url = args.api_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API_URL
    client = GitHubClient(token, api_url) if (api_mode or args.repo) else None

    repo = number = None
    if args.github:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise UsageError("--github needs GITHUB_EVENT_PATH (run inside GitHub Actions)")
        repo, number = read_event(event_path, os.environ.get("GITHUB_REPOSITORY"))
    elif args.pr:
        repo, number = parse_pr_ref(args.pr)

    if api_mode:
        pr = from_api(client, repo, number)
    else:
        pr = from_git(args.base, args.head)
        pr.body = _read_body(args)
        pr.title = args.title or (pr.commits[0].message.splitlines()[0] if pr.commits else "")
        pr.author = args.author
        pr.repo = args.repo

    results = run_checks(pr, Context(config=config, client=client, run_commands=args.run))
    print(render(results, args.format))

    if args.github or args.summary:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(render_markdown(results) + "\n")
    if args.comment:
        try:
            client.upsert_comment(repo, number, MARKER, render_markdown(results))
        except GitHubError as exc:
            # Fork PRs get a read-only token; the exit code still carries the verdict.
            print(f"prcontract: warning: could not post the PR comment: {exc}", file=sys.stderr)
    return _exit_code(results, args.fail_on)


def cmd_disclosure(args: argparse.Namespace) -> int:
    if args.none == bool(args.tool):
        raise UsageError("say either --none or at least one --tool NAME")
    marker = DEFAULTS["ai-disclosure"]["marker"]
    try:
        marker = load_config(args.config).section("ai-disclosure")["marker"]
    except ConfigError:
        pass
    lines = ["## AI disclosure", "", f"{marker}: {'none' if args.none else ', '.join(args.tool)}"]
    if args.reviewed:
        lines.append("Human review: I read and understand every changed line.")
    if args.tested:
        lines.append(f"Testing: ran `{args.tested}` locally.")
    print("\n".join(lines))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        if args.command == "check":
            return cmd_check(args)
        return cmd_disclosure(args)
    except (UsageError, ConfigError, SourceError, GitHubError) as exc:
        print(f"prcontract: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
