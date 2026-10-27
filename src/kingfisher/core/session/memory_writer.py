"""The memory writer: one model call at a turn's end that rewrites the session's memory
entries from what was said since it last ran, and the identifier check every save passes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from kingfisher.core.agent.models import model_named
from kingfisher.core.agent.prompting import memory_writer_prompt
from kingfisher.core.layout import MEMORY_WRITER
from kingfisher.core.result import MemoryChanges, MemoryEntry, RunEvent

from . import runtime
from .memory import changes, listed, memory_text, with_entries, without_entries
from .steps import Steps, changing, on_host

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.core.config import Config
    from kingfisher.core.layout import Layout

    from .turn import Prepared

#: What the writer's answer is held to, and told. Over either, it is not kept.
MAX_ENTRIES = 40
MAX_CHARACTERS = 6000

#: A `context` value shorter than this is checked for by nobody: "en" or "42" is inside
#: too many entries that identify no one.
SHORTEST_IDENTIFIER = 4


def writer_model(cfg: Config, running: Any, endpoints: Any) -> Callable[[], Any]:
    """What builds the writer's model: `models.yaml`'s `memory_writer:`, else `running`,
    the agent's own."""
    named = cfg.models.memory_writer
    if named is None:
        return lambda: running
    return lambda: model_named(
        named, cfg, endpoints=endpoints, subject=f"the memory writer's model {named!r}"
    )


def identifying(
    entries: tuple[MemoryEntry, ...], context: Mapping[str, str]
) -> tuple[tuple[MemoryEntry, ...], tuple[MemoryEntry, ...]]:
    """`entries` split into those holding no value of `context` and those holding one,
    compared ignoring case.

    Matched against the values rather than told to the model: told not to save the
    user's team, a model saved it.
    """
    values = [
        value.strip().casefold()
        for value in context.values()
        if len(value.strip()) >= SHORTEST_IDENTIFIER
    ]
    held = tuple(one for one in entries if any(v in one.text.casefold() for v in values))
    return tuple(one for one in entries if one not in held), held


@dataclass(frozen=True)
class _Unread:
    """What the writer has not read yet, kept under `/.harness` between turns."""

    #: Turns ended with memory on since the writer last ran.
    turns: int = 0
    #: What was said in them, a line each, as the writer is shown it.
    said: tuple[str, ...] = ()
    #: How many messages the paused turn's state held, which its resume carries on from.
    paused_at: int | None = None


def _not_yet_read(harness: Any) -> Steps[_Unread]:
    held = yield from harness.fetch(MEMORY_WRITER)
    if held is None:
        return _Unread()
    try:
        raw = json.loads(held)
        paused_at = raw.get("paused_at")
        return _Unread(
            turns=int(raw.get("turns", 0)),
            said=tuple(str(line) for line in raw.get("said", ())),
            paused_at=paused_at if isinstance(paused_at, int) else None,
        )
    # Unreadable is nothing unread: a memory a turn behind beats a turn that fails.
    except (ValueError, TypeError, AttributeError):
        return _Unread()


def _said_this_turn(
    messages: list[Any] | None, *, since: int, task: str, answer: str
) -> tuple[str, ...]:
    """The user's task, then each of the agent's replies and the tools it called.

    Never a tool's result, and never the message the agent was sent, which carries
    kingfisher's lines after the task: only what the user and the agent said.
    """
    lines = [f"User: {task}"] if task else []
    if messages is None:
        # A graph that keeps no state: its answer is all that is known of the turn.
        return (*lines, f"Agent: {answer}") if answer else tuple(lines)
    for message in messages[since:]:
        if message.role != "assistant":
            continue
        if message.content.strip():
            lines.append(f"Agent: {message.content.strip()}")
        if message.tool_calls:
            lines.append(f"Agent called: {', '.join(call.name for call in message.tool_calls)}")
    return tuple(lines)


def _asked(
    build: Callable[[], Any] | None,
    layout: Layout,
    entries: tuple[MemoryEntry, ...],
    said: tuple[str, ...],
) -> str:
    """The writer's answer, as text."""
    if build is None:  # pragma: no cover -- memory is off for a graph the caller built
        msg = "no model to run it on"
        raise RuntimeError(msg)
    shown = "\n".join(f"- [{one.id}] {one.text}" for one in entries) or "(empty)"
    conversation = "\n".join(said) or "(nothing)"
    reply = build().invoke(
        [
            SystemMessage(
                memory_writer_prompt(layout, entries=MAX_ENTRIES, characters=MAX_CHARACTERS)
            ),
            HumanMessage(
                f"<memory>\n{shown}\n</memory>\n\n<conversation>\n{conversation}\n</conversation>"
            ),
        ]
    )
    return reply.text


def _over(entries: tuple[MemoryEntry, ...]) -> str | None:
    """Why `entries` are over the writer's limits, or `None` where they are not."""
    if len(entries) > MAX_ENTRIES:
        return f"its answer held {len(entries)} entries, over the {MAX_ENTRIES} kept"
    size = len(with_entries(None, entries))
    if size > MAX_CHARACTERS:
        return f"its answer ran to {size:,} characters, over the {MAX_CHARACTERS:,} kept"
    return None


def _revised(
    prepared: Prepared, layout: Layout, entries: tuple[MemoryEntry, ...], said: tuple[str, ...]
) -> Steps[tuple[tuple[MemoryEntry, ...] | None, str]]:
    """The writer's list, or `None` and why it is not kept."""
    try:
        answer = yield on_host(_asked, prepared.memory_model, layout, entries, said)
    # Any failure: the memory stays as it was and the turn still succeeds.
    except Exception as exc:  # noqa: BLE001
        return None, f"its call failed: {type(exc).__name__}: {runtime.said(exc)}"
    try:
        revised = listed(answer, entries)
    except ValueError as exc:
        return None, str(exc)
    if (why := _over(revised)) is not None:
        return None, why
    return revised, ""


def kept_memory(  # noqa: PLR0913 -- the turn, and how it ended
    prepared: Prepared, snapshot: Any, *, cfg: Config, answer: str, ok: bool, paused: bool
) -> Steps[tuple[MemoryChanges, tuple[RunEvent, ...]]]:
    """The memory file as the turn leaves it, and what the turn did to it.

    The writer runs on a turn that ended on a summarised conversation, or once
    `memory_every` turns have ended since it last ran, and never on a turn that failed.
    Before the start-and-end comparison, so what it changed is reported with the rest.
    """
    layout = cfg.layout
    text, found = yield from memory_text(prepared.files, layout)
    # Before the writer as well as after it: a writer never shown a value cannot copy it.
    entries, dropped = identifying(found, prepared.caller_context)
    unread = yield from _not_yet_read(prepared.harness)
    values = getattr(snapshot, "values", None)
    messages = (
        list(runtime.as_transcript(runtime.messages_in(values))) if snapshot is not None else None
    )
    # A resume's state is the paused turn's, whose messages were read when it paused.
    resumed_from = prepared.resume is not None and unread.paused_at is not None
    since = unread.paused_at if resumed_from else len(prepared.history)
    said = (
        *unread.said,
        *_said_this_turn(messages, since=since or 0, task=prepared.task, answer=answer),
    )
    turns = unread.turns + 1
    events: list[RunEvent] = []
    written: str | None = None
    if ok and (runtime.summarised_in(snapshot) or turns >= cfg.memory_every):
        revised, why = yield from _revised(prepared, layout, entries, said)
        # Read, kept or not: the next run reads what follows, not this again.
        turns, said = 0, ()
        if revised is None:
            events.append(_not_updated(why))
        else:
            entries, more = identifying(revised, prepared.caller_context)
            dropped = (*dropped, *more)
            written = with_entries(text, entries)
    if written is None and dropped and text is not None:
        written = without_entries(text, frozenset(one.id for one in dropped))
    if written is not None and written != text:
        answers = yield changing(
            prepared.files,
            "upload_files",
            [(layout.memory_file_route, written.encode("utf-8"))],
        )
        if refused := [one.error for one in answers if one.error]:
            events.append(_not_updated(f"the session's backend would not keep it: {refused[0]}"))
            entries, dropped = found, ()
    yield from prepared.harness.store(
        MEMORY_WRITER,
        json.dumps(
            {
                "turns": turns,
                "said": list(said),
                "paused_at": len(messages) if paused and messages is not None else None,
            }
        ).encode("utf-8"),
    )
    made = changes(prepared.memory or (), entries, dropped)
    if made:
        events.append(RunEvent(kind="memory_updated", text=made.said()))
    return made, tuple(events)


def _not_updated(why: str) -> RunEvent:
    return RunEvent(kind="memory_not_updated", text=f"the memory writer's list was not kept: {why}")
