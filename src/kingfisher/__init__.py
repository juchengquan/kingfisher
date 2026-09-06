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
#: **A caller means a caller outside this wheel.** That is new, and it is the
#: rule the eleven above were removed under without anyone saying so. The command
#: ships in this distribution and is family: it reaches a name this table does
#: not carry at the module defining it, exactly as `build_backend`'s callers do,
#: and still comes through the front door for every name that *is* here. The
#: service is a distribution of its own and keeps the strict rule, because a
#: promise is what it has instead of a shared wheel.
#:
#: Applied to the command, the old reading did the opposite of what a door is
#: for: `doctor` wanting a sandbox probe turned the probe into a promise made to
#: everybody. `Confinement` and `unrunnable_delegates` are the first two out --
#: both were recorded here as forced public by a consumer, which was this table
#: saying out loud that nobody outside had asked. See *The front door* in
#: `docs/decisions.md`.
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
    # The sixth name a consumer has forced public. The service owns
    # `KINGFISHER_SERVICE_FILE_STORE_FACTORY` -- a `FileStore` resolves refs,
    # which is the vocabulary of a caller with no host paths, and the library's
    # own command has no use for one -- so the service is what turns that
    # setting into a store, and it takes `kingfisher` and nothing deeper. A
    # wrapper rather than the generic `wiring.store_named`, which would need
    # `FileStore` exported too for the caller to pass as `port=`.
    "file_store_named": "kingfisher.infrastructure.workspace.files",
    "LocalSessionStore": "kingfisher.infrastructure.session_store",
    # The port's contract, for a deployment checking its own adapter against it.
    # Public because it is the one thing in `testing` anybody outside this
    # repository is meant to import -- and public *here* rather than left to
    # `import kingfisher.testing`, because `READ_ELSEWHERE` says a name this
    # package publishes belongs in `__all__` and reaching for that table instead
    # is how something gets published without saying so.
    "SESSION_STORE_CONTRACT": "kingfisher.testing",
    # The same, for the other port a deployment can now name. `Planted` comes
    # with it and is not optional: `FileStore` has no verb for writing, so a
    # check cannot put the file it then reads there, and the deployment has to
    # hand over what it planted.
    "FILE_STORE_CONTRACT": "kingfisher.testing",
    "Planted": "kingfisher.testing",
    "UnknownSessionError": "kingfisher.domain.session",
    "UploadError": "kingfisher.infrastructure.workspace.uploads",
    "Config": "kingfisher.config",
    "WorkspacePaths": "kingfisher.config",
    "Kingfisher": "kingfisher.application.service",
    "ConfigError": "kingfisher.config",
    "Request": "kingfisher.domain.request",
    "RunOn": "kingfisher.subagents.spec",
    "RunEvent": "kingfisher.domain.result",
    "RunResult": "kingfisher.domain.result",
    "SessionInfo": "kingfisher.domain.session",
    "definitions_source": "kingfisher.infrastructure.workspace.seeding",
    "ensure_layout": "kingfisher.infrastructure.workspace.layout",
    "kinds_at": "kingfisher.infrastructure.workspace.seeding",
    "seed": "kingfisher.infrastructure.workspace.seeding",
    "Seeded": "kingfisher.infrastructure.workspace.seeding",
    "inventory": "kingfisher.application.inventory",
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
    "FILE_STORE_CONTRACT",
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
    "Held",
    "Inventory",
    "Kingfisher",
    "LocalFileStore",
    "LocalSessionStore",
    "Origin",
    "Origins",
    "Planted",
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
    "config_from_env",
    "definitions_source",
    "ensure_layout",
    "file_store_named",
    "inventory",
    "kinds_at",
    "offered",
    "paths_from_env",
    "run",
    "seed",
    "spell",
    "split_reference",
    "stream",
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
    from kingfisher.domain.access import spell as spell
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
    from kingfisher.domain.session import SessionInfo as SessionInfo
    from kingfisher.domain.session import UnknownSessionError as UnknownSessionError
    from kingfisher.infrastructure.session_store import (
        LocalSessionStore as LocalSessionStore,
    )
    from kingfisher.infrastructure.workspace.files import LocalFileStore as LocalFileStore
    from kingfisher.infrastructure.workspace.files import (
        file_store_named as file_store_named,
    )
    from kingfisher.infrastructure.workspace.layout import ensure_layout as ensure_layout
    from kingfisher.infrastructure.workspace.seeding import SEED_HINT as SEED_HINT
    from kingfisher.infrastructure.workspace.seeding import Seeded as Seeded
    from kingfisher.infrastructure.workspace.seeding import (
        definitions_source as definitions_source,
    )
    from kingfisher.infrastructure.workspace.seeding import kinds_at as kinds_at
    from kingfisher.infrastructure.workspace.seeding import seed as seed
    from kingfisher.infrastructure.workspace.uploads import UploadError as UploadError
    from kingfisher.skills.catalogue import SKILL_LAYOUT as SKILL_LAYOUT
    from kingfisher.skills.spec import SkillError as SkillError
    from kingfisher.subagents.spec import RunOn as RunOn
    from kingfisher.subagents.spec import SubagentError as SubagentError
    from kingfisher.testing import FILE_STORE_CONTRACT as FILE_STORE_CONTRACT
    from kingfisher.testing import SESSION_STORE_CONTRACT as SESSION_STORE_CONTRACT
    from kingfisher.testing import Planted as Planted
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
