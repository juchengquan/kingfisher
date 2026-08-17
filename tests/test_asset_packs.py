"""Where the seeder gets its files from.

The source used to be a constant — `kingfisher.presets`, the tree inside the
wheel. It is now whatever registered under the `kingfisher.assets` entry-point
group, and kingfisher names no pack anywhere: it asks which are installed and
copies from each.

Kingfisher's own presets register through that group like anyone else's, so the
default arrangement is one pack that happens to ship inside the wheel — and
every existing seeding test now exercises the discovery path without knowing it.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from kingfisher.config import ConfigError
from kingfisher.infrastructure import presets
from kingfisher.infrastructure.presets import Pack


def _pack_of(tmp_path, name: str, *entries: str) -> tuple[Pack, object]:
    """A pack on disk, and a stand-in `opened` that hands back its tree."""
    root = tmp_path / name
    for entry in entries:
        target = root / entry
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    return Pack(name, f"fixture.{name}"), root


# -- discovery -------------------------------------------------------------


def test_kingfisher_registers_its_own_presets_as_a_pack():
    """The line in `pyproject.toml` that makes the mechanism real before
    anything moves. Without it the seeder discovers nothing and `--seed-presets`
    silently stops working, which is the failure this phase must not have."""
    found = {pack.name: pack.package for pack in presets.installed_packs()}

    assert found.get("presets") == "kingfisher.presets"


def test_packs_come_back_in_a_stable_order():
    """Two environments holding the same packs seed in the same order, and a
    collision message reads the same way twice."""
    names = [pack.name for pack in presets.installed_packs()]

    assert names == sorted(names)


def test_the_seeder_names_no_package_of_its_own(cfg):
    """The coupling this phase exists to create. Kingfisher asks who is
    installed; it does not reach for a name. The only mention of
    `kingfisher.presets` in the module is the default handed to `opened`, which
    the entry point supplies in every real call.
    """
    import inspect

    source = inspect.getsource(presets.seed) + inspect.getsource(presets._copy)

    assert "kingfisher.presets" not in source


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
        yield roots[package]

    monkeypatch.setattr(presets, "opened", _opened)

    with pytest.raises(ConfigError, match="disagree about what to seed"):
        presets.seed(cfg, packs=[first, second])


def test_the_refusal_names_both_packs(cfg, tmp_path, monkeypatch):
    """Whoever reads it may own neither pack, so naming only the loser is no
    help — the fix is to uninstall one, and that needs both names."""
    first, first_root = _pack_of(tmp_path, "alpha", "subagents/reviewer.yaml")
    second, second_root = _pack_of(tmp_path, "beta", "subagents/reviewer.yaml")
    roots = {first.package: first_root, second.package: second_root}

    @contextmanager
    def _opened(package: str = ""):
        yield roots[package]

    monkeypatch.setattr(presets, "opened", _opened)

    with pytest.raises(ConfigError) as raised:
        presets.seed(cfg, packs=[first, second])

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
        yield roots[package]

    monkeypatch.setattr(presets, "opened", _opened)

    with pytest.raises(ConfigError):
        presets.seed(cfg, packs=[first, second])

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
        yield roots[package]

    monkeypatch.setattr(presets, "opened", _opened)

    seeding = presets.seed(cfg, packs=[first, second])

    assert set(seeding.written) == {"subagents/one.yaml", "subagents/two.yaml"}
    assert (cfg.subagents_dir / "one.yaml").is_file()
    assert (cfg.subagents_dir / "two.yaml").is_file()


def test_seeding_from_no_packs_writes_nothing(cfg):
    """What an install with no asset pack does. It must be quiet and empty
    rather than an error — the driver decides what to say about it."""
    seeding = presets.seed(cfg, packs=[])

    assert seeding == presets.Seeding()
