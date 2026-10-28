# Changelog

## Unreleased

- Fix: on Windows, quoted arguments in `[commands].run` (`python -c 'print(1)'`) were passed with the quote characters still attached, so such commands silently did the wrong thing. Quoting now behaves the same on every OS. Found by the first CI run on `windows-latest`.

## 0.1.0

First release.

- CLI: `prcontract init`, `check`, `disclosure`.
- Eight checks: `diff-size`, `issue-link`, `pr-sections`, `ai-disclosure`, `tests-with-code`, `protected-paths`, `duplicates`, `commands`.
- Sources: local git (`base...HEAD`) and the GitHub API (`--pr`, `--github`).
- Output: text, Markdown (sticky PR comment, step summary) and versioned JSON.
- GitHub Action that never checks out or executes pull request code.
