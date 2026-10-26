"""A hook passed to `Kingfisher(...)` is refused at startup when it lacks a method its
protocol lists."""

from __future__ import annotations

import inspect
from typing import Any, Protocol

import pytest

from kingfisher import Kingfisher
from kingfisher.core import interfaces
from kingfisher.core.agent.prompting import InstructionsFile
from kingfisher.core.interfaces import (
    AgentsMiddlewares,
    NoScratchRunLimit,
    Sessions,
)
from kingfisher.core.session.runlog import LoggedRunEvents
from kingfisher.core.session.unused import DeleteUnusedResults
from kingfisher.plugins import LocalSessions

#: Each hook keyword and the protocol it takes.
HOOKS: dict[str, type] = {
    "run_events": interfaces.RunEvents,
    "instructions": interfaces.Instructions,
    "turn_instructions": interfaces.TurnInstructions,
    "unused_results": interfaces.UnusedResults,
    "pdf_attachments": interfaces.PdfAttachments,
    "subagent_middlewares": interfaces.SubagentMiddlewares,
    "scratch_run_limit": interfaces.ScratchRunLimit,
}


def _methods(protocol: type) -> list[str]:
    return sorted(
        name
        for name, value in vars(protocol).items()
        if callable(value) and not name.startswith("_")
    )


class _Nothing:
    """A hook with no method at all."""


class _TurnText:
    def text(self, cfg: Any, session_id: str, caller: Any) -> str:
        return ""


class _KeepVerdict:
    def replacement(self, pdf: Any) -> None:
        return None


def test_every_hook_keyword_is_checked_here():
    """Read off the signature, so a hook keyword added later without a case here fails
    rather than going unchecked."""
    protocols = {
        name
        for name, one in vars(interfaces).items()
        if Protocol in getattr(one, "__mro__", ()) and one is not Sessions
    }
    # Annotations stay strings under `from __future__ import annotations`.
    taken = {
        name
        for name, parameter in inspect.signature(Kingfisher.__init__).parameters.items()
        if set(str(parameter.annotation).split(" | ")) & protocols
    }

    assert "RunEvents" in protocols, "the control: the protocols were found at all"
    assert taken == set(HOOKS)


@pytest.mark.parametrize(("keyword", "protocol"), HOOKS.items(), ids=HOOKS.keys())
def test_a_hook_missing_its_method_is_refused_at_startup(cfg, keyword, protocol):
    """A hook missing a method was an `AttributeError` in whichever turn first asked,
    possibly hours after the deployment started."""
    # Typed loosely on purpose: the type checker refuses exactly what this hands over.
    wired: dict[str, Any] = {keyword: _Nothing()}
    with pytest.raises(TypeError) as caught:
        Kingfisher(cfg, sessions=LocalSessions(), **wired)

    said = str(caught.value)
    assert f"{keyword}= takes a {protocol.__name__}" in said
    assert all(method in said for method in _methods(protocol)), said


@pytest.mark.parametrize(
    ("keyword", "hook"),
    [
        ("run_events", LoggedRunEvents()),
        ("instructions", InstructionsFile()),
        ("turn_instructions", _TurnText()),
        ("unused_results", DeleteUnusedResults()),
        ("pdf_attachments", _KeepVerdict()),
        ("subagent_middlewares", AgentsMiddlewares()),
        ("scratch_run_limit", NoScratchRunLimit()),
    ],
    ids=lambda one: one if isinstance(one, str) else "",
)
def test_a_complete_hook_is_taken_subclassing_nothing(cfg, keyword, hook):
    """The control: a check that refused kingfisher's own defaults, or a deployment's
    class subclassing nothing, would pass the refusals above for the wrong reason."""
    Kingfisher(cfg, sessions=LocalSessions(), **{keyword: hook})
