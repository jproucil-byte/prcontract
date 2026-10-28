# Changelog

## 0.1.0

First release.

- CLI: `prcontract init`, `check`, `disclosure`.
- Eight checks: `diff-size`, `issue-link`, `pr-sections`, `ai-disclosure`, `tests-with-code`, `protected-paths`, `duplicates`, `commands`.
- Sources: local git (`base...HEAD`) and the GitHub API (`--pr`, `--github`).
- Output: text, Markdown (sticky PR comment, step summary) and versioned JSON.
- GitHub Action that never checks out or executes pull request code.
