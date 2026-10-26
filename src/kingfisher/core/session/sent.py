"""What a turn's first model call would be sent, asked without running the turn."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.access import caller_holds
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.agent.sent import first_call

from .model import unknown_session
from .steps import Steps, adrive, changing, drive, on_host
from .store import HarnessFiles, pinned_in

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.access import Capabilities, Held, Vocabulary
    from kingfisher.core.config import Config
    from kingfisher.core.definitions.agents.spec import AgentSpec
    from kingfisher.core.request import Request
    from kingfisher.core.result import Sent

    from .model import Conversation


class Preview:
    """Building a turn's agent as the turn would, and showing what it would send."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    vocabulary: Vocabulary | None
    grants: Capabilities
    _sessions: Any
    _graph: Any
    _session_for: Callable[..., Steps[Conversation]]
    _known_steps: Callable[[], Steps[tuple[str, ...]]]
    _files_for: Callable[..., Steps[Any]]
    _reaches_pin: Callable[..., bool]
    _agent_for: Callable[..., AgentSpec | None]
    _graph_for: Callable[..., Any]

    def sent(self, request: Request, *, source_ids: Held | None = None) -> Sent:
        """What the first model call of `request`'s turn would be sent, with no turn run.

        Built in the session the request names, as its turn would be, and caught before
        the call leaves: no model is called and the session is neither claimed nor
        written to. A session made in order to look is removed again.
        """
        return drive(self._sent_steps(request, source_ids))

    async def asent(self, request: Request, *, source_ids: Held | None = None) -> Sent:
        """`sent`, for a caller on an event loop."""
        return await adrive(self._sent_steps(request, source_ids))

    def _sent_steps(self, request: Request, source_ids: Held | None) -> Steps[Sent]:
        if self._graph is not None:
            msg = (
                "this deployment supplied its own graph, which kingfisher did not build "
                "and so cannot say what it sends"
            )
            raise ValueError(msg)
        if request.inputs:
            # Placing them is a write; leaving them out silently would read as placed.
            msg = "sent places no inputs; ask without them, since they change nothing sent"
            raise ValueError(msg)
        session = yield from self._session_for(request)
        held = caller_holds(self.vocabulary, source_ids)
        made = session.id not in (yield from self._known_steps())
        files = yield from self._files_for(session.id)
        try:
            sent = yield from self._looked(request, session.id, files, held)
        except BaseException:
            if made:
                yield changing(self._sessions, "delete", self.cfg, session.id)
            raise
        if made:
            yield changing(self._sessions, "delete", self.cfg, session.id)
        return sent

    def _looked(
        self, request: Request, session_id: str, files: Any, held: frozenset[str] | None
    ) -> Steps[Sent]:
        """The build and the caught call, after the checks a turn makes before either."""
        pinned, zones = yield from pinned_in(HarnessFiles(files))
        if not self._reaches_pin(pinned, zones, held, session_id=session_id):
            raise unknown_session(session_id)
        agent = self._agent_for(request, held=held, pinned=pinned)
        built = yield on_host(
            self._graph_for,
            request,
            self.grants.intersect(request.capabilities),
            agent=agent,
            held=held,
            files=files,
            session_id=session_id,
        )
        name = agent.name if agent is not None else ""
        # As a turn's teardown does: the caught call can have started the code
        # interpreter's runtime, and one left open deadlocks the process at exit.
        try:
            sent = yield on_host(
                first_call, built, request.task, agent=name, session_id=session_id
            )
        except BaseException:
            yield on_host(release_interpreter, self.cfg, built.graph)
            raise
        yield on_host(release_interpreter, self.cfg, built.graph)
        return sent
