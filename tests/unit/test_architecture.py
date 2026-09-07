"""The layer boundary, enforced rather than remembered."""

from __future__ import annotations

import ast
import inspect
import re
import sys
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


def _modules_in(layer: str, root: Path = SRC) -> list[Path]:
    """Every module in a layer, subpackages included."""
    return sorted(
        p
        for p in (root / layer).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _module_id(path: Path) -> str:
    """`harness/agent.py`, not `agent.py`."""
    if path.is_relative_to(SRC):
        return path.relative_to(SRC).as_posix()
    return path.relative_to(REPO).as_posix()


def _inside_domain(module: str) -> bool:
    return module == "kingfisher.domain" or module.startswith("kingfisher.domain.")


def test_a_layer_rule_reaches_into_a_subpackage(tmp_path):
    """The collection every rule below is built on, tested for the case it missed."""
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


def test_a_harness_edge_is_seen_however_it_is_spelled(tmp_path):
    """`_harness_reach` had the same blindness as `_imported_modules`."""
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
    """Every failure message in this file is built from `_module_id`."""
    assert _module_id(SRC / "tools" / "spec.py") == "tools/spec.py"
    assert _module_id(SRC / "infrastructure" / "harness" / "tool.py") == (
        "infrastructure/harness/tool.py"
    )


#: Everything in this repository that may import kingfisher. The other two
#: distributions are included deliberately: they are separate wheels that depend
#: on this one, so they are the first place a move here breaks and the last place
#: anyone thinks to look.


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
    assert _names_a_real_module("kingfisher.tools.spec")
    assert _names_a_real_module("kingfisher.infrastructure.harness")
    assert _names_a_real_module("kingfisher.infrastructure.harness.agent")

    assert not _names_a_real_module("kingfisher.infrastructure.agent")
    assert not _names_a_real_module("kingfisher.server")
    assert not _names_a_real_module("kingfisher.server.asgi")


def test_the_second_distribution_is_in_scope():
    """`assets/` is where the move actually broke, and the rule is worth nothing if it
    stops looking there.
    """
    scanned = {p.relative_to(REPO).parts[0] for p in _everything_that_imports_kingfisher()}
    assert "service" in scanned, (
        "the dangling-import rule is not reading service/ — the other distribution "
        "is where a move in src/ lands first"
    )


#: What a prose reference can be rooted at, and the only form of it that can be checked.
#: `models.yaml`, `run.py` and `uploads.provision` are shaped exactly like module paths;
#: `infrastructure.harness.backend` cannot be anything else. Measured across this
#: repository: the rooted form gives fifty references and finds thirteen that are wrong,
#: while the unrestricted form gives 143 and calls 115 of them broken.
def _prose_roots(root: Path = SRC) -> tuple[str, ...]:
    """Every package and root module a prose reference may be rooted at."""
    return tuple(sorted(
        [d.name for d in root.iterdir() if d.is_dir() and (d / "__init__.py").is_file()]
        + [f.stem for f in root.glob("*.py") if f.stem != "__init__"]
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
#: prose and is not -- `agent.py` promised "the last of three parts --" and named
#: none of them, `prepare_scratch` promised "two problems" and listed neither.
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
    assert _prose_unresolved("`infrastructure.harness.backend`") == []
    assert _prose_unresolved("`skills.spec.split` and `application.inventory`") == []
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


def test_the_prose_rule_reaches_the_packages_that_are_not_layers():
    """`tools`, `skills` and `subagents` left the layers and left this rule's sight."""
    for missing in ("skills.nowhere", "tools.nowhere", "subagents.nowhere",
                    "config.nowhere"):
        assert _prose_unresolved(f"`{missing}`") == [missing], (
            f"{missing} is not a module and the rule has to say so"
        )

    # And the live ones still resolve, now that they are actually being read.
    assert _prose_unresolved("`skills.spec.split`") == []
    assert _prose_unresolved("`tools.spec`, `subagents.harness`, `config`") == []


def test_a_filename_is_not_read_as_a_module_and_a_segment():
    """`config.py` parses as the module `config` plus a segment called `py`."""
    assert _prose_unresolved("`config.py` and `tools.py`") == []
    assert _prose_unresolved("`skills.py`, `subagents.py`, `config.yaml`") == []
    # The tail is what decides it, not the root: a real module under one of them
    # still has to name something real. Built rather than written, because this
    # file is one the rule reads and a literal would be a reference it refuses.
    #
    # This said `config.models` until class members started counting, at which
    # point `Config.models` made it resolve and the rule was right about a test
    # that had gone quietly wrong.
    absent = "config.no_such_field"
    assert _prose_unresolved(f"`{absent}`") == [absent]


def test_a_name_a_class_holds_is_a_name_the_module_defines():
    """`skills.registry.misfiled` is a documented field, and was read as stale."""
    assert _prose_unresolved("`skills.registry.misfiled`") == []
    assert _prose_unresolved("`config.models`") == []

    # And the module's own top level still counts, which is most of the traffic.
    assert _prose_unresolved("`skills.spec.split`") == []


def test_the_documents_are_read_by_the_prose_rule():
    """`decisions.md` is where this repository says where things are."""
    scanned = {p.relative_to(REPO).as_posix() for p in _prose_bearing_files()}

    assert "docs/decisions.md" in scanned, "the file the rule most needs to read"
    assert "CLAUDE.md" in scanned
    # Asserted through what the rule reads rather than through `_documents()`,
    # so dropping the documents from the scan fails here instead of passing.
    assert "src/kingfisher/application/service.py" in scanned, (
        "the Python half has to survive the documents being added to it"
    )


def test_no_rule_here_is_parametrized_over_nothing():
    """A directory that stops existing takes its rule down with it, silently."""
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
    """`rglob` descends into `__pycache__`, which `glob` never reached."""
    layer = tmp_path / "domain"
    (layer / "__pycache__").mkdir(parents=True)
    (layer / "__pycache__" / "stale.py").write_text("import yaml\n", encoding="utf-8")
    (layer / "real.py").touch()

    assert [p.name for p in _modules_in("domain", root=tmp_path)] == ["real.py"]


@pytest.mark.parametrize("path", _modules_in("domain"), ids=_module_id)
def test_domain_imports_only_the_standard_library_and_itself(path):
    """Deny by default, replacing three rules that were allowlists by omission.

    **One exception, measured rather than granted.** A domain module may name an
    asset kind's `spec`: it is the *format's* vocabulary, and importing
    `domain.ports` and `agents.spec` takes 39ms and loads 101 modules, none of them
    the agent runtime, against the 888ms `decisions.md` quotes for the bad case. A
    `catalogue` or a `harness` would not be free, and those stay refused.
    """
    outside = {
        module
        for module in _imported_modules(path)
        if module.split(".")[0] not in sys.stdlib_module_names
        and not _inside_domain(module)
        and not _is_asset_spec(module)
    }
    assert not outside, (
        f"{_module_id(path)} imports {sorted(outside)} -- the domain takes the standard "
        "library, itself, and an asset kind's `spec`; have an adapter do the rest "
        "and hand it the result"
    )


def _is_asset_spec(module: str) -> bool:
    """Whether a module is an asset kind's format vocabulary."""
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    parts = module.split(".")
    return (
        len(parts) == 3
        and parts[0] == "kingfisher"
        and parts[1] in DEFINITION_KINDS
        and parts[2] == "spec"
    )


def test_the_domain_may_name_a_spec_but_not_a_catalogue():
    """The exception above, asserted at its edges rather than trusted."""
    assert _is_asset_spec("kingfisher.tools.spec")
    assert _is_asset_spec("kingfisher.skills.spec")
    assert not _is_asset_spec("kingfisher.tools.catalogue"), "the disk is not free"
    assert not _is_asset_spec("kingfisher.tools.harness"), "the runtime is not free"
    assert not _is_asset_spec("kingfisher.application.service")
    assert not _is_asset_spec("kingfisher.tools.spec.inner")
    # The kind check on its own: three parts ending in `spec`, under something
    # that is not an asset kind. Without this the predicate could drop
    # `DEFINITION_KINDS` and every assertion above would still hold.
    assert not _is_asset_spec("kingfisher.application.spec"), "only a kind has a spec"


#: Which third-party packages each area may import. Deny by default: a package
#: named nowhere below fails wherever it appears, so the table is what has to be
#: edited to take on a dependency, and editing it is where someone asks whether
#: the dependency belongs there.
#:
#: Measured, not declared -- every entry is a package some module imports today.
THIRD_PARTY: dict[str, frozenset[str]] = {
    # The agent runtime. This *was* the swap boundary in one directory, and is
    # now the larger half of it: `skills/` reaches the runtime too, and the
    # entry below says so. What the boundary buys is unchanged in kind and
    # weaker in degree -- an upgrade is still a list of files rather than a
    # search, and the list now spans two directories instead of one.
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
    # Registering skills means handing them to the runtime that reads them: `registry`
    # asks deepagents which skills an agent will actually have, and `backend` mounts the
    # directory it reads them from. Neither can be done from outside, and inverting them
    # behind a port would put one implementation behind an interface derived from it.
    "skills": frozenset({"deepagents", "langchain_core", "langgraph"}),
    # `tools.harness` reads the tool roster off a compiled graph, which is a
    # langgraph object, and resolves what a request may call against it. The
    # same trade `skills` made one entry up, and the third directory the swap
    # boundary now spans.
    "tools": frozenset({"langchain_core", "langgraph"}),
    # `subagents.harness` turns a spec into the `SubAgent` deepagents expects,
    # which cannot be done without naming the type. The third and last kind to
    # reach the runtime, and the reason the swap boundary is now stated as a
    # list of areas rather than one directory.
    "subagents": frozenset({"deepagents", "langchain_core"}),
    # The fourth kind, and the only one whose set is empty: an agent's runtime
    # half is `harness/agent.py`, which is not this package's.
    "agents": frozenset(),
    # The fifth, and back on the boundary: `middleware.catalogue` refuses a class
    # that is not an `AgentMiddleware` as the directory is read rather than at the
    # first turn, which cannot be done without naming the type.
    "middleware": frozenset({"langchain"}),
    # The one consumer still in this distribution. `presentation` was the other and is
    # now `kingfisher-service`, a package of its own with its own rules -- so fastapi
    # and uvicorn are no longer anything this table has an opinion about, and an area
    # that named them would be permitting what it cannot see.
    "presentation/cli": frozenset({"kingfisher_service", "dotenv"}),
    # Nothing. The domain has a stricter rule of its own; these two are here so
    # the table is total and an unlisted area cannot mean "anything goes".
    "domain": frozenset(),
    "application": frozenset(),
    # The modules at the package root, which belong to no layer: `__init__.py`,
    # `config.py`, `layout.py` -- the workspace layout as data -- and
    # `testing.py`, the port contracts a deployment runs against its own
    # adapter. Nothing, and `testing.py` is the one that has to stay that way on
    # purpose rather than by luck: a kit importing pytest would put a test
    # framework in the runtime wheel, which is why it raises `AssertionError` by
    # hand instead.
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


def test_an_area_is_refused_another_areas_dependencies():
    """The table has to partition, not merely enumerate."""
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
    """`infrastructure/harness/agent.py` is not judged as `infrastructure/`."""
    catalogue = SRC / "infrastructure" / "catalogue" / "__init__.py"
    buried = SRC / "infrastructure" / "catalogue" / "layered.py"
    for path in (catalogue, buried, SRC / "domain" / "capabilities.py", SRC / "config.py"):
        assert path.exists(), f"{path} does not exist, so the assertion below is about nothing"

    assert _area_of(SRC / "infrastructure" / "harness" / "agent.py") == "infrastructure/harness"
    assert _area_of(SRC / "domain" / "capabilities.py") == "domain"
    # A kind's module is its own area, which is what lets `tools/harness.py`
    # name the runtime without `domain/` inheriting the permission.
    assert _area_of(SRC / "tools" / "harness.py") == "tools"
    assert _area_of(SRC / "config.py") == ""

    # A subpackage with no entry of its own is judged by its parent, which is
    # what lets `catalogue/` inherit `{yaml}` without naming it -- and what
    # would stop being true the moment someone gave it an entry.
    assert _area_of(catalogue) == "infrastructure"
    assert _area_of(buried) == "infrastructure"


@pytest.mark.parametrize("path", _package_modules(), ids=_module_id)
def test_a_module_imports_only_what_its_area_may_depend_on(path):
    """One table, replacing two rules that were allowlists by omission."""
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
    """The half of the old rule worth keeping, scoped to where it means something."""
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


#: Which flat `infrastructure/` modules may reach into `infrastructure/harness/`, and
#: what each one reaches for. Deny by default, like `THIRD_PARTY`: an edge named nowhere
#: below fails, so this table is what has to be edited to add one, and editing it is
#: where someone asks whether the edge belongs.
def _harness_consumers() -> list[Path]:
    """Every module the harness table is about, in both layers that reach it."""
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
    # `catalogue` asked `harness.skill_registry` which names were taken; the
    # registry is `skills.registry` now, which is not the harness, so this is
    # no longer an edge into it at all.
    "catalogue": frozenset(),
    # Asks the registry what names are taken before accepting an upload, which is the
    # same question `catalogue` asks and the same answer.
    "workspace.uploads": frozenset(),
    # Builds an agent to enumerate what it registered -- the only way to know
    # the built-in tool set is to assemble one and look.
    "inventory": frozenset({"agent"}),
    # Reads `ADAPTERS` to refuse an `api` kingfisher cannot build, as the
    # catalogue loads rather than when a turn starts. The same argument as
    # `catalogue` above: the alternative is a second list of the wire formats
    # living in the config layer, and two lists of one fact drift -- here into
    # a file that loads and a deployment that cannot run.
    #
    # Cheap in the terms this package measures. `models.py` names its chat
    # classes as strings and resolves them on demand, so importing it costs
    # 1.2ms and two modules on top of `model_catalogue`, and pulls in no
    # provider SDK at all.
    "model_catalogue": frozenset({"models"}),
    # The service is the harness's largest consumer, and was never in this table
    # because the rule only walked `infrastructure/`. Running a turn *is* driving
    # the harness: an agent to run, a checkpointer to resume it, a run log to
    # record it, and the runtime that turns its stream into events.
    # `interpreter` joined the four when it left `agent`, and the edge is the
    # same one it always had: a turn opens a QuickJS runtime and must close it,
    # which is the one of the three closables that hangs the process rather than
    # leaking a handle. Splitting a harness module widens the consumer's list
    # without widening what the consumer does -- worth knowing before reading
    # five as more coupling than four.
    "service": frozenset(
        {
            "activation",
            "agent",
            "checkpointing",
            "interpreter",
            "middleware",
            "runlog",
            "runtime",
        }
    ),
    # The disposal half of `service`, which took this edge with it: reaping a
    # session deletes the thread behind it, and `thread_ids` is how it learns
    # which threads no session owns any more.
    "disposal": frozenset({"checkpointing"}),
    # The withheld report, which left `service` and took one of its four edges
    # along. It has to ask the *assembled* agent what it registered and the
    # workspace what it offers, because the whole claim of the report is that it
    # measures against what was actually wired rather than against a list kept
    # somewhere -- so the edge is the point of it, not an accident of where it
    # used to live.
    # `surface` became `tools.harness`, which is not the harness package, so
    # what is left of this edge is the activation half.
    "reporting": frozenset({"activation"}),
    # One stream chunk, read the same way by the sync and async loops. The
    # reading is deepagents' shape rather than ours -- which namespace a chunk
    # came from, which mode carries the answer -- so it is an edge wherever it
    # is written, and writing it once is the whole reason the two loops cannot
    # drift about what a chunk means.
    "turn": frozenset({"runtime"}),
}


#: The package the edges below cross into.
HARNESS = "kingfisher.infrastructure.harness"


def _consumer_key(path: Path) -> str:
    """A module's name below its layer, which is how `HARNESS_EDGES` is keyed."""
    parts = list(path.relative_to(SRC).parts[1:])
    parts = parts[:-1] if parts[-1] == "__init__.py" else [*parts[:-1], parts[-1][:-3]]
    return ".".join(parts)


def _harness_reach(path: Path) -> set[str]:
    """Which harness modules one flat module imports, by their bare names."""
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
    """The line runs one way, apart from the edges written down above."""
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
    """A rule with no cases passes, and both halves above have none by design: every
    edge is named, so narrowing the walk back to `infrastructure/` alone changes no
    result.
    """
    walked = {_consumer_key(path) for path in _harness_consumers()}

    assert "service" in walked, "the rule stopped reading application/"
    assert "inventory" in walked
    assert _harness_reach(SRC / "application" / "service.py"), (
        "service.py reaches into the harness, so it is a real case rather than "
        "a name in a set"
    )


def test_every_named_harness_edge_is_a_real_one():
    """The other half, so the table cannot outlive what it describes."""
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
    """The outward half of the rule, which went unenforced for a while."""
    for path in _modules_in("infrastructure"):
        modules = _imported_modules(path)
        assert not any(m.startswith("kingfisher.application") for m in modules), (
            f"{_module_id(path)} depends on application/ — "
            "move the shared shape into domain/"
        )


def test_the_public_api_list_matches_the_lazy_export_table():
    """`__all__` is a literal so a linter can see it, and `_EXPORTS` drives the lazy
    loading.
    """
    import kingfisher

    assert sorted(kingfisher.__all__) == sorted(kingfisher._EXPORTS)


#: Every package that re-exports through a `__getattr__` table, and the file
#: holding it. Both, because both have the same two halves that can disagree and
#: only one of them had ever been checked against anything.
LAZY_TABLES: dict[str, Path] = {
    "kingfisher": SRC / "__init__.py",
    "kingfisher.application": SRC / "application" / "__init__.py",
}


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


@pytest.mark.parametrize(
    ("package", "path"), sorted(LAZY_TABLES.items()), ids=sorted(LAZY_TABLES)
)
def test_the_stub_block_and_the_export_table_name_the_same_things(package, path):
    """The third table, and the one nothing had ever held to the other two."""
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


#: Why each public name is public: who outside this wheel asked for it.
#:
#: `_EXPORTS` says what a name *is*, one comment at a time, and that is the
#: right place for it. This says who is owed it, which is a different question
#: and the one the list could not answer -- eleven names left it in a single
#: commit because nothing told a name added on a caller from a name added on a
#: guess, and two more left on this rule.
#:
#: Four witnesses:
#:
#: `service`  -- `kingfisher-service` imports it. Read off the service's own
#:               imports below, so this half cannot rot.
#: `document` -- a page tells a reader to write it. Checked by a person and
#:               never by grep: `offered` gets five hits in the guides and `run`
#:               gets thirty-one, every one of them the English word.
#: `embedder` -- nothing in this repository asks and it is kept anyway. These
#:               are the entries worth arguing about, so each says why.
#:
#: There was a fourth, `command`, for a name nothing outside this wheel asked
#: for. It was never a witness -- it was the eviction list wearing the table's
#: shape, so that the work remaining lived in the code rather than only in a
#: proposal. Sixteen names carried it and all sixteen have gone; it went with
#: the last of them rather than staying as a value that is always an error.
#:
#: What replaces it is the rule below and nothing else, which is enough: a name
#: whose only caller is the command can be given none of the three above without
#: somebody writing a false reason, and the reason is the part a reader can
#: check. *The front door* in `docs/decisions.md`.
#:
#: Deny by default. A name in `_EXPORTS` and not here fails the rule below,
#: which is where somebody decides which kind it is rather than discovering a
#: year later that nothing looked.
WITNESSES: dict[str, str] = {
    # The nineteen the service imports. Nothing to argue about and nothing to
    # maintain: `test_the_service_witnesses_are_read_not_claimed` compares this
    # against what `kingfisher_service` actually writes, both directions.
    "AccessError": "service",
    "Capabilities": "service",
    "CapabilityError": "service",
    "Config": "service",
    "Kingfisher": "service",
    "LocalFileStore": "service",
    "QuotaExceededError": "service",
    "Request": "service",
    "RunEvent": "service",
    "RunResult": "service",
    "SessionBusyError": "service",
    "SessionInfo": "service",
    "SkillError": "service",
    "SubagentError": "service",
    "UnknownReferenceError": "service",
    "UnknownSessionError": "service",
    "UnsafeReferenceError": "service",
    "UploadError": "service",
    "config_from_env": "service",
    # Was `embedder` on this branch -- "raised by `config_from_env`, so a caller
    # that builds a `Config` must be able to catch it" -- and the service picked
    # it up before the branch landed. The rule read the service rather than the
    # label and said so, which is the half of it that is not decoration.
    "ConfigError": "service",
    "file_store_named": "service",
    # `README.md` opens on these four and the package docstring on `run`. A
    # reader who copied either is owed them.
    "definitions_source": "document",
    "ensure_layout": "document",
    "paths_from_env": "document",
    "seed": "document",
    "run": "document",
    # `formats.md` writes them out: `groups=UNSCOPED`, and
    # `from kingfisher import Request, RunOn`. Grep for those rather than trusting a
    # line number here -- two were written down and both went stale the first time
    # that page was edited.
    "UNSCOPED": "document",
    "RunOn": "document",
    # `guides/ports.md` writes `from kingfisher import SESSION_STORE_CONTRACT`
    # and `from kingfisher import FILE_STORE_CONTRACT, Planted` -- a deployment
    # runs them against a store of its own, so they exist for nobody else.
    "SESSION_STORE_CONTRACT": "document",
    "FILE_STORE_CONTRACT": "document",
    "Planted": "document",
    # The other two kits, and the type a runner returns. The same page writes
    # all three; `CommandResult` is the one that would have been missed, because
    # nothing *imports* it in a snippet -- a runner's `run` returns one, so a
    # deployment cannot write the port without it and had no public spelling.
    "SESSION_ROOT_CONTRACT": "document",
    "COMMAND_RUNNER_CONTRACT": "document",
    "CommandResult": "document",
    # The type of `Kingfisher.run`'s `groups=`. `UNSCOPED` is one of its two
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
    # The async half of `run`, which is documented. A server-shaped caller
    # streams, and the two are one decision.
    "stream": "embedder",
    # A directory of sessions, and the port it satisfies. The subject of a
    # standing proposal about deployments naming their own store, which is a
    # reason to leave it reachable while that argument is live.
    "LocalSessionStore": "embedder",
}


def _through_the_front_door(root: Path) -> frozenset[str]:
    """Every name a consumer takes from `kingfisher` itself."""
    taken: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        taken |= _imported_names(path).get("kingfisher", frozenset())
    return frozenset(taken)


def test_every_public_name_has_a_witness():
    """A name nobody can name a caller for is a promise nobody asked for."""
    import kingfisher

    unwitnessed = sorted(set(kingfisher._EXPORTS) - set(WITNESSES))
    stale = sorted(set(WITNESSES) - set(kingfisher._EXPORTS))

    assert not unwitnessed, (
        f"{unwitnessed} are public and WITNESSES does not say who asked. Name the "
        "caller: `service` if kingfisher-service imports it, `document` if a page "
        "tells a reader to write it, `embedder` with a reason if neither -- and if "
        "the honest answer is `command`, it does not belong on the door"
    )
    assert not stale, f"{stale} are in WITNESSES and are not exported any more"


def test_the_service_witnesses_are_read_not_claimed():
    """The half of the table that must never be maintained by hand."""
    claimed = {name for name, why in WITNESSES.items() if why == "service"}
    actual = _through_the_front_door(CONSUMERS["kingfisher_service"]) & set(WITNESSES)

    assert claimed == actual, (
        "WITNESSES and the service disagree about who imports what: "
        f"claimed and not imported {sorted(claimed - actual)}, "
        f"imported and labelled otherwise {sorted(actual - claimed)}"
    )


def test_the_layer_and_the_root_agree_about_this_layer():
    """Two tables naming the same nine things, held to each other."""
    import kingfisher
    from kingfisher import application

    root_here = {
        name: module
        for name, module in kingfisher._EXPORTS.items()
        if module.startswith("kingfisher.application.")
    }

    assert root_here, "the root exports nothing from this layer -- this asserts nothing"
    assert root_here == application._EXPORTS, (
        "kingfisher.application and the package root disagree about this layer: "
        f"{sorted(set(application._EXPORTS.items()) ^ set(root_here.items()))}"
    )
    assert sorted(application.__all__) == sorted(application._EXPORTS), (
        "the layer's __all__ and its export table name different things"
    )


def test_naming_the_layer_does_not_pull_in_deepagents():
    """The reason the layer's table is lazy rather than nine plain imports."""
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "from kingfisher.application import config_from_env\n"
        "assert not any(m.startswith('deepagents') for m in sys.modules), "
        "'naming the layer pulled in the harness'\n"
    )
    subprocess.run(  # noqa: S603 -- this interpreter, and a literal above
        [sys.executable, "-c", probe], check=True
    )


def test_importing_kingfisher_does_not_pull_in_deepagents():
    """The point of the lazy re-exports: a consumer that only touches domain types
    should not pay a second for three provider SDKs.
    """
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
    # The errors a caller must tell apart. Public so a consumer outside the package can
    # catch them by name -- the server being the first such consumer.
    "CapabilityError", "QuotaExceededError", "SessionBusyError", "SkillError",
    "SubagentError", "UnknownSessionError", "UploadError", "UnsafeReferenceError",
    "UnknownReferenceError", "LocalFileStore",
    # The `SessionStore` contract, for a deployment checking its own adapter.
    # Light, and it has to stay light: a deployment runs this from its own test
    # suite, and a kit that pulled three provider SDKs in to check four methods
    # over bytes would be a cost paid on every CI run for nothing. `testing`
    # imports `domain.references` and the standard library, and no test
    # framework either -- see its docstring for why that one is deliberate.
    "SESSION_STORE_CONTRACT",
    # The other port's contract, and the record a check is handed. Light for the
    # same reason and by the same route -- `testing` imports the two reference
    # errors and the standard library.
    "FILE_STORE_CONTRACT", "Planted",
    # The remaining two kits and the result type a runner builds. Light by the
    # same route: `testing` reaches `domain.references` and the standard
    # library, and `CommandResult` is a frozen dataclass in `domain.ports`.
    "SESSION_ROOT_CONTRACT", "COMMAND_RUNNER_CONTRACT", "CommandResult",
    # Turning `KINGFISHER_SERVICE_FILE_STORE_FACTORY` into a store. Light, and
    # it has to be: the service resolves it in its lifespan, and the whole point
    # of a named factory is that kingfisher has never imported what it names.
    "file_store_named",
    "ensure_layout", "config_from_env",
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
    # Seeding, and asking what a workspace offers. Measured at 21-50ms and 148-192
    # modules with no SDK loaded -- heavier than `system_prompt` at 90, because `yaml`
    # and `importlib.metadata` come with them, and nowhere near the 3,100 a provider
    # costs.
    "seed", "definitions_source", "kinds_at", "Seeded", "inventory", "Inventory",
    # Where a deployment reads from. Light for the reason `paths_from_env` is:
    # the question "which directories?" must not cost three provider SDKs, and
    # this one is asked by `doctor`, by a listing, and by anybody debugging a
    # definition that will not load.
    "Origins", "Origin",
    # A directory of sessions, and the port it satisfies. Neither imports
    # anything a deployment does not already have -- see `session_store`.
    "LocalSessionStore",
    # Reads `/proc/mounts` and two cgroup files. Nothing imported, and
    # all-`None` off Linux rather than an error.
    "memory_backing",
    # A renderer and a sentence. Both are what a consumer needed and neither
    # imports anything -- the cheapest names on this list.
    "offered", "SKILL_LAYOUT", "DEFINITION_KINDS", "SEED_HINT", "split_reference",
    # A sentence that stats one directory. Cheaper than the two above it and
    # public for the same reason `SEED_HINT` is -- `doctor` is a consumer.
    "destination_hint",
    # The access policy, its report, its error and the sentinel for running
    # without a caller. `domain.access` imports `domain.fields` and
    # `domain.capabilities` and nothing else, which is the point of it being a
    # domain module: deciding who reaches what must not cost a provider SDK,
    # because a deployment resolves a grant on every turn.
    "AccessError", "UNSCOPED", "Held", "AUDIENCED", "Audience",
    # Writing one out. Published for the same reason `ALL` below is: the command
    # prints audiences, an audience entry may be a conjunction, and a second
    # spelling in the printer is how a refusal quoting one stops matching the
    # listing showing it.
    "spell",
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
    "Kingfisher", "run", "stream",
})

PROVIDER_SDKS = ("deepagents", "langchain", "langchain_openai", "langchain_anthropic")


def _cli_reaches() -> dict[str, str]:
    """Names the command takes at their own address, and where each one lives."""
    found: dict[str, str] = {}
    for path in sorted(CONSUMERS["cli"].rglob("*.py")):
        for module, names in _imported_names(path).items():
            if _reaches_past_the_public_api(module):
                found.update(dict.fromkeys(names, module))
    return found


def _watched() -> dict[str, str]:
    """Every name a consumer pays to import, and the module that defines it."""
    import kingfisher

    return {**kingfisher._EXPORTS, **_cli_reaches()}


def test_every_watched_name_is_classified_light_or_heavy():
    """So a new export cannot slip past the rule below by not being listed."""
    assert set(_watched()) == LIGHT_EXPORTS | HEAVY_EXPORTS, (
        "a name a consumer imports must be in LIGHT_EXPORTS or HEAVY_EXPORTS — if "
        "it needs deepagents it is heavy, otherwise keep it light and say so here"
    )


def test_a_name_the_command_took_private_is_still_watched():
    """The half of the re-keying that a passing tree cannot show."""
    import kingfisher

    private = set(_watched()) - set(kingfisher._EXPORTS)

    assert private, (
        "no name is reached past the front door, so this rule is about nothing -- "
        "if the last one came back on, the union in `_watched` can go too"
    )
    assert private <= LIGHT_EXPORTS | HEAVY_EXPORTS
    assert "Confinement" in private, (
        "`Confinement` is the worked example: private, still imported by `doctor`, "
        "and light only for as long as something checks"
    )


def test_a_light_export_stays_light():
    """Touching a light name must not load a provider SDK."""
    import subprocess
    import sys

    light = {name: module for name, module in _watched().items() if name in LIGHT_EXPORTS}
    probe = (
        "import sys, importlib, kingfisher\n"
        f"for name, module in sorted({light!r}.items()):\n"
        "    if name in kingfisher.__all__:\n"
        "        getattr(kingfisher, name)\n"
        "    else:\n"
        "        getattr(importlib.import_module(module), name)\n"
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
    """`evals/` is test material and lives outside `src/`, so it is not in the wheel."""
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
    """The boundary the older tests were mistaken for."""
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
    """Orchestration decides what happens; an adapter is what makes it happen."""
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
        "infrastructure/workspace/, where the guards already are"
    )


def _mode_changes(path: Path) -> list[int]:
    """The lines on which a module changes a file's mode."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "chmod"
    )


def test_only_one_workspace_module_changes_a_mode():
    """`permissions` owns the write bits on `/data`, and used to own them by being a
    file.
    """
    offenders = [
        f"{_module_id(path)}:{line}"
        for path in _modules_in("infrastructure/workspace")
        if path.name != "permissions.py"
        for line in _mode_changes(path)
    ]

    assert not offenders, (
        f"{offenders} change a file mode inside the workspace package — "
        "`permissions` is the one module allowed to, and `writable_data` is how "
        "the rest of it asks"
    )


def test_the_mode_rule_can_tell_a_chmod_from_the_calls_around_it(tmp_path):
    """The rule above runs over a tree with no offenders, so it has to be shown to bite."""
    guilty = tmp_path / "placement.py"
    guilty.write_text("def place(path):\n    path.mkdir()\n    path.chmod(0o600)\n")
    innocent = tmp_path / "layout.py"
    innocent.write_text("def place(path):\n    path.mkdir()\n    path.touch()\n")

    assert _mode_changes(guilty) == [3], "a chmod two calls in is the case this exists for"
    assert _mode_changes(innocent) == [], "the neighbouring filesystem calls are not modes"


def test_infrastructure_is_the_layer_doing_the_touching():
    """The other half: if nothing in infrastructure/ touches the world either, the I/O
    did not move out, it moved somewhere less visible.
    """
    assert any(_world_contact(p) for p in _modules_in("infrastructure"))


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
        f"patch create_deep_agent directly at {offenders} — use conftest.capture_build, "
        "which records the call and still lets deepagents validate it"
    )


def test_only_one_module_decides_what_a_skill_is():
    """`--list` exists to tell a caller which names are valid, so it and `build_agent`
    must mean the same thing by "a skill".
    """
    repo = REPO
    owners = {
        SRC / "skills" / "spec.py",
        SRC / "skills" / "catalogue.py",
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
    """The package ships code."""

    documents = sorted(str(p.relative_to(SRC)) for p in _definition_documents(SRC))

    assert not documents, (
        f"{documents} are definitions inside the package — they ship in the "
        f"wheel and are read by no rule in this file, which is what made the "
        f"old `CONTENT` exclusion necessary. Definitions belong in "
        f"assets_examples/."
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
    from kingfisher.skills.spec import FILENAME as SKILL_FILE
    from kingfisher.subagents.reading import SUFFIX

    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.name == SKILL_FILE or path.suffix in {SUFFIX, ".yml"})
    )


def test_this_repository_still_has_a_worked_set(shipped):
    """The other half, and it fails separately."""
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    missing = sorted(kind for kind in DEFINITION_KINDS if not (shipped / kind).is_dir())

    assert not missing, (
        f"assets_examples/ holds no {', '.join(missing)} — this is the set the format "
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
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    assert DEFINITION_KINDS, "no kinds — this rule is about nothing"

    # A *file that exists*, not a name in a set. Stated as a set of stems, this
    # rule could be satisfied by widening the set -- a mutation adding every
    # kind to it left the assertion trivially true and nothing went red.
    missing = _kinds_without_a_reader(DEFINITION_KINDS, root=SRC)

    assert not missing, (
        f"{missing} is a kind the catalogue reads with no module of its own -- "
        "each kind owns a package at the root holding its `catalogue`"
    )


def test_the_package_ships_the_catalogue_example():
    """The one file that is not an asset and has to stay."""
    from importlib import resources

    from kingfisher.infrastructure.workspace import layout as workspace_layout

    # Both, from `EXAMPLES` rather than named here, so the file added next is
    # covered by having been added rather than by somebody remembering. The
    # groups example is optional where the catalogue one is required, and that
    # changes nothing about this: `_place_example` skips a missing source
    # silently, so a packaging fault would show up as a workspace quietly
    # missing furniture rather than as anything failing.
    assert workspace_layout.EXAMPLES, "nothing is asserted if the tuple is empty"
    for name in workspace_layout.EXAMPLES:
        assert (SRC / "templates" / name).is_file(), f"{name} left the package"
        installed = resources.files(workspace_layout.TEMPLATES).joinpath(name)
        assert installed.is_file(), f"{name} is not reachable the way an install reaches it"


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
    # `MissingStoreError` is here rather than above on purpose: a request naming files
    # by id with no `FileStore` wired is a deployment that forgot one, and nothing the
    # caller sends can fix it.
    #
    # `MiddlewareError` joins `ToolError` here for the reason `AgentError` sits here
    # while `SubagentError` sits above: a caller may upload a subagent and cannot
    # upload middleware, so a `middleware/` file that will not load is always the
    # deployment's own.
    "AccessError", "AgentError", "ConfigError", "DataError", "HostPathError",
    "LoadError", "MiddlewareError", "MissingStoreError", "ToolError",
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
    catching `kingfisher.SessionBusyError` catches what the domain raises.
    """
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


#: Every consumer held to the front door, and the directory each one lives in. The claim
#: that any caller can drive this library is worth something only if the callers that
#: ship with it are held to it -- which is why `offered` and `SKILL_LAYOUT` are public.
CONSUMERS: dict[str, Path] = {
    "cli": SRC / "presentation" / "cli",
    "kingfisher_service": REPO / "service" / "src" / "kingfisher_service",
}


def _consumer_modules() -> list[Path]:
    return sorted(path for root in CONSUMERS.values() for path in root.rglob("*.py"))


def _reaches_past_the_public_api(module: str) -> bool:
    """True when a consumer imports something deeper than `kingfisher` itself."""
    if module.split(".", maxsplit=1)[0] != "kingfisher" or module == "kingfisher":
        return False
    return not module.startswith("kingfisher.presentation.cli")


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
    """What the front door promises, read from the door itself."""
    import kingfisher

    return frozenset(kingfisher.__all__)


def _taken_by_the_back_door(
    module: str, names: frozenset[str], *, family: bool
) -> frozenset[str]:
    """What this import takes past the front door that it should not have."""
    if not _reaches_past_the_public_api(module):
        return frozenset()
    if not family:
        return frozenset({module})
    return names & _public_names() if names else frozenset({module})


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
    """The rule's own arithmetic, checked against named inputs."""
    assert _reaches_past_the_public_api(module) is reaches


def test_the_rule_above_still_finds_every_consumer():
    """The guard the parametrised rule cannot give itself."""
    found = {name for name, root in CONSUMERS.items() for _ in root.rglob("*.py")}

    assert found == set(CONSUMERS), (
        f"no modules found for {sorted(set(CONSUMERS) - found)} -- the consumer moved "
        "and the rule below is now silently about whatever is left"
    )


@pytest.mark.parametrize("path", _consumer_modules(), ids=_module_id)
def test_a_consumer_uses_the_library_only_through_its_public_api(path):
    """`from kingfisher import X`, never `from kingfisher.domain.y import X`."""
    family = _consumer_of(path) in FAMILY
    taken = frozenset().union(
        *(
            _taken_by_the_back_door(module, names, family=family)
            for module, names in _imported_names(path).items()
        ),
        frozenset(),
    )
    assert not taken, (
        f"{_module_id(path)} reaches for {sorted(taken)} — "
        + (
            "a name the front door carries comes through the front door, even for a "
            "consumer shipping in this wheel; that is what keeps the claim testable"
            if family
            else "a consumer outside this wheel takes `kingfisher` and nothing deeper; "
            "if it needs something private, export it on purpose"
        )
    )


@pytest.mark.parametrize("path", _consumer_modules(), ids=_module_id)
def test_both_import_collectors_see_the_same_modules(path):
    """`_imported_names` may add names; it may not lose an import."""
    assert set(_imported_names(path)) == _imported_modules(path)


def test_the_back_door_rule_tells_the_two_consumers_apart():
    """The questions the tree cannot ask."""
    deep = "kingfisher.application.service"
    private = "kingfisher.infrastructure.sandbox.confinement"

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


@pytest.mark.parametrize(
    "path",
    [p for layer in ("domain", "application", "infrastructure") for p in _modules_in(layer)]
    # Every module at the package root, globbed rather than named. It listed
    # `__init__.py` and `config.py` while those were the only two, and `testing.py`
    # arriving is what showed the cost of that: a new root module joins the
    # library and this rule does not notice, which is the drift this file
    # distrusts everywhere else. The two it named are still the two it finds on
    # a tree without the kit.
    + sorted(SRC.glob("*.py")),
    ids=_module_id,
)
def test_no_part_of_the_library_imports_the_server(path):
    """The outward half, and the half packaging leaves open."""
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


#: The consumer this is about. It was every consumer until `kingfisher run`
#: existed, at which point the rule caught the first caller it was never written
#: for: its own reason is that the sync pair blocks "every other turn sharing the
#: process", and a command has one turn and one process and exits after. Blocking
#: is what a command wants. Narrowed by name rather than by loosening the
#: predicate, so the server is held exactly as tightly as before.
ON_AN_EVENT_LOOP = "kingfisher_service"


def _server_modules() -> list[Path]:
    return sorted(CONSUMERS[ON_AN_EVENT_LOOP].rglob("*.py"))


@pytest.mark.parametrize("path", _server_modules(), ids=_module_id)
def test_the_server_calls_the_async_turn_methods(path):
    """`arun` and `astream`, never `run` and `stream`."""
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
    """`KINDS` is the closest thing to a wire contract here, and as prose it had drifted
    both ways -- naming `swept` and `sweep_failed`, which have not fired since
    retention moved off the request path, and omitting `cut_short`, which is how a
    caller learns its answer is incomplete.
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
def test_the_stop_reasons_are_what_the_package_assigns():
    """`STOP_REASONS` is a wire contract like `KINDS`, and pinned the same way."""
    from kingfisher.domain.result import STOP_REASONS

    assigned = set()
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            else:
                continue
            named = any(isinstance(t, ast.Name) and t.id == "stop_reason" for t in targets)
            if named and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                assigned.add(node.value.value)

    assert assigned == set(STOP_REASONS), (
        "STOP_REASONS and the reasons actually assigned have diverged — the value "
        "goes on the wire, so an extra entry is a reason no turn produces and a "
        "missing one is a reason no client knows to handle"
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
    read = _names_read("from kingfisher.agents.spec import DIRECTORY as AGENT_DIRECTORY")

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
    """The exemption above, asserted rather than left to the rule passing."""
    import kingfisher

    mixed = _mixed_into(frozenset(kingfisher.__all__))

    assert {"Sessions", "Disposal"} <= mixed, "Kingfisher's mixins are not seen as bases"


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
    # The SSE event names. Nothing in `src/`, the driver, `evals/` or `service/src/`
    # reads it -- `payloads.frame` puts `event.kind` on the wire straight from the event
    # and only *mentions* `KINDS` in prose -- so its readers are the clients subscribing
    # to those event names, and they are not in this repository to be counted.
    "KINDS",
    # The stop reasons, and the same argument one line for line: it goes on the
    # wire as `stop_reason` in the turn payload, so its readers are the clients
    # branching on that value and they are not in this repository. It is a
    # declaration with nothing to derive from -- the rule below is what pins it,
    # against the reasons the package actually assigns.
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


def test_no_constant_is_published_for_tests_alone():
    """A constant nothing reads is a claim about the code the code does not make."""
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
    """Every constant in the tree is read, published or exempted, so the rule above
    passes whether `_unread` subtracts anything or nothing.
    """
    here = Path("domain/result.py")
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


def test_only_the_confinement_module_calls_resolve_directly():
    """`shell_confinement` is the one place a `Config` becomes a confinement."""
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


#: Import name -> distribution, for the two `packages_distributions()` cannot
#: answer here. Both are absent from a macOS checkout for reasons that are the
#: point rather than an oversight: `sandlock` ships Linux-only wheels and
#: `kingfisher-service` is the workspace sibling, installed as a path rather
#: than resolved from an index. A guard that only worked where everything
#: happened to be installed would report success on the machine where the
#: dependency is missing, which is the one case worth catching.
PROVIDED_BY: dict[str, str] = {
    "sandlock": "sandlock",
    "kingfisher_service": "kingfisher-service",
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
    """Which installed distributions ship this exact module, not merely its root."""
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

    assert _providers("kingfisher_service.app") == {"kingfisher-service"}
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
    assert PROSE_ATTR.findall("`models.yaml` and `infrastructure.harness`") == []
