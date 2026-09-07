"""What a turn sees: the deployment's catalogue, plus the session's own."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher import layout
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.skills.catalogue import LocalSkillRepository
from kingfisher.subagents.catalogue import LocalSubagentRepository
from kingfisher.subagents.reading import DIRECTORY as SUBAGENT_DIRECTORY

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.ports import SkillRepository, SubagentRepository
    from kingfisher.subagents.spec import SubagentSpec


def uploaded_skills(session_dir: Path) -> Path:
    """Where this session's own skills were unpacked."""
    return Path(session_dir) / layout.SKILLS / layout.UPLOADED_SKILL_DIR


def uploaded_subagents(session_dir: Path) -> Path:
    """Where this session's own subagents were unpacked."""
    return Path(session_dir) / SUBAGENT_DIRECTORY


@dataclass(frozen=True)
class LayeredSkills:
    """The catalogue's skills and a session's, as one listing."""

    base: SkillRepository
    overlay: SkillRepository

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.base.names) | set(self.overlay.names)))

    def files(self, name: str) -> Mapping[str, str]:
        """The session's copy if it has one, otherwise the catalogue's."""
        try:
            return self.overlay.files(name)
        except KeyError:
            return self.base.files(name)


@dataclass(frozen=True)
class LayeredSubagents:
    """The catalogue's subagents and a session's, as one mapping."""

    base: SubagentRepository
    overlay: SubagentRepository

    @property
    def specs(self) -> Mapping[str, SubagentSpec]:
        # A new dict, not the catalogue's: its copy is cached and shared by
        # every turn, and merging into it would leak one session's uploads into
        # the next one's view.
        return dict(self.base.specs) | dict(self.overlay.specs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.specs)

    @property
    def bundles(self) -> Mapping[str, Any]:
        """The catalogue's, never the session's, whatever the session holds."""
        # `getattr`, because `SubagentRepository` is a port and only a store
        # backed by a filesystem has folders to find a bundle in -- the same
        # question `catalogue_root` asks about a root.
        return getattr(self.base, "bundles", {})


def for_session(catalogue: Definitions, session_dir: Path | None) -> Definitions:
    """The catalogue as one turn sees it."""
    if session_dir is None:
        return catalogue
    return replace(
        catalogue,
        skills=LayeredSkills(
            base=catalogue.skills,
            overlay=LocalSkillRepository(uploaded_skills(session_dir)),
        ),
        subagents=LayeredSubagents(
            base=catalogue.subagents,
            overlay=LocalSubagentRepository(uploaded_subagents(session_dir)),
        ),
        # `tools` is deliberately not layered -- see the module docstring.
    )
