"""Which standing middleware each kind of graph kingfisher builds is wrapped in."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware

from kingfisher.core.agent.backend import shell_prompt_of, shell_runs_here
from kingfisher.core.agent.middlewares.builtin_tools import BuiltinToolsMiddleware
from kingfisher.core.agent.middlewares.clearing import ResultClearingMiddleware
from kingfisher.core.agent.middlewares.large_pdf_guard import LargePdfMiddleware
from kingfisher.core.agent.middlewares.model_reply import ModelReplyMiddleware
from kingfisher.core.agent.middlewares.narrowing import (
    GrantedOnlyMiddleware,
    NarrowedSkillsMiddleware,
)
from kingfisher.core.agent.middlewares.recorder import RecorderMiddleware
from kingfisher.core.agent.middlewares.step_budget import STEPS_PER_CALL, StepBudgetMiddleware
from kingfisher.core.agent.middlewares.tool_failure import ToolFailureMiddleware
from kingfisher.core.agent.middlewares.workspace_tools import WorkspaceToolPathsMiddleware
from kingfisher.core.agent.session_paths import SessionPaths
from kingfisher.core.layout import Layout

#: The graphs kingfisher builds itself: the agent, a subagent a definition declares, and
#: deepagents' `general-purpose`, which is handed the agent's own tools. A compiled
#: subagent is not among them: deepagents runs it as given.
Kind = Literal["agent", "subagent", "general-purpose"]


def standing_middleware(  # noqa: PLR0913 -- one keyword per thing a standing one is built from
    kind: Kind,
    *,
    names: frozenset[str],
    paths: SessionPaths | None,
    layout: Layout,
    backend: Any,
    step_limit: int,
    recorder: RecorderMiddleware | None = None,
    clearing: Callable[[tuple[str, ...] | None], ResultClearingMiddleware] | None = None,
    tools: Sequence[str] | None = None,
    tools_subject: str = "this request",
    skills: Sequence[str] | None = None,
    skill_sources: Sequence[Any] = (),
    reachable: Sequence[str] | None = None,
) -> list[AgentMiddleware[Any, Any, Any]]:
    """Every standing middleware for one graph, in the order it wraps it.

    Built here and nowhere else, so what kingfisher guarantees on a graph is read in one
    place. `names` are the workspace tools the graph holds; `tools` narrows what it is
    offered and `skills` its skills index, each `None` where nothing narrows; `clearing`,
    which builds the result clearing for that grant, and `reachable` are the agent's alone.
    `step_limit` is the turn's, which reaches only the agent's own run, so a subagent is held
    to it in model calls.
    """
    if kind != "agent" and (clearing is not None or reachable is not None):
        msg = f"result clearing and the declared-subagent check are the agent's, not a {kind}'s"
        raise ValueError(msg)
    if kind == "general-purpose" and skills is not None:
        msg = "general-purpose is given the agent's skills index, and narrows none of its own"
        raise ValueError(msg)

    # One grant for the handles and the filter, so a handle never names a tool it refuses.
    granted = None if tools is None else tuple(tools)
    # First, so it measures the request deepagents' summariser counted, before anything
    # adds to it.
    standing: list[AgentMiddleware[Any, Any, Any]] = (
        [] if clearing is None else [clearing(granted)]
    )
    if kind == "agent":
        # Outside `BuiltinToolsMiddleware`, which takes its prose out where `write_todos`
        # was not granted.
        standing.append(TodoListMiddleware())
    # Ahead of the guards, so it records the arguments the model wrote, before
    # `WorkspaceToolPathsMiddleware` rewrites them to host paths.
    if recorder is not None:
        standing.append(recorder)
    standing.append(
        StepBudgetMiddleware(calls=None if kind == "agent" else step_limit // STEPS_PER_CALL)
    )
    # Ahead of the rest, which have nothing to say about a call that is not run.
    standing.append(ModelReplyMiddleware(subagent=kind != "agent", outputs=layout.outputs_route))
    # On every graph, narrowed or not. The agent's checks `task` whether or not this caller
    # narrowed anything: a subagent a future deepagents adds does not arrive unrestricted.
    # Ahead of the tools' own middleware, which read what it leaves in the request, so an
    # ungranted call is refused before anything splits or translates it.
    standing.append(
        GrantedOnlyMiddleware(
            granted,
            subagents=None if reachable is None else tuple(reachable),
            subject=tools_subject,
        )
    )
    # Outside `BuiltinToolsMiddleware`, so a `grep` it runs as several searches fails once,
    # as the call the model made, when its path is refused.
    standing.append(ToolFailureMiddleware(names))
    standing.append(LargePdfMiddleware())
    standing.append(
        BuiltinToolsMiddleware(layout, shell_prompt_of(backend), here=shell_runs_here(backend))
    )
    # Translation rewrites the arguments before anything after it judges them.
    if names and paths is not None:
        standing.append(WorkspaceToolPathsMiddleware(names, paths))
    if skills is not None:
        standing.append(
            NarrowedSkillsMiddleware(
                allowed=tuple(skills), backend=backend, sources=list(skill_sources)
            )
        )
    return standing
