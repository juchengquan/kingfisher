"""The capability flags, under the only names that now mean them."""

from __future__ import annotations

import warnings

from kingfisher.application.config import Environment
from kingfisher.presentation.cli.health import RETIRED

#: The four whose replacement is another flag, taken from the list `doctor`
#: reports rather than spelled again here -- a fifth flag renamed and added
#: there is covered by these without anybody remembering to come back.
FLAG_PAIRS = {old: new for old, new in RETIRED.items() if new.endswith("_ENABLED")}


def test_there_are_flags_to_check():
    """A dict comprehension that matched nothing would leave every rule below
    passing over an empty loop, which is the shape of a guard that guards nothing.
    """
    assert len(FLAG_PAIRS) == 4


def test_no_old_flag_name_reaches_its_setting():
    """The deprecation is over: a bare name is a name this reader has never heard
    of, so it cannot turn a capability on and cannot turn one off either.
    """
    for old, new in FLAG_PAIRS.items():
        assert Environment({old: "true"}).flag(new) is False, old


def test_an_old_name_does_not_warn_either():
    """It is not read *and* not mentioned. A shim that still warned would keep the
    cost of the deprecation -- a line in every start-up of a deployment nobody has
    migrated -- while giving none of its benefit, since the value is ignored.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Environment(dict.fromkeys(FLAG_PAIRS, "true")).flag("KINGFISHER_SKILLS_ENABLED")

    assert not caught, f"reading a current name warned at: {caught}"


def test_the_new_name_is_what_works():
    """The control for the rule above: `is False` would also be the answer if
    `flag` had stopped reading anything at all.
    """
    for new in FLAG_PAIRS.values():
        assert Environment({new: "true"}).flag(new) is True, new


def test_the_collision_the_suffix_exists_to_end():
    """The bug, written down as the behaviour it produced.

    `KINGFISHER_SKILLS` answered two questions at once: whether a deployment wired
    skills, and -- exported into the agent's shell by `shell_env` -- *where* the
    catalogue is, which is how a skill's own scripts reach their neighbours. A
    deployment writing the path, which is what the name means everywhere the agent
    can see it, set a flag to a value no parser recognises and got skills off with
    no error and nothing logged: `'/workspace/skills' -> skills enabled: False`.

    It stays here now that the bare name is read by nothing, because that is what
    ended it: the path and the flag can no longer arrive at one reader.
    """
    mistaken = Environment({"KINGFISHER_SKILLS": "/workspace/skills"})
    assert mistaken.flag("KINGFISHER_SKILLS_ENABLED") is False

    correct = Environment({"KINGFISHER_SKILLS_ENABLED": "true"})
    assert correct.flag("KINGFISHER_SKILLS_ENABLED") is True


def test_a_default_survives_a_name_nobody_set() -> None:
    """`KINGFISHER_CONVERSATION_ENABLED` is the one flag that is on unless it is
    turned off, so a reader returning the wrong empty answer would take conversation
    away from every deployment that never set it.
    """
    assert Environment({}).flag("KINGFISHER_CONVERSATION_ENABLED", default=True) is True
    assert (
        Environment({"KINGFISHER_CONVERSATION_ENABLED": "false"}).flag(
            "KINGFISHER_CONVERSATION_ENABLED", default=True
        )
        is False
    )
