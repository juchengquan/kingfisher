"""Which standing middleware each kind of graph kingfisher builds is wrapped in."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware

from kingfisher.core.agent.backend import shell_prompt_of, shell_runs_here
from kingfisher.core.agent.middlewares.clearing import ResultClearingMiddleware
from kingfisher.core.agent.middlewares.cut_replies import CutReplies
from kingfisher.core.agent.middlewares.file_tools import FileTools
from kingfisher.core.agent.middlewares.large_pdf_guard import LargePdfMiddleware
from kingfisher.core.agent.middlewares.narrowing import (
    GrantedOnlyMiddleware,
    NarrowedSkillsMiddleware,
)
from kingfisher.core.agent.middlewares.recorder import RecorderMiddleware
from kingfisher.core.agent.middlewares.shell_paths import ShellPaths
from kingfisher.core.agent.middlewares.tool_failure import ToolFailureMiddleware
from kingfisher.core.agent.middlewares.unavailable import ModelUnavailableGuard
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
    recorder: RecorderMiddleware | None = None,
    clearing: ResultClearingMiddleware | None = None,
    tools: Sequence[str] | None = None,
    tools_subject: str = "this request",
    skills: Sequence[str] | None = None,
    skill_sources: Sequence[Any] = (),
    reachable: Sequence[str] | None = None,
) -> list[AgentMiddleware[Any, Any, Any]]:
    """Every standing middleware for one graph, in the order it wraps it.

    Built here and nowhere else, so what kingfisher guarantees on a graph is read in one
    place. `names` are the workspace tools the graph holds; `tools` narrows what it is
    offered and `skills` its skills index, each `None` where nothing narrows; `clearing`
    and `reachable` are the agent's alone.
    """
    if kind != "agent" and (clearing is not None or reachable is not None):
        msg = f"result clearing and the declared-subagent check are the agent's, not a {kind}'s"
        raise ValueError(msg)
    if kind == "general-purpose" and skills is not None:
        msg = "general-purpose is given the agent's skills index, and narrows none of its own"
        raise ValueError(msg)

    # First, so it measures the request deepagents' summariser counted, before anything
    # adds to it.
    standing: list[AgentMiddleware[Any, Any, Any]] = [] if clearing is None else [clearing]
    if kind == "agent":
        standing.append(TodoListMiddleware())
    # Ahead of the guards, so it records the arguments the model wrote, before
    # `WorkspaceToolPathsMiddleware` rewrites them to host paths.
    if recorder is not None:
        standing.append(recorder)
    # Ahead of the rest, which have nothing to say about a call that is not run.
    standing.append(CutReplies(subagent=kind != "agent", outputs=layout.outputs_route))
    # Inside `CutReplies`, so a refused call reaches it already as the stop it is.
    standing.append(ModelUnavailableGuard())
    # Outside `FileTools`, so a `grep` it runs as several searches fails once, as the call
    # the model made, when its path is refused.
    standing.append(ToolFailureMiddleware(names))
    standing.append(LargePdfMiddleware())
    standing.append(ShellPaths(layout, shell_prompt_of(backend), here=shell_runs_here(backend)))
    standing.append(FileTools())
    # Translation rewrites the arguments before anything after it judges them.
    if names and paths is not None:
        standing.append(WorkspaceToolPathsMiddleware(names, paths))
    # On every graph, narrowed or not. The agent's checks `task` whether or not this caller
    # narrowed anything: a subagent a future deepagents adds does not arrive unrestricted.
    standing.append(
        GrantedOnlyMiddleware(
            None if tools is None else tuple(tools),
            subagents=None if reachable is None else tuple(reachable),
            subject=tools_subject,
        )
    )
    if skills is not None:
        standing.append(
            NarrowedSkillsMiddleware(
                allowed=tuple(skills), backend=backend, sources=list(skill_sources)
            )
        )
    return standing
