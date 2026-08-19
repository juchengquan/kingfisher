"""Where the seeder gets its files from.

A directory, given or defaulted. `seed(cfg)` uses the definitions that ship with
kingfisher; `seed(cfg, path)` uses whatever is at that path. Both write the
catalogue example, because that is kingfisher's own and a caller's directory has
no reason to carry it.

This replaces `test_asset_packs`. The source was an entry-point group for a
while: any distribution could register as a pack and seeding would copy from
every one it found, so a team could publish definitions and have them seed
alongside the shipped ones. A path covers the same ground without a wheel, a
publish step, or metadata — a deployment points at its own directory — and the
group went with the separate distribution.

What went with it is worth naming, since nothing here can miss it: two packs
seeding together, and the refusal when two claimed the same file. Neither can
happen against a single directory. If a second publisher ever wants in, the
group comes back and so do those tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.config import ConfigError
from kingfisher.infrastructure import seeding


def _definitions(root: Path, *entries: str) -> Path:
    for entry in entries:
        target = root / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    return root


# -- the default: what ships ----------------------------------------------


def test_the_definitions_ship_with_the_library():
    """The point of folding them in. `pip install kingfisher` then
    `kingfisher seed` has to write a workspace that already works -- content a
    reader must go and find teaches nobody.

    Agents come first, and the order is `Definitions`' field order rather than
    anything chosen here. It happens to be the useful one: the agent is what a
    request names, and the other three are what it selects from.
    """
    assert seeding.shipped_kinds() == ("agents", "skills", "subagents", "tools")


def test_seeding_without_a_source_writes_the_shipped_definitions(cfg, shipped):
    written = seeding.seed(cfg, shipped).written

    assert any(entry.startswith("skills/") for entry in written), written
    assert any(entry.startswith("subagents/") for entry in written), written
    assert any(entry.startswith("tools/") for entry in written), written


def test_the_catalogue_example_is_beside_models_yaml_whatever_the_source(cfg, tmp_path):
    """It is kingfisher's own, not a definition, so it does not come from the
    source -- and a caller's own directory has no reason to hold one.

    It arrives with the *layout* now rather than with the copy. The distinction
    matters because seeding can decline: a deployment naming no definitions
    still has to be told where to write `models.yaml`, and that instruction is
    this file.
    """
    from kingfisher.infrastructure.workspace_fs import EXAMPLE, ensure_layout

    mine = _definitions(tmp_path / "mine", "skills/only/SKILL.md")
    ensure_layout(cfg.workspace)

    assert (cfg.workspace / EXAMPLE).is_file()
    assert EXAMPLE not in seeding.seed(cfg, mine).written


# -- resolving one source from a flag and a variable -----------------------


def test_seed_will_not_invent_a_source(cfg):
    """The parameter is required, with no default and no `None` branch.

    Stated in the signature rather than checked at runtime, so a caller who
    forgets is caught by the type checker instead of by an empty workspace an
    hour later. Asserted here because the type checker does not run in this
    suite, and a default reappearing would otherwise be silent.
    """
    import inspect

    parameter = inspect.signature(seeding.seed).parameters["source"]

    assert parameter.default is inspect.Parameter.empty


def test_an_explicit_directory_beats_the_variable(cfg, tmp_path):
    """The ordinary shape of a flag against a variable, and the one
    `__main__` already documents for `.env`: an explicit argument must not be
    quietly replaced by something the caller may not have known was set."""
    from dataclasses import replace

    configured = _definitions(tmp_path / "configured", "skills/theirs/SKILL.md")
    asked_for = _definitions(tmp_path / "asked-for", "skills/mine/SKILL.md")

    resolved = seeding.definitions_source(replace(cfg, assets=configured), asked_for)

    assert resolved == asked_for


def test_the_variable_is_used_when_nothing_was_asked_for(cfg, tmp_path):
    """`kingfisher seed` with no `--from`, which is the common invocation."""
    from dataclasses import replace

    configured = _definitions(tmp_path / "configured", "skills/theirs/SKILL.md")

    assert seeding.definitions_source(replace(cfg, assets=configured)) == configured


def test_the_variable_reaches_a_first_run(monkeypatch, tmp_path):
    """On `WorkspacePaths`, not only on `Config`.

    Seeding a fresh workspace runs before a model catalogue can be read -- the
    catalogue is a file inside the workspace -- so a source reachable only from
    a whole `Config` would be unreachable exactly when seeding happens.
    """
    from kingfisher.application.config import paths_from_env

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(tmp_path / "mine"))

    assert paths_from_env().assets == tmp_path / "mine"


def test_no_variable_is_not_an_error_by_itself(monkeypatch, tmp_path):
    """A workspace seeded once runs for years without this being set. Only the
    act of seeding needs it, so `paths_from_env` must not refuse -- that is the
    difference between this and `KINGFISHER_WORKSPACE`, which is required
    because no default can supply it."""
    from kingfisher.application.config import paths_from_env

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)

    assert paths_from_env().assets is None


# -- a source of your own --------------------------------------------------


def test_a_given_directory_is_seeded_instead(cfg, tmp_path):
    """The reason the parameter exists: a deployment with its own definitions
    needs no package, no metadata and no publish step."""
    mine = _definitions(tmp_path / "mine", "skills/mine/SKILL.md")

    written = seeding.seed(cfg, mine).written

    assert "skills/mine" in written, written
    assert not any("code-review" in entry for entry in written), (
        "the shipped definitions were seeded as well as the given ones"
    )


def test_a_source_that_is_not_a_directory_is_refused(cfg, tmp_path):
    """Loudly, and before anything is written. A caller who mistyped a path
    should not discover it as an empty workspace an hour later."""
    with pytest.raises(ConfigError, match="nothing to seed from"):
        seeding.seed(cfg, tmp_path / "no-such-directory")


def test_nothing_is_written_before_that_refusal(cfg, tmp_path):
    """The ordering `_admit` keeps for a turn: everything able to reject runs
    first, so a refusal leaves nothing behind to clean up."""
    with pytest.raises(ConfigError):
        seeding.seed(cfg, tmp_path / "no-such-directory")

    assert not cfg.skills_dir.exists() or not any(cfg.skills_dir.iterdir())


def test_a_source_holding_only_one_kind_seeds_only_that(cfg, tmp_path):
    """A directory is not required to hold all three."""
    mine = _definitions(tmp_path / "mine", "skills/alone/SKILL.md")

    written = seeding.seed(cfg, mine).written

    assert "skills/alone" in written
    assert not any(entry.startswith("subagents/") for entry in written), written
