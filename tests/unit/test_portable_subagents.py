"""A subagent declared in Python without a `build`: the shape that travels.

A package exports plain dictionaries and a deployment writes one file to take them,
so nothing here can ask whether an entry was imported -- a re-export hands over
mappings indistinguishable from ones typed into the same file. The rules therefore
hang on the shape, and these drive the shape rather than the origin.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills, ToolAllowlist
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.kinds.subagents.rules import miscounted
from kingfisher.kinds.subagents.spec import (
    KNOWN,
    NOT_PORTABLE,
    PORTABLE,
    SubagentError,
    SubagentSpec,
    declared,
)
from kingfisher.kinds.tools.spec import tool_name
from tests.conftest import FakeToolCallingModel, delegate
from tests.unit.test_bundles import TOOL

SKILL = "---\nname: sampling\ndescription: How to sample a file.\n---\n\n# Sampling\n"


def write_portable(cfg, *, extra: str = "", with_skills: bool = False) -> None:
    """A workspace whose `surveyor` is declared in Python and carries its own."""
    root = cfg.workspace / "subagents"
    root.mkdir(parents=True, exist_ok=True)
    if with_skills:
        folder = root / "surveyor" / "skills" / "sampling"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")

    carried = '"tools": [probe]'
    if with_skills:
        carried += ', "skills": HERE / "surveyor" / "skills"'
    module = "\n".join(
        [
            "from pathlib import Path",
            "",
            "from langchain_core.tools import tool",
            "",
            "HERE = Path(__file__).parent",
            "",
            "",
            "@tool",
            "def probe(text: str) -> str:",
            '    """Probe a file."""',
            '    return "from the bundle"',
            "",
            "",
            "SUBAGENTS = [{",
            '    "name": "surveyor",',
            '    "description": "Surveys files.",',
            '    "system_prompt": "You survey.",',
            '    "builtin_tools": ["read_file", "grep", "execute"],',
            f"    {carried},",
            extra,
            "}]",
        ]
    )
    (root / "surveyor.py").write_text(module + "\n", encoding="utf-8")

    tools = cfg.workspace / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "shared.py").write_text(
        TOOL.format(name="shared", answer="from the catalogue"), encoding="utf-8"
    )


def built(cfg, session_dir, capabilities=None):
    """`surveyor`, as deepagents received it."""
    assembled = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=capabilities
        or Capabilities(subagents=("surveyor",), tools=("shared",)),
    )
    return delegate(assembled, "surveyor")


# -- what it may and may not say --------------------------------------------


@pytest.mark.parametrize("key", sorted(NOT_PORTABLE))
def test_every_refused_key_says_why_it_cannot_travel(key):
    """A generic "unknown key" reads as "kingfisher has not got round to this", when
    the answer is that the key names something only one deployment knows.
    """
    entry = {"name": "s", "description": "d", "system_prompt": "Go.", key: ["x"]}

    with pytest.raises(SubagentError) as raised:
        declared(entry, "acme.py")

    assert NOT_PORTABLE[key] in str(raised.value)


def test_an_unknown_key_lists_what_a_portable_entry_takes():
    """The list a reader is sent to has to be the portable one, not the compiled
    one -- an entry with no `build` that is told to write `build` has been sent to
    the wrong format.
    """
    entry = {"name": "s", "description": "d", "system_prompt": "Go.", "temperature": 1}

    with pytest.raises(SubagentError, match="temperature") as raised:
        declared(entry, "acme.py")

    # The list a portable entry is shown, not the compiled one. Both messages name
    # `build`, one as a key you may write and one as the thing you did not.
    assert str(sorted(PORTABLE)) in str(raised.value)


def test_the_shape_decides_the_rules_rather_than_where_the_entry_came_from():
    """A re-export hands over mappings a local literal cannot be told from, so a rule
    keyed on origin could not fire. `build` is the only thing that separates them.
    """
    portable = declared(
        {"name": "s", "description": "d", "system_prompt": "Go."}, "acme.py"
    )
    compiled = declared(
        {"name": "s", "description": "d", "build": lambda _m, _t: object()}, "acme.py"
    )

    assert portable.system_prompt and portable.build is None
    assert compiled.build is not None and not compiled.system_prompt


# -- the guard that would be a hole -----------------------------------------


def test_a_portable_declaration_is_granted_no_workspace_tool(cfg, session_dir):
    """`SubagentSpec.tools` defaults to `ALL`, which means *inherit whatever the
    request granted* -- and a portable entry has no `tools:` key to say otherwise
    with. Left at the default, an imported delegate would be handed the deployment's
    whole catalogue: the request below grants `shared`, and `shared` must not arrive.
    """
    write_portable(cfg)

    subagent = built(cfg, session_dir)

    assert {tool_name(t) for t in subagent["tools"]} == {"probe"}


def test_the_spec_says_none_rather_than_staying_quiet(cfg):
    """The same guard at the layer it lives in, because the build above would also
    pass if narrowing happened to drop `shared` for some other reason.
    """
    write_portable(cfg)

    spec = LocalSubagentRepository(cfg.workspace / "subagents").specs["surveyor"]

    assert spec.tools is None


# -- atomic ------------------------------------------------------------------


def test_a_carried_tool_reaches_its_delegate_whatever_the_request_granted(
    cfg, session_dir
):
    """What a definition carried is held, not granted: a request naming no tool at
    all still leaves the delegate the one it brought.
    """
    write_portable(cfg)

    subagent = built(
        cfg, session_dir, Capabilities(subagents=("surveyor",), tools=())
    )

    assert {tool_name(t) for t in subagent["tools"]} == {"probe"}


def test_a_carried_tool_is_offered_to_nobody_else(cfg):
    """Atomic from the other side. A carried tool in the shared catalogue would be
    grantable by any agent, which is the half of "you use the subagent, not its
    parts" that a delegate-side check cannot see.
    """
    write_portable(cfg)

    catalogue = Definitions.from_config(cfg).warm()

    assert "probe" not in {found.name for found in catalogue.tools.found}
    assert "probe" in catalogue.bundled_tools["surveyor"].names


def test_builtin_tools_stay_narrowed_for_a_portable_delegate(cfg, session_dir):
    """The exception to atomic, and the one that matters. Built-ins are the host's
    rather than the definition's, so `pip install` must not be a way to put back a
    shell the deployment turned off -- the declaration asks for `execute` and the
    request withholds it.
    """
    write_portable(cfg)

    subagent = built(
        cfg,
        session_dir,
        Capabilities(
            subagents=("surveyor",), tools=(), builtin_tools=("read_file", "grep")
        ),
    )

    (allowlist,) = [m for m in subagent["middleware"] if isinstance(m, ToolAllowlist)]
    assert "execute" not in allowlist._allowed


# -- the skills half ---------------------------------------------------------


def test_a_carried_skill_reaches_the_delegate_that_brought_it(cfg, session_dir):
    """A delegate inherits none of its parent's middleware, so a skill it is not
    given is one it has no idea exists -- and a carried bundle's skills are named by
    a path the definition resolved rather than found by walking the catalogue.
    """
    write_portable(cfg, with_skills=True)

    subagent = built(cfg, session_dir)

    (skills,) = [m for m in subagent["middleware"] if isinstance(m, NarrowedSkills)]
    assert any("sampling" in one for one in skills._allowed)


def test_a_relative_skills_path_is_refused(cfg):
    """It would resolve against whatever directory kingfisher was started in, so the
    package would find its skills from one working directory and silently offer none
    from the next.
    """
    root = cfg.workspace / "subagents"
    root.mkdir(parents=True, exist_ok=True)
    (root / "s.py").write_text(
        "SUBAGENTS = [{'name': 's', 'description': 'd', 'system_prompt': 'Go.', "
        "'skills': 'skills'}]\n",
        encoding="utf-8",
    )

    with pytest.raises(SubagentError, match="relative"):
        _ = LocalSubagentRepository(root).specs


@pytest.mark.parametrize("named", ["'sql_query'", "{'name': 'probe', 'source': 'bundled'}"])
def test_a_portable_entry_carries_tools_and_names_none(cfg, named):
    """A name is a lookup in a catalogue this definition has never seen, and the long
    form a document writes is a name too -- so both are refused, with the reason.
    """
    root = cfg.workspace / "subagents"
    root.mkdir(parents=True, exist_ok=True)
    (root / "s.py").write_text(
        "SUBAGENTS = [{'name': 's', 'description': 'd', 'system_prompt': 'Go.', "
        f"'tools': [{named}]}}]\n",
        encoding="utf-8",
    )

    with pytest.raises(SubagentError, match="tool objects themselves"):
        _ = LocalSubagentRepository(root).specs


def test_the_old_bundle_key_says_what_to_write_instead():
    """Every package shipping a portable entry meets this line on upgrading."""
    entry = {"name": "s", "description": "d", "system_prompt": "Go.", "bundle": {}}

    with pytest.raises(SubagentError) as raised:
        declared(entry, "acme.py")

    assert "'tools'" in str(raised.value)
    assert "'skills'" in str(raised.value)


# -- the claim and the goods -------------------------------------------------


def test_a_carried_bundle_makes_no_claim_to_check(cfg):
    """A document lists what it takes from its folder so the two can be checked
    against each other. A carried bundle *is* the contents, so there is nothing to
    drift from -- and `miscounted` firing on one would refuse every portable
    definition there is.
    """
    write_portable(cfg, with_skills=True)
    catalogue = Definitions.from_config(cfg).warm()

    spec = catalogue.subagents.specs["surveyor"]
    where, tools, skills = catalogue.bundled("surveyor")

    assert not any(spec.bundled.values())
    assert miscounted(spec, where=where, tools=tools, skills=skills) is None


def test_a_spec_cannot_both_describe_a_folder_and_carry_one():
    """Two sources for what a delegate holds, with no rule saying which wins -- and
    `miscounted` would be checking the list against the wrong half.
    """
    with pytest.raises(ValueError, match="one or the other"):
        SubagentSpec(
            name="s",
            description="d",
            system_prompt="Go.",
            bundled={"tools": ("probe",)},
            carried={"tools": ()},
        )


def test_every_field_the_documents_define_is_portable_or_refused_with_a_reason():
    """The drift guard, and it has to be an equality rather than a subset.

    A thirteenth field added to `KNOWN` and to neither table is a key a portable
    entry refuses as merely *unknown* -- which reads as "kingfisher has not got round
    to it" and sends the reader looking for a release rather than for the rule. The
    two directions catch different mistakes: a portable key the documents never
    defined is a field only one reader could ever produce, and a refusal for a key
    that no longer exists is a reason nobody can trigger.
    """
    # `bundle` is outside `KNOWN` because a document refuses it too, and refused here
    # with a reason of its own: it is where a portable entry used to carry its tools.
    assert PORTABLE | set(NOT_PORTABLE) == KNOWN | {"bundle"}


def test_every_portable_key_reaches_a_field_the_spec_has():
    """A key read into nothing. `test_format_parity` holds `KNOWN` against the spec
    for the document readers; this is the same check for the third shape, which that
    file cannot reach because it reads a mapping rather than text.
    """
    fields = set(SubagentSpec.__dataclass_fields__)

    # `tools` and `skills` are the ones that land somewhere else: a portable entry's
    # are carried, so they reach `carried` rather than the fields of their own names.
    assert (PORTABLE - {"tools", "skills"}) <= fields
    assert "carried" in fields


def test_a_carried_skills_folder_is_not_reported_as_abandoned(cfg):
    """`orphaned_assets` finds a folder holding `skills/` that no definition is named
    for, which is exactly what a carried bundle's folder looks like from the walk: the
    definition reaches it by absolute path rather than by sitting in it.

    The folder is named `assets` and the delegate `surveyor` **on purpose**. Name the
    two alike and the folder is covered by the label a carried bundle already
    publishes, so this passes whether or not anything reads the path -- which is how
    the shipped example passed against a check that was doing nothing.
    """
    root = cfg.workspace / "subagents"
    folder = root / "assets" / "skills" / "sampling"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "pack.py").write_text(
        "from pathlib import Path\n\n"
        "HERE = Path(__file__).parent\n\n"
        "SUBAGENTS = [{'name': 'surveyor', 'description': 'd', "
        "'system_prompt': 'Go.', "
        "'skills': HERE / 'assets' / 'skills'}]\n",
        encoding="utf-8",
    )

    repository = LocalSubagentRepository(root)

    assert repository.bundles["surveyor"].skills == root / "assets" / "skills"
    assert repository.orphaned_assets == ()


# -- visible, not grantable --------------------------------------------------


def test_the_listing_says_a_carried_tool_is_not_in_this_workspace(
    workspace_with_presets,
):
    """Both shapes print their private tools the same way, and the names alone do not
    say where the code is. A tool installed by pip that reads exactly like a file in
    this workspace is the thing an operator cannot audit -- driven against the shipped
    catalogue rather than a fixture, so it is the output a reader actually gets.
    """
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import _skills_and_subagents

    printed = list(_skills_and_subagents(inventory(workspace_with_presets)))

    assert "      iso_timestamp  [private tool, carried]" in printed
    assert "      iso-8601  [private skill, carried]" in printed
    # The folder-backed one is unmarked, which is the half that makes the mark mean
    # something: mark both and the word says only that a bundle exists.
    assert "      mask_secrets  [private tool]" in printed


def test_doctor_names_what_arrived_with_a_definition(workspace_with_presets):
    """`ok` rather than a warning -- installing a package is a decision, not a
    defect -- but said at all, because a carried bundle's tools run in this sandbox
    on this deployment's credentials and are files nobody here reviewed.
    """
    from kingfisher.presentation.cli.health import examine

    said = [
        check
        for check in examine(workspace_with_presets)
        if check.name == "delegate tools" and "carried" in check.detail
    ]

    assert [check.verdict for check in said] == ["ok"]
    assert "timestamps" in said[0].detail


def test_carrying_is_read_off_the_backing_rather_than_the_file_extension(cfg):
    """A `.py` under `subagents/` may own a folder like any document, so a listing
    that decided this by how a definition was written would call a compiled delegate
    with an ordinary bundle 'carried' and mislead about where its tools are.
    """
    from kingfisher.application.inventory import inventory

    root = cfg.workspace / "subagents" / "surveyor"
    root.mkdir(parents=True, exist_ok=True)
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools" / "probe.py").write_text(
        TOOL.format(name="probe", answer="from the folder"), encoding="utf-8"
    )
    (root / "surveyor.py").write_text(
        "SUBAGENTS = [{'name': 'surveyor', 'description': 'd', "
        "'system_prompt': 'Go.'}]\n",
        encoding="utf-8",
    )

    found = inventory(cfg)

    assert found.bundled_tools["surveyor"] == ("probe",)
    assert found.carried_bundles == ()


def test_the_helper_refusal_points_at_a_key_that_exists():
    """The advice sends a reader to `build`, and prose cannot be renamed by a
    refactor that renames the key.

    It guards staleness rather than wrongness -- no test catches advice that is
    merely bad, which is what this message used to be: it said to fold the step into
    the prompt, in the one case where a prompt cannot help. What it does catch is the
    remedy quietly ceasing to exist.
    """
    from kingfisher.kinds.subagents.spec import DECLARED

    said = NOT_PORTABLE["subagents"]
    named = [key for key in DECLARED if f"'{key}'" in said]

    assert named == ["build"]


# -- one tool axis everything, the other nothing ------------------------------

#: Each leaves one axis at `ALL` and the other empty, which is every portable
#: delegate's default: it may use the host's built-ins and holds no workspace tool.
ONE_AXIS_OPEN = {
    "portable, default": (
        "surveyor.py",
        "SUBAGENTS = [{'name': 'surveyor', 'description': 'd', 'system_prompt': 'Go.'}]\n",
    ),
    "document, no workspace tools": (
        "surveyor.yaml",
        "name: surveyor\ndescription: d\nsystem_prompt: |\n  Go.\ntools: []\n",
    ),
    "document, no built-ins": (
        "surveyor.yaml",
        "name: surveyor\ndescription: d\nsystem_prompt: |\n  Go.\nbuiltin_tools: []\n",
    ),
}


@pytest.mark.parametrize("filename, text", ONE_AXIS_OPEN.values(), ids=ONE_AXIS_OPEN)
def test_a_delegate_with_one_axis_open_builds_in_a_workspace_with_no_tools(
    cfg, session_dir, filename, text
):
    """A request naming only the delegate raised `ValueError: one tool axis resolved
    to '*'`: with no workspace tools and nothing narrowed, the names were never read
    off the probe, and "every built-in plus none" has no spelling as an allowlist.
    """
    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / filename).write_text(text, encoding="utf-8")

    subagent = built(cfg, session_dir, Capabilities(subagents=("surveyor",)))

    (allowlist,) = [m for m in subagent["middleware"] if isinstance(m, ToolAllowlist)]
    if "builtin_tools: []" in text:
        assert "read_file" not in allowlist._allowed
    else:
        assert {"read_file", "execute"} <= allowlist._allowed
    assert "*" not in allowlist._allowed
