"""`subagent` is the noun, as deepagents spells it, and the other word is only a verb.

Both words named one thing, about as often as each other, and readers asked whether
they differed. A rule that only found the old word would refuse the verb as well, and
the verb is the right word for what an agent does when it hands work on.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from tests.conftest import repository_root
from tests.unit.test_architecture import _prose_run_together

ROOT = repository_root()

#: Where kingfisher's own words are. Not `spikes/`: those are probes kept as a record of
#: what ran at the time, and rewriting one is editing that record.
AREAS = ("src", "tests", "docs", "assets", "evals")
SINGLE_FILES = (".env.example", "README.md", "CLAUDE.md")
SUFFIXES = {".py", ".md", ".yaml", ".yml", ".example"}

#: A determiner or an adjective just before the word makes it the noun. "that" and
#: "which" are left out on purpose: "an agent that delegates once" is the verb.
NOUN_IN_PROSE = re.compile(
    r"\b(?:a|an|the|each|every|its|their|this|these|those|one|no|any|another|whose|own|"
    r"compiled|portable|imported|nested|named|unnamed|builtin|activated|shipped|private|"
    r"other)\s+delegates?\b"
    r"|\bdelegate's\b|\bdelegates'(?!\w)",
    re.IGNORECASE,
)

#: In a name, the word followed by one of these is the verb: `_delegates_to`.
VERB_FOLLOWERS = {"to", "as"}


def _files() -> list[Path]:
    found = [ROOT / name for name in SINGLE_FILES if (ROOT / name).is_file()]
    for area in AREAS:
        found += [
            path
            for path in sorted((ROOT / area).rglob("*"))
            if path.is_file() and path.suffix in SUFFIXES and "__pycache__" not in path.parts
        ]
    return [path for path in found if path != Path(__file__)]


def _nouns_in_a_name(name: str) -> bool:
    """Whether a name, split at underscores and capitals, uses the word as the noun."""
    parts = [p.lower() for p in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)]
    return any(
        part in ("delegate", "delegates")
        and (i + 1 >= len(parts) or parts[i + 1] not in VERB_FOLLOWERS)
        for i, part in enumerate(parts)
    )


def test_the_scan_reaches_the_files_it_is_about():
    """An empty walk passes every rule below having checked nothing."""
    scanned = {path.suffix for path in _files()}

    assert {".py", ".md", ".yaml"} <= scanned


def test_no_name_calls_a_subagent_by_the_other_word():
    """A helper, class or test named for the noun keeps the second word alive for the
    next reader to copy -- `delegate(built, name)` sat beside `middleware_of` in
    `conftest`.
    """
    named = []
    for path in _files():
        if _nouns_in_a_name(path.stem):
            named.append(str(path.relative_to(ROOT)))
        if path.suffix != ".py":
            continue
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.NAME and _nouns_in_a_name(token.string):
                named.append(f"{path.relative_to(ROOT)}:{token.start[0]}: {token.string}")

    assert not named, f"named for the noun rather than `subagent`: {named}"


def _prose(path: Path) -> str:
    """A file's words, with a comment wrapped across lines joined back up."""
    return _prose_run_together(path) if path.suffix == ".py" else path.read_text(encoding="utf-8")


def test_no_prose_calls_a_subagent_by_the_other_word():
    """Docs, comments, messages and the shipped examples' prompts, which is where the
    word lived: most of its uses were prose rather than code.
    """
    said = [
        f"{path.relative_to(ROOT)}: {m.group(0)!r}"
        for path in _files()
        for m in NOUN_IN_PROSE.finditer(_prose(path))
    ]

    assert not said, f"the noun is `subagent`: {said}"


def test_the_prose_rule_reads_a_comment_that_wraps(tmp_path):
    """A `#` line ending on "the" and the next opening on the noun read as no noun at all.

    Both directions, as for the mode rule: the tree has nothing for it to find.
    """
    wrapped = tmp_path / "wrapped.py"
    wrapped.write_text("# The turn hands the work to the\n# delegate, which answers.\nX = 1\n")
    verb = tmp_path / "verb.py"
    verb.write_text("# An agent that may\n# delegate hands the work on.\nX = 1\n")

    assert NOUN_IN_PROSE.search(_prose(wrapped))
    assert not NOUN_IN_PROSE.search(_prose(verb)), "the verb is the right word"
