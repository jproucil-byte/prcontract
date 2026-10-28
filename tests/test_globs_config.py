import tomllib
from pathlib import Path

import pytest

from prcontract import __version__
from prcontract.config import DEFAULTS, ConfigError, load_config, merge, starter_config
from prcontract.globs import match_any, matches


@pytest.mark.parametrize(
    "path,pattern,expected",
    [
        ("src/a.py", "src/**", True),
        ("src/x/y/a.py", "src/**", True),
        ("srcx/a.py", "src/**", False),
        ("test_a.py", "**/test_*", True),
        ("pkg/sub/test_a.py", "**/test_*", True),
        ("pkg/sub/a_test.go", "**/*_test.*", True),
        ("README.md", "**/*.md", True),
        ("docs/guide/x.md", "**/*.md", True),
        ("a/b.py", "*.py", False),  # a single * never crosses a directory
        ("b.py", "*.py", True),
        ("LICENSE", "LICENSE*", True),
        ("LICENSE.md", "LICENSE*", True),
        ("sub/LICENSE", "LICENSE*", False),
        ("sub/LICENSE", "**/LICENSE*", True),
        ("./src/a.py", "src/**", True),
        ("src\\a.py", "src/**", True),
        ("v1/a.py", "v[0-9]/*.py", True),
        ("va/a.py", "v[!a]/*.py", False),
        ("a.c", "?.c", True),
        ("ab.c", "?.c", False),
        ("a.b", "a.b", True),
        ("axb", "a.b", False),  # the dot is literal
    ],
)
def test_glob(path, pattern, expected):
    assert matches(path, pattern) is expected


def test_match_any():
    assert match_any("src/a.py", ["docs/**", "src/**"])
    assert not match_any("x/a.py", [])


def test_defaults_load_without_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.severity("diff-size") == "error"
    assert cfg.section("commands")["run"] == []


def test_starter_config_round_trips_to_defaults():
    parsed = tomllib.loads(starter_config())
    assert parsed == DEFAULTS
    assert merge(parsed).sections == merge({}).sections


def test_user_values_override_and_do_not_mutate_defaults():
    cfg = merge({"diff-size": {"max_files": 5, "severity": "warn"}})
    assert cfg.section("diff-size")["max_files"] == 5
    assert cfg.severity("diff-size") == "warn"
    assert DEFAULTS["diff-size"]["max_files"] == 30
    cfg.section("pr-sections")["required"].append("Extra")
    assert "Extra" not in DEFAULTS["pr-sections"]["required"]


@pytest.mark.parametrize(
    "data,fragment",
    [
        ({"dif-size": {}}, "unknown check 'dif-size'"),
        ({"diff-size": {"max_file": 3}}, "unknown option 'max_file'"),
        ({"diff-size": {"max_files": "3"}}, "must be an integer"),
        ({"diff-size": {"max_files": True}}, "must be an integer"),
        ({"issue-link": {"allow_ref_only": "yes"}}, "must be true or false"),
        ({"pr-sections": {"required": "What"}}, "must be a list of strings"),
        ({"pr-sections": {"required": [1]}}, "must be a list of strings"),
        ({"diff-size": {"severity": "fatal"}}, "severity must be one of"),
        ({"diff-size": 3}, "must be a table"),
    ],
)
def test_invalid_config(data, fragment):
    with pytest.raises(ConfigError, match=fragment):
        merge(data)


def test_load_errors(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[diff-size\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(bad)
    wrong = tmp_path / "wrong.toml"
    wrong.write_text("[diff-size]\nmax_files = 'x'\n")
    with pytest.raises(ConfigError, match=r"wrong\.toml.*max_files"):
        load_config(wrong)


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert declared == __version__
