"""Which standing middleware each kind of graph kingfisher builds is wrapped in."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import Any, Literal

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware

from kingfisher.core.agent.backend import shell_prompt_of, shell_runs_here
from kingfisher.core.agent.session_paths import SessionPaths
from kingfisher.core.config import ConfigError
from kingfisher.core.layout import Layout

from .attachments import AttachmentsMiddleware
from .builtin_tools import BuiltinToolsMiddleware, longest_command
from .clearing import ResultClearingMiddleware
from .guarded_calls import (
    GuardedCallsMiddleware,
    OfferedToolFailureMiddleware,
)
from .model_reply import ModelReplyMiddleware
from .narrowing import (
    GrantedOnlyMiddleware,
    NarrowedSkillsMiddleware,
)
from .recorder import RecorderMiddleware
from .scratch_runs import ScratchRunsMiddleware
from .session_instructions import CACHING, SessionInstructionsMiddleware
from .step_budget import StepBudget, StepBudgetMiddleware

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
    budget: StepBudget,
    trigger: int,
    execution_timeout_s: int,
    permissions: Any = None,
    keep_results_rounds: int | None = None,
    recorder: RecorderMiddleware | None = None,
    clearing: Callable[[tuple[str, ...] | None], ResultClearingMiddleware] | None = None,
    tools: Sequence[str] | None = None,
    gated: Collection[str] = (),
    tools_subject: str = "this request",
    skills: Sequence[str] | None = None,
    skill_sources: Sequence[Any] = (),
    reachable: Sequence[str] | None = None,
    inner: Sequence[AgentMiddleware[Any, Any, Any]] = (),
    session_text: str | None = None,
    session_cached: bool = True,
) -> list[AgentMiddleware[Any, Any, Any]]:
    """Every standing middleware for one graph, in the order it wraps it.

    Built here and nowhere else, so what kingfisher guarantees on a graph is read in one
    place. `names` are the offered tools the graph holds; `tools` narrows what it is
    offered and `skills` its skills index, each `None` where nothing narrows, and `gated`
    what it stops on for a person; `clearing`, which builds the result clearing for that
    grant, and `reachable` are the agent's alone.
    `budget` is the turn's, whose step limit reaches only the agent's own run, so a subagent
    is held to it in model calls; one per build, shared by all three kinds. `trigger` is
    where this graph's own summariser fires, which sizes the PDFs its turn may hold open.

    `inner` is what the deployment's settings switch on and what its definitions name, which
    go inside every standing one. Taken here rather than appended by each caller so that the
    one layer that must stay under them -- `OfferedToolFailureMiddleware`, which answers
    for what the tool raised and must not answer for them -- cannot be left off a graph.

    `session_text` is what the deployment's `SessionInstructions` said for this turn, `None`
    where it wired none; `session_cached` is false only for a helper, whose stack its
    parent's own `SubAgentMiddleware` builds with no prompt caching.
    """
    if kind != "agent" and (clearing is not None or reachable is not None):
        msg = f"result clearing and the declared-subagent check are the agent's, not a {kind}'s"
        raise ValueError(msg)
    if kind == "general-purpose" and skills is not None:
        msg = (
            "general-purpose is handed no skills index, the agent's or its own, so has none "
            "to narrow"
        )
        raise ValueError(msg)
    # Whatever this turn's text: refused only on some sessions, it would look intermittent.
    if session_text is not None and any(m.name == CACHING for m in inner):
        msg = (
            f"{kind} middleware named {CACHING!r} replaces deepagents' prompt caching, the "
            f"slot kingfisher sends the text of session_instructions= from, after the "
            f"cached part of the prompt; with both, that text would never be sent. Drop the "
            f"replacement, which the plugin is there to make unnecessary, or the plugin"
        )
        raise ConfigError(msg)

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
    # `GuardedCallsMiddleware` rewrites them to host paths.
    if recorder is not None:
        standing.append(recorder)
    standing.append(
        StepBudgetMiddleware(budget, subagent=kind != "agent")
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
    # as the call the model made, when its path is refused. Its translation rewrites the
    # arguments before anything after it judges them.
    standing.append(GuardedCallsMiddleware(names, paths))
    # Only where the graph holds offered tools: theirs are the only calls it binds.
    if names:
        standing.append(
            ScratchRunsMiddleware(
                names,
                backend=backend,
                tools=granted,
                gated=gated,
                seconds=execution_timeout_s,
            )
        )
    # Inside the recorder, so a call whose only file was replaced fails and leaves no row.
    standing.append(AttachmentsMiddleware(trigger))
    # deepagents merges by name, so this *replaces* the `FilesystemMiddleware` it would
    # build itself -- in this graph's stack and, through the spec, in each subagent's.
    # Only to turn its offloading off: a result too long to show whole is kingfisher's to
    # save, in the one folder a ledger row names, and a second copy under a name only
    # deepagents knows cost a reconciliation at every turn's end to tell the two apart.
    # `_permissions` is deepagents' own keyword, private and passed as it asks.
    standing.append(
        FilesystemMiddleware(
            backend=backend,
            tool_token_limit_before_evict=None,
            _permissions=permissions,
        )
    )
    standing.append(
        BuiltinToolsMiddleware(
            layout,
            shell_prompt_of(backend),
            longest=longest_command(execution_timeout_s),
            here=shell_runs_here(backend),
            expires_after=keep_results_rounds,
        )
    )
    if skills is not None:
        standing.append(
            NarrowedSkillsMiddleware(
                allowed=tuple(skills), backend=backend, sources=list(skill_sources)
            )
        )
    standing.extend(inner)
    # After `inner`: in deepagents' stack it takes the caching slot by name wherever it is
    # listed, and in a helper's, which has none, it must still follow all that writes the
    # prompt.
    if session_text:
        standing.append(SessionInstructionsMiddleware(session_text, cached=session_cached))
    # Last of all, under `inner`. Left out where the graph holds no offered tool: there is
    # no name for it to answer for, and a middleware that cannot act reads as one that does.
    if names:
        standing.append(OfferedToolFailureMiddleware(names))
    return standing
