"""The layer boundary, enforced rather than remembered.

`domain/` holds kingfisher's own vocabulary and must not know the harness
exists. `application/` orchestrates and must reach the harness only through
`infrastructure/`. `infrastructure/` is where foreign types belong — that is
its entire job.

Checked by parsing imports rather than grepping, because the docstrings
legitimately discuss deepagents at length; it is the `import` that matters.
"""

from __future__ import annotations

import ast
import sys
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


def _inside_domain(module: str) -> bool:
    return module == "kingfisher.domain" or module.startswith("kingfisher.domain.")


@pytest.mark.parametrize("path", _modules_in("domain"), ids=lambda p: p.name)
def test_domain_imports_only_the_standard_library_and_itself(path):
    """Deny by default, replacing three rules that were allowlists by omission.

    Each named something the domain must not import -- the harness, the layers
    above it, `Config` -- and passed for everything nobody had thought of.
    `yaml` was the standing example: a third-party parser sitting in
    `domain/frontmatter.py`, which no rule mentioned and so no rule caught.

    Turned around, there is nothing to keep up to date. A domain module may
    import the standard library and `kingfisher.domain`. Anything else is a
    dependency the vocabulary should not have, whatever it is called:

      * a foreign shape entering kingfisher's own types -- deepagents,
        langchain -- which is what the first of the three rules watched for
      * `kingfisher.application` or `kingfisher.infrastructure`, inverting the direction
        dependencies point
      * `kingfisher.config`, which holds base_url, api_key and timeout_s: a
        domain rule that needs a value takes the value, as `sweep(workspace,
        keep)` always did
      * a library -- the case the other three could not see
    """
    outside = {
        module
        for module in _imported_modules(path)
        if module.split(".")[0] not in sys.stdlib_module_names and not _inside_domain(module)
    }
    assert not outside, (
        f"domain/{path.name} imports {sorted(outside)} -- the domain takes the standard "
        "library and itself; have an adapter do that part and hand it the result"
    )


@pytest.mark.parametrize("path", _modules_in("application"), ids=lambda p: p.name)
def test_application_reaches_the_harness_only_through_infrastructure(path):
    """Orchestration speaks Request/RunEvent/RunResult, never AIMessage.

    run.py and runlog.py once each carried their own copy of LangChain's
    usage-metadata shape, kept in sync by nobody. This is the guard.
    """
    foreign = {m for m in _imported_modules(path) if _is_foreign(m)}
    assert not foreign, (
        f"application/{path.name} imports {sorted(foreign)} — route it through infrastructure/"
    )


def test_infrastructure_is_where_foreign_types_live():
    """Not a restriction — a check that the layer is actually doing its job.

    If no adapter imports anything foreign, the ACL has evaporated and the
    coupling has gone somewhere less visible.
    """
    imports = {m for path in _modules_in("infrastructure") for m in _imported_modules(path)}
    assert any(_is_foreign(m) for m in imports)


def test_infrastructure_does_not_reach_back_into_application():
    """The outward half of the rule, which went unenforced for a while.

    Dependencies point inward: application -> infrastructure -> domain, never
    back. The inward half is
    `test_domain_imports_only_the_standard_library_and_itself`.

    `Config` lived in the application layer and every adapter imported it,
    inverting the direction this module claims to hold. It sits at the package
    root now, belonging to no layer, and this is what stops it drifting back
    up. `application/config.py` reads `infrastructure.models` for the
    credential variable names, which is the legal direction.
    """
    for path in _modules_in("infrastructure"):
        modules = _imported_modules(path)
        assert not any(m.startswith("kingfisher.application") for m in modules), (
            f"infrastructure/{path.name} depends on application/ — "
            "move the shared shape into domain/"
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


def test_the_package_does_not_depend_on_the_eval_harness():
    """`evals/` is test material and lives outside `src/`, so it is not in the
    wheel. If the package imports it, an installed kingfisher breaks -- and the
    348-line fixture module has quietly moved back in.
    """
    for layer in ("domain", "infrastructure", "application"):
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


def test_infrastructure_is_the_layer_doing_the_touching():
    """The other half: if nothing in infrastructure/ touches the world either, the
    I/O did not move out, it moved somewhere less visible."""
    assert any(_world_contact(p) for p in _modules_in("infrastructure"))


def test_no_test_stubs_out_agent_construction():
    """The blind spot, closed and kept closed.

    Patching `create_deep_agent` with something that does not call through
    makes every assertion in that test blind to whatever deepagents validates
    while constructing. Three bugs reached a live run that way -- `/data`,
    `/skills` and `/memory` each needed a backend route before `permissions=`
    would be accepted, and no unit test could see it.

    `conftest.capture_build` records the arguments *and* lets the call happen,
    which costs about 30ms and removes the category. This stops a future test
    quietly reintroducing the stub.
    """
    here = Path(__file__).resolve()
    offenders = []
    for path in sorted(here.parent.glob("test_*.py")):
        if path == here:  # this module names the thing it forbids
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "setattr" in line and "create_deep_agent" in line:
                offenders.append(f"{path.name}:{number}")

    assert not offenders, (
        f"patch create_deep_agent directly at {offenders} — use conftest.capture_build, "
        "which records the call and still lets deepagents validate it"
    )


def test_only_one_module_decides_what_a_skill_is():
    """`--list` exists to tell a caller which names are valid, so it and
    `build_agent` must mean the same thing by "a skill". The driver carried a
    byte-identical copy of the lookup, so a change to the definition would have
    left `--list` advertising names the validator then rejected.

    `domain.skill` owns the filename and `infrastructure.skill_store` owns the
    listing. Asserting they *agree* with a caller is tautological once the
    caller imports them; what is worth asserting is that nothing else decides.
    """
    root = Path(__file__).resolve().parent.parent
    owners = {
        root / "src" / "kingfisher" / "domain" / "skill.py",
        root / "src" / "kingfisher" / "infrastructure" / "skill_store.py",
    }

    searched = [*(root / "src").rglob("*.py"), root / "main.py", *(root / "evals").glob("*.py")]
    offenders = [
        path.relative_to(root)
        for path in searched
        if path not in owners and "SKILL.md" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        f"{offenders} decide what a skill is; use domain.skill.FILENAME and "
        "skill_store.names so the inventory and the validator cannot disagree"
    )


def test_the_package_ships_its_presets():
    """`--seed-presets` has to work for an installed kingfisher.

    That means the definitions live *inside* the wheel rather than beside it in
    the repo: `packages = ["src/kingfisher"]`, so anything one level up is not
    shipped and a pip-installed kingfisher would have nothing to copy. Moving
    them back out would break seeding for every user who is not in a checkout,
    and nothing else would notice.
    """
    from kingfisher.infrastructure import presets

    assert (SRC / "presets" / "skills").is_dir()
    # And reachable the way an installed one reaches them, not by path.
    with presets.opened() as root:
        for kind in presets.KINDS:
            assert (root / kind).is_dir(), kind
