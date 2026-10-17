"""The layer boundary, enforced rather than remembered."""

from __future__ import annotations

import ast
import inspect
import re
import sys
from functools import cache
from pathlib import Path

import pytest


def _repository_root(start: Path | None = None) -> Path:
    """The checkout this file is in, found rather than counted."""
    here = (start or Path(__file__)).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "kingfisher").is_dir():
            return candidate
    msg = (
        f"no repository root above {here}: expected a directory holding both "
        f"pyproject.toml and src/kingfisher. These rules read files by path and "
        f"would otherwise scan the wrong tree, or nothing, and report success."
    )
    raise AssertionError(msg)


#: The checkout, and the library inside it. Everything path-shaped here starts from one
#: of these two, so a move is one line rather than four -- which is how three of the
#: four came to disagree the last time this tree changed shape.
REPO = _repository_root()
SRC = REPO / "src" / "kingfisher"

#: The definition kinds' own directory. Named here rather than spelled at each of
#: the four rules below it, which is how three of the four path constants came to
#: disagree the last time this tree changed shape.
KINDS_DIR = SRC / "core" / "definitions"


def _package_of(path: Path) -> tuple[str, ...]:
    """The dotted package a module sits in, walked rather than counted."""
    parts: list[str] = []
    directory = path.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return tuple(reversed(parts))


def _imported_modules(path: Path) -> set[str]:
    """Every module this file imports, relative ones resolved to their real name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                if node.module:
                    modules.add(node.module)
                continue
            # `.` is this package, `..` its parent, and so on.
            base = package[: max(len(package) - (node.level - 1), 0)]
            if node.module:
                modules.add(".".join((*base, node.module)))
            else:
                # `from . import x` imports modules, so each name is one.
                modules.update(".".join((*base, alias.name)) for alias in node.names)
    return modules


def _imported_names(path: Path) -> dict[str, frozenset[str]]:
    """Every module this file imports, and the names it takes from each."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(path)
    taken: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                taken.setdefault(alias.name, set())
        elif isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if not node.level:
                if node.module:
                    taken.setdefault(node.module, set()).update(names)
                continue
            base = package[: max(len(package) - (node.level - 1), 0)]
            if node.module:
                taken.setdefault(".".join((*base, node.module)), set()).update(names)
            else:
                for alias in node.names:
                    taken.setdefault(".".join((*base, alias.name)), set())
    return {module: frozenset(names) for module, names in taken.items()}


def _every(collection: list[Path], what: str) -> list[Path]:
    """The files a rule is about, refused when there are none.

    A rule that walks a collection, gathers offenders and asserts the list is empty
    passes when the collection is empty too -- the loop simply never runs. That is
    the failure `test_no_rule_here_is_parametrized_over_nothing` exists for, and it
    watches five collections *by name*, so a rule walking a sixth was never covered
    by it. Asked here instead, by every rule at the point it walks anything, which is
    the one place the question cannot be forgotten.

    It matters more since these rules stopped being parametrised. One case per module
    at least made an empty collection visible as a test that vanished from the run;
    collapsed to a single case, the rule goes on reporting success over nothing.
    """
    assert collection, (
        f"{what} collected no files — the rule reading it walks nothing, gathers no "
        "offenders and passes, which is the shape of a rule that has stopped being "
        "about anything"
    )
    return collection


def _modules_in(layer: str, root: Path = SRC) -> list[Path]:
    """Every module in a layer, subpackages included.

    `root` is a parameter so the behaviour can be tested against a tree built for the
    purpose, rather than by waiting for a real subpackage to prove it.

    This was `glob`, one directory deep, and every rule here that walks a layer reads
    it: the first module to move into a subpackage would have dropped out of all of
    them at once, with all of them still passing.
    """
    return sorted(
        p
        for p in (root / layer).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _module_id(path: Path) -> str:
    """`core/agent/assembly.py`, not `assembly.py`."""
    if path.is_relative_to(SRC):
        return path.relative_to(SRC).as_posix()
    return path.relative_to(REPO).as_posix()


def test_a_layer_rule_reaches_into_a_subpackage(tmp_path):
    """The collection every rule below is built on, tested for the case it missed.

    Built here rather than asserted against `src/`, so it keeps testing the collection
    after the real tree changes shape. `import yaml` in a subpackage is the case: a
    third-party parser once sat in one while three rules looked straight past it.
    """
    layer = tmp_path / "domain"
    (layer / "inner").mkdir(parents=True)
    (layer / "__init__.py").touch()
    (layer / "inner" / "__init__.py").write_text("import yaml\n", encoding="utf-8")
    (layer / "inner" / "buried.py").write_text("import yaml\n", encoding="utf-8")
    (layer / "shallow.py").write_text("import json\n", encoding="utf-8")

    found = _modules_in("domain", root=tmp_path)

    assert [p.relative_to(layer).as_posix() for p in found] == [
        "__init__.py",
        "inner/__init__.py",
        "inner/buried.py",
        "shallow.py",
    ]
    assert "yaml" in _imported_modules(layer / "inner" / "buried.py")
    assert "yaml" in _imported_modules(layer / "inner" / "__init__.py")


def test_a_relative_import_is_read_as_the_module_it_reaches(tmp_path):
    """`from .."""
    package = tmp_path / "kingfisher"
    (package / "domain").mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "domain" / "__init__.py").touch()
    (package / "config.py").touch()
    module = package / "domain" / "subagent.py"
    module.write_text(
        "from . import fields\nfrom .. import config\nfrom .capabilities import ALL\n",
        encoding="utf-8",
    )

    found = _imported_modules(module)

    assert found == {
        "kingfisher.domain.fields",  # `.` is this package
        "kingfisher.config",  # `..` is its parent -- the one that was invisible
        "kingfisher.domain.capabilities",  # and a dotted form resolves too
    }


def test_a_module_is_identified_by_where_it_is_not_what_it_is_called():
    """Every failure message in this file is built from `_module_id`."""
    assert _module_id(SRC / "tools" / "spec.py") == "tools/spec.py"
    assert _module_id(SRC / "infrastructure" / "harness" / "tool.py") == (
        "infrastructure/harness/tool.py"
    )


#: Everything in this repository that may import kingfisher, not only the package
#: and its tests: a tree outside those two is the last place anyone thinks to look
#: after a move here.


#: The trees this repository keeps Python in. One list, because two rules read it and
#: a second copy is a second thing to keep in step. `assets` is apart from
#: them: it imports kingfisher only through the front door, as a deployment's own tool
#: file does, so the import rules must not walk it, while the shape rules below must --
#: it ships modules a deployment copies into its own workspace.
CODE_AREAS: tuple[str, ...] = ("src", "tests", "evals", "spikes")
ASSETS_AREA = "assets"


def _everything_that_imports_kingfisher() -> list[Path]:
    areas = CODE_AREAS
    found = [
        p
        for area in areas
        if (REPO / area).is_dir()
        for p in (REPO / area).rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    # No special case for the driver any more: it is `tests/integration/driver.py`
    # and arrives with `tests`. It needed one while it sat at the root, and the
    # rule this feeds exists *because* a stale import in a tree nobody walked went
    # unnoticed -- so an area dropping out here has form.
    return sorted(found)


def _every_module_here() -> list[Path]:
    """Every Python file in the repository, which is wider than the set above.

    `assets/` is the addition: it ships modules a deployment copies into its
    own workspace, and `ruff` reads it, so a mistake there reaches somebody. Named as
    an addition rather than by writing the area list again, so the two cannot drift.
    """
    found = [*_everything_that_imports_kingfisher()]
    assets = REPO / ASSETS_AREA
    if assets.is_dir():
        found += [p for p in assets.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(found)


def _bound_twice_at_module_level(tree: ast.Module) -> dict[str, list[int]]:
    """Names this module binds twice at its top level, and the lines.

    Top-level statements only, and the narrowness is the point: a name rebound inside
    `try`/`except ImportError`, inside an `if` on the platform, or by a `for` is being
    rebound on purpose, and this walk never enters those statements.

    Every way a module-level statement binds a name, which this read as `Assign` alone
    until 2026-10-14 -- one of seven. A duplicated `def` is the sharpest case the rule's
    own docstring describes, and was invisible; so was `AnnAssign`, which this
    repository has 79 of at the top level, one of them introduced beside the rule.
    """
    where: dict[str, list[int]] = {}

    def bind(name: str, line: int) -> None:
        where.setdefault(name, []).append(line)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bind(target.id, node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            # An annotated declaration with no value binds nothing at runtime, so it is
            # not a second definition of anything.
            if node.value is not None:
                bind(node.target.id, node.lineno)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bind(node.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bind(alias.asname or alias.name, node.lineno)
        elif isinstance(node, ast.Import):
            # Only an alias, because `import a.b` and `import a.c` both bind `a` -- to
            # the same module object, which is the ordinary way to reach two submodules
            # and not a shadowing at all. `http_fetch.py` does it with `urllib`, and
            # counting it was this rule's first false positive. A plain duplicate
            # `import x` is ruff's F811, not this.
            for alias in node.names:
                if alias.asname:
                    bind(alias.asname, node.lineno)
    return {name: lines for name, lines in where.items() if len(lines) > 1}


def test_every_module_on_disk_reaches_the_collection_the_rules_walk():
    """`_every` refuses a collection that is empty and cannot see one that is partial.

    Measured: dropping `src`, `tests`, `evals` and `spikes` from the walk left the
    files of `assets` behind, and the rule below passed over them -- with
    `_every` satisfied the whole time.

    Counted per area, not named per area, which is the second half of that lesson. The
    first version of this compared the *set of area names*: one file from each area
    satisfied it, so cutting the walk to five modules of several hundred passed here
    too, and what noticed were two unrelated rules. The count comes from a glob of its
    own rather than from the collector, so losing most of an area fails in the rule
    written for it.
    """
    want = {
        name: sorted(
            p for p in (REPO / name).rglob("*.py") if "__pycache__" not in p.parts
        )
        for name in (*CODE_AREAS, ASSETS_AREA)
        if (REPO / name).is_dir()
    }
    assert want, "no area named here is on disk, so this rule is about nothing"

    reached: dict[str, list[Path]] = {}
    for path in _every_module_here():
        reached.setdefault(path.relative_to(REPO).parts[0], []).append(path)

    missing = {
        name: sorted(set(files) - set(reached.get(name, ())))
        for name, files in want.items()
        if set(files) - set(reached.get(name, ()))
    }
    assert not missing, (
        "modules on disk that no rule here walks: "
        + "; ".join(
            f"{name} ({len(gone)} of {len(want[name])})" for name, gone in missing.items()
        )
    )
    assert set(reached) <= set(want), (
        f"{sorted(set(reached) - set(want))} reach a rule without being named above"
    )


def test_no_module_binds_one_name_twice_at_the_top_level():
    """The second definition wins in silence, and whatever read the first gets it.

    Found by writing one: a new `DELEGATED_TOOL` in `test_access_wiring.py` collided
    with a template of that name 276 lines further down, whose definition won, so the
    fixture wrote an unformatted template out as a tool module. That one failed loudly,
    because a template does not parse as Python. The case this rule is for is the one
    where both values are valid -- then nothing says which won, and a rule asserting
    against the wrong fixture passes.
    """
    twice = [
        f"{path.relative_to(REPO).as_posix()}: {name} at {lines}"
        for path in _every(_every_module_here(), "the repository's modules")
        for name, lines in sorted(
            _bound_twice_at_module_level(ast.parse(path.read_text(encoding="utf-8"))).items()
        )
    ]

    assert not twice, (
        f"{twice} bind one name twice at the top level -- the second wins and nothing "
        "says so. Rename one, or move the rebinding inside the branch that means it"
    )


#: Every way a module-level statement binds a name, each written twice, with the line
#: numbers the rule should report. One entry per shape, because the rule's own fixture
#: held plain assignment alone -- so a predicate blind to the other seven passed both
#: directions of the control below, and a duplicated `def` silenced a deny-list guard
#: one file over with nothing red.
COLLIDES = {
    "assign": ("A = 1\nA = 2\n", {"A": [1, 2]}),
    "annotated": ("A: int = 1\nA: int = 2\n", {"A": [1, 2]}),
    "assign then annotated": ("A = 1\nA: int = 2\n", {"A": [1, 2]}),
    "def": ("def f():\n    pass\n\n\ndef f():\n    pass\n", {"f": [1, 5]}),
    "async def": (
        "async def f():\n    pass\n\n\nasync def f():\n    pass\n", {"f": [1, 5]}
    ),
    "class": ("class C:\n    pass\n\n\nclass C:\n    pass\n", {"C": [1, 5]}),
    "import alias": ("import os as x\nimport sys as x\n", {"x": [1, 2]}),
    "from-import": ("from os import path\nfrom sys import path\n", {"path": [1, 2]}),
}

#: And every way one is rebound on purpose, which this must leave alone. A bare
#: annotation is here because it binds nothing at runtime: two of them are two
#: declarations of one name, not two definitions.
MEANT_IT = {
    "fallback import": (
        "try:\n    import fast as engine\nexcept ImportError:\n    import slow as engine\n"
    ),
    "platform branch": "if 1:\n    LIMIT = 1\nelse:\n    LIMIT = 2\n",
    "loop variable": "for x in (1, 2):\n    SEEN = x\n",
    "bare annotation": "A: int\nA: int\n",
    "submodules of one root": "import urllib.error\nimport urllib.request\n",
}


@pytest.mark.parametrize("shape", sorted(COLLIDES), ids=sorted(COLLIDES))
def test_the_shadowing_rule_sees_every_way_a_name_is_bound_twice(shape):
    """One case per shape. The rule above runs over a tree with no offenders, so a
    predicate answering "no" to everything passes it for ever -- and answering "no" to
    all but one shape is what it did.
    """
    source, expected = COLLIDES[shape]

    assert _bound_twice_at_module_level(ast.parse(source)) == expected


@pytest.mark.parametrize("shape", sorted(MEANT_IT), ids=sorted(MEANT_IT))
def test_the_shadowing_rule_leaves_a_deliberate_rebinding_alone(shape):
    """The other direction: a predicate answering "yes" to everything would have to be
    carved out until it covered nothing. `submodules of one root` is here because
    counting it was this rule's first false positive, on a shipped tool.
    """
    assert _bound_twice_at_module_level(ast.parse(MEANT_IT[shape])) == {}


def _decorated_twice(tree: ast.Module) -> dict[str, list[str]]:
    """Definitions wearing one decorator more than once, and which one.

    Every definition, not only the top level: what this catches is an insertion landing
    between a decorator and the `def` under it, and a method is as reachable by that as
    a function. Compared by source text, so `parametrize` twice with different tables
    -- which is how a test takes the product of two -- is not a repeat.
    """
    worn: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        spelled = [ast.unparse(one) for one in node.decorator_list]
        again = sorted({one for one in spelled if spelled.count(one) > 1})
        if again:
            worn[node.name] = again
    return worn


def test_no_definition_wears_one_decorator_twice():
    """A decorator stack split by an insertion: the definition going in keeps a copy of
    the marker above it, and the one below is left bare.

    Found by doing it. `test_the_shell_cannot_lift_the_bits_on_the_callers_inputs` went
    in between `@macos` and the `def` under it, so it wore `@macos` twice and
    `test_the_shell_cannot_change_the_keys_file` wore none. Both still run on macOS
    either way, so the suite was green here and the bare one failed on Linux, where it
    drove a mechanism that is not there.
    """
    twice = [
        f"{path.relative_to(REPO).as_posix()}: {name} wears {again}"
        for path in _every(_every_module_here(), "the repository's modules")
        for name, again in sorted(
            _decorated_twice(ast.parse(path.read_text(encoding="utf-8"))).items()
        )
    ]

    assert not twice, (
        f"{twice} -- a stack that reads this way was split by an insertion, so look at "
        "the definition under it for the marker it should have kept"
    )


#: A split stack, and the two shapes that repeat a decorator for a reason: a product of
#: two tables, and one marker on a class and again on a method it holds.
STACKS = {
    "split": ("@macos\n@macos\ndef f():\n    pass\n", {"f": ["macos"]}),
    "a product of two tables": ("@p('a', A)\n@p('b', B)\ndef f(a, b):\n    pass\n", {}),
    "a class and its method": (
        "@macos\nclass C:\n    @macos\n    def f(self):\n        pass\n",
        {},
    ),
}


@pytest.mark.parametrize("shape", sorted(STACKS), ids=sorted(STACKS))
def test_the_decorator_rule_reads_a_stack_the_way_python_applies_it(shape):
    """Both directions in one table. The rule above runs over a tree with no offenders,
    so a predicate answering "no" to everything passes it for ever; and one answering
    "yes" to everything would refuse the two stacks below, which are how a decorator is
    repeated on purpose.
    """
    source, expected = STACKS[shape]

    assert _decorated_twice(ast.parse(source)) == expected


def _documents() -> list[Path]:
    """The prose that is only prose, and names modules for a living."""
    found = [*(REPO / "docs").rglob("*.md")] if (REPO / "docs").is_dir() else []
    found += [REPO / name for name in ("README.md", "CLAUDE.md")]
    return sorted(p for p in found if p.is_file())


def _prose_bearing_files() -> list[Path]:
    """Everything the prose rule reads, named once so a companion can check it."""
    return [*_everything_that_imports_kingfisher(), *_documents()]


def _names_a_real_module(module: str) -> bool:
    """Resolved on disk rather than imported."""
    base = SRC.parent.joinpath(*module.split("."))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def test_every_kingfisher_import_in_this_repository_names_a_module_that_exists():
    """The rule that was missing when `infrastructure/harness/` landed."""
    dangling = sorted({
        f"{path.relative_to(REPO).as_posix()} -> {module}"
        for path in _everything_that_imports_kingfisher()
        for module in _imported_modules(path)
        if module.split(".")[0] == "kingfisher" and not _names_a_real_module(module)
    })
    assert not dangling, (
        f"{dangling} import kingfisher modules that do not exist — something moved "
        "and left these behind"
    )


def test_the_dangling_import_rule_can_tell_a_gone_module_from_a_real_one():
    """Everything in the repository resolves, so the rule above passes whether it
    discriminates or answers `True`.
    """
    assert _names_a_real_module("kingfisher")
    assert _names_a_real_module("kingfisher.core.definitions.tools.spec")
    assert _names_a_real_module("kingfisher.core.session")
    assert _names_a_real_module("kingfisher.core.agent.assembly")

    assert not _names_a_real_module("kingfisher.infrastructure.agent")
    assert not _names_a_real_module("kingfisher.server")
    assert not _names_a_real_module("kingfisher.server.asgi")


def test_the_trees_outside_the_package_are_in_scope():
    """`assets/` is where a move once broke, in a tree outside the package, and the
    rule is worth nothing if it stops looking outside `src/` and `tests/`.
    """
    scanned = {p.relative_to(REPO).parts[0] for p in _everything_that_imports_kingfisher()}
    assert {"evals", "spikes"} <= scanned, (
        f"the dangling-import rule reads only {sorted(scanned)} — a move in src/ "
        "would break evals/ or spikes/ with nothing red"
    )


#: What a prose reference can be rooted at, and the only form of it that can be checked.
#: `models.yaml`, `run.py` and `uploads.provision` are shaped exactly like module paths;
#: `core.agent.backend` cannot be anything else. Unrooted, the pattern reads
#: every dotted phrase in the repository as a claim about a module and calls most of them
#: broken -- `importlib.resources` and every filename among them. Rooting it at a package
#: this tree actually has is what separates a claim from ordinary prose.
def _prose_roots(root: Path = SRC) -> tuple[str, ...]:
    """Every package and root module a prose reference may be rooted at.

    Derived rather than written down: this is a match pattern rather than a lookup, so
    it has no fallback to fail closed onto the way `THIRD_PARTY` does, where `""` makes
    the table total.

    Every package at any depth, not only the ones directly under `root`. A name that
    stops being a top-level directory drops out of the pattern, and the references to
    it stop reading as module paths at all -- so the rule goes quiet rather than red,
    which is the one failure it cannot report about itself. Narrowed back to `iterdir`,
    `test_a_nested_package_is_a_prose_root` is what goes red.
    """
    return tuple(sorted(
        {d.name for d in root.rglob("*") if d.is_dir() and (d / "__init__.py").is_file()}
        | {f.stem for f in root.glob("*.py") if f.stem != "__init__"}
    ))


PROSE_ROOTS = _prose_roots()
PROSE_REF = re.compile(
    r"`((?:kingfisher\.)?(?:" + "|".join(PROSE_ROOTS) + r")(?:\.[a-z_]+)+)`"
)

#: Tails that make a reference a filename rather than a module path. Not needed
#: while the roots were four layers, because there is no `domain.py`; needed the
#: moment `config`, `tools`, `skills` and `subagents` joined them, since each is
#: also a real file and `config.py` parses as a module and a segment. Thirteen
#: references in this repository are that shape, and every one of them is prose
#: about a file doing its job.
NOT_A_MODULE = frozenset({
    "py", "yaml", "yml", "md", "json", "toml", "txt", "example", "cfg", "ini",
})

#: Prose that names a module *because* it is gone, excused per file. Deny by
#: default like the tables above, and keyed by file rather than by name for a
#: reason the first draft found: `infrastructure.agent` appears twice in this
#: repository, once in the docstring explaining which move renamed it and once
#: in `domain/subagent.py` as a live pointer at where a spec is translated. One
#: is the rule doing its job and the other is the defect it exists to catch, and
#: a table keyed by name alone would have to excuse both.
PROSE_GONE: dict[str, frozenset[str]] = {}


def _module_file(name: str) -> Path | None:
    """The file a dotted name refers to, or `None`."""
    base = SRC.joinpath(*name.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    return base / "__init__.py" if (base / "__init__.py").exists() else None


def _defined_names(path: Path) -> set[str]:
    """What a module defines or imports, a class's own members included."""
    def declared(body: list[ast.stmt]) -> set[str]:
        found: set[str] = set()
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name)
            elif isinstance(node, ast.Assign):
                found.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                found.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                found.update(a.asname or a.name.split(".")[0] for a in node.names)
        return found

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = declared(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names |= declared(node.body)
    return names


def _prose_unresolved(text: str, excused: frozenset[str] = frozenset()) -> list[str]:
    """References in some text that name neither a module nor something in one."""
    unresolved = []
    for ref in PROSE_REF.findall(text):
        bare = ref.removeprefix("kingfisher.")
        if bare.rsplit(".", 1)[-1] in NOT_A_MODULE:
            continue  # `config.py` is a file, not `config` and a segment
        if bare in excused or _module_file(bare):
            continue
        parent, _, last = bare.rpartition(".")
        target = _module_file(parent)
        if target is None or last not in _defined_names(target):
            unresolved.append(bare)
    return unresolved


def test_prose_naming_a_module_names_one_that_exists():
    """A comment naming a module makes a claim, and a move falsifies it in silence."""
    stale = []
    for path in _prose_bearing_files():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        excused = PROSE_GONE.get(rel, frozenset())
        stale += [f"{rel} -> {ref}" for ref in _prose_unresolved(text, excused)]

    assert not stale, (
        f"{stale} name kingfisher modules that do not exist — something moved "
        "and the comment about it did not"
    )


#: A docstring's last line, when it has promised something that is not there.
#:
#: These are the endings a sentence cannot stop on: a dash or a colon introducing
#: a list, a comma or a conjunction mid-clause. Trimming a docstring drops the
#: block underneath and leaves the introduction behind, which reads as complete
#: prose and is not -- `assembly.py` promised "the last of three parts --" and named
#: none of them, and the scratch preparer promised "two problems" and listed
#: neither. That second one is named without backticks on purpose: it went with
#: the shared scratch directory, and the rule below would now call it unresolved.
UNFINISHED = ("--", ":", ",", " and", " or", " the", " is", " are")

#: Prose citing a position in a file rather than something in it. `formats.md`
#: was cited by line twice, both correct when written and both wrong the first
#: time that page was edited -- a claim nothing checks, in a file the rule above
#: would have covered had it named a module instead.
PROSE_LINE_REF = re.compile(r"\bat line \d+|\bline \d+ of\b", re.IGNORECASE)


def _docstrings(path: Path) -> list[tuple[str, str]]:
    """Every docstring in a module, with the name it belongs to."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover -- the suite would not import either
        return []
    holds = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return [
        (getattr(node, "name", "<module>"), doc)
        for node in ast.walk(tree)
        if isinstance(node, holds) and (doc := ast.get_docstring(node, clean=False))
    ]


def test_no_docstring_stops_mid_sentence():
    """A docstring that introduces a list nobody wrote is a wrong map, not a typo.

    Cheap where the module rule was expensive: whether a sentence ends is syntax,
    where whether a module exists needed tense and intent. Measured against the
    tree before the prose was cut -- 2,705 docstrings, no false positives -- and
    it catches all three the cutting produced.
    """
    unfinished = []
    for path in _everything_that_imports_kingfisher():
        for name, doc in _docstrings(path):
            if inspect.cleandoc(doc).rstrip().endswith(UNFINISHED):
                unfinished.append(f"{_module_id(path)}::{name}")

    assert not unfinished, (
        f"{unfinished} end on a dash, a colon or a conjunction — each promises "
        "something underneath it that is not there, which reads as finished prose"
    )


def test_no_prose_cites_a_line_number():
    """A line number is a claim about a file that editing the file falsifies.

    The neighbouring rule checks that prose naming a module names a real one, and
    cannot see this: a position is not a name. Both citations this found were
    right when written and wrong two commits later, with nothing going red.
    """
    cited = []
    for path in _prose_bearing_files():
        text = path.read_text(encoding="utf-8")
        cited += [
            f"{path.relative_to(REPO).as_posix()} -> {hit}"
            for hit in PROSE_LINE_REF.findall(text)
        ]

    assert not cited, (
        f"{cited} cite a line number — name the thing instead, so a reader greps "
        "for it rather than trusting an address the next edit moves"
    )


#: Prose counting its neighbours instead of naming them -- "the four above", "the
#: two below". A position and a count, so editing either end falsifies it in
#: silence, and no rule here can see it: every check in this file resolves
#: *names*, which is exactly what this shape declines to write.
#:
#: A count that says what it counts is left alone. "the two refusals above" and
#: "the two tests above" name a kind a reader can find, where a bare number names
#: a place that moves. So is a singular -- "the one above" is adjacency rather
#: than a count, and there is no number in it to drift.
#:
#: This file is skipped, structurally: the examples the companion asserts on are
#: the shape being refused, so the rule matches its own source.
PROSE_BARE_COUNT = re.compile(
    r"\b(?:the|these|those)\s+"
    r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\s+"
    r"(?:above|below)\b",
    re.IGNORECASE,
)


def test_no_prose_counts_its_neighbours():
    """A bare count of what is nearby is a claim nothing here can check.

    Eleven were written and one had already broken: `config.py` said the
    definition roots were `None` "for a different reason than the two above",
    and the two were `state_root` and `scratch_root`, which left `Config` in a
    later change. Naming them instead is what puts the claim in reach of
    `test_prose_names_class_attributes_that_exist` and the module rule, neither of which
    can see a number.
    """
    counting = []
    here = Path(__file__).resolve()
    for path in _prose_bearing_files():
        if path.resolve() == here:
            continue
        text = _prose_run_together(path) if path.suffix == ".py" else path.read_text(
            encoding="utf-8"
        )
        counting += [
            f"{path.relative_to(REPO).as_posix()} -> {hit!r}"
            for hit in PROSE_BARE_COUNT.findall(text)
        ]

    assert not counting, (
        f"{counting} count what is above or below instead of naming it — write the "
        "names, so that a rule can tell when one of them goes"
    )


def test_the_bare_count_rule_leaves_a_count_that_says_what_it_counts(tmp_path):
    """The rule is about the missing noun, not about numbers in prose."""
    assert PROSE_BARE_COUNT.search("Not beside the four above because they are not")
    assert PROSE_BARE_COUNT.search("Narrowed, unlike the two below, because")
    assert PROSE_BARE_COUNT.search("the paragraph naming the eleven above went")

    for kept in (
        "The case the two refusals above do not cover",
        "without it the two tests above would pass",
        "the two halves of `tool_name` above are the two shapes",
    ):
        assert not PROSE_BARE_COUNT.search(kept), kept

    # And it reads prose that was wrapped, which is how the `config.py` one hid:
    # "the two" ended a line and "above" opened the next.
    wrapped = tmp_path / "wrapped.py"
    wrapped.write_text(
        '"""A file."""\n\n#: Derived from the workspace, for a different reason than the two\n'
        "#: above: these hold content a person authored.\nX = 1\n",
        encoding="utf-8",
    )
    assert PROSE_BARE_COUNT.search(_prose_run_together(wrapped))


#: Prose sending a reader to the top of the file for the reason it is about. The
#: guard and the thing it guards end up apart, so a cut to either end leaves a
#: pointer at nothing and nothing goes red. All four in the library named a
#: paragraph that had gone -- why `builtin_tools` carries no audience, why `tools`
#: has no layer, why the service's error list is a copy.
DEFERS_TO_MODULE_DOCSTRING = re.compile(r"\bthe module docstring\b", re.IGNORECASE)

#: Areas this rule reads that the collection every other prose rule uses does not.
#: The shipped assets are the files most likely to defer -- each has a module
#: docstring worth reading, so pointing at it feels free -- and four did.
#:
#: There is no exemption table, and the first draft's was worse than none: it
#: excused `assets/` on the argument that an example is read top to
#: bottom, and mutating that entry proved it dead, because the walk had never
#: reached the directory it named. An exemption nothing exercises is a comment
#: asserting something untrue.
#:
#: This file is skipped, structurally rather than by choice: the pattern above
#: *is* the phrase it searches for, so the rule matches its own source however
#: the prose around it is worded. That hole is real and small, and is the one
#: `PROSE_GONE` already accepts for naming a gone module on purpose.
DEFERRAL_WALKS = ("assets",)


def _prose_run_together(path: Path) -> str:
    """A file's text with its comment markers and line breaks taken out.

    The wrapping is the point. `spec.py` ended one `#:` line on "see the module"
    and opened the next with "docstring for why", which no line-based search sees
    -- and re-wrapping is exactly what a pass over the prose does.
    """
    text = path.read_text(encoding="utf-8")
    return " ".join(re.sub(r"^\s*#:?", " ", text, flags=re.MULTILINE).split())


def _deferral_walked() -> list[Path]:
    """Everything this rule reads, named once so the companion can check it."""
    return [
        *_everything_that_imports_kingfisher(),
        *sorted(
            path
            for area in DEFERRAL_WALKS
            for path in (REPO / area).rglob("*.py")
            if "__pycache__" not in path.parts
        ),
    ]


def test_no_prose_defers_to_the_module_docstring():
    """A pointer at the top of the file outlives the paragraph it points at.

    Four were found at once, and one had been dangling long before the prose was
    cut: `spec.py` sent a reader up for "why the seam into a turn was left
    unbuilt", which no version of that docstring ever mentioned. Neither kind can
    fail, because a reference resolves only in a reader's head.
    """
    deferring = []
    here = Path(__file__).resolve()
    for path in _deferral_walked():
        if path.resolve() == here:
            continue
        if DEFERS_TO_MODULE_DOCSTRING.search(_prose_run_together(path)):
            deferring.append(path.relative_to(REPO).as_posix())

    assert not deferring, (
        f"{deferring} send a reader to the module docstring for a reason — write "
        "it where the mistake would be made instead, so that cutting either end "
        "cannot leave the other pointing at nothing"
    )


def test_the_deferral_rule_reads_across_a_wrapped_line(tmp_path):
    """Every one it caught was wrapped, so a line-based search would have missed them."""
    wrapped = tmp_path / "wrapped.py"
    wrapped.write_text(
        '"""A file."""\n\n#: Carried and never read -- see the module\n'
        "#: docstring for why the seam was left unbuilt.\nX = 1\n",
        encoding="utf-8",
    )
    assert DEFERS_TO_MODULE_DOCSTRING.search(_prose_run_together(wrapped))

    plain = tmp_path / "plain.py"
    plain.write_text('"""A file."""\n\n#: Carried and never read.\nX = 1\n', encoding="utf-8")
    assert not DEFERS_TO_MODULE_DOCSTRING.search(_prose_run_together(plain))

    # A pointer at a class or a function is left alone: it sits beside its target
    # and moves when the target moves, which is the pair this rule is not about.
    beside = tmp_path / "beside.py"
    beside.write_text('"""A file."""\n\n#: See the class docstring.\nX = 1\n', encoding="utf-8")
    assert not DEFERS_TO_MODULE_DOCSTRING.search(_prose_run_together(beside))

    # And the walk reaches what it says it does. The first draft named an area it
    # excused without ever having read it, which mutating the table is what found
    # -- so the emptiness check is here too, because an empty table would leave
    # the loop below vacuous rather than satisfied.
    walked = {path.relative_to(REPO).as_posix() for path in _deferral_walked()}
    assert len(walked) > 100
    assert DEFERRAL_WALKS
    for area in DEFERRAL_WALKS:
        assert any(name.startswith(f"{area}/") for name in walked), f"{area} is not walked"


def test_the_driver_is_not_collected():
    """The one module here that spends money must never be run by `pytest`."""
    live = sorted(
        path.name
        for path in (REPO / "tests" / "integration").rglob("*.py")
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    )

    assert not live, (
        f"{live} under tests/integration/ will be collected by a bare `pytest` — "
        "everything on this shelf reaches a real model and spends real money"
    )


def test_the_two_shelves_hold_what_they_say():
    """`tests/` itself holds the shared fixtures and nothing else."""
    loose = sorted(p.name for p in (REPO / "tests").glob("*.py") if p.name != "conftest.py")

    assert not loose, (
        f"{loose} sit between the two shelves — a test belongs under unit/ if it "
        "is offline and fast, or integration/ if it costs money to run"
    )
    assert (REPO / "tests" / "unit").is_dir()
    assert (REPO / "tests" / "integration").is_dir()


def test_the_prose_rule_can_tell_a_gone_module_from_a_real_one():
    """Every reference in the tree resolves once the thirteen are fixed, so the rule
    above passes whether it discriminates or answers nothing at all.
    """
    assert _prose_unresolved("`core.agent.backend`") == []
    assert _prose_unresolved(
        "`core.definitions.skills.spec.FILENAME` and `core.inventory`"
    ) == []
    assert _prose_unresolved("`plugins.sessions.shell.shell_env`") == []

    # Built rather than written, so this file is not itself a reference the rule refuses.
    misplaced = "core.agent.backend.shell_env"
    assert _prose_unresolved(f"`{misplaced}`") == [misplaced]
    # A package and a name it does not hold. This is the one a parent-only check
    # let through, and six of the thirteen were this shape.
    holds_nothing = "core.backend"
    assert _prose_unresolved(f"`{holds_nothing}`") == [holds_nothing]
    # Excused, but only where the table says so.
    gone = "core.agent.gone"
    assert _prose_unresolved(f"`{gone}`") == [gone]
    assert _prose_unresolved(f"`{gone}`", frozenset({gone})) == []

    # Not rooted at a layer, so not this rule's business: these are the shapes
    # that make the unrestricted version unusable.
    assert _prose_unresolved("`models.yaml`, `run.py`, `importlib.resources`") == []


def test_the_prose_roots_are_read_off_the_tree_and_not_written_down(tmp_path):
    """The list was corrected once; this is what stops it needing correcting again."""
    (tmp_path / "newkind").mkdir()
    (tmp_path / "newkind" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "templates").mkdir()  # shipped data, no `__init__.py`
    (tmp_path / "templates" / "models.yaml.example").write_text("", encoding="utf-8")
    (tmp_path / "loose.py").write_text("", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")

    assert _prose_roots(tmp_path) == ("loose", "newkind"), (
        "a package is found by its __init__.py and a root module by its suffix"
    )

    # And the real tree: every package that exists is one prose may name.
    packages = {
        d.name for d in SRC.iterdir() if d.is_dir() and (d / "__init__.py").is_file()
    }
    assert packages <= set(PROSE_ROOTS)
    assert "templates" not in PROSE_ROOTS, "shipped data is not a package"


def test_a_nested_package_is_a_prose_root(tmp_path):
    """A package below the top level is still a name a reference may be rooted at.

    Read one directory deep, the rule stops *matching* references to a package that
    moved down a level rather than failing them -- so the move buries every comment
    naming the old path and nothing goes red, which is the one failure this rule
    cannot report about itself. `session`, `sandbox` and `access` are nested
    packages here.
    """
    (tmp_path / "layer").mkdir()
    (tmp_path / "layer" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "layer" / "buried").mkdir()
    (tmp_path / "layer" / "buried" / "__init__.py").write_text("", encoding="utf-8")
    # No `__init__.py`, so shipped data at any depth is still not a root.
    (tmp_path / "layer" / "templates").mkdir()

    assert _prose_roots(tmp_path) == ("buried", "layer")

    # And against the real tree, which is what the rule actually runs on. Backticks
    # are added here rather than written, so the rule scanning this file does not
    # read its own negatives as claims about the tree.
    for missing in ("session.nowhere", "access.nowhere", "sandbox.nowhere"):
        assert _prose_unresolved(f"`{missing}`") == [missing], (
            f"{missing} is rooted at a package that is not at the top level, and a "
            "reference the pattern never matches is one the rule cannot fail"
        )

    # Rooted at the top, a real module still resolves: widening the roots must not
    # turn the full path into a second way to be wrong.
    assert _prose_unresolved("`core.agent.assembly`") == []


def test_the_prose_rule_reaches_the_packages_that_are_not_layers():
    """`tools`, `skills` and `subagents` left the layers and left this rule's sight.

    The positive cases were already here and one was vacuous: a name under
    `core.definitions.skills` was asserted to resolve while `skills` was not a root, so the
    pattern never matched it and the assertion held for the wrong reason.
    """
    for missing in ("kinds.nowhere", "core.nowhere"):
        assert _prose_unresolved(f"`{missing}`") == [missing], (
            f"{missing} is not a module and the rule has to say so"
        )

    # And the live ones still resolve, now that they are actually being read.
    assert _prose_unresolved("`core.definitions.skills.spec.FILENAME`") == []
    assert _prose_unresolved(
        "`core.definitions.tools.spec`, `core.definitions.subagents.rules`, `core.config`"
    ) == []


def test_a_filename_is_not_read_as_a_module_and_a_segment():
    """`tools.py` parses as the package `tools` plus a segment called `py`."""
    assert _prose_unresolved("`config.py` and `tools.py`") == []
    assert _prose_unresolved("`skills.py`, `subagents.py`, `config.yaml`") == []
    # The tail is what decides it, not the root: a real module under one of them
    # still has to name something real. Built rather than written, because this
    # file is one the rule reads and a literal would be a reference it refuses.
    #
    # This said `config.models` until class members started counting, at which
    # point `Config.models` made it resolve and the rule was right about a test
    # that had gone quietly wrong.
    absent = "core.config.no_such_field"
    assert _prose_unresolved(f"`{absent}`") == [absent]


def test_a_name_a_class_holds_is_a_name_the_module_defines():
    """`core.definitions.skills.registry.misfiled` is a documented field, and was read as stale."""
    assert _prose_unresolved("`core.definitions.skills.registry.misfiled`") == []
    assert _prose_unresolved("`core.config.models`") == []

    # And the module's own top level still counts, which is most of the traffic.
    assert _prose_unresolved("`core.definitions.skills.spec.FILENAME`") == []


def test_the_documents_are_read_by_the_prose_rule():
    """`decisions.md` is where this repository says where things are."""
    scanned = {p.relative_to(REPO).as_posix() for p in _prose_bearing_files()}

    assert "docs/decisions.md" in scanned, "the file the rule most needs to read"
    assert "CLAUDE.md" in scanned
    # Asserted through what the rule reads rather than through `_documents()`,
    # so dropping the documents from the scan fails here instead of passing.
    assert "src/kingfisher/core/service.py" in scanned, (
        "the Python half has to survive the documents being added to it"
    )


#: The kinds whose formats `core.definitions.documents` is written for, which its own docstring
#: names. Here rather than parsed out of the prose: a rule that reads the sentence it
#: guards passes whatever the sentence says.
SHARE_A_DOCUMENT_READER = frozenset({"agents", "subagents"})


def test_the_shared_document_reader_is_read_by_the_kinds_it_names():
    """`core.definitions.documents` says which formats it serves, and the sentence went stale.

    It read "all three kinds" while a skill's frontmatter came through it, and went on
    reading three for four commits after the reader that did that went with the upload
    path it served -- with the paragraph below naming two, contradicting a sentence one
    line up in the same docstring, and nothing red.

    Both directions, because the stale half is the one nobody removes: a kind that
    stops reading this leaves a sentence too generous, and a kind that starts leaves
    one too mean.
    """
    def reads_it(module: Path) -> bool:
        # Both spellings: `import kingfisher.core.definitions.documents` names the module, and
        # `from kingfisher.core.definitions import documents` -- which is what both callers
        # write -- names the package and takes the module as a name from it.
        return "kingfisher.core.definitions.documents" in _imported_modules(module) or (
            "documents" in _imported_names(module).get("kingfisher.core.definitions", frozenset())
        )

    kinds = _every(
        [one for one in sorted(KINDS_DIR.iterdir()) if (one / "__init__.py").is_file()],
        "kind packages",
    )
    reading_it = {
        one.name
        for one in kinds
        if any(reads_it(m) for m in _modules_in(f"core/definitions/{one.name}"))
    }

    assert reading_it == set(SHARE_A_DOCUMENT_READER), (
        f"{sorted(reading_it)} read kingfisher.core.definitions.documents, and its docstring "
        f"names {sorted(SHARE_A_DOCUMENT_READER)} -- update whichever is behind"
    )


def test_no_rule_here_is_parametrized_over_nothing():
    """A directory that stops existing takes its rule down with it, silently.

    Found by mutation while renaming `server/` to `presentation/`: pointing the
    collector at the old name left one rule covering fifteen modules one moment and zero
    the next, with a green run either way.

    `_every` is what each rule asks now, and this is the half that survives a rule
    forgetting to ask: it names the collections rather than the rules, so dropping the
    call still fails here. It only reaches a collection somebody wrote down.
    """
    collections = {
        "the flat core": _flat_core(),
        "the package": _package_modules(),
        "consumers": _consumer_modules(),
    }
    empty = sorted(name for name, found in collections.items() if not found)
    assert not empty, (
        f"{empty} collected no modules — a renamed or moved directory has taken "
        "its rules with it, and every one of them is still reporting success"
    )


def test_compiled_files_are_not_mistaken_for_modules(tmp_path):
    """`rglob` descends into `__pycache__`, which `glob` never reached."""
    layer = tmp_path / "domain"
    (layer / "__pycache__").mkdir(parents=True)
    (layer / "__pycache__" / "stale.py").write_text("import yaml\n", encoding="utf-8")
    (layer / "real.py").touch()

    assert [p.name for p in _modules_in("domain", root=tmp_path)] == ["real.py"]


#: Which third-party packages each area may import. Deny by default: a package
#: named nowhere below fails wherever it appears, so the table is what has to be
#: edited to take on a dependency, and editing it is where someone asks whether
#: the dependency belongs there.
#:
#: Measured, not declared -- every entry is a package some module imports today.
THIRD_PARTY: dict[str, frozenset[str]] = {
    # The agent runtime, which building one request's graph speaks to throughout.
    # `core/session`, `core/definitions/skills` and
    # `core/definitions/middlewares` reach it too, and their entries below say so --
    # an upgrade is still a list of files rather than a search, and the list spans
    # more than one directory.
    "core/agent": frozenset({
        "deepagents",
        "langchain",
        "langchain_core",
        "langchain_quickjs",
        "langgraph",
    }),
    # Running a turn and keeping its conversation: the stream the graph emits and the
    # checkpointer it was built with.
    "core/session": frozenset({"deepagents", "langchain_core", "langgraph"}),
    # Kingfisher's own sessions: a deepagents composite over a session's folder, and
    # the Linux shell fence, which is the OS in the most literal sense this table holds
    # -- Landlock, applied to a process before it execs. Optional and Linux-only, so
    # both imports of `sandlock` are inside functions behind an `ImportError` or a
    # platform check, and a macOS install never sees it.
    "plugins": frozenset({"deepagents", "sandlock"}),
    # Registering skills means handing them to the runtime that reads them: `registry`
    # asks deepagents which skills an agent will actually have. That cannot be done from
    # outside, and inverting it behind a port would put one implementation behind an
    # interface derived from it.
    "core/definitions/skills": frozenset({"deepagents"}),
    # Nothing, since the route to the runtime moved out: reading a tool roster off a
    # compiled langgraph object is `core.agent.kinds.tools` now.
    "core/definitions/tools": frozenset(),
    # Nothing, for the same move: turning a spec into the `SubAgent` deepagents
    # expects cannot be done without naming the type, and it is done in
    # `core.agent.kinds.subagents` now.
    "core/definitions/subagents": frozenset(),
    # Nothing, and this one never had anything: an agent's runtime half is
    # `core/agent/assembly.py`, which is not this package's.
    "core/definitions/agents": frozenset(),
    # The fifth, and back on the boundary: `core.definitions.middlewares.catalogue` refuses a class
    # that is not an `AgentMiddleware` as the directory is read rather than at the
    # first turn, which cannot be done without naming the type.
    "core/definitions/middlewares": frozenset({"langchain"}),
    # The folder itself, which holds no kind. `core.definitions.importing` loads a workspace's
    # own Python and takes nothing but the standard library; `core.definitions.documents` reads
    # the YAML the agent and subagent formats share, which takes the parser.
    #
    # The cost of that entry, stated rather than discovered: a sixth kind added
    # without one of its own falls through to here and inherits `yaml`, where it
    # used to inherit nothing. It cannot arrive quietly --
    # `test_kinds_holds_exactly_the_kinds` refuses a directory that is not in
    # `DEFINITION_KINDS` -- so the hole needs a deliberate edit before it opens.
    "core/definitions": frozenset({"yaml"}),
    # `policy` reads the access policy file, which is YAML.
    "core/access": frozenset({"yaml"}),
    # The command reads a `.env` in the directory it runs in; nothing else here does.
    "cli": frozenset({"dotenv"}),
    # The modules directly in `core/` reach the runtime through the packages beside
    # them. `model_catalogue` reads `models.yaml`, which takes the parser.
    "core": frozenset({"yaml"}),
    # The package root: `__init__.py`, the public names. Nothing.
    "": frozenset(),
}


def _package_modules() -> list[Path]:
    return sorted(
        p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts
    )


def _area_of(path: Path) -> str:
    """The longest area in `THIRD_PARTY` that contains this module."""
    parent = path.relative_to(SRC).parent.as_posix()
    parent = "" if parent == "." else parent
    candidates = [a for a in THIRD_PARTY if a in ("", parent) or parent.startswith(f"{a}/")]
    return max(candidates, key=len)


def _undeclared(used: set[str], area: str) -> set[str]:
    """The one place the table is consulted, so there is one place to get wrong."""
    return used - THIRD_PARTY[area]


def _unspent(area: str, imported: set[str]) -> frozenset[str]:
    """Grants the area never uses.

    The direction the table cannot fail in on its own: `_undeclared` fires on an import
    with no grant, and nothing at all fires on a grant with no import, so a permission
    outlives the code it was written for without anything going red.
    """
    return THIRD_PARTY[area] - imported


def test_an_area_is_refused_another_areas_dependencies():
    """The table has to partition, not merely enumerate."""
    assert _undeclared({"deepagents"}, "core") == {"deepagents"}
    assert _undeclared({"deepagents"}, "cli") == {"deepagents"}
    assert _undeclared({"fastapi"}, "core/agent") == {"fastapi"}
    assert _undeclared({"yaml"}, "core/agent") == {"yaml"}

    assert _undeclared({"deepagents", "langgraph"}, "core/agent") == set()
    assert _undeclared({"yaml"}, "core") == set()
    assert _undeclared({"fastapi"}, "cli") == {"fastapi"}
    assert _undeclared({"dotenv"}, "cli") == set()
    assert _undeclared({"dotenv"}, "core") == {"dotenv"}


def test_a_subpackage_is_judged_by_its_own_area():
    """`core/session/runtime.py` is judged as `core/session`, not the package root."""
    buried = SRC / "plugins" / "sessions" / "sandbox" / "fence.py"
    for path in (buried, SRC / "core" / "request.py", SRC / "__init__.py"):
        assert path.exists(), f"{path} does not exist, so the assertion below is about nothing"

    assert _area_of(SRC / "core" / "session" / "runtime.py") == "core/session"
    assert _area_of(SRC / "core" / "request.py") == "core"
    # A kind's module is its own area, which is what lets `core/definitions/skills/registry.py`
    # name the runtime without the rest of `core/definitions` inheriting the permission.
    assert _area_of(KINDS_DIR / "skills" / "registry.py") == "core/definitions/skills"
    # And the folder over them is an area of its own, so a kind that arrives
    # without an entry is judged by `kinds` -- which grants only `yaml` -- rather
    # than by the longest prefix happening to be the package root.
    assert _area_of(KINDS_DIR / "importing.py") == "core/definitions"
    assert _area_of(SRC / "__init__.py") == ""

    # A subpackage with no entry of its own is judged by its parent -- which would
    # stop being true the moment someone gave `sessions/` an entry.
    assert _area_of(buried) == "plugins"


def test_a_module_imports_only_what_its_area_may_depend_on():
    """One table, replacing two rules that were allowlists by omission.

    Both of the old rules had the same hole: `FOREIGN` named five packages where six of
    the agent runtime's are imported here, and `langchain_quickjs` was in neither list.

    The second of the two passed while *any one* file in `infrastructure/` imported from
    its tuple, so it had stopped being about the file it was written for.
    """
    stray = []
    for path in _every(_package_modules(), "the package"):
        area = _area_of(path)
        used = {
            m.split(".")[0]
            for m in _imported_modules(path)
            if m.split(".")[0] not in sys.stdlib_module_names
            and m.split(".")[0] != "kingfisher"
        }
        if undeclared := _undeclared(used, area):
            stray.append(
                f"{_module_id(path)} imports {sorted(undeclared)}; "
                f"{area or 'the package root'} may import "
                f"{sorted(THIRD_PARTY[area]) or 'nothing third-party'}"
            )

    assert not stray, (
        "\n".join(stray) + "\n— have an adapter in an area that may do that, and hand "
        "this one the result"
    )


def test_no_area_is_granted_a_dependency_it_does_not_import():
    """A grant outliving the import it was written for.

    `aiosqlite` is the worked case: the savers that imported it left in *Drop the sqlite
    savers, and the two dependencies that carried them*, the grant stayed, and
    `infrastructure/harness/` has been permitted a package this repository does not
    depend on and does not name anywhere in `src/`.
    """
    imported: dict[str, set[str]] = {area: set() for area in THIRD_PARTY}
    for path in _every(_package_modules(), "the package"):
        imported[_area_of(path)].update(m.split(".")[0] for m in _imported_modules(path))

    unspent = {
        area or "the package root": sorted(dead)
        for area in THIRD_PARTY
        if (dead := _unspent(area, imported[area]))
    }

    assert not unspent, (
        f"{unspent} — permitted and imported by nothing. A package named only in a "
        "string, the way `WIRE_FORMATS` names its chat classes, is not an import and is "
        "not what this table is about"
    )


def test_an_unspent_grant_is_what_that_rule_reads():
    """Driven rather than trusted.

    The rule above passes over any tree where the two sets happen to agree, so it says
    nothing about whether the predicate can tell them apart. Read against the table
    rather than a copy of it, for the reason the rule beside `_undeclared` is.
    """
    assert _unspent("plugins", {"deepagents", "sandlock", "json"}) == set()
    assert _unspent("plugins", {"deepagents"}) == {"sandlock"}
    assert _unspent("", set()) == set()
#: What may import from `plugins/`: the command, which may import anything. The plugins'
#: own address is inside `plugins/`, and the package root names none of them.
MAY_NAME_A_PLUGIN = ("cli",)


def _reaches_a_plugin(path: Path) -> list[str]:
    return sorted(m for m in _imported_modules(path) if m.startswith("kingfisher.plugins"))


def test_the_core_imports_nothing_from_plugins():
    """A default the core falls back to filed under `plugins/`, or an `isinstance` against
    `LocalSessions`, which would make kingfisher's own sessions impossible to ship apart
    -- the reason the five places that did it were cut before anything moved."""
    reaching = {
        _module_id(path): found
        for path in _every(sorted(SRC.rglob("*.py")), "the package")
        if not path.is_relative_to(SRC / "plugins")
        and path.relative_to(SRC).parts[0] not in MAY_NAME_A_PLUGIN
        and (found := _reaches_a_plugin(path))
    }

    assert not reaching, f"{reaching} -- the core names a plugin; ask it by an interface"


def test_the_plugin_rule_sees_an_import_of_one(tmp_path):
    """A rule that read nothing would pass over every import it is about."""
    written = tmp_path / "probe.py"
    written.write_text("from kingfisher.plugins.sessions.backend import LocalSessions\n")

    assert _reaches_a_plugin(written) == ["kingfisher.plugins.sessions.backend"]


def _other_plugins(path: Path, own: str) -> list[str]:
    """The plugins other than `own` that a module imports, the package's root included:
    the root is the public address, and it names every plugin."""
    return [
        module
        for module in _reaches_a_plugin(path)
        if (module.split(".")[2] if module.count(".") >= 2 else "") != own
    ]


def test_no_plugin_imports_another():
    """A plugin naming another can no longer be shipped or replaced without it -- the
    reason the core names none, one level down."""
    plugins = SRC / "plugins"
    crossing = {
        _module_id(path): found
        for path in _every(sorted(plugins.rglob("*.py")), "the plugins")
        if path != plugins / "__init__.py"
        and (found := _other_plugins(path, path.relative_to(plugins).parts[0].removesuffix(".py")))
    }

    assert not crossing, f"{crossing} -- a plugin imports the core and never another plugin"


def test_the_cross_plugin_rule_tells_its_own_from_another(tmp_path):
    """Both directions, so a predicate refusing everything, or nothing, fails here."""
    written = tmp_path / "probe.py"
    written.write_text(
        "from kingfisher.plugins.events import JsonlRunEvents\n"
        "from kingfisher.plugins.sessions.folders import LocalSessionDirs\n"
    )

    assert _other_plugins(written, "sessions") == ["kingfisher.plugins.events"]
    assert _other_plugins(written, "events") == ["kingfisher.plugins.sessions.folders"]
    written.write_text("from kingfisher.plugins import LocalSessions\n")
    assert _other_plugins(written, "sessions") == ["kingfisher.plugins"], (
        "the address is not a back door"
    )


# -- a plugin has of kingfisher what an outsider has --------------------------


#: Code held to what a plugin written elsewhere could import, and whether it is held as
#: written. The kit under `tests/checks/` is copied out of this repository, so its
#: imports leave with it and must name a door. Kingfisher's own plugins may not import
#: their door (`test_no_plugin_imports_another`), so each takes a name a door carries
#: from the module that door takes it from -- the object an outsider gets.
OUTSIDERS: dict[Path, bool] = {SRC / "plugins": False, REPO / "tests" / "checks": True}

#: What each file still takes that no door carries, as `module:name`. It only shrinks:
#: the change that stops an import removes its line, and the table goes once it is empty.
NOT_YET: dict[str, frozenset[str]] = {
    "src/kingfisher/plugins/sessions/backend.py": frozenset({
        "kingfisher.core.agent.backend:skill_mounts",
        "kingfisher.core.agent.host_paths:reject_host_path",
        "kingfisher.core.agent.prompting:kingfishers_shell",
        "kingfisher.core.definitions.catalogue:Definitions",
        "kingfisher.core.definitions.catalogue:refuse_unmountable",
        "kingfisher.core.layout:CLAIM",
        "kingfisher.core.session.model:session_dir",
        "kingfisher.core.session.model:still_held",
    }),
    "src/kingfisher/plugins/sessions/folders.py": frozenset({
        "kingfisher.core.layout:AGENTS_SCAFFOLD",
        "kingfisher.core.session.model:names_a_session",
    }),
    "src/kingfisher/plugins/sessions/sandbox/confinement.py": frozenset({
        "kingfisher.core.layout:SANDBOX_PROFILE",
    }),
    "src/kingfisher/plugins/sessions/sandbox/sandbox_exec.py": frozenset({
        "kingfisher.core.layout:SANDBOX_PROFILE",
    }),
    "src/kingfisher/plugins/sessions/shell.py": frozenset({
        "kingfisher.core.definitions.catalogue:Definitions",
        "kingfisher.core.definitions.skills.catalogue:LocalSkillRepository",
        "kingfisher.core.layout:SKILLS_VIEW",
    }),
    "tests/checks/backend.py": frozenset({
        "kingfisher.core.agent.usable_backend:executes",
        "kingfisher.core.agent.usable_backend:not_executing",
        "kingfisher.core.agent.usable_backend:uncovered",
    }),
    "tests/checks/sessions.py": frozenset({
        "kingfisher.core.agent.backend:skill_mounts",
        "kingfisher.core.agent.permitted_backend:host_path",
        "kingfisher.core.definitions.catalogue:Definitions",
        "kingfisher.core.definitions.skills:registry",
    }),
}


def _past_the_doors(path: Path, *, as_written: bool) -> frozenset[str]:
    """What this file takes of kingfisher that no door carries: `module:name`, or the
    module alone where the import names nothing."""
    doors = [_lazy_tables()[door] for door in PUBLIC]
    taken: set[str] = set()
    for module, names in _imported_names(path).items():
        if module.split(".", maxsplit=1)[0] != "kingfisher" or module in PUBLIC:
            continue
        if not as_written and module.startswith("kingfisher.plugins."):
            continue
        if not names:
            taken.add(module)
            continue
        taken |= {
            f"{module}:{name}"
            for name in names
            if as_written or not any(door.get(name) == module for door in doors)
        }
    return frozenset(taken)


def test_a_plugin_has_of_kingfisher_only_what_an_outsider_has():
    """A plugin of kingfisher's own taking a name no door carries is one an outside author
    cannot write -- `LocalSessions` took the folder names every deployment's sessions must
    honour that way -- and the kit doing it breaks the day it is copied out."""
    found = {
        path.relative_to(REPO).as_posix(): taken
        for root, as_written in OUTSIDERS.items()
        for path in _every(sorted(root.rglob("*.py")), root.relative_to(REPO).as_posix())
        if (taken := _past_the_doors(path, as_written=as_written))
    }

    new = {
        path: sorted(more)
        for path, taken in found.items()
        if (more := taken - NOT_YET.get(path, frozenset()))
    }
    assert not new, (
        f"{new} -- no door carries these: import a name a door carries, or make one public "
        "on purpose, with a witness"
    )
    stopped = {
        path: sorted(gone)
        for path, names in NOT_YET.items()
        if (gone := names - found.get(path, frozenset()))
    }
    assert not stopped, f"{stopped} are no longer taken -- remove them from NOT_YET"


def test_the_outsider_rule_tells_a_door_s_name_from_a_private_one(tmp_path):
    """Both ways, for both kinds of outsider, so a predicate passing everything or
    nothing fails here."""
    written = tmp_path / "probe.py"
    written.write_text(
        "import kingfisher.core.workspace\n"
        "from kingfisher import Config\n"
        "from kingfisher.core.interfaces import Sessions\n"
        "from kingfisher.core.layout import HARNESS\n"
        "from kingfisher.plugins import CommandRunner\n"
        "from kingfisher.plugins.sessions.folders import LocalSessionDirs\n"
    )

    assert _past_the_doors(written, as_written=False) == {
        "kingfisher.core.workspace",
        "kingfisher.core.layout:HARNESS",
    }
    assert _past_the_doors(written, as_written=True) == {
        "kingfisher.core.workspace",
        "kingfisher.core.layout:HARNESS",
        "kingfisher.core.interfaces:Sessions",
        "kingfisher.plugins.sessions.folders:LocalSessionDirs",
    }


def test_the_agent_package_is_the_one_speaking_to_the_harness():
    """The runtime's entry in `THIRD_PARTY` is about a package that still uses it."""
    runtime = THIRD_PARTY["core/agent"]
    imports = {m.split(".")[0] for p in _modules_in("core/agent") for m in _imported_modules(p)}
    assert imports & runtime, (
        "no module under core/agent/ imports the agent runtime — "
        "either the graph is built somewhere else, or the coupling did not move with it"
    )


#: Where kingfisher's own middleware lives, and the only place it may.
OWN_MIDDLEWARE = "kingfisher.core.agent.middlewares"


def _bases_of(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Each class a file defines, and its bases as `(module, name)`.

    A base the file imported is reported where it was imported from; one it did not
    import is reported as the file's own. `ast.walk` rather than the module body, so
    a class defined inside a function is a class here too.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(path)
    here = next(name for name, at in _module_paths().items() if at == path)
    names: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = package[: max(len(package) - (node.level - 1), 0)] if node.level else ()
            source = ".".join((*base, *([node.module] if node.module else [])))
            for alias in node.names:
                names[alias.asname or alias.name] = (source, alias.name)
                modules[alias.asname or alias.name] = f"{source}.{alias.name}"

    def resolved(base: ast.expr) -> tuple[str, str] | None:
        if isinstance(base, ast.Subscript):  # `Base[T]`
            base = base.value
        if isinstance(base, ast.Name):
            return names.get(base.id, (here, base.id))
        dotted: list[str] = []
        while isinstance(base, ast.Attribute):
            dotted.append(base.attr)
            base = base.value
        if not isinstance(base, ast.Name) or base.id not in modules:
            return None
        *inner, name = reversed(dotted)
        return ".".join((modules[base.id], *inner)), name

    return {
        node.name: [found for base in node.bases if (found := resolved(base)) is not None]
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _middleware_classes() -> set[tuple[str, str]]:
    """`(module, ClassName)` for every middleware the package defines.

    Read off the source and asked of the runtime only where the source runs out: a
    base in this tree is followed to its own bases, and a base from the agent runtime
    is imported and asked. Matching the name `AgentMiddleware` instead would miss the
    skills index, which subclasses deepagents' `SkillsMiddleware` and never names it.
    """
    import importlib

    from langchain.agents.middleware import AgentMiddleware

    runtime = THIRD_PARTY["core/agent"]
    bases = {
        (module, name): found
        for module, path in _module_paths().items()
        for name, found in _bases_of(path).items()
    }

    def foreign(module: str, name: str) -> bool:
        if module.partition(".")[0] not in runtime:
            return False
        try:
            candidate = getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            return False
        return isinstance(candidate, type) and issubclass(candidate, AgentMiddleware)

    found: set[tuple[str, str]] = set()
    while True:
        more = {
            klass
            for klass, its in bases.items()
            if klass not in found and any(base in found or foreign(*base) for base in its)
        }
        if not more:
            break
        found |= more
    return found


def test_kingfisher_keeps_its_own_middleware_in_one_package():
    """A middleware written beside the code that installs it, where nobody looking for
    "what does kingfisher wrap an agent in" would find it.

    Six were spread over three modules named for other things before this rule, and
    the package they moved to stays the whole answer only if the seventh cannot land
    anywhere else.
    """
    found = _middleware_classes()

    # The witness that the walk follows a base rather than matching a name: it is the
    # one class here that never says `AgentMiddleware`. Without it, a walk that found
    # nothing would pass the assertion below on an empty mapping.
    assert "NarrowedSkills" in {name for _, name in found}, f"the walk found only {found}"

    elsewhere = sorted(
        f"{module}.{name}" for module, name in found if not module.startswith(f"{OWN_MIDDLEWARE}.")
    )
    assert not elsewhere, (
        f"{elsewhere} are middleware defined outside {OWN_MIDDLEWARE} — move the class "
        "there and import it where it is installed; what it is built on can stay put"
    )


def test_the_public_api_list_matches_the_lazy_export_table():
    """`__all__` is a literal so a linter can see it, and `_EXPORTS` drives the lazy
    loading.
    """
    import importlib

    for package in sorted(LAZY_TABLES):
        module = importlib.import_module(package)
        assert sorted(module.__all__) == sorted(module._EXPORTS), package


#: Every package that re-exports through a `__getattr__` table, and the file
#: holding it. Both, because both have the same two halves that can disagree and
#: only one of them had ever been checked against anything.
LAZY_TABLES: dict[str, Path] = {
    "kingfisher": SRC / "__init__.py",
    "kingfisher.plugins": SRC / "plugins" / "__init__.py",
}


#: Who asked for each public name, one table per door. Keyed by `LAZY_TABLES`, which
#: is the list of doors, so opening a third without a table of its own fails below --
#: this rule went missing once already, swept out with the layer rules it was filed
#: beside, and a door nothing watches is how it would go missing again.
#:
#: `document` means a page writes the name out and a reader who copied it is owed it;
#: `embedder` means it is what a documented call takes or hands back; `interface` means
#: a plugin implements it, which `core/interfaces.py` is where it is written and the
#: rule below checks.
WITNESSES: dict[str, dict[str, str]] = {
    "kingfisher": {
        # The guides write these out, and a reader who copied one is owed it. Grep
        # `docs/guides/` for each rather than trusting a page named here -- which
        # page says what is the part that moves.
        "Kingfisher": "document",
        "config_from_env": "document",
        "Request": "document",
        "Capabilities": "document",
        # `ports.md` tells an adapter to raise this one, imported from `kingfisher`,
        # because a caller tells a bad session id from a broken store by the class.
        "UnsafeReferenceError": "document",
        # What those documented calls take and hand back -- `Kingfisher` takes a
        # `Config`, `run` returns a `RunResult`, `stream` yields `RunEvent`s. The
        # type of a documented call's answer cannot be private.
        "Config": "embedder",
        # `configuration.md` writes a `KINGFISHER_WIRE_FORMATS` out, and the factory it
        # shows returns a landing per format.
        "Landing": "document",
        "RunResult": "embedder",
        "RunEvent": "embedder",
        # What `Kingfisher.sessions()` and `session()` return.
        "Session": "embedder",
        # The four an approval gate needs. A turn that stops at one hands back
        # `PendingDecision`s on its result and cannot be continued without a `Resume`
        # carrying `Decision`s -- so a consumer that cannot name all three cannot answer
        # a gate at all, and one that cannot catch `DecisionError` cannot tell a bad
        # answer from a broken session.
        "PendingDecision": "embedder",
        # What `RunResult.ledger` holds, one per recorded result. A caller keeping or
        # showing those entries names the type to do it.
        "LedgerEntry": "embedder",
        "Resume": "embedder",
        "Decision": "embedder",
        "DecisionError": "embedder",
        # What they raise. A caller has to be able to catch, by name, what the
        # library raises at it without reaching past the door.
        "AccessError": "embedder",
        "CapabilityError": "embedder",
        "ConfigError": "embedder",
        "QuotaExceededError": "embedder",
        "SessionBusyError": "embedder",
        "SubagentError": "embedder",
        "UnknownSessionError": "embedder",
        # What `Kingfisher.artifact` raises for a name that is not an artifact, which a
        # caller fetching by a name it was handed tells apart from a session it cannot
        # reach.
        "ArtifactError": "embedder",
        # `README.md` opens on these four and the package docstring on `run`. A
        # reader who copied either is owed them.
        "definitions_source": "document",
        "ensure_definitions": "document",
        "paths_from_env": "document",
        "seed": "document",
        "run": "document",
        # `formats.md` writes them out: `source_ids=UNSCOPED`, and
        # `from kingfisher import Request, RunOn`. Grep for those rather than trusting a
        # line number here -- two were written down and both went stale the first time
        # that page was edited.
        "UNSCOPED": "document",
        "RunOn": "document",
        # `tools.md` writes `from kingfisher import ToolContext` for the annotation that
        # hands a tool the turn's backend. A tool file is in a deployment's workspace,
        # so nothing in this repository imports the name and nothing ever will.
        "ToolContext": "document",
        # `tools.md` writes `kingfisher.tool(...)` for a tool that says who may use it, in
        # a tool file of the deployment's own, as `ToolContext` above is.
        "tool": "document",
        # The type of `Kingfisher.run`'s `source_ids=`. `UNSCOPED` is one of its two
        # members and is documented; the type that admits it cannot be private.
        "Held": "embedder",
        # Seeding and the inventory, plus what each returns.
        # `test_the_whole_job_is_reachable_through_the_front_door` in
        # `test_inventory.py` is the written form of the claim in
        # `cli/__init__.py`, and names these five as the job it walks. Whether
        # `kinds_at` belongs in that job is an argument about the proof rather than
        # about this door.
        "Seeded": "embedder",
        "Inventory": "embedder",
        "inventory": "embedder",
        "kinds_at": "embedder",
        "WorkspacePaths": "embedder",
        # Where a deployment reads from. Made public on 2026-09-02 for a library
        # caller who "had no way to ask at all" -- see *Where a deployment reads
        # from* in `docs/decisions.md`. No such caller has appeared; the decision is
        # recent and deliberate enough to leave alone.
        "Origins": "embedder",
        "Origin": "embedder",
        # The streaming half of `run`, which is documented; the two are one decision.
        "stream": "embedder",
    },
    "kingfisher.plugins": {
        # The five a plugin implements, written in `core/interfaces.py` and checked
        # below to still be there. `ports.md` gives `CommandRunner`, `RunEvents` and
        # `Sessions` a section each, and `configuration.md`'s `KINGFISHER_SESSIONS` row
        # names the type a factory returns.
        "Sessions": "interface",
        "RunEvents": "interface",
        "CommandRunner": "interface",
        "Instructions": "interface",
        # The type a runner returns. Nothing *imports* it in a snippet -- a runner's
        # `run` returns one, so a deployment cannot write the interface without it.
        "CommandResult": "interface",
        # Kingfisher's own, which a plugin author builds on rather than implements.
        # `ports.md` shows subclassing the default, and `backend_at` for a backend over
        # a directory of its own; `formats.md` and `middleware.md` pass `LocalSessions`
        # in every snippet that builds a service.
        "LocalSessions": "document",
        "backend_at": "document",
        # `ports.md` writes `LocalSessions(shell=ShellSettings(...))` for a fence decided in
        # code rather than read from the environment, and `configuration.md` sends a
        # reader of the two shell settings there.
        "ShellSettings": "document",
        # `ports.md` sends a plugin to `cfg.layout` for the folders a session holds and
        # the routes it serves, and names the type and the layout a `Config` defaults to.
        "Layout": "document",
        "DEFAULT_LAYOUT": "document",
        # The shipped pair behind two of the interfaces above, public so a deployment
        # can wrap one rather than reimplement it. `upgrading.md` records the move:
        # they were "not public anywhere" before this door existed.
        "JsonlRunEvents": "document",
        "InstructionsFile": "document",
        # What `KINGFISHER_WIRE_FORMATS` returns, and the shipped two a factory adding a
        # third starts from. `configuration.md` writes the setting and the factory out,
        # importing `WireFormat` from this door.
        "WireFormat": "document",
        "WIRE_FORMATS": "document",
        # What `KINGFISHER_PLUGINS` returns, which `configuration.md` tells a deployment
        # to build in a module of its own.
        "Plugins": "document",
        # `ports.md` has a deployment whose shell is not kingfisher's say what it sees,
        # on its runner or on its own sandbox; nothing here has a shell of that kind.
        "ShellPrompt": "document",
        # `ports.md` tells a backend that refuses host paths to refuse them with this,
        # because it is the type `HostPathGuard` turns into a correction.
        "HostPathError": "document",
    },
}

#: Where a name witnessed as an interface has to be written, so that witness is a
#: claim about the tree rather than a label.
INTERFACES = SRC / "core" / "interfaces.py"


def test_every_public_name_has_a_witness():
    """A name nobody can name a caller for is a promise nobody asked for.

    Restored after being swept out with the layer rules, which it was filed beside and
    had nothing to do with: it keyed on bare public names and would have survived the
    move untouched. Nothing had been added to either door meanwhile -- the root shrank
    by the seven that moved here -- so what this catches is the next name, which is
    what it was for.
    """
    import importlib

    assert sorted(WITNESSES) == sorted(LAZY_TABLES), (
        "every door needs a witness table and every table a door: "
        f"{sorted(set(WITNESSES) ^ set(LAZY_TABLES))}"
    )

    for package, witnessed in sorted(WITNESSES.items()):
        table = importlib.import_module(package)._EXPORTS
        unwitnessed = sorted(set(table) - set(witnessed))
        stale = sorted(set(witnessed) - set(table))

        assert not unwitnessed, (
            f"{unwitnessed} are public on {package} and nothing says who asked. Name "
            "the caller: `document` if a page tells a reader to write it, `interface` "
            "if a plugin implements it, `embedder` with a reason if neither -- and if "
            "the honest answer is nobody, it does not belong on the door"
        )
        assert not stale, f"{stale} are witnessed on {package} and not exported any more"


def test_a_name_witnessed_as_an_interface_is_written_where_that_claims():
    """Otherwise `interface` is a label, and a label is what the witness table exists
    instead of. Driven against the module's own source, so a protocol that moves out of
    it fails here rather than keeping a witness that has stopped being true.
    """
    written = INTERFACES.read_text(encoding="utf-8")
    claimed = sorted(
        name
        for witnessed in WITNESSES.values()
        for name, who in witnessed.items()
        if who == "interface"
    )

    assert claimed, "no name claims to be an interface -- this rule is about nothing"
    # The control: the other witnesses are not held to this file, so a rule that
    # checked every name would pass for the wrong reason.
    assert any(
        who != "interface" for witnessed in WITNESSES.values() for who in witnessed.values()
    )

    # Anchored, and `\b` at the end: `class Instructions` is a prefix of
    # `class InstructionsMoved`, so a substring test passed a protocol renamed out from
    # under its witness -- measured, by mutating exactly that.
    missing = [
        name
        for name in claimed
        if not re.search(rf"^class {re.escape(name)}\b", written, re.MULTILINE)
    ]
    assert not missing, (
        f"{missing} are witnessed as interfaces and are not written in "
        f"{INTERFACES.name} -- move the witness or move the class back"
    )


def _stub_reexports(path: Path) -> tuple[dict[str, str], list[str]]:
    """The `if TYPE_CHECKING:` block: `{name: module}`, and what is not re-exported."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    bare: list[str] = []
    for node in ast.walk(tree):
        guard = node.test if isinstance(node, ast.If) else None
        named = getattr(guard, "id", None) or getattr(guard, "attr", None)
        if named != "TYPE_CHECKING":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                for alias in sub.names:
                    found[alias.name] = sub.module
                    if alias.asname != alias.name:
                        bare.append(alias.name)
    return found, bare


def test_the_stub_block_and_the_export_table_name_the_same_things():
    """The third table, and the one nothing had ever held to the other two.

    `__getattr__` returns `Any`, so a name with no stub still imports and still passes
    every test -- it is simply untyped, which is why this asks `ty` rather than the
    suite.
    """
    for package, path in sorted(LAZY_TABLES.items()):
        import importlib

        table = importlib.import_module(package)._EXPORTS
        stubs, bare = _stub_reexports(path)

        assert stubs, (
            f"no `if TYPE_CHECKING:` re-exports found in {path.name} -- this rule is "
            "about nothing, which is how the collector in this file has failed twice"
        )

        missing = sorted(set(table) - set(stubs))
        assert not missing, (
            f"{missing} are exported by {package} and have no stub, so a type checker "
            f"sees `Any` for them. Add `from <module> import X as X` to the "
            "`if TYPE_CHECKING:` block"
        )

        extra = sorted(set(stubs) - set(table))
        assert not extra, (
            f"{extra} have a stub in {package} and are not exported -- a type checker "
            "believes they are importable and `__getattr__` raises AttributeError"
        )

        disagree = sorted(n for n in table if table[n] != stubs[n])
        assert not disagree, (
            f"{package} and its stub block disagree about where {disagree} come from: "
            + ", ".join(f"{n}: {table[n]!r} against {stubs[n]!r}" for n in disagree)
        )

        assert not bare, (
            f"{sorted(bare)} are imported into {package}'s stub block without the "
            "`as X` alias, which under PEP 484 means they are not re-exported at all"
        )


def _dotted(node: ast.Import | ast.ImportFrom, package: tuple[str, ...]) -> list[str]:
    """What one import statement names, one dotted string per name it takes.

    `from x import y` is `x.y` rather than `x`, because whether that loads a module is
    the question every rule below it asks. Relative imports are resolved the way
    `_imported_modules` resolves them; there are none in `src/` today and a scanner that
    quietly returned nothing for the first one would be the wrong kind of ready.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not node.level:
        return [f"{node.module}.{alias.name}" for alias in node.names] if node.module else []
    base = package[: max(len(package) - (node.level - 1), 0)]
    stem = (*base, node.module) if node.module else base
    return [".".join((*stem, alias.name)) for alias in node.names]


def _on_type_checking(test: ast.expr) -> bool:
    return any(
        getattr(node, "id", None) == "TYPE_CHECKING"
        or getattr(node, "attr", None) == "TYPE_CHECKING"
        for node in ast.walk(test)
    )


@cache
def _imports_by_scope(path: Path) -> tuple[frozenset[str], tuple[tuple[str, int], ...]]:
    """What a module imports when it loads, and what it imports only when called.

    `if TYPE_CHECKING:` is neither. It does not run, so it puts nothing in `sys.modules`
    and makes no deferral below it pointless -- and only its `body` is guarded, since an
    `else` on that test is code that does run.

    Cached because the rules below ask for the same ninety files repeatedly and the
    answer is a parse: uncached, the SDK rule alone took five seconds. Safe only while
    nothing rewrites a path within one run -- a rule that builds a tree should build it
    under `tmp_path`, which is a fresh key every time.
    """
    package = _package_of(path)
    at_import: set[str] = set()
    deferred: list[tuple[str, int]] = []

    def visit(node: ast.AST, *, called: bool, typing: bool) -> None:
        if isinstance(node, ast.Import | ast.ImportFrom):
            if typing:
                return
            if called:
                deferred.extend((name, node.lineno) for name in _dotted(node, package))
            else:
                at_import.update(_dotted(node, package))
            return
        inside = called or isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        guard = isinstance(node, ast.If) and _on_type_checking(node.test)
        for field, value in ast.iter_fields(node):
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    visit(child, called=inside, typing=typing or (guard and field == "body"))

    visit(ast.parse(path.read_text(encoding="utf-8")), called=False, typing=False)
    return frozenset(at_import), tuple(deferred)


@cache
def _module_paths() -> dict[str, Path]:
    """`kingfisher.core.interfaces` -> the file, for every module in the package."""
    found: dict[str, Path] = {}
    for path in _every(_package_modules(), "the package"):
        parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = path
    return found


@cache
def _lazy_tables() -> dict[str, dict[str, str]]:
    """Each `__getattr__` package's table, read off the package rather than the file."""
    import importlib

    return {package: importlib.import_module(package)._EXPORTS for package in LAZY_TABLES}


def _loaded_by(dotted: str) -> str | None:
    """The module an import statement actually puts in `sys.modules`.

    `from kingfisher import Kingfisher` loads `core.service`, not `kingfisher`:
    the name resolves through `__getattr__`. That is the one place walking the prefix
    gives the wrong answer, and it is the answer the three `cli` modules
    turn on -- each reaches a dozen names through the front door and would otherwise
    look like it loads nothing but `kingfisher` itself.
    """
    package, _, name = dotted.rpartition(".")
    if name in _lazy_tables().get(package, {}):
        return _lazy_tables()[package][name]
    candidate = dotted
    while candidate and candidate not in _module_paths():
        candidate = candidate.rpartition(".")[0]
    return candidate or None


@cache
def _loads_directly(module: str) -> frozenset[str]:
    """The modules `import <module>` loads before running a line of its own body.

    Its own module-scope imports and the packages above it. One hop, so the closure
    below and the cycle rule further down read the same graph rather than two walks
    that can come to disagree.
    """
    parts = module.split(".")
    above = {".".join(parts[:index]) for index in range(1, len(parts))}
    at_import, _ = _imports_by_scope(_module_paths()[module])
    reached = {
        found
        for dotted in at_import
        if dotted.split(".")[0] == "kingfisher" and (found := _loaded_by(dotted))
    }
    return frozenset(name for name in above | reached if name in _module_paths())


def _package_graph() -> dict[str, frozenset[str]]:
    """The whole package as a graph, one entry per module."""
    return {module: _loads_directly(module) for module in _module_paths()}


@cache
def _module_closure(module: str) -> frozenset[str]:
    """Every module of this package that `import <module>` puts in `sys.modules`.

    A worklist rather than recursion, so a cycle is a set that stops growing rather than
    a `RecursionError` -- this has to keep answering while the tree is wrong, since what
    reads it is the rules that say the tree is wrong.
    """
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        if current in seen or current not in _module_paths():
            continue
        seen.add(current)
        pending.extend(_loads_directly(current))
    return frozenset(seen)


def test_no_deferred_import_defers_nothing():
    """A function-scope import of a module the file has already loaded.

    It costs a `noqa`, reads as a decision somebody weighed, and buys nothing at all.
    Four were like that. `activation` re-imported `model_for` forty lines below the
    module-scope import of the same name. `reporting` deferred `Definitions` and
    `Offering` in a file whose first imports are 3,166 modules, so both ran long after
    the bill they were avoiding had been paid.

    Kingfisher's own modules only, which is where the closure is exact. A deferral of
    deepagents is what the light and heavy export rules already watch, and none of those
    is dead.
    """
    pointless = []
    for module, path in sorted(_module_paths().items()):
        loaded = _module_closure(module)
        _, deferred = _imports_by_scope(path)
        for dotted, line in deferred:
            if dotted.split(".")[0] != "kingfisher":
                continue
            target = _loaded_by(dotted)
            if target in loaded:
                pointless.append(
                    f"{_module_id(path)}:{line} defers `{dotted}`, and importing "
                    f"{module} already loads {target}"
                )

    assert not pointless, (
        "\n".join(pointless) + "\n— the deferral saves nothing, so move the import to "
        "module scope and drop the `noqa`, or move whatever loads it early out of the "
        "way first"
    )


#: The two modules the closure is driven against below. One of each shape that can go
#: wrong: `listing` reaches its names through the lazy front door, which is where a
#: prefix walk under-reports, and `reporting` sits at the bottom of a graph three
#: thousand modules deep.
CLOSURE_WITNESSES = (
    "kingfisher.cli.listing",
    "kingfisher.core.reporting",
)


def test_the_closure_is_what_an_import_actually_loads():
    """Driven, because every rule that reads the closure is only as good as it.

    A closure that under-reports makes a dead deferral look live: the rule above goes
    quiet rather than red, which is the one failure it cannot report about itself. It
    did under-report, until `_loaded_by` learned to follow the lazy tables. Read against
    a real interpreter and the real tree rather than a graph built for the purpose,
    since a graph built for the purpose agrees with the code that built it.
    """
    import json
    import subprocess

    # Through `_module_paths` rather than as bare strings, so a witness naming a module
    # that has moved fails here rather than passing over an empty list.
    _every([_module_paths()[name] for name in CLOSURE_WITNESSES], "the closure witnesses")

    for module in CLOSURE_WITNESSES:
        probe = (
            f"import json, sys, importlib; importlib.import_module({module!r}); "
            "print(json.dumps(sorted(m for m in sys.modules "
            "if m == 'kingfisher' or m.startswith('kingfisher.'))))"
        )
        out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )
        real = set(json.loads(out.stdout))
        computed = _module_closure(module)

        assert real == computed, (
            f"importing {module} really loads {sorted(real - computed)} that the closure "
            f"misses, and does not load {sorted(computed - real)} that it claims"
        )


def _cycles(graph: dict[str, frozenset[str]]) -> list[tuple[str, ...]]:
    """Every group of modules that can reach each other, and any module reaching itself.

    Tarjan, so one pass answers for the whole graph rather than a walk per module. A
    group of one is a cycle only when it holds an edge to itself; every other group of
    one is just a module.
    """
    order: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[tuple[str, ...]] = []

    def walk(node: str) -> None:
        order[node] = low[node] = len(order)
        stack.append(node)
        on_stack.add(node)
        for onward in sorted(graph.get(node, frozenset())):
            if onward not in order:
                walk(onward)
                low[node] = min(low[node], low[onward])
            elif onward in on_stack:
                low[node] = min(low[node], order[onward])
        if low[node] != order[node]:
            return
        group = []
        while True:
            one = stack.pop()
            on_stack.discard(one)
            group.append(one)
            if one == node:
                break
        if len(group) > 1 or node in graph.get(node, frozenset()):
            found.append(tuple(sorted(group)))

    for node in sorted(graph):
        if node not in order:
            walk(node)
    return sorted(found)


def test_no_module_in_the_package_can_reach_itself():
    """Two modules that import each other at module scope.

    Python resolves one by luck of ordering and the other by raising, and which is which
    depends on who imports first -- so the suite passes while a cold
    `import kingfisher.core.definitions.subagents.catalogue` fails outright, which is what happened
    once, and is recorded under *The core and its plugins* in `docs/decisions.md`. This
    forbids the shape itself, whichever folders it crosses.
    """
    found = _cycles(_package_graph())

    assert not found, (
        f"{found} import each other at module scope — whether that raises depends on "
        "which is imported first, so it can pass here and fail a cold import of either"
    )


def test_the_cycle_finder_finds_one_in_this_tree():
    """Driven, because the rule above passes over a finder that returns nothing at all.

    On the real graph with one edge added rather than on a graph built for the purpose:
    a graph built for the purpose agrees with whatever built it, and says nothing about
    the ninety modules the rule is actually asked about. The edge is a real one in the
    wrong direction -- the innermost layer reaching the outermost -- so what it proves
    is that the finder sees a loop this package could actually grow.
    """
    graph = _package_graph()
    assert "kingfisher.core.access.capabilities" in graph, "the edge below is about nothing"

    closed = dict(graph)
    closed["kingfisher.core.access.capabilities"] = frozenset({"kingfisher.core.service"})
    found = _cycles(closed)

    assert found, "the finder reports nothing on a graph with a cycle in it"
    assert any("kingfisher.core.access.capabilities" in group for group in found), (
        f"the finder found {found}, none of it the loop that was added"
    )


#: What building or running an agent loads, and what makes it cost a second and about
#: 3,100 modules to import. `langchain_core` is not here: `kingfisher.tool` subclasses
#: its `StructuredTool`, and every tool file that says who may use it imports that name.
AGENT_RUNTIME = ("deepagents", "langgraph", "langchain", "langchain_openai", "langchain_anthropic")

#: The modules a deployment imports by name.
PUBLIC = ("kingfisher", "kingfisher.plugins")

#: The public names whose job is building or running an agent, or checking an object
#: deepagents has to accept -- the only ones that may load the runtime to be reached.
RUNS_AN_AGENT = frozenset({
    "Kingfisher", "run", "stream",
    "LocalSessions", "backend_at",
})

#: What the command may load before it knows which verb it is running: it reads `.env`
#: and YAML for every one of them.
THE_COMMAND_LOADS = frozenset({"dotenv", "yaml"})


def _installed_after(code: str) -> frozenset[str]:
    """The installed packages a fresh interpreter holds once it has run `code`.

    Installed rather than "outside the standard library": `sys.modules` also holds a
    virtualenv's site hook and the names a Cython extension registers, and neither is a
    package anything chose to import.
    """
    import subprocess
    from importlib.metadata import packages_distributions

    probe = f"{code}\nimport sys\nprint('\\n'.join(sys.modules))"
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    top = {line.split(".")[0] for line in out.stdout.splitlines()}
    return frozenset(top & packages_distributions().keys()) - {"kingfisher"}


def _touching(qualified: set[str]) -> str:
    """Code that reaches each `module.name`, the way a consumer's import would."""
    return "\n".join(f"import {q.rpartition('.')[0]}; {q}" for q in sorted(qualified))


def _the_command() -> str:
    """The module the `kingfisher` command starts in, as `pyproject.toml` names it.

    Read from there rather than written here, so moving the command cannot leave this
    rule importing a module that no longer starts anything.
    """
    import tomllib

    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return project["scripts"]["kingfisher"].partition(":")[0]


def test_importing_kingfisher_loads_nothing_installed():
    """A consumer that only touches a request or a result paying for packages it never uses."""
    assert _installed_after("import kingfisher") == frozenset()


def test_the_command_starts_without_the_agent_runtime():
    """`kingfisher help` spending a second to print text, because one verb's import moved
    to the top of a module every verb loads.
    """
    loaded = _installed_after(f"import {_the_command()}")

    assert loaded <= THE_COMMAND_LOADS, (
        f"starting the command loads {sorted(loaded - THE_COMMAND_LOADS)} before it knows "
        "which verb it runs -- import it inside the verb that needs it"
    )


def test_a_public_name_that_runs_no_agent_loads_no_runtime():
    """A config, request or result type that starts costing a second, because a module two
    imports away gained a top-level import of the runtime.
    """
    import importlib

    cheap = {
        f"{module}.{name}"
        for module in PUBLIC
        for name in importlib.import_module(module).__all__
        if name not in RUNS_AN_AGENT
    }
    assert cheap, "no public name is left to check, so this rule is about nothing"

    loaded = set(AGENT_RUNTIME) & _installed_after(_touching(cheap))
    # One process for all of them, and one each only once that is red: sixty fresh
    # interpreters on every run would buy nothing but the name of a culprit there is not.
    culprits = (
        sorted(q for q in cheap if set(AGENT_RUNTIME) & _installed_after(_touching({q})))
        if loaded
        else []
    )

    assert not loaded, (
        f"{culprits} load {sorted(loaded)} -- move the import that does it inside the "
        "function that needs it, or, if the name's job is running an agent, add it to "
        "RUNS_AN_AGENT"
    )


def test_every_name_allowed_the_runtime_is_public():
    """A name left in `RUNS_AN_AGENT` after it was renamed or removed, excusing nothing."""
    import importlib

    public = {name for module in PUBLIC for name in importlib.import_module(module).__all__}
    dead = sorted(RUNS_AN_AGENT - public)

    assert not dead, f"{dead} are in RUNS_AN_AGENT and are not public -- take them out"


def test_the_runtime_check_sees_the_runtime():
    """A misspelt `AGENT_RUNTIME` entry, which the rules above could never see loaded.

    Driven on a name that really builds an agent, and every entry asserted rather than any
    one: a check that answered "nothing loaded" for everything would pass all three rules
    above.
    """
    loaded = _installed_after(_touching({"kingfisher.Kingfisher"}))

    assert set(AGENT_RUNTIME) <= loaded, (
        f"reaching `Kingfisher` loads none of {sorted(set(AGENT_RUNTIME) - loaded)}, so "
        "those entries are misspelt or no longer part of the runtime"
    )


#: Names the formats define, which the package writes because they are its own words.
FORMAT_FILES = frozenset({"SKILL.md", "README.md", "__init__.py", "source_ids.yaml"})


def test_the_package_names_nothing_in_assets():
    """`assets/` is this repository's and not in the wheel, so a path, file or definition
    named from it in `src/` points an installed reader at something they do not have.

    Read off the tree rather than listed here, so whatever is added there is covered.
    """
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS, Definitions

    assets = REPO / ASSETS_AREA
    files = {p.name for p in assets.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    roots = Definitions.from_roots({kind: assets / kind for kind in DEFINITION_KINDS})
    names = {
        name.split("::")[-1]
        for name in (*roots.agents.names, *roots.subagents.names, *roots.tools.names,
                     *roots.middlewares.names, *roots.registry.offered,
                     *(n for bundle in roots.bundled_tools.values() for n in bundle.names))
    }
    named = re.compile(
        r"\bassets/|"
        + "|".join(map(re.escape, sorted(files - FORMAT_FILES)))
        + r"|`(?:" + "|".join(map(re.escape, sorted(names))) + r")(?![\w-])"
    )
    found = [
        f"{path.relative_to(REPO)}: {match.group()}"
        for path in sorted((REPO / "src").rglob("*.*"))
        if "__pycache__" not in path.parts
        for match in named.finditer(path.read_text(encoding="utf-8"))
    ]

    assert files and names, "assets/ read as empty, so this checks nothing"
    assert not found, f"src/ names what only this repository has: {found}"


def test_the_package_does_not_depend_on_the_eval_harness():
    """`evals/` is test material and lives outside `src/`, so it is not in the wheel."""
    for path in _every(_package_modules(), "the package"):
        modules = _imported_modules(path)
        assert not any(m.split(".")[0] == "evals" for m in modules), (
            f"{_module_id(path)} imports evals/ — the wheel does not ship it"
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


#: What a request is asked in and answered with, the interfaces a deployment implements,
#: and the access vocabulary they are written in. Named rather than found by folder:
#: they sit among modules that do touch the disk, and what they share is a property,
#: not a place.
RECORDS = (
    "core/request.py",
    "core/result.py",
    "core/interfaces.py",
    "core/access/capabilities.py",
    "core/access/source_ids.py",
    "core/access/reach.py",
    "core/definitions/fields.py",
)


def test_the_records_touch_nothing_outside_the_process():
    """A record that reads a file or runs a command cannot be built without a workspace
    to stand behind it, in a test or in a deployment's own code."""
    reaching = [
        f"{_module_id(path)}: {contact}"
        for path in _every([SRC / record for record in RECORDS], "the records")
        if (contact := _world_contact(path))
    ]

    assert not reaching, (
        f"the records reach the world: {reaching} -- have the caller do it and hand the "
        "record the result"
    )


def test_the_world_rule_sees_a_read_and_an_import(tmp_path):
    """The records pass today, so the rule has to be shown to bite."""
    written = tmp_path / "record.py"
    written.write_text("import subprocess\n\ndef load(p):\n    return p.read_text()\n")
    clean = tmp_path / "clean.py"
    clean.write_text("def load(text):\n    return text.strip()\n")

    assert _world_contact(written) == ["L1 import subprocess", "L4 .read_text()"]
    assert _world_contact(clean) == []


#: Calls that *change* the filesystem, as opposed to reading it or the
#: environment. `open`, `replace` and `copy` are left out because `Session.open`,
#: `dataclasses.replace` and a compiled graph's `copy` are named the same, and all three
#: are legitimate where this rule runs; `shutil.copy` is refused by its import instead.
MUTATING_CALLS = frozenset({
    "mkdir", "rmdir", "rmtree", "copytree", "copyfile", "move",
    "write_text", "write_bytes", "unlink", "touch", "chmod", "rename",
})


#: The flat-core module whose job is writing: it lays out a new workspace. Named by
#: path and asserted to write, so a move fails the rule instead of excusing nothing.
LAYS_OUT_THE_WORKSPACE = SRC / "core" / "workspace.py"


def _disk_writes(path: Path) -> list[str]:
    """Each `shutil` import and filesystem-changing call in one module, by line."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import) and any(a.name == "shutil" for a in node.names):
            found.append(f"{node.lineno} imports shutil")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATING_CALLS
        ):
            found.append(f"{node.lineno} .{node.func.attr}()")
    return found


def _flat_core() -> list[Path]:
    """The modules directly in `core/`: the service and the records it is asked in."""
    return sorted((SRC / "core").glob("*.py"))


def test_the_flat_core_does_not_write_to_disk_itself():
    """Orchestration decides what happens; the package that owns a write makes it happen.

    This was not true when it was written -- `service.py` copied a request's input files
    itself, a `mkdir` and a bare `shutil.copy`. Reading is not the target:
    `core/environment.py` reads the environment, which is its job.

    Measured against the real service before both went through `_checked`: two inputs
    sharing a basename were accepted and one silently lost, and a missing one left the
    earlier files behind in the turn.
    """
    assert _disk_writes(LAYS_OUT_THE_WORKSPACE), (
        f"{_module_id(LAYS_OUT_THE_WORKSPACE)} writes nothing, so it is excused for nothing"
    )
    offenders = [
        f"{_module_id(path)}:{write}"
        for path in _every(_flat_core(), "the flat core")
        if path != LAYS_OUT_THE_WORKSPACE
        for write in _disk_writes(path)
    ]

    assert not offenders, (
        f"{offenders} write to disk from the flat core — put it beside the write it "
        "belongs with, as `core/session/files.py` holds placing a request's inputs"
    )


def _mode_changes(path: Path) -> list[int]:
    """The lines on which a module changes a file's mode.

    A predicate rather than a loop inside the test, so it can be shown to bite against a
    file built for the purpose: the rule below runs over a tree where the answer is
    currently "none", and a rule with no cases passes whether or not it works.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "chmod"
    )


#: The one module in the package that changes a file's mode.
MODE_OWNER = "plugins/sessions/permissions.py"


def test_only_one_module_changes_a_mode():
    """`permissions` owns the write bits on `/inputs`, and the scratch directory's mode.

    Walked over the whole package rather than the folder `permissions` sits in: scoped
    to a folder, this went on passing over one that no longer held it, and checked
    nothing. The owner is named by path and asserted to change a mode, so a move
    fails here instead of exempting nothing.

    Reaching for a mode change when a directory refused a copy is what once left
    root-owned files in a workspace and made a session permanently unusable.
    """
    owner = SRC / MODE_OWNER
    assert _mode_changes(owner), f"{MODE_OWNER} changes no mode, so it owns nothing here"

    offenders = [
        f"{_module_id(path)}:{line}"
        for path in _every(_package_modules(), "the package")
        if path != owner
        for line in _mode_changes(path)
    ]

    assert not offenders, (
        f"{offenders} change a file mode — `{MODE_OWNER}` is the one module allowed "
        "to, and `writable_inputs` is how the rest ask"
    )


def test_the_mode_rule_can_tell_a_chmod_from_the_calls_around_it(tmp_path):
    """The rule above runs over a tree with no offenders, so it has to be shown to bite.

    Both directions, because a predicate answering "yes" to everything would pass the
    first half of this on its own.
    """
    guilty = tmp_path / "placement.py"
    guilty.write_text("def place(path):\n    path.mkdir()\n    path.chmod(0o600)\n")
    innocent = tmp_path / "layout.py"
    innocent.write_text("def place(path):\n    path.mkdir()\n    path.touch()\n")

    assert _mode_changes(guilty) == [3], "a chmod two calls in is the case this exists for"
    assert _mode_changes(innocent) == [], "the neighbouring filesystem calls are not modes"


def test_no_test_stubs_out_agent_construction():
    """The blind spot, closed and kept closed."""
    here = Path(__file__).resolve()
    offenders = []
    for path in sorted(here.parent.glob("test_*.py")):
        if path == here:  # this module names the thing it forbids
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "setattr" in line and "create_deep_agent" in line:
                offenders.append(f"{path.name}:{number}")

    assert not offenders, (
        f"patch create_deep_agent directly at {offenders} — read what the build "
        "attached off the `Assembled` record `build_agent` returns, which needs no "
        "patching and still lets deepagents validate the call"
    )


def test_only_one_module_decides_what_a_skill_is():
    """`--list` exists to tell a caller which names are valid, so it and `build_agent`
    must mean the same thing by "a skill".
    """
    repo = REPO
    owners = {
        KINDS_DIR / "skills" / "spec.py",
        KINDS_DIR / "skills" / "catalogue.py",
    }

    searched = [
        *SRC.rglob("*.py"),
        repo / "tests" / "integration" / "driver.py",
        *(repo / "evals").glob("*.py"),
    ]
    offenders = [
        path.relative_to(repo)
        for path in searched
        if path not in owners and "SKILL.md" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        f"{offenders} decide what a skill is; use core.definitions.skills.spec.FILENAME and "
        "`reachable`, or the registry, so no two readers of the catalogue disagree"
    )


def test_only_the_record_says_what_a_skipped_definition_needs():
    """Three callers printed this and two of them got it wrong.

    `seed` leaves a definition behind for one of two reasons, and the remedies are
    opposite: middleware is registered in code, a source id is declared in a file.
    The command said so; the integration driver and the example script each wrote
    their own line saying "register those" for both. Nothing bound the three, and the
    one people copy was one of the wrong ones.
    """
    repo = REPO
    owner = KINDS_DIR / "seeding.py"

    searched = [
        *SRC.rglob("*.py"),
        repo / "tests" / "integration" / "driver.py",
        repo / "tests" / "integration" / "seed_example.py",
        *(repo / "evals").glob("*.py"),
    ]
    offenders = [
        path.relative_to(repo)
        for path in searched
        if path != owner and 'f"skipped ' in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        f"{offenders} build their own line for a skipped definition; print "
        "`Seeded.report()`, which knows that a source id is declared rather than "
        "registered"
    )


def test_no_definitions_live_inside_the_package():
    """The package ships code.

    A definition reappearing under `src/` would be content read as code -- shipped, and
    skipped by nothing here, because there is no longer anything to skip.
    """

    documents = sorted(str(p.relative_to(SRC)) for p in _definition_documents(SRC))

    assert not documents, (
        f"{documents} are definitions inside the package — they ship in the "
        f"wheel and are read by no rule in this file, which is what made the "
        f"old `CONTENT` exclusion necessary. Definitions belong in "
        f"assets/."
    )


def test_the_content_rule_can_tell_a_document_from_a_package(tmp_path):
    """The proxy this replaced could not, which is why it had to change."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "registry.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    assert _definition_documents(tmp_path) == [], "code is not content"

    (tmp_path / "pkg" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    (tmp_path / "reviewer.yaml").write_text("name: reviewer\n", encoding="utf-8")

    found = {p.name for p in _definition_documents(tmp_path)}
    assert found == {"SKILL.md", "reviewer.yaml"}, "a document under any name is content"


def _definition_documents(root: Path) -> list[Path]:
    """Every definition document below `root`, whatever directory it sits in."""
    from kingfisher.core.definitions.documents import SUFFIX
    from kingfisher.core.definitions.skills.spec import FILENAME as SKILL_FILE

    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.name == SKILL_FILE or path.suffix in {SUFFIX, ".yml"})
    )


def test_this_repository_still_has_a_worked_set(shipped):
    """The other half, and it fails separately.

    `test_shipped_assets` is about the files this names, so without it those tests would
    pass by having no subject.
    """
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

    missing = sorted(kind for kind in DEFINITION_KINDS if not (shipped / kind).is_dir())

    assert not missing, (
        f"assets/ holds no {', '.join(missing)} — this is the set the format "
        "tests read and the one a reader learns from"
    )


def _kinds_without_a_reader(kinds: tuple[str, ...], *, root: Path) -> list[str]:
    """Kinds with no module of their own reading them.

    One place, as of `agents` becoming a module. It took two while three kinds
    had left `catalogue/` and one had not, and a rule with a branch nothing can
    reach is half a rule.
    """
    return sorted(kind for kind in kinds if not (root / kind / "catalogue.py").is_file())


def test_the_kind_rule_can_tell_a_missing_reader_from_a_present_one(tmp_path):
    """Exercised where there is something to find, because the rule above runs on a tree
    where there is not.
    """
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "catalogue.py").write_text("", encoding="utf-8")
    # A directory without the module is the near miss worth telling apart from
    # an absent kind.
    (tmp_path / "tools").mkdir()

    found = _kinds_without_a_reader(("tools", "skills", "ghosts"), root=tmp_path)

    assert found == ["ghosts", "tools"], "a kind with no module of its own is reported"


def test_the_catalogue_holds_one_module_per_kind():
    """Every kind has a reader, bound to the constant that says which kinds exist."""
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

    assert DEFINITION_KINDS, "no kinds — this rule is about nothing"

    # A *file that exists*, not a name in a set. Stated as a set of stems, this
    # rule could be satisfied by widening the set -- a mutation adding every
    # kind to it left the assertion trivially true and nothing went red.
    missing = _kinds_without_a_reader(DEFINITION_KINDS, root=KINDS_DIR)

    assert not missing, (
        f"{missing} is a kind the catalogue reads with no module of its own -- "
        "each kind owns a directory under `kinds/` holding its `catalogue`"
    )


#: What sits in `kinds/` and is not a kind. `importing` loads a workspace's own
#: Python without putting it on the import path, and the kind catalogues that load
#: Python read it; `documents` reads the YAML the agent and subagent formats are
#: written in; `fields` reads one decoded field as the value the format meant, for
#: both those formats and the model catalogue, which share the grammar; `definition`
#: declares once the fields an agent and a subagent both have; `__init__` is
#: the folder's docstring and holds no import on purpose.
#: Another name here is where the kinds start sharing an implementation, which they
#: duplicate in order to avoid -- so it is an edit somebody argues for, not a file
#: that turns up.
#: `catalogue` and `seeding` are not helpers the kinds share but the two readers over all
#: of them: one reads every kind into `Definitions`, the other copies a definitions folder.
KINDS_HELPERS = frozenset(
    {"__init__", "catalogue", "definition", "documents", "fields", "importing", "seeding"}
)


def _kinds_directory_contents(root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """The directories and the loose modules in a `kinds/` directory.

    `root` is a parameter so the reporting can be tested against a tree built wrong on
    purpose, rather than by waiting for this one to go wrong.

    **A directory counts only where it holds Python**, which covers `__pycache__` by
    the same rule rather than by name. Not a loosening: git cannot commit an empty
    directory, so one with no source in it never arrived in a diff -- it is left on a
    working tree, which is what renaming `middleware` to `middlewares` did. The tracked
    files moved, the ignored `__pycache__` beside them did not, and the rule below then
    named a kind whose code was gone on every tree that had run the suite before the
    rename, with nothing in the diff to explain it.
    """
    return (
        frozenset(d.name for d in root.iterdir() if d.is_dir() and any(d.glob("*.py"))),
        frozenset(f.stem for f in root.glob("*.py")),
    )


def test_kinds_holds_exactly_the_kinds():
    """A directory shaped like a kind that is in no constant, and a kind with no
    directory, are both things nothing could say before this folder existed.

    The second direction is the new half. `test_the_catalogue_holds_one_module_per_kind`
    walks `DEFINITION_KINDS` and asks whether each name has a reader, so a sixth
    directory carrying a `spec` and a `catalogue` passes it by never being asked about.
    Flat at the package root there was nothing to compare against -- the top level holds
    four layers and three loose modules besides -- and this is what the move buys.
    """
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

    assert DEFINITION_KINDS, "no kinds -- this rule is about nothing"

    directories, modules = _kinds_directory_contents(KINDS_DIR)

    assert directories == set(DEFINITION_KINDS), (
        f"kinds/ holds {sorted(directories)} where DEFINITION_KINDS names "
        f"{sorted(DEFINITION_KINDS)} -- a directory here is a kind, or it belongs "
        "somewhere that is not this folder"
    )
    assert modules == KINDS_HELPERS, (
        f"kinds/ holds the loose modules {sorted(modules)} rather than "
        f"{sorted(KINDS_HELPERS)} -- a helper the kinds share is the thing they "
        "duplicate to avoid, so adding one means editing that set and saying why "
        "this one is different"
    )


def test_the_kinds_directory_rule_can_tell_a_stray_from_a_kind(tmp_path):
    """Exercised where there is something wrong to find, because the rule above runs
    against a tree where there is not -- which is how it would pass while reading the
    wrong directory, or none.
    """
    for name in ("skills", "widgets"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "catalogue.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    # A kind renamed away, as `middleware` was: the tracked files went with the
    # rename and the ignored build output stayed. Built here rather than trusted
    # to the real tree, because the real tree is clean exactly when somebody has
    # just cleaned it -- and this rule going red days after an unrelated commit
    # is the failure being prevented.
    (tmp_path / "middleware" / "__pycache__").mkdir(parents=True)
    (tmp_path / "middleware" / "__pycache__" / "spec.cpython-312.pyc").write_bytes(b"")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "shared.py").write_text("", encoding="utf-8")

    directories, modules = _kinds_directory_contents(tmp_path)

    assert directories == {"skills", "widgets"}, "a directory that is no kind is reported"
    assert modules == {"__init__", "shared"}, "and so is a loose module beside them"
    assert "__pycache__" not in directories, "build output is not a claim about anything"
    assert "middleware" not in directories, "and neither is what a renamed kind left"


def test_the_package_ships_the_catalogue_example():
    """The one file that is not an asset and has to stay."""
    from importlib import resources

    from kingfisher.core import workspace as workspace_layout

    # Both, from `EXAMPLES` rather than named here, so the file added next is
    # covered by having been added rather than by somebody remembering. The
    # source ids example is optional where the catalogue one is required, and that
    # changes nothing about this: `_place_example` skips a missing source
    # silently, so a packaging fault would show up as a workspace quietly
    # missing furniture rather than as anything failing.
    assert workspace_layout.EXAMPLES, "nothing is asserted if the tuple is empty"
    for name in workspace_layout.EXAMPLES:
        assert (SRC / "core" / "templates" / name).is_file(), f"{name} left the package"
        installed = resources.files(workspace_layout.TEMPLATES).joinpath(name)
        assert installed.is_file(), f"{name} is not reachable the way an install reaches it"


# -- what a caller is handed ------------------------------------------------


#: A dataclass here that is deliberately mutable, with the reason. Empty, and
#: that is the finding: all 58 are frozen, so this is a rule rather than a list.
#: Anything added has to say why a record somebody is handed can be edited
#: underneath them.
MAY_BE_MUTABLE: frozenset[str] = frozenset({
    # `_Turn` is the one record here that is never handed anywhere: it is private
    # to `core/service.py` and exists so that `_turn_outcome` and the
    # loop inside it agree about what the turn produced. Being written in place is
    # the mechanism -- a frozen one would have to be rebuilt on every chunk, and
    # the context manager could not see what the loop wrote. The rule this
    # exempts it from is about a record edited *after crossing a layer*, and this
    # one crosses none.
    "_Turn",
})


def _dataclasses(tree: ast.Module) -> list[tuple[str, bool]]:
    """Every `@dataclass` in a module, and whether it was frozen."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            named = call.func if call else decorator
            if (getattr(named, "id", None) or getattr(named, "attr", None)) != "dataclass":
                continue
            written = {
                keyword.arg: getattr(keyword.value, "value", None)
                for keyword in (call.keywords if call else ())
            }
            found.append((node.name, written.get("frozen") is True))
    return found


def test_every_record_this_package_hands_out_is_frozen():
    """A record edited in place after it was handed across a layer, which nothing
    downstream would notice and no other rule here can see.

    `test_the_record_cannot_be_edited_after_it_is_handed_back` says "frozen, like
    everything else a caller is handed here" and checks one of them. Measured on a
    mutation run: unfreezing any of the other fifty-seven left the whole suite
    green, so the claim in that docstring was true and unguarded.
    """
    mutable = []
    for path in _every(_package_modules(), "the package"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mutable += [
            f"{path.relative_to(SRC).as_posix()}:{name}"
            for name, frozen in _dataclasses(tree)
            if not frozen and name not in MAY_BE_MUTABLE
        ]

    assert not mutable, (
        f"{mutable} are dataclasses this package hands out and they can be edited "
        "in place -- add `frozen=True`, or name them in MAY_BE_MUTABLE with the "
        "reason a caller may rewrite one"
    )


# -- who caused it ---------------------------------------------------------
#
# A consumer that cannot name an error can only catch `ValueError`. All but one
# of the error types here are one, and so is `Request`'s empty-task check, and
# so is whatever a dependency raises -- so that net turns a bug into a refusal
# and a refusal into a 500. Naming them is what makes the difference reportable.

#: Errors a caller can cause and must be able to tell apart. Public.
CALLER_FACING_ERRORS = frozenset({
    "ArtifactError", "CapabilityError", "DecisionError", "QuotaExceededError",
    "SessionBusyError", "SubagentError", "UnknownSessionError",
    "UnsafeReferenceError",
})

#: The rest, which say the deployment is wrong rather than the caller.
#: `HostPathError` is the backend refusing a host path the *agent* produced
#: mid-turn, so it is not a request-time fault at all. Being here does not mean
#: private -- `ConfigError` was public long before this rule existed -- it
#: means a consumer is not expected to branch on it.
DEPLOYMENT_ERRORS = frozenset({
    # `MiddlewareError` joins `ToolError` here for the reason `AgentError` sits here
    # while `SubagentError` sits above: a caller may upload a subagent and cannot
    # upload middleware, so a `middlewares/` file that will not load is always the
    # deployment's own.
    "AccessError", "AgentError", "ConfigError", "HostPathError", "InputsError",
    "LoadError", "MiddlewareError", "ToolError",
})


def _error_classes() -> set[str]:
    found = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Error")
        }
    return found


def test_every_error_is_classified_by_who_caused_it():
    """So a new error cannot arrive unclassified and be a 500 by default."""
    assert _error_classes() == CALLER_FACING_ERRORS | DEPLOYMENT_ERRORS, (
        "a new error type must be added to CALLER_FACING_ERRORS or "
        "DEPLOYMENT_ERRORS -- caller-facing ones must also be exported"
    )


def test_every_caller_facing_error_is_public():
    """The rule the split is for."""
    import kingfisher

    assert set(kingfisher.__all__) >= CALLER_FACING_ERRORS


def test_a_caller_facing_error_is_the_same_class_either_way():
    """The lazy export table resolves to the class itself, not a copy -- so a consumer
    catching `kingfisher.SessionBusyError` catches what the core raises.
    """
    import kingfisher
    from kingfisher.core.session.model import SessionBusyError

    assert kingfisher.SessionBusyError is SessionBusyError


# -- the command is a consumer, not an insider -----------------------------


#: Every consumer held to the front door, and the directory each one lives in. The claim
#: that any caller can drive this library is worth something only if the callers that
#: ship with it are held to it.
CONSUMERS: dict[str, Path] = {
    "cli": SRC / "cli",
}


def _consumer_modules() -> list[Path]:
    return sorted(path for root in CONSUMERS.values() for path in root.rglob("*.py"))


def _reaches_past_the_public_api(module: str) -> bool:
    """True when a consumer imports something deeper than `kingfisher` itself."""
    if module.split(".", maxsplit=1)[0] != "kingfisher" or module in PUBLIC:
        return False
    return not module.startswith("kingfisher.cli")


#: Consumers that ship in this wheel, and may therefore reach for a name the front door
#: does not carry.
FAMILY = frozenset({"cli"})


def _consumer_of(path: Path) -> str:
    """Which consumer this module belongs to."""
    for name, root in CONSUMERS.items():
        if path.is_relative_to(root):
            return name
    msg = f"{path} is under no consumer in CONSUMERS"
    raise AssertionError(msg)


def _public_names() -> frozenset[str]:
    """What the two doors promise, read from the doors themselves."""
    import importlib

    return frozenset().union(*(importlib.import_module(door).__all__ for door in PUBLIC))


def _taken_by_the_back_door(
    module: str, names: frozenset[str], *, family: bool
) -> frozenset[str]:
    """What this import takes past the front door that it should not have."""
    if not _reaches_past_the_public_api(module):
        return frozenset()
    if not family:
        return frozenset({module})
    return names & _public_names() if names else frozenset({module})


def test_the_reach_predicate_says_what_it_means():
    """The rule's own arithmetic, checked against named inputs."""
    for module, reaches in [
            ("kingfisher", False),                      # the front door itself
            ("kingfisher.plugins", False),              # the plugin author's door
            ("kingfisher.plugins.sessions.backend", True),  # past it
            ("kingfisher.core.request", True),        # past it
            ("kingfisher.core.service", True),   # past it, and the tempting one
            # the CLI ships inside the package
            ("kingfisher.cli.health", False),
            ("kingfisher_other.app", False),            # shares the prefix, not the package
            ("fastapi", False),                         # not ours to have an opinion on
        ]:
        assert _reaches_past_the_public_api(module) is reaches


def test_the_rule_above_still_finds_every_consumer():
    """The guard the parametrised rule cannot give itself."""
    found = {name for name, root in CONSUMERS.items() for _ in root.rglob("*.py")}

    assert found == set(CONSUMERS), (
        f"no modules found for {sorted(set(CONSUMERS) - found)} -- the consumer moved "
        "and the rule below is now silently about whatever is left"
    )


def test_a_consumer_uses_the_library_only_through_its_public_api():
    """`from kingfisher import X`, never `from kingfisher.core.y import X`."""
    reaching = []
    for path in _every(_consumer_modules(), "the consumers"):
        family = _consumer_of(path) in FAMILY
        taken = frozenset().union(
            *(
                _taken_by_the_back_door(module, names, family=family)
                for module, names in _imported_names(path).items()
            ),
            frozenset(),
        )
        if taken:
            reaching.append(
                f"{_module_id(path)} reaches for {sorted(taken)} — "
                + (
                    "a name the front door carries comes through the front door, even "
                    "for a consumer shipping in this wheel; that is what keeps the "
                    "claim testable"
                    if family
                    else "a consumer outside this wheel takes `kingfisher` and nothing "
                    "deeper; if it needs something private, export it on purpose"
                )
            )

    assert not reaching, "\n".join(reaching)


def test_both_import_collectors_see_the_same_modules():
    """`_imported_names` may add names; it may not lose an import."""
    disagree = [
        _module_id(path)
        for path in _every(_consumer_modules(), "the consumers")
        if set(_imported_names(path)) != _imported_modules(path)
    ]

    assert not disagree, (
        f"{disagree} are read differently by the two collectors — a rule reading one "
        "while another reads the other is how an import becomes invisible to half "
        "this file"
    )


def test_the_back_door_rule_tells_the_two_consumers_apart():
    """The questions the tree cannot ask."""
    deep = "kingfisher.core.service"
    private = "kingfisher.plugins.sessions.sandbox.confinement"

    # A stranger may not reach, whatever it reached for.
    assert _taken_by_the_back_door(deep, frozenset({"Kingfisher"}), family=False) == {deep}
    assert _taken_by_the_back_door(private, frozenset({"Confinement"}), family=False) == {
        private
    }

    # Family may reach for what the door does not carry, and not for what it does.
    assert _taken_by_the_back_door(private, frozenset({"Confinement"}), family=True) == set()
    assert _taken_by_the_back_door(deep, frozenset({"Kingfisher"}), family=True) == {
        "Kingfisher"
    }

    # `import kingfisher.x.y` names nothing, so neither kind may write it.
    assert _taken_by_the_back_door(private, frozenset(), family=True) == {private}
    assert _taken_by_the_back_door(private, frozenset(), family=False) == {private}

    # The front door itself is not a reach for anybody.
    assert _taken_by_the_back_door("kingfisher", frozenset({"Kingfisher"}), family=True) == set()
    assert _taken_by_the_back_door("kingfisher", frozenset({"Kingfisher"}), family=False) == set()


def test_the_event_kinds_are_what_the_package_emits():
    """`KINDS` is the closest thing to a contract with a caller here, and as prose it had drifted
    both ways -- naming `swept` and `sweep_failed`, which have not fired since
    retention moved off the request path, and omitting `cut_short`, which is how a
    caller learns its answer is incomplete.
    """
    from kingfisher.core.result import KINDS

    emitted = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "RunEvent":
                continue
            for word in node.keywords:
                if word.arg == "kind" and isinstance(word.value, ast.Constant):
                    emitted.add(word.value.value)

    assert emitted == set(KINDS), (
        "KINDS and the kinds actually constructed have diverged — it is what a "
        "caller branching on `event.kind` is promised, so an extra entry is a kind "
        "no caller sees and a missing one is a kind nobody handles"
    )


def test_the_stop_reasons_are_what_the_package_assigns():
    """`STOP_REASONS` is a contract with a caller like `KINDS`, and pinned the same way."""
    from kingfisher.core import result as result_module
    from kingfisher.core.result import STOP_REASONS

    # Every module-level name in `core/result.py` whose value is a stop reason, so
    # that assigning one by its constant counts as producing it. This resolved only
    # `END_TURN`, by name, and the second constant to be assigned that way --
    # `AWAITING` -- then looked like a reason no turn produces. Resolved against the
    # module rather than listed here, so a third cannot repeat it.
    by_name = {
        name: value
        for name, value in vars(result_module).items()
        if isinstance(value, str) and value in set(STOP_REASONS)
    }

    assigned = set()
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            else:
                continue
            # An attribute as well as a name. It was a local until the turn's
            # state moved onto one record so that a sync loop and an async one
            # could share a lifecycle; `turn.stop_reason = "max_steps"` is the
            # same assignment, and a detector reading only `ast.Name` found
            # nothing at all and failed rather than going quiet -- which is the
            # only reason this was noticed.
            named = any(
                (isinstance(t, ast.Name) and t.id == "stop_reason")
                or (isinstance(t, ast.Attribute) and t.attr == "stop_reason")
                for t in targets
            )
            if not named:
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                assigned.add(node.value.value)
            # Or by its constant, which is the spelling `core/result.py` asks for
            # and the one a dataclass default uses. Reading only literals made the
            # *better* spelling look like a reason no turn produces.
            elif isinstance(node.value, ast.Name) and node.value.id in by_name:
                assigned.add(by_name[node.value.id])

    assert assigned == set(STOP_REASONS), (
        "STOP_REASONS and the reasons actually assigned have diverged — the value "
        "reaches every caller as `stop_reason`, so an extra entry is a reason no "
        "turn produces and a missing one is a reason no caller knows to handle"
    )


def _decides_from_a_stop_reason(node: ast.Compare) -> bool:
    """One comparison that reads a decision out of a stop reason's spelling."""
    operands = [node.left, *node.comparators]
    reads = any(
        (isinstance(operand, ast.Attribute) and operand.attr == "stop_reason")
        or (isinstance(operand, ast.Name) and operand.id == "stop_reason")
        for operand in operands
    )
    literal = any(
        isinstance(operand, ast.Constant) and isinstance(operand.value, str)
        for operand in operands
    )
    return reads and literal


def test_no_surface_decides_for_itself_what_a_finished_turn_is():
    """The other half of the pin above: that rule says which reasons exist, this one
    says where a decision may be read out of one.

    Two surfaces ask whether a turn finished -- `Kingfisher.run(delete_session=True)`
    and `kingfisher run --delete-session` -- and they share no code path, because one
    drains `stream` and the other is the drain. Two copies of `stop_reason ==
    "end_turn"` pass every behaviour test in this tree and still come apart over a
    reason added later, with nothing going red: both would still be asserting
    about `end_turn` and `max_steps`, which still work. So the comparison lives in
    `RunResult.completed`, and the only place a new one can be written is the place
    that already has it.
    """
    decided = []
    trees = [path for path in SRC.rglob("*.py") if path != SRC / "core" / "result.py"]
    for path in sorted(trees):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Compare) and _decides_from_a_stop_reason(node):
                decided.append(f"{path.relative_to(REPO)}:{node.lineno}")

    assert not decided, (
        f"a stop reason compared against a literal outside core/result.py: {decided} "
        "— ask `RunResult.completed` instead, or the surfaces that ask will disagree "
        "about a reason added later and nothing will go red"
    )


def _kinds_branched_on(source: str) -> set[str]:
    """Every literal a `self.kind == ...` comparison tests for."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Attribute) and node.left.attr == "kind"):
            continue
        for other in node.comparators:
            if isinstance(other, ast.Constant) and isinstance(other.value, str):
                found.add(other.value)
            elif isinstance(other, ast.Tuple | ast.List | ast.Set):
                found.update(
                    item.value
                    for item in other.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
    return found


def _unreachable(branched: set[str], kinds: tuple[str, ...]) -> set[str]:
    """Branches for a kind no run can emit."""
    return branched - set(kinds)


def test_no_branch_is_written_for_a_kind_that_cannot_exist():
    """The other direction of the rule above, and the one that went unwatched."""
    from kingfisher.core.result import KINDS

    branched = _kinds_branched_on((SRC / "core" / "result.py").read_text(encoding="utf-8"))

    # A reader pointed at the wrong file finds nothing and passes, which is the
    # `_modules_in` failure again: a rule that has quietly stopped being about
    # anything reports success.
    assert branched, "found no kind branches at all — is this still the right file?"
    assert not _unreachable(branched, KINDS), (
        f"result.py branches on {sorted(_unreachable(branched, KINDS))}, which no run "
        "can emit — the branch is unreachable, so delete it or add the kind to KINDS"
    )


def test_the_unreachable_check_can_tell_a_live_branch_from_a_dead_one():
    """Every branch in the tree is live, so the rule above passes whether it subtracts
    anything or nothing.
    """
    assert _unreachable({"swept"}, ("token", "finished")) == {"swept"}
    assert _unreachable({"token"}, ("token", "finished")) == set()
    # The direction that must *not* fire: a kind nobody branches on is the
    # ordinary case, not a fault.
    assert _unreachable(set(), ("token", "finished")) == set()


def test_the_branch_reader_reads_branches():
    """Pinned against source written for the purpose, because the real file is expected
    to be clean and a reader that found nothing would look identical.
    """
    source = (
        "class E:\n"
        "    def __str__(self):\n"
        '        if self.kind == "token":\n'
        "            return 1\n"
        '        if self.kind in ("a", "b"):\n'
        "            return 2\n"
        "        if self.other == \"ignored\":\n"
        "            return 3\n"
        "        return 4\n"
    )

    assert _kinds_branched_on(source) == {"token", "a", "b"}


# -- nothing here is written for tests alone -------------------------------
#
# The recurring failure this guards is not any one module's. Something gets
# written, gets a test, and never acquires a caller -- so the next person who
# needs it either does not find it, or finds it and gets a wrong answer.
# `Capabilities.unknown` was the worst case: the domain's own copy of a rule
# that two adapters had each rewritten, dead, and axis-blind enough that it
# could not have been right if revived. `Capabilities.intersect` was the same
# shape, and T1 caught that one by hand.

#: Where a caller may live. Tests deliberately do not count -- a test is what kept every
#: instance of this alive.
PRODUCTION = ("src/kingfisher", "tests/integration/driver.py", "evals")

#: Names dispatched by something other than a call in this repository. Each is a
#: framework contract rather than a convenience nobody got round to using, and
#: each is listed by name so adding one stays a decision.
DISPATCHED_ELSEWHERE = frozenset({
    # langchain's callback protocol and deepagents' middleware hooks.
    "on_llm_end", "on_llm_error", "on_tool_start", "on_tool_end", "on_tool_error",
    "awrap_model_call", "awrap_tool_call", "wrap_model_call", "wrap_tool_call",
    # Awaited by `steps.adrive`, which calls a step's method with an `a` in front:
    # `changing(shared, "forget", ...)` is the only spelling of this in the tree.
    "aforget",
})


def _production_files() -> list[Path]:
    # The repository, not this package -- the driver and `evals/` live outside
    # `src/`. Named rather than recomputed, so that when the tree last moved this
    # was one line to change instead of a silent walk over the wrong one.
    root = REPO
    files: list[Path] = []
    for name in PRODUCTION:
        target = root / name
        # No exclusion here any more. `presets/` was skipped because it held
        # tools the agent imports and this repository never calls; those are a
        # separate distribution now, so `src/kingfisher` is all production code
        # and the walk covers it whole.
        files += [target] if target.is_file() else list(target.rglob("*.py"))
    return files


def _names_read(source: str) -> set[str]:
    """Every name one module *reads*, ignoring prose and ignoring what it binds."""
    seen: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            # Both halves, and the two forms differ. `from x import DIRECTORY as
            # AGENT_DIRECTORY` reads `DIRECTORY` as surely as a bare import does;
            # recording only the alias made the original invisible, so a constant
            # reached this way by its only caller read as dead.
            for alias in node.names:
                seen.add(alias.name.split(".")[-1])
                if alias.asname:
                    seen.add(alias.asname)
        elif isinstance(node, ast.Import):
            # `import a.b as ROUTE` is not the same shape: the last segment is a
            # *module*, not a name defined in one, so recording it would sight
            # constants that merely share a module's name.
            for alias in node.names:
                seen.add(alias.asname or alias.name.split(".")[-1])
    return seen


def test_an_aliased_import_reads_the_name_it_renames():
    """The scanner recorded the alias and dropped the original, which made a constant
    reached only through `import X as Y` look like one nothing reads.
    """
    read = _names_read(
        "from kingfisher.core.definitions.agents.spec import DIRECTORY as AGENT_DIRECTORY"
    )

    assert "DIRECTORY" in read, "the original name is what the constant is called"
    assert "AGENT_DIRECTORY" in read, "and the alias is what this module now reads"


def _referenced_in_code() -> set[str]:
    """Every name production code reads, across every file production means."""
    seen: set[str] = set()
    for path in _production_files():
        seen |= _names_read(path.read_text(encoding="utf-8"))
    return seen


def _mixed_into(public: frozenset[str]) -> frozenset[str]:
    """Classes an exported class inherits from, which are exported through it."""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef) and node.name in public:
                found.update(b.id for b in node.bases if isinstance(b, ast.Name))
    return frozenset(found)


def _defined_in_package(public: frozenset[str]) -> dict[str, Path]:
    """Module-level functions and classes, plus methods of classes that stand alone."""
    published = public | _mixed_into(public)
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if not node.decorator_list and not node.name.startswith("__"):
                    found[node.name] = path
            elif isinstance(node, ast.ClassDef):
                found[node.name] = path
                if node.bases or node.name in published:
                    continue
                for inner in node.body:
                    if not isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef):
                        continue
                    if inner.name.startswith("_") or inner.decorator_list:
                        continue
                    found[inner.name] = path
    return found


def test_a_mixin_of_an_exported_class_is_published_through_it():
    """The exemption above, asserted rather than left to the rule passing.

    A mixin whose methods stopped being recognised as published would not fail loudly --
    it would ask for `delete_session` to be deleted, and the obvious reading of that
    message is that the method is dead.
    """
    import kingfisher

    mixed = _mixed_into(frozenset(kingfisher.__all__))

    assert {"SessionMethods", "Disposal"} <= mixed, "Kingfisher's mixins are not seen as bases"


def test_nothing_is_defined_for_tests_alone():
    """A function with no caller outside tests is a fourth copy waiting to be written by
    somebody who could not find the third.
    """
    import kingfisher

    used = _referenced_in_code()
    public = frozenset(kingfisher.__all__)

    orphans = {
        name: str(path.relative_to(SRC))
        for name, path in _defined_in_package(public).items()
        if name not in used and name not in public and name not in DISPATCHED_ELSEWHERE
    }

    assert not orphans, (
        f"defined but never used outside tests: {orphans} -- delete it, export it, "
        "or add it to DISPATCHED_ELSEWHERE with the contract that calls it"
    )


# -- and neither is any constant --------------------------------------------
#
# The rule above collects `FunctionDef`, `AsyncFunctionDef` and `ClassDef` off
# each module's body, which is every kind of definition a module has except the
# commonest one. A constant was invisible to it, and `core.access.capabilities.AXES`
# spent its whole life in that blind spot: a public tuple in the domain, read by
# three test files and by nothing in either distribution, deleted only because
# somebody went looking by hand. This half is the same rule pointed at the shape
# the first half could not see.


#: Constants whose reader is somewhere neither `PRODUCTION` nor a test, named one
#: at a time with what reads them. Deliberately not a general "it is published"
#: escape: a name this package publishes belongs in `kingfisher.__all__`, which
#: the rule already exempts, and reaching for this table instead would be the way
#: to publish something without saying so.
READ_ELSEWHERE = frozenset({
    # The event kinds `stream()` yields. Nothing in `src/`, the driver or `evals/`
    # reads it, so its readers are the callers of `stream()` branching on
    # `event.kind`, and they are not in this repository to be counted.
    "KINDS",
    # The stop reasons, and the same argument one line for line: every caller gets
    # one as `RunResult.stop_reason`, so its readers are the callers branching on
    # that value and they are not in this repository. It is a declaration with
    # nothing to derive from -- the rule below is what pins it, against the reasons
    # the package actually assigns.
    "STOP_REASONS",
})


def _constants_defined(source: str) -> list[tuple[str, str]]:
    """Every SCREAMING_CASE name a module binds at its top level, with its value."""
    found: list[tuple[str, str]] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                continue  # `NAME: int` declares a type, not a constant
            targets: list[ast.expr] = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        else:
            continue
        written = ast.unparse(value)
        found += [
            (t.id, written) for t in targets if isinstance(t, ast.Name) and t.id.isupper()
        ]
    return found


def _constants_in_package() -> dict[str, Path]:
    """Every module-level constant in the package, at the first file defining it."""
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        for name, _ in _constants_defined(path.read_text(encoding="utf-8")):
            found.setdefault(name, path)
    return found


def _unread(found: dict[str, Path], read: set[str], public: frozenset[str]) -> dict[str, Path]:
    """Of the constants defined, the ones nothing outside a test reads."""
    return {
        name: path
        for name, path in found.items()
        if name not in read and name not in public and name not in READ_ELSEWHERE
    }


#: Class shapes whose bare annotations are the point rather than a requirement: a
#: dataclass's are its constructor, a Protocol's are its contract, and the others
#: build their members out of them too.
DECLARED_BY_SHAPE = frozenset({"Protocol", "TypedDict", "Enum", "StrEnum", "NamedTuple"})


def test_no_mixin_declares_what_it_does_not_read():
    """A mixin says what it needs from the instance it is mixed into. One that names
    something it never reads is either a requirement that moved or one that was never
    there -- and the second is worse than untidy.

    `SessionMethods` declared `_claims: Path` and nothing anywhere set `_claims`, so
    `self._claims.exists()` type-checked cleanly and raised `AttributeError` on every
    turn that reached it. Two rules already ask this of a module's own definitions
    and of its constants; neither can see a bare annotation in a class body, which is
    where that one lived.
    """
    import ast

    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for klass in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            decorated = {getattr(d, "id", getattr(d, "attr", "")) for d in klass.decorator_list}
            decorated |= {
                getattr(getattr(d, "func", None), "id", "") for d in klass.decorator_list
            }
            inherits = {getattr(b, "id", getattr(b, "attr", "")) for b in klass.bases}
            if "dataclass" in decorated or inherits & DECLARED_BY_SHAPE:
                continue
            declared = [
                node.target.id
                for node in klass.body
                if isinstance(node, ast.AnnAssign) and node.value is None
            ]
            read = {
                node.attr
                for node in ast.walk(klass)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            }
            if dead := [name for name in declared if name not in read]:
                offenders[f"{path.relative_to(SRC)}::{klass.name}"] = dead

    assert not offenders, (
        f"{offenders} are declared and never read by the class declaring them -- "
        "delete the line, or read it where the requirement actually is"
    )


def test_no_constant_is_published_for_tests_alone():
    """A constant nothing reads is a claim about the code the code does not make."""
    import kingfisher

    read = _referenced_in_code()
    public = frozenset(kingfisher.__all__)
    found = _constants_in_package()

    # A collector pointed at the wrong root finds nothing and reports success --
    # the failure this file has shipped twice, once in `_modules_in` and once in
    # the consumer collector. Named layers rather than a count, so this says
    # which part of the package stopped being walked. All three define constants.
    layers = {"core", "plugins", "cli"}
    walked = {path.relative_to(SRC).parts[0] for path in found.values()}

    assert layers <= walked, (
        f"no constants found under {sorted(layers - walked)} -- the walk has shrunk "
        "and this rule is now about whatever is left"
    )

    orphans = {
        name: str(path.relative_to(SRC))
        for name, path in _unread(found, read, public).items()
    }

    assert not orphans, (
        f"defined but never read outside tests: {orphans} -- delete it, export it, "
        "or add it to READ_ELSEWHERE naming what reads it. A constant a test is "
        "the sole reader of pins the test to itself"
    )


def test_the_unread_check_knows_the_three_ways_out():
    """Every constant in the tree is read, published or exempted, so the rule above
    passes whether `_unread` subtracts anything or nothing.
    """
    here = Path("core/result.py")
    found = {"READ": here, "PUBLISHED": here, "KINDS": here, "ORPHAN": here}

    assert _unread(found, {"READ"}, frozenset({"PUBLISHED"})) == {"ORPHAN": here}
    # And the direction that must not fire: nothing defined is nothing to report.
    assert _unread({}, set(), frozenset()) == {}


def test_a_constant_is_not_counted_as_its_own_reader():
    """The mutation the tree cannot catch, because a clean tree is silent about it."""
    assert _names_read("KINDS = ('token',)\n") == set()
    assert _names_read("SOURCES = [ROUTE, OTHER]\n") == {"ROUTE", "OTHER"}
    assert _names_read("SOURCES: list[str] = [ROUTE]\n") == {"list", "str", "ROUTE"}
    assert _names_read("x = mod.KINDS\n") == {"mod", "KINDS"}
    assert _names_read("from m import KINDS\nimport a.b as ROUTE\n") == {"KINDS", "ROUTE"}
    # An aliased `from` import reads both: the name in the other module, and the
    # one this module then uses. `import a.b as ROUTE` reads only `ROUTE`,
    # because `b` is a module rather than a name inside one.
    assert _names_read("from m import KINDS as K\n") == {"KINDS", "K"}
    assert _names_read('"""KINDS is named here in prose only."""\n') == set()


def test_the_constant_reader_reads_module_level_constants():
    """Pinned against source written for the purpose, because the real tree is expected
    to be clean and a reader that found nothing would look identical -- the same
    reason `_kinds_branched_on` has a test of its own.
    """
    source = (
        "ROUTE = '/skills/'\n"
        "SOURCES: list[str] = [ROUTE]\n"
        "FIRST = SECOND = 1\n"
        "LATER: int\n"
        "__version__ = '0.1.0'\n"
        "lower = 1\n"
        "class K:\n"
        "    INNER = 2\n"
        "def f():\n"
        "    ALSO_INNER = 3\n"
    )

    assert _constants_defined(source) == [
        ("ROUTE", "'/skills/'"),
        ("SOURCES", "[ROUTE]"),
        ("FIRST", "1"),
        ("SECOND", "1"),
    ]


def test_every_named_constant_exemption_is_a_real_constant():
    """An exemption for a name nobody defines any more silences nothing, and reads as
    though somebody thought about it.
    """
    defined = set(_constants_in_package())

    assert defined >= READ_ELSEWHERE, (
        f"{sorted(READ_ELSEWHERE - defined)} is exempted but no longer defined in "
        "the package -- drop the entry"
    )


def test_every_console_script_points_at_something_that_exists():
    """A `[project.scripts]` line is only checked when somebody installs and runs."""
    import tomllib
    from importlib import import_module

    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = manifest["project"]["scripts"]
    assert scripts, "the scripts table emptied out"

    for command, target in scripts.items():
        module_name, _, attribute = target.partition(":")
        module = import_module(module_name)
        assert callable(getattr(module, attribute, None)), f"{command} -> {target}"


def _tracked_python() -> list[str]:
    """Every Python file git tracks, as a path from the repository root.

    Asked of git rather than walked: the root also holds a `.venv`, caches and
    whatever a developer left there, and a walk finds different things on different
    machines.
    """
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True, check=False
    )
    assert listed.returncode == 0, f"git could not list the tracked files: {listed.stderr}"
    return listed.stdout.split()


def _ty() -> dict:
    import tomllib

    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ty"]


def test_every_directory_holding_python_is_type_checked():
    """A directory missing from ty's `include` is never checked, and nothing goes red --
    which is how `evals/` went unchecked while the driver imported it.
    """
    tracked = _tracked_python()
    holding = {path.split("/", 1)[0] for path in tracked}
    included = set(_ty()["src"]["include"])

    assert holding, "git lists no Python -- this rule is about nothing"
    assert holding <= included, (
        f"{sorted(holding - included)} holds Python that ty never reads -- add it to "
        "`include` under [tool.ty.src]"
    )


def test_ty_exempts_test_modules_and_nothing_else_under_tests():
    """The override for tests' seams covered all of `tests/`, driver included, and
    ignoring unresolved attributes there let the driver read a `Config` field that had
    been removed until a live smoke run crashed on it.
    """
    from fnmatch import fnmatch
    from pathlib import PurePosixPath

    patterns = [
        pattern for override in _ty().get("overrides", []) for pattern in override["include"]
    ]

    # `*` crosses `/` in fnmatch, and `**/` is also tried as nothing, so this matches
    # at least what ty's globs match for patterns like these: loose towards a red
    # build, never towards a quiet one.
    def exempted(path: str) -> bool:
        return any(
            fnmatch(path, pattern) or fnmatch(path, pattern.replace("**/", ""))
            for pattern in patterns
        )

    under_tests = [path for path in _tracked_python() if path.startswith("tests/")]
    modules = [path for path in under_tests if PurePosixPath(path).name.startswith("test_")]
    helpers = [path for path in under_tests if path not in modules]

    assert helpers, "nothing under tests/ but test modules -- this rule is about nothing"
    assert any(exempted(path) for path in modules), (
        "no override reaches a test module, so this is reading the wrong table"
    )
    assert not [path for path in helpers if exempted(path)], (
        f"{sorted(path for path in helpers if exempted(path))} are not test modules, and "
        "a ty override exempts them from rules the tests need switched off"
    )


#: The module `resolve` lives in, by the tail of its dotted name. Matched that way
#: rather than in full because `_production_files` yields paths, and an import may
#: spell the package relatively.
CONFINEMENT = "confinement"


def _attribute_chain(node: ast.AST) -> str | None:
    """An attribute chain as the source wrote it: `a.b.c` -> `"a.b.c"`.

    Named apart from `_dotted` above, which flattens an *import statement*. Both turn
    syntax into a dotted string and they are not the same question.

    `None` for a chain rooted in anything but a name -- `f().resolve` is not a module
    reach, and guessing at one would make this rule refuse what it cannot see.
    """
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _confinement_aliases(tree: ast.AST) -> set[str]:
    """Local names in this file that stand for the confinement module.

    `import ...sandbox.confinement as sb` binds `sb`, and `from ...sandbox import
    confinement as sb` binds it too -- neither spells `confinement` at the call. The
    unaliased forms need nothing here: `a.b.confinement.resolve` and
    `confinement.resolve` both end in the module's own name, which the caller checks.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound.update(
                alias.asname
                for alias in node.names
                if alias.asname and alias.name.split(".")[-1] == CONFINEMENT
            )
        elif isinstance(node, ast.ImportFrom):
            bound.update(
                alias.asname
                for alias in node.names
                if alias.asname and alias.name == CONFINEMENT
            )
    return bound


def _reaches_resolve(tree: ast.AST) -> bool:
    """Whether this file calls the confinement module's `resolve`, however it says so.

    Six spellings, and the rule used to see two of them: it required an `ast.Name`
    reading exactly `confinement`, so every aliased import and every `getattr` walked
    past. Measured on 2026-10-12 by running the old matcher over all six.
    """
    aliases = _confinement_aliases(tree)

    def is_the_module(node: ast.AST) -> bool:
        name = _attribute_chain(node)
        return name is not None and (name in aliases or name.split(".")[-1] == CONFINEMENT)

    for node in ast.walk(tree):
        # `confinement.resolve`, `a.b.confinement.resolve`, `sb.resolve`.
        if isinstance(node, ast.Attribute) and node.attr == "resolve" and is_the_module(node.value):
            return True
        # `from ...confinement import resolve`, aliased or not.
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[-1] == CONFINEMENT
            and any(alias.name == "resolve" for alias in node.names)
        ):
            return True
        # `getattr(confinement, "resolve")`, which no amount of name-matching reaches.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "resolve"
            and is_the_module(node.args[0])
        ):
            return True
    return False


def test_only_the_confinement_module_calls_resolve_directly():
    """`shell_confinement` is the one place the shell's settings become a confinement."""
    # Production only. A test of `resolve` calls `resolve`, and exempting the
    # test tree is what lets that one keep testing the thing it is about.
    offenders = [
        path.name
        for path in _production_files()
        if path.name != "confinement.py"
        and _reaches_resolve(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert not offenders, (
        f"{sorted(set(offenders))} call `confinement.resolve` directly — use "
        "`shell_confinement(cfg, shell)`, so one place decides what the settings mean"
    )


#: Every way a file can reach that `resolve`, and the two the rule above used to see.
#: A control beside the rule, because the matcher is the thing being asserted and a
#: narrower one passes the rule itself -- which is how it came to see two of six.
SPELLINGS = (
    "confinement.resolve(cfg)",
    "from kingfisher.plugins.sessions.sandbox.confinement import resolve",
    "import kingfisher.plugins.sessions.sandbox.confinement as sb\nsb.resolve(cfg)",
    "from kingfisher.plugins.sessions.sandbox import confinement as sb\nsb.resolve(cfg)",
    "import kingfisher.plugins.sessions.sandbox.confinement\n"
    "kingfisher.plugins.sessions.sandbox.confinement.resolve(cfg)",
    'getattr(confinement, "resolve")(cfg)',
)


@pytest.mark.parametrize("source", SPELLINGS)
def test_the_resolve_rule_sees_every_spelling_of_that_reach(source):
    """Each of these is one file reaching past `shell_confinement`, and the rule above
    is only worth its name if it sees all of them.
    """
    assert _reaches_resolve(ast.parse(source)), f"this spelling walks past the rule: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        "other.resolve(cfg)",
        "from kingfisher.core.access.source_ids import parse",
        "confinement.profile(home=h)",
        'getattr(confinement, "profile")(home=h)',
        "make().resolve(cfg)",
    ],
)
def test_the_resolve_rule_does_not_see_a_reach_that_is_not_one(source):
    """The control the rule needs: a matcher answering `True` for everything would pass
    every case above and refuse the whole codebase.
    """
    assert not _reaches_resolve(ast.parse(source))


# -- the path every other rule starts from ---------------------------------


def _fake_checkout(root: Path) -> Path:
    """A directory that looks like this repository to `_repository_root`."""
    (root / "src" / "kingfisher").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    return root


def test_the_root_holds_this_file():
    """The property that makes every other rule here mean anything."""
    assert Path(__file__).resolve().is_relative_to(REPO)


def test_the_root_is_the_nearest_one_not_an_outer_one(tmp_path):
    """A checkout inside another checkout picks the inner one."""
    outer = _fake_checkout(tmp_path / "outer")
    inner = _fake_checkout(outer / "nested" / "inner")
    deep = inner / "tests"
    deep.mkdir()

    assert _repository_root(deep / "a_test.py") == inner


def test_no_root_at_all_is_an_error_rather_than_a_climb(tmp_path):
    """It raised `StopIteration` from a generator, which reads as a collection error
    nobody can act on.
    """
    lonely = tmp_path / "nowhere" / "tests"
    lonely.mkdir(parents=True)

    with pytest.raises(AssertionError, match="no repository root"):
        _repository_root(lonely / "a_test.py")


def test_a_directory_that_only_half_matches_is_not_the_root(tmp_path):
    """Both halves of the marker are load-bearing."""
    outer = _fake_checkout(tmp_path / "outer")
    half = outer / "nested"
    (half / "tests").mkdir(parents=True)
    (half / "pyproject.toml").write_text("", encoding="utf-8")  # no src/kingfisher

    assert _repository_root(half / "tests" / "a_test.py") == outer


def test_every_path_rule_starts_from_the_same_two_names():
    """Four separate computations of "the repository" is how three of them came to
    disagree.
    """
    assert SRC == REPO / "src" / "kingfisher"
    assert SRC.is_dir()
    assert (REPO / "pyproject.toml").is_file()


def test_no_value_is_written_down_twice():
    """One definition per value, across the library."""
    seen: dict[tuple[str, str], list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for name, value in _constants_defined(path.read_text(encoding="utf-8")):
            seen.setdefault((name, value), []).append(str(path.relative_to(SRC)))

    twice = {
        f"{name} = {value}": places
        for (name, value), places in seen.items()
        if len(places) > 1
    }

    assert not twice, (
        "defined in more than one place, with the same value: "
        f"{twice}. Import it from wherever it belongs; a second definition is a "
        "second thing to keep in step, and the two will agree by coincidence "
        "until they do not."
    )


#: Import name -> distribution, for the one `packages_distributions()` cannot
#: answer here. It is absent from a macOS checkout for a reason that is the point
#: rather than an oversight: `sandlock` ships Linux-only wheels. A guard that only
#: worked where everything happened to be installed would report success on the
#: machine where the dependency is missing, which is the one case worth catching.
PROVIDED_BY: dict[str, str] = {
    "sandlock": "sandlock",
}


def _canonical(requirement: str) -> str:
    """The distribution name inside a requirement string, PEP 503 normalised."""
    name = re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0]
    return name.strip().lower().replace("_", "-")


def _declared_distributions() -> set[str]:
    """Every distribution `pyproject.toml` names, extras included."""
    import tomllib

    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    named = list(manifest["project"]["dependencies"])
    for extra in manifest["project"].get("optional-dependencies", {}).values():
        named.extend(extra)
    return {_canonical(name) for name in named}


def _providers(module: str) -> set[str]:
    """Which installed distributions ship this exact module, not merely its root.

    Falls back to `PROVIDED_BY` for what is not installed here and returns the empty set
    for what is neither, which fails the rule -- as an import nobody can account for
    should.

    Several distributions answer to `langgraph`, so a check on the root name declared
    `langgraph.errors` satisfied by `langgraph-checkpoint`, which does not ship it.
    """
    from importlib.metadata import files, packages_distributions

    root, _, _ = module.partition(".")
    candidates = packages_distributions().get(root)
    if not candidates:
        named = PROVIDED_BY.get(root)
        return {named} if named else set()

    wanted = Path(*module.split("."))
    shipping = set()
    for dist in candidates:
        for path in files(dist) or ():
            carried = Path(*Path(str(path)).parts)
            if carried == wanted.with_suffix(".py") or wanted / "__init__.py" == carried:
                shipping.add(_canonical(dist))
                break
    return shipping


def _foreign_imports() -> dict[str, Path]:
    """Every non-stdlib module `src/` imports, and one file that imports it."""
    found: dict[str, Path] = {}
    for path in _package_modules():
        for module in _imported_modules(path):
            if module.split(".")[0] in sys.stdlib_module_names:
                continue
            if module == "kingfisher" or module.startswith("kingfisher."):
                continue
            found.setdefault(module, path)
    return found


def test_every_package_the_library_imports_is_a_declared_dependency():
    """A transitive dependency is not a promise, checked rather than repeated."""
    declared = _declared_distributions()
    undeclared = {
        module: path
        for module, path in _foreign_imports().items()
        if not (_providers(module) & declared)
    }
    assert not undeclared, (
        "imported but not declared in pyproject.toml: "
        + ", ".join(f"{m} ({_module_id(p)})" for m, p in sorted(undeclared.items()))
        + " -- it may resolve today through another package's requirements, and "
        "that is not a promise it will resolve tomorrow"
    )


def test_the_provider_lookup_is_not_fooled_by_a_shared_root_name():
    """The mutation the rule above cannot catch by passing."""
    assert "langgraph" in _providers("langgraph.errors")
    assert "langgraph-checkpoint" not in _providers("langgraph.errors")

    assert "langgraph-checkpoint" in _providers("langgraph.checkpoint.memory")
    assert "langgraph" not in _providers("langgraph.checkpoint.memory")

    assert _providers("sandlock") == {"sandlock"}
    assert _providers("nothing_ships_this") == set()


#: Prose naming an attribute of a class this package exports. The complement to
#: `PROSE_REF` above, which is rooted at a *layer* and therefore cannot see
#: `Config.resolve_model` at all -- the head is a class, not a package.
#:
#: Restricted to exported classes on purpose, and that is what makes it sound
#: rather than noisy. Measured across this repository when it was written: this
#: form gives a handful of references and found three that were wrong, every one
#: of them naming something that has never existed --
#:
#:   `Config.resolve_model`     it is `cfg.models.resolve`
#:   `Capabilities.unknown`     it is `Offering.refuse_unknown`, a file away
#:   `Kingfisher._snapshot`     it is `workspace_snapshots.agent_snapshot`
#:
#: -- while the unrestricted form, matching any capitalised head, cannot tell a
#: class this package owns from one it is describing in someone else's library.
PROSE_ATTR = re.compile(r"`([A-Z][A-Za-z0-9]*)\.([a-z_][a-z0-9_]*)`")

#: Prose naming a class attribute *because* it is gone, excused per file. Empty,
#: and kept for the reason `PROSE_GONE` is keyed by file: the day a docstring
#: explains which rename removed an attribute, that is the rule working rather
#: than a defect, and there has to be somewhere to say so.
PROSE_ATTR_GONE: dict[str, frozenset[str]] = {}


def _exported_classes() -> dict[str, type]:
    """The public names that are classes, resolved through the lazy front door."""
    import kingfisher

    found = {}
    for name in kingfisher.__all__:
        value = getattr(kingfisher, name)
        if isinstance(value, type):
            found[name] = value
    return found


def test_prose_names_class_attributes_that_exist():
    """A docstring pointing at a method nobody has is a wrong map, not a typo."""
    classes = _exported_classes()
    wrong: list[str] = []
    for path in _package_modules():
        relative = path.relative_to(REPO).as_posix()
        excused = PROSE_ATTR_GONE.get(relative, frozenset())
        for head, attr in PROSE_ATTR.findall(path.read_text(encoding="utf-8")):
            reference = f"{head}.{attr}"
            if head not in classes or reference in excused:
                continue
            if not hasattr(classes[head], attr):
                wrong.append(f"{relative}: `{reference}`")

    assert not wrong, (
        "prose names an attribute the class does not have: "
        + ", ".join(sorted(wrong))
        + " -- correct it, or add it to PROSE_ATTR_GONE with the rename that "
        "removed it"
    )


def test_the_class_attribute_rule_is_looking_at_something():
    """A rule that resolves no classes passes every module it is pointed at."""
    classes = _exported_classes()
    assert len(classes) >= 10, f"only {len(classes)} exported classes resolved"
    for name in ("Config", "Capabilities", "Kingfisher", "Request"):
        assert name in classes, f"{name} is exported and is a class, but was not collected"

    # And the matcher has to find the shape it is about.
    assert PROSE_ATTR.findall("see `Config.resolve_model` for this") == [
        ("Config", "resolve_model")
    ]
    assert PROSE_ATTR.findall("`models.yaml` and `core.session`") == []
