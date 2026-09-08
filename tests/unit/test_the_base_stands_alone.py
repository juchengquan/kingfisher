"""`pip install kingfisher` has to work with no service anywhere."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import repository_root

SRC = repository_root() / "src" / "kingfisher"

#: Every module the rule below walks, collected once so the collection itself can
#: be checked.
#:
#: An empty collection does not fail. It used to be a parametrize, where pytest
#: *skips* an empty one and a skip in a wall of dots is indistinguishable from a
#: pass; walked in a loop it is quieter still, because the body runs zero times,
#: gathers no complaints and passes outright. That is not hypothetical: `SRC` was
#: counted as `parents[1]`, the tests moved one level down into `tests/unit/`, and
#: this file went green with nothing walked at all. The rule asserts it is
#: non-empty before it walks anything.
MODULES = sorted(SRC.rglob("*.py"))

#: What this distribution must not need. `kingfisher_service` is a separate
#: wheel; `fastapi` and `uvicorn` are its dependencies and no longer anything
#: this one declares.
ABSENT = ("kingfisher_service", "fastapi", "uvicorn", "starlette")

#: The one module allowed to name the service, and only to say it is missing.
#: `kingfisher serve` imports it inside a function behind `except ImportError`.
#: Listed by path rather than waved through by rule, so adding a second one is a
#: decision somebody makes here rather than a thing that quietly becomes true.
MAY_NAME_IT = {"presentation/cli/__main__.py"}


def _named_by(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.split(".")[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return {node.module.split(".")[0]}
    return set()


def _modules(path: Path) -> set[str]:
    """Everything this file imports, wherever it does it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {name for node in ast.walk(tree) for name in _named_by(node)}


def _at_module_scope(path: Path) -> set[str]:
    """Only what it imports as it loads."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {name for node in tree.body for name in _named_by(node)}


def _relative(path: Path) -> str:
    return str(path.relative_to(SRC))


def _needs_absent(path: Path, name: str) -> list[str]:
    """Every way this file requires something a base install does not have.

    One function rather than two assertions, so both halves are reachable from a test
    that can supply a violating file: the module-scope half has no case in `src/` by
    design, and a rule with no cases passes.
    """
    complaints = []
    named = _modules(path) & set(ABSENT)
    if name in MAY_NAME_IT:
        named -= {"kingfisher_service"}
    if named:
        complaints.append(
            f"{name} needs {sorted(named)}, which a base install does not have — "
            "the service ships as `kingfisher-service` and this distribution "
            "must not require it"
        )

    # The allowance does not extend to module scope, which is where it would
    # stop being an allowance. Found by mutation: `import kingfisher_service` at
    # the top of the one permitted file passed everything, while a base install
    # would fail before `kingfisher list` ran.
    if as_it_loads := _at_module_scope(path) & set(ABSENT):
        complaints.append(
            f"{name} imports {sorted(as_it_loads)} as it loads — even the module "
            "allowed to ask for the service must ask inside a function, behind "
            "`except ImportError`"
        )
    return complaints


def test_no_module_needs_the_service_or_its_dependencies():
    """Anywhere in the import graph, at module scope or inside a function."""
    assert MODULES, "no modules walked -- see the note on MODULES above"
    complaints = [
        complaint for path in MODULES for complaint in _needs_absent(path, _relative(path))
    ]

    assert not complaints, "\n".join(complaints)


def test_the_exception_is_real_rather_than_defensive():
    """`MAY_NAME_IT` should shrink to nothing if the code stops needing it."""
    still_needed = {
        name for name in MAY_NAME_IT if "kingfisher_service" in _modules(SRC / name)
    }

    assert still_needed == MAY_NAME_IT, (
        f"{sorted(MAY_NAME_IT - still_needed)} no longer names the service — "
        "take it out of MAY_NAME_IT"
    )


def test_every_public_name_resolves_without_the_service(monkeypatch):
    """Driven, not read."""
    import sys

    import kingfisher

    class Absent:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ABSENT:
                msg = f"No module named {name!r} (no service installed)"
                raise ImportError(msg)

    for name in list(sys.modules):
        if name.split(".")[0] in ABSENT:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Absent(), *sys.meta_path])

    # Before the loop, because a loop with no cases passes -- which is the scar
    # the test below this one exists to hold, and this had the same shape.
    # Demonstrated rather than argued: emptied and unguarded, this test passed.
    assert len(kingfisher._EXPORTS) > 10, "the export list stopped being the export list"

    for name in kingfisher._EXPORTS:
        getattr(kingfisher, name)


def test_the_module_scope_rule_can_actually_fire(tmp_path):
    """A rule with no cases passes, and this one has none by design.

    Nothing in `src/` imports the service as it loads, so the assertion above never
    runs against a real violation -- deleting it changes no result, which a mutation
    proved. `test_architecture` has the same scar from `_presentation_modules`
    collecting zero files and reporting success either way. So the detection is
    exercised directly, on a file written to fail.
    """
    top = tmp_path / "top.py"
    top.write_text("import kingfisher_service\n", encoding="utf-8")

    deferred = tmp_path / "deferred.py"
    deferred.write_text(
        "def serve():\n"
        "    try:\n"
        "        import kingfisher_service\n"
        "    except ImportError:\n"
        "        return 1\n"
        "    return kingfisher_service\n",
        encoding="utf-8",
    )

    # Named as the one file allowed to mention the service, so only the
    # module-scope half can be what complains.
    allowed = next(iter(MAY_NAME_IT))

    assert _needs_absent(top, allowed), "a top-level import must be caught"
    assert "as it loads" in _needs_absent(top, allowed)[0]
    assert not _needs_absent(deferred, allowed), (
        "an import inside a function, behind except ImportError, is the whole "
        "allowance and must pass"
    )
    assert _needs_absent(deferred, "somewhere/else.py"), (
        "and any other file naming it at all is still refused"
    )


def test_the_rule_above_walks_something():
    """`SRC` resolving somewhere with no modules, which the rule above cannot notice."""
    assert len(MODULES) > 20, (
        f"{len(MODULES)} modules found under {SRC} — the rule above walks this list "
        "in a loop, and an empty one gathers no complaints and passes"
    )
