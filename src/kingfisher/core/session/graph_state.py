"""What the graph's state holds when a turn ends -- the conversation as the model last saw
it, and the calls a pause waits on -- and the answers a resume drives it with."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.request import Decision, DecisionError
from kingfisher.core.result import DECISIONS, PendingDecision

from .runtime import SUBAGENT_TYPE_ARG, TOOLS_NODE, cut_off


def ended_cut_off(state: Any) -> Any | None:
    """The agent's last reply where it was cut off at the output cap, else `None`."""
    messages = (getattr(state, "values", None) or {}).get("messages") or ()
    last = messages[-1] if messages else None
    return last if isinstance(last, AIMessage) and cut_off(last) else None


#: Where deepagents' summariser keeps the summary it made. Private state, so what
#: `invoke` returns leaves it out and only the graph's own state has it.
SUMMARY_EVENT = "_summarization_event"


def as_seen(snapshot: Any) -> list[Any]:
    """The conversation as the model last saw it: the summary and what followed its cut,
    where deepagents summarised, else every message.

    Every message stays in state with the summary beside it, as private state the next
    turn's graph is never given: kept whole, each turn summarises the conversation again.
    """
    messages = held_in(snapshot)
    if (summary := summary_in(snapshot)) is None:
        return messages
    message, cut = summary
    # Cut here, before `as_messages` drops any message: the cut is a position in this list.
    return [message, *messages[cut:]]


def summarised_away(snapshot: Any) -> list[Any]:
    """The messages a summary stands for, which `as_seen` leaves out; none where deepagents
    summarised nothing."""
    if (summary := summary_in(snapshot)) is None:
        return []
    return held_in(snapshot)[: summary[1]]


def with_answers(snapshot: Any, answers: Mapping[str, Any]) -> Any:
    """`snapshot` with each of `answers` after the message asking for its call, where the
    state holds no answer to it: the graph was cancelled before it took the answer."""
    values = getattr(snapshot, "values", None)
    if not answers or not isinstance(values, Mapping) or not hasattr(snapshot, "_replace"):
        return snapshot
    messages = list(values.get("messages") or ())
    answered = {getattr(one, "tool_call_id", None) for one in messages}
    joined: list[Any] = []
    for one in messages:
        joined.append(one)
        for call in getattr(one, "tool_calls", None) or ():
            if call.get("id") in answers and call.get("id") not in answered:
                joined.append(answers[call["id"]])
    return snapshot._replace(values={**values, "messages": joined})


def summarised_in(snapshot: Any) -> bool:
    """Whether a turn ended on a conversation deepagents had summarised, even before
    the turn began, as a resume's restored state can be."""
    return summary_in(snapshot) is not None


def held_in(snapshot: Any) -> list[Any]:
    """Every message the state holds, those a summary stands for included."""
    values = getattr(snapshot, "values", None)
    return list(values.get("messages") or ()) if isinstance(values, Mapping) else []


def summary_in(snapshot: Any) -> tuple[Any, int] | None:
    """The summary deepagents last wrote and where it cut `held_in`, or `None`.

    Only the last: a second summary in one turn replaces the first, its cut still counted
    from the start of every message held.
    """
    values = getattr(snapshot, "values", None)
    event = values.get(SUMMARY_EVENT) if isinstance(values, Mapping) else None
    if not isinstance(event, Mapping):
        return None
    return event["summary_message"], event["cutoff_index"]


#: Separates the interrupt a gated call belongs to from its place within it. Neither
#: half is an address alone: one `Interrupt` covers every gated call in a message, and
#: the tool call's own id is not in the pause.
CALL_SEPARATOR = "#"


def pending_in(snapshot: Any) -> tuple[PendingDecision, ...]:
    """Every gated call a paused turn is waiting on; nothing for a turn that ran on."""
    found: list[PendingDecision] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        subagent = _subagent_behind(task, snapshot)
        for pause in getattr(task, "interrupts", ()) or ():
            value = pause.value
            if not isinstance(value, dict):
                continue
            requests = list(value.get("action_requests") or ())
            configs = list(value.get("review_configs") or ())
            for index, request in enumerate(requests):
                review = configs[index] if index < len(configs) else {}
                allowed = review.get("allowed_decisions", ())
                found.append(
                    PendingDecision(
                        call_id=f"{pause.id}{CALL_SEPARATOR}{index}",
                        tool=str(request.get("name") or ""),
                        args=dict(request.get("args") or {}),
                        agent=subagent,
                        # Narrowed to what kingfisher accepts: a bare `True` gate also
                        # offers `edit`, which the resume path would refuse.
                        decisions=tuple(d for d in allowed if d in DECISIONS),
                    )
                )
    return tuple(found)


def calls_paused(snapshot: Any, saver: Any) -> dict[str, str]:
    """The provider's id of each gated call a paused turn is waiting on, by the id
    `pending_in` gives it; a call that cannot be found is left out."""
    found: dict[str, str] = {}
    for task in getattr(snapshot, "tasks", ()) or ():
        for pause in getattr(task, "interrupts", ()) or ():
            value = pause.value
            if not isinstance(value, dict):
                continue
            requests = list(value.get("action_requests") or ())
            for index, call in enumerate(_asked_about(requests, task, snapshot, saver)):
                if call:
                    found[f"{pause.id}{CALL_SEPARATOR}{index}"] = call
    return found


def _asked_about(
    requests: Sequence[Any], task: Any, snapshot: Any, saver: Any
) -> list[str | None]:
    """The id of each call `requests` asks about, read off the message the gate stopped.

    A subagent's message is in no state the snapshot reaches: its own graph keeps it
    under a namespace of the tool task that runs it, a helper's one segment below that.
    """
    if getattr(task, "name", "") != TOOLS_NODE:
        return _gated_calls(requests, held_in(snapshot))
    thread = ((getattr(snapshot, "config", None) or {}).get("configurable") or {}).get(
        "thread_id"
    )
    root = f"{task.name}:{task.id}"
    spaces = getattr(saver, "storage", {}).get(thread, {})
    # The deepest first: a helper's own stop surfaces on the task of the subagent above it.
    for space in sorted((s for s in spaces if s == root or s.startswith(f"{root}|")), key=len,
                        reverse=True):
        kept = saver.get_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": space}})
        held = kept.checkpoint["channel_values"].get("messages") if kept else None
        if all(found := _gated_calls(requests, held or ())):
            return found
    return [None] * len(requests)


def _gated_calls(requests: Sequence[Any], messages: Sequence[Any]) -> list[str | None]:
    """Each request matched to a call of the last reply in `messages`, by position.

    `HumanInTheLoopMiddleware` asks about that reply's calls in the order the model wrote
    them, skipping those it does not gate, and names none by id; so each is matched to the
    next call with its name and arguments.
    """
    reply = next((one for one in reversed(messages) if isinstance(one, AIMessage)), None)
    calls = list(reply.tool_calls) if reply is not None else []
    found: list[str | None] = []
    at = 0
    for request in requests:
        match = next(
            (
                index
                for index in range(at, len(calls))
                if calls[index]["name"] == request.get("name")
                and calls[index].get("args") == request.get("args")
            ),
            None,
        )
        found.append(calls[match].get("id") if match is not None else None)
        at = at if match is None else match + 1
    return found


def _subagent_behind(task: Any, snapshot: Any) -> str | None:
    """Which subagent proposed this, where the pause can say so without guessing.

    The name is in the parent's last message, as the `subagent_type` of the `task` call
    that started it, and is unambiguous only while one subagent is in flight. With two
    this is `None`: the wrong name beside a call about to be approved is worse than none.
    """
    # The pause's task first: one message may gate the agent's own call and start one
    # subagent, and the search would hand the agent's call that subagent's name.
    if getattr(task, "name", "") != TOOLS_NODE:
        return None
    for message in reversed(list(getattr(snapshot, "values", {}).get("messages", ()))):
        calls = getattr(message, "tool_calls", None)
        if not calls:
            continue
        started = [
            str(call["args"][SUBAGENT_TYPE_ARG])
            for call in calls
            if call.get("name") == TASK_TOOL and SUBAGENT_TYPE_ARG in (call.get("args") or {})
        ]
        return started[0] if len(started) == 1 else None
    return None


def resume_payload(answers: Iterable[Decision], pending: Iterable[PendingDecision]) -> Any:
    """The caller's answers as a graph is driven with them: per interrupt, in order.

    Returns the `Command`, not the mapping inside it, so its caller never names
    langgraph. Keyed by interrupt id, so a turn paused in two places is answered
    in one resume; a wrong or missing id is named here rather than counted by langgraph.
    """
    waiting = {item.call_id: item for item in pending}
    answered: dict[str, Decision] = {}
    for answer in answers:
        if answer.call_id not in waiting:
            msg = f"no pending decision with id {answer.call_id!r}"
            raise DecisionError(msg)
        allowed = waiting[answer.call_id].decisions
        if allowed and answer.action not in allowed:
            msg = (
                f"{answer.call_id} ({waiting[answer.call_id].tool}) takes "
                f"{', '.join(allowed)} -- not {answer.action!r}"
            )
            raise DecisionError(msg)
        if answer.action == "respond" and not answer.message:
            msg = f"{answer.call_id} was answered 'respond' with nothing to respond"
            raise DecisionError(msg)
        answered[answer.call_id] = answer
    if unanswered := sorted(set(waiting) - set(answered)):
        msg = f"still waiting on {', '.join(unanswered)}"
        raise DecisionError(msg)

    grouped: dict[str, list[Any]] = {}
    # Back into the gate's order: langgraph matches `decisions` to `action_requests` by
    # position, so the caller's order could carry an approval onto a rejected call.
    for call_id, answer in sorted(
        answered.items(), key=lambda pair: int(pair[0].rsplit(CALL_SEPARATOR, 1)[1])
    ):
        where = call_id.rsplit(CALL_SEPARATOR, 1)[0]
        grouped.setdefault(where, []).append(_as_langgraph_decision(answer))
    return Command(
        resume={where: {"decisions": decisions} for where, decisions in grouped.items()}
    )


def _as_langgraph_decision(answer: Decision) -> dict[str, Any]:
    """One answer in the shape `HumanInTheLoopMiddleware` reads."""
    if answer.action == "approve":
        return {"type": "approve"}
    # `reject` may carry nothing and the middleware writes its own refusal; `respond`
    # may not, and is refused above rather than sent on as an empty tool result.
    if answer.message:
        return {"type": answer.action, "message": answer.message}
    return {"type": answer.action}
