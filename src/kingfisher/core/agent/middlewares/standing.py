"""Which standing middleware each kind of graph kingfisher builds is wrapped in."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from typing import TYPE_CHECKING, Any, Literal

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware

from kingfisher.core.agent.backend import (
    folders_of,
    session_root_of,
    shell_runs_here,
    shell_told,
)
from kingfisher.core.agent.session_paths import SessionPaths
from kingfisher.core.config import ConfigError
from kingfisher.core.interfaces import NoScratchRunLimit
from kingfisher.core.layout import Layout

from .attachments import AttachmentsMiddleware
from .builtin_tools import BuiltinToolsMiddleware, longest_command
from .caching import CACHING, PromptCachingMiddleware
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
from .session_instructions import SessionInstructionsMiddleware
from .signed_thinking import SignedThinkingMiddleware, signers_of
from .step_budget import StepBudget, StepBudgetMiddleware

if TYPE_CHECKING:
    from kingfisher.core.config import Models
    from kingfisher.core.interfaces import PdfAttachments, ScratchRunLimit

_log = logging.getLogger(__name__)

#: The graphs kingfisher builds itself: the agent, a subagent a definition declares, and
#: deepagents' `general-purpose`, which is handed the agent's own tools. A compiled
#: subagent is not among them: deepagents runs it as given.
Kind = Literal["agent", "subagent", "general-purpose"]

#: The name deepagents merges its filesystem middleware by, which a deployment's may take.
FILESYSTEM = FilesystemMiddleware.__name__


def standing_middleware(  # noqa: PLR0913 -- one keyword per thing a standing one is built from
    kind: Kind,
    *,
    name: str,
    names: frozenset[str],
    paths: SessionPaths | None,
    layout: Layout,
    backend: Any,
    budget: StepBudget,
    trigger: int,
    execution_timeout_s: int,
    models: Models,
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
    pdf_attachments: PdfAttachments | None = None,
    scratch_run_limit: ScratchRunLimit | None = None,
    inner: Sequence[AgentMiddleware[Any, Any, Any]] = (),
    session_text: str | None = None,
    session_cached: bool = True,
) -> list[AgentMiddleware[Any, Any, Any]]:
    """Every standing middleware for one graph, in the order it wraps it.

    Built here and nowhere else, so what kingfisher guarantees on a graph is read in one
    place. `names` are the offered tools the graph holds; `tools` narrows what it is
    offered and `skills` its skills index, each `None` where nothing narrows, and `gated`
    what it stops on for a person; `clearing` builds the result clearing for that grant,
    `None` where no ledger is kept, and `reachable` is the agent's alone.
    `budget` is the turn's, whose step limit reaches only the agent's own run, so a subagent
    is held to it in model calls; one per build, shared by all three kinds. `trigger` is
    where this graph's own summariser fires, which sizes the PDFs its turn may hold open,
    and `pdf_attachments` is the deployment's say in what each of them is replaced with.
    `name` is the graph's, as `scratch_run_limit` is asked about it, and `models` the
    deployment's catalogue, whose endpoints say who answered each call.

    `inner` is what the deployment's settings switch on and what its definitions name, which
    go inside every standing one. Taken here rather than appended by each caller so that the
    one layer that must stay under them -- `OfferedToolFailureMiddleware`, which answers
    for what the tool raised and must not answer for them -- cannot be left off a graph.

    `session_text` is what the deployment's `SessionInstructions` said for this turn, `None`
    where it wired none; `session_cached` is false only for a helper, whose stack its
    parent's own `SubAgentMiddleware` builds with no prompt caching.
    """
    if kind != "agent" and reachable is not None:
        msg = f"the declared-subagent check is the agent's, not a {kind}'s"
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
    # After clearing, which measures what the summariser counted. Outside the step budget
    # and the reply checks, so the reply it records is the one kept and a call asked again
    # goes as they built it; and outside the caching slot, which marks what this sends.
    standing.append(SignedThinkingMiddleware(signers_of(models)))
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
        limit = NoScratchRunLimit() if scratch_run_limit is None else scratch_run_limit
        refusal = None
        try:
            allows = limit.allows(name, kind)
        except Exception as error:  # noqa: BLE001 -- a plugin's bug costs this graph its
            # runs, never the turn, and never grants one.
            _log.warning(
                "the scratch run limit raised on %s %r; its tools are refused a run",
                kind,
                name,
                exc_info=True,
            )
            allows = False
            refusal = (
                f"refused: this deployment's ScratchRunLimit raised "
                f"{type(error).__name__}: {error}"
            )
        standing.append(
            ScratchRunsMiddleware(
                names,
                backend=backend,
                tools=granted,
                gated=gated,
                seconds=execution_timeout_s,
                limit_allows=allows,
                limit_refusal=refusal,
            )
        )
    # Inside the recorder, so a call whose only file was replaced fails and leaves no row.
    standing.append(AttachmentsMiddleware(trigger, pdf_attachments))
    # deepagents merges by name, so this *replaces* the `FilesystemMiddleware` it would
    # build itself -- in this graph's stack and, through the spec, in each subagent's.
    # Only to turn its offloading off: a result too long to show whole is kingfisher's to
    # save, in the one folder a ledger row names, and a second copy under a name only
    # deepagents knows cost a reconciliation at every turn's end to tell the two apart.
    # `_permissions` is deepagents' own keyword, private and passed as it asks.
    # Not beside one `inner` names: deepagents' merge keeps the later of the two, and a
    # helper's stack, which meets no merge, refused to build holding both.
    if not any(one.name == FILESYSTEM for one in inner):
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
            shell_told(backend),
            rooted=session_root_of(backend) is not None,
            longest=longest_command(execution_timeout_s),
            here=shell_runs_here(backend),
            expires_after=keep_results_rounds,
            folders=folders_of(layout, backend),
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
    # A deployment's own caching keeps the slot; a helper has none to take.
    elif session_cached and not any(m.name == CACHING for m in inner):
        standing.append(PromptCachingMiddleware())
    # Last of all, under `inner`. Left out where the graph holds no offered tool: there is
    # no name for it to answer for, and a middleware that cannot act reads as one that does.
    if names:
        standing.append(OfferedToolFailureMiddleware(names))
    return standing
