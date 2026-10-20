"""The service: wired once, then asked to run things.

Each request builds its agent afresh on purpose: construction is cheap beside a turn.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import (
    UNRESTRICTED,
    AccessReport,
    Capabilities,
    CapabilityError,
    Vocabulary,
    audit,
    reaches,
)
from kingfisher.core.agent.activation import defined_subagents
from kingfisher.core.agent.assembly import (
    build_agent,
    builtin_tool_names,
)
from kingfisher.core.agent.kinds.declared_middleware import (
    MiddlewareFactory,
    refuse_unbuildable_middleware,
)
from kingfisher.core.agent.prompting import InstructionsFile, refuse_a_workspace_prompt
from kingfisher.core.agent.usable_backend import refuse_unusable_backend
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.definitions.agents.spec import AgentSpec
from kingfisher.core.definitions.catalogue import Definitions, resolve_definitions
from kingfisher.core.definitions.seeding import SEED_HINT, starter_agent
from kingfisher.core.session.disposal import Disposal
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.methods import SessionMethods
from kingfisher.core.session.model import opened_for
from kingfisher.core.session.preparation import Preparation
from kingfisher.core.session.runlog import LoggedRunEvents
from kingfisher.core.session.running import Running
from kingfisher.core.session.steps import (
    Steps,
    changing,
)
from kingfisher.core.session.store import (
    HarnessFiles,
    remember_agent,
)
from kingfisher.core.session.threads import thread_pool
from kingfisher.core.session.unused import DeleteUnusedResults

from . import environment as config_module
from .config import Config, ConfigError
from .interfaces import Instructions, Sessions, UnusedResults
from .origins import Origins
from .request import Request, Resume
from .workspace import ensure_layout

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.definitions.documents import DefinitionText

    from .interfaces import RunEvents


#: `kingfisher.origins`, and deliberately not `kingfisher`.
logger = logging.getLogger("kingfisher.origins")


class Kingfisher(SessionMethods, Disposal, Preparation, Running):
    """A configured kingfisher. Construct once; call `run` or `stream` per request."""

    def __init__(  # noqa: PLR0913 -- the composition root: each argument is substitutable
        self,
        cfg: Config | None = None,
        *,
        # No default: the backend is the sandbox, and a default would let a deployment
        # wire the service without learning there was a boundary. Required beside a
        # pre-built graph too, whose builder alone knows where a session is kept.
        sessions: Sessions,
        middlewares: Mapping[str, MiddlewareFactory] | None = None,
        graph: Any | None = None,
        run_events: RunEvents | None = None,
        # Left out, `INSTRUCTIONS.md` beside the definitions. Asked once per agent built,
        # so a change reaches the next turn.
        instructions: Instructions | None = None,
        # Left out, each saved result nothing names is removed.
        unused_results: UnusedResults | None = None,
    ) -> None:
        self.cfg = cfg or config_module.config_from_env()
        refuse_a_workspace_prompt(self.cfg)
        config_module.enforce_local_only_tracing()
        # Asked for here rather than at the first async turn, so a second size in
        # one process is refused where the service is built.
        self._pool = thread_pool(self.cfg.thread_pool_size)

        # Only what sessions share: a session's own path is known once a request names it.
        self.workspace: Path = ensure_layout(self.cfg.workspace)

        # Read and parsed now, not per request: a catalogue that cannot be read is a
        # wiring mistake, to fail at startup rather than serve an agent told about nothing.
        self.catalogue: Definitions = resolve_definitions(self.cfg).warm()
        # Whether an offered tool wears a built-in's name, refused at startup: only an
        # assembled graph can say, so `warm` cannot. A subagent's own tools as well as the
        # catalogue's: a bundled `read_file` replaced deepagents' own in that subagent and
        # crashed every model call it made, and nothing said so -- `doctor` called the
        # workspace healthy.
        if self.catalogue.tools.found or any(
            repository.found for repository in self.catalogue.bundled_tools.values()
        ):
            # Only `ConfigError`: the deployment could not be assembled, which the turn and
            # `doctor` report, and raised here it would stop the `sessions` and `reap`
            # commands, which need no model. `CapabilityError` still refuses.
            with suppress(ConfigError):
                builtin_tool_names(self.cfg, self.catalogue)

        # Sessions, not a backend: one backend shared by every session is one filesystem
        # for every caller, and nothing at the call site would look wrong.
        if not isinstance(sessions, Sessions):
            msg = (
                "sessions= takes a Sessions, which opens each session's backend "
                "and answers for every session: which there are, how big, when each was "
                "used, and deleting one. A backend is rooted at a session, so sharing one "
                "is sharing a filesystem between callers. To build on kingfisher's own, "
                "subclass LocalSessions and override open; to build your own, subclass "
                "Sessions, which brings aopen and asessions with it"
            )
            raise TypeError(msg)
        self.run_events: RunEvents = (
            run_events if run_events is not None else LoggedRunEvents()
        )
        # `intersect` can only subtract, so no request can widen past this.
        self.grants: Capabilities = (
            self.cfg.grants if self.cfg.grants is not None else UNRESTRICTED
        )
        # What a definition may name under `middlewares:`. Registering is not permitting:
        # `grants` still clamps which registered names a request may reach.
        self.middlewares: Mapping[str, MiddlewareFactory] = middlewares or {}
        self.instructions: Instructions = instructions or InstructionsFile()
        self.unused_results: UnusedResults = (
            unused_results if unused_results is not None else DeleteUnusedResults()
        )
        # Here, not when a definition names one, so the deployment hears it where it
        # wired the registry rather than from the first caller to reach the wrong name.
        refuse_unbuildable_middleware(self.middlewares)
        self._sessions: Sessions = sessions
        self._graph = graph
        # Collected rather than read from a file: a lock written anywhere is in force, so no
        # missing file can leave one inert.
        self.vocabulary: Vocabulary | None = self.catalogue.vocabulary
        self.access_report: AccessReport = AccessReport()
        if self.vocabulary is not None:
            # Tools are not audited: one stating no audience is open to whoever opens an
            # agent granting it.
            self.access_report = audit(
                ("agent", self.catalogue.agents.specs),
                ("subagent", defined_subagents(self.cfg, catalogue=self.catalogue)),
            )

        # Last, so the line reports what was resolved, and a wiring failure raises
        # instead of announcing a deployment that never came up.
        if logger.isEnabledFor(logging.INFO):
            logger.info("reading from: %s", self.origins.line())

    @property
    def origins(self) -> Origins:
        """Where this deployment is actually reading from."""
        return Origins.of(
            self.cfg,
            catalogue=self.catalogue,
            sessions=self._sessions,
        )

    def _graph_for(  # noqa: PLR0913 -- setup holds each; deriving one here derives it twice
        self,
        request: Request | Resume,
        capabilities: Capabilities,
        checkpointer: Any = None,
        *,
        agent: AgentSpec | None,
        held: frozenset[str] | None,
        files: Any,
        ledger: Ledger | None = None,
    ) -> Any:
        """What serves one request, rooted at its session.

        `Assembled` for a build made here; a supplied graph comes back as itself on this
        turn's saver, because an `Assembled` for it would assert a build that never ran.
        """
        if self._graph is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built graph"
                raise ValueError(msg)
            # On this turn's saver, never its own: its own would replay every earlier turn
            # beside the transcript, or keep no state to write one, or lose a pause.
            return self._graph.copy(update={"checkpointer": checkpointer})

        return build_agent(
            self.cfg,
            # Resolved by setup, which needs the same spec for the withheld report.
            agent=agent,
            held=held,
            # The one setup opened, holding the inputs: opened again, a remote backend
            # would be a second sandbox without them.
            backend=files,
            # Never `request.capabilities`, which would hand a caller a tool the
            # deployment withheld.
            capabilities=capabilities,
            run_on=request.run_on,
            middleware_registry=self.middlewares,
            instructions=self.instructions.text(self.cfg),
            checkpointer=checkpointer,
            catalogue=self.catalogue,
            ledger=ledger,
        )

    def _files_for(self, session_id: str) -> Steps[Any]:
        """The backend a session's files are reached through, by kingfisher and any agent it builds.

        What runs its commands is the sessions' choice: only they know a session's host.
        """
        files = yield changing(
            self._sessions, "open", self.cfg, session_id, catalogue=self.catalogue
        )
        # Here as well as at the build: a pre-built graph has none, and setup reads the
        # session through this backend before any build.
        refuse_unusable_backend(files, self.cfg.layout)
        # Every backend kingfisher opens comes through here, so a turn handed one as
        # `files=` can tell whose it is.
        opened_for(files, session_id)
        return files

    def _pin_agent_in(self, harness: HarnessFiles, name: str | None) -> Steps[None]:
        """Keep the agent, in the session's own files.

        Never at a path rebuilt from the id: that is the session only under the default
        `session_root`, and a pin written elsewhere is never read back.
        """
        if name is None:
            return
        if (text := self.catalogue.agents.documents.get(name)) is not None:
            yield from remember_agent(harness, text)

    def _agent_for(
        self,
        request: Request | Resume,
        harness: HarnessFiles,
        *,
        held: frozenset[str] | None,
        pinned: DefinitionText | None,
    ) -> Steps[AgentSpec | None]:
        """The agent this turn runs, which is the one its session opened with, `pinned`."""
        if pinned is None:
            spec = self._agent_named(request.agent, held=held)
            yield from self._pin_agent_in(harness, request.agent)
            return spec

        started = read(pinned)
        if request.agent is not None and request.agent != started.name:
            msg = (
                f"this session is running {started.name!r}; it was fixed when the "
                f"session opened and cannot be changed to {request.agent!r} "
                f"mid-conversation -- start a session to run a different agent"
            )
            raise CapabilityError(msg)
        return started

    def _agent_named(self, name: str | None, *, held: frozenset[str] | None) -> AgentSpec | None:
        """The agent this request asked for, out of the catalogue.

        `held` as `caller_holds` answered it, never `held_by`: `None` offers every agent,
        which for a caller nobody named under a policy is the hole `caller_holds` refuses.
        """
        offered = self.catalogue.agents.specs
        # Filtered before the listing is built: an agent out of reach must read exactly
        # like one never written, or a caller could enumerate the catalogue by guessing.
        if held is not None:
            offered = {
                n: spec for n, spec in offered.items() if reaches(spec.source_ids, held)
            }
        listing = ", ".join(sorted(offered)) if offered else "none"
        # With nothing offered, a starter file as well as `SEED_HINT`, whose `--from DIR`
        # needs a DIR a reader who only installed the package may not have.
        root = self.catalogue.agents.root
        starter = "" if root is None else f", or write one:\n\n{starter_agent(root)}"
        empty = "" if offered else f" -- try {SEED_HINT}{starter}"
        if name is None:
            msg = f"this request names no agent; this workspace offers {listing}{empty}"
            raise CapabilityError(msg)
        spec = offered.get(name)
        if spec is None:
            msg = f"no agent named {name!r}; this workspace offers {listing}{empty}"
            raise CapabilityError(msg)
        return spec


