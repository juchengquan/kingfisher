"""A delegate that answers, then reports exactly which tools it actually ran.

**A compiled subagent, and the shape is the point.** Everything else in this
folder is a YAML document that kingfisher assembles into a delegate. This is a
Python module that assembles one itself and exports it as `SUBAGENTS`, and it
ships because it does the one thing a document cannot.

"Show your work" is the instruction everyone writes into a prompt and nobody
reliably gets. Ask a model which tools it used and you get a *claim*: usually
right, occasionally a narration of steps it skipped, and most confidently wrong
about a call that failed -- "I verified the totals" after the verification tool
raised. Compute the same list from the tool calls in the transcript and you get
a *fact*. The record node here cannot be talked out of what happened, because it
is not asked.

That is the reason to reach for this format. If a definition would do, write a
definition: it is reviewable by people who do not read Python, it gets
`system_prompt`, `skills`, `subagents` and `builtin_tools`, and it cannot go
wrong in a way a YAML parser will not catch. Five fields are refused here for
reasons `NOT_COMPILED` states one by one, and every one of them is a capability
being given up.

**No `tools:` line, which is the other half of the example.** A compiled
definition omitting it means `absent=ALL` -- whatever the request granted. This
delegate reports on what it was handed rather than on a fixed pair, so it is
useful against any tool set.

**The record joins the answer rather than following it.** deepagents returns a
delegate's result by walking back to the last `AIMessage` with non-empty text,
so a record appended as its own message would not accompany the answer, it would
*replace* it -- the caller would get the footer and lose the reply. One message
carrying both is the only shape that survives that, and it is worth knowing
before anyone rearranges these nodes.

**The imports are deferred, and that is not style.** This module is imported
whenever the subagent catalogue is read -- `kingfisher list` included -- and
`from langchain.agents import create_agent` costs about 370 ms. At module scope
that is paid on every listing, for a delegate the request may never activate.
"""

from __future__ import annotations

from typing import Any

#: How much of one call's arguments is printed. A display limit rather than a
#: judgement: it decides how much of a known value to show, never what a value
#: means. A tool handed a whole SQL statement or a page of text would otherwise
#: put it in the caller's context on every turn.
ARGUMENT_WIDTH = 120

#: What the record says when the delegate answered without calling anything.
#: Said rather than omitted, because "no tools were run" is the single most
#: useful line this delegate ever prints -- an answer produced without touching
#: the files it is about is exactly what an auditor is looking for, and an empty
#: footer reads as though the question was never asked.
NOTHING_RAN = "ran nothing: this answer used no tools."


def _arguments(args: Any) -> str:
    """One call's arguments, compactly, cut at `ARGUMENT_WIDTH`."""
    if not isinstance(args, dict) or not args:
        return ""
    written = ", ".join(f"{key}={value!r}" for key, value in sorted(args.items()))
    return written if len(written) <= ARGUMENT_WIDTH else written[: ARGUMENT_WIDTH - 1] + "…"


def _record(messages: list[Any]) -> str:
    """Every tool call in this transcript, with its arguments and whether it failed.

    Read from the messages the graph itself produced, never from anything a
    person wrote -- there is nothing here to guess at, which is the difference
    between this and a heuristic.

    Failure comes from `ToolMessage.status`, which is a field rather than a
    string to sniff. A call that raised and a call that returned are the pair a
    model is least reliable about, so the record would be worth much less
    without it.
    """
    outcome: dict[str, str] = {}
    for message in messages:
        call_id = getattr(message, "tool_call_id", None)
        if call_id is not None:
            outcome[call_id] = getattr(message, "status", "success") or "success"

    lines = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            written = _arguments(call.get("args"))
            failed = outcome.get(call.get("id")) == "error"
            lines.append(
                f"  {call.get('name', '?')}({written})"
                + ("  — failed" if failed else "")
            )
    if not lines:
        return NOTHING_RAN
    return "ran, in order:\n" + "\n".join(lines)


def _build(model: Any, tools: list[Any]) -> Any:
    """Assemble the graph: answer, then record, with no edge that skips the record.

    `tools` is what the request actually granted, already narrowed -- kingfisher
    resolves the declaration against the offering and hands over objects. A
    compiled delegate is given no allowlist middleware, so this list is the whole
    of what it can call.
    """
    from langchain.agents import create_agent  # noqa: PLC0415 -- see the module docstring
    from langchain_core.messages import AIMessage  # noqa: PLC0415
    from langgraph.graph import START, MessagesState, StateGraph  # noqa: PLC0415

    def record(state: MessagesState) -> dict[str, Any]:
        """Replace the answer with the answer *and* what it took to produce it."""
        messages = list(state["messages"])
        # Walked back the same way deepagents walks back to decide what a
        # delegate returned -- last `AIMessage` with non-empty text -- so the
        # message this node replaces is exactly the one that would have been
        # sent on. `.text` is a property; calling it is deprecated upstream.
        answered = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage) and (message.text or "").strip():
                answered = message.text.rstrip()
                break
        return {"messages": [AIMessage(content=f"{answered}\n\n--- {_record(messages)}")]}

    # ty reads langgraph's `StateT` bound as unsatisfied by a
    # `typing_extensions.TypedDict` under `from __future__ import annotations`,
    # which `MessagesState` is. The graph compiles and runs; the limitation is
    # in the checker's model of the bound, and the suppression is narrowed to
    # the one line that hits it rather than the file.
    builder = StateGraph(MessagesState)  # ty: ignore[invalid-argument-type]
    builder.add_node("answer", create_agent(model, tools))
    builder.add_node("record", record)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", "record")
    return builder.compile()


SUBAGENTS = [
    {
        "name": "show-your-work",
        "description": (
            "Answers a question and then reports exactly which tools it ran, with "
            "their arguments and whether any failed. Use when the answer will be "
            "checked, or when you need to know whether a check actually happened."
        ),
        # No `tools:` line on purpose -- see the module docstring. It reports on
        # whatever the request granted.
        #
        # No `model` key, so this runs whatever summoned it. Reporting is
        # cheap work: add `"model": "<one your models.yaml defines>"` to pin it,
        # left out here for the reason `extractor.yaml` gives at length.
        "build": _build,
    }
]
