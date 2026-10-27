"""A user's memory kept by a `MemoryStore`: loaded into the session at each turn's start,
and what the turn changed handed back at its end."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, MemoryChanges, MemoryEntry, Request
from kingfisher.core.interfaces import MemoryStore
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted

#: What the memory block says with a store and a key, and without.
USERS = "kept for their later conversations"
SESSIONS = "kept for later turns of this conversation"


class Shelf(MemoryStore):
    """A store in a dict, recording every call, merging by id as a store should."""

    def __init__(self, **users: tuple[MemoryEntry, ...]) -> None:
        self.users: dict[str, tuple[MemoryEntry, ...]] = dict(users)
        self.calls: list[tuple[Any, ...]] = []

    def key(self, caller) -> str | None:
        self.calls.append(("key",))
        return caller.context.get("user_id")

    def load(self, key: str) -> tuple[MemoryEntry, ...]:
        self.calls.append(("load", key))
        return self.users.get(key, ())

    def apply(self, key: str, changes: MemoryChanges) -> tuple[MemoryEntry, ...]:
        self.calls.append(("apply", key, changes))
        kept = {one.id: one for one in self.users.get(key, ())}
        for one in changes.removed:
            kept.pop(one.id, None)
        for one in (*changes.changed, *changes.added):
            kept[one.id] = one
        self.users[key] = tuple(kept.values())
        return self.users[key]


class Broken(Shelf):
    """A store whose `failing` method raises."""

    def __init__(self, failing: str, **users: tuple[MemoryEntry, ...]) -> None:
        super().__init__(**users)
        self.failing = failing

    def _asked(self, name: str) -> None:
        if name == self.failing:
            msg = "the store is down"
            raise ConnectionError(msg)

    def key(self, caller) -> str | None:
        self._asked("key")
        return super().key(caller)

    def load(self, key: str) -> tuple[MemoryEntry, ...]:
        self._asked("load")
        return super().load(key)

    def apply(self, key: str, changes: MemoryChanges) -> tuple[MemoryEntry, ...]:
        self._asked("apply")
        return super().apply(key, changes)


def _kingfisher(scripted, store: Any, *, memory: bool = True) -> Kingfisher:
    an_agent(scripted, **EVERY_TOOL)
    return Kingfisher(
        replace(scripted, memory_enabled=memory), sessions=LocalSessions(), memory_store=store
    )


def _turn(
    kf: Kingfisher, *script: Any, session: str = "s", user: str | None = "ana", way: str = "sync"
) -> tuple[Any, list[Any]]:
    Scripted.script.extend(script)
    context = {"user_id": user} if user is not None else {}
    request = Request("go", agent="only", session_id=session, context=context)
    if way == "async":

        async def drained() -> list[Any]:
            return [one async for one in kf.astream(request)]

        events = asyncio.run(drained())
    else:
        events = list(kf.stream(request))
    (finished,) = [one for one in events if one.kind == "finished"]
    return finished.result, events


def _adds(line: str, *, after: str = "# Memory\n") -> AIMessage:
    args = {"file_path": "/memory/AGENTS.md", "old_string": after, "new_string": f"{after}{line}\n"}
    return AIMessage(content="", tool_calls=[{"name": "edit_file", "args": args, "id": "c1"}])


def _heard() -> str:
    """Every text the model was handed on its latest call, system prompt included."""
    texts = []
    for message in Scripted.heard[-1]:
        content = message.content
        blocks = [content] if isinstance(content, str) else content
        texts += [one if isinstance(one, str) else one.get("text", "") for one in blocks]
    return " ".join(" ".join(texts).split())


def _memory_file(scripted, session: str = "s"):
    return scripted.workspace / "sessions" / session / "memory" / "AGENTS.md"


def _kinds(events: list[Any], kind: str) -> list[str]:
    return [one.text for one in events if one.kind == kind]


EUR = MemoryEntry("m1", "Likes EUR")
SHORT = MemoryEntry("m2", "Short answers")


def test_a_turn_starts_from_the_users_entries(scripted, way):
    """Loaded after the start's numbering, the user's entries would never reach the model
    this turn; loaded and not counted as the baseline, each would read as added by it."""
    shelf = Shelf(ana=(EUR, SHORT))
    kf = _kingfisher(scripted, shelf)
    start(scripted, "s")

    result, events = _turn(kf, AIMessage(content="hello"), way=way)

    assert "- [m1] Likes EUR" in _heard() and "- [m2] Short answers" in _heard()
    assert _memory_file(scripted).read_text(encoding="utf-8") == (
        "# Memory\n\n- [m1] Likes EUR\n- [m2] Short answers\n"
    )
    assert not result.memory
    assert not _kinds(events, "memory_updated")
    # Nothing changed, so nothing is handed back.
    assert [call[0] for call in shelf.calls] == ["key", "load"]


def test_an_entry_the_agent_adds_reaches_the_store_as_added(scripted, way):
    """Only what changed is handed over: a whole list would undo what another session of
    the user saved since this one loaded."""
    shelf = Shelf(ana=(EUR,))
    kf = _kingfisher(scripted, shelf)
    start(scripted, "s")

    result, _ = _turn(
        kf, _adds("- Reviews on Mondays", after="- [m1] Likes EUR\n"), AIMessage("noted"), way=way
    )

    added = MemoryEntry("m2", "Reviews on Mondays")
    assert result.memory.added == (added,)
    assert shelf.calls[-1] == ("apply", "ana", MemoryChanges(added=(added,)))
    assert shelf.users["ana"] == (EUR, added)


def test_two_sessions_of_one_user_share_their_memory(scripted):
    """Read once when a session opened, or never reloaded, one conversation would not see
    what the other learnt, and its saves would carry a list the other had moved past."""
    shelf = Shelf()
    kf = _kingfisher(scripted, shelf)
    start(scripted, "one")
    start(scripted, "two")

    _turn(kf, _adds("- Likes EUR"), AIMessage("noted"), session="one")
    _turn(kf, _adds("- Short answers", after="- [m1] Likes EUR\n"), AIMessage("ok"), session="two")
    assert "- [m1] Likes EUR" in _heard()

    _turn(kf, AIMessage("hello"), session="one")

    assert "- [m2] Short answers" in _heard()
    assert shelf.users["ana"] == (EUR, SHORT)


def test_the_agent_is_told_whose_memory_it_is(scripted):
    """The block promising later conversations where nothing keeps them had the agent
    writing for a reader who never arrives; never promising them where a store does
    leaves it saving as if the user started afresh each time."""
    start(scripted, "s")
    start(scripted, "t")
    _turn(_kingfisher(scripted, Shelf()), AIMessage("hello"))
    users = _heard()
    _turn(_kingfisher(scripted, None), AIMessage("hello"), session="t")
    sessions = _heard()

    assert USERS in users and SESSIONS not in users
    assert SESSIONS in sessions and USERS not in sessions


def test_a_request_naming_no_user_keeps_the_sessions_memory_and_says_so(scripted):
    """Refusing it would make a user id a requirement of every request; keeping it quiet
    would leave a deployment believing the turn's memory was the user's."""
    shelf = Shelf(ana=(EUR,))
    kf = _kingfisher(scripted, shelf)
    start(scripted, "s")

    result, events = _turn(
        kf, _adds("- Short answers", after="# Project memory\n"), AIMessage("noted"), user=None
    )

    assert _kinds(events, "memory_unkeyed") == [
        "Shelf named no user, so this turn keeps the session's memory"
    ]
    assert result.memory.added == (MemoryEntry("m1", "Short answers"),)
    assert shelf.calls == [("key",)]
    assert "Likes EUR" not in _heard() and SESSIONS in _heard()


@pytest.mark.parametrize("failing", ["key", "load", "apply"])
def test_a_failing_store_never_fails_the_turn(scripted, failing):
    """A store that is down would otherwise cost the user every answer, over memory the
    session still holds a copy of."""
    shelf = Broken(failing, ana=(MemoryEntry("m1", "Likes GBP"), MemoryEntry("m2", "Stored")))
    kf = _kingfisher(scripted, shelf)
    start(scripted, "s")
    _memory_file(scripted).write_text("# Memory\n\n- [m1] Likes GBP\n", encoding="utf-8")

    result, events = _turn(
        kf, _adds("- Short answers", after="- [m1] Likes GBP\n"), AIMessage("noted")
    )

    assert result.answer == "noted"
    (said,) = _kinds(events, "memory_store_failed")
    assert said.startswith(f"Broken.{failing} raised ConnectionError: the store is down; ")
    # Where nothing was loaded the agent read the session's copy, and it keeps what was saved.
    assert ("- [m2] Stored" in _heard()) == (failing == "apply")
    assert "Short answers" in _memory_file(scripted).read_text(encoding="utf-8")


def test_changes_made_on_the_sessions_copy_still_reach_the_store(scripted):
    """A load that failed leaves the copy the last turn wrote, whose ids are the user's,
    so what this turn adds to it is theirs to keep."""
    shelf = Broken("load", ana=(EUR,))
    kf = _kingfisher(scripted, shelf)
    start(scripted, "s")
    _memory_file(scripted).write_text("# Memory\n\n- [m1] Likes EUR\n", encoding="utf-8")

    _turn(kf, _adds("- Short answers", after="- [m1] Likes EUR\n"), AIMessage("noted"))

    assert shelf.users["ana"] == (EUR, SHORT)


def test_with_memory_off_the_store_is_never_asked(scripted):
    """Off is off: a deployment switching memory away would still have its users' memory
    loaded into every session, and every key asked for."""
    shelf = Shelf(ana=(EUR,))
    kf = _kingfisher(scripted, shelf, memory=False)
    start(scripted, "s")

    _turn(kf, AIMessage("hello"))

    assert shelf.calls == []
    assert "Likes EUR" not in _memory_file(scripted).read_text(encoding="utf-8")


def test_an_agent_declining_memory_is_never_asked_about(scripted):
    """`memory: false` opts an agent out, and a store asked anyway would write the user's
    memory into a file the agent was told nothing of."""
    shelf = Shelf(ana=(EUR,))
    an_agent(scripted, memory="false", **EVERY_TOOL)
    kf = Kingfisher(
        replace(scripted, memory_enabled=True), sessions=LocalSessions(), memory_store=shelf
    )
    start(scripted, "s")

    _turn(kf, AIMessage("hello"))

    assert shelf.calls == []


def test_an_entry_the_identifier_check_drops_leaves_the_store_too(scripted):
    """The check reports what it takes out as dropped, not removed; handed to the store as
    it is, the entry stayed in the user's memory and the next turn's load brought it back."""
    team = MemoryEntry("m2", "Works in Finance Ops")
    shelf = Shelf(ana=(EUR, team))
    kf = _kingfisher(scripted, shelf)
    start(scripted, "s")
    Scripted.script.extend([AIMessage("hello")])

    events = list(
        kf.stream(
            Request(
                "go",
                agent="only",
                session_id="s",
                context={"user_id": "ana", "team": "Finance Ops"},
            )
        )
    )

    (finished,) = [one for one in events if one.kind == "finished"]
    assert finished.result.memory.dropped == (team,)
    assert shelf.users["ana"] == (EUR,)
