"""A delegate that profiles a data file before it is allowed to answer.

**A compiled subagent, and the shape is the point.** Everything else in this
folder is a YAML document that kingfisher assembles into a delegate. This is a
Python module that assembles one itself and exports it as `SUBAGENTS`, and it
ships because it does the one thing the other format cannot.

A prompt can *ask* for a step. `analysis/profiler.yaml` says "csv_columns first
if you only need the shape" and means it, and a model that decides the answer is
obvious will skip it — occasionally, unpredictably, and most often on the file
where skipping it costs the most. A graph does not ask. The survey node runs
before the model node because there is no edge that reaches the model without
passing through it.

That is the whole reason to reach for this format. If a definition would do,
write a definition: it is reviewable by people who do not read Python, it gets
`system_prompt`, `skills`, `subagents` and `builtin_tools`, and it cannot go
wrong in a way a YAML parser will not catch. Five fields are refused here for
reasons `NOT_COMPILED` states one by one, and every one of them is a capability
you are giving up.

**The imports are deferred, and that is not style.** This module is imported
whenever the subagent catalogue is read — `kingfisher list` included — and
`from langchain.agents import create_agent` costs about 370 ms. At module scope
that is 370 ms added to every listing, for a delegate the request may never
activate. Inside `_build` it is paid once, by whoever actually uses this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: What a survey line looks like when there is nothing to survey. Said rather
#: than left blank, because a delegate whose profiling step silently did nothing
#: is exactly the failure this delegate exists to prevent -- and it would look
#: identical to a file with nothing interesting in it.
NOTHING_FOUND = "No readable file path was found in the request, so nothing was profiled."


def _paths_in(text: str) -> list[Path]:
    """Every token in the request that names a file which is actually there.

    A heuristic, and a deliberately shallow one: it splits on whitespace and
    keeps what exists on disk. It will miss a path with a space in it and it
    will find one mentioned in passing.

    That limit is stated rather than hidden because of what the delegate does
    when it finds nothing -- it says so, in the transcript, instead of quietly
    answering as though it had surveyed. A heuristic that fails loudly is a
    different thing from one that fails.
    """
    found = []
    for token in text.split():
        # Trailing punctuation is what prose does -- "what is in rows.csv?" was
        # the first thing a test tried and the first thing this missed. A
        # trailing `.` is stripped separately from the symmetric set, because
        # `./data.csv` starts with one and stripping both ends would eat it.
        candidate = Path(token.strip("\"'`,;:!?()[]{}<>").rstrip("."))
        if candidate.is_file():
            found.append(candidate)
    return found


def _build(model: Any, tools: list[Any]) -> Any:
    """Assemble the graph: survey, then answer, and no edge that skips the first.

    `tools` is what the request actually granted, already narrowed -- kingfisher
    resolves `tools:` below against the offering and hands over objects. A
    compiled delegate gets no allowlist middleware, so this list is the whole of
    what it can call, and the survey node calls them by name from it.
    """
    from langchain.agents import create_agent  # noqa: PLC0415 -- see the module docstring
    from langchain_core.messages import HumanMessage  # noqa: PLC0415
    from langgraph.graph import START, MessagesState, StateGraph  # noqa: PLC0415

    by_name = {getattr(one, "name", getattr(one, "__name__", "")): one for one in tools}

    def survey(state: MessagesState) -> dict[str, Any]:
        """Run every granted tool over every path in the request, before anything.

        Failures are reported rather than raised. This node runs before the
        model has said a word, so an exception here would end the turn with a
        traceback in place of an answer -- where the honest outcome is a delegate
        that says which profile it could not take and answers anyway.
        """
        asked = state["messages"][-1].content if state["messages"] else ""
        paths = _paths_in(asked if isinstance(asked, str) else str(asked))
        if not paths or not by_name:
            return {"messages": [HumanMessage(content=NOTHING_FOUND)]}
        lines = []
        for path in paths:
            for name, tool in sorted(by_name.items()):
                try:
                    if hasattr(tool, "invoke"):
                        answer = tool.invoke({"path": str(path)})
                    else:
                        answer = tool(str(path))
                except Exception as exc:  # noqa: BLE001 -- reported, never raised
                    answer = f"({name} failed: {exc})"
                lines.append(f"{name}({path}): {answer}")
        taken = "Profile taken before answering:\n" + "\n".join(lines)
        return {"messages": [HumanMessage(content=taken)]}

    # ty reads langgraph's `StateT` bound as unsatisfied by a
    # `typing_extensions.TypedDict` under `from __future__ import annotations`,
    # which `MessagesState` is. The graph compiles and runs; the limitation is
    # in the checker's model of the bound, and the suppression is narrowed to
    # the one line that hits it rather than the file -- `add_node` does not,
    # and ty says so if you suppress it there anyway.
    builder = StateGraph(MessagesState)  # ty: ignore[invalid-argument-type]
    builder.add_node("survey", survey)
    builder.add_node("answer", create_agent(model, tools))
    builder.add_edge(START, "survey")
    builder.add_edge("survey", "answer")
    return builder.compile()


SUBAGENTS = [
    {
        "name": "first-look",
        "description": (
            "Profiles a data file and then answers questions about it. The profiling "
            "step cannot be skipped. Use for the first question anyone asks about a "
            "file nobody has looked at yet."
        ),
        # Resolved against the catalogue exactly as a YAML definition's would be,
        # and narrowed by what the request granted. Written the long way for the
        # check it buys: if `csv_columns` moves out of `csv_profile/`, this says
        # so at startup rather than surveying with one tool fewer.
        "tools": ["line_count", "csv_profile::csv_columns"],
        # Profiling is bulk reading, which is what the cheap model is for. Bound
        # by your `models.yaml` rather than named here, for the reason
        # `extractor.yaml` gives.
        "alias": "cheap",
        "build": _build,
    }
]
