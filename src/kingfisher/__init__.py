"""kingfisher — a personal, local, general-purpose agent built on deepagents.

    from kingfisher import run
    result = run("Profile /data/sales.csv and report what stands out.")
    print(result.answer, result.run_dir)

Names resolve lazily. Importing anything from `kingfisher.infrastructure` pulls in
deepagents, which imports langchain-anthropic, langchain-openai *and*
langchain-google-genai at module level -- about 1.1s, most of it provider SDKs
this deployment will never call. Eager re-exports here made every consumer pay
that: `--help`, a config check, or a test that only touches `Request`.

The names and their spelling are unchanged; only the moment of import moved.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

#: Public name -> the module that defines it. The single source for both
#: `__getattr__` and `__all__`, so the two cannot drift.
#:
#: Eleven names left this table without leaving the package: `build_agent`,
#: `build_backend`, `build_model`, `system_prompt`,
#: `writable_data`, `protect_data`, `shell_env`, `normalize_answer`,
#: `AccessReport`, `Groups` and `Stated`. Every one is live -- `build_backend`
#: alone is called from dozens of places -- and every one of those callers
#: imports it from the module that defines it. Nothing had ever come through the
#: front door for them.
#:
#: That is the distinction this table exists to draw and had stopped drawing.
#: The comments below record why a name is public one at a time -- "the fourth
#: name the consumer rule has forced public", "public because it reached" --
#: and nothing recorded the opposite, so a name added on a guess looked exactly
#: like a name added on a caller. A door advertising what nobody walks through
#: cannot answer the only question asked of it, which is what a caller may rely
#: on.
#:
#: Narrowed rather than deleted. `from kingfisher.infrastructure.harness.agent
#: import build_agent` still works and is what the package itself does. An
#: outside caller on the old spelling changes one import line, which is a real
#: cost measured against users this repository cannot see -- accepted at 0.1.0,
#: and noted here rather than discovered later.
_EXPORTS = {
    "ALL": "kingfisher.domain.capabilities",
    "AUDIENCED": "kingfisher.domain.access",
    "spell": "kingfisher.domain.access",
    "Audience": "kingfisher.domain.access",
    "Held": "kingfisher.domain.access",
    "AccessError": "kingfisher.domain.access",
    "UNSCOPED": "kingfisher.domain.access",
    "Capabilities": "kingfisher.domain.capabilities",
    "CapabilityError": "kingfisher.domain.capabilities",
    "QuotaExceededError": "kingfisher.domain.session",
    "SessionBusyError": "kingfisher.domain.session",
    "SkillError": "kingfisher.skills.spec",
    "SubagentError": "kingfisher.subagents.spec",
    "UnknownReferenceError": "kingfisher.domain.references",
    "UnsafeReferenceError": "kingfisher.domain.references",
    "LocalFileStore": "kingfisher.infrastructure.workspace.files",
    "LocalSessionStore": "kingfisher.infrastructure.session_store",
    # The port's contract, for a deployment checking its own adapter against it.
    # Public because it is the one thing in `testing` anybody outside this
    # repository is meant to import -- and public *here* rather than left to
    # `import kingfisher.testing`, because `READ_ELSEWHERE` says a name this
    # package publishes belongs in `__all__` and reaching for that table instead
    # is how something gets published without saying so.
    "SESSION_STORE_CONTRACT": "kingfisher.testing",
    "UnknownSessionError": "kingfisher.domain.session",
    "UploadError": "kingfisher.infrastructure.workspace.uploads",
    "Config": "kingfisher.config",
    # The fourth name the consumer rule has forced public, and the first
    # that improved the library on its own: `resolve` takes six arguments
    # and two callers were assembling them from the same `Config`.
    "Confinement": "kingfisher.infrastructure.sandbox.confinement",
    "bubblewrap_available": "kingfisher.infrastructure.sandbox.bubblewrap",
    "landlock_abi": "kingfisher.infrastructure.sandbox.confinement",
    "shell_confinement": "kingfisher.infrastructure.sandbox.confinement",
    "WorkspacePaths": "kingfisher.config",
    "Kingfisher": "kingfisher.application.service",
    "ConfigError": "kingfisher.config",
    "Request": "kingfisher.domain.request",
    "RunOn": "kingfisher.subagents.spec",
    "RunEvent": "kingfisher.domain.result",
    "RunResult": "kingfisher.domain.result",
    "SessionInfo": "kingfisher.domain.session",
    "definitions_source": "kingfisher.infrastructure.workspace.seeding",
    # A sentence, for the reason `SEED_HINT` is one: the refusal and
    # `doctor` both tell a reader where their own definitions go, and two
    # spellings of that is how the advice starts disagreeing with itself.
    "destination_hint": "kingfisher.infrastructure.workspace.seeding",
    "ensure_layout": "kingfisher.infrastructure.workspace.layout",
    "kinds_at": "kingfisher.infrastructure.workspace.seeding",
    "memory_backing": "kingfisher.infrastructure.workspace.backing",
    "seed": "kingfisher.infrastructure.workspace.seeding",
    "Seeded": "kingfisher.infrastructure.workspace.seeding",
    "inventory": "kingfisher.application.inventory",
    # The fifth name a consumer has forced public. A purpose-built answer
    # rather than `model_for` itself, which the design named: `doctor` wants
    # *which definitions cannot run*, and exporting the two calls it would
    # need to combine would promise a recipe instead of an answer.
    "unrunnable_delegates": "kingfisher.infrastructure.harness.activation",
    # Reached for by `kingfisher.presentation.cli`, and public because it
    # reached. A
    # renderer in the domain looks odd until you see what it is for: the
    # block a *refusal* prints is the block a listing prints, so a name two
    # files define reads the same in both. A consumer rendering its own
    # would be the drift that rule exists to stop.
    "offered": "kingfisher.tools.spec",
    # The third name a consumer turned out to need, and it arrived the same
    # way: two folders may each define a `surveyor`, so a listing has to tell
    # a bare name from a `where::what` reference before deciding whether to
    # print the file it came from.
    "split_reference": "kingfisher.tools.spec",
    # Said once "so callers can quote it without knowing the filename
    # themselves", by its own comment. It was `skill_store.LAYOUT` with one
    # caller; renamed because a bare `LAYOUT` at the top level sits next to
    # `LAYOUT_DIRS` and means something else.
    "DEFINITION_KINDS": "kingfisher.infrastructure.catalogue",
    "SEED_HINT": "kingfisher.infrastructure.workspace.seeding",
    "SKILL_LAYOUT": "kingfisher.skills.catalogue",
    "Inventory": "kingfisher.application.inventory",
    # Where this deployment reads from, as against what it offers. Public
    # because the answer was assembled three times and agreed nowhere: the
    # command printed four of eleven places, `doctor` printed one, and a
    # library caller had no way to ask at all.
    "Origin": "kingfisher.application.origins",
    "Origins": "kingfisher.application.origins",
    "config_from_env": "kingfisher.application.config",
    "paths_from_env": "kingfisher.application.config",
    "run": "kingfisher.application.run",
    "stream": "kingfisher.application.run",
}

__all__ = [
    "ALL",
    "AUDIENCED",
    "DEFINITION_KINDS",
    "SEED_HINT",
    "SESSION_STORE_CONTRACT",
    "SKILL_LAYOUT",
    "UNSCOPED",
    "AccessError",
    "Audience",
    "Capabilities",
    "CapabilityError",
    "Config",
    "ConfigError",
    "Confinement",
    "Held",
    "Inventory",
    "Kingfisher",
    "LocalFileStore",
    "LocalSessionStore",
    "Origin",
    "Origins",
    "QuotaExceededError",
    "Request",
    "RunEvent",
    "RunOn",
    "RunResult",
    "Seeded",
    "SessionBusyError",
    "SessionInfo",
    "SkillError",
    "SubagentError",
    "UnknownReferenceError",
    "UnknownSessionError",
    "UnsafeReferenceError",
    "UploadError",
    "WorkspacePaths",
    "bubblewrap_available",
    "config_from_env",
    "definitions_source",
    "destination_hint",
    "ensure_layout",
    "inventory",
    "kinds_at",
    "landlock_abi",
    "memory_backing",
    "offered",
    "paths_from_env",
    "run",
    "seed",
    "shell_confinement",
    "spell",
    "split_reference",
    "stream",
    "unrunnable_delegates",
]

if TYPE_CHECKING:
    # So type checkers and IDEs see the real symbols rather than `Any`.
    # Redundant aliases mark these as re-exports; `__all__` is computed, so a
    # checker cannot otherwise tell they are public.
    from kingfisher.application.config import config_from_env as config_from_env
    from kingfisher.application.config import paths_from_env as paths_from_env
    from kingfisher.application.inventory import Inventory as Inventory
    from kingfisher.application.inventory import inventory as inventory
    from kingfisher.application.origins import Origin as Origin
    from kingfisher.application.origins import Origins as Origins
    from kingfisher.application.run import run as run
    from kingfisher.application.run import stream as stream
    from kingfisher.application.service import Kingfisher as Kingfisher
    from kingfisher.config import Config as Config
    from kingfisher.config import ConfigError as ConfigError
    from kingfisher.config import WorkspacePaths as WorkspacePaths
    from kingfisher.domain.access import AUDIENCED as AUDIENCED
    from kingfisher.domain.access import UNSCOPED as UNSCOPED
    from kingfisher.domain.access import AccessError as AccessError
    from kingfisher.domain.access import Audience as Audience
    from kingfisher.domain.access import Held as Held
    from kingfisher.domain.capabilities import ALL as ALL
    from kingfisher.domain.capabilities import Capabilities as Capabilities
    from kingfisher.domain.capabilities import CapabilityError as CapabilityError
    from kingfisher.domain.references import (
        UnknownReferenceError as UnknownReferenceError,
    )
    from kingfisher.domain.references import UnsafeReferenceError as UnsafeReferenceError
    from kingfisher.domain.request import Request as Request
    from kingfisher.domain.result import RunEvent as RunEvent
    from kingfisher.domain.result import RunResult as RunResult
    from kingfisher.domain.session import QuotaExceededError as QuotaExceededError
    from kingfisher.domain.session import SessionBusyError as SessionBusyError
    from kingfisher.domain.session import UnknownSessionError as UnknownSessionError
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS as DEFINITION_KINDS
    from kingfisher.infrastructure.harness.activation import (
        unrunnable_delegates as unrunnable_delegates,
    )
    from kingfisher.infrastructure.sandbox.bubblewrap import (
        bubblewrap_available as bubblewrap_available,
    )
    from kingfisher.infrastructure.sandbox.confinement import Confinement as Confinement
    from kingfisher.infrastructure.sandbox.confinement import landlock_abi as landlock_abi
    from kingfisher.infrastructure.sandbox.confinement import (
        shell_confinement as shell_confinement,
    )
    from kingfisher.infrastructure.session_store import (
        LocalSessionStore as LocalSessionStore,
    )
    from kingfisher.infrastructure.workspace.backing import memory_backing as memory_backing
    from kingfisher.infrastructure.workspace.files import LocalFileStore as LocalFileStore
    from kingfisher.infrastructure.workspace.layout import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace.seeding import SEED_HINT as SEED_HINT
    from kingfisher.infrastructure.workspace.seeding import Seeded as Seeded
    from kingfisher.infrastructure.workspace.seeding import (
        definitions_source as definitions_source,
    )
    from kingfisher.infrastructure.workspace.seeding import (
        destination_hint as destination_hint,
    )
    from kingfisher.infrastructure.workspace.seeding import kinds_at as kinds_at
    from kingfisher.infrastructure.workspace.seeding import seed as seed
    from kingfisher.infrastructure.workspace.uploads import UploadError as UploadError
    from kingfisher.skills.catalogue import SKILL_LAYOUT as SKILL_LAYOUT
    from kingfisher.skills.spec import SkillError as SkillError
    from kingfisher.subagents.spec import RunOn as RunOn
    from kingfisher.subagents.spec import SubagentError as SubagentError
    from kingfisher.testing import SESSION_STORE_CONTRACT as SESSION_STORE_CONTRACT
    from kingfisher.tools.spec import offered as offered
    from kingfisher.tools.spec import split_reference as split_reference


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export."""
    try:
        module = _EXPORTS[name]
    except KeyError:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from None

    value = getattr(import_module(module), name)
    globals()[name] = value  # resolve once; subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return __all__
