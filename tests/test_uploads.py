"""Definitions a request brings with it, fetched by id."""

from __future__ import annotations

import pytest

from kingfisher.domain.ports import DefinitionStore
from kingfisher.domain.request import Request
from kingfisher.infrastructure.uploads import UploadError, provision

SKILL = b"---\nname: extractor\ndescription: Pulls fields out of documents.\n---\nBody.\n"
SUBAGENT = b"name: reviewer\ndescription: Checks arithmetic.\nsystem_prompt: |\n  You review.\n"


class FakeStore:
    """A catalogue, as a dict. The port exists so this is all a test needs."""

    def __init__(self, **definitions: dict[str, bytes]) -> None:
        self.definitions = definitions
        self.fetched: list[str] = []

    def fetch(self, definition_id: str) -> dict[str, bytes]:
        self.fetched.append(definition_id)
        return self.definitions[definition_id]


def test_a_dict_satisfies_the_port():
    """Protocols by shape: nothing has to import kingfisher to be a store."""
    assert isinstance(FakeStore(), DefinitionStore)


def test_an_uploaded_skill_is_unpacked_under_its_declared_name(cfg, session_dir):
    """Not under its id. deepagents validates the frontmatter name against the
    directory, so the definition names its own directory."""
    store = FakeStore(skl_1={"SKILL.md": SKILL, "reference/notes.md": b"more"})

    provision(Request("t", skill_refs=("skl_1",)), store, session_dir, cfg)

    unpacked = session_dir / "skills" / "uploaded" / "extractor"
    assert (unpacked / "SKILL.md").read_bytes() == SKILL
    assert (unpacked / "reference" / "notes.md").read_bytes() == b"more"
    assert store.fetched == ["skl_1"]


def test_an_uploaded_subagent_is_unpacked_under_its_declared_name(cfg, session_dir):
    store = FakeStore(sub_1={"whatever.md": SUBAGENT})

    provision(Request("t", subagent_refs=("sub_1",)), store, session_dir, cfg)

    assert (session_dir / "subagents" / "reviewer.yaml").read_bytes() == SUBAGENT


def test_an_upload_cannot_shadow_the_catalogue(cfg, session_dir):
    """Otherwise a request could stand its own definition in for a reviewed one
    under the same name, and deepagents' later-source-wins would let it."""
    (cfg.skills_dir / "extractor").mkdir(parents=True)
    (cfg.skills_dir / "extractor" / "SKILL.md").write_bytes(SKILL)
    store = FakeStore(skl_1={"SKILL.md": SKILL})

    with pytest.raises(UploadError, match="already defined by the catalogue"):
        provision(Request("t", skill_refs=("skl_1",)), store, session_dir, cfg)


def test_two_uploads_of_one_name_in_a_request_are_refused(cfg, session_dir):
    """The second would silently replace the first on disk."""
    store = FakeStore(a={"SKILL.md": SKILL}, b={"SKILL.md": SKILL})

    with pytest.raises(UploadError, match="uploaded twice"):
        provision(Request("t", skill_refs=("a", "b")), store, session_dir, cfg)


def test_a_path_that_escapes_its_directory_is_refused(cfg, session_dir):
    """A catalogue is a remote service, so its paths are input, not data we
    produced. `../` in one would write anywhere this process can."""
    store = FakeStore(skl_1={"SKILL.md": SKILL, "../../escaped.md": b"nope"})

    with pytest.raises(UploadError, match="escapes the directory"):
        provision(Request("t", skill_refs=("skl_1",)), store, session_dir, cfg)

    assert not (session_dir.parent / "escaped.md").exists()


def test_a_skill_without_a_skill_md_is_refused(cfg, session_dir):
    store = FakeStore(skl_1={"README.md": b"not a skill"})

    with pytest.raises(UploadError, match=r"must contain SKILL\.md"):
        provision(Request("t", skill_refs=("skl_1",)), store, session_dir, cfg)


def test_ids_without_a_store_fail_loudly(cfg, session_dir):
    """Silently ignoring them would run the task without what it asked for."""
    with pytest.raises(UploadError, match="no DefinitionStore is wired"):
        provision(Request("t", skill_refs=("skl_1",)), None, session_dir, cfg)


def test_a_request_that_brings_nothing_needs_no_store(cfg, session_dir):
    """A deployment that never serves uploads wires nothing and is unaffected."""
    provision(Request("t"), None, session_dir, cfg)

    assert not any((session_dir / "skills" / "uploaded").glob("*"))


SPEC_SHAPED_SKILL = b"""---
name: extractor
description: >-
  Pulls fields out of documents,
  one record at a time.
allowed-tools:
  - read_file
  - grep
---
Body.
"""


def test_a_skill_written_to_the_published_spec_can_be_uploaded(cfg, session_dir):
    """The defect this closes: catalogue skills are never parsed by kingfisher
    -- `LocalSkillRepository.names` only lists directories -- but uploaded ones are. So
    a skill using the Agent Skills spec\'s documented block list for
    `allowed-tools`, or a folded description, loaded fine from the catalogue
    and was refused on upload by a stricter parser of our own.
    """
    store = FakeStore(skl_1={"SKILL.md": SPEC_SHAPED_SKILL})

    provision(Request("t", skill_refs=("skl_1",)), store, session_dir, cfg)

    unpacked = session_dir / "skills" / "uploaded" / "extractor" / "SKILL.md"
    assert unpacked.read_bytes() == SPEC_SHAPED_SKILL
