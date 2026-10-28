"""Configuration: defaults, validation and the starter file written by ``init``."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAME = ".prcontract.toml"
SEVERITIES = ("error", "warn", "off")

AI_TOOL_KEYWORDS = [
    "claude", "anthropic", "copilot", "cursor", "codex", "aider",
    "gemini", "chatgpt", "openai", "devin", "windsurf",
]  # fmt: skip

DEFAULTS: dict[str, dict[str, Any]] = {
    "diff-size": {
        "severity": "error",
        "max_files": 30,
        "max_lines": 1000,
        "ignore": [
            "**/*.lock",
            "**/package-lock.json",
            "**/pnpm-lock.yaml",
            "**/yarn.lock",
            "**/*.min.js",
            "**/*.snap",
        ],
    },
    "issue-link": {
        "severity": "error",
        "allow_ref_only": True,
        "exempt_title_prefixes": ["docs", "chore", "typo", "ci"],
    },
    "pr-sections": {
        "severity": "warn",
        "required": ["What", "Why", "Testing"],
        "min_chars": 10,
    },
    "ai-disclosure": {
        "severity": "error",
        "marker": "AI assistance",
        "tools": AI_TOOL_KEYWORDS,
    },
    "tests-with-code": {
        "severity": "warn",
        "source": ["src/**", "lib/**", "app/**"],
        "tests": ["tests/**", "test/**", "**/test_*", "**/*_test.*", "**/*.test.*", "**/*.spec.*"],
        "exempt": [],
    },
    "protected-paths": {
        "severity": "error",
        "paths": [".github/workflows/**", "LICENSE*"],
        "allow_authors": [],
    },
    "duplicates": {
        "severity": "warn",
        "max_open_prs": 30,
        "min_shared_files": 2,
        "overlap": 0.6,
    },
    "commands": {
        "severity": "error",
        "run": [],
        "timeout": 600,
    },
}

COMMENTS: dict[str, str] = {
    "diff-size": "Reject PRs that are too big to review in one sitting (lockfiles etc. are ignored).",
    "issue-link": "Require a linked issue (\"Closes #123\"). Titles starting with an exempt prefix skip this.",
    "pr-sections": "Require headings in the PR description, each with real content (HTML comments do not count).",
    "ai-disclosure": "Require a line like \"AI assistance: none\" or \"AI assistance: <tool>\" and make it consistent\n"
    "with AI trailers in commit messages (Co-authored-by, \"Generated with ...\"). This verifies\n"
    "honesty about disclosure; it does NOT try to guess whether text was written by an AI.",
    "tests-with-code": "If files matching `source` change, something matching `tests` must change too.",
    "protected-paths": "Changes to these paths need a maintainer (listed in allow_authors, GitHub logins).",
    "duplicates": "GitHub modes only: warn when an open PR links the same issue or touches many of the same files.",
    "commands": "Commands that must succeed. Only run with `prcontract check --run` in a trusted local checkout,\n"
    "never by the GitHub Action (which does not execute PR code).",
}


class ConfigError(Exception):
    """Raised for unreadable or invalid configuration."""


@dataclass
class Config:
    sections: dict[str, dict[str, Any]]

    def section(self, name: str) -> dict[str, Any]:
        return self.sections[name]

    def severity(self, name: str) -> str:
        return self.sections[name]["severity"]


def _check_type(where: str, key: str, value: Any, default: Any) -> None:
    label = f"[{where}] {key}"
    if isinstance(default, bool):
        ok = isinstance(value, bool)
        expected = "true or false"
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
        expected = "an integer"
    elif isinstance(default, float):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        expected = "a number"
    elif isinstance(default, str):
        ok = isinstance(value, str)
        expected = "a string"
    else:
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        expected = "a list of strings"
    if not ok:
        raise ConfigError(f"{label} must be {expected}, got {value!r}")


def merge(data: dict[str, Any]) -> Config:
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        raise ConfigError(
            f"unknown check {unknown[0]!r} (known: {', '.join(DEFAULTS)})"
        )
    merged: dict[str, dict[str, Any]] = {}
    for name, defaults in DEFAULTS.items():
        user = data.get(name, {})
        if not isinstance(user, dict):
            raise ConfigError(f"[{name}] must be a table")
        bad = sorted(set(user) - set(defaults))
        if bad:
            raise ConfigError(
                f"[{name}] unknown option {bad[0]!r} (known: {', '.join(defaults)})"
            )
        section = {k: (list(v) if isinstance(v, list) else v) for k, v in defaults.items()}
        for key, value in user.items():
            _check_type(name, key, value, defaults[key])
            section[key] = value
        if section["severity"] not in SEVERITIES:
            raise ConfigError(
                f"[{name}] severity must be one of {', '.join(SEVERITIES)}, got {section['severity']!r}"
            )
        merged[name] = section
    return Config(merged)


def load_config(path: str | Path | None = None) -> Config:
    """Load ``path`` (or ``.prcontract.toml`` in the cwd, or built-in defaults)."""
    if path is None:
        candidate = Path(DEFAULT_CONFIG_NAME)
        if not candidate.is_file():
            return merge({})
        file = candidate
    else:
        file = Path(path)
        if not file.is_file():
            raise ConfigError(f"config file not found: {file}")
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file}: invalid TOML: {exc}") from exc
    try:
        return merge(data)
    except ConfigError as exc:
        raise ConfigError(f"{file}: {exc}") from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    inline = "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if len(inline) <= 78:
        return inline
    return "[\n" + "".join(f"    {_toml_value(v)},\n" for v in value) + "]"


def starter_config() -> str:
    """Render DEFAULTS as a commented TOML file (so docs can never drift)."""
    lines = [
        "# prcontract configuration.",
        '# severity: "error" fails the check, "warn" reports it without failing, "off" disables it.',
        "",
    ]
    for name, section in DEFAULTS.items():
        for comment_line in COMMENTS[name].splitlines():
            lines.append(f"# {comment_line}")
        lines.append(f"[{name}]")
        for key, value in section.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)
