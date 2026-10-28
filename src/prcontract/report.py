"""Rendering: plain text, Markdown (PR comment / step summary) and JSON."""

from __future__ import annotations

import json
from collections import Counter

from . import __version__
from .model import Result

MARKER = "<!-- prcontract -->"

_TEXT_LABEL = {"pass": "ok", "fail": "FAIL", "warn": "warn", "skipped": "skip"}
_EMOJI = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skipped": "➖"}


def counts(results: list[Result]) -> dict[str, int]:
    c = Counter(r.status for r in results)
    return {s: c.get(s, 0) for s in ("pass", "fail", "warn", "skipped")}


def summary_line(results: list[Result]) -> str:
    c = counts(results)
    return f"{c['fail']} failed, {c['warn']} warning(s), {c['pass']} passed, {c['skipped']} skipped"


def render_text(results: list[Result]) -> str:
    width = max((len(r.check) for r in results), default=0)
    lines = [f"prcontract: {summary_line(results)}", ""]
    for r in results:
        lines.append(f"[{_TEXT_LABEL[r.status]:>4}] {r.check:<{width}}  {r.message}")
        lines.extend(f"         {'':<{width}}  - {d}" for d in r.details)
    return "\n".join(lines)


def render_json(results: list[Result]) -> str:
    payload = {
        "version": 1,
        "tool": {"name": "prcontract", "version": __version__},
        "summary": counts(results),
        "results": [
            {"check": r.check, "status": r.status, "message": r.message, "details": list(r.details)}
            for r in results
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(results: list[Result]) -> str:
    c = counts(results)
    headline = "❌" if c["fail"] else ("⚠️" if c["warn"] else "✅")
    lines = [
        MARKER,
        f"### {headline} PR contract",
        "",
        summary_line(results),
        "",
        "| | Check | Result |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {_EMOJI[r.status]} | `{r.check}` | {_cell(r.message)} |")
    detailed = [r for r in results if r.details and r.status in ("fail", "warn")]
    if detailed:
        lines.append("")
        for r in detailed:
            lines.append(f"**`{r.check}`**")
            lines.extend(f"- {d.strip()}" for d in r.details)
            lines.append("")
    lines.append("")
    lines.append(
        f"<sub>Deterministic checks, no AI involved (prcontract {__version__}). "
        "Run it locally before pushing: `prcontract check`.</sub>"
    )
    return "\n".join(lines)


def render(results: list[Result], fmt: str) -> str:
    if fmt == "json":
        return render_json(results)
    if fmt == "markdown":
        return render_markdown(results)
    return render_text(results)
