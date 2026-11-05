"""An async method is its sync twin with `await` added, unless `DIFFERS` says why not."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator

import pytest

from tests.conftest import repository_root

SRC = repository_root() / "src" / "kingfisher"

_DEFAULT = "a protocol default, so a plugin writing only the sync method still serves `arun`"
_CANCELLED = "a cancelled task has to end the turn it interrupted, and only an awaited turn is"
_NO_ASYNC_LAMBDA = "the handler it passes on is awaited, and Python has no async lambda"

#: Each pair whose async half does what the sync half does not, and why. Every other pair
#: has to be a copy: that is what lets a behaviour test drive the sync half alone.
DIFFERS = {
    "core/interfaces.py:Sessions.aopen": _DEFAULT,
    "core/interfaces.py:Sessions.asessions": _DEFAULT,
    "core/interfaces.py:Sessions.adelete": _DEFAULT,
    "core/interfaces.py:Sessions.amark_used": _DEFAULT,
    "core/interfaces.py:Sessions.asize": _DEFAULT,
    "core/interfaces.py:Sessions.aclaim": _DEFAULT,
    "core/interfaces.py:Sessions.arelease": _DEFAULT,
    "core/session/steps.py:adrive": _CANCELLED,
    "core/session/running.py:Running._aend_turn": _CANCELLED,
    "core/session/running.py:Running._astream_turn": _CANCELLED,
    "core/session/running.py:Running.astream": (
        "an async generator has no `yield from`, and closes the turn it wraps itself"
    ),
    "core/agent/middlewares/caching.py:PromptCachingMiddleware.awrap_model_call": (
        _NO_ASYNC_LAMBDA
    ),
    "core/agent/middlewares/turn_instructions.py:TurnInstructionsMiddleware.awrap_model_call": (
        _NO_ASYNC_LAMBDA
    ),
    "core/agent/middlewares/attachments.py:AttachmentsMiddleware.awrap_tool_call": (
        "reading a PDF blocks, so the awaited call reads one on a thread"
    ),
    "core/agent/middlewares/scratch_runs.py:RefusingRunner.arun": (
        "it refuses before any work is done, so it has nothing to await"
    ),
    "core/agent/scratch.py:ScratchRunner.arun": (
        "a backend's own async method is preferred to its sync one on a thread"
    ),
    "core/agent/tool_guards.py:GuardedTool._arun": (
        "a wrapped tool with no async run of its own is run through its sync one"
    ),
}


def _async_spelling(sync: str) -> str:
    """`aopen` for `open`, `_arun` for `_run`."""
    return f"_a{sync[1:]}" if sync.startswith("_") else f"a{sync}"


def _same(asynchronous: object, sync: object) -> bool:
    """Whether `asynchronous` is `sync` with only `await` and async spellings added."""
    if isinstance(asynchronous, ast.Await):
        return _same(asynchronous.value, sync)
    if isinstance(asynchronous, ast.AsyncFor) and isinstance(sync, ast.For):
        return _same_fields(asynchronous, sync, ("target", "iter", "body", "orelse"))
    if isinstance(asynchronous, list) and isinstance(sync, list):
        return len(asynchronous) == len(sync) and all(map(_same, asynchronous, sync))
    if isinstance(asynchronous, ast.AST) and isinstance(sync, ast.AST):
        return type(asynchronous) is type(sync) and _same_fields(
            asynchronous, sync, asynchronous._fields
        )
    return type(asynchronous) is type(sync) and asynchronous == sync


def _same_fields(asynchronous: ast.AST, sync: ast.AST, fields: Iterable[str]) -> bool:
    return all(
        _same_field(field, getattr(asynchronous, field), getattr(sync, field)) for field in fields
    )


def _same_field(field: str, mine: object, theirs: object) -> bool:
    """A name may be the async spelling of its sync one: `aupload_files`, `_arun`."""
    if field in {"id", "attr", "name"} and isinstance(theirs, str):
        return mine in (theirs, _async_spelling(theirs))
    return _same(mine, theirs)


def _body(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """Its statements, less the docstring, which says `run` in one and `arun` in the other."""
    first = function.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return function.body[1:]
    return function.body


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Names and defaults, not the hints: an awaited handler is typed differently."""
    shape = ast.arguments(
        posonlyargs=[ast.arg(one.arg) for one in function.args.posonlyargs],
        args=[ast.arg(one.arg) for one in function.args.args],
        vararg=function.args.vararg and ast.arg(function.args.vararg.arg),
        kwonlyargs=[ast.arg(one.arg) for one in function.args.kwonlyargs],
        kw_defaults=function.args.kw_defaults,
        kwarg=function.args.kwarg and ast.arg(function.args.kwarg.arg),
        defaults=function.args.defaults,
    )
    return ast.dump(shape)


def _is_a_copy(asynchronous: ast.AsyncFunctionDef, sync: ast.FunctionDef) -> bool:
    """`sync`, with at least one `await` added: a twin that awaits nothing hands back a
    coroutine where its sync half hands back a result."""
    awaits = any(isinstance(one, ast.Await | ast.AsyncFor) for one in ast.walk(asynchronous))
    return (
        awaits
        and _parameters(asynchronous) == _parameters(sync)
        and _same(_body(asynchronous), _body(sync))
    )


def _pairs_in(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef, ast.AsyncFunctionDef]]:
    """Each sync definition beside an async one of its async spelling, by qualified name."""
    classes = [one for one in ast.walk(tree) if isinstance(one, ast.ClassDef)]
    for prefix, body in [("", tree.body), *((f"{one.name}.", one.body) for one in classes)]:
        defined = {
            one.name: one for one in body if isinstance(one, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        for name, sync in defined.items():
            twin = defined.get(_async_spelling(name))
            if isinstance(sync, ast.FunctionDef) and isinstance(twin, ast.AsyncFunctionDef):
                yield f"{prefix}{twin.name}", sync, twin


def _pairs() -> dict[str, tuple[ast.FunctionDef, ast.AsyncFunctionDef]]:
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = path.relative_to(SRC).as_posix()
        for name, sync, twin in _pairs_in(ast.parse(path.read_text(encoding="utf-8"))):
            found[f"{module}:{name}"] = (sync, twin)
    return found


def test_an_async_method_is_its_sync_twin_with_await_added():
    """A step changed in one half of a pair and not the other, which a behaviour test
    driving the sync half alone never sees."""
    pairs = _pairs()
    copies = {at: pair for at, pair in pairs.items() if at not in DIFFERS}

    drifted = sorted(at for at, (sync, twin) in copies.items() if not _is_a_copy(twin, sync))

    assert copies, "no pair was found, so this rule passed against nothing"
    assert not drifted, (
        f"{drifted} no longer do what their sync halves do -- make the two agree, or add "
        "the pair to DIFFERS with the reason its async half must differ"
    )


def test_every_pair_excused_still_differs():
    """An excuse left behind once its pair became a copy, or went, would wave through the
    next change made to it."""
    pairs = _pairs()

    stale = sorted(
        at for at in DIFFERS if at not in pairs or _is_a_copy(pairs[at][1], pairs[at][0])
    )

    assert not stale, f"{stale} are copies now, or gone -- take them out of DIFFERS"


#: One way each that a twin can do what its sync half does not.
CHANGED = {
    "a step added after the last": (
        "def run(h, x):\n    h(x)",
        "async def arun(h, x):\n    await h(x)\n    h(x)",
    ),
    "a value changed": (
        "def run(h, x):\n    return h(x, 1)",
        "async def arun(h, x):\n    return await h(x, 2)",
    ),
    "another method called in a loop": (
        "def run(self, x):\n    for one in x:\n        self.save(one)",
        "async def arun(self, x):\n    async for one in x:\n        await self.bsave(one)",
    ),
    "an await dropped": (
        "def run(h, x):\n    return h(x)",
        "async def arun(h, x):\n    return h(x)",
    ),
    "a default changed": (
        "def run(h, x=1):\n    return h(x)",
        "async def arun(h, x=2):\n    return await h(x)",
    ),
}


@pytest.mark.parametrize("change", sorted(CHANGED), ids=sorted(CHANGED))
def test_the_twin_rule_sees_each_way_a_twin_can_differ(change):
    """A comparison loose at any one of these calls a changed twin a copy, and still passes
    against a tree in which every twin is one."""
    sync, asynchronous = (ast.parse(source).body[0] for source in CHANGED[change])
    assert isinstance(sync, ast.FunctionDef) and isinstance(asynchronous, ast.AsyncFunctionDef)

    assert not _is_a_copy(asynchronous, sync)
