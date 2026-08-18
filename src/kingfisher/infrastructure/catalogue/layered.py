"""What a turn sees: the deployment's catalogue, plus the session's own.

A session may upload definitions of its own, and they have to reach the agent
alongside the reviewed ones. That merge existed twice in `agent.py` as two
inline expressions, and the two quietly did different things -- a sorted set
union for skills, a right-wins `dict |` for subagents -- with nothing anywhere
saying why. Here each rule is a class, so the difference is legible and the
reason is written next to it.

The composition is what keeps the reading cheap. A catalogue's repositories are
built once when the deployment is wired and answer every turn from what they
read then; a session's are built for the turn that needs them, because uploads
arrive per request and cannot be read in advance. Layering the two is the shape
that lets the expensive half stay where it is: `for_session` wraps rather than
rebuilds, so nothing re-reads the catalogue to add one uploaded file to it.

A layer is itself a repository, which is the whole reason `AssetRepository` is a
port and not a base class. Nothing downstream can tell a layered view from a
plain one, so `build_agent` keeps taking one `Definitions` and knows nothing about
sessions.

Tools have no layer, and that is not an oversight. A tool is Python that gets
imported into this process, and a session cannot upload one -- `uploads` accepts
`skill_refs` and `subagent_refs` and nothing else. Adding a layer for
symmetry would advertise a capability that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.domain import skill
from kingfisher.domain.subagent import DIRECTORY as SUBAGENT_DIRECTORY
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.ports import SkillRepository, SubagentRepository
    from kingfisher.domain.subagent import SubagentSpec


def uploaded_skills(session_dir: Path) -> Path:
    """Where this session's own skills were unpacked."""
    return Path(session_dir) / skill.DIRECTORY / skill.UPLOADED


def uploaded_subagents(session_dir: Path) -> Path:
    """Where this session's own subagents were unpacked."""
    return Path(session_dir) / SUBAGENT_DIRECTORY


@dataclass(frozen=True)
class LayeredSkills:
    """The catalogue's skills and a session's, as one listing.

    A flat set, because `capabilities.skills` names skills and not sources: a
    request granting `code-review` should not have to know which half offered
    it. Sorted so two sessions holding the same names build the same agent.

    Nothing is overridden here, because nothing can be: `uploads` refuses an
    upload that shares a catalogue name before it is written, so the two halves
    are disjoint by the time this sees them. Were that check ever removed, a
    union would silently keep both and the *subagent* rule below would silently
    prefer one -- which is why these are two classes and not one generic merge.
    """

    base: SkillRepository
    overlay: SkillRepository

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.base.names) | set(self.overlay.names)))

    def files(self, name: str) -> Mapping[str, str]:
        """The session's copy if it has one, otherwise the catalogue's.

        The overlay wins, which is the *subagent* rule rather than the union
        above -- and it has to be, because a union is not a thing you can do to
        two sets of file contents. Unreachable today for the same reason as
        everywhere else here (`uploads` refuses a name the catalogue defines),
        so what it settles is which way to fall if that check ever fails: to the
        one request's own copy, never the catalogue every request shares.
        """
        try:
            return self.overlay.files(name)
        except KeyError:
            return self.base.files(name)


@dataclass(frozen=True)
class LayeredSubagents:
    """The catalogue's subagents and a session's, as one mapping.

    The session wins a collision. That is unreachable today for the same reason
    as above -- `uploads` refuses a name the catalogue already defines -- so what
    this rule really settles is which way to fall if that check ever fails: to
    the definition belonging to the one request, not to the reviewed catalogue
    every other request shares. A session may then only harm itself.
    """

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


def for_session(catalogue: Definitions, session_dir: Path | None) -> Definitions:
    """The catalogue as one turn sees it.

    Returns a `Definitions`, so every caller downstream is unchanged and none of
    them learns what a session is. `build_agent` asks for `catalogue.skills.names`
    either way.

    Handed `None`, it is the catalogue itself rather than a layer over nothing:
    a turn with no session directory has no uploads by definition, and wrapping
    two empty repositories would cost a listing of a directory that is not there
    on every call that does not need one.

    The session's half never varies with where the catalogue is: uploads land
    under the session by definition, and a deployment relocating its catalogue
    does not move them.
    """
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
