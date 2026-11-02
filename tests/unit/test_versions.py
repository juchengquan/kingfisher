"""The definitions a turn runs on, read again whenever the folder changes."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher
from kingfisher.core.access import caller_holds
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, start, tools_dir
from tests.unit.scripted import Scripted

#: Where a refused change is said.
VERSIONS_LOG = "kingfisher.core.versions"


def _echo_returning(cfg, prefix: str) -> None:
    """`echo` as a package, its code in a helper module, answering `prefix` + the text."""
    package = tools_dir(cfg) / "echo"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("from .words import echo\n\nTOOLS = [echo]\n")
    (package / "words.py").write_text(
        "def echo(text: str) -> str:\n"
        '    """Says the text back."""\n'
        f'    return "{prefix}" + text\n',
        encoding="utf-8",
    )


def _deployment(cfg) -> Kingfisher:
    _echo_returning(cfg, "ECHO:")
    an_agent(cfg, tools="[echo]")
    return Kingfisher(cfg, sessions=LocalSessions())


def _echoed(kf: Kingfisher, cfg) -> str:
    """What `echo` answered on one turn, in a session of its own."""
    Scripted.script.extend(
        [
            AIMessage("", tool_calls=[{"name": "echo", "args": {"text": "x"}, "id": "c1"}]),
            AIMessage("done"),
        ]
    )
    kf.run(Request("go", agent="only", session_id=start(cfg, f"s{len(Scripted.heard)}")))
    answer = [one for one in Scripted.heard[-1] if isinstance(one, ToolMessage)][-1]
    # The ledger's id is the last line of every result.
    return answer.text.rsplit("\n", 1)[0]


def test_an_edit_reaches_the_next_turn_without_a_restart(scripted):
    """Read once, an edit waited for a restart; and read again in one process, a tool
    package ran its new `__init__` over the helper module it imported the first time,
    which is the file edited here."""
    kf = _deployment(scripted)
    assert _echoed(kf, scripted) == "ECHO:x"

    _echo_returning(scripted, "EDITED:")

    assert _echoed(kf, scripted) == "EDITED:x"


def test_a_change_that_fails_leaves_turns_on_the_set_that_loaded_last(scripted, caplog):
    """Swapped in, a broken edit would fail every turn after it, and said on every turn it
    would bury the log; once fixed it is read."""
    kf = _deployment(scripted)
    words = tools_dir(scripted) / "echo" / "words.py"

    words.write_text("def echo(:\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=VERSIONS_LOG):
        answers = [_echoed(kf, scripted), _echoed(kf, scripted)]
    refused = [one for one in caplog.records if one.name == VERSIONS_LOG]
    _echo_returning(scripted, "FIXED:")

    assert answers == ["ECHO:x", "ECHO:x"]
    assert len(refused) == 1
    assert "words.py" in refused[0].getMessage()
    assert _echoed(kf, scripted) == "FIXED:x"


def test_a_folder_that_goes_away_leaves_turns_on_the_set_that_loaded_last(scripted, caplog):
    """A definitions folder unmounted under a running deployment would otherwise fail
    every turn until it came back, though nothing a turn runs is read from it again."""
    kf = _deployment(scripted)
    moved = scripted.definitions.with_name("moved")
    scripted.definitions.rename(moved)

    with caplog.at_level(logging.WARNING, logger=VERSIONS_LOG):
        answers = [_echoed(kf, scripted), _echoed(kf, scripted)]

    assert answers == ["ECHO:x", "ECHO:x"]
    assert [one.name for one in caplog.records].count(VERSIONS_LOG) == 1


def test_a_skill_lock_changed_while_kingfisher_runs_is_in_force_on_the_next_call(cfg):
    """`skill_locks.yaml` sits beside the folders rather than in one, and missed, a lock
    narrowed while kingfisher ran would go on telling the callers it now shuts out."""
    (cfg.skills_dir / "s1").mkdir(parents=True)
    (cfg.skills_dir / "s1" / "SKILL.md").write_text(
        "---\nname: s1\ndescription: A skill.\n---\nDo it.\n", encoding="utf-8"
    )
    locks = cfg.skills_dir.parent / "skill_locks.yaml"
    locks.write_text("- name: s1\n  source_ids: [A, B]\n", encoding="utf-8")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    def shut_to_a() -> frozenset[str]:
        catalogue = kf.catalogue
        return catalogue.skills_shut(caller_holds(catalogue.vocabulary, ["A"]))

    assert shut_to_a() == frozenset()
    locks.write_text("- name: s1\n  source_ids: [B]\n", encoding="utf-8")

    assert shut_to_a() == {"catalogue::s1"}
