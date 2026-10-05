"""Session I/O as sequences: what the two drivers do, and that every sequence is driven."""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path

import pytest

from kingfisher.infrastructure.steps import adrive, changing, drive, on_host, reading

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "kingfisher"
CALLERS = [ROOT / d for d in ("src", "tests", "spikes", "evals", "assets_examples")]
DRIVERS = {"drive", "adrive", "adrive_finished"}


class _Port:
    """A port whose async calls wait on `gate`, so a test can cancel during one."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.gate = asyncio.Event()

    def claim(self) -> bool:
        self.log.append("claim")
        return True

    async def aclaim(self) -> bool:
        self.log.append("aclaim started")
        await self.gate.wait()
        self.log.append("aclaim finished")
        return True

    def look(self) -> bytes:
        return b"held"

    async def alook(self) -> bytes:
        await self.gate.wait()
        await asyncio.sleep(10)
        return b"held"

    def refuse(self) -> None:
        msg = "refused"
        raise ValueError(msg)

    async def arefuse(self) -> None:
        msg = "refused"
        raise ValueError(msg)

    def release(self) -> None:
        self.log.append("release")


def _claimed_then_read(port: _Port):
    yield changing(port, "claim")
    try:
        return (yield reading(port, "look"))
    except BaseException:
        port.release()
        raise


def _refused_inside(port: _Port):
    try:
        yield reading(port, "refuse")
    except ValueError as exc:
        return f"seen by the sequence: {exc}"


# -- the drivers ---------------------------------------------------------------


def test_both_drivers_make_the_same_calls_and_return_the_same_answer():
    """One sequence, two drivers: the sync one calls `claim`, the async one `aclaim`."""
    port = _Port()
    port.gate.set()

    async def fast_fetch() -> bytes:
        return b"held"

    port.alook = fast_fetch  # type: ignore[method-assign]

    assert drive(_claimed_then_read(port)) == b"held"
    assert asyncio.run(adrive(_claimed_then_read(port))) == b"held"
    assert port.log == ["claim", "aclaim started", "aclaim finished"]


@pytest.mark.parametrize("driver", ["sync", "async"])
def test_a_failing_call_is_thrown_into_the_sequence(driver):
    """Raised past the sequence, its own `except` never ran -- and that `except` is
    where a sequence that took a claim gives it back.
    """
    port = _Port()
    run = drive if driver == "sync" else (lambda steps: asyncio.run(adrive(steps)))

    assert run(_refused_inside(port)) == "seen by the sequence: refused"


def test_a_cancel_during_a_call_that_changes_something_waits_for_it():
    """Abandoned mid-call, a claim the backend took would never reach the sequence that
    has to give it back -- the setup bug, one level down.
    """
    port = _Port()

    async def cancel_during_the_claim() -> None:
        task = asyncio.ensure_future(adrive(_claimed_then_read(port)))
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        port.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_the_claim())

    assert port.log == ["aclaim started", "aclaim finished", "release"]


def test_a_cancel_during_a_read_abandons_it_and_still_reaches_the_sequence():
    """A read changes nothing, so waiting it out buys nothing; the sequence still has to
    hear of the cancel, or what it holds stays held.
    """
    port = _Port()
    port.gate.set()

    async def cancel_during_the_read() -> None:
        task = asyncio.ensure_future(adrive(_claimed_then_read(port)))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

    asyncio.run(cancel_during_the_read())

    assert port.log == ["aclaim started", "aclaim finished", "release"]


def test_work_on_the_host_runs_inline_sync_and_on_the_pool_async():
    """On the loop's own thread, disk and CPU work would stall every other turn."""

    def where():
        return (yield on_host(lambda: threading.current_thread().name))

    assert drive(where()) == threading.current_thread().name
    assert asyncio.run(adrive(where())).startswith("kingfisher")


def test_a_sequence_yielded_rather_than_delegated_to_is_refused_by_name():
    """`yield inner()` hands the driver a generator; made as a call it would be silently
    nothing.
    """

    def forgot_from():
        yield _refused_inside(_Port())

    with pytest.raises(TypeError, match="goes under `yield from`, not `yield`"):
        drive(forgot_from())


# -- every sequence is driven --------------------------------------------------


def _sequences() -> dict[str, list[str]]:
    """Every function under `src/` annotated as returning `Steps`, by name."""
    found: dict[str, list[str]] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.FunctionDef)
                and node.returns is not None
                and ast.unparse(node.returns).startswith("Steps[")
            ):
                found.setdefault(node.name, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def _callee(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_a_sequence_s_name_is_not_also_a_plain_function_s():
    """The rule below finds a sequence's calls by name. A plain function sharing one
    would be read as a sequence called without a driver, or would hide one that is.
    """
    sequences = _sequences()
    plain = {
        node.name
        for path in SRC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in sequences
        and not (node.returns is not None and ast.unparse(node.returns).startswith("Steps["))
    }

    assert not plain, f"{sorted(plain)} name both a sequence and a plain function; rename one"


def test_every_sequence_is_driven_or_delegated_to():
    """A sequence called like a function does nothing. The test helper `pin` called
    `remember_agent(harness, document)` as a statement once these became sequences, no
    agent was pinned, and the turns that relied on one were told they named no agent.
    """
    sequences = _sequences()
    # The control beside the escape: a rule that collected nothing would pass
    # against every forgotten call there is.
    assert {"read_pause_mark", "remember_agent", "fetch", "_files_for"} <= set(sequences)

    undriven: list[str] = []
    for root in CALLERS:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {
                child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and _callee(node) in sequences):
                    continue
                parent = parents.get(node)
                delegated = isinstance(parent, ast.YieldFrom)
                driven = (
                    isinstance(parent, ast.Call)
                    and _callee(parent) in DRIVERS
                    and parent.args[:1] == [node]
                )
                if not (delegated or driven):
                    undriven.append(f"{path.relative_to(ROOT)}:{node.lineno} {_callee(node)}")

    assert not undriven, (
        "a sequence called without `yield from`, `drive(...)` or `adrive(...)` does "
        f"nothing: {undriven}"
    )
