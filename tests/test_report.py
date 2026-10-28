import json

from prcontract.model import Result
from prcontract.report import counts, render, render_json, render_markdown, render_text, summary_line

RESULTS = [
    Result("diff-size", "pass", "2 files, 10 lines changed"),
    Result("issue-link", "fail", "No linked issue found.", ("see CONTRIBUTING",)),
    Result("pr-sections", "warn", "Incomplete | really", ("missing section: Testing",)),
    Result("duplicates", "skipped", "needs the GitHub API"),
]


def test_counts_and_summary():
    assert counts(RESULTS) == {"pass": 1, "fail": 1, "warn": 1, "skipped": 1}
    assert summary_line(RESULTS) == "1 failed, 1 warning(s), 1 passed, 1 skipped"


def test_text_output_lists_details():
    text = render_text(RESULTS)
    assert "[FAIL] issue-link" in text and "- see CONTRIBUTING" in text
    assert "[skip] duplicates" in text
    text.encode("ascii")  # plain ASCII so Windows consoles never choke


def test_json_output_is_stable_and_valid():
    payload = json.loads(render_json(RESULTS))
    assert payload["version"] == 1 and payload["tool"]["name"] == "prcontract"
    assert payload["results"][1] == {
        "check": "issue-link", "status": "fail", "message": "No linked issue found.", "details": ["see CONTRIBUTING"],
    }


def test_markdown_escapes_pipes_and_shows_details_only_for_problems():
    md = render_markdown(RESULTS)
    assert md.startswith("<!-- prcontract -->\n### ❌ PR contract")
    assert "Incomplete \\| really" in md
    assert "- missing section: Testing" in md and "**`issue-link`**" in md
    assert "**`diff-size`**" not in md


def test_markdown_headline_levels():
    assert "### ✅" in render_markdown([Result("a", "pass", "ok")])
    assert "### ⚠️" in render_markdown([Result("a", "warn", "hmm")])
    assert render([], "text").startswith("prcontract: 0 failed")
