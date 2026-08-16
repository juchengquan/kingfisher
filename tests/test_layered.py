"""A turn's view: the deployment's catalogue with the session's own on top.

The merge used to be two inline expressions in `agent.py` that quietly did
different things -- a sorted set union for skills, a right-wins `dict |` for
subagents -- with nothing saying why. These are the rules, stated, plus the
composition that keeps the catalogue's half from being re-read to add one
uploaded file to it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from kingfisher.domain.ports import SkillRepository, SubagentRepository
from kingfisher.domain.subagent import SubagentSpec
from kingfisher.infrastructure.catalogue import Catalogue
from kingfisher.infrastructure.layered import (
    LayeredSkills,
    LayeredSubagents,
    for_session,
    uploaded_skills,
    uploaded_subagents,
)

SKILL = "---\nname: {name}\ndescription: A skill.\n---\n\nDo the thing.\n"
DEFINITION = "name: {name}\ndescription: A subagent.\nsystem_prompt: |\n  x\n"


@dataclass(frozen=True)
class InMemory:
    """A store with no directory behind it, for either kind."""

    held: dict

    @property
    def names(self):
        return tuple(self.held)

    @property
    def specs(self):
        return self.held


def _spec(name, prompt="from the catalogue"):
    return SubagentSpec(name=name, description="A subagent.", system_prompt=prompt)


def _upload_skill(session_dir, name):
    directory = uploaded_skills(session_dir) / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")


def _upload_subagent(session_dir, name, prompt="from the session"):
    directory = uploaded_subagents(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(
        DEFINITION.format(name=name).replace("  x", f"  {prompt}"), encoding="utf-8"
    )


# -- the two rules, which are not the same rule ---------------------------


def test_skills_are_a_flat_sorted_union():
    """`capabilities.skills` names skills and not sources, so a request granting
    one should not have to know which half offered it. Sorted so two sessions
    holding the same names build the same agent."""
    layered = LayeredSkills(base=InMemory({"b": 1, "a": 1}), overlay=InMemory({"c": 1}))

    assert layered.names == ("a", "b", "c")


def test_a_session_subagent_wins_a_collision():
    """Unreachable today -- `uploads` refuses a name the catalogue defines -- so
    what this settles is which way to fall if that check ever fails: to the
    definition belonging to the one request, not to the reviewed catalogue every
    other request shares. A session may then only harm itself.
    """
    layered = LayeredSubagents(
        base=InMemory({"reviewer": _spec("reviewer", "reviewed")}),
        overlay=InMemory({"reviewer": _spec("reviewer", "uploaded")}),
    )

    assert layered.specs["reviewer"].system_prompt == "uploaded"


def test_layering_does_not_write_into_the_catalogues_own_mapping():
    """The catalogue's copy is cached and shared by every turn, so merging into
    it would leak one session's uploads into the next one's view."""
    shared = {"reviewer": _spec("reviewer")}
    catalogue_side = InMemory(shared)

    merged = LayeredSubagents(base=catalogue_side, overlay=InMemory({"own": _spec("own")})).specs

    assert set(merged) == {"reviewer", "own"}
    assert set(shared) == {"reviewer"}, "the catalogue's mapping was mutated"


# -- a layer is a repository ----------------------------------------------


def test_a_layer_satisfies_the_same_port_as_what_it_layers():
    """The whole reason `AssetRepository` is a port and not a base class:
    nothing downstream can tell a layered view from a plain one."""
    skills = LayeredSkills(base=InMemory({"a": 1}), overlay=InMemory({}))
    subagents = LayeredSubagents(base=InMemory({}), overlay=InMemory({"x": _spec("x")}))

    assert isinstance(skills, SkillRepository)
    assert isinstance(subagents, SubagentRepository)


def test_the_two_halves_need_not_be_the_same_kind_of_store(cfg, session_dir):
    """A directory-backed catalogue with an in-memory session overlay is now
    expressible, and the merge rules ask only for `names` and `specs` -- so it
    works, rather than working by accident. Worth pinning: a deployment holding
    its catalogue in a database still gets uploads off the session's disk.
    """
    _upload_subagent(session_dir, "uploaded")
    catalogue = replace(
        Catalogue.from_config(cfg), subagents=InMemory({"remote": _spec("remote")})
    )

    turn = for_session(catalogue, session_dir)

    assert set(turn.subagents.specs) == {"remote", "uploaded"}


# -- the composition ------------------------------------------------------


def test_a_turns_view_is_itself_a_catalogue(cfg, session_dir):
    """Which is what keeps every caller downstream unchanged: `build_agent` asks
    for `catalogue.skills.names` whether or not a session is involved."""
    turn = for_session(Catalogue.from_config(cfg), session_dir)

    assert isinstance(turn, Catalogue)


def test_no_session_is_the_catalogue_itself_and_not_a_layer_over_nothing(cfg):
    """A turn with no session directory has no uploads by definition, and
    wrapping two empty repositories would cost a listing of a directory that is
    not there on every call that does not need one."""
    catalogue = Catalogue.from_config(cfg)

    assert for_session(catalogue, None) is catalogue


def test_the_catalogue_is_not_re_read_to_add_one_uploaded_definition(cfg, session_dir):
    """The reason this wraps rather than rebuilds. A catalogue's repositories
    are read when the deployment is wired; a session arriving with one file must
    not cost a second walk of the reviewed set.
    """
    reads = []

    @dataclass(frozen=True)
    class Counting:
        held: dict

        @property
        def specs(self):
            reads.append("read")
            return self.held

        @property
        def names(self):
            return tuple(self.held)

    _upload_subagent(session_dir, "own")
    catalogue = replace(Catalogue.from_config(cfg), subagents=Counting({"shared": _spec("shared")}))

    turn = for_session(catalogue, session_dir)
    assert set(turn.subagents.specs) == {"shared", "own"}

    assert len(reads) == 1, "the catalogue's half was read more than once"


def test_tools_are_not_layered(cfg, session_dir):
    """Not an oversight. A tool is Python imported into this process, and a
    session cannot upload one -- `uploads` accepts `skill_refs` and
    `subagent_refs` and nothing else. A layer here would advertise a capability
    that does not exist.
    """
    catalogue = Catalogue.from_config(cfg)

    turn = for_session(catalogue, session_dir)

    assert turn.tools is catalogue.tools


def test_uploads_reach_the_agents_view_of_both_kinds(cfg, session_dir):
    """End to end through the real directories, which is what the two path
    helpers are for -- `uploads` writes there and this reads there, and a
    disagreement between them would be silent."""
    _upload_skill(session_dir, "session-only")
    _upload_subagent(session_dir, "session-only")

    turn = for_session(Catalogue.from_config(cfg), session_dir)

    assert "session-only" in turn.skills.names
    assert "session-only" in turn.subagents.specs


def test_one_sessions_uploads_are_invisible_to_another(cfg, session_dir, tmp_path):
    """The layer is built per turn against one session directory, so this is
    structural rather than enforced -- but it is the property that matters most
    and nothing else states it."""
    _upload_subagent(session_dir, "mine")
    other = tmp_path / "other-session"
    other.mkdir()

    catalogue = Catalogue.from_config(cfg)

    assert "mine" in for_session(catalogue, session_dir).subagents.specs
    assert "mine" not in for_session(catalogue, other).subagents.specs


@pytest.mark.parametrize("kind", ["skills", "subagents"])
def test_an_empty_session_adds_nothing(cfg, session_dir, kind):
    """The common case: most turns upload nothing, and a missing uploads
    directory reads as empty rather than as a failure."""
    catalogue = Catalogue.from_config(cfg)
    plain = getattr(catalogue, kind).names

    assert getattr(for_session(catalogue, session_dir), kind).names == plain


def test_every_implementation_offers_names_in_a_stable_order(cfg, session_dir):
    """The port says stable, because the agent is built from this list and two
    processes reading the same definitions must offer the model the same one.

    `available_skills` used to `sorted()` at the call site, which worked only
    while there was a single implementation to sort. There are three now -- the
    local one, the layer, and whatever a deployment supplies -- so the guarantee
    had to move into the contract.
    """
    for name in ("b-second", "a-first", "c-third"):
        _upload_skill(session_dir, name)

    turn = for_session(Catalogue.from_config(cfg), session_dir)

    assert list(turn.skills.names) == sorted(turn.skills.names)
    # and the same answer twice, which a set-backed implementation would not give
    assert turn.skills.names == for_session(Catalogue.from_config(cfg), session_dir).skills.names
