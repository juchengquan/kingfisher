"""What a turn's first model call would be sent, asked without running the turn."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import caller_holds
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.agent.sent import first_call

from .model import SessionBusyError
from .steps import Steps, adrive, changing, drive, on_host
from .store import HarnessFiles
from .turnlog import read_log

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.access import Capabilities, Held
    from kingfisher.core.config import Config
    from kingfisher.core.definitions.agents.spec import AgentSpec
    from kingfisher.core.definitions.catalogue import Definitions
    from kingfisher.core.request import Request
    from kingfisher.core.result import Sent
    from kingfisher.core.versions import Versions

    from .model import Conversation


class FirstCall:
    """Building a turn's agent as the turn would, and showing what it would send."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _versions: Versions
    grants: Capabilities
    _sessions: Any
    _graph: Any
    _session_for: Callable[..., Steps[Conversation]]
    _known_steps: Callable[[], Steps[tuple[str, ...]]]
    _open_files: Callable[..., Steps[Any]]
    _under_claim: Callable[..., Steps[Any]]
    _agent_named: Callable[..., AgentSpec]
    _graph_for: Callable[..., Any]

    def sent(self, request: Request, *, source_ids: Held | None = None) -> Sent:
        """What the first model call of `request`'s turn would be sent, with no turn run.

        Built in the session the request names, as its turn would be, and caught before
        the call leaves: no model is called, and a session already there is neither
        claimed nor written to. One made in order to look is removed again, under its
        claim, unless a turn has started in it meanwhile.
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
        # Once, as a turn asks.
        catalogue = yield on_host(self._versions.current)
        session = yield from self._session_for(request)
        held = caller_holds(catalogue.vocabulary, source_ids)
        made = session.id not in (yield from self._known_steps())
        files = yield from self._open_files(session.id, catalogue=catalogue)
        try:
            return (yield from self._looked(request, session.id, files, held, catalogue))
        finally:
            if made:
                # Refused, the claim is a turn's that started on this id during the look,
                # and so is the session.
                with suppress(SessionBusyError):
                    yield from self._under_claim(
                        session.id, "the look leaves it", self._unused_removed, session.id, files
                    )

    def _unused_removed(self, session_id: str, files: Any) -> Steps[None]:
        """Delete a session this look made, unless a turn ran in it during the look."""
        if not (yield from read_log(HarnessFiles(files))).written:
            yield changing(self._sessions, "delete", self.cfg, session_id)

    def _looked(
        self,
        request: Request,
        session_id: str,
        files: Any,
        held: frozenset[str] | None,
        catalogue: Definitions,
    ) -> Steps[Sent]:
        """The build and the caught call, after the check a turn makes before either."""
        agent = self._agent_named(request.agent, held=held, catalogue=catalogue)
        built = yield on_host(
            self._graph_for,
            request,
            self.grants.intersect(request.capabilities),
            agent=agent,
            held=held,
            files=files,
            session_id=session_id,
            catalogue=catalogue,
        )
        # As a turn's teardown does: the caught call can have started the code
        # interpreter's runtime, and one left open deadlocks the process at exit.
        try:
            return (
                yield on_host(
                    first_call, built, request.task, agent=agent.name, session_id=session_id
                )
            )
        finally:
            yield on_host(release_interpreter, self.cfg, built.graph)
