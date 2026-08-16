"""Where a deployment's definitions are read from, and who gets to decide.

The catalogue used to be three hardcoded reads of `Config`, one each in
`available_skills`, `defined_subagents` and the tool loader. It is now one
mapping settled at construction, so a deployment that stages its definitions
somewhere else has one place to say so -- and so `--list`, the upload collision
check and the agent cannot end up reading three different answers.
"""

from __future__ import annotations

import platform
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher.application.service import Kingfisher
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.agent import (
    available_skills,
    build_agent,
    defined_subagents,
    workspace_tool_names,
)
from kingfisher.infrastructure.backend import SKILLS_ROUTE, build_backend
from kingfisher.infrastructure.catalogue import Catalogue, resolve_catalogue
from tests.conftest import FakeToolCallingModel, capture_build

SUBAGENT = """name: reviewer
description: Checks an analysis for arithmetic errors.
system_prompt: |
  You review analyses.

"""

TOOL = """from langchain_core.tools import tool


@tool
def elsewhere(x: int) -> int:
    \"\"\"A tool that only the staged catalogue defines.\"\"\"
    return x


TOOLS = [elsewhere]
"""

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)


def _staged(root, *, skill=None, subagent=None, tool=None):
    """A catalogue laid out somewhere that is not a workspace."""
    roots = Catalogue(**{kind: root / kind for kind in ("skills", "subagents", "tools")})
    for path in (roots.skills, roots.subagents, roots.tools):
        path.mkdir(parents=True, exist_ok=True)
    if skill is not None:
        (roots.skills / skill).mkdir()
        (roots.skills / skill / "SKILL.md").write_text(
            f"name: {skill}\ndescription: A skill.\n"
            "system_prompt: |\n  Do the thing.\n", encoding="utf-8"
        )
    if subagent is not None:
        (roots.subagents / "reviewer.yaml").write_text(subagent, encoding="utf-8")
    if tool is not None:
        (roots.tools / "extra.py").write_text(tool, encoding="utf-8")
    return roots


def test_omitted_it_is_the_three_directories_config_names(cfg):
    """The fallback, and the whole reason 45 call sites did not have to change.

    `build_agent` derives from `cfg` or raises but never invents, which is the
    rule `model=` already followed. Catalogue roots have a `cfg`-derived answer,
    so this is that rule and not an exception to it.
    """
    assert resolve_catalogue(cfg) == Catalogue(
        skills=cfg.skills_dir,
        subagents=cfg.subagents_dir,
        tools=cfg.tools_dir,
    )


def test_relocated_directories_are_created_rather_than_silently_empty(tmp_path, cfg):
    """The gap this closes, and it predates the feature.

    `build_backend` created `skills_dir` and only that one. Point
    `KINGFISHER_SUBAGENTS_DIR` or `KINGFISHER_TOOLS_DIR` at somewhere that does
    not exist yet and nothing created it and nothing said so: `load_all` and
    `load_tools` both read a missing directory as an empty one, so the
    deployment started cleanly with a catalogue it had configured and did not
    get.
    """
    elsewhere = tmp_path / "elsewhere"
    relocated = replace(
        cfg,
        skills_root=elsewhere / "s",
        subagents_root=elsewhere / "a",
        tools_root=elsewhere / "t",
    )
    assert not (elsewhere / "a").exists()

    roots = resolve_catalogue(relocated)

    assert all(path.is_dir() for path in (roots.skills, roots.subagents, roots.tools))


def test_a_supplied_catalogue_must_already_exist(tmp_path, cfg):
    """Creating one would hide the failure it is there to surface.

    A derived root is kingfisher's own, so making it is repair. A supplied one
    was staged by whoever supplied it, and an absent one most likely means the
    staging is what went wrong -- so creating it would turn a fetch that failed
    into an agent quietly told about no skills at all.
    """
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError, match="not a directory"):
        resolve_catalogue(
            cfg,
            {"skills": missing, "subagents": missing, "tools": missing},
        )
    assert not missing.exists()


def test_a_supplied_catalogue_names_all_three(tmp_path, cfg):
    """Leaving one out would mean an empty one, which is never what was meant."""
    roots = _staged(tmp_path / "staged")
    with pytest.raises(ConfigError, match="missing tools"):
        resolve_catalogue(cfg, {"skills": roots.skills, "subagents": roots.subagents})


def test_the_agent_reads_the_supplied_catalogue_and_not_the_workspace(tmp_path, cfg):
    """Supplied roots replace the configured ones; they do not add to them.

    Every function downstream assumes one root per kind -- `load_tools` takes a
    directory, `_skill_denials` emits against one route -- so a deployment that
    wants both merges them itself and decides its own collision rule.
    """
    (cfg.skills_dir / "in-the-workspace").mkdir(parents=True)
    (cfg.skills_dir / "in-the-workspace" / "SKILL.md").write_text(
        "name: in-the-workspace\ndescription: A skill.\n"
        "system_prompt: |\n  Do the thing.\n", encoding="utf-8"
    )
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT, tool=TOOL)

    assert available_skills(cfg, None, catalogue=roots) == ("staged-only",)
    assert tuple(defined_subagents(cfg, None, catalogue=roots)) == ("reviewer",)
    assert workspace_tool_names(cfg, catalogue=roots) == ("elsewhere",)

    # And the configured one is still what is read when nothing is supplied.
    assert available_skills(cfg, None) == ("in-the-workspace",)


def test_the_skills_route_follows_the_catalogue(tmp_path, cfg, session_dir):
    """The file tools have to reach what the listing advertised.

    Skills are not read by kingfisher; they are read by the agent, through this
    route. A catalogue that moved the listing without moving the route would
    advertise a skill and then fail to open it.
    """
    roots = _staged(tmp_path / "staged", skill="staged-only")
    backend = build_backend(cfg, session_dir, catalogue=roots)

    routed = backend.routes[SKILLS_ROUTE]

    assert str(routed.cwd) == str(roots.skills.resolve())
    assert backend.read(f"{SKILLS_ROUTE}staged-only/SKILL.md")


@macos
def test_the_shell_reaches_a_supplied_catalogue(cfg, session_dir):
    """The other half of the same answer, and the half a route check cannot see.

    `execute` bypasses tool-level permissions entirely, so the sandbox profile
    decides whether the shell can read a skill at all, and `$KINGFISHER_SKILLS`
    is how a skill's own scripts address the catalogue they live in. Both used
    to come off `cfg` while the route followed the catalogue -- a split view
    rather than a refusal, of exactly the kind `readable_roots` documents
    already having caused once.

    Staged under the operator's home on purpose. The profile denies the home and
    re-allows what has to stay readable, so anywhere else is readable regardless
    and would prove nothing: this fails if the grant names the configured
    directory instead of the supplied one, and a catalogue in `/tmp` would not.
    """
    probe = Path.home() / "kingfisher-supplied-catalogue-probe"
    roots = _staged(probe)
    (roots.skills / "demo").mkdir()
    (roots.skills / "demo" / "run.sh").write_text("echo from-the-supplied-catalogue\n")
    try:
        backend = build_backend(cfg, session_dir, catalogue=roots)

        result = backend.execute('sh "$KINGFISHER_SKILLS/demo/run.sh"')

        assert result.exit_code == 0, f"the shell cannot reach it: {result.output}"
        assert "from-the-supplied-catalogue" in str(result.output)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_the_service_settles_it_once_and_hands_it_down(tmp_path, cfg):
    """Resolved at construction, not per request.

    A deployment that fetches its catalogue pays for that once per `Kingfisher`
    rather than once per turn -- and a catalogue that cannot be read fails at
    startup, which is what `Kingfisher` already promises about a broken
    workspace or an unreachable state directory.
    """
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT)

    service = Kingfisher(cfg, catalogue_roots=roots)

    assert service.catalogue == roots


def test_a_broken_catalogue_fails_at_startup(tmp_path, cfg):
    """Rather than on the first turn, when a caller is already waiting."""
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError):
        Kingfisher(cfg, catalogue_roots={"skills": missing, "subagents": missing,
                                         "tools": missing})


def test_a_delegate_is_activated_from_the_supplied_catalogue(tmp_path, cfg, monkeypatch,
                                                             session_dir):
    """The subagent half, through `build_agent` rather than beside it.

    `_activated_subagents` resolves what a request wired *before* the tools,
    because whether a definition names one decides if the tool probe runs. It
    reads the catalogue to do that, so it needs the same one everything else
    got -- and a request activating a delegate the staged catalogue defines is
    the only thing that shows it did.
    """
    roots = _staged(tmp_path / "staged", subagent=SUBAGENT)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(subagents=("reviewer",)),
        catalogue=roots,
    )

    names = [spec["name"] for spec in captured["subagents"]]
    assert "reviewer" in names, "the staged catalogue's delegate was not wired"


def test_the_agent_it_builds_offers_the_staged_definitions(tmp_path, cfg, session_dir):
    """End to end: what the service resolved is what the graph was built from."""
    roots = _staged(tmp_path / "staged", skill="staged-only")
    enabled = replace(cfg, skills_enabled=True)

    graph = build_agent(
        enabled,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        catalogue=roots,
    )

    assert graph is not None
    assert available_skills(enabled, session_dir, catalogue=roots) == ("staged-only",)


# -- the type itself ------------------------------------------------------


def test_the_three_directories_are_attributes_not_keys(cfg):
    """A string key that is wrong is a `KeyError` at runtime, and in this
    codebase that surfaces as an empty catalogue -- the silent emptiness these
    modules keep refusing. An attribute that is wrong is a type error before it
    runs.
    """
    catalogue = Catalogue.from_config(cfg)

    assert catalogue.skills == cfg.skills_dir
    assert catalogue.subagents == cfg.subagents_dir
    assert catalogue.tools == cfg.tools_dir
    assert not hasattr(catalogue, "__getitem__"), "indexing would let both idioms survive"


def test_resolving_accepts_one_that_is_already_resolved(tmp_path, cfg):
    """A deployment stages directories and hands over a mapping, which is the
    documented seam. Something already holding a `Catalogue` -- another
    kingfisher, a test fixture -- should not have to take it apart to pass it
    back. The fixture in this file hit exactly that.
    """
    staged = _staged(tmp_path / "staged")

    assert resolve_catalogue(cfg, staged) == staged


def test_a_resolved_one_is_still_checked(tmp_path, cfg):
    """Accepting the type is not accepting it unread. A supplied catalogue is
    staged by whoever supplies it, so a directory that is not there is a staging
    failure and has to say so however it arrived.
    """
    missing = tmp_path / "never-staged"
    handed = Catalogue(skills=missing, subagents=missing, tools=missing)

    with pytest.raises(ConfigError, match="not a directory"):
        resolve_catalogue(cfg, handed)
