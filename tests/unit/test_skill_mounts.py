"""A skills directory mounted beside the catalogue, at `/skills/<label>/`."""

from __future__ import annotations

import platform
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.config import paths_from_env
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import (
    default_backend,
    shell_env,
    skills_sources,
    skills_view,
)
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills
from kingfisher.kinds.skills import registry as skill_registry
from tests.conftest import FakeToolCallingModel

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nBody of {name}.\n"


def _skill(root: Path, folder: str, name: str, desc: str = "A skill.") -> Path:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL.format(name=name, desc=desc), encoding="utf-8")
    return directory


@pytest.fixture
def mounted(cfg, tmp_path):
    """A catalogue holding `local`, and a mount labelled `vendor` holding `lookup`."""
    _skill(cfg.skills_dir, "local", "local", "The catalogue's own.")
    vendor = tmp_path / "vendor-skills"
    _skill(vendor, "lookup", "lookup", "The vendor's way.")
    for kind in ("subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    return replace(cfg, skills_enabled=True, skills_mounts={"vendor": vendor})


def _drive(cfg, session_dir, capabilities, tool, args):
    """Run one tool call through a real graph and return what the tool said."""
    call = AIMessage(content="", tool_calls=[{"name": tool, "id": "1", "args": args}])
    graph = build_agent(
        cfg,
        session_dir=session_dir,
        capabilities=capabilities,
        model=FakeToolCallingModel(responses=[call, AIMessage(content="done")]),
    ).graph
    result = graph.invoke(
        {"messages": [("user", "go")]},
        config={"configurable": {"thread_id": "t"}, "recursion_limit": 8},
    )
    return " ".join(
        str(m.content) for m in result["messages"] if type(m).__name__ == "ToolMessage"
    )


# -- reading the variable ---------------------------------------------------


def test_the_variable_reads_label_path_pairs(tmp_path):
    """The shape `.env.example` shows is the shape that parses."""
    paths = paths_from_env({
        "KINGFISHER_WORKSPACE": str(tmp_path / "ws"),
        "KINGFISHER_SKILLS_MOUNTS": f"vendor={tmp_path / 'a'}:team={tmp_path / 'b'}",
    })

    assert dict(paths.skills_mounts) == {
        "vendor": (tmp_path / "a").resolve(),
        "team": (tmp_path / "b").resolve(),
    }


@pytest.mark.parametrize("value", ["vendor", "=/opt/x", "vendor=", "a=/x:a=/y"])
def test_a_malformed_or_repeated_mount_is_refused(tmp_path, value):
    """A pair missing half, or a label given twice, would otherwise drop a mount unsaid."""
    with pytest.raises(ConfigError, match="KINGFISHER_SKILLS_MOUNTS"):
        paths_from_env({
            "KINGFISHER_WORKSPACE": str(tmp_path / "ws"),
            "KINGFISHER_SKILLS_MOUNTS": value,
        })


# -- the registry -----------------------------------------------------------


def test_a_mounted_skill_is_offered_under_its_label(mounted):
    """A mount is one more source, so its skill's identity is `label::name`."""
    registry = Definitions.from_config(mounted).registry

    assert "vendor::lookup" in registry.offered
    assert "catalogue::local" in registry.offered
    assert "vendor" in registry.folders


def test_a_mounted_skill_is_addressed_under_its_label(mounted):
    """The path is what a deny rule is built from, so it must carry the mount's segment."""
    registry = Definitions.from_config(mounted).registry

    assert registry.offered["vendor::lookup"].path == "/vendor/lookup/SKILL.md"


def test_a_broken_skill_in_a_mount_is_reported_under_its_label(mounted):
    """An unloadable directory in a mount is named where a reader would look for it."""
    broken = mounted.skills_mounts["vendor"] / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no header here\n", encoding="utf-8")

    assert "vendor/broken" in Definitions.from_config(mounted).registry.unloadable


def test_a_mount_is_flat(mounted):
    """A mount is one source and deepagents lists a source one level deep."""
    _skill(mounted.skills_mounts["vendor"], "group/deep", "deep")
    definitions = Definitions.from_config(mounted)

    assert "vendor/group/deep" in definitions.skills.misplaced
    assert "vendor::deep" not in definitions.registry.offered


def test_the_index_reaches_a_mount_through_the_backend(mounted, session_dir):
    """Listed through the composite the agent gets, not the registry's own backend, so
    a missing route shows as a skill that never loads.
    """
    registry = Definitions.from_config(mounted).registry
    middleware = NarrowedSkills(
        allowed=("vendor::lookup",),
        backend=default_backend(mounted, session_dir),
        sources=skills_sources(registry.folders),
    )

    loaded = middleware.before_agent({}, None, {})["skills_metadata"]

    assert "vendor::lookup" in [s[skill_registry.KEY] for s in loaded]
    assert "The vendor's way." in middleware._format_skills_list(loaded)


# -- the file tools ---------------------------------------------------------


def test_an_activated_mounted_skill_is_readable(mounted, session_dir):
    """The control for the denial below: the path is reachable when granted."""
    said = _drive(
        mounted, session_dir,
        Capabilities(builtin_tools=("read_file",), skills=("vendor::lookup",)),
        "read_file", {"file_path": "/skills/vendor/lookup/SKILL.md"},
    )

    assert "Body of lookup." in said


def test_an_unactivated_mounted_skill_is_not_readable(mounted, session_dir):
    """Driven, because the rule can be well-formed and name a path that is not there --
    which is what a mounted skill's rule does if its path loses the label.
    """
    said = _drive(
        mounted, session_dir,
        Capabilities(builtin_tools=("read_file",), skills=("local",)),
        "read_file", {"file_path": "/skills/vendor/lookup/SKILL.md"},
    )

    assert "permission denied" in said.lower()
    assert "Body of lookup." not in said


def test_a_file_tool_cannot_write_into_a_mount(mounted, session_dir):
    """Covered by the catalogue's `/skills/**` rule because the route sits under it."""
    said = _drive(
        mounted, session_dir,
        Capabilities(builtin_tools=("write_file",)),
        "write_file", {"file_path": "/skills/vendor/lookup/SKILL.md", "content": "x"},
    )

    assert "permission denied" in said.lower()
    assert "Body of lookup." in (
        mounted.skills_mounts["vendor"] / "lookup" / "SKILL.md"
    ).read_text(encoding="utf-8")


# -- refused before anything is mounted --------------------------------------


@pytest.mark.parametrize("label", ["catalogue", "subagents", "a::b", "a/b"])
def test_a_label_that_means_something_else_is_refused(mounted, label):
    """Each would collide with a label, a route or an identity the catalogue already has."""
    cfg = replace(mounted, skills_mounts={label: mounted.skills_mounts["vendor"]})

    with pytest.raises(ConfigError, match="skills mount"):
        _ = Definitions.from_config(cfg).registry


def test_a_label_the_catalogue_already_has_is_refused(mounted):
    """The mount's route is longer, so it would hide the catalogue's `local` without a word."""
    cfg = replace(mounted, skills_mounts={"local": mounted.skills_mounts["vendor"]})

    with pytest.raises(ConfigError, match="would hide"):
        _ = Definitions.from_config(cfg).registry


def test_a_mount_that_is_not_there_is_refused(mounted, tmp_path):
    """Not created: an empty mount hides a staging failure behind an agent with no skills."""
    cfg = replace(mounted, skills_mounts={"vendor": tmp_path / "absent"})

    with pytest.raises(ConfigError, match="not a directory"):
        _ = Definitions.from_config(cfg).registry


def test_a_mount_overlapping_the_catalogue_is_refused(mounted):
    """Its skills would be listed twice, once under each label."""
    cfg = replace(mounted, skills_mounts={"inner": mounted.skills_dir / "local"})

    with pytest.raises(ConfigError, match="overlaps"):
        _ = Definitions.from_config(cfg).registry


def test_the_backend_refuses_before_it_mounts(mounted, session_dir):
    """A backend can be built before the registry is read, so it checks for itself."""
    cfg = replace(mounted, skills_mounts={"a/b": mounted.skills_mounts["vendor"]})

    with pytest.raises(ConfigError, match="skills mount"):
        default_backend(cfg, session_dir)


# -- the shell ---------------------------------------------------------------


@macos
def test_the_shell_may_read_a_mount_under_a_denied_home(cfg, session_dir, tmp_path, monkeypatch):
    """The mount sits under the home the profile denies, so only its own grant lets the
    shell in -- a mount outside the home would be readable with no grant at all.
    """
    home = tmp_path / "home"
    vendor = home / "vendor-skills"
    _skill(vendor, "lookup", "lookup")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    shell = default_backend(replace(cfg, skills_mounts={"vendor": vendor}), session_dir)

    assert shell.execute(f"cat {vendor}/lookup/SKILL.md").exit_code == 0
    assert shell.execute(f"echo pwned > {vendor}/lookup/SKILL.md").exit_code != 0
    assert "Body of lookup." in (vendor / "lookup" / "SKILL.md").read_text(encoding="utf-8")


@macos
def test_the_shell_cannot_write_a_mount_inside_the_workspace(cfg, session_dir):
    """Inside the workspace the shell may write, so only the mount's protection stops it
    -- a mount outside would be unwritable with no protection at all.
    """
    vendor = cfg.workspace / "vendor-skills"
    _skill(vendor, "lookup", "lookup")
    shell = default_backend(replace(cfg, skills_mounts={"vendor": vendor}), session_dir)

    assert shell.execute(f"echo pwned > {vendor}/lookup/SKILL.md").exit_code != 0
    assert shell.execute(f"echo pwned > {vendor}/PWNED.md").exit_code != 0
    assert not (vendor / "PWNED.md").exists()


# -- the shell reaches a mount's scripts --------------------------------------


def _script(skill: Path, text: str = "echo ran from the vendor") -> None:
    scripts = skill / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "hello.sh").write_text(text + "\n", encoding="utf-8")


@macos
def test_a_mounted_skills_script_runs_through_the_shells_catalogue_path(
    cfg, session_dir, tmp_path, monkeypatch
):
    """The path a skill writes is `$KINGFISHER_SKILLS/<what follows /skills/>`. The
    mount sits under the home the profile denies, so only the grants on the view and
    its target let the script run -- outside the home it would run with neither.
    """
    home = tmp_path / "home"
    vendor = home / "vendor-skills"
    _script(_skill(vendor, "lookup", "lookup"))
    _skill(cfg.skills_dir, "local", "local")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    shell = default_backend(replace(cfg, skills_mounts={"vendor": vendor}), session_dir)

    ran = shell.execute('sh "$KINGFISHER_SKILLS/vendor/lookup/scripts/hello.sh"')

    assert ran.exit_code == 0, ran.output
    assert "ran from the vendor" in ran.output
    assert shell.execute('cat "$KINGFISHER_SKILLS/local/SKILL.md"').exit_code == 0


@macos
def test_the_shell_cannot_write_through_the_view(mounted, session_dir):
    """The view is a directory of links in the workspace, which the shell may write
    everywhere else in.
    """
    shell = default_backend(mounted, session_dir)

    assert shell.execute('touch "$KINGFISHER_SKILLS/planted"').exit_code != 0
    assert shell.execute('echo x > "$KINGFISHER_SKILLS/vendor/planted"').exit_code != 0
    assert not (mounted.skills_mounts["vendor"] / "planted").exists()


def test_without_mounts_the_shell_is_given_the_catalogue_itself(cfg, session_dir):
    """Nothing is built for a deployment that mounts nothing."""
    assert shell_env(cfg, session_dir)["KINGFISHER_SKILLS"] == str(cfg.skills_dir)


def test_the_view_holds_the_catalogue_and_every_mount(mounted):
    """Each entry of the catalogue and each mount, side by side, as `/skills/` has them."""
    view = skills_view(Definitions.from_config(mounted).skills, mounted.workspace)

    assert sorted(p.name for p in view.iterdir()) == ["local", "vendor"]
    assert (view / "vendor").resolve() == mounted.skills_mounts["vendor"].resolve()


def test_one_catalogue_is_one_view_and_a_changed_one_is_another(mounted):
    """Named for what it holds: turns share a view, and a skill added to the catalogue
    is not missing from the one the shell is given.
    """
    first = skills_view(Definitions.from_config(mounted).skills, mounted.workspace)
    again = skills_view(Definitions.from_config(mounted).skills, mounted.workspace)
    _skill(mounted.skills_dir, "added", "added")
    changed = skills_view(Definitions.from_config(mounted).skills, mounted.workspace)

    assert first == again
    assert changed != first
    assert (changed / "added").is_dir()


def test_a_turn_that_loses_the_race_uses_the_winners_view(mounted, monkeypatch):
    """Two turns can both find no view and both build one. The second rename fails,
    and must neither raise nor leave its staging directory behind.
    """
    skills = Definitions.from_config(mounted).skills
    built = skills_view(skills, mounted.workspace)
    real = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: False if self == built else real(self))

    assert skills_view(skills, mounted.workspace) == built
    assert [p.name for p in built.parent.iterdir()] == [built.name]


def test_the_linux_fence_is_granted_the_view(mounted, session_dir, monkeypatch):
    """Asserted on what the fence is handed, because no host this runs on can show it:
    on macOS the view is readable as part of the workspace, and the Linux fences grant
    only what they are named -- the targets alone leave the links unreachable.
    """
    from kingfisher.infrastructure.harness import backend

    handed = []
    monkeypatch.setattr(
        backend, "_fence_for", lambda cfg, session, confined, skills, env: handed.append(skills)
    )
    default_backend(mounted, session_dir)

    view = skills_view(Definitions.from_config(mounted).skills, mounted.workspace)
    assert view in handed[0]
    assert mounted.skills_mounts["vendor"] in handed[0]
