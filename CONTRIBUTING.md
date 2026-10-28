# Contributing

Thanks for helping. This project checks its own pull requests with prcontract, so the rules below are enforced automatically. You can run the same checks locally first:

```sh
pip install -e ".[dev]"
pytest
prcontract check --base origin/main --body-file my-pr-description.md --run
```

## Before you open a PR

1. **Open or pick an issue first** and say you are working on it. PRs without a linked issue (`Closes #123`) fail the `issue-link` check, except for `docs:`, `chore:`, `typo` and `ci:` titles.
2. **Keep it small.** One concern per PR, under about 1000 changed lines.
3. **Add tests** for behaviour you change. Every check has unit tests, and new checks need a test that fails when the check is broken.
4. **Fill in the template**: What, Why, Testing, and the AI disclosure line.

## AI assistance

Using AI tools is fine. Hiding it is not. Add `AI assistance: none` or `AI assistance: <tools>` to the PR description, and read every line you submit. If your commits carry `Co-authored-by` trailers from a coding agent, the description has to say so.

## Style

- No runtime dependencies. Standard library only.
- Messages shown to users say what is wrong and how to fix it.
- Checks never guess. If something cannot be verified, it is skipped, not failed.
