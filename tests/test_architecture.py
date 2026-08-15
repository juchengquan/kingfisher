"""The layer boundary, enforced rather than remembered.

`domain/` holds kingfisher's own vocabulary and must not know the harness
exists. `app/` orchestrates and must reach the harness only through `adapters/`.
`adapters/` is where foreign types belong — that is its entire job.

Checked by parsing imports rather than grepping, because the docstrings
legitimately discuss deepagents at length; it is the `import` that matters.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FOREIGN = ("langchain", "langgraph", "deepagents", "langchain_core", "langchain_anthropic")

SRC = Path(__file__).resolve().parent.parent / "src" / "kingfisher"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _is_foreign(module: str) -> bool:
    root = module.split(".", maxsplit=1)[0]
    return root in {f.split(".")[0] for f in FOREIGN}


def _modules_in(layer: str) -> list[Path]:
    return sorted(p for p in (SRC / layer).glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("path", _modules_in("domain"), ids=lambda p: p.name)
def test_domain_knows_nothing_of_the_harness(path):
    """If this fails, a foreign shape has entered kingfisher's vocabulary."""
    foreign = {m for m in _imported_modules(path) if _is_foreign(m)}
    assert not foreign, f"domain/{path.name} imports {sorted(foreign)}"


@pytest.mark.parametrize("path", _modules_in("app"), ids=lambda p: p.name)
def test_app_reaches_the_harness_only_through_adapters(path):
    """Orchestration speaks Request/RunEvent/RunResult, never AIMessage.

    run.py and runlog.py once each carried their own copy of LangChain's
    usage-metadata shape, kept in sync by nobody. This is the guard.
    """
    foreign = {m for m in _imported_modules(path) if _is_foreign(m)}
    assert not foreign, f"app/{path.name} imports {sorted(foreign)} — route it through adapters/"


def test_adapters_are_where_foreign_types_live():
    """Not a restriction — a check that the layer is actually doing its job.

    If no adapter imports anything foreign, the ACL has evaporated and the
    coupling has gone somewhere less visible.
    """
    imports = {m for path in _modules_in("adapters") for m in _imported_modules(path)}
    assert any(_is_foreign(m) for m in imports)


def test_domain_does_not_depend_on_the_layers_above_it():
    """Dependencies point inward: app -> adapters -> domain, never back."""
    for path in _modules_in("domain"):
        modules = _imported_modules(path)
        assert not any(m.startswith(("kingfisher.app", "kingfisher.adapters")) for m in modules), (
            f"domain/{path.name} depends on an outer layer"
        )


def test_adapters_do_not_reach_back_into_app():
    """The other half of that rule, which went unenforced for a while.

    `Config` lived in `app/` and all four adapters imported it, inverting the
    direction this module claims to hold. It lives in `domain/` now; this is
    what stops it drifting back. `app/config.py` reads `adapters.models` for
    the credential variable names, which is the legal direction.
    """
    for path in _modules_in("adapters"):
        modules = _imported_modules(path)
        assert not any(m.startswith("kingfisher.app") for m in modules), (
            f"adapters/{path.name} depends on app/ — move the shared shape into domain/"
        )
