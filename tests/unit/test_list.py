"""`kingfisher list`, and the rule it is held to."""

from __future__ import annotations

import pytest

from kingfisher.presentation.cli.__main__ import main
from tests.conftest import subagents_dir


def test_listing_reports_a_workspace_that_will_not_load(cfg, monkeypatch, capsys):
    """Non-zero, because a listing gets read by scripts."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")  # or the endpoint is dropped
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list"]) == 1

    assert "cannot load" in capsys.readouterr().out


def test_a_workspace_with_no_agents_says_so_and_says_what_to_do(cfg, monkeypatch, capsys):
    """The empty listing for agents, which the ones for skills and subagents had
    and this did not -- a mutation removing it went unnoticed.

    It carries the seed hint because a workspace with no agents cannot serve a
    request at all: every other emptiness here is survivable.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "(none)" in printed
    assert "a request must name one" in printed


def test_a_workspace_holding_an_agent_does_not_say_none(cfg, monkeypatch, capsys):
    """So the rule above is not passing on a listing that says it whatever it holds."""
    from tests.conftest import an_agent

    an_agent(cfg, "analyst")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

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


def _catalogue(cfg) -> object:
    """A minimal `models.yaml` beside the workspace, so `list` gets past config."""
    path = cfg.workspace / "models.yaml"
    path.write_text(
        "endpoints:\n  fake:\n    api: anthropic\n"
        "    base_url: http://127.0.0.1:9/never-called\n    key_env: FAKE_KEY\n"
        "default: fake-model\n"
        "models:\n  fake-model:\n    endpoint: fake\n",
        encoding="utf-8",
    )
    return path


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
        "workspace", "agents", "middleware", "skills", "subagents", "tools",
        "models", "groups", "seed", "sessions",
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
    assert origins["groups"]["kind"] == "unset"


def test_json_and_the_human_form_describe_the_same_workspace(cfg, monkeypatch, capsys):
    """Two formats, one answer."""
    import json

    _seed_something(cfg)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

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


def test_a_broken_workspace_is_non_zero_in_either_format(cfg, monkeypatch, capsys):
    """The exit code does not depend on the format, and the reason is in the document
    too -- so a script can find out either way round.
    """
    import json

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["subagents_error"]


def test_json_is_asked_for_rather_than_assumed(cfg, monkeypatch, capsys):
    """A listing whose default output is JSON is a listing nobody reads."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

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


def test_a_skill_offered_under_another_name_is_named_in_the_listing(cfg, monkeypatch, capsys):
    """`--list` is where somebody goes *because* a grant was refused for a skill they
    can see in the tree.
    """
    directory = cfg.skills_dir / "company-lookup"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: find-company\ndescription: Looks a company up.\n---\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")  # or the endpoint is dropped
    monkeypatch.setenv("KINGFISHER_SKILLS_ENABLED", "1")

    assert main(["list"]) == 0, "a misfiled skill loads, so this is not a failure"

    printed = capsys.readouterr().out
    assert "company-lookup/ is offered as find-company" in printed
    assert "rename the directory to match" in printed


def test_an_unloadable_agent_catalogue_is_non_zero_too(cfg, monkeypatch, capsys):
    """The kind that arrived last, and the one `failed` did not name."""
    import json

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "broken.yaml").write_text("name: broken\ndescription: d\nnope: 1\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["agents_error"]


def test_an_unloadable_tool_still_leaves_the_rest_of_the_listing(cfg, monkeypatch, capsys):
    """One unloadable catalogue must not take the others down with it, which is this
    record's own rule and was not true of tools.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
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


# -- listing under a group vocabulary ---------------------------------------

TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''


NARROW = """name: narrow
description: An agent.
groups: [A, B]
tools:
  - name: line_count
    groups: [A]
system_prompt: |
  You do the task.
"""


WIDE = """name: wide
description: An agent.
groups: [A, B]
tools:
  - name: line_count
    groups: [A, B]
system_prompt: |
  You do the task.
"""


def _workspace(cfg, monkeypatch, *agents: str, vocabulary: str = "groups: [A, B]\n"):
    """A workspace with one tool and whichever agents the test names."""
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for document in agents:
        name = document.split("name: ", 1)[1].split("\n", 1)[0]
        (directory / f"{name}.yaml").write_text(document, encoding="utf-8")
    (cfg.workspace / "groups.yaml").write_text(vocabulary, encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    return cfg


@pytest.fixture
def policied(cfg, monkeypatch):
    return _workspace(cfg, monkeypatch, NARROW)


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


def test_the_roll_up_shows_one_tool_at_two_audiences(cfg, monkeypatch, capsys):
    """The case it exists for: a call site quietly wider than its neighbour."""
    _workspace(cfg, monkeypatch, NARROW, WIDE)

    assert main(["list"]) == 0

    section = capsys.readouterr().out.split("by tool", 1)[1]
    assert "narrow  [A]" in section
    assert "wide  [A, B]" in section


BOTH = """name: both
description: An agent.
groups: [{all_of: [A, B]}]
tools:
  - name: line_count
    groups: [A, B]
system_prompt: |
  You do the task.
"""


NAMED = """name: named
description: An agent.
groups: [ab]
system_prompt: |
  You do the task.
"""


def test_an_inline_conjunction_reads_as_a_plus_b(cfg, monkeypatch, capsys):
    """`+` for "and", so the audience column stays a column."""
    _workspace(cfg, monkeypatch, BOTH)

    assert main(["list"]) == 0

    assert "agent both  [A+B]" in capsys.readouterr().out


def test_a_conjunction_is_spelled_the_same_way_wherever_it_appears(cfg, monkeypatch, capsys):
    """The by-definition view and the roll-up print the same audience, and a reader
    comparing the two should not have to translate.
    """
    _workspace(cfg, monkeypatch, BOTH)

    shown = capsys.readouterr().out if main(["list"]) == 0 else ""
    before, after = shown.split("by tool", 1)
    assert "[A+B]" in before
    assert "both  [A, B]" in after


def test_a_named_compound_says_what_it_requires(cfg, monkeypatch, capsys):
    """A name tells a reader nothing on the line it appears on, and every line it
    appears on needs it -- so it is said once, above.
    """
    _workspace(
        cfg, monkeypatch, NAMED, vocabulary="groups:\n  A: {}\n  B: {}\n  ab: {all_of: [A, B]}\n"
    )

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "groups that require others" in shown
    assert "ab = A+B" in shown
    assert "agent named  [ab]" in shown


def test_a_vocabulary_with_no_compounds_gets_no_such_section(policied, capsys):
    """It exists to make audiences readable, so it earns its lines or it has none."""
    assert main(["list"]) == 0

    assert "groups that require others" not in capsys.readouterr().out


def test_a_conjunction_survives_the_json_round_trip(cfg, monkeypatch):
    """`json` holds neither a set nor a tuple, so this is not a formality: an audience
    carrying a conjunction used to be unencodable outright.
    """
    import json

    from kingfisher import config_from_env, inventory
    from kingfisher.presentation.cli.listing import as_json

    _workspace(
        cfg, monkeypatch, BOTH, NAMED,
        vocabulary="groups:\n  A: {}\n  B: {}\n  ab: {all_of: [A, B]}\n",
    )

    # From the environment, not the fixture: the vocabulary is a file the
    # helper just wrote, and the fixture config predates it.
    document = json.loads(json.dumps(as_json(inventory(config_from_env()))))

    # Nested, not "A+B": a script should not have to parse a separator out of a
    # name, and a group name may legally contain one.
    assert document["audiences"]["agents"]["both"]["groups"] == [["A", "B"]]
    assert document["access"]["requires"]["ab"] == ["A", "B"]
    assert document["access"]["names"]["A"] == ["A"]


NARROWED = """name: narrowed
description: An agent.
groups: [A, B]
tools:
  - name: line_count
    groups: [C]
system_prompt: |
  You do the task.
"""


def test_an_entry_narrowing_past_its_definition_is_reported(cfg, monkeypatch, capsys):
    """It used to be refused."""
    _workspace(cfg, monkeypatch, NARROWED, vocabulary="groups: [A, B, C]\n")

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "narrows past this definition's own audience" in shown
    assert "agent narrowed: tool line_count  [C]" in shown


def test_a_narrowed_entry_reaches_a_caller_holding_both(cfg, monkeypatch):
    """The report is not the point -- this is."""
    from kingfisher import config_from_env
    from kingfisher.domain.access import reaches
    from kingfisher.kinds.agents.catalogue import LocalAgentRepository

    _workspace(cfg, monkeypatch, NARROWED, vocabulary="groups: [A, B, C]\n")
    reach = config_from_env().access
    assert reach is not None
    spec = LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["narrowed"]

    assert spec.declares(reach.expand(["A", "C"])).tools == ("line_count",)
    assert spec.declares(reach.expand(["A"])).tools == ()
    # And the definition's own line still gates the agent itself: C alone opens
    # nothing, so there is no way to reach the tool by holding only C.
    assert not reaches(spec.groups, reach.expand(["C"]))


def test_a_callers_view_carries_no_audiences(policied, capsys):
    """Who else reaches a thing is the operator's question, not a caller's."""
    assert main(["list", "--as", "A"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" not in shown
    assert "by tool" not in shown


def test_a_callers_view_drops_an_agent_they_cannot_open(cfg, monkeypatch, capsys):
    _workspace(cfg, monkeypatch, NARROW, vocabulary="groups: [A, B, C]\n")

    assert main(["list", "--as", "C"]) == 0

    assert "narrow" not in capsys.readouterr().out


def test_the_operator_still_sees_it(cfg, monkeypatch, capsys):
    """So the assertion above is not passing because the agent vanished."""
    _workspace(cfg, monkeypatch, NARROW, vocabulary="groups: [A, B, C]\n")

    assert main(["list"]) == 0

    assert "narrow" in capsys.readouterr().out


def test_listing_names_a_definition_that_restricts_nobody(cfg, monkeypatch, capsys):
    """Default-open, said where somebody will see it."""
    from tests.conftest import an_agent

    _workspace(cfg, monkeypatch)
    an_agent(cfg, "open_to_all")

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "reachable by everyone" in printed
    assert "open_to_all" in printed


def test_naming_a_group_that_does_not_exist_is_refused(policied, capsys):
    assert main(["list", "--as", "Q"]) != 0

    assert "unknown group" in capsys.readouterr().err


def test_no_vocabulary_means_no_access_section(cfg, monkeypatch, capsys):
    from tests.conftest import an_agent

    an_agent(cfg, "plain")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0
    assert "access —" not in capsys.readouterr().out


def test_the_listing_reports_a_definition_naming_an_undeclared_group(cfg, monkeypatch, capsys):
    """The listing is where somebody diagnosing this looks, and it goes through
    `inventory` rather than `Kingfisher` -- so the check has to be in both or the one
    place a reader would check shows a broken definition as ordinary.
    """
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", groups="[analists]")
    (cfg.workspace / "groups.yaml").write_text("groups: [analysts]\n", encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 1, "a workspace that will not build is a non-zero listing"

    printed = capsys.readouterr().out
    assert "analists" in printed
    assert "analysts" in printed, "and the spelling that would have worked"


def test_the_listing_is_clean_when_every_group_is_declared(cfg, monkeypatch, capsys):
    """So the rule above is not passing because every listing says that."""
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", groups="[analysts]")
    (cfg.workspace / "groups.yaml").write_text("groups: [analysts]\n", encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0
    assert "cannot load" not in capsys.readouterr().out
