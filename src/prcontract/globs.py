"""Tiny gitignore-style glob matcher.

``fnmatch`` lets ``*`` cross directory separators and has no ``**``, which makes
patterns like ``**/test_*.py`` behave surprisingly. This one follows the rules
people expect: ``*`` and ``?`` stay inside one path segment, ``**/`` matches
zero or more directories, ``**`` at the end matches everything below.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    i, n = 0, len(pattern)
    out: list[str] = []
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _normalize(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def matches(path: str, pattern: str) -> bool:
    return _compile(_normalize(pattern)).match(_normalize(path)) is not None


def match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(matches(path, p) for p in patterns)
