"""Find rules written more than once. Run it; it asserts nothing.

    uv run python tests/audit_duplication.py

Not a test, deliberately. It catches the case tests are blind to -- a copy that
has *not* drifted yet, and so behaves identically everywhere it is exercised.
`subagent_skills` carried one for months, equal to `narrowed` on every input;
no behaviour test could have failed on it. As an assertion this would also fire
on any legitimately similar pair and get muted, which is worse than absent.

Two passes:

  structural   statement sequences with identifiers normalised to the order
               they appear, so a copy that renamed its variables still matches
  literal      strings spelled in more than one module, which is how a route
               or a filename comes to have two definitions

**What it catches, and what it does not.** It found `_narrow_tools`, a whole
function duplicating `capabilities._narrow` with the arguments swapped, and
reintroducing that today still lights it up. It did *not* find the copy at the
end of `subagent_skills`, and could not have: `narrowed` binds `allowed =
set(by)` and tests `name in allowed`, where the copy tested `name in activated`
directly. Equal on every input, different tree.

So this catches a rule *transcribed*, not a rule *rewritten*. The second kind
was found by reading a docstring that claimed two things were the same rule and
then running both over the same inputs to check -- which is a different
technique, and one no static pass will do for you. When a docstring here says
some rule is "the same as" another, that is the thing to go and verify.

Windows rather than whole functions because a rule transcribed into the middle
of a longer function is the case a function-level hash cannot see, and the more
likely one.

The literal pass is advisory. Most of its hits are dict keys, and the ones that
do name something shared were checked by hand: renaming either spelling turns
the suite red, so behaviour already binds them.
"""

from __future__ import annotations

import ast
import collections
import hashlib
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "kingfisher"

#: Short enough that a rule transcribed as a couple of lines is still visible.
MIN_STATEMENTS = 2

#: And it has to *decide* something. Length does not separate signal from noise
#: at this size -- measured on the pass's own output, a two-statement narrowing
#: dumps to 351 characters while a pair of `x: list[str] = []` declarations
#: dumps to 236, so a length floor either loses findings or keeps idioms.
#:
#: What separates them is whether the window computes: a rule compares, filters
#: or combines, while an idiom declares or guards. Every false positive this
#: pass produced had none of these; the narrowing had three.
COMPUTES = (ast.comprehension, ast.Compare, ast.BoolOp, ast.BinOp, ast.IfExp)
MIN_COMPUTES = 2


class Normalise(ast.NodeTransformer):
    """Rename every identifier to the order it was first seen."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def _slot(self, name: str) -> str:
        return self.seen.setdefault(name, f"v{len(self.seen)}")

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.copy_location(ast.Name(id=self._slot(node.id), ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        return ast.copy_location(ast.arg(arg=self._slot(node.arg), annotation=None), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        return ast.copy_location(
            ast.Attribute(value=node.value, attr=self._slot(node.attr), ctx=node.ctx), node
        )


def _statements(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """The body, minus the docstring."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _fingerprint(window: list[ast.stmt]) -> str | None:
    module = ast.Module(body=[ast.fix_missing_locations(s) for s in window], type_ignores=[])
    try:
        reparsed = ast.parse(ast.unparse(module))
    except SyntaxError:  # pragma: no cover -- a fragment that will not stand alone
        return None
    if sum(isinstance(n, COMPUTES) for n in ast.walk(reparsed)) < MIN_COMPUTES:
        return None
    dump = ast.dump(Normalise().visit(reparsed), annotate_fields=False)
    return hashlib.sha1(dump.encode()).hexdigest()[:12]  # noqa: S324 -- not a credential


def _windows(path: pathlib.Path) -> list[tuple[str, str, int, int]]:
    """Every fingerprintable run of statements in one file."""
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = _statements(node)
        for start in range(len(body)):
            for stop in range(start + MIN_STATEMENTS, len(body) + 1):
                key = _fingerprint(body[start:stop])
                if key:
                    found.append((key, node.name, body[start].lineno, stop - start))
    return found


def structural() -> int:
    groups: dict[str, list[tuple[str, str, int, int]]] = collections.defaultdict(list)
    for path in sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts):
        module = str(path.relative_to(SRC))
        for key, fn, line, size in _windows(path):
            groups[key].append((module, fn, line, size))

    reported = 0
    seen_pairs: set[tuple[str, ...]] = set()
    for members in groups.values():
        modules = {m[0] for m in members}
        if len(modules) < 2:
            continue
        # The longest window of a given pairing says the most; its shorter
        # sub-windows are the same finding reported again.
        pair = tuple(sorted(modules))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        reported += 1
        widest = max(m[3] for m in members)
        print(f"\n  {widest} statements, in {len(modules)} modules:")
        for module, fn, line, _ in sorted(members):
            print(f"    {module}:{line}  {fn}()")
    return reported


def literals() -> int:
    where: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if 2 < len(text) < 40 and "\n" not in text and " " not in text:
                    where[text].add(str(path.relative_to(SRC)))
    shared = {k: v for k, v in where.items() if len(v) > 1}
    for text, modules in sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  {text!r:<26} {', '.join(sorted(modules))}")
    return len(shared)


def main() -> int:
    print("=== rules written more than once ===")
    found = structural()
    if not found:
        print("  none across modules")

    print("\n=== strings spelled in more than one module (advisory) ===")
    count = literals()
    print(f"  -> {count} shared")

    print(f"\n{found} structural finding(s). Exits 0 either way -- see the docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
