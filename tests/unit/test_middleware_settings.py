"""Settings a definition writes beside a middleware name."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from langchain.agents.middleware import AgentMiddleware

from kingfisher.domain.capabilities import ALL, CapabilityError
from kingfisher.infrastructure.harness.middleware import ByName, declared_middleware
from kingfisher.kinds.agents import spec as agent_format
from kingfisher.kinds.agents.spec import AgentError
from kingfisher.kinds.subagents import reading as subagent_format
from kingfisher.kinds.subagents.spec import SubagentError


class Audit(AgentMiddleware):
    """A deployment's own, with one key it is willing to be told about."""

    name = "Audit"
    defaults = {"level": "INFO", "destination": "/var/log/audit"}
    yaml_settable = frozenset({"level"})

    def __init__(self, level: str, destination: str) -> None:
        self.level = level
        self.destination = destination
        super().__init__()


class Bare(AgentMiddleware):
    """A class that declares neither attribute, which is most of them."""

    name = "Bare"


class NeedsAnArgument(AgentMiddleware):
    """A registry entry whose `defaults` do not cover its own constructor."""

    name = "NeedsAnArgument"

    def __init__(self, required: str) -> None:
        self.required = required
        super().__init__()


class WantsABackend(AgentMiddleware):
    """A class needing an object, which is the half no yaml file can reach."""

    name = "WantsABackend"
    wants = frozenset({"backend"})

    def __init__(self, backend: object) -> None:
        self.backend = backend
        super().__init__()


class WantsAModel(AgentMiddleware):
    """The shape `compaction.py` ships: a want a definition may name instead."""

    name = "WantsAModel"
    wants = frozenset({"model"})
    defaults = {"keep": 20}
    yaml_settable = frozenset({"model"})

    def __init__(self, model: object, keep: int) -> None:
        self.model = model
        self.keep = keep
        super().__init__()


def _never_resolved(written: str, subject: str) -> object:
    """A resolver that fails the test if a name was resolved when none was written."""
    msg = f"resolved {written!r} for {subject}, and nothing named one"
    raise AssertionError(msg)


def agent_spec(body: str):
    return agent_format.parse(yaml.safe_load(body), Path("researcher.yaml"))


def subagent_spec(body: str):
    # The document rather than decoded fields: reading one is a single call now,
    # and the YAML step this used to do by hand is inside it.
    return subagent_format.read(body, Path("sweeper.yaml"))


def written(middleware: str) -> str:
    return (
        f"name: researcher\ndescription: d\n{middleware}"
        "system_prompt: |\n  You answer questions.\n"
    )


# -- the two spellings ----------------------------------------------------


def test_a_name_on_its_own_is_what_it_always_was():
    """The form every existing definition uses, unchanged and settings-free."""
    spec = agent_spec(written("middlewares: [audit]\n"))

    assert spec.middlewares == ("audit",)
    assert dict(spec.middleware_settings) == {}


def test_a_name_written_long_carries_its_settings():
    spec = agent_spec(
        written("middlewares:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    assert spec.middlewares == ("audit",), "the name is still just a name"
    assert dict(spec.middleware_settings) == {"audit": {"level": "DEBUG"}}


def test_both_spellings_may_share_one_list():
    """An entry is a name, and the mapping is that name with values attached."""
    spec = agent_spec(
        written(
            "middlewares:\n"
            "  - call-cap-strict\n"
            "  - name: audit\n"
            "    settings:\n"
            "      level: DEBUG\n"
        )
    )

    assert spec.middlewares == ("call-cap-strict", "audit")
    assert dict(spec.middleware_settings) == {"audit": {"level": "DEBUG"}}


def test_the_long_form_may_write_no_settings_at_all():
    """Absent and empty are the same answer here: this entry asked for nothing."""
    spec = agent_spec(written("middlewares:\n  - name: audit\n"))

    assert spec.middlewares == ("audit",)
    assert dict(spec.middleware_settings) == {"audit": {}}


def test_a_subagent_reads_the_same_field_the_same_way():
    """One field, two formats."""
    spec = subagent_spec(
        "name: sweeper\ndescription: d\n"
        "middlewares:\n  - name: audit\n    settings:\n      level: DEBUG\n"
        "system_prompt: |\n  You read a lot.\n"
    )

    assert spec.middlewares == ("audit",)
    assert dict(spec.middleware_settings) == {"audit": {"level": "DEBUG"}}


# -- the wildcard ---------------------------------------------------------


def test_the_plain_star_still_means_everything():
    """The form `assistant.yaml` ships, and the only one a shipped file may carry."""
    spec = agent_spec(written('middlewares: ["*"]\n'))

    assert spec.middlewares == ALL
    assert dict(spec.middleware_settings) == {}


def test_a_star_may_not_be_written_in_the_mapping_form():
    """`"*"` is whatever this deployment registered, so a setting written beside it is a
    setting for classes the file has never seen.
    """
    with pytest.raises(AgentError, match="does not take"):
        agent_spec(
            written('middlewares:\n  - name: "*"\n    settings:\n      level: DEBUG\n')
        )


def test_a_star_in_the_mapping_form_is_refused_even_with_no_settings():
    """Two ways to say one thing is one way too many."""
    with pytest.raises(AgentError, match="does not take"):
        agent_spec(written('middlewares:\n  - name: "*"\n'))


# -- what the format refuses ----------------------------------------------


def test_an_unknown_key_in_an_entry_is_refused_with_a_guess():
    """A key we ignore is a key the author believes took effect -- the same rule the two
    formats already apply to their own fields, applied one level down.
    """
    with pytest.raises(AgentError, match="setings"):
        agent_spec(
            written("middlewares:\n  - name: audit\n    setings:\n      level: DEBUG\n")
        )


def test_an_entry_with_no_name_is_refused():
    """The settings are *for* the name, so there is nothing to attach them to without
    one.
    """
    with pytest.raises(AgentError, match="no 'name'"):
        agent_spec(written("middlewares:\n  - settings:\n      level: DEBUG\n"))


def test_one_name_written_twice_is_refused():
    """One name is one thing to build, so a second entry for it is either settings that
    cannot both apply or a line that says nothing.
    """
    with pytest.raises(AgentError, match="twice"):
        agent_spec(
            written(
                "middlewares:\n"
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
            written("middlewares:\n  - audit\n  - name: audit\n    settings: {level: X}\n")
        )


def test_an_entry_that_is_neither_a_name_nor_a_mapping_is_refused():
    with pytest.raises(AgentError, match="neither a name nor a mapping"):
        agent_spec(written("middlewares: [3]\n"))


def test_settings_that_are_not_a_mapping_are_refused():
    """`mapping` already refuses this and says why; what this pins is that the entry's
    settings reach it at all.
    """
    with pytest.raises(AgentError, match="must be a mapping"):
        agent_spec(written("middlewares:\n  - name: audit\n    settings: gold\n"))


def test_a_subagent_refuses_the_same_shapes_as_its_own_error():
    """Same rules, and each format's own exception -- a caller catching `SubagentError`
    should not be handed the agent format's.
    """
    with pytest.raises(SubagentError, match="does not take"):
        subagent_spec(
            "name: sweeper\ndescription: d\n"
            'middlewares:\n  - name: "*"\n    settings: {level: X}\n'
            "system_prompt: |\n  You read.\n"
        )


# -- building it ----------------------------------------------------------


def test_a_deployments_defaults_apply_when_a_definition_wrote_nothing():
    spec = agent_spec(written("middlewares: [audit]\n"))

    (built,) = declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")

    assert built.level == "INFO"
    assert built.destination == "/var/log/audit"


def test_a_definition_overrides_the_default_for_a_key_it_may_write():
    """The precedence rule, which is the whole of it: the registry holds what applies
    when nobody says otherwise, and a definition overrides it only where the class
    said it may.
    """
    spec = agent_spec(
        written("middlewares:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    (built,) = declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")

    assert built.level == "DEBUG", "the definition's value did not win"
    assert built.destination == "/var/log/audit", "an unwritten key stayed the deployment's"


def test_a_key_the_class_did_not_offer_is_refused():
    """The rule the shape exists for, and the one that has to be loud."""
    spec = agent_spec(
        written("middlewares:\n  - name: audit\n    settings:\n      destination: /tmp/mine\n")
    )

    with pytest.raises(CapabilityError, match="does not accept"):
        declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")


def test_a_class_offering_nothing_says_so_rather_than_printing_an_empty_list():
    """`Bare` declares no `yaml_settable`, which is most classes."""
    spec = agent_spec(
        written("middlewares:\n  - name: bare\n    settings:\n      level: DEBUG\n")
    )

    with pytest.raises(CapabilityError, match="takes no settings from a definition"):
        declared_middleware(spec, {"bare": Bare}, ALL, kind="agent")


def test_a_class_that_declares_neither_attribute_still_builds():
    """Registering a class was legal before settings existed and stays legal without
    them: no `defaults` means called with nothing.
    """
    spec = agent_spec(written("middlewares: [bare]\n"))

    (built,) = declared_middleware(spec, {"bare": Bare}, ALL, kind="agent")

    assert type(built).__name__ == "Bare"


def test_an_old_style_factory_still_works():
    """A registry has held zero-argument factories since before settings existed, and
    breaking every deployment that wrote one would be a poor trade for a field most
    definitions will never use.
    """
    spec = agent_spec(written("middlewares: [audit]\n"))
    registry = {"audit": lambda: Audit(level="WARN", destination="/dev/null")}

    (built,) = declared_middleware(spec, registry, ALL, kind="agent")

    assert built.level == "WARN"


def test_settings_written_for_a_factory_are_refused_rather_than_dropped():
    """A factory closed over its values when the deployment wrote the lambda, so there
    is no seam to pass a setting through.
    """
    spec = agent_spec(
        written("middlewares:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )
    registry = {"audit": lambda: Audit(level="WARN", destination="/dev/null")}

    with pytest.raises(CapabilityError, match="factory taking no arguments"):
        declared_middleware(spec, registry, ALL, kind="agent")


def test_a_class_whose_defaults_miss_an_argument_names_the_registry_entry():
    """The deployment's mistake, so the message says which entry and what it was given
    rather than surfacing a bare `TypeError` from a constructor the definition's
    author has never seen.
    """
    spec = agent_spec(written("middlewares: [needy]\n"))

    with pytest.raises(CapabilityError, match="could not build middleware 'needy'"):
        declared_middleware(spec, {"needy": NeedsAnArgument}, ALL, kind="agent")


def test_a_class_attribute_is_not_mutated_by_the_merge():
    """`defaults` is copied before the settings go over it."""
    spec = agent_spec(
        written("middlewares:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    declared_middleware(spec, {"audit": Audit}, ALL, kind="agent")

    assert Audit.defaults == {"level": "INFO", "destination": "/var/log/audit"}
    plain = agent_spec(written("middlewares: [audit]\n"))
    (second,) = declared_middleware(plain, {"audit": Audit}, ALL, kind="agent")
    assert second.level == "INFO", "the first build's setting leaked into the second"


# -- narrowing ------------------------------------------------------------


def test_a_withheld_name_is_refused_even_when_it_carries_settings():
    """Settings change nothing about how a name narrows."""
    spec = agent_spec(
        written("middlewares:\n  - name: audit\n    settings:\n      level: DEBUG\n")
    )

    with pytest.raises(CapabilityError, match="may not use"):
        declared_middleware(spec, {"audit": Audit}, (), kind="agent")


def test_a_star_that_resolves_to_a_registry_takes_no_settings_with_it():
    """`["*"]` cannot carry settings by construction, so everything it resolves to is
    built on the deployment's own values.
    """
    spec = agent_spec(written('middlewares: ["*"]\n'))

    built = declared_middleware(spec, {"audit": Audit, "bare": Bare}, ALL, kind="agent")

    assert sorted(type(m).__name__ for m in built) == ["Audit", "Bare"]
    audit = next(m for m in built if type(m).__name__ == "Audit")
    assert audit.level == "INFO"


# -- what the harness hands over ------------------------------------------


def test_a_want_arrives_as_the_object_this_build_is_holding():
    """The whole reason `wants` exists: a class needing a live object gets the one
    this graph was assembled with, where `defaults` could only have given it a scalar.
    """
    spec = agent_spec(written("middlewares: [fs]\n"))
    backend = object()

    (built,) = declared_middleware(
        spec, {"fs": WantsABackend}, ALL, kind="agent", provisions={"backend": backend}
    )

    assert built.backend is backend


def test_a_want_this_build_does_not_provide_is_refused_naming_what_it_does():
    """A typo in `wants`, or a class written against a build that holds more.

    The listing is the actionable half. Without it the reader is told `backend` is
    unavailable and left to guess what is -- and the set is deliberately not a
    constant anywhere, so there is nothing to go and read instead.
    """
    spec = agent_spec(written("middlewares: [fs]\n"))

    with pytest.raises(CapabilityError, match="wants 'backend'") as raised:
        declared_middleware(
            spec, {"fs": WantsABackend}, ALL, kind="agent", provisions={"definition": spec}
        )

    assert "provides definition" in str(raised.value)


def test_a_want_nobody_named_falls_back_to_what_this_build_runs():
    """`middlewares: [compact]` with no settings is a working line.

    A fallback that resolved something would mean the bare form ran the deployment's
    default rather than the model the agent in front of it was pinned to, which is
    the whole failure `wants` exists to close.
    """
    spec = agent_spec(written("middlewares: [needs-model]\n"))
    running = object()

    (built,) = declared_middleware(
        spec,
        {"needs-model": WantsAModel},
        ALL,
        kind="agent",
        provisions={"model": ByName(running, resolve=_never_resolved)},
    )

    assert built.model is running
    assert built.keep == 20, "the ordinary `defaults` half still applies beside it"


def test_a_name_a_definition_wrote_is_resolved_rather_than_passed_through():
    """`model: cheap` is a name in the file and an object in the constructor.

    Passed through, the class is handed the string -- which is how
    `SummarizationMiddleware` ends up at `init_chat_model`, inferring a provider and
    reading credentials from the environment around the catalogue entirely.
    """
    spec = agent_spec(
        written("middlewares:\n  - name: needs-model\n    settings:\n      model: cheap\n")
    )
    cheap = object()
    asked: list[tuple[str, str]] = []

    def resolve(name: str, subject: str) -> object:
        asked.append((name, subject))
        return cheap

    (built,) = declared_middleware(
        spec,
        {"needs-model": WantsAModel},
        ALL,
        kind="agent",
        provisions={"model": ByName(object(), resolve=resolve)},
    )

    assert built.model is cheap
    # The subject is what a bad name's refusal is read under, and it has to name
    # both halves: which file wrote it, and which middleware it was written for.
    assert asked == [("cheap", "middleware 'needs-model' on agent 'researcher'")]


def test_a_deployments_own_default_may_name_one_too():
    """Static configuration, with `yaml_settable` shut.

    A class shipping `defaults = {"model": "cheap"}` and opening nothing pins the
    model in the deployment's code -- the right shape for a middleware whose model
    is not a definition's business, and unreachable if only `settings:` were read.
    """

    class Pinned(WantsAModel):
        name = "Pinned"
        defaults = {"model": "cheap", "keep": 20}
        yaml_settable = frozenset()

    spec = agent_spec(written("middlewares: [pinned]\n"))
    cheap = object()

    (built,) = declared_middleware(
        spec,
        {"pinned": Pinned},
        ALL,
        kind="agent",
        provisions={"model": ByName(object(), resolve=lambda name, subject: cheap)},
    )

    assert built.model is cheap


def test_a_value_written_for_a_want_that_arrives_whole_is_refused():
    """There is no name a file could carry for "the filesystem", so a value for one
    means nothing and is a mistake worth saying out loud rather than ignoring.
    """

    class Confused(WantsABackend):
        name = "Confused"
        defaults = {"backend": "/tmp"}

    spec = agent_spec(written("middlewares: [confused]\n"))

    with pytest.raises(CapabilityError, match="wants 'backend'"):
        declared_middleware(
            spec, {"confused": Confused}, ALL, kind="agent", provisions={"backend": object()}
        )


def test_a_whole_want_opened_to_definitions_is_refused_before_one_writes_it():
    """Refused on the class rather than on the write, or the trap stays armed until
    somebody falls into it -- and the definition that does gets blamed for a
    declaration the deployment made.
    """

    class Opened(WantsABackend):
        name = "Opened"
        yaml_settable = frozenset({"backend"})

    spec = agent_spec(written("middlewares: [opened]\n"))

    with pytest.raises(CapabilityError, match="wants 'backend'"):
        declared_middleware(
            spec, {"opened": Opened}, ALL, kind="agent", provisions={"backend": object()}
        )


def test_a_factory_declaring_wants_is_refused_rather_than_built_without_them():
    """A callable that is not a class is called with nothing, so a want it declared
    would be dropped in silence -- the middleware built without the model it was
    written to use, failing later and somewhere else.
    """

    class FactoryThatWants:
        wants = frozenset({"model"})

        def __call__(self) -> Bare:
            return Bare()

    spec = agent_spec(written("middlewares: [made]\n"))

    with pytest.raises(CapabilityError, match="declares `wants`"):
        declared_middleware(
            spec,
            {"made": FactoryThatWants()},
            ALL,
            kind="agent",
            provisions={"model": ByName(object(), resolve=_never_resolved)},
        )
