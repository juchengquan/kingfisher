"""A hook passed to `Kingfisher(...)` is refused at startup unless it subclasses its
interface."""

from __future__ import annotations

from typing import Any

import pytest

from kingfisher import Kingfisher
from kingfisher.core import interfaces
from kingfisher.core.agent.prompting import InstructionsFile
from kingfisher.core.interfaces import (
    AgentsMiddlewares,
    NoScratchRunLimit,
    hooks_of,
)
from kingfisher.core.session.runlog import LoggedRunEvents
from kingfisher.core.session.unused import DeleteUnusedResults
from kingfisher.plugins import LocalSessions

#: Each hook keyword and the interface it takes.
HOOKS: dict[str, type] = {
    "run_events": interfaces.RunEvents,
    "instructions": interfaces.Instructions,
    "turn_instructions": interfaces.TurnInstructions,
    "unused_results": interfaces.UnusedResults,
    "pdf_attachments": interfaces.PdfAttachments,
    "subagent_middlewares": interfaces.SubagentMiddlewares,
    "scratch_run_limit": interfaces.ScratchRunLimit,
    "memory_store": interfaces.MemoryStore,
}


class _Everything:
    """Every method of every hook, subclassing none of them."""

    def record(self, event: Any) -> None: ...
    def text(self, *_: Any) -> str:
        return ""

    def settle(self, found: Any) -> None: ...
    def replacement(self, pdf: Any) -> None: ...
    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Any:
        return middlewares

    def allows(self, name: str, kind: Any) -> bool:
        return True

    def key(self, caller: Any) -> None: ...
    def load(self, key: str) -> tuple[()]:
        return ()

    def apply(self, key: str, changes: Any) -> tuple[()]:
        return ()


class _TurnText(interfaces.TurnInstructions):
    def text(self, cfg: Any, session_id: str, caller: Any) -> str:
        return ""


class _KeepVerdict(interfaces.PdfAttachments):
    def replacement(self, pdf: Any) -> None:
        return None


def test_the_check_finds_every_hook_on_kingfisher_from_its_signature():
    """A hook the signature reading missed would start unchecked, and one it took that is
    not a hook -- `sessions`, an interface too -- would be checked twice over."""
    assert hooks_of(Kingfisher.__init__) == HOOKS


@pytest.mark.parametrize(("keyword", "interface"), HOOKS.items(), ids=HOOKS.keys())
def test_a_hook_not_subclassing_its_interface_is_refused_at_startup(cfg, keyword, interface):
    """Driven with a hook holding every method, so what is refused is the missing base:
    without it Python never checked the hook's methods, and one it lacked was an
    `AttributeError` in whichever turn first asked, possibly hours after startup."""
    # Typed loosely on purpose: the type checker refuses exactly what this hands over.
    wired: dict[str, Any] = {keyword: _Everything()}
    with pytest.raises(TypeError) as caught:
        Kingfisher(cfg, sessions=LocalSessions(), **wired)

    said = str(caught.value)
    assert f"{keyword}= takes a {interface.__name__}" in said
    assert "_Everything does not subclass it" in said


@pytest.mark.parametrize("interface", HOOKS.values(), ids=HOOKS.keys())
def test_a_subclass_missing_a_method_cannot_be_made(interface):
    """`Kingfisher` asks only for the subclass, so a method left without
    `@abstractmethod` would be an `AttributeError` mid-turn again."""
    lacking = type("Lacking", (interface,), {})
    with pytest.raises(TypeError, match="abstract"):
        lacking()


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
def test_a_subclass_is_taken(cfg, keyword, hook):
    """The control: a check that refused kingfisher's own defaults, or a deployment's
    subclass, would pass the refusals above for the wrong reason."""
    Kingfisher(cfg, sessions=LocalSessions(), **{keyword: hook})
