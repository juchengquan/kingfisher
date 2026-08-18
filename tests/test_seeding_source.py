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


def test_seeding_without_a_source_writes_the_shipped_definitions(cfg):
    written = seeding.seed(cfg).written

    assert any(entry.startswith("skills/") for entry in written), written
    assert any(entry.startswith("subagents/") for entry in written), written
    assert any(entry.startswith("tools/") for entry in written), written


def test_the_catalogue_example_is_written_either_way(cfg, tmp_path):
    """It is kingfisher's own, not a definition, so it does not come from the
    source -- and a caller's own directory has no reason to hold one."""
    from kingfisher.infrastructure.seeding import EXAMPLE

    mine = _definitions(tmp_path / "mine", "skills/only/SKILL.md")

    assert EXAMPLE in seeding.seed(cfg).written
    assert EXAMPLE in seeding.seed(cfg, mine).overwritten + seeding.seed(cfg, mine).written


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
