# prcontract

**Make a pull request prove it is worth reviewing.**

`prcontract` checks a pull request against a contract that lives in your repository: linked issue, sensible size, a real description, tests next to code, an honest AI disclosure, protected paths, no duplicate work. It runs locally for contributors and as a GitHub Action for maintainers.

It is **deterministic**. No LLM, no network calls except the GitHub API, and **no attempt to guess whether text was written by an AI**.

## Why

Maintainers are drowning in low-effort contributions. A [Linux Foundation survey (Oct 2026)](https://www.linuxfoundation.org/press/new-lf-research-survey-shows-generative-ai-cuts-open-source-coding-time-in-half-but-25-of-incoming-pull-requests-are-considered-ai-slop) reports maintainers estimating that 25 % of incoming pull requests are AI slop (31 % on large projects), and it recommends disclosure and testing before submission. GitHub has responded with [settings to restrict or disable pull requests](https://heise.de/-11176690) altogether.

Most tooling in this space tries to *detect* AI-written text. Detection is guesswork: it produces false positives for honest contributors and is trivially bypassed by everyone else. `prcontract` takes the opposite approach and verifies things that anyone, human or agent, can actually prove:

- the PR links an issue that someone agreed was worth solving,
- it is small enough to review,
- the description is filled in, not a pristine template,
- code changes come with tests,
- the AI disclosure exists and **does not contradict the commit trailers**,
- nobody else is already working on the same thing.

A contributor (or their coding agent) can run the same checks before opening the PR, so maintainers only see PRs that already passed.

## Checks

| Check | Verifies | Default |
|---|---|---|
| `diff-size` | files and changed lines below limits; lockfiles and snapshots ignored | error |
| `issue-link` | `Closes #123`, `Fixes owner/repo#4`, an issue URL, or `Refs #5` in the description or a commit | error |
| `pr-sections` | required headings (`What`, `Why`, `Testing`) exist with real content; HTML comments and empty checkboxes do not count | warn |
| `ai-disclosure` | an `AI assistance: …` line exists, is not a placeholder, and `none` is not contradicted by `Co-authored-by: Claude`-style or `Generated with …` trailers | error |
| `tests-with-code` | if files under `source` globs change, something under `tests` changes too | warn |
| `protected-paths` | workflows, licence etc. are only changed by listed maintainers | error |
| `duplicates` | an open PR links the same issue or touches most of the same files (GitHub modes) | warn |
| `commands` | your test/lint commands succeed (only with `--run`, local only) | error |

Every check can be set to `error`, `warn` or `off`.

## Quick start

Not on PyPI yet. Install straight from GitHub (Python 3.11+, no dependencies):

```sh
pip install git+https://github.com/jproucil-byte/prcontract
```

```sh
prcontract init                                   # write a commented .prcontract.toml
prcontract disclosure --tool "Claude Code" --reviewed --tested "pytest -q" >> pr.md
prcontract check --base origin/main --body-file pr.md
```

```text
prcontract: 1 failed, 1 warning(s), 4 passed, 2 skipped

[  ok] diff-size        2 files, 14 lines changed (limits 30 / 1000)
[FAIL] issue-link       No linked issue found. Find or open an issue first, then add a line such as "Closes #123" ...
[  ok] pr-sections      All required sections present: What, Why, Testing
[  ok] ai-disclosure    AI assistance: Claude Code
[warn] tests-with-code  Source files changed but no tests were added or updated.
                          - src/app.py
...
```

Add `--repo owner/name` to also run the `duplicates` check from your machine (set `GITHUB_TOKEN` for a higher rate limit).

## GitHub Action

```yaml
# .github/workflows/pr-contract.yml
name: PR contract
on:
  pull_request_target:
    types: [opened, edited, synchronize, reopened, ready_for_review]
permissions:
  contents: read
  pull-requests: write
jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jproucil-byte/prcontract@v0
```

It posts one sticky comment (updated on every push), writes the step summary and fails the job when a check set to `error` fails. Inputs: `config`, `comment`, `fail-on` (`error`, `warn`, `never`).

Also copy [`examples/PULL_REQUEST_TEMPLATE.md`](examples/PULL_REQUEST_TEMPLATE.md) to `.github/` so contributors start with the right headings.

### Security model

Running automation on pull requests from forks is a classic way to leak secrets, so the Action is built to be boring:

- It uses `pull_request_target`, so the token can comment on fork PRs, **but it never checks out or executes the PR's code**. The change is read through the GitHub API (file list with line counts, commit messages, description).
- The config always comes from your base branch, so a PR cannot weaken its own contract.
- `[commands].run` is never executed by the Action. It exists for contributors running `prcontract check --run` on their own machine.
- The token is only sent to the API origin; pagination links pointing anywhere else are refused.
- If the API is unreachable or the comment cannot be posted, the verdict still comes from the exit code and nothing leaks into the log.

Do not add a step to this workflow that checks out the PR head.

## Configuration

`prcontract init` writes the full, commented reference. A few examples:

```toml
[diff-size]
max_lines = 400

[issue-link]
exempt_title_prefixes = ["docs", "chore"]   # "docs: fix typo" needs no issue

[tests-with-code]
severity = "error"
source = ["src/**", "lib/**"]
tests  = ["tests/**", "**/*_test.go"]

[protected-paths]
paths = [".github/workflows/**", "LICENSE*", "SECURITY.md"]
allow_authors = ["your-maintainer-login"]

[commands]
run = ["pytest -q", "ruff check ."]
```

Globs follow the usual rules: `*` stays inside one directory, `**/` matches any depth.

## Output and exit codes

`--format text|markdown|json`. JSON is versioned (`"version": 1`) for scripts and agents:

```json
{"version": 1, "summary": {"pass": 4, "fail": 1, "warn": 1, "skipped": 2},
 "results": [{"check": "issue-link", "status": "fail", "message": "...", "details": []}]}
```

| Exit code | Meaning |
|---|---|
| 0 | no failing check (`--fail-on warn` also fails on warnings, `never` always exits 0) |
| 1 | at least one check failed |
| 2 | usage, configuration, git or API error |

Checks that cannot run (no description supplied, no API access) are reported as `skipped`, never as failures.

## What it does not do

- It does not judge code quality and cannot tell a thoughtful change from a plausible-looking one. It raises the cost of careless PRs; it does not replace review.
- It does not detect AI usage. The disclosure check catches *contradictions* between what the author says and what the commits say. A determined liar can strip trailers.
- `diff-size` uses fixed limits. A per-repository baseline derived from past merged PRs is on the roadmap.
- `tests-with-code` and `duplicates` are path and reference heuristics. Duplicates only scans the 30 most recent open PRs.
- GitHub only for now.

## Related projects

- [CHAOSS ai-detection-action](https://pkg.go.dev/github.com/chaoss/ai-detection-action) labels PRs that *declare* AI involvement through trailers and text. It pairs well with `prcontract`.
- [Maniflight](https://github.com/agrovr/maniflight/releases/tag/v1.0.0) explains what currently blocks a pull request (reviews, checks, branch rules). It is read-only diagnostics, a different job.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The package has no runtime dependencies. See [CONTRIBUTING.md](CONTRIBUTING.md); this repository checks its own PRs with prcontract.

## License

MIT
