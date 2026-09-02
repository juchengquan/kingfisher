"""One answer to "what does this workspace offer", not two.

`--list` and `--without-skills` used to work it out apart: each built its own
agent, walked its own catalogue, and nothing made the two agree. The one that
mattered was the subtraction, which has to be taken from the set the run will
actually offer or it refuses a name the run did not have.

These hold the record to being that one answer, and to carrying a broken
catalogue rather than raising over the rest of the inventory.
"""

from __future__ import annotations

import pytest

from kingfisher.application.inventory import Inventory, inventory
from tests.conftest import subagents_dir, tools_dir

A_TOOL = '''
from langchain_core.tools import tool


@tool
def probe_one(text: str) -> str:
    """A tool that exists so the inventory has something of its own to find."""
    return text


TOOLS = [probe_one]
'''


def _populate(cfg) -> None:
    """Put one of each kind in the workspace.

    Every axis, deliberately. Written against an empty workspace, the assertion
    below reads `() == ()` on three of the four and passes whatever the code
    does -- which is what a mutation caught: emptying `offered["skills"]`
    outright left it green.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "probe.py").write_text(A_TOOL, encoding="utf-8")

    skill = cfg.skills_dir / "probe-skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: probe-skill\ndescription: A skill the inventory can find.\n---\n"
        "Do the thing.\n",
        encoding="utf-8",
    )

    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "probe-agent.yaml").write_text(
        "name: probe-agent\ndescription: A delegate the inventory can find.\n"
        "system_prompt: |\n  Answer briefly.\n",
        encoding="utf-8",
    )


def test_the_names_a_subtraction_uses_are_the_names_the_listing_shows(cfg):
    """The guard this whole record exists for.

    Two implementations of "what may a request activate here" is one more than
    can be kept in step, and the drift is invisible: `--without-skills X`
    refusing a name that `--list` had just printed, or worse, letting one
    through. Derived rather than stored, so they cannot come apart.

    Each axis is asserted non-empty first. Without that this passes on an empty
    workspace no matter what `offered` returns, which is how it survived having
    `skills` replaced with `()`.
    """
    _populate(cfg)

    found = inventory(cfg)

    assert found.tools and found.builtin_tools and found.skills and found.subagents
    assert found.offered["tools"] == found.tools
    assert found.offered["builtin_tools"] == found.builtin_tools
    assert found.offered["skills"] == tuple(found.skills)
    assert found.offered["subagents"] == tuple(found.subagents)


def test_the_driver_and_the_record_agree_about_what_is_offered(cfg, capsys):
    """The two callers, against each other rather than against a literal.

    `the driver` prints one and subtracts from the other. Asserting both against
    the same record is what says the split into printer and computation did not
    quietly change either.
    """
    from tests.integration import driver

    _populate(cfg)

    assert driver.show_inventory(cfg, cfg.workspace) == 0
    printed = capsys.readouterr().out

    offered = driver._offered(cfg)
    # Every axis, and each non-empty, so this cannot pass by having nothing to
    # compare -- the mistake the test above was rewritten for.
    for kind in ("tools", "builtin_tools", "skills", "subagents"):
        assert offered[kind], kind
        for name in offered[kind]:
            assert name in printed, f"{kind}: {name}"


def test_a_tool_catalogue_that_will_not_load_is_carried_not_raised(cfg):
    """A listing is where someone goes *because* something is broken.

    Raising here put a traceback over the rest of the inventory. The error is a
    field, so the printer decides what to say -- and skills and subagents are
    still answered, which is the half a traceback took away.
    """
    directory = tools_dir(cfg) / "research"
    directory.mkdir(parents=True)
    (directory / "t.py").write_text(
        A_TOOL + "\nTOOLS = [probe_one, probe_one]\n", encoding="utf-8"
    )

    found = inventory(cfg)

    assert found.tools_error is not None
    assert found.tools == ()  # nothing claimed, because nothing could be walked
    assert found.subagents_error is None  # and the other catalogues still answered


def test_a_subagent_catalogue_that_will_not_load_is_carried_too(cfg):
    """The same rule for the other loader, so neither can take the other down."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    found = inventory(cfg)

    assert found.subagents_error is not None
    assert found.tools_error is None
    assert found.builtin_tools  # the build still happened


def test_a_delegation_cycle_is_carried_like_any_other_failure(cfg):
    """An inventory that says a workspace is fine while a run refuses it is the
    failure this whole file exists to prevent, and a cycle was exactly that: it
    is checked when an agent is built, and `--list` does not build one.

    Not caught by reading the definitions, which is why it needs asking for. A
    file naming a helper is well-formed on its own -- the loop only exists
    across files, so no single parse can see it.
    """
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name, helper in (("a", "b"), ("b", "a")):
        (subagents_dir(cfg) / f"{name}.yaml").write_text(
            f"name: {name}\ndescription: d\nsubagents: [{helper}]\n"
            f"system_prompt: |\n  Go.\n",
            encoding="utf-8",
        )

    found = inventory(cfg)

    assert found.subagents_error is not None
    assert "reach themselves" in found.subagents_error
    assert found.tools_error is None, "one catalogue must not take the other down"


def test_a_workspace_with_no_cycle_reports_none(cfg):
    """The negative control. A definition naming a helper, and a helper naming
    its own, is the shape a cycle is written in -- and is perfectly legal."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name, helper in (("a", "b"), ("b", "c")):
        (subagents_dir(cfg) / f"{name}.yaml").write_text(
            f"name: {name}\ndescription: d\nsubagents: [{helper}]\n"
            f"system_prompt: |\n  Go.\n",
            encoding="utf-8",
        )
    (subagents_dir(cfg) / "c.yaml").write_text(
        "name: c\ndescription: d\nsystem_prompt: |\n  Go.\n", encoding="utf-8"
    )

    assert inventory(cfg).subagents_error is None


def test_answering_leaves_no_session_behind(cfg):
    """An agent needs a session to root its backend at, and what a workspace
    *offers* is a question about the workspace. A session left here is one
    `keep_runs` would eventually reap a real one to make room for."""
    before = set((cfg.workspace / "sessions").glob("*")) if (
        cfg.workspace / "sessions"
    ).is_dir() else set()

    inventory(cfg)

    after = set((cfg.workspace / "sessions").glob("*")) if (
        cfg.workspace / "sessions"
    ).is_dir() else set()
    assert after == before


def test_a_tool_says_which_module_defined_it(cfg):
    """Not decoration: a folder cannot reach a tool's name, so a package
    contributes tools under names that are not its own and `csv_columns` comes
    from `csv_profile/` with no slash in sight."""
    (tools_dir(cfg)).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "probe.py").write_text(A_TOOL, encoding="utf-8")

    found = inventory(cfg)

    assert found.tool_sources["probe_one"] == "probe.py"


def test_the_record_says_where_each_catalogue_resolved_to(cfg):
    """A catalogue can be deployed outside the workspace and shared. Three bugs
    have come from a path going stale, so the answer names them.

    All four kinds, which it could not before: the record carried
    `skills_source`, `subagents_source` and `agents_source` as loose strings and
    had no field for `tools` at all, so the one catalogue nobody could see was
    the one nobody had added a line for.
    """
    found = inventory(cfg)

    assert found.origins.skills.path == cfg.skills_dir
    assert found.origins.subagents.path == subagents_dir(cfg)
    assert found.origins.tools.path == cfg.catalogue_roots["tools"]


def test_the_record_cannot_be_edited_after_it_is_handed_back(cfg):
    """Frozen, and its mappings are proxies. A caller that could edit the
    sources in place would be editing what the next caller reads."""
    found = inventory(cfg)

    with pytest.raises(AttributeError):
        found.tools = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        found.tool_sources["probe_one"] = "elsewhere"  # type: ignore[index]


def test_a_resolved_catalogue_is_not_resolved_twice(cfg, monkeypatch):
    """A caller that already has one hands it over. Resolving again is not just
    waste -- it is a second read of the same directories, which is how the two
    halves came to disagree in the first place."""
    from kingfisher.application import inventory as module
    from kingfisher.infrastructure.catalogue import resolve_definitions

    already = resolve_definitions(cfg)
    calls: list[object] = []
    monkeypatch.setattr(module, "resolve_definitions", calls.append)

    found = inventory(cfg, catalogue=already)

    assert calls == []
    # And the handed-over one is what was read, rather than being accepted and
    # ignored -- which would pass the line above and answer from somewhere else.
    assert found.origins.skills.path == cfg.skills_dir


def test_the_record_is_the_only_shape_callers_need(cfg):
    """`Inventory` is what phase 2 makes public, so it is worth saying out loud
    that it carries every field the driver prints -- a caller who has one needs
    nothing else from the library to render a listing."""
    found = inventory(cfg)

    assert isinstance(found, Inventory)
    for name in (
        # One field where there were four -- the workspace and three of the four
        # catalogue paths -- and it answers for all eleven places rather than
        # those three.
        "origins", "builtin_tools", "tools",
        "tool_sources", "tools_error", "skills", "skills_unloadable",
        "skills_misplaced", "skills_misfiled", "subagents", "subagent_sources",
        "subagents_error",
        "skills_enabled",
    ):
        assert hasattr(found, name), name


def test_the_whole_job_is_reachable_through_the_front_door(cfg, shipped):
    """What phase 2 is for, and what the CLI will be held to.

    A consumer -- the server today, the shipped command next -- may write
    `from kingfisher import X` and nothing deeper. If seeding or the inventory
    needed a reach into `infrastructure`, the claim that any caller can do this
    was never true; it simply had nothing testing it.

    Imported here the way a consumer would, not through the modules that define
    them, so a name quietly dropped from `_EXPORTS` fails this rather than
    passing on the module import.
    """
    from kingfisher import Inventory, Seeded, inventory, kinds_at, seed

    written = seed(cfg, shipped)
    found = inventory(cfg)

    assert isinstance(written, Seeded)
    assert isinstance(found, Inventory)
    assert all(isinstance(kind, str) for kind in kinds_at(shipped))
    # And it did something, so this cannot pass by every call being a no-op.
    assert written.written
    assert found.builtin_tools


def test_the_public_names_cost_no_provider_sdk_to_reach(cfg):
    """Reaching them must stay cheap; *calling* `inventory` is another matter.

    Answering builds an agent, so `harness.agent` is imported inside the
    function rather than at module scope. Measured at 21-50ms and 148-192
    modules against 3,100 for a provider -- the split that keeps `--help` from
    paying for a model it will never build.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "from kingfisher import seed, kinds_at, definitions_source, inventory, Inventory\n"
        "print(','.join(m for m in ('deepagents', 'langchain_openai',"
        " 'langchain_anthropic') if m in sys.modules))"
    )
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == ""
