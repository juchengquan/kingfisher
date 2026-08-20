"""A skill sent with the request, which could not be used at all.

The registry was built so one thing answers "what will this agent actually
have". It covered the catalogue and not the half a request brings with it, and
the two halves then disagreed: `available_skills` merged the session's
directory listing over the catalogue registry, `build_agent` resolved against
the catalogue registry alone, and every uploaded skill was advertised and then
refused as unknown. The whole feature, not an edge of it.

The quieter half is here too. An upload deepagents will not load -- one with no
`description` -- was written, listed, accepted, and then absent from an agent
that said nothing was wrong. That is the original bug this module exists to
remove, surviving in the one place it never reached.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import (
    activatable_skills,
    available_skills,
    build_agent,
)
from kingfisher.infrastructure.uploads import UploadError, materialise_skills
from tests.conftest import FakeToolCallingModel
from tests.unit.test_uploads import FakeStore

GOOD = b"---\nname: mine\ndescription: A skill this request brought along.\n---\nBody.\n"
NODESC = b"---\nname: nodesc\n---\nBody.\n"


def _skills(cfg):
    return replace(cfg, skills_enabled=True)


def _upload(cfg, session_dir, body, ref="skl_1"):
    return materialise_skills((ref,), FakeStore(**{ref: {"SKILL.md": body}}), session_dir, ())


# -- the feature, which did not work at all -------------------------------


def test_an_uploaded_skill_can_be_activated(cfg, session_dir):
    """It could not. Advertised by validation, refused by the build -- so no
    request could use a skill it supplied itself."""
    _upload(cfg, session_dir, GOOD)

    build_agent(
        _skills(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(skills=("mine",)),
    )


def test_validation_and_the_build_read_the_same_registry(cfg, session_dir):
    """The property, rather than the symptom. Two readers is what produced the
    bug, and asserting only that activation works would let them drift apart
    again the moment one of them grew a second source."""
    _upload(cfg, session_dir, GOOD)
    scoped = _skills(cfg)

    assert available_skills(scoped, session_dir) == activatable_skills(
        scoped, session_dir
    ).names


def test_an_upload_is_offered_beside_the_catalogue(cfg, session_dir):
    """Both halves in one answer, and a bare name is enough because `uploads`
    refuses one the catalogue already holds."""
    _skill = cfg.skills_dir / "shipped"
    _skill.mkdir(parents=True, exist_ok=True)
    (_skill / "SKILL.md").write_text(
        "---\nname: shipped\ndescription: From the catalogue.\n---\nB.\n", encoding="utf-8"
    )
    _upload(cfg, session_dir, GOOD)

    assert available_skills(_skills(cfg), session_dir) == ("mine", "shipped")


def test_a_request_with_no_uploads_is_unaffected(cfg, session_dir):
    """The negative control. Every deployment that never uploads anything
    behaves exactly as it did."""
    _skill = cfg.skills_dir / "shipped"
    _skill.mkdir(parents=True, exist_ok=True)
    (_skill / "SKILL.md").write_text(
        "---\nname: shipped\ndescription: From the catalogue.\n---\nB.\n", encoding="utf-8"
    )

    assert available_skills(_skills(cfg), session_dir) == ("shipped",)


# -- the quieter half -----------------------------------------------------


def test_an_upload_the_agent_cannot_load_is_refused_when_it_arrives(cfg, session_dir):
    """Told at the moment it is sent, against the ref that was sent, rather
    than later against a name the caller may not have chosen."""
    with pytest.raises(UploadError, match="cannot load this skill"):
        _upload(cfg, session_dir, NODESC, ref="skl_broken")


def test_the_refusal_names_the_ref_and_the_skill(cfg, session_dir):
    """A refusal naming neither is one the caller has to go and work out."""
    with pytest.raises(UploadError) as raised:
        _upload(cfg, session_dir, NODESC, ref="skl_broken")

    assert "skl_broken" in str(raised.value)
    assert "nodesc" in str(raised.value)


def test_the_registry_would_have_caught_it_anyway(cfg, session_dir):
    """The upload check is about *when*, not whether. Written straight to disk,
    bypassing that check, an unloadable skill is still never offered -- which is
    what stops the two answers drifting apart if one of them is ever relaxed.
    """
    directory = session_dir / "skills" / "uploaded" / "nodesc"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(NODESC)

    registry = activatable_skills(_skills(cfg), session_dir)

    assert "nodesc" not in registry.names
    assert "nodesc" in registry.unloadable


def test_naming_an_unloadable_upload_is_an_ordinary_refusal(cfg, session_dir):
    """The same message a catalogue skill gets, because it is the same rule."""
    directory = session_dir / "skills" / "uploaded" / "nodesc"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(NODESC)

    with pytest.raises(CapabilityError, match="unknown skill"):
        build_agent(
            _skills(cfg),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(skills=("nodesc",)),
        )
