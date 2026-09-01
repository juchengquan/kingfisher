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
import re
import sys
from pathlib import Path

import pytest


def _repository_root(start: Path | None = None) -> Path:
    """The checkout this file is in, found rather than counted.

    `start` is for the tests below, and it is a parameter rather than a second
    copy of the walk for a reason found by mutation: the first version of those
    tests restated this function so it could be driven from a fake tree, and
    then every one of them tested the restatement. Three mutations of the real
    walk survived, including one that let it climb out of the checkout -- the
    exact failure this rewrite exists to prevent.

    Every rule below reads from a path, and all four ways of arriving at one
    have now been wrong at least once:

    - **Counting levels** (`Path(__file__).parent.parent`) is correct until the
      tree moves. It broke twice this month, and neither time did it *fail* --
      the rules pointed at a directory that no longer held what they were
      about and went on passing.
    - **Matching a marker** upward broke worse. The marker was a directory
      holding `pyproject.toml` *and* `packages/`; when `packages/` went, nothing
      in the checkout matched, so the walk climbed out of it entirely and found
      the parent clone -- which still had one. Every rule then read a different
      repository and passed. Only CI, with no parent clone, went red.

    So this searches upward for a marker that is *this* project, and stops with
    a real message rather than climbing past the checkout. `src/kingfisher`
    beside a `pyproject.toml` is the definition of this repository; the nearest
    match going up is this one, and if there is no match at all that is a broken
    checkout worth saying so about.
    """
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


#: The checkout, and the library inside it. Everything path-shaped here starts
#: from one of these two, so a move is one line rather than four -- which is how
#: three of the four came to disagree the last time this tree changed shape.
#:
#: Deriving `SRC` from `REPO` rather than counting to it again is unobservable
#: today and left that way deliberately: in this layout the two expressions
#: name the same directory, so a mutation swapping them survives and no test can
#: separate them. They part company exactly when the tree moves, which is the
#: case this whole finder exists for and the one a test cannot stage.
REPO = _repository_root()
SRC = REPO / "src" / "kingfisher"


def _package_of(path: Path) -> tuple[str, ...]:
    """The dotted package a module sits in, walked rather than counted.

    Climbs while there is an `__init__.py`, so it answers the same way for the
    real tree and for the fake ones these tests build -- and does not need to
    know where `src/` is, which is the assumption every other way of finding
    this has eventually got wrong here.
    """
    parts: list[str] = []
    directory = path.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return tuple(reversed(parts))


def _imported_modules(path: Path) -> set[str]:
    """Every module this file imports, relative ones resolved to their real name.

    Relative imports were dropped, not resolved: the guard was `elif
    isinstance(node, ast.ImportFrom) and node.module`, and `from . import x`
    has no `module` at all. So it contributed nothing, and every rule in this
    file reported success without having looked.

    That is exploitable rather than merely untidy. `from .. import config` in a
    domain module is the import the domain rule exists to refuse -- the layer
    reading deployment configuration -- and it was invisible, while the same
    import written out was caught. A rule that depends on which spelling
    someone used is not a rule.

    Resolved rather than refused, so the analysis reads relative imports as
    what they are. Refusing them would be this file legislating a style because
    it could not parse one, and `assets/` already uses them for a reason of its
    own: those files are copied out into a workspace, where nothing is named
    `kingfisher` any more.
    """
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


def _modules_in(layer: str, root: Path = SRC) -> list[Path]:
    """Every module in a layer, subpackages included.

    This was `glob` -- one directory deep. Nine rules in this file read from
    it, so the first module to move into a subpackage would drop out of all
    nine at once, and all nine would keep passing. Not a rule getting weaker:
    a rule silently ceasing to be about the file it was written for, which is
    the failure mode this whole file exists to prevent elsewhere.

    Nothing had subpackages when this changed, so it caught nothing on the day
    -- which is the argument for changing it then rather than in the commit
    that first needed it. `_consumer_modules`, a few lines from the bug, had
    used `rglob` since it was written.

    `root` is here so the behaviour can be tested against a tree built for the
    purpose, rather than by waiting for a real subpackage to prove it.
    """
    return sorted(
        p
        for p in (root / layer).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _module_id(path: Path) -> str:
    """`harness/agent.py`, not `agent.py`.

    A bare filename stops identifying a module once two layers can hold the
    same one, and the failure message has to name a file someone can open.

    Relative to the repository for anything outside `src/kingfisher`, because
    one consumer is a distribution of its own and `relative_to(SRC)` raises on
    it rather than naming it. `service/src/kingfisher_service/app.py` is longer
    than `app.py` and is the thing someone can open.
    """
    if path.is_relative_to(SRC):
        return path.relative_to(SRC).as_posix()
    return path.relative_to(REPO).as_posix()


def _inside_domain(module: str) -> bool:
    return module == "kingfisher.domain" or module.startswith("kingfisher.domain.")


def test_a_layer_rule_reaches_into_a_subpackage(tmp_path):
    """The collection every rule below is built on, tested for the case it missed.

    Nine rules take their files from `_modules_in`. While it used `glob`, a
    module one directory deeper was not merely unchecked -- it was invisible,
    and the rule that should have covered it reported success. That is worse
    than an absent rule, which at least does not claim anything.

    Built here rather than asserted against `src/`, so it keeps testing the
    collection after the real tree changes shape. `import yaml` is the
    violation the domain rule was written for -- the third-party parser that
    sat in `domain/fields.py` while three rules looked straight past it.

    An `__init__.py` counts, and that is the second half of the same defect.
    The walk skipped them, which was harmless while every one in this package
    held a docstring and nothing else -- and stopped being harmless the moment
    `infrastructure/catalogue/` made one hold two hundred and fifty lines,
    including the package's only edge into the harness. Nine rules would have
    looked straight past it, exactly as they looked past a subpackage before.
    Skipping a file because of its name is the same mistake as skipping it
    because of its depth.
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
    """`from .. import config` in a domain module used to pass every rule here.

    The collector kept only `node.module`, and a relative import written that
    way has none -- so the import contributed nothing and the rules reported
    success without having looked. Written out as
    `from kingfisher.config import Config` the same import was caught, which
    made the layer boundary a question of spelling.

    Built against a fake tree for the reason the collection test above is: it
    has to keep testing the resolution after the real one changes shape. Both
    forms are checked, because they fail differently -- one was invisible, the
    other was read as a foreign top-level package.
    """
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


def test_a_harness_edge_is_seen_however_it_is_spelled(tmp_path):
    """`_harness_reach` had the same blindness as `_imported_modules`, found
    only after the other was fixed and the file searched for the rest.

    It compares `node.module` against the harness's absolute name, so
    `from .harness import agent` matched neither branch and the edge was lost --
    which would let a module absent from `HARNESS_EDGES` reach the harness with
    the rule reporting success. Its own docstring records that a first draft
    detected none of these edges at all, so this is the second near-miss for the
    same function.
    """
    package = tmp_path / "kingfisher" / "infrastructure"
    (package / "harness").mkdir(parents=True)
    for marker in (
        tmp_path / "kingfisher" / "__init__.py",
        package / "__init__.py",
        package / "harness" / "__init__.py",
    ):
        marker.touch()

    absolute = package / "absolute.py"
    absolute.write_text(
        "from kingfisher.infrastructure.harness import agent\n", encoding="utf-8"
    )
    relative = package / "relative.py"
    relative.write_text("from .harness import agent\n", encoding="utf-8")

    assert _harness_reach(absolute) == {"agent"}
    assert _harness_reach(relative) == {"agent"}, "the spelling that used to hide the edge"


def test_a_module_is_identified_by_where_it_is_not_what_it_is_called():
    """Every failure message in this file is built from `_module_id`.

    `tool.py` was a sufficient answer to "which file" while each layer was one
    directory deep. It stops being one the moment a subpackage exists, and the
    message would then send its reader to a file with the right name and the
    wrong contents -- a worse outcome than saying nothing.

    Asserted because nothing else asserts it. Four refusal messages in
    `capabilities.py` drifted apart for exactly this reason: they existed to be
    read, and no test held them to reading well.
    """
    assert _module_id(SRC / "domain" / "tool.py") == "domain/tool.py"
    assert _module_id(SRC / "infrastructure" / "harness" / "tool.py") == (
        "infrastructure/harness/tool.py"
    )


#: Everything in this repository that may import kingfisher. The other two
#: distributions are included deliberately: they are separate wheels that depend
#: on this one, so they are the first place a move here breaks and the last place
#: anyone thinks to look.
#:



def _everything_that_imports_kingfisher() -> list[Path]:
    areas = ("src", "tests", "service", "evals", "spikes")
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


def _names_a_real_module(module: str) -> bool:
    """Resolved on disk rather than imported.

    `importlib.util.find_spec` would answer the same question by executing
    every parent package on the way, which for `kingfisher.presentation.cli` means
    fastapi and for the harness means three provider SDKs. This rule should
    cost nothing and have no way to fail for a reason other than the one it is
    about.
    """
    base = SRC.parent.joinpath(*module.split("."))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def test_every_kingfisher_import_in_this_repository_names_a_module_that_exists():
    """The rule that was missing when `infrastructure/harness/` landed.

    `assets/tests/test_shipped_assets.py` imported
    `kingfisher.infrastructure.agent`, the move renamed it, and nothing here
    noticed -- every rule in this file reads `src/`, and the suite was run as
    `pytest tests/` where `assets/` is not collected at all. CI runs bare
    `pytest`, so it found it, one merge too late.

    Checking the *path* rather than the symbol is deliberate. A dangling module
    path is the failure a move causes, it is mechanical to detect, and it costs
    nothing; a dangling name inside a module is what the type checker is for.
    """
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
    """Everything in the repository resolves, so the rule above passes whether
    it discriminates or answers `True`. These are the two answers the tree
    cannot supply -- and the negatives are the paths this series actually
    removed, so they keep being asserted gone rather than merely absent.
    """
    assert _names_a_real_module("kingfisher")
    assert _names_a_real_module("kingfisher.domain.tool")
    assert _names_a_real_module("kingfisher.infrastructure.harness")
    assert _names_a_real_module("kingfisher.infrastructure.harness.agent")

    assert not _names_a_real_module("kingfisher.infrastructure.agent")
    assert not _names_a_real_module("kingfisher.server")
    assert not _names_a_real_module("kingfisher.server.asgi")


def test_the_second_distribution_is_in_scope():
    """`assets/` is where the move actually broke, and the rule is worth nothing
    if it stops looking there. It is a separate wheel depending on this one, so
    it is the first thing a move here breaks and the last place anyone checks --
    which is precisely what happened.
    """
    scanned = {p.relative_to(REPO).parts[0] for p in _everything_that_imports_kingfisher()}
    assert "service" in scanned, (
        "the dangling-import rule is not reading service/ — the other distribution "
        "is where a move in src/ lands first"
    )


#: The layers a prose reference can be rooted at, and the only form of it that
#: can be checked. `models.yaml`, `run.py` and `uploads.provision` are shaped
#: exactly like module paths; `infrastructure.harness.backend` cannot be
#: anything else. Measured across this repository: the rooted form gives fifty
#: references and finds thirteen that are wrong, while the unrestricted form
#: gives 143 and calls 115 of them broken.
PROSE_LAYERS = ("domain", "application", "infrastructure", "presentation")
PROSE_REF = re.compile(
    r"`((?:kingfisher\.)?(?:" + "|".join(PROSE_LAYERS) + r")(?:\.[a-z_]+)+)`"
)

#: Prose that names a module *because* it is gone, excused per file. Deny by
#: default like the tables above, and keyed by file rather than by name for a
#: reason the first draft found: `infrastructure.agent` appears twice in this
#: repository, once in the docstring explaining which move renamed it and once
#: in `domain/subagent.py` as a live pointer at where a spec is translated. One
#: is the rule doing its job and the other is the defect it exists to catch, and
#: a table keyed by name alone would have to excuse both.
PROSE_GONE: dict[str, frozenset[str]] = {
    # The file that owns the rules is the one place a gone module is named on
    # purpose -- in the docstring of the rule that renaming broke, and in the
    # negatives below, which are asserted gone rather than merely absent.
    "tests/unit/test_architecture.py": frozenset({
        "infrastructure.agent",
        "infrastructure.backend",
        "infrastructure.backend.prepare_scratch",
        "infrastructure.workspace_fs.resolve_definitions",
    }),
}


def _module_file(name: str) -> Path | None:
    """The file a dotted name refers to, or `None`.

    Resolved on disk rather than imported, for the reason `_names_a_real_module`
    gives: reaching `kingfisher.presentation.cli` through `find_spec` executes
    fastapi on the way, and a rule should have no way to fail for a reason other
    than the one it is about.
    """
    base = SRC.joinpath(*name.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    return base / "__init__.py" if (base / "__init__.py").exists() else None


def _top_level_names(path: Path) -> set[str]:
    """What a module defines or imports at its top level.

    Parsed rather than imported, and only ever asked about a module some comment
    already named, so the cost is a handful of files rather than the tree.
    """
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return names


def _prose_unresolved(text: str, excused: frozenset[str] = frozenset()) -> list[str]:
    """References in some text that name neither a module nor something in one.

    Two answers count as resolved, and the second is why this reads the target
    file instead of a set of module names. A reference may name a module, or a
    module and one thing defined in it -- `domain.skill.split` and
    `infrastructure.harness.backend.prepare_scratch` are both correct prose about
    a real function, and refusing them would refuse the most useful thing a
    comment can say.

    Checking the trailing segment is what closes the hole the first draft had.
    Matching a *parent* was enough to pass, and `infrastructure` is a package,
    so every `infrastructure.<gone module>` resolved as a package and an
    attribute nobody looked for -- which is exactly the shape of six of the
    thirteen references this found on the day it was written.
    """
    unresolved = []
    for ref in PROSE_REF.findall(text):
        bare = ref.removeprefix("kingfisher.")
        if bare in excused or _module_file(bare):
            continue
        parent, _, last = bare.rpartition(".")
        target = _module_file(parent)
        if target is None or last not in _top_level_names(target):
            unresolved.append(bare)
    return unresolved


def test_prose_naming_a_module_names_one_that_exists():
    """A comment naming a module makes a claim, and a move falsifies it in silence.

    The rule above does this for `import` statements, and its own docstring says
    why it checks the path rather than the symbol. Nothing did it for prose, and
    thirteen references were wrong when this was written -- six of them left by
    one move, `infrastructure/harness/`, which renamed four modules that eight
    comments went on naming at the old path.

    The same files as the dangling-import rule, for the reason stated there: the
    other distributions are where a move here lands first.
    """
    stale = []
    for path in _everything_that_imports_kingfisher():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        excused = PROSE_GONE.get(rel, frozenset())
        stale += [f"{rel} -> {ref}" for ref in _prose_unresolved(text, excused)]

    assert not stale, (
        f"{stale} name kingfisher modules that do not exist — something moved "
        "and the comment about it did not"
    )


def test_the_driver_is_not_collected():
    """The one module here that spends money must never be run by `pytest`.

    It lives under `tests/` and is reached by `testpaths`, so nothing keeps it
    out except its name: pytest collects `test_*.py` and `*_test.py`, and
    `driver.py` is deliberately neither. Rename it to `test_driver.py` and a
    bare `pytest` starts making real model calls against whatever key the
    machine holds.

    Checked by the naming rule rather than by running a collector, because that
    *is* the rule -- a collector would only agree with it, and slowly.

    The whole directory, not just the one file. What is dangerous here is the
    shelf, and a second live driver added beside this one would arrive with the
    same hazard and no test looking for it.
    """
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
    """`tests/` itself holds the shared fixtures and nothing else.

    The split is by what a test *costs to run*, not by what it is about: 1,669
    offline tests finishing in eleven seconds on one shelf, and on the other the
    single thing that reaches a live model. A test file left at the top level
    belongs to neither and says nothing about which it is -- which is the state
    this rule exists to keep the tree out of.

    `conftest` stays at the top because both shelves use it, and pytest only
    shares a conftest downward.
    """
    loose = sorted(p.name for p in (REPO / "tests").glob("*.py") if p.name != "conftest.py")

    assert not loose, (
        f"{loose} sit between the two shelves — a test belongs under unit/ if it "
        "is offline and fast, or integration/ if it costs money to run"
    )
    assert (REPO / "tests" / "unit").is_dir()
    assert (REPO / "tests" / "integration").is_dir()


def test_the_prose_rule_can_tell_a_gone_module_from_a_real_one():
    """Every reference in the tree resolves once the thirteen are fixed, so the
    rule above passes whether it discriminates or answers nothing at all. These
    are the answers the tree cannot supply, and the negatives are the names this
    commit actually removed -- asserted gone rather than merely absent.
    """
    assert _prose_unresolved("`infrastructure.harness.backend`") == []
    assert _prose_unresolved("`domain.skill.split` and `application.inventory`") == []
    assert _prose_unresolved("`infrastructure.harness.backend.prepare_scratch`") == []

    assert _prose_unresolved("`infrastructure.backend.prepare_scratch`") == [
        "infrastructure.backend.prepare_scratch"
    ]
    # A package and a name it does not hold. This is the one a parent-only check
    # let through, and six of the thirteen were this shape.
    assert _prose_unresolved("`infrastructure.backend`") == ["infrastructure.backend"]
    # Excused, but only where the table says so.
    assert _prose_unresolved("`infrastructure.agent`") == ["infrastructure.agent"]
    assert _prose_unresolved("`infrastructure.agent`", frozenset({"infrastructure.agent"})) == []

    # Not rooted at a layer, so not this rule's business: these are the shapes
    # that make the unrestricted version unusable.
    assert _prose_unresolved("`models.yaml`, `run.py`, `importlib.resources`") == []


def test_no_rule_here_is_parametrized_over_nothing():
    """A directory that stops existing takes its rule down with it, silently.

    `pytest.mark.parametrize` over an empty list generates no cases, and a rule
    with no cases passes. Found by mutation while renaming `server/` to
    `presentation/`: pointing the collector at the old name left
    `test_the_server_uses_the_library_only_through_its_public_api` covering
    fifteen modules one moment and zero the next, with a green run either way.

    Same shape as the `glob`/`rglob` bug above and the same reason it matters --
    a rule that has quietly stopped being about anything is worse than one that
    was never written, because the file still reads as though it is covered.
    """
    collections = {
        "domain": _modules_in("domain"),
        "application": _modules_in("application"),
        "infrastructure": _modules_in("infrastructure"),
        "the package": _package_modules(),
        "consumers": _consumer_modules(),
    }
    empty = sorted(name for name, found in collections.items() if not found)
    assert not empty, (
        f"{empty} collected no modules — a renamed or moved directory has taken "
        "its rules with it, and every one of them is still reporting success"
    )


def test_compiled_files_are_not_mistaken_for_modules(tmp_path):
    """`rglob` descends into `__pycache__`, which `glob` never reached.

    Nothing there is a `.py`, so this holds today by accident rather than by
    the filter. It is the filter that is being pinned: a stray source file
    under `__pycache__` would otherwise be parametrized as a module of the
    layer and named in a failure nobody could act on.
    """
    layer = tmp_path / "domain"
    (layer / "__pycache__").mkdir(parents=True)
    (layer / "__pycache__" / "stale.py").write_text("import yaml\n", encoding="utf-8")
    (layer / "real.py").touch()

    assert [p.name for p in _modules_in("domain", root=tmp_path)] == ["real.py"]


@pytest.mark.parametrize("path", _modules_in("domain"), ids=_module_id)
def test_domain_imports_only_the_standard_library_and_itself(path):
    """Deny by default, replacing three rules that were allowlists by omission.

    Each named something the domain must not import -- the harness, the layers
    above it, `Config` -- and passed for everything nobody had thought of.
    `yaml` was the standing example: a third-party parser sitting in
    `domain/fields.py`, which no rule mentioned and so no rule caught.

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
        f"{_module_id(path)} imports {sorted(outside)} -- the domain takes the standard "
        "library and itself; have an adapter do that part and hand it the result"
    )


#: Which third-party packages each area may import. Deny by default: a package
#: named nowhere below fails wherever it appears, so the table is what has to be
#: edited to take on a dependency, and editing it is where someone asks whether
#: the dependency belongs there.
#:
#: Measured, not declared -- every entry is a package some module imports today.
THIRD_PARTY: dict[str, frozenset[str]] = {
    # The agent runtime. This is the swap boundary: replace deepagents and the
    # rewrite stops at this directory.
    "infrastructure/harness": frozenset({
        "aiosqlite",
        "deepagents",
        "langchain",
        "langchain_anthropic",
        "langchain_core",
        "langchain_openai",
        "langchain_quickjs",
        "langgraph",
    }),
    # The rest of the layer adapts the disk, the OS and the environment, and
    # needs one parser to do it -- plus the Linux shell fence, which is the OS
    # in the most literal sense this table holds: Landlock, applied to a process
    # before it execs. Optional and Linux-only, so both imports of it are inside
    # functions behind an `ImportError` or a platform check, and a macOS install
    # never sees it.
    "infrastructure": frozenset({"sandlock", "yaml"}),
    # The one consumer still in this distribution. `presentation` was the other
    # and is now `kingfisher-service`, a package of its own with its own rules --
    # so fastapi and uvicorn are no longer anything this table has an opinion
    # about, and an area that named them would be permitting what it cannot see.
    #
    # `kingfisher_service` is foreign for exactly that reason, and named here
    # because `kingfisher serve` is the one thing in this distribution allowed to
    # reach for it -- inside a function, behind `except ImportError`, to say how
    # to install it. The rule below keeps the *library* clear of it; this area is
    # not covered by that rule, which is what makes naming it here the decision
    # rather than an oversight.
    # `dotenv` because the command reads `./.env` before anything asks the
    # environment. A driver's business rather than the library's: `config_from_env`
    # takes a mapping and does not care where it came from, which is what keeps
    # this on one side of the line.
    "presentation/cli": frozenset({"kingfisher_service", "dotenv"}),
    # Nothing. The domain has a stricter rule of its own; these two are here so
    # the table is total and an unlisted area cannot mean "anything goes".
    "domain": frozenset(),
    "application": frozenset(),
    # `__init__.py` and `config.py`, which belong to no layer.
    "": frozenset(),
}


def _package_modules() -> list[Path]:
    return sorted(
        p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts
    )


def _area_of(path: Path) -> str:
    """The longest area in `THIRD_PARTY` that contains this module.

    Longest rather than first, so `infrastructure/harness` wins over
    `infrastructure` and a subpackage can be stricter or looser than its parent
    without the order of a dict deciding which.
    """
    parent = path.relative_to(SRC).parent.as_posix()
    parent = "" if parent == "." else parent
    candidates = [a for a in THIRD_PARTY if a in ("", parent) or parent.startswith(f"{a}/")]
    return max(candidates, key=len)


def _undeclared(used: set[str], area: str) -> set[str]:
    """The one place the table is consulted, so there is one place to get wrong.

    Separate from the rule below because the rule can only ever see modules
    that exist. Every one of them passes today, so a rule that quietly stopped
    distinguishing between areas -- allowing anything any area allows -- would
    keep passing too, and would have lost the whole point while looking healthy.
    `test_an_area_is_refused_another_areas_dependencies` asks this the questions
    the tree cannot.
    """
    return used - THIRD_PARTY[area]


def test_an_area_is_refused_another_areas_dependencies():
    """The table has to partition, not merely enumerate.

    Nothing in `src/` can show this. Every module satisfies the rule, so the
    rule passes whether the areas are distinct or the union of them all is
    allowed everywhere -- and the union is the mutation that removes the value
    without removing a single test.
    """
    assert _undeclared({"deepagents"}, "domain") == {"deepagents"}
    assert _undeclared({"deepagents"}, "application") == {"deepagents"}
    assert _undeclared({"deepagents"}, "presentation/cli") == {"deepagents"}
    assert _undeclared({"deepagents"}, "infrastructure") == {"deepagents"}
    assert _undeclared({"fastapi"}, "infrastructure/harness") == {"fastapi"}
    assert _undeclared({"yaml"}, "infrastructure/harness") == {"yaml"}

    assert _undeclared({"deepagents", "langgraph"}, "infrastructure/harness") == set()
    assert _undeclared({"yaml"}, "infrastructure") == set()
    assert _undeclared({"fastapi"}, "presentation/cli") == {"fastapi"}
    assert _undeclared({"kingfisher_service"}, "presentation/cli") == set()
    assert _undeclared({"kingfisher_service"}, "application") == {"kingfisher_service"}


def test_a_subpackage_is_judged_by_its_own_area():
    """`infrastructure/harness/agent.py` is not judged as `infrastructure/`.

    Longest match, so a subpackage can be stricter or looser than its parent
    and the order of a dict does not decide which. Shortest match would let the
    harness's eight packages leak into all thirteen flat modules.

    Every path here exists, and that is not decoration. `_area_of` computes
    from the path string and never touches disk, so an assertion about a file
    that has moved goes on passing while being about nothing -- which is what
    this one did when `catalogue.py` became a package, and the whole suite
    stayed green. The paths are asserted present so the next move fails here
    instead.
    """
    catalogue = SRC / "infrastructure" / "catalogue" / "__init__.py"
    buried = SRC / "infrastructure" / "catalogue" / "skills.py"
    for path in (catalogue, buried, SRC / "domain" / "tool.py", SRC / "config.py"):
        assert path.exists(), f"{path} does not exist, so the assertion below is about nothing"

    assert _area_of(SRC / "infrastructure" / "harness" / "agent.py") == "infrastructure/harness"
    assert _area_of(SRC / "domain" / "tool.py") == "domain"
    assert _area_of(SRC / "config.py") == ""

    # A subpackage with no entry of its own is judged by its parent, which is
    # what lets `catalogue/` inherit `{yaml}` without naming it -- and what
    # would stop being true the moment someone gave it an entry.
    assert _area_of(catalogue) == "infrastructure"
    assert _area_of(buried) == "infrastructure"


@pytest.mark.parametrize("path", _package_modules(), ids=_module_id)
def test_a_module_imports_only_what_its_area_may_depend_on(path):
    """One table, replacing two rules that were allowlists by omission.

    The first checked that no `application/` module imported something in a
    hand-written `FOREIGN` tuple -- written when the guard was about LangChain
    leaking into orchestration, which it genuinely was: `run.py` and `runlog.py`
    each carried their own copy of LangChain's usage-metadata shape, kept in
    sync by nobody. The second checked that *somebody* in `infrastructure/`
    imported something from that tuple, and passed while any one file did.

    Both had the same hole. `FOREIGN` named five packages; six of the agent
    runtime's are actually imported here, and `langchain_quickjs`, `aiosqlite`
    and `langchain_openai` were in none of them. An `application/` module
    importing any of the three passed, as would one importing `yaml` or
    `requests` -- the rule only ever knew the names someone had thought of.

    Turned around, there is nothing to keep up to date. Every area's dependency
    surface is written down, and a package nobody wrote down fails wherever it
    is used. That is the same correction
    `test_domain_imports_only_the_standard_library_and_itself` already made for
    the domain, applied to the four areas that still had a list.
    """
    area = _area_of(path)
    used = {
        m.split(".")[0]
        for m in _imported_modules(path)
        if m.split(".")[0] not in sys.stdlib_module_names and m.split(".")[0] != "kingfisher"
    }
    stray = _undeclared(used, area)
    assert not stray, (
        f"{_module_id(path)} imports {sorted(stray)}; "
        f"{area or 'the package root'} may import "
        f"{sorted(THIRD_PARTY[area]) or 'nothing third-party'} — have an adapter in an "
        "area that may do that, and hand this one the result"
    )


def test_the_harness_package_is_the_one_speaking_to_the_harness():
    """The half of the old rule worth keeping, scoped to where it means something.

    A `harness/` that imports nothing foreign is not a layer that got cleaner.
    It is the coupling having moved somewhere no rule is looking, which is what
    the original existence check was for -- it just asked the question of a
    whole layer, where thirteen of twenty-three modules were never going to
    answer it.
    """
    runtime = THIRD_PARTY["infrastructure/harness"]
    imports = {
        m.split(".")[0]
        for p in _modules_in("infrastructure")
        if "harness" in p.parts
        for m in _imported_modules(p)
    }
    assert imports & runtime, (
        "no module under infrastructure/harness/ imports the agent runtime — "
        "either the adapters left, or the coupling did not"
    )


#: Which flat `infrastructure/` modules may reach into `infrastructure/harness/`,
#: and what each one reaches for. Deny by default, like `THIRD_PARTY`: an edge
#: named nowhere below fails, so this table is what has to be edited to add one,
#: and editing it is where someone asks whether the edge belongs.
#:
#: The split's argument was that the line runs *one way* -- harness modules read
#: the adapters, not the reverse -- with a single exception reasoned about in the
#: design note. That claim decayed without saying so: two more edges arrived
#: nine hours after the note was written, neither of them wrong and neither of
#: them noticed. This is the claim turned into a rule, which is the same move
#: `_modules_in` made when nine rules quietly stopped covering a subpackage.
#:
#: The enforced import rule is scoped to *foreign* packages on purpose (L4a), so
#: nothing here forbids these edges. What it forbids is a fourth one arriving
#: unremarked.
def _harness_consumers() -> list[Path]:
    """Every module the harness table is about, in both layers that reach it.

    One function rather than the same comprehension in three places, and that
    is not tidiness: a mutation narrowing it back to `infrastructure/` survived
    twice. The first time because both rules pass when the table is complete --
    a rule with no cases passes -- and the second because the test written to
    catch that restated the walk instead of calling it, so it tested its own
    copy. `_repository_root` grew a parameter for the same reason.
    """
    return [
        path
        for path in [*_modules_in("infrastructure"), *_modules_in("application")]
        if "harness" not in path.parts
    ]


HARNESS_EDGES: dict[str, frozenset[str]] = {
    # Reads the registry to answer which skill names a deployment already
    # offers. Left a direct import rather than inverted through a port: the port
    # would have exactly one implementation, forever, whose whole purpose is to
    # be deepagents-specific.
    "catalogue": frozenset({"skill_registry"}),
    # Asks the registry what names are taken before accepting an upload, which
    # is the same question `catalogue` asks and the same answer.
    "uploads": frozenset({"skill_registry"}),
    # Builds an agent to enumerate what it registered -- the only way to know
    # the built-in tool set is to assemble one and look.
    "inventory": frozenset({"agent"}),
    # The service is the harness's largest consumer, and was never in this table
    # because the rule only walked `infrastructure/`. Running a turn *is* driving
    # the harness: an agent to run, a checkpointer to resume it, a run log to
    # record it, and the runtime that turns its stream into events.
    "service": frozenset({"agent", "checkpointing", "runlog", "runtime"}),
}


#: The package the edges below cross into.
HARNESS = "kingfisher.infrastructure.harness"


def _consumer_key(path: Path) -> str:
    """A module's name below its layer, which is how `HARNESS_EDGES` is keyed.

    Was `path.stem`, and a package broke it: `catalogue/__init__.py` has the
    stem `__init__`, so the entry whose reason is written beside it stopped
    matching and the edge read as an unnamed escape. Keyed this way the four
    existing entries are unchanged -- a move does not get to rewrite the
    reasons -- and two modules sharing a stem in different layers no longer
    share one key.
    """
    parts = list(path.relative_to(SRC).parts[1:])
    parts = parts[:-1] if parts[-1] == "__init__.py" else [*parts[:-1], parts[-1][:-3]]
    return ".".join(parts)


def _harness_reach(path: Path) -> set[str]:
    """Which harness modules one flat module imports, by their bare names.

    Its own walk rather than `_imported_modules`, which keeps only the *module*
    of an `ImportFrom` and drops the names -- so `from <pkg>.harness import
    agent` arrives as the bare package and the module reached is lost. That is
    the form nearly every one of these edges is written in, and a first draft of
    this rule read it through the shared helper and detected none of them.
    Mutation testing is the only reason that is a sentence in a docstring rather
    than a rule in the file doing nothing.

    It resolves relative imports for the same reason `_imported_modules` does,
    and it is worth saying that the two had the *same* defect independently: a
    rule whose answer depends on how someone spelled an import is not a rule.
    Written `from .harness import agent`, every edge here was invisible and the
    table of who may reach the harness enforced nothing.
    """
    reached: set[str] = set()
    package = _package_of(path)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            # Resolved first, for the reason `_imported_modules` is: written
            # relatively, `node.module` is a fragment that matches neither
            # branch below, and the edge is simply lost. Same rule, same file,
            # different spelling.
            base = package[: max(len(package) - (node.level - 1), 0)] if node.level else ()
            module = ".".join((*base, node.module)) if node.module else ".".join(base)
            if module == HARNESS:                           # from <pkg>.harness import agent
                reached.update(alias.name for alias in node.names)
            elif module.startswith(HARNESS + "."):          # from <pkg>.harness.agent import x
                reached.add(module[len(HARNESS) + 1 :].split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:                        # import <pkg>.harness.agent
                if alias.name.startswith(HARNESS + "."):
                    reached.add(alias.name[len(HARNESS) + 1 :].split(".")[0])
    return reached


def test_only_the_named_adapters_reach_into_the_harness():
    """The line runs one way, apart from the edges written down above.

    `harness/` earns its folder by carrying a rule: replace deepagents and
    exactly those ten files are rewritten. An adapter that imports one of them
    has taken a share of that rewrite, which is a thing to decide rather than to
    discover -- and three modules have decided it so far.

    Names, not counts. A rule that said "at most three" would pass while an edge
    moved from one module to another, and the question is always *which*
    adapter took the coupling on.
    """
    escaped: list[str] = []
    # Both layers, and `application` was missing until `inventory` moved there
    # and took its edge out of sight. The hole was older than that move:
    # `application/service.py` has reached into four harness modules the whole
    # time, unnamed, because this walked one directory.
    for path in _harness_consumers():
        allowed = HARNESS_EDGES.get(_consumer_key(path), frozenset())
        if extra := _harness_reach(path) - allowed:
            escaped.append(f"{_module_id(path)} -> harness.{{{', '.join(sorted(extra))}}}")

    assert not escaped, (
        f"{escaped} reach into infrastructure/harness/ without being named in "
        "HARNESS_EDGES; add the entry and the reason, or route the call through "
        "an adapter that already has one"
    )


def test_the_harness_rule_looks_at_both_layers():
    """A rule with no cases passes, and both halves above have none by design:
    every edge is named, so narrowing the walk back to `infrastructure/` alone
    changes no result. A mutation proved it -- the walk could stop seeing
    `application/` entirely and the suite stayed green.

    So the coverage is asserted rather than the outcome. `application/service.py`
    is the largest consumer of the harness in the repository and went unwatched
    for as long as this rule walked one directory.
    """
    walked = {_consumer_key(path) for path in _harness_consumers()}

    assert "service" in walked, "the rule stopped reading application/"
    assert "inventory" in walked
    assert _harness_reach(SRC / "application" / "service.py"), (
        "service.py reaches into the harness, so it is a real case rather than "
        "a name in a set"
    )


def test_every_named_harness_edge_is_a_real_one():
    """The other half, so the table cannot outlive what it describes.

    An allowlist nobody prunes is a list of permissions granted for reasons that
    stopped applying -- and the next reader takes it as evidence the coupling is
    load-bearing. `THIRD_PARTY` carries the same promise one comment up:
    "measured, not declared".
    """
    actual = {_consumer_key(path): _harness_reach(path) for path in _harness_consumers()}
    stale = [
        f"{module} -> harness.{{{', '.join(sorted(named - actual.get(module, set())))}}}"
        for module, named in HARNESS_EDGES.items()
        if named - actual.get(module, set())
    ]

    assert not stale, (
        f"HARNESS_EDGES names {stale}, which nothing imports any more; delete the "
        "entry so the table keeps describing the code"
    )


def test_infrastructure_does_not_reach_back_into_application():
    """The outward half of the rule, which went unenforced for a while.

    Dependencies point inward: application -> infrastructure -> domain, never
    back. The inward half is
    `test_domain_imports_only_the_standard_library_and_itself`.

    `Config` lived in the application layer and every adapter imported it,
    inverting the direction this module claims to hold. It sits at the package
    root now, belonging to no layer, and this is what stops it drifting back
    up. `application/config.py` reads `infrastructure.harness.models` for the
    credential variable names, which is the legal direction.
    """
    for path in _modules_in("infrastructure"):
        modules = _imported_modules(path)
        assert not any(m.startswith("kingfisher.application") for m in modules), (
            f"{_module_id(path)} depends on application/ — "
            "move the shared shape into domain/"
        )


def test_the_public_api_list_matches_the_lazy_export_table():
    """`__all__` is a literal so a linter can see it, and `_EXPORTS` drives the
    lazy loading. Nothing keeps them in step but this.

    The same *names*, not the same order. It read `== sorted(_EXPORTS)`, which
    also asserted a plain sort -- and that quietly disagreed with `RUF022`, which
    orders SCREAMING_CASE first. The two agreed until `SKILL_LAYOUT` arrived and
    broke the tie, at which point the linter's fix failed the test and the
    test's order failed the linter. Ordering is the linter's job; membership is
    this one's.
    """
    import kingfisher

    assert sorted(kingfisher.__all__) == sorted(kingfisher._EXPORTS)


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


#: Exports that must stay reachable without loading a provider SDK. Measured,
#: not guessed: each is 6-9ms and ~90 modules, against 817-1157ms and ~3100 for
#: the heavy ones.
LIGHT_EXPORTS = frozenset({
    "Capabilities", "Config", "ConfigError", "Request", "RunEvent", "RunOn",
    "RunResult", "SessionInfo",
    # The errors a caller must tell apart, and the saver `astream` refuses to
    # run without. Public so a consumer outside the package can catch them by
    # name and open one -- the server being the first such consumer.
    "CapabilityError", "QuotaExceededError", "SessionBusyError", "SkillError",
    "SubagentError", "UnknownSessionError", "UploadError", "UnsafeReferenceError",
    "UnknownReferenceError", "LocalFileStore", "async_checkpointer",
    "build_checkpointer", "build_model", "ensure_layout", "config_from_env",
    "normalize_answer", "protect_data", "system_prompt", "writable_data",
    # Asking the host what it can fence with, either way round.
    "bubblewrap_available",
    # Asking the kernel what it can fence with. `ctypes` and a syscall, no
    # dependency at all -- and it has to stay that way, because it runs on
    # hosts where the fence is not installed to say whether installing one
    # would help.
    "landlock_abi",
    # The directory half of a configuration, and the record it returns. Light
    # because seeding a fresh workspace runs on them before anything is loaded
    # -- paying for three provider SDKs to find out where `skills/` goes would
    # be the wrong shape entirely.
    "paths_from_env", "WorkspacePaths",
    # Seeding, and asking what a workspace offers. Measured at 21-50ms and
    # 148-192 modules with no SDK loaded -- heavier than `system_prompt` at 90,
    # because `yaml` and `importlib.metadata` come with them, and nowhere near
    # the 3,100 a provider costs.
    #
    # `inventory` is light to *reach*, not to call: answering builds an agent,
    # so `harness.agent` is imported inside the function. That is the shape
    # this classification is about -- what a name costs to touch.
    "seed", "definitions_source", "kinds_at", "Seeded", "inventory", "Inventory",
    # A directory of sessions, and the port it satisfies. Neither imports
    # anything a deployment does not already have -- see `session_store`.
    "LocalSessionStore",
    # Reads `/proc/mounts` and two cgroup files. Nothing imported, and
    # all-`None` off Linux rather than an error.
    "memory_backing",
    # A renderer and a sentence. Both are what a consumer needed and neither
    # imports anything -- the cheapest names on this list.
    "offered", "SKILL_LAYOUT", "DEFINITION_KINDS", "SEED_HINT", "split_reference",
    # The access policy, its report, its error and the sentinel for running
    # without a caller. `domain.access` imports `domain.fields` and
    # `domain.capabilities` and nothing else, which is the point of it being a
    # domain module: deciding who reaches what must not cost a provider SDK,
    # because a deployment resolves a grant on every turn.
    "Groups", "AccessError", "AccessReport", "UNSCOPED", "Held", "AUDIENCED",
    "Audience", "Stated",
    # The `"*"` sentinel itself, published because the command prints an
    # audience and has to tell "everyone" from a list of names. Reaching into
    # `domain.capabilities` for it is what the consumer rule forbids.
    "ALL",
    # Reaching it costs nothing; calling it may write a sandbox profile,
    # which is the same light-to-reach / heavy-to-call split `inventory` has.
    "shell_confinement", "Confinement",
})

#: The rest, which genuinely need deepagents to do their job.
HEAVY_EXPORTS = frozenset({
    # Heavy to reach as well as to call: it lives beside `indistinct_delegates`
    # in the harness, which imports deepagents at module scope. 868ms and 3,137
    # modules, measured -- which is why `doctor` imports it inside the check
    # rather than at the top of `health`, where every other verb would pay it.
    "unrunnable_delegates",
    "Kingfisher", "build_agent", "build_backend", "run", "shell_env", "stream",
})

PROVIDER_SDKS = ("deepagents", "langchain", "langchain_openai", "langchain_anthropic")


def test_every_export_is_classified_light_or_heavy():
    """So a new export cannot slip past the rule below by not being listed."""
    import kingfisher

    assert set(kingfisher._EXPORTS) == LIGHT_EXPORTS | HEAVY_EXPORTS, (
        "a new export must be added to LIGHT_EXPORTS or HEAVY_EXPORTS — if it "
        "needs deepagents it is heavy, otherwise keep it light and say so here"
    )


def test_a_light_export_stays_light():
    """Touching a light name must not load a provider SDK.

    The test above it only covers bare `import kingfisher`, which is a weaker
    promise than the one `_EXPORTS` makes -- and weak in the place that bit.
    `system_prompt` needs nothing but `Config` and the standard library, yet
    reaching it cost **764ms and 3,107 modules**, because it shared a file with
    `create_deep_agent` and Python cannot import one name from a module without
    executing all of it. Splitting `prompting` out took it to 7ms and 90.

    Nothing about that is self-sustaining: one `from deepagents import ...`
    added to `prompting` or `models` brings the whole cost back, everywhere,
    silently. This is what notices.

    One subprocess for all of them -- they are light, so it costs about 100ms.
    """
    import subprocess
    import sys

    probe = (
        "import sys, kingfisher\n"
        f"for name in {sorted(LIGHT_EXPORTS)!r}:\n"
        "    getattr(kingfisher, name)\n"
        f"print(','.join(m for m in {PROVIDER_SDKS!r} if m in sys.modules))"
    )
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == "", (
        f"a light export pulled in {out.stdout.strip()} — find which name did it "
        "and either move its module off the foreign import, or reclassify it heavy"
    )


def test_the_package_does_not_depend_on_the_eval_harness():
    """`evals/` is test material and lives outside `src/`, so it is not in the
    wheel. If the package imports it, an installed kingfisher breaks -- and the
    348-line fixture module has quietly moved back in.
    """
    for layer in ("domain", "infrastructure", "application"):
        for path in _modules_in(layer):
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


@pytest.mark.parametrize("path", _modules_in("domain"), ids=_module_id)
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
    assert not contact, f"{_module_id(path)} reaches the world: {contact}"


#: Calls that *change* the filesystem, as opposed to reading it or the
#: environment. Deliberately narrower than `WORLD_CALLS`: `open` and `replace`
#: are left out because `Session.open` and `dataclasses.replace` are named the
#: same and this rule runs over a layer where both are legitimate.
MUTATING_CALLS = frozenset({
    "mkdir", "rmdir", "rmtree", "copytree", "copyfile", "copy", "move",
    "write_text", "write_bytes", "unlink", "touch", "chmod", "rename",
})


def test_the_application_layer_does_not_write_to_disk_itself():
    """Orchestration decides what happens; an adapter is what makes it happen.

    This was not true when it was written. `service.py` copied a request's
    input files itself -- a `mkdir` and a bare `shutil.copy`, the one place in
    this layer doing its own I/O -- while the same files bound for `/data` went
    through `place_data`, which refuses a duplicate basename or a missing file
    before copying anything.

    So the two sets of caller-supplied files had different guarantees, and the
    difference was invisible: measured against the real service, two inputs
    sharing a basename were accepted and one silently lost, and a missing one
    left the earlier files behind in the turn. Both now go through `_checked`.

    Reading is not the target. `application/config.py` reads the environment,
    which is its job.
    """
    offenders = []
    for path in _modules_in("application"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "shutil" for a in node.names):
                offenders.append(f"{_module_id(path)}:{node.lineno} imports shutil")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in MUTATING_CALLS
            ):
                offenders.append(f"{_module_id(path)}:{node.lineno} .{node.func.attr}()")

    assert not offenders, (
        f"{offenders} write to disk from the application layer — put it in "
        "infrastructure/workspace_fs.py, where the guards already are"
    )


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

    `domain.skill` owns the filename and `infrastructure.catalogue.skills` owns the
    listing. Asserting they *agree* with a caller is tautological once the
    caller imports them; what is worth asserting is that nothing else decides.
    """
    repo = REPO
    owners = {
        SRC / "domain" / "skill.py",
        SRC / "infrastructure" / "catalogue" / "skills.py",
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
        f"{offenders} decide what a skill is; use domain.skill.FILENAME and "
        "LocalSkillRepository.names so the inventory and the validator cannot disagree"
    )


def test_no_definitions_live_inside_the_package():
    """The package ships code. Definitions are content and ship nowhere.

    This assertion has now been three things, and the first two were each true
    for about a month. It began as "the framework supplies none", while they
    were a distribution of their own behind a `kingfisher.assets` entry point.
    Then D1 of *the definitions ship with the library* reversed it, and it
    became "they live under `assets/` and nowhere else" -- at which point the
    package carried content and this file needed a `CONTENT` exclusion to keep
    every other rule off it.

    Now they are `examples/`, outside the wheel, and the rule can be the simple
    one it never could be before: **no definition kind exists under `src/`**.
    Easier to state, impossible to satisfy by accident, and it needs no
    exclusion anywhere -- the separation stopped being a rule and became the
    layout, which is the best argument the move had.

    It is also what stops the move regressing. A `skills/` reappearing under
    `src/` would be content read as code: shipped, and skipped by nothing here
    because there is no longer anything to skip.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    stray = sorted(
        str(path.relative_to(SRC))
        for kind in DEFINITION_KINDS
        for path in SRC.rglob(kind)
        if path.is_dir() and "__pycache__" not in path.parts
    )

    assert not stray, (
        f"{stray} hold definitions inside the package — they ship in the wheel "
        f"and are read by no rule in this file, which is what made the old "
        f"`CONTENT` exclusion necessary. Definitions belong in examples/."
    )


def test_this_repository_still_has_a_worked_set(shipped):
    """The other half, and it fails separately.

    Nothing ships definitions, and that is the point -- but a repository that
    kept none would be teaching the formats with nothing to read. The four
    hundred lines of tests in `test_shipped_assets` are about the files this
    names; without it they would pass by having no subject.

    Named per kind rather than counted. `assert shipped.is_dir()` passed with
    two of the four gone, and seeding a workspace without agents is not a
    smaller version of seeding one -- a request must name an agent, so a set
    without that kind produces something that cannot run.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    missing = sorted(kind for kind in DEFINITION_KINDS if not (shipped / kind).is_dir())

    assert not missing, (
        f"examples/ holds no {', '.join(missing)} — this is the set the format "
        "tests read and the one a reader learns from"
    )


def test_the_catalogue_holds_one_module_per_kind():
    """The third place the three kinds are written down, bound to the first.

    `DEFINITION_KINDS` is derived from `Definitions`' fields, so those two
    cannot drift. The module names are the copy with no type and no constant
    behind it: `skills.py`, `subagents.py` and `tools.py` say "there are three
    kinds and these are they" as loudly as either, and nothing was holding them
    to it. A fourth kind added to `Definitions` with no module to read it, or a
    module renamed out from under the constant, would both have passed.

    The folder is what makes this checkable at all. Flat among thirteen other
    modules, "one module per kind" was not a shape anything could ask about.

    Subset rather than equality: `layered`, `documents` and `importing` are in
    this package for good reasons and are not kinds.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    package = SRC / "infrastructure" / "catalogue"
    modules = {p.stem for p in package.glob("*.py")}

    assert modules, f"{package} holds no modules — this rule is about nothing"
    assert set(DEFINITION_KINDS) <= modules, (
        f"{sorted(set(DEFINITION_KINDS) - modules)} is a kind the catalogue reads "
        "with no module in catalogue/ named for it"
    )


def test_the_package_ships_the_catalogue_example():
    """The one file that is not an asset and has to stay.

    `models.yaml` is required and has no fallback, so the worked example is the
    one document a new deployment cannot start without reading, and the error it
    hits without one names this file as the place to look. It must arrive with
    the framework rather than with a pack somebody may not have installed --
    which is the whole reason the test above can assert what it does.

    It lived at the repo root once -- outside `packages = ["src/kingfisher"]` --
    which meant a pip-installed kingfisher shipped a required format with no
    example of it, and nothing noticed. Both paths are asserted because they
    fail separately: the first catches it moving back out of the package, the
    second catches it not being reachable the way an install reaches it.
    """
    from importlib import resources

    from kingfisher.infrastructure import workspace_fs

    assert (SRC / workspace_fs.EXAMPLE).is_file()
    installed = resources.files(workspace_fs.PACKAGE).joinpath(workspace_fs.EXAMPLE)
    assert installed.is_file()


# -- who caused it ---------------------------------------------------------
#
# A consumer that cannot name an error can only catch `ValueError`. Ten of the
# eleven error types here are one, and so is `Request`'s empty-task check, and
# so is whatever a dependency raises -- so that net turns a bug into a refusal
# and a refusal into a 500. Naming them is what makes the difference reportable.

#: Errors a caller can cause and must be able to tell apart. Public.
CALLER_FACING_ERRORS = frozenset({
    "CapabilityError", "QuotaExceededError", "SessionBusyError", "SkillError",
    "SubagentError", "UnknownReferenceError", "UnknownSessionError",
    "UnsafeReferenceError", "UploadError",
})

#: The rest, which say the deployment is wrong rather than the caller.
#: `HostPathError` is the backend refusing a host path the *agent* produced
#: mid-turn, so it is not a request-time fault at all. Being here does not mean
#: private -- `ConfigError` was public long before this rule existed -- it
#: means a consumer is not expected to branch on it.
DEPLOYMENT_ERRORS = frozenset({
    # `MissingStoreError` is here rather than above on purpose: a request naming
    # files by id with no `FileStore` wired is a deployment that forgot one, and
    # nothing the caller sends can fix it.
    #
    # `AgentError` sits here and `SubagentError` sits above, which looks like an
    # inconsistency and is the rule working: a caller may *upload* a subagent, so
    # a malformed one is their text and their fault. Agents come from the
    # catalogue only, so a malformed one is always the deployment's own file.
    #
    # `AccessError` is here for a reason worth stating, because "access" sounds
    # caller-facing and is not. Every way of raising it is the *integrator*
    # being wrong: a policy file that will not parse, a call that did not say
    # who was calling, a group name outside the closed vocabulary. A caller who
    # is merely denied something never sees it -- an asset out of their reach
    # reads as one the workspace does not offer, so what reaches them is the
    # `CapabilityError` that any absent name produces. If this ever becomes
    # something a caller can provoke, it has moved above.
    "AccessError", "AgentError", "ConfigError", "DataError", "HostPathError",
    "LoadError", "MissingStoreError", "ToolError",
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
    """So a new error cannot arrive unclassified and be a 500 by default.

    The same shape as the light/heavy split above, and it exists for the same
    reason: a list nothing checks is a list that drifts.
    """
    assert _error_classes() == CALLER_FACING_ERRORS | DEPLOYMENT_ERRORS, (
        "a new error type must be added to CALLER_FACING_ERRORS or "
        "DEPLOYMENT_ERRORS -- caller-facing ones must also be exported"
    )


def test_every_caller_facing_error_is_public():
    """The rule the split is for. Reaching into `kingfisher.domain.session` to
    catch `SessionBusyError` is what a consumer does when the package will not
    say the name out loud."""
    import kingfisher

    assert set(kingfisher.__all__) >= CALLER_FACING_ERRORS


def test_a_caller_facing_error_is_the_same_class_either_way():
    """The lazy export table resolves to the class itself, not a copy -- so a
    consumer catching `kingfisher.SessionBusyError` catches what the domain
    raises."""
    import kingfisher
    from kingfisher.domain.session import SessionBusyError

    assert kingfisher.SessionBusyError is SessionBusyError


# -- the server is a consumer, not an insider ------------------------------
#
# `kingfisher-service` is its own distribution now, so half of this is enforced
# by packaging: the library cannot import a package that is not installed. The
# other half is not, and stays here -- an *installed* service is importable, and
# nothing but this rule stops a library module reaching for it. The point is not
# tidiness:
# it puts the server on the same footing as anybody outside the package, so when
# it needs something the library does not export, the answer is to export it
# deliberately. Three things came out that way before the server existed -- the
# caller-facing errors, `async_checkpointer`, and a way to send a file.


#: Every consumer held to the front door, and the directory each one lives in.
#: The claim that any caller can drive this library is worth something only if
#: the callers that ship with it are held to it -- which is why `offered` and
#: `SKILL_LAYOUT` are public.
#:
#: The paths are here because one of them is not under `src/`. `presentation`
#: used to be, and became `kingfisher-service`, a wheel of its own -- at which
#: point this collector, which read `SRC / name`, stopped finding it. The rule
#: kept its name, kept passing, and covered the CLI alone. A comment here said
#: the service "holds itself to the same rule in its own tests"; it does not,
#: and there is no architecture test under `service/tests/` at all.
#:
#: Kept in this suite rather than moved there, deliberately: the rule is about
#: *this* package's public API, and a base that can break its own contract
#: without its own tests noticing is the arrangement that produced the gap.
CONSUMERS: dict[str, Path] = {
    "cli": SRC / "presentation" / "cli",
    "kingfisher_service": REPO / "service" / "src" / "kingfisher_service",
}


def _consumer_modules() -> list[Path]:
    return sorted(path for root in CONSUMERS.values() for path in root.rglob("*.py"))


def _reaches_past_the_public_api(module: str) -> bool:
    """True when a consumer imports something deeper than `kingfisher` itself.

    `kingfisher_service.*` is not a reach and never trips this -- it is a
    different top-level package. `kingfisher.presentation.cli.*` is not one either: the CLI
    ships *inside* the library, so its own modules are its own business.
    """
    if module.split(".", maxsplit=1)[0] != "kingfisher" or module == "kingfisher":
        return False
    return not module.startswith("kingfisher.presentation.cli")


@pytest.mark.parametrize(
    ("module", "reaches"),
    [
        ("kingfisher", False),                      # the front door itself
        ("kingfisher.domain.request", True),        # past it
        ("kingfisher.application.service", True),   # past it, and the tempting one
        ("kingfisher.presentation.cli.health", False),           # the CLI ships inside the package
        ("kingfisher_service.app", False),          # a different top-level package
        ("fastapi", False),                         # not ours to have an opinion on
    ],
)
def test_the_reach_predicate_says_what_it_means(module, reaches):
    """The rule's own arithmetic, checked against named inputs.

    Nothing in this repository violates the rule today, which means weakening
    the predicate -- `return False` -- passes every module it is pointed at. A
    rule that can be switched off in silence is decoration, and this file has
    twice shipped one: a collector reading the wrong root, and an import scan
    that discarded the names it needed.

    `kingfisher_service.app` is the case worth naming. It looks like a reach and
    is not: string-prefix matching on "kingfisher" would call it one, and the
    consumer most subject to this rule would fail it for importing itself.
    """
    assert _reaches_past_the_public_api(module) is reaches


def test_the_rule_above_still_finds_every_consumer():
    """The guard the parametrised rule cannot give itself.

    A rule parametrised over an empty list passes. That is how the service
    slipped out: it moved to a distribution of its own, the collector kept
    reading `SRC / name`, and a test named for the server ran against four CLI
    files and reported success. `_modules_in` learned to recurse for the same
    reason one level down; recursion does not help when the root is wrong.

    Named roots, not a count, so this says *which* consumer went missing.
    """
    found = {name for name, root in CONSUMERS.items() for _ in root.rglob("*.py")}

    assert found == set(CONSUMERS), (
        f"no modules found for {sorted(set(CONSUMERS) - found)} -- the consumer moved "
        "and the rule below is now silently about whatever is left"
    )


@pytest.mark.parametrize("path", _consumer_modules(), ids=_module_id)
def test_a_consumer_uses_the_library_only_through_its_public_api(path):
    """`from kingfisher import X`, never `from kingfisher.domain.y import X`.

    A consumer that reaches into `kingfisher.application.service` for something
    unexported has quietly made a private name load-bearing -- and the next
    person to move it breaks an HTTP contract, or a command, without touching
    anything that looks like one.
    """
    reaching = {m for m in _imported_modules(path) if _reaches_past_the_public_api(m)}
    assert not reaching, (
        f"{_module_id(path)} imports {sorted(reaching)} — a consumer takes `kingfisher` "
        "and nothing deeper; if it needs something private, export it on purpose"
    )


@pytest.mark.parametrize(
    "path",
    [p for layer in ("domain", "application", "infrastructure") for p in _modules_in(layer)]
    + [SRC / "__init__.py", SRC / "config.py"],
    ids=_module_id,
)
def test_no_part_of_the_library_imports_the_server(path):
    """The outward half, and the half packaging leaves open.

    A base install cannot import the service because it is not there -- that
    much is free. But an install with the service *present* can, and then
    `pip install kingfisher` alone breaks for everyone else, at import, with a
    module-not-found nobody can act on. This is the only thing standing between
    those two states.
    """
    modules = _imported_modules(path)
    assert not any(m.startswith("kingfisher_service") for m in modules), (
        f"{_module_id(path)} imports kingfisher_service — the library ships "
        "without it and does not know it exists"
    )


#: The synchronous pair. On an event loop these do not merely block one
#: request, they block every other turn sharing the process.
BLOCKING_METHODS = frozenset({"run", "stream"})

#: Receivers whose `run` is not `Kingfisher.run`. Named one by one rather than
#: loosening the rule, because the rule is worth exactly as much as the list is
#: short: `uvicorn.run` is how the server is served, and it is not the
#: loop-blocking mistake this watches for.
NOT_KINGFISHER = frozenset({"uvicorn"})


@pytest.mark.parametrize("path", _consumer_modules(), ids=_module_id)
def test_the_server_calls_the_async_turn_methods(path):
    """`arun` and `astream`, never `run` and `stream`.

    A one-line check for the mistake that turns a concurrent server into a
    serial one, caught where it is written rather than under load. `astream`
    exists for exactly this: four turns measured at 0.4-1.2 turns of wall clock
    instead of four.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = sorted({
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in BLOCKING_METHODS
        and not (
            isinstance(node.func.value, ast.Name) and node.func.value.id in NOT_KINGFISHER
        )
    })
    assert not offenders, (
        f"{_module_id(path)} calls {offenders} — use arun/astream; the sync pair "
        "blocks every other turn on this loop, not just this one"
    )


def test_the_event_kinds_are_what_the_package_emits():
    """`KINDS` is the closest thing to a wire contract here, and as prose it had
    drifted both ways -- naming `swept` and `sweep_failed`, which have not fired
    since retention moved off the request path, and omitting `cut_short`, which
    is how a caller learns its answer is incomplete.

    The server publishes these as SSE event names, so a wrong entry is a kind no
    client will ever see and a missing one is a kind nobody knows to handle.
    """
    from kingfisher.domain.result import KINDS

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
        "KINDS and the kinds actually constructed have diverged — it is published "
        "as the SSE event names, so an extra entry is a kind no client sees and a "
        "missing one is a kind nobody handles"
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
    """Branches for a kind no run can emit.

    One direction only. Kinds with no branch are fine and expected -- six of
    them fall through to the default line on purpose -- so this is a difference
    rather than a comparison.
    """
    return branched - set(kinds)


def test_no_branch_is_written_for_a_kind_that_cannot_exist():
    """The other direction of the rule above, and the one that went unwatched.

    That one compares `KINDS` against the kinds *constructed*, so it notices a
    kind nobody emits and a kind nobody declared. It says nothing about the code
    that *reads* a kind -- and `RunEvent.__str__` carried a branch for `swept`
    long after retention moved off the request path, rendering a line no run
    could produce. Two comments in the package already said `swept` had stopped
    firing, which is the tell: it was known, written down, and still there.

    A dead branch is quieter than a dead function. It has a caller, it type
    checks, and coverage over a suite that never constructs the kind looks the
    same as coverage over one that does. Comparing the two lists is the only
    thing that sees it.
    """
    from kingfisher.domain.result import KINDS

    branched = _kinds_branched_on((SRC / "domain" / "result.py").read_text(encoding="utf-8"))

    # A reader pointed at the wrong file finds nothing and passes, which is the
    # `_modules_in` failure again: a rule that has quietly stopped being about
    # anything reports success.
    assert branched, "found no kind branches at all — is this still the right file?"
    assert not _unreachable(branched, KINDS), (
        f"result.py branches on {sorted(_unreachable(branched, KINDS))}, which no run "
        "can emit — the branch is unreachable, so delete it or add the kind to KINDS"
    )


def test_the_unreachable_check_can_tell_a_live_branch_from_a_dead_one():
    """Every branch in the tree is live, so the rule above passes whether it
    subtracts anything or nothing. These are the two answers `src/` cannot give.
    """
    assert _unreachable({"swept"}, ("token", "finished")) == {"swept"}
    assert _unreachable({"token"}, ("token", "finished")) == set()
    # The direction that must *not* fire: a kind nobody branches on is the
    # ordinary case, not a fault.
    assert _unreachable(set(), ("token", "finished")) == set()


def test_the_branch_reader_reads_branches():
    """Pinned against source written for the purpose, because the real file is
    expected to be clean and a reader that found nothing would look identical.
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

#: Where a caller may live. Tests deliberately do not count -- a test is what
#: kept every instance of this alive.
#:
#: `tests/integration/driver.py` is in this list while living under `tests/`,
#: which reads like a contradiction and is not. The rule is about *calls*: the
#: driver calls `seed` in order to seed a workspace, where a test constructs a
#: call in order to observe one. That difference is the whole subject here, and
#: it does not depend on which directory the caller sits in.
#:
#: Named rather than derived, because getting this wrong is silent in the
#: direction that matters. It was `main.py` at the repository root; when the
#: library moved under `packages/` and this walk lost it, three live helpers were
#: reported as defined for tests alone. Moving the driver into `tests/` did it
#: again, to the same three.
PRODUCTION = ("src/kingfisher", "tests/integration/driver.py", "evals")

#: Names dispatched by something other than a call in this repository. Each is a
#: framework contract rather than a convenience nobody got round to using, and
#: each is listed by name so adding one stays a decision.
DISPATCHED_ELSEWHERE = frozenset({
    # fastapi calls a route handler through its decorator.
    "open_session", "read_session", "close_session", "run_turn", "run_one_shot",
    # langchain's callback protocol and deepagents' middleware hooks.
    "on_llm_end", "on_llm_error", "on_tool_start", "on_tool_end", "on_tool_error",
    "awrap_model_call", "awrap_tool_call", "wrap_model_call", "wrap_tool_call",
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
    """Every name one module *reads*, ignoring prose and ignoring what it binds.

    Prose matters here: `withheld`'s docstring named `Capabilities.unknown`, so
    a guard counting text would have taken that mention for a caller and left
    the dead method exactly where it was.

    Binding matters for the constant rule below. A `def` or a `class` carries its
    own name as a string on the node, so a definition is never an `ast.Name` and
    every `Name` in the tree -- including one in the defining file -- is a real
    reference. `KINDS = (...)` is not built that way: the target is a `Name` like
    any other, and counting it would mean every constant in the package
    referenced itself and the constant rule found nothing, ever. So a load counts
    and a store does not, which is the truer reading for functions too: `foo = 1`
    was never a use of `foo`. Narrowing it changes no answer today -- both
    readings leave `test_nothing_is_defined_for_tests_alone` with zero orphans,
    and no name defined in the package is sighted in production by a store alone.
    Measured before the narrowing went in, because a rule that only ever agreed
    with itself is not evidence.
    """
    seen: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.alias):
            seen.add(node.asname or node.name.split(".")[-1])
    return seen


def _referenced_in_code() -> set[str]:
    """Every name production code reads, across every file production means."""
    seen: set[str] = set()
    for path in _production_files():
        seen |= _names_read(path.read_text(encoding="utf-8"))
    return seen


def _defined_in_package(public: frozenset[str]) -> dict[str, Path]:
    """Module-level functions and classes, plus methods of classes that stand alone.

    Two kinds are skipped, for two different reasons. Methods of a *subclass*,
    because overriding something is a contract with whatever declared it and
    "nobody here calls it" is the ordinary state of a hook. Methods of an
    *exported* class, because exporting `Kingfisher` exports `arun` and `reap`
    with it -- their callers are outside this repository by design, which is
    what publishing them meant.
    """
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if not node.decorator_list and not node.name.startswith("__"):
                    found[node.name] = path
            elif isinstance(node, ast.ClassDef):
                found[node.name] = path
                if node.bases or node.name in public:
                    continue
                for inner in node.body:
                    if not isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef):
                        continue
                    if inner.name.startswith("_") or inner.decorator_list:
                        continue
                    found[inner.name] = path
    return found


def test_nothing_is_defined_for_tests_alone():
    """A function with no caller outside tests is a fourth copy waiting to be
    written by somebody who could not find the third."""
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
# commonest one. A constant was invisible to it, and `domain.capabilities.AXES`
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
    # The SSE event names. Nothing in `src/`, the driver, `evals/` or
    # `service/src/` reads it -- `payloads.frame` puts `event.kind` on the wire
    # straight from the event and only *mentions* `KINDS` in prose -- so its
    # readers are the clients subscribing to those event names, and they are not
    # in this repository to be counted.
    #
    # Which is why `AXES`' fix does not transfer. That one was deleted and
    # re-derived in the tests that wanted it, because deriving it from
    # `fields(Capabilities)` is one line and cannot drift from the type it asks.
    # `KINDS` has nothing to derive from: it is the declaration, and the two
    # rules above are what pin it -- one against the kinds the package
    # constructs, one against the kinds it branches on. Deriving it in a test
    # would leave both comparing a list against itself, which is the tautology
    # the `AXES` commit deleted two tests for.
    "KINDS",
})


def _constants_defined(source: str) -> list[tuple[str, str]]:
    """Every SCREAMING_CASE name a module binds at its top level, with its value.

    Top level by reading `body` rather than walking the tree: a name bound inside
    a class or a function is that scope's business, and `Capabilities`' fields
    are not module constants. Nothing in the package hides one inside a
    module-level `if` either -- of the twenty-one, twenty are `if TYPE_CHECKING`
    and one is `if __name__ == "__main__"`, and not one of them binds a name.
    Counted before this leaned on it, since the shortcut is only safe if it is
    true of the tree rather than of the tree somebody imagined.

    Case is the whole test for "constant", which is not a new opinion: it is what
    `test_no_value_is_written_down_twice` was already using, and the two rules
    share this so they cannot come to disagree about what a constant is. It also
    settles `__version__` without an exemption, since `"__version__".isupper()`
    is false -- which is the right answer rather than a lucky one. A package
    version has no reader in its own package and never will, and a rule that
    demanded one would be teaching people to write the exemption list.
    """
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
    """Every module-level constant in the package, at the first file defining it.

    By name, like the rule above, and with the same blind spot: two files may
    each define `EXPORT` with different values, and this reports one path for
    both. `test_no_value_is_written_down_twice` is the rule that has an opinion
    about a name in two places; this one only asks whether anything reads it.
    """
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        for name, _ in _constants_defined(path.read_text(encoding="utf-8")):
            found.setdefault(name, path)
    return found


def _unread(found: dict[str, Path], read: set[str], public: frozenset[str]) -> dict[str, Path]:
    """Of the constants defined, the ones nothing outside a test reads.

    A separate function rather than a comprehension inside the rule, and the
    reason is the one `_reaches_past_the_public_api` gives: nothing in the tree
    is unread today, so the rule passes whether this subtracts anything or
    nothing, and `if False and ...` slipped into the comprehension would go
    green. A rule that can be switched off in silence is decoration. Here the
    three ways out -- read, published, exempted -- are answerable against named
    input instead.
    """
    return {
        name: path
        for name, path in found.items()
        if name not in read and name not in public and name not in READ_ELSEWHERE
    }


def test_no_constant_is_published_for_tests_alone():
    """A constant nothing reads is a claim about the code the code does not make.

    `AXES` is the instance that found this gap. It was a public tuple on
    `domain.capabilities`, and the only things that read it were three test
    files -- one of which had already given up and re-derived it from
    `fields(Capabilities)` rather than importing it. Forty-nine architecture
    rules ran over it for as long as it existed and not one of them was looking
    at constants.

    The second instance is what this caught on the way in, and it is the worse
    of the two because it was still being maintained. `harness.backend`
    published `SKILLS_SOURCES = [(SKILLS_ROUTE, "catalogue"),
    (UPLOADED_SKILLS_ROUTE, "uploaded")]`, which is exactly what `skills_sources()`
    returns when a session has no catalogue folders. The commit that let two
    parties each ship a `lookup` replaced every production reader of the constant
    with a call to the function, and in the same diff edited the constant --
    "Catalogue" to "catalogue" -- so it went on looking cared for. Its comment
    still claimed "both `agent` and `delegation` need them"; neither had
    mentioned it for a day. One test held it up, asserting
    `captured["skills"] == SKILLS_SOURCES` while the test beside it in
    `test_capability_wiring` made the identical assertion against
    `skills_sources()`. That is the fourth copy, caught at three.

    The service is not counted as a reader, and that is a decision rather than an
    inherited default. `PRODUCTION` has never included `service/src`, and for
    constants it provably need not: `test_a_consumer_uses_the_library_only_through_its_public_api`
    forbids the server from importing anything but `kingfisher` itself, so every
    library constant it can legally read is in `__all__` and exempt here already.
    Checked against the tree rather than argued -- no constant in the package is
    read by `service/src` and unread by the library.
    """
    import kingfisher

    read = _referenced_in_code()
    public = frozenset(kingfisher.__all__)
    found = _constants_in_package()

    # A collector pointed at the wrong root finds nothing and reports success --
    # the failure this file has shipped twice, once in `_modules_in` and once in
    # the consumer collector. Named layers rather than a count, so this says
    # which half of the package stopped being walked. All four define constants;
    # `domain` and `infrastructure` hold sixty-seven of the seventy-three.
    layers = {"domain", "application", "infrastructure", "presentation"}
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
    """Every constant in the tree is read, published or exempted, so the rule
    above passes whether `_unread` subtracts anything or nothing. This is the
    answer `src/` cannot give: one name for each way out, and one with none.
    """
    here = Path("domain/result.py")
    found = {"READ": here, "PUBLISHED": here, "KINDS": here, "ORPHAN": here}

    assert _unread(found, {"READ"}, frozenset({"PUBLISHED"})) == {"ORPHAN": here}
    # And the direction that must not fire: nothing defined is nothing to report.
    assert _unread({}, set(), frozenset()) == {}


def test_a_constant_is_not_counted_as_its_own_reader():
    """The mutation the tree cannot catch, because a clean tree is silent about it.

    Drop the `Load` test in `_names_read` and every constant in the package
    reports itself read, the rule above passes over anything, and nothing goes
    red -- which is precisely how it would ship. These are the six answers
    `src/` cannot give: an assignment is not a read of what it assigns, but a
    load, an attribute, either shape of import and an annotated assignment's
    value all are, and prose is not.
    """
    assert _names_read("KINDS = ('token',)\n") == set()
    assert _names_read("SOURCES = [ROUTE, OTHER]\n") == {"ROUTE", "OTHER"}
    assert _names_read("SOURCES: list[str] = [ROUTE]\n") == {"list", "str", "ROUTE"}
    assert _names_read("x = mod.KINDS\n") == {"mod", "KINDS"}
    assert _names_read("from m import KINDS\nimport a.b as ROUTE\n") == {"KINDS", "ROUTE"}
    assert _names_read('"""KINDS is named here in prose only."""\n') == set()


def test_the_constant_reader_reads_module_level_constants():
    """Pinned against source written for the purpose, because the real tree is
    expected to be clean and a reader that found nothing would look identical --
    the same reason `_kinds_branched_on` has a test of its own.
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
    """An exemption for a name nobody defines any more silences nothing, and
    reads as though somebody thought about it. `DISPATCHED_ELSEWHERE` has no such
    guard and should; this table starts with one, since it exists to hold the
    cases a reader has to take on trust.
    """
    defined = set(_constants_in_package())

    assert defined >= READ_ELSEWHERE, (
        f"{sorted(READ_ELSEWHERE - defined)} is exempted but no longer defined in "
        "the package -- drop the entry"
    )


def test_every_console_script_points_at_something_that_exists():
    """A `[project.scripts]` line is only checked when somebody installs and runs.

    `kingfisher = "kingfisher.presentation.cli.__main__:main"` naming a function that is not
    there fails at the shell, for a stranger, after a pip install -- which is
    the worst place to find out and the last place we would look. Nothing
    covered this: renaming the target to `:absent` left the suite green.

    Both scripts, and by import rather than by reading the source, so a target
    that exists but cannot be imported fails here too.
    """
    import tomllib
    from importlib import import_module

    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = manifest["project"]["scripts"]
    assert scripts, "the scripts table emptied out"

    for command, target in scripts.items():
        module_name, _, attribute = target.partition(":")
        module = import_module(module_name)
        assert callable(getattr(module, attribute, None)), f"{command} -> {target}"


def test_only_the_confinement_module_calls_resolve_directly():
    """`shell_confinement` is the one place a `Config` becomes a confinement.

    `resolve` takes one argument per root the profile has to name, and two
    callers were assembling those six from the same `Config` -- the backend that
    runs commands, and the driver that warns when nothing is confining them. Two
    assemblies of one fact is how they come to disagree, and disagreeing here
    means warning about a confinement other than the one in force.

    Nothing caught that: replacing the helper call in the driver with a
    hand-assembled `resolve` left the whole suite green, which is the shape of a
    rule that exists only in a docstring.
    """
    # Production only. A test of `resolve` calls `resolve`, and exempting the
    # test tree is what lets that one keep testing the thing it is about.
    offenders: list[str] = []
    for path in _production_files():
        if path.name == "confinement.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            reached = (
                isinstance(node, ast.Attribute)
                and node.attr == "resolve"
                and isinstance(node.value, ast.Name)
                and node.value.id == "confinement"
            ) or (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("confinement")
                and any(alias.name == "resolve" for alias in node.names)
            )
            if reached:
                offenders.append(path.name)

    assert not offenders, (
        f"{sorted(set(offenders))} call `confinement.resolve` directly — use "
        "`shell_confinement(cfg)`, so one place decides what a Config means"
    )


# -- the path every other rule starts from ---------------------------------


def _fake_checkout(root: Path) -> Path:
    """A directory that looks like this repository to `_repository_root`."""
    (root / "src" / "kingfisher").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    return root


def test_the_root_holds_this_file():
    """The property that makes every other rule here mean anything.

    Not a tautology: the previous version found a directory holding
    `pyproject.toml` and `packages/`, and when `packages/` went it climbed out
    of the checkout and returned the *parent clone* -- which does not contain
    this file. Every rule then read a different repository and passed.
    """
    assert Path(__file__).resolve().is_relative_to(REPO)


def test_the_root_is_the_nearest_one_not_an_outer_one(tmp_path):
    """A checkout inside another checkout picks the inner one.

    Exactly the shape that broke it: this repository is developed in git
    worktrees under `.claude/worktrees/`, so there is nearly always an outer
    clone that also looks like a repository.
    """
    outer = _fake_checkout(tmp_path / "outer")
    inner = _fake_checkout(outer / "nested" / "inner")
    deep = inner / "tests"
    deep.mkdir()

    assert _repository_root(deep / "a_test.py") == inner


def test_no_root_at_all_is_an_error_rather_than_a_climb(tmp_path):
    """It raised `StopIteration` from a generator, which reads as a collection
    error nobody can act on. And the alternative to raising is worse: walking
    to `/` and taking whatever matches there is how the wrong tree got read."""
    lonely = tmp_path / "nowhere" / "tests"
    lonely.mkdir(parents=True)

    with pytest.raises(AssertionError, match="no repository root"):
        _repository_root(lonely / "a_test.py")


def test_a_directory_that_only_half_matches_is_not_the_root(tmp_path):
    """Both halves of the marker are load-bearing. A `pyproject.toml` alone
    describes most Python directories on a disk -- including the ones this
    repository is developed inside."""
    outer = _fake_checkout(tmp_path / "outer")
    half = outer / "nested"
    (half / "tests").mkdir(parents=True)
    (half / "pyproject.toml").write_text("", encoding="utf-8")  # no src/kingfisher

    assert _repository_root(half / "tests" / "a_test.py") == outer


def test_every_path_rule_starts_from_the_same_two_names():
    """Four separate computations of "the repository" is how three of them came
    to disagree. `REPO` and `SRC` are the only two, and `SRC` is derived."""
    assert SRC == REPO / "src" / "kingfisher"
    assert SRC.is_dir()
    assert (REPO / "pyproject.toml").is_file()


def test_no_value_is_written_down_twice():
    """One definition per value, across the library.

    `domain.tool` made this move for `SEPARATOR` and said why -- "one separator
    both kinds import beats two that agree by coincidence" -- and named skills
    as the other kind. `harness.skill_registry` kept its copy anyway, along with
    a second copy of `domain.skill.UPLOADED`, and both carried comments claiming
    they matched the original. A copied literal cannot keep that promise; it can
    only happen to. Nothing noticed, because nothing was looking.

    Same name *and* same value, so the cases that merely rhyme are left alone:
    `EXPORT` is `"TOOLS"` in one place and `"SUBAGENTS"` in another, `DIRECTORY`
    is `"skills"` against `"subagents"`, and `TOOLS` is the export protocol each
    asset module declares for itself. Those are three formats each naming their
    own thing, which is the opposite of this.

    No exclusion for content any more: there is none under `src/` to exclude,
    which `test_no_definitions_live_inside_the_package` is what guarantees.

    The collector moved out to `_constants_defined` when the orphan rule needed
    the same walk. Two readings of "what is a constant" in one file is the fault
    this rule is named for, one level up.
    """
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
