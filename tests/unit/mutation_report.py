"""Break one line of `src/` at a time and see whether the suite notices.

Run it; it asserts nothing. A survivor is a change nothing here would have
caught, which is either a gap worth a test or a mutation that changed no
behaviour -- and telling those apart is reading, not arithmetic.

    PYTHONPATH=. uv run python tests/unit/mutation_report.py --sample 40

Each mutant costs one run of the suite, so a corpus large enough to trust is
measured in hours. Six git worktrees running disjoint `--shard` slices is what
made 300 tractable; the arithmetic is the same either way.

**This edits files in place.** It puts each one back in a `finally`, but a
`kill -9` between the write and the restore leaves the tree mutated -- run it in
a worktree you can throw away, not the one you are working in.
"""

from __future__ import annotations

import argparse
import ast
import collections
import io
import pathlib
import random
import re
import subprocess
import sys
import tokenize

from tests.conftest import repository_root

REPO = repository_root()
SRC = REPO / "src" / "kingfisher"

#: Each rule is one edit a careless change could plausibly be. Deliberately not
#: exhaustive: an operator set is a claim about which mistakes are worth
#: catching, and a wider one costs a suite run per mutant it adds.
RULES = (
    (r"(?<![=!<>])== ", "!= "), (r"!= ", "== "),
    (r" and ", " or "), (r" or ", " and "),
    (r" < ", " >= "), (r" > ", " <= "),
    (r" <= ", " > "), (r" >= ", " < "),
    (r"\bTrue\b", "False"), (r"\bFalse\b", "True"),
    (r"\bnot ", ""),
)


#: The tokens holding prose rather than code.
#:
#: `FSTRING_MIDDLE` is the one that is easy to leave out and expensive to: since
#: 3.12 an f-string is never a `STRING` token, it is a start, its literal pieces
#: and an end -- so a filter naming only `STRING` skips plain strings and lets
#: every f-string through. This file's messages are nearly all f-strings, and so
#: are the ones it walks.
PROSE_TOKENS = (tokenize.STRING, tokenize.COMMENT, tokenize.FSTRING_MIDDLE)


def _prose(path: pathlib.Path) -> dict[int, list[tuple[int, int]]]:
    """Columns holding a string literal or a comment, per line.

    Mutating those edits an error message, not behaviour, and almost nothing
    asserts on the word `and` inside one. Skipped rather than reported: the pass
    that did not skip them called 19 of its 54 survivors a gap in the suite,
    when what it had found was that nobody tests prose.
    """
    spans: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    text = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type not in PROSE_TOKENS:
            continue
        (first, start), (last, end) = token.start, token.end
        for row in range(first, last + 1):
            spans[row].append((start if row == first else 0, end if row == last else sys.maxsize))
    return spans


def _owner(tree: ast.Module, line: int) -> str:
    """The innermost definition holding a line, which is what a report should name.

    Not the line number: `#411` took out blank lines across the tree and moved
    every anchor a previous run had written down.
    """
    holds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    inner = None
    for node in ast.walk(tree):
        if (
            isinstance(node, holds)
            and node.lineno <= line <= (node.end_lineno or node.lineno)
            and (inner is None or node.lineno > inner.lineno)
        ):
            inner = node
    return f"{inner.name}()" if inner else "<module>"


def candidates() -> list[tuple[pathlib.Path, int, str, str]]:
    """Every line one rule can edit, prose excluded."""
    found = []
    for path in sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts):
        lines = path.read_text(encoding="utf-8").splitlines()
        spans = _prose(path)
        for number, line in enumerate(lines, 1):
            if line.strip().startswith("for "):
                continue  # ` in ` and ` not ` inside a loop header are syntax
            for pattern, replacement in RULES:
                hit = re.search(pattern, line)
                if hit and not any(a <= hit.start() < b for a, b in spans.get(number, ())):
                    found.append((path, number, pattern, replacement))
                    break
    return found


def verdict(root: pathlib.Path, timeout: int) -> tuple[str, str]:
    """What the suite made of the tree as it currently stands.

    `-x` because a mutant that fails early should cost one test rather than a
    whole run -- and because a mutant can be lethal enough to hang: breaking
    `create_exclusive` so it never reports success spins the turn-id loop
    forever. Read as "no failures", a timeout is the exact opposite of one.
    """
    try:
        done = subprocess.run(
            ["uv", "run", "--no-sync", "pytest", "-q", "--tb=no", "-p", "no:randomly", "-x"],
            cwd=root, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return "hangs", f"no result in {timeout}s"
    tail = done.stdout.strip().splitlines()
    return ("killed" if done.returncode else "survived"), (tail[-1] if tail else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=20, help="how many mutants to run")
    parser.add_argument("--seed", type=int, default=0, help="which sample, reproducibly")
    parser.add_argument("--per-module", type=int, default=8, help="spread, rather than clustering")
    parser.add_argument("--timeout", type=int, default=300, help="a run this slow has hung")
    parser.add_argument("--shard", default="0/1", help="`i/n`, to split a corpus across worktrees")
    args = parser.parse_args()

    index, shards = (int(part) for part in args.shard.split("/"))
    pool = candidates()
    random.Random(args.seed).shuffle(pool)  # noqa: S311 -- picking a sample, not a key
    seen: collections.Counter[pathlib.Path] = collections.Counter()
    corpus = []
    for entry in pool:
        if seen[entry[0]] < args.per_module:
            corpus.append(entry)
            seen[entry[0]] += 1
        if len(corpus) == args.sample:
            break
    mine = [c for n, c in enumerate(corpus) if n % shards == index]
    print(f"{len(pool)} mutable lines; running {len(mine)} of {len(corpus)}")

    outcomes: collections.Counter[str] = collections.Counter()
    notable = []
    for number, (path, line, pattern, replacement) in enumerate(mine, 1):
        original = path.read_text(encoding="utf-8")
        rows = original.splitlines(keepends=True)
        rows[line - 1] = re.sub(pattern, replacement, rows[line - 1], count=1)
        mutant = "".join(rows)
        try:
            compile(mutant, str(path), "exec")
        except SyntaxError:
            continue
        path.write_text(mutant, encoding="utf-8")
        try:
            outcome, tail = verdict(REPO, args.timeout)
        finally:
            path.write_text(original, encoding="utf-8")
        outcomes[outcome] += 1
        where = f"{path.relative_to(SRC)} {_owner(ast.parse(original), line)}"
        if outcome != "killed":
            notable.append((outcome, where, rows[line - 1].strip(), tail))
        print(f"  [{number}/{len(mine)}] {outcome:<8} {where}")

    print(f"\n{dict(outcomes)}")
    if notable:
        print("\nnothing caught these:")
        for outcome, where, line_text, tail in notable:
            print(f"  {outcome:<8} {where}\n           {line_text[:96]}\n           {tail}")
    print("\nA survivor is a question, not a defect: read it and decide whether the")
    print("change it makes is one the code promises anybody.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
