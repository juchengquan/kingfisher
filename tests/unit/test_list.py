"""`kingfisher list`, and the rule it is held to."""

from __future__ import annotations

import json

import pytest

from kingfisher.cli.__main__ import main
from tests.conftest import EVERY_TOOL, an_agent, subagents_dir


def test_listing_reports_a_workspace_that_will_not_load(cfg, at_the_command_line, capsys):
    """Non-zero, because a listing gets read by scripts."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list"]) == 1

    assert "cannot load" in capsys.readouterr().out


def test_a_workspace_with_no_agents_says_so_and_says_what_to_do(at_the_command_line, capsys):
    """The empty listing for agents, which the ones for skills and subagents had
    and this did not -- a mutation removing it went unnoticed.

    It carries the seed hint because definitions with no agents cannot serve a
    request at all: every other emptiness here is survivable.
    """

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "(none)" in printed
    assert "a request must name one" in printed


def test_a_workspace_holding_an_agent_does_not_say_none(cfg, at_the_command_line, capsys):
    """So the rule above is not passing on a listing that says it whatever it holds."""
    from tests.conftest import an_agent

    an_agent(cfg, "analyst")

    assert main(["list"]) == 0

    assert "a request must name one" not in capsys.readouterr().out


def test_a_missing_catalogue_is_reported_rather_than_raised(tmp_path, monkeypatch, capsys):
    """The one error a caller causes and can fix."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert main(["list"]) == 2

    printed = capsys.readouterr().err
    assert "configuration error" in printed
    # Where the answer would have come from. It said `.env` is never read, which
    # was true and was the reason this failed where `the driver` worked; now the
    # useful thing is *which* file was read, since a caller one directory from
    # theirs is told about a variable that is set, just not here.
    assert "the environment and" in printed
    assert ".env" in printed


def test_both_drivers_render_through_the_same_code(cfg, capsys):
    """Two doors printing one block, and now by construction."""
    from kingfisher import inventory
    from kingfisher.cli.listing import render
    from tests.integration import driver

    _seed_something(cfg)

    assert driver.show_inventory(cfg, cfg.workspace) == 0
    printed = capsys.readouterr().out

    expected = "\n".join(render(inventory(cfg)))
    assert printed.strip() == expected.strip()


def _seed_something(cfg) -> None:
    """One skill and one subagent, so the comparison has something to disagree about."""
    skill = cfg.skills_dir / "probe-skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: probe-skill\ndescription: Something to list.\n---\nDo it.\n",
        encoding="utf-8",
    )
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "probe-agent.yaml").write_text(
        "name: probe-agent\ndescription: Something to list.\n"
        "system_prompt: |\n  Answer briefly.\n",
        encoding="utf-8",
    )
    # And a name two folders both claim, which is printed as a reference rather
    # than as a name plus the file it came from. The case the copy got wrong.
    for folder in ("team", "vendor"):
        (subagents_dir(cfg) / folder).mkdir(parents=True, exist_ok=True)
        (subagents_dir(cfg) / folder / "surveyor.yaml").write_text(
            f"name: surveyor\ndescription: Surveys, the {folder} way.\n"
            "system_prompt: |\n  Survey it.\n",
            encoding="utf-8",
        )


# -- `list --json`, for a script rather than a person ----------------------


def test_the_json_document_carries_every_field_the_record_has(cfg):
    """"Field for field" is a claim, and this is the mechanism."""
    from dataclasses import fields

    from kingfisher import inventory
    from kingfisher.cli.listing import as_json

    document = as_json(inventory(cfg))

    assert set(document) == {field.name for field in fields(inventory(cfg))}


def test_the_json_document_survives_a_round_trip(cfg):
    """It is only worth having if `json.dumps` accepts it."""
    import json

    from kingfisher import inventory
    from kingfisher.cli.listing import as_json

    _seed_something(cfg)

    document = json.loads(json.dumps(as_json(inventory(cfg))))

    # Under `origins` now, with every other place beside it. `Path` is what
    # made this test worth having and the record still holds them, one layer in.
    assert isinstance(document["origins"]["workspace"], str)
    assert document["origins"]["definitions"] == str(cfg.definitions)
    assert document["origins"]["skills"] == {
        "kind": "default",
        "path": str(cfg.skills_dir),
    }
    assert "probe-agent" in document["subagents"]
    assert document["tools_error"] is None


def test_the_header_names_every_catalogue_including_tools(cfg):
    """The regression this record was built to make impossible."""
    from kingfisher import inventory
    from kingfisher.cli.listing import render

    header = list(render(inventory(cfg)))[:10]

    assert [line.split(" :")[0].strip() for line in header] == [
        "workspace", "definitions", "agents", "middlewares", "skills", "subagents", "tools",
        "models", "seed", "sessions",
    ]


def test_the_json_carries_the_kind_and_a_path_a_script_can_open(cfg, tmp_path):
    """Two things the header deliberately does not do."""
    import json
    from dataclasses import replace

    from kingfisher import inventory
    from kingfisher.cli.listing import as_json

    document = json.loads(json.dumps(as_json(inventory(replace(cfg, assets=tmp_path)))))
    origins = document["origins"]

    assert origins["skills"]["path"].startswith("/"), "not the ./name the header prints"
    assert origins["seed"] == {"kind": "relocated", "path": str(tmp_path)}


def test_json_and_the_human_form_describe_the_same_workspace(cfg, at_the_command_line, capsys):
    """Two formats, one answer."""
    import json

    _seed_something(cfg)

    assert main(["list"]) == 0
    printed = capsys.readouterr().out
    assert main(["list", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)

    # The workspace was seeded above, so both are populated -- asserted rather
    # than assumed, because two empty loops agree with each other perfectly and
    # this test's whole claim is that the two formats say the same thing.
    assert document["subagents"] and document["skills"]

    for name in document["subagents"]:
        assert name in printed
    for name in document["skills"]:
        assert name in printed

    # And the access section, which is where the two came apart: the human form printed
    # none of it while `--json` carried every definition's `source_ids:` line. Asserted in
    # both directions, so that either form going silent does not pass it.
    says_so = "access —" in printed
    assert (document["vocabulary"] is not None) == says_so
    assert bool(document["audiences"]) == says_so


def test_a_broken_workspace_is_non_zero_in_either_format(cfg, at_the_command_line, capsys):
    """The exit code does not depend on the format, and the reason is in the document
    too -- so a script can find out either way round.
    """
    import json

    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["subagents_error"]


def test_json_is_asked_for_rather_than_assumed(cfg, at_the_command_line, capsys):
    """A listing whose default output is JSON is a listing nobody reads."""

    assert main(["list"]) == 0

    assert not capsys.readouterr().out.lstrip().startswith("{")


# -- a subagent its definition built itself --------------------------------


COMPILED_MODULE = """from langchain_core.runnables import RunnableLambda


def _build(model, tools):
    return RunnableLambda(lambda state: state)


SUBAGENTS = [
    {
        "name": "researcher",
        "description": "Researches a topic.",
        "build": _build,
    }
]
"""


PROMPTED_DEFINITION = """name: reviewer
description: Checks figures.
system_prompt: |
  You check figures.
"""


def _subagent_catalogue(cfg):
    directory = cfg.definitions / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "researcher.py").write_text(COMPILED_MODULE, encoding="utf-8")
    (directory / "reviewer.yaml").write_text(PROMPTED_DEFINITION, encoding="utf-8")


def test_the_listing_marks_a_compiled_subagent(cfg):
    """Marked because the rest of the listing means something different for it, and
    nothing else in the output would say so.
    """
    from kingfisher.cli.listing import render
    from kingfisher.core.inventory import inventory

    _subagent_catalogue(cfg)
    lines = list(render(inventory(cfg)))
    named = {name: [one for one in lines if one.strip().startswith(name)]
             for name in ("researcher", "reviewer")}

    assert "[compiled]" in named["researcher"][0]
    assert "[compiled]" not in named["reviewer"][0]


def test_the_listing_says_what_a_compiled_subagent_costs(cfg):
    """The assumption a reader would otherwise make."""
    from kingfisher.cli.listing import render
    from kingfisher.core.inventory import inventory

    _subagent_catalogue(cfg)
    printed = "\n".join(render(inventory(cfg)))

    assert "a tools: grant narrows" in printed
    assert "not what it can call" in printed


def test_a_workspace_with_no_compiled_subagent_says_nothing_about_them(cfg):
    """The note is about a minority, so it stays absent for everyone else -- a caveat
    printed to every reader is one none of them reads.
    """
    from kingfisher.cli.listing import render
    from kingfisher.core.inventory import inventory

    directory = cfg.definitions / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reviewer.yaml").write_text(PROMPTED_DEFINITION, encoding="utf-8")

    printed = "\n".join(render(inventory(cfg)))

    assert "compiled" not in printed


def test_a_compiled_subagent_is_not_annotated_with_the_file_you_can_already_see(cfg):
    """`_from` stays silent when the name already tells you the file."""
    from kingfisher.cli.listing import render
    from kingfisher.core.inventory import inventory

    _subagent_catalogue(cfg)
    (line,) = [
        one for one in render(inventory(cfg))
        if one.strip().startswith("researcher")
    ]

    assert "(researcher.py)" not in line


def test_the_json_listing_carries_it_too(cfg):
    """`--json` is what a script reads, and a script deciding whether a grant means
    anything needs the same fact the text gives a person.
    """
    from kingfisher.cli.listing import as_json
    from kingfisher.core.inventory import inventory

    _subagent_catalogue(cfg)

    assert as_json(inventory(cfg))["compiled_subagents"] == ["researcher"]


def test_a_skill_offered_under_another_name_is_named_in_the_listing(
    cfg,
    at_the_command_line,
    monkeypatch,
    capsys,
):
    """`--list` is where somebody goes *because* a grant was refused for a skill they
    can see in the tree.
    """
    directory = cfg.skills_dir / "company-lookup"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: find-company\ndescription: Looks a company up.\n---\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KINGFISHER_SKILLS_ENABLED", "1")

    assert main(["list"]) == 0, "a misfiled skill loads, so this is not a failure"

    printed = capsys.readouterr().out
    assert "company-lookup/ is offered as find-company" in printed
    assert "rename the directory to match" in printed


def test_an_unloadable_agent_catalogue_is_non_zero_too(cfg, at_the_command_line, capsys):
    """The kind that arrived last, and the one `failed` did not name."""
    import json

    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "broken.yaml").write_text("name: broken\ndescription: d\nnope: 1\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["agents_error"]


def test_an_unloadable_middleware_module_is_non_zero_too(cfg, at_the_command_line, capsys):
    """The fifth kind, and the one `failed` named last.

    `middlewares/*.py` is Python that has to import, exactly like `tools/*.py`, and a
    deployment that starts and fails on the first request activating an agent that
    names one is the shape this predicate already exists to stop.
    """
    import json

    middlewares = cfg.catalogue_roots["middlewares"]
    middlewares.mkdir(parents=True, exist_ok=True)
    (middlewares / "wrong.py").write_text(
        "class NotMiddleware:\n    name = 'nope'\n\n\nMIDDLEWARES = [NotMiddleware]\n",
        encoding="utf-8",
    )

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["middlewares_error"]


def test_a_definition_naming_a_moved_tool_is_non_zero_too(
    cfg,
    at_the_command_line,
    capsys,
    shipped,
):
    """`failed` stopped meaning "will not load" here, and this is the case that
    changed it: a subagent naming a moved tool will not load, an agent naming one
    loads and runs without it. Both are definitions that do not mean what they say.
    """
    import json

    from kingfisher import seed

    seed(cfg.definitions, shipped)
    (cfg.catalogue_roots["tools"] / "csv_profile").rename(
        cfg.catalogue_roots["tools"] / "analysis"
    )

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["moved_tools"]


def test_a_broken_middleware_module_tells_the_person_which_file(
    cfg, at_the_command_line, capsys
):
    """Exit 1 over output that looked fine is what this closes. The human listing had no
    middlewares section at all, so the only hint was `(could not introspect)` under the
    *builtin tools* heading -- which sends a reader to the wrong directory, since what a
    broken middleware costs is the graph the built-in set is read off.
    """
    middlewares = cfg.catalogue_roots["middlewares"]
    middlewares.mkdir(parents=True, exist_ok=True)
    (middlewares / "cap.py").write_text("this is not python(\n", encoding="utf-8")

    assert main(["list"]) == 1

    printed = capsys.readouterr().out
    assert "cap.py" in printed, "the file to go and open"
    assert "cannot load" in printed


def test_the_middlewares_section_names_what_is_registered(cfg, at_the_command_line, capsys):
    """So the rule above is not passing on a section that only ever says it cannot load,
    and the fifth kind is listed like the other four rather than only when it breaks.
    """
    middlewares = cfg.catalogue_roots["middlewares"]
    middlewares.mkdir(parents=True, exist_ok=True)
    (middlewares / "audit.py").write_text(
        "from langchain.agents.middleware import AgentMiddleware\n\n\n"
        "class Audit(AgentMiddleware):\n    name = 'audit'\n\n\n"
        "MIDDLEWARES = [Audit]\n",
        encoding="utf-8",
    )

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "\nmiddlewares\n" in printed
    assert "  audit\n" in printed, "the name a definition writes, and nothing else"
    assert "kingfisher_workspace" not in printed, "never the dynamic module name"


def test_a_middleware_that_came_from_a_package_says_which(cfg, at_the_command_line, capsys):
    """The other half, and the one the mark means something by: a definition file may list
    a class it imported, and then the code is not in the definitions folder to be read. Without
    this the section says the same thing about both, which is what marking one is for.
    """
    middlewares = cfg.catalogue_roots["middlewares"]
    middlewares.mkdir(parents=True, exist_ok=True)
    (middlewares / "borrowed.py").write_text(
        "from langchain.agents.middleware import TodoListMiddleware\n\n\n"
        "MIDDLEWARES = [TodoListMiddleware]\n",
        encoding="utf-8",
    )

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "(from langchain" in printed, "the package it arrived from"
    # And the name, which is the one case `name_of`'s string guard is for:
    # `AgentMiddleware.name` is a *property*, and this class does not override it, so
    # without that guard the listing prints `<property object at 0x...>` as a
    # middleware's name and `classes` is keyed by something no definition can select.
    # Measured: removing the guard left the whole suite green, because every other
    # middleware in it spells its own name.
    assert "  todo_list" in printed or "  TodoListMiddleware" in printed
    assert "property object" not in printed


def test_an_unloadable_tool_still_leaves_the_rest_of_the_listing(
    cfg,
    at_the_command_line,
    capsys,
):
    """One unloadable catalogue must not take the others down with it, which is this
    record's own rule and was not true of tools.
    """
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        "name: helper\ndescription: A subagent.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )
    tools = cfg.catalogue_roots["tools"]
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "broken.py").write_text("this is not python(\n", encoding="utf-8")

    assert main(["list"]) == 1
    printed = capsys.readouterr().out

    assert "cannot load" in printed
    assert "\nskills" in printed, "the skills section went with the tools"
    assert "helper" in printed, "so did the subagents"


# -- listing under a source-id vocabulary ---------------------------------------

#: A tool goes out of a caller's reach two ways and the rules below need to tell them
#: apart: `line_count` is kept from `B` by its own lock inside an agent they *can* open,
#: and `secret_export` is offered only by one they cannot. `word_tally` says nothing
#: about who may use it, for an agent to share with both.
TOOL = '''
from kingfisher import ToolSpec


def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


def word_tally(path: str) -> str:
    """Tally the words in a text file."""
    return "0"


def secret_export(path: str) -> str:
    """Export something."""
    return "0"


TOOLS = [ToolSpec(line_count, source_ids=["A"]), word_tally, secret_export]
'''


NARROW = """name: narrow
description: An agent.
source_ids: [A, B]
tools: [line_count]
system_prompt: |
  You do the task.
"""


WIDE = """name: wide
description: An agent.
source_ids: [A, B]
tools: [word_tally]
system_prompt: |
  You do the task.
"""


def _workspace(cfg, *agents: str, skills: tuple[str, ...] = ()):
    """Definitions with two tools, whichever agents the test names, and no skills
    unless it asks for some.

    Fills the definitions folder and nothing else: pointing the command at it is
    `at_the_command_line`'s job, which every caller here takes.
    """
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    # Named for neither tool it defines, because the rules below search the whole
    # rendered output for a name: a module named after one of its tools puts that name
    # in the *path* printed beside the other one.
    (tools_dir(cfg) / "kit.py").write_text(TOOL, encoding="utf-8")
    for skill in skills:
        folder = cfg.skills_dir / skill
        folder.mkdir(parents=True, exist_ok=True)
        # A description sharing no word with any skill name here, for the same reason:
        # one skill's name inside another's description is a match too.
        (folder / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: Something to do.\n---\nDo it.\n",
            encoding="utf-8",
        )
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for document in agents:
        name = document.split("name: ", 1)[1].split("\n", 1)[0]
        (directory / f"{name}.yaml").write_text(document, encoding="utf-8")
    return cfg


@pytest.fixture
def policied(cfg, at_the_command_line):
    return _workspace(cfg, NARROW)


#: An agent only `A` reaches, beside the `A, B` one the fixtures use, so a caller
#: holding `B` has something to be kept from.
A_ONLY = """name: hidden
description: An agent.
source_ids: [A]
tools: [secret_export]
system_prompt: |
  You do the task.
"""


#: Names belonging to subagents only `A` reaches: a subagent with its own folder, the
#: private tool, skill and too-deep skill in it, and a compiled one.
OUT_OF_REACH = ("shadowy", "hushhush", "quietly", "buried", "clandestine")

#: The same, for subagents whose faults are reported rather than refused by a listing:
#: one listing less than its folder holds, one naming a tool by a moved path, a
#: compiled one with a skill it can never be shown, a folder no definition is named
#: for, and one naming a helper nobody defines.
BROKEN_OUT_OF_REACH = (
    "muddled", "stray", "drifter", "stowaway", "cargo", "ghostly", "wanderer", "nowhere",
    # An agent's own, because the per-definition fault maps key agents as well as
    # subagents and the two halves are filtered by separate expressions: with only
    # subagents here, pointing the agent half at every agent changed nothing.
    "straying", "noplace",
)


def _private(path, name: str, body: str = '"""Does a thing."""\n    return ""') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"def {name}(path: str) -> str:\n    {body}\n\n\nTOOLS = [{name}]\n", encoding="utf-8"
    )


def _skill(folder, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Something to do.\n---\nDo it.\n", encoding="utf-8"
    )


def _subagents_out_of_reach(cfg) -> None:
    """Every per-subagent field valid definitions fill, for a subagent `B` cannot open.

    The private `line_count` shadows the catalogue's, so `shadowed` names it too.
    """
    root = cfg.catalogue_roots["subagents"]
    folder = root / "shadowy"
    _private(folder / "tools" / "hushhush.py", "hushhush")
    _private(folder / "tools" / "own_count.py", "line_count")
    _skill(folder / "skills" / "quietly", "quietly")
    _skill(folder / "skills" / "group" / "buried", "buried")
    (folder / "shadowy.yaml").write_text(
        "name: shadowy\ndescription: A subagent.\nsource_ids: [A]\n"
        "builtin_tools: [read_file]\n"
        "tools: [{name: hushhush, source: bundled}, {name: line_count, source: bundled}]\n"
        "skills: [{name: quietly, source: bundled}]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    (root / "clandestine.py").write_text(
        "def build(model, tools):\n    return None\n\n\n"
        "SUBAGENTS = [{'name': 'clandestine', 'description': 'A compiled one.', "
        "'build': build, 'source_ids': ['A']}]\n",
        encoding="utf-8",
    )


def _broken_definitions_out_of_reach(cfg) -> None:
    """Faults belonging to definitions only `A` reaches, of both kinds.

    The agent is not decoration: `moved_tools` and `unresolved_subagents` key agents
    and subagents alike, and the listing filters the two halves with separate
    expressions -- so a fixture of subagents alone leaves the agent half unheld.
    """
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "straying.yaml").write_text(
        'name: straying\ndescription: An agent.\nsource_ids: [A]\nbuiltin_tools: ["*"]\n'
        "tools: [adrift.py::line_count]\nsubagents: [noplace]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    root = cfg.catalogue_roots["subagents"]
    _private(root / "muddled" / "tools" / "stray.py", "stray")
    (root / "muddled" / "muddled.yaml").write_text(
        "name: muddled\ndescription: A subagent.\nsource_ids: [A]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    (root / "drifter.yaml").write_text(
        "name: drifter\ndescription: A subagent.\nsource_ids: [A]\n"
        "tools: [moved.py::line_count]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    (root / "stowaway").mkdir(parents=True)
    (root / "stowaway" / "stowaway.py").write_text(
        "def build(model, tools):\n    return None\n\n\n"
        "SUBAGENTS = [{'name': 'stowaway', 'description': 'A compiled one.', "
        "'build': build, 'source_ids': ['A']}]\n",
        encoding="utf-8",
    )
    _skill(root / "stowaway" / "skills" / "cargo", "cargo")
    _private(root / "ghostly" / "tools" / "boo.py", "boo")
    (root / "wanderer.yaml").write_text(
        "name: wanderer\ndescription: A subagent.\nsource_ids: [A]\n"
        'builtin_tools: ["*"]\ntools: ["*"]\nsubagents: [nowhere]\nsystem_prompt: |\n  Go.\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_carries_nothing_out_of_reach(form, cfg, at_the_command_line, capsys):
    """Both forms, and asserted over the whole output rather than field by field.

    Measured through the real command before this existed: the text form hid the
    unreachable agent and `--json` carried its name, the file it came from, its
    subagent chain, every definition's audience and the vocabulary itself -- so an
    operator checking a policy got a different answer depending on the flag, and the
    flag that answered wrongly is the one a script reads.

    Searching the rendered output for the name, rather than naming the fields that
    leaked, is deliberate: the four that did were not a closed set, and a field added
    later would leak the same way past a field-by-field assertion.

    And subagents out of reach in the definitions folder, with folders of their own: without
    them every per-subagent field is empty, and the rule passes whatever those fields
    would have held.
    """
    _workspace(cfg, NARROW, A_ONLY)
    _subagents_out_of_reach(cfg)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "hidden" not in printed, f"the {form} form named an agent this caller cannot open"
    named = [one for one in OUT_OF_REACH if one in printed]
    assert not named, f"the {form} form named {named}, which belong to subagents out of reach"
    assert "narrow" in printed, "and it should still show the one they can"


def test_a_buried_skill_is_kept_to_callers_who_reach_its_subagent(
    cfg, at_the_command_line, capsys
):
    """Keyed by subagent name, so unfiltered it names a subagent this caller cannot
    open -- the leak the per-subagent maps here have had before. The operator's view
    is the control.
    """
    _workspace(cfg, NARROW)
    folder = cfg.catalogue_roots["subagents"] / "keeper"
    skill = folder / "skills" / "group" / "buried"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: buried\ndescription: Something to do.\n---\nDo it.\n", encoding="utf-8"
    )
    (folder / "keeper.yaml").write_text(
        "name: keeper\ndescription: A subagent.\nsource_ids: [A]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )

    assert main(["list", "--as", "B", "--json"]) == 0
    scoped = json.loads(capsys.readouterr().out)
    assert main(["list", "--json"]) == 0
    whole = json.loads(capsys.readouterr().out)

    assert scoped["bundled_misplaced"] == {}
    assert whole["bundled_misplaced"] == {"keeper": ["group/buried"]}


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_reports_no_fault_of_a_definition_out_of_reach(
    form, cfg, at_the_command_line, capsys
):
    """A moved tool, a miscounted folder, a stranded skill, an orphan and an agent of
    its own, each naming a definition or a folder the caller cannot attribute -- and
    the operator's listing, the control, names all of them and fails.

    The exit status follows the caller's view too: failing a listing over a fault in a
    subagent the caller cannot open would say there is one.
    """
    _workspace(cfg, NARROW)
    _broken_definitions_out_of_reach(cfg)
    as_json = ["--json"] if form == "json" else []

    whole_code = main(["list", *as_json])
    whole = capsys.readouterr().out
    scoped_code = main(["list", "--as", "B", *as_json])
    scoped = capsys.readouterr().out

    assert (whole_code, [one for one in BROKEN_OUT_OF_REACH if one not in whole]) == (1, [])
    named = [one for one in BROKEN_OUT_OF_REACH if one in scoped]
    assert (scoped_code, named) == (0, []), f"the {form} form named {named}"


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_names_no_lock_out_of_reach(form, cfg, at_the_command_line, capsys):
    """The names in use list every definition a lock is on and every name it writes, so
    a scoped view carrying them would hand a caller the definitions out of their reach
    and the names that lock them.

    The operator's view is the control: it names them all, which is what the scoped
    silence is measured against. One of each kind, because a kind left out of the walk
    would pass with the other one alone.
    """
    _workspace(cfg, NARROW, A_ONLY)
    agents = cfg.catalogue_roots["agents"]
    (agents / "trespasser.yaml").write_text(
        "name: trespasser\ndescription: An agent.\nsource_ids: [A, YAK]\n"
        "system_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    root = cfg.catalogue_roots["subagents"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "insider.yaml").write_text(
        "name: insider\ndescription: A subagent.\nsource_ids: [A, ZEBRA]\n"
        "system_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    as_json = ["--json"] if form == "json" else []
    told = ("insider", "ZEBRA", "trespasser", "YAK")

    whole_code = main(["list", *as_json])
    whole = capsys.readouterr().out
    scoped_code = main(["list", "--as", "B", *as_json])
    scoped = capsys.readouterr().out

    named = [one for one in told if one in scoped]
    assert (scoped_code, named) == (0, []), f"the {form} form named {named}"
    assert (whole_code, [one for one in told if one not in whole]) == (0, [])


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_carries_only_the_skill_faults_it_reaches(
    form, cfg, at_the_command_line, capsys
):
    """A misfiled skill *is* loaded and offered, so the fault names a skill and its
    directory where `skills` itself had correctly kept both out of this caller's view.
    A skill that never loaded reaches nobody, so its directory is the operator's
    business like an orphan.

    The skill this caller does reach is the half that stops the fix being "empty it":
    filtered by what the definitions they reach offer, the fault they can act on
    survives. Driven with the skills switch off, which is where the fault fields are
    reported from anyway.
    """
    reaches_it = NARROW.replace(
        "system_prompt:", "builtin_tools: [read_file]\nskills: [shown]\nsystem_prompt:"
    )
    out_of_reach = A_ONLY.replace(
        "system_prompt:", "builtin_tools: [read_file]\nskills: [concealed]\nsystem_prompt:"
    )
    _workspace(cfg, reaches_it, out_of_reach)
    for folder, declares in (("shown_dir", "shown"), ("concealed_dir", "concealed")):
        where = cfg.skills_dir / folder
        where.mkdir(parents=True, exist_ok=True)
        (where / "SKILL.md").write_text(
            f"---\nname: {declares}\ndescription: Something to do.\n---\nDo it.\n",
            encoding="utf-8",
        )
    nested = cfg.skills_dir / "too" / "deep" / "buriedskill"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "SKILL.md").write_text(
        "---\nname: buriedskill\ndescription: Something to do.\n---\nDo it.\n", encoding="utf-8"
    )
    unreadable = cfg.skills_dir / "wontparse"
    unreadable.mkdir(parents=True, exist_ok=True)
    (unreadable / "SKILL.md").write_text("---\nnot: yaml: at all\n", encoding="utf-8")
    as_json = ["--json"] if form == "json" else []
    withheld = ("concealed", "wontparse", "buriedskill")

    assert main(["list", *as_json]) == 0
    whole = capsys.readouterr().out
    assert main(["list", "--as", "B", *as_json]) == 0
    scoped = capsys.readouterr().out

    named = [one for one in withheld if one in scoped]
    assert not named, f"the {form} form named {named}, which this caller cannot reach"
    assert "shown_dir" in scoped, "the fault this caller can act on went with them"
    assert [one for one in (*withheld, "shown_dir") if one not in whole] == []


#: Each way a definition can name a subagent that a run then refuses: the files under
#: `subagents/` as `where: (name, its subagents line)`, the agent's own line, and the
#: name the listing should blame on whom.
UNRESOLVED = {
    "a helper nobody defines": (
        {"reviewer.yaml": ("reviewer", "[ghost]")}, "[reviewer]", "subagent 'reviewer'", "ghost"
    ),
    "a helper two files define": (
        {
            "reviewer.yaml": ("reviewer", "[second]"),
            "a/second.yaml": ("second", None),
            "b/second.yaml": ("second", None),
        },
        "[reviewer]",
        "subagent 'reviewer'",
        "second",
    ),
    "a subagent nobody defines": (
        {"reviewer.yaml": ("reviewer", None)}, "[reviewer, ghost]", "agent 'lead'", "ghost"
    ),
    "a subagent two files define": (
        {"a/second.yaml": ("second", None), "b/second.yaml": ("second", None)},
        "[second]",
        "agent 'lead'",
        "second",
    ),
}


#: Both tool lines at `["*"]`, for a subagent here that names a helper and so needs `task`.
STARS = 'builtin_tools: ["*"]\ntools: ["*"]\n'


def _define(cfg, files, agent_line: str) -> None:
    for where, (name, helpers) in files.items():
        path = subagents_dir(cfg) / where
        path.parent.mkdir(parents=True, exist_ok=True)
        line = "" if helpers is None else f"{STARS}subagents: {helpers}\n"
        path.write_text(
            f"name: {name}\ndescription: A subagent.\n{line}system_prompt: |\n  Go.\n",
            encoding="utf-8",
        )
    an_agent(cfg, "lead", subagents=agent_line, **EVERY_TOOL)


@pytest.mark.parametrize("case", sorted(UNRESOLVED))
def test_a_subagent_name_a_run_refuses_fails_the_listing(case, cfg, at_the_command_line, capsys):
    """Each of these exited 0 and said nothing while every run reaching the name
    refused it -- measured through the real command, where the agent's own subagents
    line dropped the name without a word.
    """
    files, agent_line, subject, name = UNRESOLVED[case]
    _define(cfg, files, agent_line)

    assert main(["list"]) == 1
    assert f"{subject}: {name}" in capsys.readouterr().out
    assert main(["list", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["unresolved_subagents"] == {subject: [name]}


def test_a_shared_name_written_with_its_file_is_not_reported(cfg, at_the_command_line, capsys):
    """The control: what the rule above tells a reader to write must pass it, by
    either kind -- a check comparing bare names would report it too.
    """
    _define(
        cfg,
        {
            "reviewer.yaml": ("reviewer", "[a/second.yaml::second]"),
            "a/second.yaml": ("second", None),
            "b/second.yaml": ("second", None),
        },
        "[reviewer, b/second.yaml::second]",
    )

    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unresolved_subagents"] == {}


def test_an_agent_granted_every_subagent_is_not_reported(cfg, at_the_command_line, capsys):
    """`*` read as a name is one no subagent has, which reported an agent granted all
    of them as naming a subagent that does not exist.
    """
    _define(cfg, {"reviewer.yaml": ("reviewer", None)}, '["*"]')

    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unresolved_subagents"] == {}


#: Each way a definition can name a tool that a run then refuses: the agent's own fields,
#: the subagent's `tools:` line or `None` for no subagent, and the name the listing should
#: blame on whom.
UNKNOWN_TOOLS = {
    "an offered tool nobody defines": (
        {"tools": "[word_tally, lokup]"}, None, "agent 'lead'", "lokup"
    ),
    "a built-in that does not exist": (
        {"builtin_tools": "[read_fiel]"}, None, "agent 'lead'", "read_fiel"
    ),
    "an offered tool on the built-in line": (
        {"builtin_tools": "[word_tally]"}, None, "agent 'lead'", "word_tally"
    ),
    "a subagent's tool nobody defines": (
        {**EVERY_TOOL, "subagents": "[reviewer]"}, "[lokup]", "subagent 'reviewer'", "lokup"
    ),
}


def _name_tools(cfg, agent_fields: dict[str, str], subagent_tools: str | None) -> None:
    _workspace(cfg)
    if subagent_tools is not None:
        path = subagents_dir(cfg) / "reviewer.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "name: reviewer\ndescription: A subagent.\n"
            f"tools: {subagent_tools}\nsystem_prompt: |\n  Go.\n",
            encoding="utf-8",
        )
    an_agent(cfg, "lead", **agent_fields)


@pytest.mark.parametrize("case", sorted(UNKNOWN_TOOLS))
def test_a_tool_name_a_run_refuses_fails_the_listing(case, cfg, at_the_command_line, capsys):
    """Each of these exited 0, and `doctor` with it, while every run reaching the definition
    was refused -- found through an agent naming a tool its client's adapter had left out.
    """
    fields, subagent_tools, subject, name = UNKNOWN_TOOLS[case]
    _name_tools(cfg, fields, subagent_tools)

    assert main(["list"]) == 1
    assert f"{subject}: {name}" in capsys.readouterr().out
    assert main(["list", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["unresolved_tools"] == {subject: [name]}


def test_tools_named_as_they_are_offered_are_not_reported(cfg, at_the_command_line, capsys):
    """The control: both lines naming what a run accepts, a tool in its long form and a
    subagent at `*` -- a check reading every name as unknown would report each.
    """
    _name_tools(
        cfg,
        {
            "builtin_tools": "[read_file, task]",
            "tools": "[word_tally, kit.py::line_count]",
            "subagents": "[reviewer]",
        },
        '["*"]',
    )

    assert main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unresolved_tools"] == {}


@pytest.mark.parametrize("form", ["text", "json"])
def test_the_operator_still_sees_all_of_it(form, cfg, at_the_command_line, capsys):
    """The control both rules above need: the filtering is the caller's view, not the
    listing losing the ability to say who reaches what.
    """
    _workspace(cfg, NARROW, A_ONLY)
    _subagents_out_of_reach(cfg)

    assert main(["list"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "hidden" in printed
    assert "narrow" in printed
    assert [one for one in OUT_OF_REACH if one not in printed] == []
    # The tools half of the same control, so the rules below are not passing on a
    # listing that shows the operator nothing either.
    assert "line_count" in printed
    assert "secret_export" in printed


#: An agent that stops on one tool of each kind: a built-in, which has no audience and so
#: is shown to everybody, and `line_count`, whose own lock keeps it from `B`.
GATED = """name: gatekeeper
description: An agent.
source_ids: [A, B]
tools: [line_count]
interrupt_on: [execute, line_count]
system_prompt: |
  You do the task.
"""


def test_the_listing_names_what_an_agent_stops_on(cfg, at_the_command_line, capsys):
    """`interrupt_on` reached no listing at all: an operator could read every tool an
    agent holds and not learn that a person is asked before two of them run -- and a gate
    is the one line of an agent file that changes what happens rather than what is held.
    """
    _workspace(cfg, GATED)

    assert main(["list"]) == 0

    assert "      interrupt_on: execute, line_count" in capsys.readouterr().out


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_names_no_gated_tool_out_of_reach(
    form, cfg, at_the_command_line, capsys
):
    """A gate names a tool, so the line filters as the tools line does: `line_count`'s own
    lock keeps it from `B`, and naming it under an agent they *can* open is the leak the
    per-agent maps here have had before.

    The built-in beside it is the control, and it is read off the gate line rather than
    searched for in the output: `execute` is in the built-in section whatever this filter
    does, so a bare `"execute" in printed` is a check that cannot fail.
    """
    _workspace(cfg, GATED)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "line_count" not in printed, "a gate named a tool this caller's lock shuts out"
    kept = (
        json.loads(printed)["agent_gates"]["gatekeeper"]
        if form == "json"
        else [one.split(": ", 1)[1] for one in printed.splitlines() if "interrupt_on:" in one]
    )
    assert kept == ["execute"], f"the {form} form dropped the gate it should still name"


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_carries_no_tool_out_of_reach(form, cfg, at_the_command_line, capsys):
    """Neither the tool whose own lock keeps it from this caller, nor the one
    only an agent they cannot open offers, nor the file either lives in.

    Measured through the real command before this existed: the filter standing here
    looked up audiences under a `tools` key nothing builds, so every tool matched at
    `*` -- replacing the whole call with the unfiltered tuple left all 2,167 tests
    passing.
    """
    _workspace(cfg, NARROW, A_ONLY)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "line_count" not in printed, "its own lock keeps this one from B"
    assert "secret_export" not in printed, "and only `hidden` offers this one"
    assert "narrow" in printed, "and it should still show the agent they can open"


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_keeps_a_tool_shared_with_them(form, cfg, at_the_command_line, capsys):
    """The control the rule above needs, and the one a filter that drops everything
    would fail: `wide` shares `word_tally` with A and B alike.
    """
    _workspace(cfg, WIDE, A_ONLY)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "word_tally" in printed
    assert "secret_export" not in printed, "which only an agent out of their reach offers"


#: An agent out of B's reach offering every tool, `["*"]`. At run time its own line is
#: held by the caller never opening the agent; a listing has no such gate, and the
#: definition's line narrows no entry of a field written `["*"]`, so it filters to the
#: definitions the caller reaches before asking what those offer.
A_ONLY_STAR = """name: starred
description: An agent.
source_ids: [A]
tools: ["*"]
system_prompt: |
  You do the task.
"""

#: A reachable agent granting every tool, which offers the whole catalogue at the
#: definition's own audience.
GRANTS_EVERY_TOOL = """name: unfussy
description: An agent.
source_ids: [A, B]
tools: ["*"]
system_prompt: |
  You do the task.
"""

#: The same agent with no `tools:` line, which grants none.
NAMES_NO_TOOLS = GRANTS_EVERY_TOOL.replace('tools: ["*"]\n', "")


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_star_entry_out_of_reach_stays_out_of_reach(form, cfg, at_the_command_line, capsys):
    """A tool an unreachable agent offers by `["*"]` is still absent, because this caller
    does not reach that definition.

    Measured: reading the entries of every definition rather than only the reachable
    ones hands `secret_export` straight back, and nothing else in the suite notices --
    every listed field here is narrowed by its definition's own line, which covers for
    the missing filter.
    """
    _workspace(cfg, WIDE, A_ONLY_STAR)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "secret_export" not in printed
    assert "word_tally" in printed, "and the one `wide` shares with them is still there"


@pytest.mark.parametrize("form", ["text", "json"])
def test_an_agent_granting_every_tool_offers_the_whole_catalogue(
    form, cfg, at_the_command_line, capsys
):
    """A caller reaching an agent written `tools: ["*"]` is shown every offered tool.

    `"*"` is a string, so a walk that unions the entries of each field without stopping
    at it collects the character rather than the catalogue.
    """
    _workspace(cfg, GRANTS_EVERY_TOOL, A_ONLY)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "word_tally" in printed
    assert "secret_export" in printed, "the star offers this one too"


@pytest.mark.parametrize("form", ["text", "json"])
def test_an_agent_naming_no_tools_offers_none(form, cfg, at_the_command_line, capsys):
    """A listing reading an absent `tools:` as the whole catalogue would show a caller
    tools that no agent they reach holds. The test above is the control.
    """
    _workspace(cfg, NAMES_NO_TOOLS, A_ONLY)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "word_tally" not in printed
    assert "secret_export" not in printed


#: The long form `formats.md` documents, `where::what`, which the shipped `surveyor` is
#: written with. The catalogue spells a uniquely-named tool short, so the two have to be
#: compared through the offering's own spelling -- and were compared as written.
LONG_FORM = """name: longhand
description: An agent.
source_ids: [A, B]
tools: [kit.py::line_count]
system_prompt: |
  You do the task.
"""


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_grant_written_the_long_way_still_reaches_its_caller(
    form, cfg, at_the_command_line, capsys
):
    """Measured: an agent granting `kit.py::line_count` left a caller holding `A` with
    `tools: []` and a refusal reading `offered: (none)`, while the build handed them the
    tool. The whole catalogue was theirs to see unscoped, so only a scoped caller met it.

    `secret_export` beside it, because a comparison that matched everything would pass
    the first line.
    """
    _workspace(cfg, LONG_FORM)

    assert main(["list", "--as", "A"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "line_count" in printed, "the long form names a tool this caller reaches"
    assert "secret_export" not in printed, "and not the one no definition offers them"


# -- what each definition holds ------------------------------------------------------

#: Naming each grant, `word_tally` the long way, and handing work to two subagents.
LISTS_ITS_TOOLS = """name: lister
description: An agent.
source_ids: [A, B]
builtin_tools: [task, read_file, ls]
tools: [kit.py::word_tally, line_count]
subagents: [itemiser, taker]
system_prompt: |
  You do the task.
"""

#: `["*"]` on every grant.
GRANTS_EVERYTHING = """name: greedy
description: An agent.
source_ids: [A, B]
builtin_tools: ["*"]
tools: ["*"]
subagents: ["*"]
system_prompt: |
  You do the task.
"""

#: Neither tool line, which grants none.
HOLDS_NO_TOOLS = """name: bare
description: An agent.
source_ids: [A, B]
system_prompt: |
  You do the task.
"""

#: A subagent's own tool locked to `A`, as a tool file writes the lock.
SEALED = '''
from kingfisher import ToolSpec


def sealed(path: str) -> str:
    """Does a thing."""
    return ""


TOOLS = [ToolSpec(sealed, source_ids=["A"])]
'''


def _subagents_holding_tools(cfg) -> None:
    """One subagent naming its tools, one granted every tool, and a compiled one.

    `itemiser` names a catalogue tool locked to `A` and owns one locked to `A`, so a
    caller holding `B` has one of each kind to be kept from.
    """
    root = subagents_dir(cfg)
    _private(root / "itemiser" / "tools" / "tally.py", "tally")
    (root / "itemiser" / "tools" / "sealed.py").write_text(SEALED, encoding="utf-8")
    (root / "itemiser" / "itemiser.yaml").write_text(
        "name: itemiser\ndescription: A subagent.\nsource_ids: [A, B]\n"
        "builtin_tools: [read_file]\n"
        "tools: [word_tally, line_count, {name: tally, source: bundled},"
        " {name: sealed, source: bundled}]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    _private(root / "taker" / "tools" / "own_count.py", "own_count")
    (root / "taker" / "taker.yaml").write_text(
        'name: taker\ndescription: A subagent.\nsource_ids: [A, B]\nbuiltin_tools: ["*"]\n'
        'tools: ["*", {name: own_count, source: bundled}]\nsystem_prompt: |\n  Go.\n',
        encoding="utf-8",
    )
    (root / "assembled.py").write_text(
        "def build(model, tools):\n    return None\n\n\n"
        "SUBAGENTS = [{'name': 'assembled', 'description': 'A compiled one.', "
        "'build': build, 'tools': ['word_tally']}]\n",
        encoding="utf-8",
    )


def _grant_lines(printed: str, header: str) -> dict[str, str]:
    """The grant lines under the definition whose own line starts with `header`.

    Split on the colon alone, so a line printed with nothing after it is still read.
    """
    lines = printed.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith(header))
    held = {}
    for line in lines[at + 1 :]:
        if not line.startswith("      "):
            break
        label, said, names = line.strip().partition(":")
        if said and label in ("tools", "builtin tools", "subagents"):
            held[label] = names.strip()
    return held


def test_an_agent_lists_the_tools_it_holds_as_the_catalogue_spells_them(
    cfg, at_the_command_line, capsys
):
    """No line said which tools an agent holds, so "who can use `word_tally`?" meant
    opening every agent file -- and printed as written, `kit.py::word_tally` would read
    as a tool other than the catalogue's `word_tally`.
    """
    _workspace(cfg, LISTS_ITS_TOOLS)
    _subagents_holding_tools(cfg)

    assert main(["list"]) == 0

    assert _grant_lines(capsys.readouterr().out, "  lister — ") == {
        "tools": "line_count, word_tally",
        "builtin tools": "ls, read_file, task",
        "subagents": "itemiser, taker",
    }


def test_a_star_shows_itself_beside_what_it_covers_today(cfg, at_the_command_line, capsys):
    """Expanded alone, `["*"]` read as a fixed list, and a tool added tomorrow would
    reach the agent with nothing in the listing saying it would.
    """
    _workspace(cfg, GRANTS_EVERYTHING)
    _subagents_holding_tools(cfg)

    assert main(["list"]) == 0

    held = _grant_lines(capsys.readouterr().out, "  greedy — ")
    assert held["tools"] == "* — today line_count, secret_export, word_tally"
    assert held["subagents"] == "* — today assembled, itemiser, taker"
    assert held["builtin tools"].startswith("* — today ")
    assert "read_file" in held["builtin tools"]


def test_an_agent_granting_no_tools_prints_no_tool_lines(cfg, at_the_command_line, capsys):
    """A line printed for a field left out would read as a grant, of the empty list or
    of everything, where leaving it out grants none.
    """
    _workspace(cfg, HOLDS_NO_TOOLS)

    assert main(["list"]) == 0

    assert _grant_lines(capsys.readouterr().out, "  bare — ") == {}


def test_a_subagent_lists_its_own_grants_and_leaves_its_star_to_its_caller(
    cfg, at_the_command_line, capsys
):
    """A subagent's star expanded to the catalogue would name tools it never gets past
    the agent calling it, and its own tools printed bare would read as the catalogue's.
    """
    _workspace(cfg, LISTS_ITS_TOOLS)
    _subagents_holding_tools(cfg)

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert _grant_lines(printed, "  itemiser ") == {
        "tools": "itemiser::sealed, itemiser::tally, line_count, word_tally",
        "builtin tools": "read_file",
    }
    assert _grant_lines(printed, "  taker ") == {
        "tools": "* — whatever the calling agent holds, plus taker::own_count",
        "builtin tools": "* — whatever the calling agent holds",
    }
    # Handed no built-in, so no line says it holds one.
    assert _grant_lines(printed, "  assembled ") == {"tools": "word_tally"}


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_drops_a_locked_tool_from_the_definition_granting_it(
    form, cfg, at_the_command_line, capsys
):
    """The `tools` section kept `line_count` and `sealed` from a caller their locks shut
    out, and a grant line read straight off each file would hand both back.

    The caller holding `A` is the control, and `word_tally` beside it the half a filter
    dropping everything would fail.
    """
    _workspace(cfg, LISTS_ITS_TOOLS, GRANTS_EVERYTHING)
    _subagents_holding_tools(cfg)
    as_json = ["--json"] if form == "json" else []

    seen = {}
    for caller in ("A", "B"):
        assert main(["list", "--as", caller, *as_json]) == 0
        printed = capsys.readouterr().out
        if form == "json":
            document = json.loads(printed)
            seen[caller] = {
                "lister": ", ".join(document["agent_tools"]["lister"]["names"]),
                "greedy": ", ".join(document["agent_tools"]["greedy"]["names"]),
                "itemiser": ", ".join(document["subagent_tools"]["itemiser"]["names"]),
            }
        else:
            seen[caller] = {
                "lister": _grant_lines(printed, "  lister — ")["tools"],
                "greedy": _grant_lines(printed, "  greedy — ")["tools"].removeprefix(
                    "* — today "
                ),
                "itemiser": _grant_lines(printed, "  itemiser ")["tools"],
            }

    assert seen["B"] == {
        "lister": "word_tally",
        "greedy": "secret_export, word_tally",
        "itemiser": "itemiser::tally, word_tally",
    }
    assert seen["A"] == {
        "lister": "line_count, word_tally",
        "greedy": "line_count, secret_export, word_tally",
        "itemiser": "itemiser::sealed, itemiser::tally, line_count, word_tally",
    }


def test_the_json_carries_each_grant_as_a_star_beside_its_names(
    cfg, at_the_command_line, capsys
):
    """A bare list cannot say `["*"]`, so a script read an agent granted every subagent as
    one naming today's, and `--json` carried no tool grant at all.
    """
    _workspace(cfg, LISTS_ITS_TOOLS, GRANTS_EVERYTHING, HOLDS_NO_TOOLS)
    _subagents_holding_tools(cfg)

    assert main(["list", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["agent_subagents"]["greedy"] == {
        "every": True,
        "names": ["assembled", "itemiser", "taker"],
    }
    assert document["agent_subagents"]["lister"] == {"every": False, "names": ["itemiser", "taker"]}
    assert document["agent_subagents"]["bare"] == {"every": False, "names": []}
    assert document["agent_tools"]["greedy"] == {
        "every": True,
        "names": ["line_count", "secret_export", "word_tally"],
    }
    assert document["agent_tools"]["bare"] == {"every": False, "names": []}
    assert document["agent_builtin_tools"]["lister"] == {
        "every": False,
        "names": ["ls", "read_file", "task"],
    }
    assert document["agent_builtin_tools"]["bare"] == {"every": False, "names": []}
    # A subagent's star names only what it holds besides, since the rest is its caller's.
    assert document["subagent_tools"]["taker"] == {"every": True, "names": ["taker::own_count"]}
    assert document["subagent_builtin_tools"]["taker"] == {"every": True, "names": []}
    assert document["subagent_builtin_tools"]["assembled"] == {"every": False, "names": []}


#: An agent that grants one tool to both callers and hands work to `helper`. Both
#: halves are load-bearing: the grant makes the answer non-empty, and naming the
#: subagent is what puts its declarations in front of the walk at all.
WIDE_AND_HANDS_WORK_ON = """name: wide
description: An agent.
source_ids: [A, B]
tools: [word_tally]
builtin_tools: ["*"]
subagents: [helper]
system_prompt: |
  You do the task.
"""


#: A subagent whose own line admits this caller, declaring a tool no agent they reach
#: grants. Its own `source_ids:` makes it reachable on its own terms and that is not
#: enough: a subagent is never a widener, so the name is one the caller gets by no path.
DECLARES_MORE_THAN_IT_CAN_HOLD = """name: helper
description: A subagent.
source_ids: [A, B]
tools: [secret_export]
system_prompt: |
  You do the part you are given.
"""


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_tool_only_a_subagent_declares_is_not_offered_to_that_caller(
    form, cfg, at_the_command_line, capsys
):
    """`ceiling` narrows a subagent's tools by what the agent that reached it holds, so
    an offered tool no reachable agent grants is one no path reaches.

    This read the other way round for a release, and said so: the walk unioned what
    every reached definition *declared*, and offered `secret_export` to `B` while a run
    as `B` handed its subagent nothing -- measured, through the real build. The promise
    is "an asset a caller's source ids do not reach"; declarations answer a different
    sentence. `word_tally` is asserted beside it because the agent grants that one to
    `B`, so a filter that offered nothing would pass the first line alone.

    The agent has to *name* the subagent, which the first version of this fixture
    forgot: unnamed, the subagent is out of reach for a different reason, the union
    this rules out contributes nothing either way, and unioning the subagents back in
    left the test green. Found by mutating it.
    """
    _workspace(cfg, WIDE_AND_HANDS_WORK_ON)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        DECLARES_MORE_THAN_IT_CAN_HOLD, encoding="utf-8"
    )

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "secret_export" not in printed, "no agent B reaches grants it"
    assert "word_tally" in printed, "and `wide` does, so this is not an empty answer"


#: An agent that hands work to one subagent, and the two it could have named. Both
#: lines admit both callers; only `helper` is named. Nothing names `kept_apart`, so no request
#: can wire it -- a request may narrow what an agent declares and never add to it, which
#: `Capabilities.subagents` says in as many words.
NAMES_ONE_SUBAGENT = """name: boss
description: An agent.
source_ids: [A, B]
builtin_tools: ["*"]
tools: ["*"]
subagents: [helper]
system_prompt: |
  You do the task.
"""

PLAIN_SUBAGENT = """name: {name}
description: A subagent.
source_ids: [A, B]
system_prompt: |
  You do the part you are given.
"""


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_subagent_no_agent_in_reach_activates_is_not_listed(
    form, cfg, at_the_command_line, capsys
):
    """A subagent reaches a run only where an agent names it, which `activated` records
    -- so its own `source_ids:` admitting a caller is necessary and not sufficient.

    Filtered by that line alone, `kept_apart` was listed to `B` while a request naming
    it wired nothing: measured through the real build, both directions. `helper` is
    asserted beside it, because a filter answering nothing would pass the first line.
    """
    _workspace(cfg, NAMES_ONE_SUBAGENT)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name in ("helper", "kept_apart"):
        (subagents_dir(cfg) / f"{name}.yaml").write_text(
            PLAIN_SUBAGENT.format(name=name), encoding="utf-8"
        )

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "kept_apart" not in printed, "no agent B reaches hands it work"
    assert "helper" in printed, "and `boss` names this one, so this is not an empty answer"


def test_the_operator_is_still_shown_the_subagent_nobody_activates(
    cfg, at_the_command_line, capsys
):
    """The other half of the rule above, and the reason it reads `held`: an unscoped
    listing is the operator's, and a definition no agent names is exactly what they
    are looking at a listing to find.
    """
    _workspace(cfg, NAMES_ONE_SUBAGENT)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    for name in ("helper", "kept_apart"):
        (subagents_dir(cfg) / f"{name}.yaml").write_text(
            PLAIN_SUBAGENT.format(name=name), encoding="utf-8"
        )

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "kept_apart" in printed and "helper" in printed


#: The same agent for the skills half: told about one skill, and naming the subagent
#: whose own `skills:` names another.
SHARES_SKILL_AND_HANDS_WORK_ON = """name: sharer
description: An agent.
source_ids: [A, B]
skills: [routine]
builtin_tools: ["*"]
tools: ["*"]
subagents: [helper]
system_prompt: |
  You do the task.
"""


#: A subagent declaring a skill the agent that reaches it is not told about. The skills
#: half of the same rule: `subagent_skills` narrows by what the run activated, so this
#: one is inert, and a listing that counted it named a skill no path reaches.
#:
#: `spare`, and that is the whole of what makes the rule below able to fail. It named
#: `routine` first -- the skill the agent already grants -- so "a skill only a subagent
#: declares" did not exist in the fixture: unioning the subagents back in added a name
#: the test asserts is *present*, and reverting the fix left the suite green on both
#: readers. Found by mutating it.
DECLARES_A_SKILL = """name: helper
description: A subagent.
source_ids: [A, B]
builtin_tools: [read_file]
skills: [spare]
system_prompt: |
  You do the part you are given.
"""


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_skill_only_a_subagent_declares_is_not_offered_to_that_caller(
    form, cfg, at_the_command_line, capsys, monkeypatch
):
    """Measured: `askill` offered to a caller whose only agent was told about `bskill`,
    while the build handed that caller's subagent `skills: None`.

    Two things make this able to fail, and the first version had neither: the agent has
    to *name* the subagent, or the declaration never reaches the walk; and the subagent
    has to declare a skill the agent does not, or the union it rules out contributes a
    name the rule already expects to see.
    """
    monkeypatch.setenv("KINGFISHER_SKILLS_ENABLED", "1")
    _workspace(cfg, SHARES_SKILL_AND_HANDS_WORK_ON, skills=("routine", "spare"))
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(DECLARES_A_SKILL, encoding="utf-8")

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "routine" in printed, "`sharer` is told about this one, and B reaches `sharer`"
    assert "spare" not in printed, "and nothing a caller reaches is told about this one"


#: An agent open to everyone naming a tool by a path it has moved from, where the tool's
#: own lock is for `A` alone. Two faults in one line, which is what makes it useful: the
#: tool is withheld from `B` and the *fault* was reported to them anyway.
MOVED_BEHIND_AN_AUDIENCE = """name: boss
description: An agent.
source_ids: [A, B]
tools: [moved/elsewhere.py::line_count]
system_prompt: |
  You do the task.
"""


#: An agent open to everyone naming a subagent the catalogue has no key for.
NAMES_A_GHOST = """name: boss
description: An agent.
source_ids: [A, B]
builtin_tools: ["*"]
tools: ["*"]
subagents: [ghost]
system_prompt: |
  You do the task.
"""


def test_the_operator_is_still_told_which_definition_names_nothing(
    cfg, at_the_command_line, capsys
):
    """The control: the fault is real and the unscoped listing is where it is fixed."""
    _workspace(cfg, NAMES_A_GHOST)

    assert main(["list"]) == 1

    printed = capsys.readouterr().out
    assert "ghost" in printed


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_fault_on_an_entry_out_of_reach_is_not_reported_to_that_caller(
    form, cfg, at_the_command_line, capsys
):
    """`_moved_tools` read the spec as written: its keys were kept to the caller's
    reach and the names inside were not, so a listing that correctly withheld
    `line_count` from `tools` named it three sections down -- and exited 1 over a row
    that caller cannot act on.

    `decisions.md` logs that shape twice already: the field that leaked is the one whose
    filter was somewhere else.
    """
    _workspace(cfg, MOVED_BEHIND_AN_AUDIENCE)

    code = main(["list", "--as", "B"] + (["--json"] if form == "json" else []))

    printed = capsys.readouterr().out
    assert "line_count" not in printed
    assert code == 0, "and the exit status follows the view"


def test_the_operator_is_still_told_which_definition_moved_it(cfg, at_the_command_line, capsys):
    """The control, and the half that matters most: the fault is real, and the unscoped
    listing is where somebody goes to fix it.
    """
    _workspace(cfg, MOVED_BEHIND_AN_AUDIENCE)

    assert main(["list"]) == 1

    printed = capsys.readouterr().out
    assert "tools named by a path they have moved from" in printed
    assert "line_count" in printed


#: A skill an agent shares with both callers, and one only an agent out of B's reach is
#: told about. A skill's own file has nowhere to write an audience, so those are the two
#: states one can be in -- there is no third where the skill itself restricts somebody.
SHARES_SKILL = """name: sharer
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file]
skills: [routine]
system_prompt: |
  You do the task.
"""

KEEPS_SKILL = """name: keeper
description: An agent.
source_ids: [A]
builtin_tools: [read_file]
skills: [procedure]
system_prompt: |
  You do the task.
"""


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_carries_no_skill_out_of_reach(form, cfg, at_the_command_line, capsys):
    """A skill only an agent out of this caller's reach is told about is absent, and the
    one shared with them is not.

    The same defect as the tools rule above and the same fix: a skill has no audience of
    its own either, so the only one it has is the entry in the definition offering it --
    and the listing was not reading those at all.
    """
    _workspace(cfg, SHARES_SKILL, KEEPS_SKILL, skills=("routine", "procedure"))

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "procedure" not in printed, "only `keeper` is told about this one"
    assert "routine" in printed, "and `sharer` shares this one with them"


def test_the_operator_sees_a_skill_no_caller_reaches(cfg, at_the_command_line, capsys):
    """The control for the skills half: the filtering is the caller's view, not the
    listing losing the ability to show a skill nobody but the operator can see.
    """
    _workspace(cfg, SHARES_SKILL, KEEPS_SKILL, skills=("routine", "procedure"))

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "procedure" in printed
    assert "routine" in printed


QUALIFIED_SKILL = """name: qualifier
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file]
skills: [catalogue::routine]
system_prompt: |
  You do the task.
"""


def test_a_grant_and_the_listing_may_spell_one_skill_differently(
    cfg, at_the_command_line, capsys
):
    """An agent granting `catalogue::routine` reaches the skill the listing calls
    `routine`, so a caller who reaches that agent is shown it.

    Measured: comparing the two spellings as written drops the skill from the caller's
    view although they reach it. The withheld report already carries a note about this
    and this filter is the second reader of the same grants, which is the whole reason
    both go through the registry's own identity rather than the string.
    """
    _workspace(cfg, QUALIFIED_SKILL, skills=("routine",))

    assert main(["list", "--as", "B"]) == 0

    assert "routine" in capsys.readouterr().out


def test_a_scoped_view_carries_no_policy(cfg, at_the_command_line):
    """The vocabulary, each definition's audience, and the report of what restricts
    nobody are the policy. A caller reading their own view is not the operator
    checking it, and the record is where that is decided now -- the text renderer used
    to decide it a second time and `as_json` not at all.
    """
    from kingfisher.core.inventory import inventory

    # A definition restricting nobody, so the operator's report has something in it
    # and the scoped view emptying it is a difference rather than two empty tuples.
    wide_open = "name: open\ndescription: An agent.\nsystem_prompt: |\n  Go.\n"
    _workspace(cfg, NARROW, A_ONLY, wide_open)
    scoped = inventory(cfg, source_ids=("B",))
    operator = inventory(cfg)

    assert scoped.vocabulary is None
    assert scoped.audiences == {}
    assert scoped.access_report.is_clean
    # Not vacuous: the operator's view carries all three.
    assert operator.vocabulary is not None
    assert operator.audiences
    assert not operator.access_report.is_clean


def test_a_broken_tools_catalogue_still_leaves_the_access_sections(
    cfg, at_the_command_line, capsys
):
    """The half the early return dropped. `render` used to stop after the skills and
    subagents sections when `tools/` would not walk, so an operator auditing a policy on
    a definitions folder with one unparseable `.py` got no access report -- and no sign that
    there was one to get. Skills and subagents had been added to that path; this had not.
    """
    _workspace(cfg, NARROW)
    from tests.conftest import tools_dir

    (tools_dir(cfg) / "broken.py").write_text("this is not python(\n", encoding="utf-8")

    assert main(["list"]) == 1

    printed = capsys.readouterr().out
    assert "cannot load" in printed, "and it still says the tools catalogue is the fault"
    assert "access — by definition" in printed
    assert "agent narrow  [A, B]" in printed


def test_the_operator_sees_audiences_per_definition(policied, capsys):
    """The unscoped listing is the operator's audit view."""
    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" in shown
    assert "agent narrow  [A, B]" in shown
    assert "tool line_count  [A]" in shown


#: Two agents telling one skill to different callers, since a skill is the one thing an
#: entry may lock.
TELLS_A = """name: narrow
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file]
skills:
  - name: routine
    source_ids: [A]
system_prompt: |
  You do the task.
"""

TELLS_BOTH = """name: wide
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file]
skills:
  - name: routine
    source_ids: [A, B]
system_prompt: |
  You do the task.
"""


def test_the_roll_up_shows_one_skill_at_two_audiences(cfg, at_the_command_line, capsys):
    """The case it exists for: a use quietly wider than its neighbour."""
    _workspace(cfg, TELLS_A, TELLS_BOTH, skills=("routine",))

    assert main(["list"]) == 0

    section = capsys.readouterr().out.split("by skill", 1)[1]
    assert "routine" in section
    assert "narrow  [A]" in section
    assert "wide  [A, B]" in section


def test_there_is_no_roll_up_by_tool(policied, capsys):
    """A per-use roll-up of tools would print each locked tool a second time, beside
    nothing that could differ from it.
    """
    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "tool line_count  [A]" in shown
    assert "by tool" not in shown


BOTH = """name: both
description: An agent.
source_ids: [{A, B}]
builtin_tools: [read_file]
skills:
  - name: routine
    source_ids: [A, B]
system_prompt: |
  You do the task.
"""


def test_an_inline_requirement_reads_as_the_set_it_is_written_as(cfg, at_the_command_line, capsys):
    """The listing prints what a reader would write in the file. It printed `A+B` when
    the file said `all_of`, which was a third spelling of the same idea and ambiguous
    besides -- a source id may legally contain a `+`.
    """
    _workspace(cfg, BOTH, skills=("routine",))

    assert main(["list"]) == 0

    assert "agent both  [{A, B}]" in capsys.readouterr().out


def test_a_conjunction_is_spelled_the_same_way_wherever_it_appears(
    cfg,
    at_the_command_line,
    capsys,
):
    """The by-definition view and the roll-up print the same audience, and a reader
    comparing the two should not have to translate.
    """
    _workspace(cfg, BOTH, skills=("routine",))

    shown = capsys.readouterr().out if main(["list"]) == 0 else ""
    before, after = shown.split("by skill", 1)
    assert "[{A, B}]" in before
    assert "both  [A, B]" in after


def test_a_conjunction_survives_the_json_round_trip(cfg, at_the_command_line):
    """`json` holds neither a set nor a tuple, so this is not a formality: an audience
    carrying a conjunction used to be unencodable outright.
    """
    import json

    from kingfisher import inventory
    from kingfisher.cli.listing import as_json

    _workspace(cfg, BOTH, skills=("routine",))

    document = json.loads(json.dumps(as_json(inventory(cfg))))

    # Nested, not the braces the file writes: a script should not have to parse a
    # set out of a string.
    assert document["audiences"]["agents"]["both"]["source_ids"] == [["A", "B"]]
    assert document["vocabulary"]["source_ids"] == {
        "A": ["agent both", "tool line_count"],
        "B": ["agent both"],
    }


NARROWED = """name: narrowed
description: An agent.
source_ids: [A, B]
builtin_tools: [read_file]
skills:
  - name: routine
    source_ids: [C]
system_prompt: |
  You do the task.
"""


def test_an_entry_narrowing_past_its_definition_is_reported(cfg, at_the_command_line, capsys):
    """It used to be refused."""
    _workspace(cfg, NARROWED, skills=("routine",))

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "narrows past this definition's own audience" in shown
    assert "agent narrowed: skill routine  [C]" in shown


def test_a_narrowed_entry_reaches_a_caller_holding_both(cfg, at_the_command_line):
    """The report is not the point -- this is."""
    from kingfisher.core.access.reach import reaches
    from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository

    _workspace(cfg, NARROWED, skills=("routine",))
    spec = LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["narrowed"]

    assert spec.declares(frozenset({"A", "C"})).skills == ("routine",)
    assert spec.declares(frozenset({"A"})).skills == ()
    # And the definition's own line still gates the agent itself: C alone opens
    # nothing, so there is no way to reach the skill by holding only C.
    assert not reaches(spec.source_ids, frozenset({"C"}))


def test_a_callers_view_carries_no_audiences(policied, capsys):
    """Who else reaches a thing is the operator's question, not a caller's."""
    assert main(["list", "--as", "A"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" not in shown


def test_a_callers_view_drops_an_agent_they_cannot_open(cfg, at_the_command_line, capsys):
    _workspace(cfg, NARROW)

    assert main(["list", "--as", "C"]) == 0

    assert "narrow" not in capsys.readouterr().out


def test_the_operator_still_sees_it(cfg, at_the_command_line, capsys):
    """So the assertion above is not passing because the agent vanished."""
    _workspace(cfg, NARROW)

    assert main(["list"]) == 0

    assert "narrow" in capsys.readouterr().out


def test_listing_names_a_definition_that_restricts_nobody(cfg, at_the_command_line, capsys):
    """Default-open, said where somebody will see it."""
    from tests.conftest import an_agent

    _workspace(cfg)
    an_agent(cfg, "open_to_all")

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "reachable by everyone" in printed
    assert "open_to_all" in printed


def test_naming_a_source_id_no_lock_uses_lists_only_what_is_unlocked(policied, capsys):
    """Refused, a caller's listing failed on which locks existed that week; accepted, it
    is the truth about `Q`: the locked agent is not theirs to see."""
    assert main(["list", "--as", "Q"]) == 0

    assert "narrow" not in capsys.readouterr().out


def test_naming_a_caller_on_a_workspace_with_no_lock_is_refused(
    cfg, at_the_command_line, capsys
):
    """The listing accepted any name where nothing narrows and printed the unscoped
    answer: `list --as marketing` showed the whole catalogue and exited zero, which
    reads as what marketing reaches. A turn naming the same caller was refused.

    The refusal is one door now, so this and the turn share it -- `held_by`, which is
    already the only place the shape of a caller's source ids is read.
    """
    from tests.conftest import an_agent

    an_agent(cfg, "plain")

    assert main(["list", "--as", "marketing"]) != 0

    said = capsys.readouterr().err
    assert "no definition here carries a lock" in said
    assert "marketing" in said, "and which name narrowed nothing"


def test_the_operators_own_view_is_not_refused_with_it(
    cfg, at_the_command_line, capsys
):
    """The control: a rule refusing every `--as` on a policy-free workspace would pass
    the one above, and `UNSCOPED` is what somebody writes to mean exactly this.
    """
    from tests.conftest import an_agent

    an_agent(cfg, "plain")

    assert main(["list", "--as", "UNSCOPED"]) == 0
    assert main(["list"]) == 0
    assert "plain" in capsys.readouterr().out


def test_no_vocabulary_means_no_access_section(cfg, at_the_command_line, capsys):
    from tests.conftest import an_agent

    an_agent(cfg, "plain")

    assert main(["list"]) == 0
    assert "access —" not in capsys.readouterr().out


def test_the_listing_names_each_source_id_in_use_beside_who_uses_it(
    cfg, at_the_command_line, capsys
):
    """What replaced refusing a misspelling at startup, so it is the one place a reader
    sees `analists` at all: a row of its own, under the spelling the others use."""
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", source_ids="[analists]")
    an_agent(cfg, "auditor", source_ids="[analysts]")
    an_agent(cfg, "reviewer", source_ids="[analysts]")

    assert main(["list"]) == 0, "a misspelling is no fault: nothing refuses it"

    in_use = capsys.readouterr().out.split("names in use", 1)[1]
    assert "  analists  agent analyst\n" in in_use
    assert "  analysts  agent auditor, agent reviewer\n" in in_use


def test_the_json_names_each_source_id_in_use_beside_who_uses_it(cfg, at_the_command_line):
    """The same answer for a script, which should not parse it out of the text."""
    import json

    from kingfisher import inventory
    from kingfisher.cli.listing import as_json
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", source_ids="[analists]")
    an_agent(cfg, "auditor", source_ids="[analysts]")

    document = json.loads(json.dumps(as_json(inventory(cfg))))

    assert document["vocabulary"] == {
        "source_ids": {"analists": ["agent analyst"], "analysts": ["agent auditor"]},
        "datazones": {},
    }


# -- every kind, rather than the ones somebody listed ------------------------

#: Why an error this record carries is not one of the kinds. A `*_error` in neither
#: this table nor the per-kind view is one `failed` can miss, which is how
#: `middlewares_error` came to be computed and read by nothing for a release.
ERRORS_OUTSIDE_THE_KINDS = {
    "bundles_error": "a bundle is one subagent's own folder, not a catalogue",
    "build_error": "a graph is built from every kind at once, so no one kind owns it",
}


def _errors_carried() -> tuple[str, ...]:
    from dataclasses import fields

    from kingfisher.core.inventory import Inventory

    return tuple(sorted(f.name for f in fields(Inventory) if f.name.endswith("_error")))


def test_the_per_kind_view_covers_every_kind_there_is():
    """`doctor` read three of the five kinds, then four, and the listing read the
    errors it happened to know about. The view is what those two walk now, so the
    thing that has to be total is the view.
    """
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
    from tests.conftest import an_inventory

    named = {kind.name for kind in an_inventory().by_kind()}

    assert named == set(DEFINITION_KINDS)


@pytest.mark.parametrize("field_name", _errors_carried())
def test_every_error_the_inventory_carries_makes_a_listing_non_zero(field_name):
    """Driven one error at a time, because that is how one goes missing: a kind gains
    an error, every other check keeps passing, and the deployment that breaks on it
    is the one nobody told.
    """
    from kingfisher.cli.listing import failed
    from tests.conftest import an_inventory

    broken = an_inventory(**{field_name: "something is wrong here"})

    assert failed(broken), f"{field_name} is carried and nothing reads it"


@pytest.mark.parametrize("field_name", _errors_carried())
def test_every_error_the_inventory_carries_is_said_in_the_human_listing(field_name):
    """The other half of the rule above, and the half that was missing. A non-zero exit
    tells a script something is wrong; only the text tells the person what.

    `middlewares_error` and `bundles_error` were both carried, both counted by `failed`,
    and printed by neither renderer -- so `kingfisher list` on a definitions folder with one
    unparseable `middlewares/*.py` exited 1 over a listing that looked fine. Every rule
    that proved those errors were carried asserted on `--json`, which is how.
    """
    from kingfisher.cli.listing import render
    from tests.conftest import an_inventory

    said = "cap.py: SyntaxError: '(' was never closed"
    printed = "\n".join(render(an_inventory(**{field_name: said})))

    assert said in printed, f"{field_name} makes the exit non-zero and tells nobody why"


def test_a_listing_with_nothing_wrong_says_none_of_that():
    """The control: the rule above passes for every field if the renderer prints the
    string unconditionally, or prints every error it can think of.
    """
    from kingfisher.cli.listing import render
    from tests.conftest import an_inventory

    printed = "\n".join(render(an_inventory()))

    assert "cannot load" not in printed
    assert "moved from" not in printed


#: What makes a listing non-zero besides a kind's error, each with a value that triggers
#: it and something the printed output must then say. `moved_tools` is the one that was
#: missing: an agent naming a tool by a path it has moved from loads, runs, and quietly
#: does not have the tool it was granted, which this record singles out as the worst of
#: these to find out about later.
TROUBLES_BESIDE_THE_ERRORS = {
    "moved_tools": ({"agent 'analyst'": ("csv_profile",)}, "csv_profile"),
    "miscounted_bundles": ({"redactor": "its folder holds two"}, "its folder holds two"),
    "unresolved_subagents": ({"agent 'analyst'": ("phantom",)}, "phantom"),
    "unresolved_tools": ({"agent 'analyst'": ("lokup",)}, "lokup"),
    "doubled_skills": (
        {"agent 'analyst'": "skills names 'audit' and 'catalogue::audit', which are one skill"},
        "which are one skill",
    ),
}

#: Why a field the inventory says it asked, by a `_checked` twin, is no trouble. Every
#: other one is something startup refuses, so it belongs in the table above.
CHECKED_BUT_NO_TROUBLE = {
    "builtin_tools": "what an agent may be granted; unread, the listing calls it unknown",
    "shadowed": "a bundle answering for a catalogue tool, acceptable while it is shown",
}


def test_every_trouble_failed_reads_has_a_rule_here():
    """Read off `failed`'s own source rather than remembered. The list of what makes a
    listing non-zero and the list of what it prints are two lists of one set, and they
    had already drifted by two entries.
    """
    import inspect
    from dataclasses import fields

    from kingfisher.cli.listing import failed
    from kingfisher.core.inventory import Inventory

    source = inspect.getsource(failed)
    named = {f.name for f in fields(Inventory) if f"found.{f.name}" in source}

    assert named <= set(TROUBLES_BESIDE_THE_ERRORS) | set(_errors_carried())
    assert set(TROUBLES_BESIDE_THE_ERRORS) <= named, "a trouble that is gone from `failed`"


def test_every_refusal_the_inventory_asks_is_a_trouble_here():
    """The rule above starts from `failed`, so a refusal `failed` never read passed it:
    `doubled_skills` was asked, put in `--json` and failed by `doctor`, and `list` exited 0
    over a skill startup refuses, printing its audience as if it were the only entry.
    """
    from dataclasses import fields

    from kingfisher.core.inventory import Inventory

    asked = {
        f.name.removesuffix("_checked") for f in fields(Inventory) if f.name.endswith("_checked")
    }

    assert asked - set(CHECKED_BUT_NO_TROUBLE) == set(TROUBLES_BESIDE_THE_ERRORS)


@pytest.mark.parametrize("field_name", sorted(TROUBLES_BESIDE_THE_ERRORS))
def test_every_trouble_besides_an_error_is_said_in_the_human_listing(field_name):
    """The same rule for the two that are not a kind's error. Driven one at a time,
    because that is how one goes missing: a trouble is added to `failed`, the exit code
    rule keeps passing, and the person looking at the broken definitions is told nothing.
    """
    from types import MappingProxyType

    from kingfisher.cli.listing import render
    from tests.conftest import an_inventory

    value, said = TROUBLES_BESIDE_THE_ERRORS[field_name]
    # A subagent for it to hang under: `miscounted_bundles` prints beneath its owner,
    # so a record with no subagents cannot show it however the renderer is written.
    broken = an_inventory(
        subagents=MappingProxyType({"redactor": "Redacts."}), **{field_name: value}
    )

    assert said in "\n".join(render(broken)), f"{field_name} tells nobody why"


def test_an_inventory_with_nothing_wrong_is_not_reported_as_broken():
    """The control: the rule above passes for every field if `failed` is always true."""
    from kingfisher.cli.listing import failed
    from tests.conftest import an_inventory

    assert not failed(an_inventory())


def test_every_error_is_a_kind_or_says_why_it_is_not():
    """The other half: an error reaching `failed` through the table below rather than
    through a kind has to say what it is instead.
    """
    from tests.conftest import an_inventory

    of_kinds = {f"{kind.name}_error" for kind in an_inventory().by_kind()}
    carried = set(_errors_carried())

    assert carried <= of_kinds | set(ERRORS_OUTSIDE_THE_KINDS)
    assert set(ERRORS_OUTSIDE_THE_KINDS) <= carried, "an exception for an error that is gone"
