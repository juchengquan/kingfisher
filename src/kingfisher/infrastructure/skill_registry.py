"""Which skills the agent will actually have, asked of the thing that decides.

Two readers looked at this catalogue and did not agree. Kingfisher listed the
directories holding a skill file; deepagents opened each one and kept the ones
it could parse. Measured on four directories that all held one, kingfisher
advertised four names and deepagents loaded three -- two of them different.

The consequence was the failure this codebase refuses everywhere else, sitting
in the one kind it never parses. A skill with no `description` is dropped by
deepagents and advertised by kingfisher, so activating it passed validation,
allowed the name through the filter, and produced an agent with no skills at
all. Nothing said so.

So this asks deepagents. Not "parses a skill the same way deepagents does" --
that is a second opinion wearing the first one's clothes, and it would put the
name rules, the description limit and the size cap in this repo to drift against
an upstream that owns them. `SkillRepository` still answers `names` and `files`,
because *what exists to mount* is a different question from *what the agent will
be told about*, and running the two together is what produced the bug.

The private call is the price, and it is one this codebase already pays
knowingly: `WorkspaceScopedBackend` overrides `_get_backend_and_key` and says
so, pinned by a test "so a deepagents upgrade that renames it fails the build
rather than quietly removing the guard". The same applies here with more force.
A registry that silently came back empty would be the original bug again, in a
new place -- so `test_skill_registry` pins the import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from kingfisher.domain.ports import SkillRepository

#: The path a source is listed under when reading a repository directly. Not
#: `SKILLS_ROUTE`: this reads a catalogue on its own, before any session exists
#: and outside the backend an agent will eventually get, so there is no route to
#: speak of -- only the root of whatever is mounted.
ROOT = "/"


@dataclass(frozen=True)
class SkillRegistry:
    """Every skill the agent will be told about, and the ones it will not.

    `offered` is the answer to "what may a request activate", and it is the
    answer deepagents itself gave -- keyed by the name it will list the skill
    under, which is the name in the header rather than the folder. Those differ
    more often than they should, and when they do it is the header that wins.

    `unloadable` is the gap between what looks like a skill on disk and what
    deepagents kept. It is reported rather than refused, the way `misplaced`
    already is: one malformed skill should not stop a deployment starting, and
    the dangerous half -- a caller *naming* one -- is refused by validation
    reading `offered` instead of a directory listing.
    """

    offered: Mapping[str, Any]
    unloadable: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """What a request may activate, sorted, for a listing or a refusal."""
        return tuple(sorted(self.offered))

    def description(self, name: str) -> str:
        """What a skill says it is for. Empty for a name this does not hold.

        deepagents refuses a skill with no description, so every name in here
        has one -- which is why a listing can show it without a fallback, and
        why the fallback exists anyway for a caller asking about a name from
        somewhere else.
        """
        return str(self.offered.get(name, {}).get("description", ""))


def read(repository: SkillRepository, *, root: Path | None = None) -> SkillRegistry:
    """Ask deepagents what this repository offers.

    `root` is that repository's directory when it has one, and it is passed in
    rather than asked for: `catalogue_root` lives in `catalogue`, and importing
    it here would have `catalogue` and this module import each other. Reading a
    repository does not need to know what a catalogue is.

    A repository with a real directory behind it is read as a filesystem,
    because `skills_backend`
    says why not to do otherwise -- a store "holds every skill's contents for
    the life of the deployment", which is a copy worth making only when there is
    no path to read instead.

    A source-level failure -- the directory is missing, the store cannot be
    reached -- comes back as no skills rather than an exception, because that is
    what `_list_skills_with_errors` reports and because an empty catalogue is a
    thing a deployment may legitimately have. What it must not be is an empty
    catalogue nobody mentioned, which is why `unloadable` carries the difference
    and `--list` prints it.
    """
    # Deferred, and the architecture test is why: `Catalogue` holds a registry
    # and `Catalogue` is reachable from `kingfisher`'s light exports, so a
    # module-scope import here would make `from kingfisher import Config` load
    # three provider SDKs. The lister is private, which is the coupling
    # `test_skill_registry` pins -- a rename upstream fails there rather than
    # emptying this registry in silence.
    from deepagents.backends import FilesystemBackend  # noqa: PLC0415
    from deepagents.middleware.skills import (  # noqa: PLC0415
        _list_skills_with_errors,
    )

    from kingfisher.infrastructure.skills_backend import skills_backend  # noqa: PLC0415

    backend = FilesystemBackend(root_dir=str(root)) if root else skills_backend(repository)

    loaded, _source_error = _list_skills_with_errors(backend, ROOT)
    offered = {skill["name"]: skill for skill in loaded}

    # A directory that looked like a skill and did not come back. deepagents says
    # why in a warning it logs; what matters here is only which ones, so a
    # reader can go and look at the file rather than wonder why a skill they
    # wrote is not on offer.
    kept = {skill["path"] for skill in loaded}
    missing = tuple(
        sorted(
            name
            for name in repository.names
            if not any(f"/{name}/" in path for path in kept)
        )
    )
    return SkillRegistry(offered=offered, unloadable=missing)
