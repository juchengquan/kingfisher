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


def test_the_public_api_list_matches_the_lazy_export_table():
    """`__all__` is a literal so a linter can see it, and `_EXPORTS` drives the
    lazy loading. Nothing keeps them in step but this."""
    import kingfisher

    assert kingfisher.__all__ == sorted(kingfisher._EXPORTS)


def test_importing_kingfisher_does_not_pull_in_deepagents():
    """The point of the lazy re-exports: a consumer that only touches domain
    types should not pay a second for three provider SDKs."""
    import subprocess
    import sys

    probe = "import sys, kingfisher; print('deepagents' in sys.modules)"
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_domain_does_not_read_deployment_config():
    """`Config` holds base_url, api_key and timeout_s. It lived in `domain/`
    on import-direction grounds -- both outer layers could reach it without
    reaching each other -- but no domain rule ever read one.

    A domain rule that needs a value takes the value. `sweep(workspace, keep)`
    always did; this stops the record itself drifting back inward.
    """
    for path in _modules_in("domain"):
        modules = _imported_modules(path)
        assert "kingfisher.config" not in modules, (
            f"domain/{path.name} imports Config — pass it the values it needs instead"
        )


def test_the_package_does_not_depend_on_the_eval_harness():
    """`evals/` is test material and lives outside `src/`, so it is not in the
    wheel. If the package imports it, an installed kingfisher breaks -- and the
    348-line fixture module has quietly moved back in.
    """
    for layer in ("domain", "adapters", "app"):
        for path in _modules_in(layer):
            modules = _imported_modules(path)
            assert not any(m.split(".")[0] == "evals" for m in modules), (
                f"{layer}/{path.name} imports evals/ — the wheel does not ship it"
            )


#: Calls that reach outside the process. Not exhaustive as a security measure --
#: it is a design guard, and its job is to make the *easy* violation loud.
WORLD_CALLS = frozenset({
    "mkdir", "rmdir", "rmtree", "copytree", "copyfile", "copy", "move",
    "write_text", "write_bytes", "read_text", "read_bytes", "open",
    "unlink", "touch", "chmod", "rename", "replace",
    "iterdir", "glob", "rglob", "walk", "exists", "is_dir", "is_file",
    "stat", "resolve", "run", "check_output", "Popen",
})

WORLD_MODULES = frozenset({"subprocess", "shutil", "os", "tempfile", "io", "socket"})


def _world_contact(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [
                f"L{node.lineno} import {a.name}"
                for a in node.names
                if a.name.split(".")[0] in WORLD_MODULES
            ]
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in WORLD_MODULES
        ):
            found.append(f"L{node.lineno} from {node.module}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WORLD_CALLS
        ):
            found.append(f"L{node.lineno} .{node.func.attr}()")
    return found


@pytest.mark.parametrize("path", _modules_in("domain"), ids=lambda p: p.name)
def test_domain_touches_nothing_outside_the_process(path):
    """The boundary the older tests were mistaken for.

    They checked that `domain/` imported nothing from langchain or deepagents,
    and passed -- while `domain/workspace.py` shelled out to git, chmod'd files,
    created directories and rmtree'd them. 35 such calls across three modules.
    "No foreign imports" is not "no side effects", and only the second makes a
    domain layer worth having: a rule you can read, run and trust without a
    filesystem underneath it.

    Where a rule genuinely needs a primitive -- turn allocation is atomic
    because `mkdir` refuses a taken name -- it takes a port from
    `domain.ports`. Where it does not, it returns a decision and the caller
    acts: `retention.plan` names the sessions to drop and touches none of them.
    """
    contact = _world_contact(path)
    assert not contact, f"domain/{path.name} reaches the world: {contact}"


def test_the_adapters_are_the_ones_doing_the_touching():
    """The other half: if nothing in adapters/ touches the world either, the
    I/O did not move out, it moved somewhere less visible."""
    assert any(_world_contact(p) for p in _modules_in("adapters"))
