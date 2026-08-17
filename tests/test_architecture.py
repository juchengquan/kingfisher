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


#: Content, not code. `assets/` holds the definitions `kingfisher seed` copies
#: into a workspace: tools the *agent* imports and this package never calls,
#: skills that are markdown, subagent definitions that are yaml. They ship
#: inside the wheel so a fresh install seeds something that works, and they are
#: excluded from every rule in this file for the reason `presets/` used to be --
#: a tool written for an agent is judged by whether the agent can run it, not by
#: this package's layering.
#:
#: Excluded here rather than at each rule, so a rule added later cannot forget.
CONTENT = "assets"


def _is_content(path: Path) -> bool:
    return CONTENT in path.relative_to(SRC).parts


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
        if p.name != "__init__.py"
        and "__pycache__" not in p.parts
        and CONTENT not in p.parts
    )


def _module_id(path: Path) -> str:
    """`harness/agent.py`, not `agent.py`.

    A bare filename stops identifying a module once two layers can hold the
    same one, and the failure message has to name a file someone can open.
    """
    return path.relative_to(SRC).as_posix()


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
    """
    layer = tmp_path / "domain"
    (layer / "inner").mkdir(parents=True)
    (layer / "__init__.py").touch()
    (layer / "inner" / "__init__.py").touch()
    (layer / "inner" / "buried.py").write_text("import yaml\n", encoding="utf-8")
    (layer / "shallow.py").write_text("import json\n", encoding="utf-8")

    found = _modules_in("domain", root=tmp_path)

    assert [p.relative_to(layer).as_posix() for p in found] == ["inner/buried.py", "shallow.py"]
    assert "yaml" in _imported_modules(layer / "inner" / "buried.py")


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
#: The library sits at the root again, so this is two levels up and counted.
#:
#: It was found by marker for one change -- the directory holding both
#: `pyproject.toml` and `packages/` -- and that is worth a warning rather than
#: just deleting. Once `packages/` went, no directory in this tree matched, so
#: the walk climbed out of the checkout entirely and found the *parent* clone,
#: which still had one. The rules then read a different repository and passed.
#: CI, which has no parent clone, raised `StopIteration` instead.
#:
#: A marker only works if it cannot match somewhere else. `src/` beside a
#: `pyproject.toml` describes half the Python repositories on this disk, so
#: counting is the honest option here.
REPO = SRC.parent.parent


def _everything_that_imports_kingfisher() -> list[Path]:
    areas = ("src", "tests", "service", "evals", "spikes")
    found = [
        p
        for area in areas
        if (REPO / area).is_dir()
        for p in (REPO / area).rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    if (REPO / "main.py").exists():
        found.append(REPO / "main.py")
    return sorted(found)


def _names_a_real_module(module: str) -> bool:
    """Resolved on disk rather than imported.

    `importlib.util.find_spec` would answer the same question by executing
    every parent package on the way, which for `kingfisher.cli` means
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
    # needs one parser to do it.
    "infrastructure": frozenset({"yaml"}),
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
    "cli": frozenset({"kingfisher_service"}),
    # Nothing. The domain has a stricter rule of its own; these two are here so
    # the table is total and an unlisted area cannot mean "anything goes".
    "domain": frozenset(),
    "application": frozenset(),
    # `__init__.py` and `config.py`, which belong to no layer.
    "": frozenset(),
}


def _package_modules() -> list[Path]:
    return sorted(
        p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts and not _is_content(p)
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
    assert _undeclared({"deepagents"}, "cli") == {"deepagents"}
    assert _undeclared({"deepagents"}, "infrastructure") == {"deepagents"}
    assert _undeclared({"fastapi"}, "infrastructure/harness") == {"fastapi"}
    assert _undeclared({"yaml"}, "infrastructure/harness") == {"yaml"}

    assert _undeclared({"deepagents", "langgraph"}, "infrastructure/harness") == set()
    assert _undeclared({"yaml"}, "infrastructure") == set()
    assert _undeclared({"fastapi"}, "cli") == {"fastapi"}
    assert _undeclared({"kingfisher_service"}, "cli") == set()
    assert _undeclared({"kingfisher_service"}, "application") == {"kingfisher_service"}


def test_a_subpackage_is_judged_by_its_own_area():
    """`infrastructure/harness/agent.py` is not judged as `infrastructure/`.

    Longest match, so a subpackage can be stricter or looser than its parent
    and the order of a dict does not decide which. Shortest match would let the
    harness's eight packages leak into all thirteen flat modules.
    """
    assert _area_of(SRC / "infrastructure" / "harness" / "agent.py") == "infrastructure/harness"
    assert _area_of(SRC / "infrastructure" / "catalogue.py") == "infrastructure"
    assert _area_of(SRC / "domain" / "tool.py") == "domain"
    assert _area_of(SRC / "config.py") == ""


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
    "build_checkpointer", "build_model", "ensure_layout", "from_env",
    "normalize_answer", "protect_data", "system_prompt", "writable_data",
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
    "seed", "shipped_kinds", "Seeding", "inventory", "Inventory",
    # A renderer and a sentence. Both are what a consumer needed and neither
    # imports anything -- the cheapest names on this list.
    "offered", "SKILL_LAYOUT", "split_reference",
    # Reaching it costs nothing; calling it may write a sandbox profile,
    # which is the same light-to-reach / heavy-to-call split `inventory` has.
    "shell_confinement", "Confinement",
})

#: The rest, which genuinely need deepagents to do their job.
HEAVY_EXPORTS = frozenset({
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

    `domain.skill` owns the filename and `infrastructure.skill_store` owns the
    listing. Asserting they *agree* with a caller is tautological once the
    caller imports them; what is worth asserting is that nothing else decides.
    """
    root = Path(__file__).resolve().parent.parent
    # This package; `main.py` and `evals/` live two levels up, at the
    # repository root, and are searched from there.
    repo = REPO
    owners = {
        root / "src" / "kingfisher" / "domain" / "skill.py",
        root / "src" / "kingfisher" / "infrastructure" / "skill_store.py",
    }

    searched = [
        *(root / "src").rglob("*.py"),
        repo / "main.py",
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


def test_the_package_ships_no_assets():
    """The framework loads and composes definitions; it does not supply any.

    This asserted the opposite, and was right to: `packages = ["src/kingfisher"]`
    means anything one level up is not shipped, so seeding from an installed
    kingfisher needed the definitions inside the wheel. They are a distribution
    of their own now, found through the `kingfisher.assets` entry point, and
    this holds the framework to shipping none of them.
    """
    from kingfisher.infrastructure.catalogue import CATALOGUE_KINDS

    for kind in CATALOGUE_KINDS:
        assert not (SRC / "reference" / kind).exists(), kind


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
    from kingfisher.infrastructure import seeding

    assert (SRC / "reference" / seeding.EXAMPLE).is_file()
    with seeding.opened(seeding.PACKAGE) as root:
        assert (root / seeding.EXAMPLE).is_file()


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
    "ConfigError", "DataError", "HostPathError", "MissingStoreError", "ToolError",
})


def _error_classes() -> set[str]:
    found = set()
    for path in sorted(p for p in SRC.rglob("*.py") if not _is_content(p)):
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


#: Everything in this distribution held to the front door. `presentation` was
#: first and has left -- it is `kingfisher-service` now, and holds itself to the
#: same rule in its own tests. `cli` is what remains, and it is here for the
#: reason the other one was: the claim that any caller can seed a workspace is
#: worth something only if the command that seeds is one of them. Adding a name
#: here is what makes that checked rather than asserted, and it is why `offered`
#: and `SKILL_LAYOUT` are public.
CONSUMERS = ("cli",)


def _consumer_modules() -> list[Path]:
    return sorted(path for name in CONSUMERS for path in (SRC / name).rglob("*.py"))


def _reaches_past_the_public_api(module: str) -> bool:
    return (
        module.split(".", maxsplit=1)[0] == "kingfisher"
        and module != "kingfisher"
        and not any(module.startswith(f"kingfisher.{name}") for name in CONSUMERS)
    )


@pytest.mark.parametrize("path", _consumer_modules(), ids=_module_id)
def test_the_server_uses_the_library_only_through_its_public_api(path):
    """`from kingfisher import X`, never `from kingfisher.domain.y import X`.

    A server that reaches into `kingfisher.application.service` for something
    unexported is a server that has quietly made a private name load-bearing --
    and the next person to move it breaks an HTTP contract without touching
    anything that looks like one.
    """
    reaching = {m for m in _imported_modules(path) if _reaches_past_the_public_api(m)}
    assert not reaching, (
        f"{_module_id(path)} imports {sorted(reaching)} — the server takes `kingfisher` "
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
    for path in sorted(p for p in SRC.rglob("*.py") if not _is_content(p)):
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
PRODUCTION = ("src/kingfisher", "main.py", "evals")

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
    # The repository, not this package -- `main.py` and `evals/` live beside
    # `src/`. Named rather than recomputed, so that when the library last moved
    # this was one line to change instead of a silent walk over the wrong tree.
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


def _referenced_in_code() -> set[str]:
    """Every name production code *uses*, ignoring prose.

    Prose matters here: `withheld`'s docstring named `Capabilities.unknown`, so
    a guard counting text would have taken that mention for a caller and left
    the dead method exactly where it was.
    """
    seen: set[str] = set()
    for path in _production_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.alias):
                seen.add(node.asname or node.name.split(".")[-1])
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
    for path in sorted(p for p in SRC.rglob("*.py") if not _is_content(p)):
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


def test_every_console_script_points_at_something_that_exists():
    """A `[project.scripts]` line is only checked when somebody installs and runs.

    `kingfisher = "kingfisher.cli.__main__:main"` naming a function that is not
    there fails at the shell, for a stranger, after a pip install -- which is
    the worst place to find out and the last place we would look. Nothing
    covered this: renaming the target to `:absent` left the suite green.

    Both scripts, and by import rather than by reading the source, so a target
    that exists but cannot be imported fails here too.
    """
    import tomllib
    from importlib import import_module

    manifest = tomllib.loads((SRC.parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
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

    Nothing caught that: replacing the helper call in `main.py` with a
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
