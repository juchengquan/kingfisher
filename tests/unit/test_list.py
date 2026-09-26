"""`kingfisher list`, and the rule it is held to."""

from __future__ import annotations

import pytest

from kingfisher.presentation.cli.__main__ import main
from tests.conftest import subagents_dir


def test_listing_reports_a_workspace_that_will_not_load(cfg, at_the_command_line, capsys):
    """Non-zero, because a listing gets read by scripts."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list"]) == 1

    assert "cannot load" in capsys.readouterr().out


def test_a_workspace_with_no_agents_says_so_and_says_what_to_do(at_the_command_line, capsys):
    """The empty listing for agents, which the ones for skills and subagents had
    and this did not -- a mutation removing it went unnoticed.

    It carries the seed hint because a workspace with no agents cannot serve a
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
    from kingfisher.presentation.cli.listing import render
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
    from kingfisher.presentation.cli.listing import as_json

    document = as_json(inventory(cfg))

    assert set(document) == {field.name for field in fields(inventory(cfg))}


def test_the_json_document_survives_a_round_trip(cfg):
    """It is only worth having if `json.dumps` accepts it."""
    import json

    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import as_json

    _seed_something(cfg)

    document = json.loads(json.dumps(as_json(inventory(cfg))))

    # Under `origins` now, with every other place beside it. `Path` is what
    # made this test worth having and the record still holds them, one layer in.
    assert isinstance(document["origins"]["workspace"], str)
    assert document["origins"]["skills"] == {
        "kind": "default",
        "path": str(cfg.skills_dir),
    }
    assert "probe-agent" in document["subagents"]
    assert document["tools_error"] is None


def test_the_header_names_every_catalogue_including_tools(cfg):
    """The regression this record was built to make impossible."""
    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import render

    header = list(render(inventory(cfg)))[:10]

    assert [line.split(" :")[0].strip() for line in header] == [
        "workspace", "agents", "middlewares", "skills", "subagents", "tools",
        "models", "source_ids", "seed", "sessions",
    ]


def test_the_json_carries_the_kind_and_a_path_a_script_can_open(cfg, tmp_path):
    """Two things the header deliberately does not do."""
    import json
    from dataclasses import replace

    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import as_json

    document = json.loads(json.dumps(as_json(inventory(replace(cfg, assets=tmp_path)))))
    origins = document["origins"]

    assert origins["skills"]["path"].startswith("/"), "not the ./name the header prints"
    assert origins["seed"] == {"kind": "relocated", "path": str(tmp_path)}
    assert origins["source_ids"]["kind"] == "unset"


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


# -- a delegate the workspace built itself ----------------------------------


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
    directory = cfg.workspace / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "researcher.py").write_text(COMPILED_MODULE, encoding="utf-8")
    (directory / "reviewer.yaml").write_text(PROMPTED_DEFINITION, encoding="utf-8")


def test_the_listing_marks_a_compiled_delegate(cfg):
    """Marked because the rest of the listing means something different for it, and
    nothing else in the output would say so.
    """
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    _subagent_catalogue(cfg)
    lines = list(render(inventory(cfg)))
    named = {name: [one for one in lines if one.strip().startswith(name)]
             for name in ("researcher", "reviewer")}

    assert "[compiled]" in named["researcher"][0]
    assert "[compiled]" not in named["reviewer"][0]


def test_the_listing_says_what_a_compiled_delegate_costs(cfg):
    """The assumption a reader would otherwise make."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    _subagent_catalogue(cfg)
    printed = "\n".join(render(inventory(cfg)))

    assert "--tools" in printed
    assert "do not restrict what it can call" in printed


def test_a_workspace_with_no_compiled_delegate_says_nothing_about_them(cfg):
    """The note is about a minority, so it stays absent for everyone else -- a caveat
    printed to every reader is one none of them reads.
    """
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    directory = cfg.workspace / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reviewer.yaml").write_text(PROMPTED_DEFINITION, encoding="utf-8")

    printed = "\n".join(render(inventory(cfg)))

    assert "compiled" not in printed


def test_a_compiled_delegate_is_not_annotated_with_the_file_you_can_already_see(cfg):
    """`_from` stays silent when the name already tells you the file."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

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
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import as_json

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
    loads and runs without it. Both are a workspace that does not mean what it says.
    """
    import json

    from kingfisher import seed

    seed(cfg, shipped)
    (cfg.catalogue_roots["tools"] / "csv_profile").rename(
        cfg.catalogue_roots["tools"] / "analysis"
    )

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["moved_tools"]


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
        "name: helper\ndescription: A delegate.\nsystem_prompt: |\n  x\n", encoding="utf-8"
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

TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''


NARROW = """name: narrow
description: An agent.
source_ids: [A, B]
tools:
  - name: line_count
    source_ids: [A]
system_prompt: |
  You do the task.
"""


WIDE = """name: wide
description: An agent.
source_ids: [A, B]
tools:
  - name: line_count
    source_ids: [A, B]
system_prompt: |
  You do the task.
"""


def _workspace(cfg, *agents: str, vocabulary: str = "source_ids: [A, B]\n"):
    """A workspace with one tool and whichever agents the test names.

    Fills the workspace and nothing else: pointing the command at it is
    `at_the_command_line`'s job, which every caller here takes.
    """
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for document in agents:
        name = document.split("name: ", 1)[1].split("\n", 1)[0]
        (directory / f"{name}.yaml").write_text(document, encoding="utf-8")
    (cfg.workspace / "source_ids.yaml").write_text(vocabulary, encoding="utf-8")
    return cfg


@pytest.fixture
def policied(cfg, at_the_command_line):
    return _workspace(cfg, NARROW)


#: An agent only `A` reaches, beside the `A, B` one the fixtures use, so a caller
#: holding `B` has something to be kept from.
A_ONLY = """name: hidden
description: An agent.
source_ids: [A]
system_prompt: |
  You do the task.
"""


@pytest.mark.parametrize("form", ["text", "json"])
def test_a_scoped_listing_carries_nothing_out_of_reach(form, cfg, at_the_command_line, capsys):
    """Both forms, and asserted over the whole output rather than field by field.

    Measured through the real command before this existed: the text form hid the
    unreachable agent and `--json` carried its name, the file it came from, its
    delegate chain, every definition's audience and the vocabulary itself -- so an
    operator checking a policy got a different answer depending on the flag, and the
    flag that answered wrongly is the one a script reads.

    Searching the rendered output for the name, rather than naming the fields that
    leaked, is deliberate: the four that did were not a closed set, and a field added
    later would leak the same way past a field-by-field assertion.
    """
    _workspace(cfg, NARROW, A_ONLY)

    assert main(["list", "--as", "B"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "hidden" not in printed, f"the {form} form named an agent this caller cannot open"
    assert "narrow" in printed, "and it should still show the one they can"


@pytest.mark.parametrize("form", ["text", "json"])
def test_the_operator_still_sees_all_of_it(form, cfg, at_the_command_line, capsys):
    """The control both rules above need: the filtering is the caller's view, not the
    listing losing the ability to say who reaches what.
    """
    _workspace(cfg, NARROW, A_ONLY)

    assert main(["list"] + (["--json"] if form == "json" else [])) == 0

    printed = capsys.readouterr().out
    assert "hidden" in printed
    assert "narrow" in printed


def test_a_scoped_view_carries_no_policy(cfg, at_the_command_line):
    """The vocabulary, each definition's audience, and the report of what restricts
    nobody are the policy. A caller reading their own view is not the operator
    checking it, and the record is where that is decided now -- the text renderer used
    to decide it a second time and `as_json` not at all.
    """
    from dataclasses import replace

    import yaml

    from kingfisher.application.inventory import inventory
    from kingfisher.domain.access import parse

    # A definition restricting nobody, so the operator's report has something in it
    # and the scoped view emptying it is a difference rather than two empty tuples.
    wide_open = "name: open\ndescription: An agent.\nsystem_prompt: |\n  Go.\n"
    _workspace(cfg, NARROW, A_ONLY, wide_open)
    # The vocabulary on the `Config`, not only in the file: read directly, `inventory`
    # takes the policy from `cfg.access`, and a config without one is not a scoped view
    # at all -- which is how the first version of this test passed against the defect.
    policied = replace(
        cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="source_ids.yaml")
    )
    scoped = inventory(policied, source_ids=("B",))
    operator = inventory(policied)

    assert scoped.access is None
    assert scoped.audiences == {}
    assert scoped.access_report.is_clean
    # Not vacuous: the operator's view carries all three.
    assert operator.access is not None
    assert operator.audiences
    assert not operator.access_report.is_clean


def test_the_operator_sees_audiences_per_definition(policied, capsys):
    """The unscoped listing is the operator's audit view."""
    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" in shown
    assert "agent narrow  [A, B]" in shown
    assert "tool line_count  [A]" in shown


def test_the_operator_sees_a_roll_up_by_asset(policied, capsys):
    """The question the files can no longer answer on their own."""
    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "by tool" in shown
    assert "line_count" in shown.split("by tool", 1)[1]


def test_the_roll_up_shows_one_tool_at_two_audiences(cfg, at_the_command_line, capsys):
    """The case it exists for: a call site quietly wider than its neighbour."""
    _workspace(cfg, NARROW, WIDE)

    assert main(["list"]) == 0

    section = capsys.readouterr().out.split("by tool", 1)[1]
    assert "narrow  [A]" in section
    assert "wide  [A, B]" in section


BOTH = """name: both
description: An agent.
source_ids: [{A, B}]
tools:
  - name: line_count
    source_ids: [A, B]
system_prompt: |
  You do the task.
"""


NAMED = """name: named
description: An agent.
source_ids: [ab]
system_prompt: |
  You do the task.
"""


def test_an_inline_requirement_reads_as_the_set_it_is_written_as(cfg, at_the_command_line, capsys):
    """The listing prints what a reader would write in the file. It printed `A+B` when
    the file said `all_of`, which was a third spelling of the same idea and ambiguous
    besides -- a source id may legally contain a `+`.
    """
    _workspace(cfg, BOTH)

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
    _workspace(cfg, BOTH)

    shown = capsys.readouterr().out if main(["list"]) == 0 else ""
    before, after = shown.split("by tool", 1)
    assert "[{A, B}]" in before
    assert "both  [A, B]" in after


def test_a_named_compound_says_what_it_requires(cfg, at_the_command_line, capsys):
    """A name tells a reader nothing on the line it appears on, and every line it
    appears on needs it -- so it is said once, above.
    """
    _workspace(cfg, NAMED,
        vocabulary="source_ids:\n  A:\n  B:\n  ab: {A, B}\n",
    )

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "source ids that require others" in shown
    assert "ab = {A, B}" in shown
    assert "agent named  [ab]" in shown


def test_a_vocabulary_with_no_compounds_gets_no_such_section(policied, capsys):
    """It exists to make audiences readable, so it earns its lines or it has none."""
    assert main(["list"]) == 0

    assert "source ids that require others" not in capsys.readouterr().out


def test_a_conjunction_survives_the_json_round_trip(cfg, at_the_command_line):
    """`json` holds neither a set nor a tuple, so this is not a formality: an audience
    carrying a conjunction used to be unencodable outright.
    """
    import json

    from kingfisher import config_from_env, inventory
    from kingfisher.presentation.cli.listing import as_json

    _workspace(cfg, BOTH, NAMED,
        vocabulary="source_ids:\n  A:\n  B:\n  ab: {A, B}\n",
    )

    # From the environment, not the fixture: the vocabulary is a file the
    # helper just wrote, and the fixture config predates it.
    document = json.loads(json.dumps(as_json(inventory(config_from_env()))))

    # Nested, not the braces the file writes: a script should not have to parse a
    # set out of a string.
    assert document["audiences"]["agents"]["both"]["source_ids"] == [["A", "B"]]
    assert document["access"]["requires"]["ab"] == ["A", "B"]
    assert document["access"]["names"]["A"] == ["A"]


NARROWED = """name: narrowed
description: An agent.
source_ids: [A, B]
tools:
  - name: line_count
    source_ids: [C]
system_prompt: |
  You do the task.
"""


def test_an_entry_narrowing_past_its_definition_is_reported(cfg, at_the_command_line, capsys):
    """It used to be refused."""
    _workspace(cfg, NARROWED, vocabulary="source_ids: [A, B, C]\n")

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "narrows past this definition's own audience" in shown
    assert "agent narrowed: tool line_count  [C]" in shown


def test_a_narrowed_entry_reaches_a_caller_holding_both(cfg, at_the_command_line):
    """The report is not the point -- this is."""
    from kingfisher import config_from_env
    from kingfisher.domain.access import reaches
    from kingfisher.kinds.agents.catalogue import LocalAgentRepository

    _workspace(cfg, NARROWED, vocabulary="source_ids: [A, B, C]\n")
    reach = config_from_env().access
    assert reach is not None
    spec = LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["narrowed"]

    assert spec.declares(reach.expand(["A", "C"])).tools == ("line_count",)
    assert spec.declares(reach.expand(["A"])).tools == ()
    # And the definition's own line still gates the agent itself: C alone opens
    # nothing, so there is no way to reach the tool by holding only C.
    assert not reaches(spec.source_ids, reach.expand(["C"]))


def test_a_callers_view_carries_no_audiences(policied, capsys):
    """Who else reaches a thing is the operator's question, not a caller's."""
    assert main(["list", "--as", "A"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" not in shown
    assert "by tool" not in shown


def test_a_callers_view_drops_an_agent_they_cannot_open(cfg, at_the_command_line, capsys):
    _workspace(cfg, NARROW, vocabulary="source_ids: [A, B, C]\n")

    assert main(["list", "--as", "C"]) == 0

    assert "narrow" not in capsys.readouterr().out


def test_the_operator_still_sees_it(cfg, at_the_command_line, capsys):
    """So the assertion above is not passing because the agent vanished."""
    _workspace(cfg, NARROW, vocabulary="source_ids: [A, B, C]\n")

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


def test_naming_a_source_id_that_does_not_exist_is_refused(policied, capsys):
    assert main(["list", "--as", "Q"]) != 0

    assert "unknown source id" in capsys.readouterr().err


def test_no_vocabulary_means_no_access_section(cfg, at_the_command_line, capsys):
    from tests.conftest import an_agent

    an_agent(cfg, "plain")

    assert main(["list"]) == 0
    assert "access —" not in capsys.readouterr().out


def test_the_listing_reports_a_definition_naming_an_undeclared_source_id(
    cfg,
    at_the_command_line,
    capsys,
):
    """The listing is where somebody diagnosing this looks, and it goes through
    `inventory` rather than `Kingfisher` -- so the check has to be in both or the one
    place a reader would check shows a broken definition as ordinary.
    """
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", source_ids="[analists]")
    (cfg.workspace / "source_ids.yaml").write_text("source_ids: [analysts]\n", encoding="utf-8")

    assert main(["list"]) == 1, "a workspace that will not build is a non-zero listing"

    printed = capsys.readouterr().out
    assert "analists" in printed
    assert "analysts" in printed, "and the spelling that would have worked"


def test_the_listing_is_clean_when_every_source_id_is_declared(cfg, at_the_command_line, capsys):
    """So the rule above is not passing because every listing says that."""
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", source_ids="[analysts]")
    (cfg.workspace / "source_ids.yaml").write_text("source_ids: [analysts]\n", encoding="utf-8")

    assert main(["list"]) == 0
    assert "cannot load" not in capsys.readouterr().out


# -- every kind, rather than the ones somebody listed ------------------------

#: Why an error this record carries is not one of the kinds. A `*_error` in neither
#: this table nor the per-kind view is one `failed` can miss, which is how
#: `middlewares_error` came to be computed and read by nothing for a release.
ERRORS_OUTSIDE_THE_KINDS = {
    "bundles_error": "a bundle is one delegate's own folder, not a catalogue",
}


def _errors_carried() -> tuple[str, ...]:
    from dataclasses import fields

    from kingfisher.application.inventory import Inventory

    return tuple(sorted(f.name for f in fields(Inventory) if f.name.endswith("_error")))


def test_the_per_kind_view_covers_every_kind_there_is():
    """`doctor` read three of the five kinds, then four, and the listing read the
    errors it happened to know about. The view is what those two walk now, so the
    thing that has to be total is the view.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS
    from tests.conftest import an_inventory

    named = {kind.name for kind in an_inventory().by_kind()}

    assert named == set(DEFINITION_KINDS)


@pytest.mark.parametrize("field_name", _errors_carried())
def test_every_error_the_inventory_carries_makes_a_listing_non_zero(field_name):
    """Driven one error at a time, because that is how one goes missing: a kind gains
    an error, every other check keeps passing, and the deployment that breaks on it
    is the one nobody told.
    """
    from kingfisher.presentation.cli.listing import failed
    from tests.conftest import an_inventory

    broken = an_inventory(**{field_name: "something is wrong here"})

    assert failed(broken), f"{field_name} is carried and nothing reads it"


def test_an_inventory_with_nothing_wrong_is_not_reported_as_broken():
    """The control: the rule above passes for every field if `failed` is always true."""
    from kingfisher.presentation.cli.listing import failed
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
