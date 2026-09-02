"""Where a deployment reads from, and the states nothing could express before.

Three of these guard something that was previously unreportable: a `groups.yaml`
that is not there still names where it was looked for, a catalogue handed in at
construction is reported as itself rather than as the configuration it ignores,
and `tools` is in the record at all -- it was the one catalogue `kingfisher
list` never named, because there was no field for it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher import Kingfisher, Origin, Origins
from kingfisher.infrastructure.catalogue import Definitions


def test_a_plain_workspace_reports_every_catalogue_as_derived(cfg):
    """Four catalogues, and `tools` among them.

    Named individually rather than looped, because the bug this record exists
    to stop was one kind being silently absent from the answer.
    """
    found = Origins.of(cfg)

    for kind in ("agents", "skills", "subagents", "tools"):
        entry = getattr(found, kind)
        assert entry.kind == "default", kind
        assert entry.path == cfg.workspace / kind, kind


def test_a_relocated_catalogue_is_told_apart_from_a_derived_one(cfg, tmp_path):
    """`doctor` warns on one and not the other, so a string comparison on the
    path is not enough -- the kinds have to differ."""
    elsewhere = tmp_path / "shared-skills"
    elsewhere.mkdir()

    found = Origins.of(replace(cfg, skills_root=elsewhere))

    assert found.skills == Origin("relocated", elsewhere)
    assert found.subagents.kind == "default"


def test_naming_the_derived_path_explicitly_is_still_derived(cfg):
    """A deployment that spells out the default is not relocated.

    Otherwise `doctor`'s relocated-and-empty warning fires on every fresh
    workspace whose operator happened to be explicit, which is the reading that
    would make it noise.
    """
    found = Origins.of(replace(cfg, skills_root=cfg.workspace / "skills"))

    assert found.skills.kind == "default"


def test_a_catalogue_supplied_at_construction_is_reported_as_itself(cfg, tmp_path):
    """The case the record exists for: configuration says one thing, and
    something else is being read.

    Reported as `overridden` rather than as the configured path, because a
    reader shown the configured path goes and edits a variable that does
    nothing.
    """
    staged = {kind: tmp_path / kind for kind in ("agents", "skills", "subagents", "tools")}
    for path in staged.values():
        path.mkdir()

    found = Origins.of(cfg, catalogue=Definitions.from_roots(staged))

    assert found.skills == Origin("overridden", staged["skills"])
    assert found.tools.kind == "overridden"


def test_a_supplied_catalogue_matching_the_configuration_is_not_an_override(cfg):
    """Indistinguishable and equivalent, so there is nothing to report."""
    found = Origins.of(cfg, catalogue=Definitions.from_roots(cfg.catalogue_roots))

    assert found.skills.kind == "default"


def test_a_repository_with_no_directory_has_no_path(cfg):
    """A store the deployment wired satisfies the port without a root, and the
    record says so rather than inventing a folder."""

    class Rootless:
        pass

    catalogue = replace(Definitions.from_roots(cfg.catalogue_roots), subagents=Rootless())  # type: ignore[arg-type]

    found = Origins.of(cfg, catalogue=catalogue)

    assert found.subagents == Origin("supplied", None)


def test_an_absent_groups_file_still_says_where_it_looked(cfg, tmp_path):
    """The line this whole record was worth building for.

    A `groups.yaml` written one directory off leaves a deployment reachable by
    everyone, and every other surface is silent about it: the process comes up,
    nothing fails, and no message anywhere names a path.
    """
    looked = tmp_path / "ws" / "groups.yaml"

    found = Origins.of(replace(cfg, access=None, access_source=looked))

    assert found.groups == Origin("unset", looked)


def test_a_config_assembled_in_code_reports_no_groups_file(cfg):
    """Distinct from the above, and the distinction is actionable: one means
    "go and look at that path", the other means "there was never a file"."""
    assert Origins.of(cfg).groups == Origin("unset", None)


def test_the_models_file_is_named(cfg, tmp_path):
    """`doctor` counts models and endpoints and has never said which file they
    came from, which is the question somebody sharing a catalogue across a
    fleet actually has."""
    written = tmp_path / "catalogue" / "models.yaml"
    catalogue = replace(cfg.models, source=written)

    found = Origins.of(replace(cfg, models=catalogue))

    assert found.models == Origin("relocated", written)


def test_a_catalogue_built_in_code_has_no_file_behind_it(cfg):
    """Which is how every test in this suite is wired, so it must not be
    reported as a path that does not exist."""
    assert Origins.of(cfg).models == Origin("supplied", None)


def test_the_seed_directory_is_never_derived(cfg, tmp_path):
    """There is nowhere kingfisher would look on its own, so an unset one is
    unset rather than defaulted -- and `doctor` already warns about it."""
    assert Origins.of(cfg).seed == Origin("unset", None)
    assert Origins.of(replace(cfg, assets=tmp_path)).seed == Origin("relocated", tmp_path)


def test_the_working_roots_follow_the_workspace_until_they_are_moved(cfg, tmp_path):
    """State and scratch are derived from the workspace, and relocate together
    -- moving state moves scratch, because scratch hangs off it."""
    plain = Origins.of(cfg)
    assert plain.state == Origin("default", cfg.workspace / ".kingfisher")
    assert plain.scratch.kind == "default"

    moved = Origins.of(replace(cfg, state_root=tmp_path / "state"))
    assert moved.state == Origin("relocated", tmp_path / "state")
    assert moved.scratch.kind == "default", "scratch derives from state, so it did not move"


def test_a_session_store_handed_in_is_told_from_a_configured_one(cfg, tmp_path):
    """The same override the catalogues have, on the one other seam that
    corresponds to a path a deployment configured."""

    class Elsewhere:
        root = tmp_path / "blob"

    kept = replace(cfg, session_store=tmp_path / "kept")

    assert Origins.of(cfg).sessions == Origin("unset", None)
    assert Origins.of(kept).sessions == Origin("relocated", tmp_path / "kept")
    assert Origins.of(kept, sessions=Elsewhere()).sessions == Origin(
        "overridden", tmp_path / "blob"
    )
    # Nothing configured and a store handed in: there is no path the deployment
    # named, so there is nothing for the store to be an override of.
    assert Origins.of(cfg, sessions=Elsewhere()).sessions == Origin("supplied", None)


def test_entries_are_derived_from_the_record_not_listed_beside_it(cfg):
    """A field added here must not be one a printer can silently omit.

    That is exactly how `tools` came to be missing from `kingfisher list`: the
    header named three catalogues from three hand-written lines.
    """
    found = Origins.of(cfg)
    names = [name for name, _ in found.entries()]

    assert names == [
        "agents", "skills", "subagents", "tools",
        "models", "groups", "seed", "state", "scratch", "sessions",
    ]


def test_the_record_cannot_be_edited_after_it_is_handed_back(cfg):
    """Frozen, like everything else a caller is handed here."""
    found = Origins.of(cfg)

    with pytest.raises(AttributeError):
        found.workspace = Path("/elsewhere")  # type: ignore[misc]


def test_a_running_kingfisher_reports_what_it_resolved(cfg):
    """Not what it was configured with. The property reads `self.catalogue`,
    which is what the constructor resolved and warmed."""
    kf = Kingfisher(cfg)

    assert kf.origins.workspace == cfg.workspace
    assert kf.origins.skills.kind == "default"


def test_asking_for_origins_does_not_create_the_directories_it_reports(cfg, tmp_path):
    """A report must not change what it reports on.

    `resolve_definitions` creates derived roots, which is right at construction
    and wrong here -- a caller checking where skills *would* come from would
    otherwise bring the directory into being by asking.
    """
    fresh = tmp_path / "never-laid-out"

    found = Origins.of(replace(cfg, workspace=fresh))

    assert found.skills.path == fresh / "skills"
    assert not fresh.exists()
