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
                offenders.append(f"application/{path.name}:{node.lineno} imports shutil")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in MUTATING_CALLS
            ):
                offenders.append(f"application/{path.name}:{node.lineno} .{node.func.attr}()")

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
        "LocalSkillRepository.names so the inventory and the validator cannot disagree"
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
    from kingfisher.infrastructure.catalogue import CATALOGUE_KINDS

    assert (SRC / "presets" / "skills").is_dir()
    # And reachable the way an installed one reaches them, not by path.
    with presets.opened() as root:
        for kind in CATALOGUE_KINDS:
            assert (root / kind).is_dir(), kind


def test_the_package_ships_the_catalogue_example():
    """The same rule as above, for the file a deployment needs *first*.

    `models.yaml` is required and has no fallback, so the worked example is the
    one document a new deployment cannot start without reading. It lived at the
    repo root -- outside `packages = ["src/kingfisher"]` -- which meant a
    pip-installed kingfisher shipped a required format with no example of it,
    and nothing noticed. Exactly the mistake the test above was written about,
    one directory over.
    """
    from kingfisher.infrastructure import presets

    assert (SRC / "presets" / presets.EXAMPLE).is_file()
    with presets.opened() as root:
        assert (root / presets.EXAMPLE).is_file()


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
# `kingfisher.server` ships in this distribution and is separated from the
# library by these two rules rather than by intention. The point is not tidiness:
# it puts the server on the same footing as anybody outside the package, so when
# it needs something the library does not export, the answer is to export it
# deliberately. Three things came out that way before the server existed -- the
# caller-facing errors, `async_checkpointer`, and a way to send a file.


def _server_modules() -> list[Path]:
    return sorted((SRC / "server").rglob("*.py"))


def _reaches_past_the_public_api(module: str) -> bool:
    return (
        module.split(".", maxsplit=1)[0] == "kingfisher"
        and module != "kingfisher"
        and not module.startswith("kingfisher.server")
    )


@pytest.mark.parametrize("path", _server_modules(), ids=lambda p: p.name)
def test_the_server_uses_the_library_only_through_its_public_api(path):
    """`from kingfisher import X`, never `from kingfisher.domain.y import X`.

    A server that reaches into `kingfisher.application.service` for something
    unexported is a server that has quietly made a private name load-bearing --
    and the next person to move it breaks an HTTP contract without touching
    anything that looks like one.
    """
    reaching = {m for m in _imported_modules(path) if _reaches_past_the_public_api(m)}
    assert not reaching, (
        f"server/{path.name} imports {sorted(reaching)} — the server takes `kingfisher` "
        "and nothing deeper; if it needs something private, export it on purpose"
    )


@pytest.mark.parametrize(
    "path",
    [p for layer in ("domain", "application", "infrastructure") for p in _modules_in(layer)]
    + [SRC / "__init__.py", SRC / "config.py"],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_no_part_of_the_library_imports_the_server(path):
    """The outward half. Dependencies point one way, and a library that imports
    its own server makes the extra a lie -- `pip install kingfisher` would fail
    at import without fastapi."""
    modules = _imported_modules(path)
    assert not any(m.startswith("kingfisher.server") for m in modules), (
        f"{path.parent.name}/{path.name} imports kingfisher.server — the library "
        "does not know its server exists"
    )


#: The synchronous pair. On an event loop these do not merely block one
#: request, they block every other turn sharing the process.
BLOCKING_METHODS = frozenset({"run", "stream"})

#: Receivers whose `run` is not `Kingfisher.run`. Named one by one rather than
#: loosening the rule, because the rule is worth exactly as much as the list is
#: short: `uvicorn.run` is how the server is served, and it is not the
#: loop-blocking mistake this watches for.
NOT_KINGFISHER = frozenset({"uvicorn"})


@pytest.mark.parametrize("path", _server_modules(), ids=lambda p: p.name)
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
        f"server/{path.name} calls {offenders} — use arun/astream; the sync pair "
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


def test_every_caller_facing_error_has_a_status():
    """The half phase 1 could not check yet.

    `CALLER_FACING_ERRORS` says which errors a caller can cause;
    `errors.STATUS` says what each becomes on the wire. Nothing but this keeps
    them the same set -- and the failure is quiet in both directions. An error
    classified caller-facing but absent from the map is a 500 for something the
    caller could fix; one in the map but not classified is a status nobody
    decided on.
    """
    from kingfisher.server.errors import STATUS

    mapped = {error.__name__ for error in STATUS}

    assert mapped == CALLER_FACING_ERRORS, (
        "every caller-facing error needs a status and code, and nothing else "
        "belongs in the map — a deployment error is a 500 on purpose"
    )


def test_no_two_refusals_share_a_code():
    """The code is what a client branches on, so two refusals answering the
    same code are two things it cannot tell apart. Statuses may repeat --
    `bad_reference`, `bad_skill` and `bad_subagent` are all 400 -- which is
    exactly why the code carries the meaning."""
    from kingfisher.server.errors import CODE_FOR_STATUS, STATUS

    codes = [code for _, code in STATUS.values()] + list(CODE_FOR_STATUS.values())

    assert len(codes) == len(set(codes)), sorted(codes)
