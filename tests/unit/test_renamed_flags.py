"""The capability flags under their new names, and their old ones.

Renaming an environment variable is the one rename that fails in silence: a moved import
stops the program and says which, while a variable nobody reads falls back to its
default and the deployment comes up with skills off and no reason to look.

`KINGFISHER_SKILLS` was both a deployment's yes/no and the variable `shell_env` exports
holding the path to the skills catalogue, so a deployment writing the path set the flag
to a value no parser recognises and got skills off, silently.
"""

from __future__ import annotations

import warnings

import pytest

from kingfisher.application.config import RENAMED, Environment

PAIRS = sorted(RENAMED.items())


@pytest.mark.parametrize(("new", "old"), PAIRS, ids=[old for _, old in PAIRS])
def test_the_old_name_is_still_read_and_says_so(new: str, old: str) -> None:
    """Honoured so nothing breaks on upgrade, reported so it does not quietly become a
    second name nobody knows is load-bearing.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert Environment({old: "true"}).flag(new) is True

    assert len(caught) == 1, f"{old} was read without a word about it"
    assert old in str(caught[0].message)
    assert new in str(caught[0].message)
    assert issubclass(caught[0].category, DeprecationWarning)


@pytest.mark.parametrize(("new", "old"), PAIRS, ids=[old for _, old in PAIRS])
def test_the_new_name_wins_where_both_are_set(new: str, old: str) -> None:
    """A deployment mid-migration has the new one for a reason, and the old line is the
    one it has not got round to deleting.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        held = Environment({new: "false", old: "true"}).flag(new)

    assert held is False


@pytest.mark.parametrize(("new", "old"), PAIRS, ids=[old for _, old in PAIRS])
def test_the_new_name_alone_warns_about_nothing(new: str, old: str) -> None:
    """The common case, and the one that would be intolerable to make noisy: every start
    of every correctly configured deployment.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Environment({new: "true"}).flag(new)

    assert not caught, f"a deployment using {new} was warned at: {caught}"


def test_a_default_survives_the_old_name_being_absent() -> None:
    """`KINGFISHER_CONVERSATION_ENABLED` is the one flag that is on unless it is turned
    off, so a fallback that returned the wrong empty answer would take conversation
    away from every deployment that never set it.
    """
    assert Environment({}).flag("KINGFISHER_CONVERSATION_ENABLED", default=True) is True
    assert (
        Environment({"KINGFISHER_CONVERSATION_ENABLED": "false"}).flag(
            "KINGFISHER_CONVERSATION_ENABLED", default=True
        )
        is False
    )


def test_the_collision_the_suffix_exists_to_end() -> None:
    """The bug, written down as the behaviour it produced."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mistaken = Environment({"KINGFISHER_SKILLS": "/workspace/skills"})
        assert mistaken.flag("KINGFISHER_SKILLS_ENABLED") is False

    correct = Environment({"KINGFISHER_SKILLS_ENABLED": "true"})
    assert correct.flag("KINGFISHER_SKILLS_ENABLED") is True
