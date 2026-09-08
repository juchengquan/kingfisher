"""The mutation report's own guard: what it refuses to call a mutation."""

from __future__ import annotations

import re

from tests.unit import mutation_report


def _offered(tmp_path, source: str) -> list[str]:
    """The lines `candidates` would mutate in one file, as text."""
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    lines = source.splitlines()
    spans = mutation_report._prose(path)
    offered = []
    for number, line in enumerate(lines, 1):
        for pattern, _ in mutation_report.RULES:
            hit = re.search(pattern, line)
            if hit and not any(a <= hit.start() < b for a, b in spans.get(number, ())):
                offered.append(line.strip())
                break
    return offered


def test_an_f_string_is_prose_like_any_other_string(tmp_path):
    """Since 3.12 an f-string is not a `STRING` token, so a filter naming only that
    skips plain strings and lets every f-string through.

    It shipped that way. Nearly every message in this repository is an f-string,
    so the fix covered the smaller half and the report went on editing wording
    and calling the result a gap in the suite.
    """
    source = 'def f(where):\n    return f"{where}: must be a list or tuple of tools"\n'

    assert _offered(tmp_path, source) == []


def test_a_plain_string_is_prose_too(tmp_path):
    """The half that did work, so a change cannot fix one by losing the other."""
    source = 'def f():\n    return "must be a list or tuple of tools"\n'

    assert _offered(tmp_path, source) == []


def test_code_beside_prose_is_still_offered(tmp_path):
    """The rule has to keep biting: a filter that skips everything reports no
    survivors and looks like a suite that catches everything.
    """
    source = 'def f(a, b):\n    if a is not None or b is None:\n        return "or else"\n'

    assert _offered(tmp_path, source) == ["if a is not None or b is None:"]


def test_an_operator_inside_an_f_strings_expression_is_code(tmp_path):
    """The half of an f-string that is not prose. `{a or b}` is evaluated, so
    changing it changes what the message says about the run rather than how it
    reads.
    """
    source = 'def f(a, b):\n    return f"{a or b} went wrong"\n'

    assert _offered(tmp_path, source) == ['return f"{a or b} went wrong"']
