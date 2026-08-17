"""Where the seeder gets its files from.

The source used to be a constant — a tree inside the wheel. It is now whatever
registered under the `kingfisher.assets` entry-point group, and kingfisher names
no pack anywhere: it asks which are installed and copies from each.

For one phase kingfisher registered its own presets through that group, so the
mechanism could be proved before any file moved. It ships none now. What remains
of `PACKAGE` is its own package data — the catalogue example and the format
documentation — which is seeded outside the loop over packs and is the one thing
here a deployment gets whether or not it installed anything.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from kingfisher.config import ConfigError
from kingfisher.infrastructure import seeding
from kingfisher.infrastructure.seeding import Pack


def _pack_of(tmp_path, name: str, *entries: str) -> tuple[Pack, object]:
    """A pack on disk, and a stand-in `opened` that hands back its tree."""
    root = tmp_path / name
    for entry in entries:
        target = root / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("name: x\ndescription: d\nsystem_prompt: |\n  body\n", encoding="utf-8")
    return Pack(name, f"fixture.{name}"), root


# -- discovery -------------------------------------------------------------


def test_kingfisher_registers_no_pack_of_its_own():
    """It did, for one phase, so the mechanism could be proved before the files
    moved. It ships no assets now, so it registers nothing -- a framework that
    announced itself as a source of content would be the thing this change
    exists to stop."""
    found = {pack.package for pack in seeding.installed_packs()}

    assert not any(package.startswith("kingfisher.") for package in found)


def test_the_asset_pack_in_this_repository_is_discovered():
    """The real arrangement, end to end: a second distribution, found through
    the entry point, with no name written down in kingfisher's source."""
    found = {pack.name: pack.package for pack in seeding.installed_packs()}

    assert found.get("kingfisher-assets") == "kingfisher_assets"


def test_packs_come_back_in_a_stable_order():
    """Two environments holding the same packs seed in the same order, and a
    collision message reads the same way twice."""
    names = [pack.name for pack in seeding.installed_packs()]

    assert names == sorted(names)


def test_the_seeder_copies_pack_content_without_naming_a_package(cfg):
    """The coupling this whole change exists to create: kingfisher asks who is
    installed, it does not reach for a name.

    Asserted against the module's own constant rather than a string literal.
    The literal version said `"kingfisher.presets" not in source`, which stopped
    meaning anything the moment that directory was renamed -- it passed for the
    reason a test must never pass, because the thing it names no longer exists.

    `PACKAGE` is exempt in `_copy_example` alone. That file is kingfisher's, not
    a pack's, so naming it there is the point rather than the leak.
    """
    import inspect

    source = inspect.getsource(seeding.seed) + inspect.getsource(seeding._copy)

    assert seeding.PACKAGE not in source
    assert "kingfisher." not in source
    # And the exemption is real, so this cannot pass by the constant being unused.
    assert "PACKAGE" in inspect.getsource(seeding._copy_example)


# -- two packs, one file ---------------------------------------------------


def test_two_packs_claiming_one_entry_are_refused(cfg, tmp_path, monkeypatch):
    """Last-one-wins is "silently different from what you asked for", which is
    refused at every other boundary here — an upload shadowing a catalogue
    name, two tool modules claiming one tool, a workspace tool shadowing a
    built-in. This is that failure one level out.
    """
    first, first_root = _pack_of(tmp_path, "alpha", "subagents/reviewer.yaml")
    second, second_root = _pack_of(tmp_path, "beta", "subagents/reviewer.yaml")
    roots = {first.package: first_root, second.package: second_root}

    @contextmanager
    def _opened(package: str = ""):
        # `_copy_example` asks for kingfisher's own tree with no argument, so
        # the stand-in has to answer that too -- it is not a pack.
        yield roots.get(package, tmp_path)

    monkeypatch.setattr(seeding, "opened", _opened)

    with pytest.raises(ConfigError, match="disagree about what to seed"):
        seeding.seed(cfg, packs=[first, second])


def test_the_refusal_names_both_packs(cfg, tmp_path, monkeypatch):
    """Whoever reads it may own neither pack, so naming only the loser is no
    help — the fix is to uninstall one, and that needs both names."""
    first, first_root = _pack_of(tmp_path, "alpha", "subagents/reviewer.yaml")
    second, second_root = _pack_of(tmp_path, "beta", "subagents/reviewer.yaml")
    roots = {first.package: first_root, second.package: second_root}

    @contextmanager
    def _opened(package: str = ""):
        # `_copy_example` asks for kingfisher's own tree with no argument, so
        # the stand-in has to answer that too -- it is not a pack.
        yield roots.get(package, tmp_path)

    monkeypatch.setattr(seeding, "opened", _opened)

    with pytest.raises(ConfigError) as raised:
        seeding.seed(cfg, packs=[first, second])

    assert "alpha" in str(raised.value)
    assert "beta" in str(raised.value)
    assert "reviewer.yaml" in str(raised.value)


def test_nothing_is_written_before_the_refusal(cfg, tmp_path, monkeypatch):
    """The ordering `_admit` keeps for a turn: everything able to reject runs
    first, so a refusal leaves nothing to clean up. Written the other way round,
    the first pack would land and the second would raise over a catalogue that
    is now half seeded.
    """
    first, first_root = _pack_of(tmp_path, "alpha", "subagents/reviewer.yaml", "skills/a/SKILL.md")
    second, second_root = _pack_of(tmp_path, "beta", "subagents/reviewer.yaml")
    roots = {first.package: first_root, second.package: second_root}

    @contextmanager
    def _opened(package: str = ""):
        # `_copy_example` asks for kingfisher's own tree with no argument, so
        # the stand-in has to answer that too -- it is not a pack.
        yield roots.get(package, tmp_path)

    monkeypatch.setattr(seeding, "opened", _opened)

    with pytest.raises(ConfigError):
        seeding.seed(cfg, packs=[first, second])

    # `skills/a` belongs only to alpha and would have been copied first.
    assert not (cfg.skills_dir / "a").exists()


def test_two_packs_that_do_not_collide_both_seed(cfg, tmp_path, monkeypatch):
    """The negative control. Without it the rule could refuse any two packs at
    all and every test above would still pass."""
    first, first_root = _pack_of(tmp_path, "alpha", "subagents/one.yaml")
    second, second_root = _pack_of(tmp_path, "beta", "subagents/two.yaml")
    roots = {first.package: first_root, second.package: second_root}

    @contextmanager
    def _opened(package: str = ""):
        # `_copy_example` asks for kingfisher's own tree with no argument, so
        # the stand-in has to answer that too -- it is not a pack.
        yield roots.get(package, tmp_path)

    monkeypatch.setattr(seeding, "opened", _opened)

    result = seeding.seed(cfg, packs=[first, second])

    assert set(result.written) == {"subagents/one.yaml", "subagents/two.yaml"}
    assert (cfg.subagents_dir / "one.yaml").is_file()
    assert (cfg.subagents_dir / "two.yaml").is_file()


def test_seeding_from_no_packs_still_writes_the_catalogue_example(cfg):
    """What an install with no asset pack does.

    Not nothing: `models.yaml` is required and has no fallback, and the error a
    deployment without one hits points at this example. It is kingfisher's own
    file rather than a pack's, so it does not depend on having installed any.
    No definitions are written, because there are none to write.
    """
    result = seeding.seed(cfg, packs=[])

    assert result.written == (seeding.EXAMPLE,)
    assert (cfg.workspace / seeding.EXAMPLE).is_file()
