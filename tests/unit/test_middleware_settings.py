"""Settings a definition writes beside a middleware name.

`middleware:` selects code the deployment wrote, and that has not changed.
What this adds is a second way to write one entry of the list -- a mapping of
`name` and `settings` -- so a definition can pass values to the code it
selected, for the keys the class behind that name says it may.

The rule the whole shape rests on is that a name and a value are different
kinds of thing. A name is granted, narrowed and refused; a value is passed to a
constructor. So the two travel apart: `middleware` stays a `Selection` and
`middleware_settings` rides beside it, which is `tool_sources` beside `tools`
and for the same reason.

What a definition may write is not the definition's decision and not the
format's. `yaml_settable` is a class attribute -- written once, beside the code
it governs -- and a class that declares none is a class no definition can
configure. `CallCap` is deliberately that one: a cap a definition can set is
not a cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from langchain.agents.middleware import AgentMiddleware

from kingfisher.domain import agent as agent_format
from kingfisher.domain.agent import AgentError
from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.domain.subagent import SubagentError
from kingfisher.domain.subagent import reading as subagent_format
from kingfisher.infrastructure.harness.agent import declared_middleware


class Audit(AgentMiddleware):
    """A deployment's own, with one key it is willing to be told about.

    Two settings and only one settable, which is the case worth having in a
    fixture: `destination` is the deployment's to choose and `level` is not, so
    a test that confuses the two fails rather than passing by accident.
    """

    name = "Audit"
    defaults = {"level": "INFO", "destination": "/var/log/audit"}
    yaml_settable = frozenset({"level"})

    def __init__(self, level: str, destination: str) -> None:
        self.level = level
        self.destination = destination
        super().__init__()


class Bare(AgentMiddleware):
    """A class that declares neither attribute, which is most of them.

    Registering a class was legal before settings existed and stays legal
    without them: no `defaults` means called with nothing, no `yaml_settable`
    means a definition may write nothing.
    """

    name = "Bare"


class NeedsAnArgument(AgentMiddleware):
    """A registry entry whose `defaults` do not cover its own constructor.

    The deployment's mistake rather than any definition's, and the one case
    where the build refuses something no agent file can fix.
    """

    name = "NeedsAnArgument"

    def __init__(self, required: str) -> None:
        self.required = required
        super().__init__()


def agent_spec(body: str):
    return agent_format.parse(yaml.safe_load(body), Path("researcher.yaml"))


def subagent_spec(body: str):
    return subagent_format.parse(yaml.safe_load(body), Path("sweeper.yaml"))


def written(middleware: str) -> str:
    return (
        f"name: researcher\ndescription: d\n{middleware}"
        "system_prompt: |\n  You answer questions.\n"
    )


# -- the two spellings ----------------------------------------------------


def test_a_name_on_its_own_is_what_it_always_was():
    """The form every existing definition uses, unchanged and settings-free.

    First rather than last, because it is the case that must not regress: the
    long form is the addition, and a file that never writes one should not be
    able to tell that it exists.
    """
    spec = agent_spec(written("middleware: [audit]\n"))

    assert spec.middleware == ("audit",)
    assert dict(spec.middleware_settings) == {}


def test_a_name_written_long_carries_its_settings():
    spec = agent_spec(
        written("middleware:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    assert spec.middleware == ("audit",), "the name is still just a name"
    assert dict(spec.middleware_settings) == {"audit": {"level": "DEBUG"}}


def test_both_spellings_may_share_one_list():
    """An entry is a name, and the mapping is that name with values attached.

    A format where the whole list changed shape as soon as one entry wanted a
    setting would make every definition pay for the one that did.
    """
    spec = agent_spec(
        written(
            "middleware:\n"
            "  - call-cap-strict\n"
            "  - name: audit\n"
            "    settings:\n"
            "      level: DEBUG\n"
        )
    )

    assert spec.middleware == ("call-cap-strict", "audit")
    assert dict(spec.middleware_settings) == {"audit": {"level": "DEBUG"}}


def test_the_long_form_may_write_no_settings_at_all():
    """Absent and empty are the same answer here: this entry asked for nothing.

    Worth pinning because the two reach `mapping` by different routes -- one
    has no key, the other has a key with nothing under it -- and a reader that
    told them apart would be inventing a distinction the format does not have.
    """
    spec = agent_spec(written("middleware:\n  - name: audit\n"))

    assert spec.middleware == ("audit",)
    assert dict(spec.middleware_settings) == {"audit": {}}


def test_a_subagent_reads_the_same_field_the_same_way():
    """One field, two formats. A definition writing settings has to mean the
    same thing in either file, or the pairing in `assets_examples/middleware/` would
    be two features wearing one name."""
    spec = subagent_spec(
        "name: sweeper\ndescription: d\n"
        "middleware:\n  - name: audit\n    settings:\n      level: DEBUG\n"
        "system_prompt: |\n  You read a lot.\n"
    )

    assert spec.middleware == ("audit",)
    assert dict(spec.middleware_settings) == {"audit": {"level": "DEBUG"}}


# -- the wildcard ---------------------------------------------------------


def test_the_plain_star_still_means_everything():
    """The form `assistant.yaml` ships, and the only one a shipped file may
    carry. Nothing about settings touches it."""
    spec = agent_spec(written('middleware: ["*"]\n'))

    assert spec.middleware == ALL
    assert dict(spec.middleware_settings) == {}


def test_a_star_may_not_be_written_in_the_mapping_form():
    """`"*"` is whatever this deployment registered, so a setting written
    beside it is a setting for classes the file has never seen.

    Refused rather than read as "apply these to all of them", which is the
    reading that looks helpful and silently passes a key to a class that has
    never heard of it."""
    with pytest.raises(AgentError, match="does not take"):
        agent_spec(
            written('middleware:\n  - name: "*"\n    settings:\n      level: DEBUG\n')
        )


def test_a_star_in_the_mapping_form_is_refused_even_with_no_settings():
    """Two ways to say one thing is one way too many.

    `[{name: "*"}]` means exactly `["*"]`, so allowing it buys nothing -- and
    the day someone adds a `settings:` block underneath, the rule either kicks
    in suddenly or has to be relaxed. Refusing it now keeps one meaning per
    shape.
    """
    with pytest.raises(AgentError, match="does not take"):
        agent_spec(written('middleware:\n  - name: "*"\n'))


# -- what the format refuses ----------------------------------------------


def test_an_unknown_key_in_an_entry_is_refused_with_a_guess():
    """A key we ignore is a key the author believes took effect -- the same
    rule the two formats already apply to their own fields, applied one level
    down."""
    with pytest.raises(AgentError, match="setings"):
        agent_spec(
            written("middleware:\n  - name: audit\n    setings:\n      level: DEBUG\n")
        )


def test_an_entry_with_no_name_is_refused():
    """The settings are *for* the name, so there is nothing to attach them to
    without one."""
    with pytest.raises(AgentError, match="no 'name'"):
        agent_spec(written("middleware:\n  - settings:\n      level: DEBUG\n"))


def test_one_name_written_twice_is_refused():
    """One name is one thing to build, so a second entry for it is either
    settings that cannot both apply or a line that says nothing."""
    with pytest.raises(AgentError, match="twice"):
        agent_spec(
            written(
                "middleware:\n"
                "  - name: audit\n"
                "    settings: {level: DEBUG}\n"
                "  - name: audit\n"
                "    settings: {level: INFO}\n"
            )
        )


def test_a_bare_name_and_the_same_name_written_long_are_still_twice():
    """The duplicate rule is about the name, not the spelling."""
    with pytest.raises(AgentError, match="twice"):
        agent_spec(
            written("middleware:\n  - audit\n  - name: audit\n    settings: {level: X}\n")
        )


def test_an_entry_that_is_neither_a_name_nor_a_mapping_is_refused():
    with pytest.raises(AgentError, match="neither a name nor a mapping"):
        agent_spec(written("middleware: [3]\n"))


def test_settings_that_are_not_a_mapping_are_refused():
    """`mapping` already refuses this and says why; what this pins is that the
    entry's settings reach it at all."""
    with pytest.raises(AgentError, match="must be a mapping"):
        agent_spec(written("middleware:\n  - name: audit\n    settings: gold\n"))


def test_a_subagent_refuses_the_same_shapes_as_its_own_error():
    """Same rules, and each format's own exception -- a caller catching
    `SubagentError` should not be handed the agent format's."""
    with pytest.raises(SubagentError, match="does not take"):
        subagent_spec(
            "name: sweeper\ndescription: d\n"
            'middleware:\n  - name: "*"\n    settings: {level: X}\n'
            "system_prompt: |\n  You read.\n"
        )


# -- building it ----------------------------------------------------------


def test_a_deployments_defaults_apply_when_a_definition_wrote_nothing():
    spec = agent_spec(written("middleware: [audit]\n"))

    (built,) = declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")

    assert built.level == "INFO"
    assert built.destination == "/var/log/audit"


def test_a_definition_overrides_the_default_for_a_key_it_may_write():
    """The precedence rule, which is the whole of it: the registry holds what
    applies when nobody says otherwise, and a definition overrides it only
    where the class said it may."""
    spec = agent_spec(
        written("middleware:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    (built,) = declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")

    assert built.level == "DEBUG", "the definition's value did not win"
    assert built.destination == "/var/log/audit", "an unwritten key stayed the deployment's"


def test_a_key_the_class_did_not_offer_is_refused():
    """The rule the shape exists for, and the one that has to be loud.

    A definition that wrote a setting believes it took effect. Building without
    it -- running with the deployment's value while the file says otherwise --
    is how a cap nobody raised turns out to have been raised.
    """
    spec = agent_spec(
        written("middleware:\n  - name: audit\n    settings:\n      destination: /tmp/mine\n")
    )

    with pytest.raises(CapabilityError, match="does not accept"):
        declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")


def test_a_class_offering_nothing_says_so_rather_than_printing_an_empty_list():
    """`Bare` declares no `yaml_settable`, which is most classes. The refusal
    has to read as "this takes none" rather than as an empty tuple the reader
    has to interpret."""
    spec = agent_spec(
        written("middleware:\n  - name: bare\n    settings:\n      level: DEBUG\n")
    )

    with pytest.raises(CapabilityError, match="takes no settings from a definition"):
        declared_middleware(spec, {"bare": Bare}, ALL, kind="agent")


def test_a_class_that_declares_neither_attribute_still_builds():
    """Registering a class was legal before settings existed and stays legal
    without them: no `defaults` means called with nothing."""
    spec = agent_spec(written("middleware: [bare]\n"))

    (built,) = declared_middleware(spec, {"bare": Bare}, ALL, kind="agent")

    assert type(built).__name__ == "Bare"


def test_an_old_style_factory_still_works():
    """A registry has held zero-argument factories since before settings
    existed, and breaking every deployment that wrote one would be a poor trade
    for a field most definitions will never use."""
    spec = agent_spec(written("middleware: [audit]\n"))
    registry = {"audit": lambda: Audit(level="WARN", destination="/dev/null")}

    (built,) = declared_middleware(spec, registry, ALL, kind="agent")

    assert built.level == "WARN"


def test_settings_written_for_a_factory_are_refused_rather_than_dropped():
    """A factory closed over its values when the deployment wrote the lambda,
    so there is no seam to pass a setting through.

    Refused rather than built without them, because a factory that quietly
    ignored a `settings:` block is the same failure one layer down and harder
    to see: the file says a value and the object does not have it.
    """
    spec = agent_spec(
        written("middleware:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )
    registry = {"audit": lambda: Audit(level="WARN", destination="/dev/null")}

    with pytest.raises(CapabilityError, match="factory taking no arguments"):
        declared_middleware(spec, registry, ALL, kind="agent")


def test_a_class_whose_defaults_miss_an_argument_names_the_registry_entry():
    """The deployment's mistake, so the message says which entry and what it
    was given rather than surfacing a bare `TypeError` from a constructor the
    definition's author has never seen."""
    spec = agent_spec(written("middleware: [needy]\n"))

    with pytest.raises(CapabilityError, match="could not build middleware 'needy'"):
        declared_middleware(spec, {"needy": NeedsAnArgument}, ALL, kind="agent")


def test_a_class_attribute_is_not_mutated_by_the_merge():
    """`defaults` is copied before the settings go over it. Shared and mutated,
    one definition's setting would arrive in the next agent built from the same
    registry -- which is a bug that only shows up on the second build."""
    spec = agent_spec(
        written("middleware:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")

    assert Audit.defaults == {"level": "INFO", "destination": "/var/log/audit"}
    plain = agent_spec(written("middleware: [audit]\n"))
    (second,) = declared_middleware(plain, {"audit": Audit}, ALL, kind="agent")
    assert second.level == "INFO", "the first build's setting leaked into the second"


# -- narrowing ------------------------------------------------------------


def test_a_withheld_name_is_refused_even_when_it_carries_settings():
    """Settings change nothing about how a name narrows.

    A request that withheld the middleware refuses the definition, exactly as
    it did before the field existed -- running with less middleware than the
    definition specified could mean running without the audit hook it was
    written to have, and a `settings:` block does not make that safer.
    """
    spec = agent_spec(
        written("middleware:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    with pytest.raises(CapabilityError, match="may not use"):
        declared_middleware(spec, {"audit": Audit}, (), kind="agent")


def test_a_star_that_resolves_to_a_registry_takes_no_settings_with_it():
    """`["*"]` cannot carry settings by construction, so everything it resolves
    to is built on the deployment's own values. Worth driving rather than
    arguing, because it is the path `assistant.yaml` takes on every deployment
    that registered anything."""
    spec = agent_spec(written('middleware: ["*"]\n'))

    built = declared_middleware(spec, {"audit": Audit, "bare": Bare}, ALL, kind="agent")

    assert sorted(type(m).__name__ for m in built) == ["Audit", "Bare"]
    audit = next(m for m in built if type(m).__name__ == "Audit")
    assert audit.level == "INFO"
