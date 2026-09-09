"""Where a session's files go when the machine may not keep them."""

from __future__ import annotations

import pytest

from kingfisher.infrastructure.session_store import LocalSessionStore
from kingfisher.testing import SESSION_STORE_CONTRACT


@pytest.fixture
def store(tmp_path):
    return LocalSessionStore(tmp_path / "kept")


def test_the_local_store_keeps_the_port_contract(tmp_path):
    """The kit, run against the store it was extracted from.

    One counter across every check rather than one `tmp_path` each, and the counter is
    what keeps them apart: `make` takes a directory of its own every time it is called,
    so no check opens what the last one left behind.
    """
    made = 0

    def make():
        nonlocal made
        made += 1
        return LocalSessionStore(tmp_path / f"kept-{made}")

    for check in SESSION_STORE_CONTRACT:
        check(make)


def test_the_contract_is_not_quietly_empty():
    """The kit is a tuple somebody maintains by hand, so it can be emptied by an edit
    that looks like tidying -- and the rule above would then walk nothing and pass.
    """
    assert len(SESSION_STORE_CONTRACT) >= 12
    assert all(callable(check) for check in SESSION_STORE_CONTRACT)


# -- kingfisher's own functions over a store --------------------------------
#
# Not part of the contract, and the distinction took a moment to see: these
# exercise `restore_into` and `keep_from`, which any store is *passed to*. A
# deployment's store does not implement them and cannot fail them except by
# failing the contract above first.


def test_a_session_survives_losing_its_directory(store, tmp_path):
    """The prototype's whole claim, at the level the port can be tested."""
    from kingfisher.infrastructure.session_store import keep_from, restore_into

    first = tmp_path / "before"
    (first / "derived").mkdir(parents=True)
    (first / "derived" / "report.md").write_text("forty rows", encoding="utf-8")
    keep_from(store, "s1", first, ["derived/report.md"])

    # The machine goes. Nothing carries over but the store.
    second = tmp_path / "after"
    second.mkdir()
    restored = restore_into(store, "s1", second)

    assert restored == ("derived/report.md",)
    assert (second / "derived" / "report.md").read_text(encoding="utf-8") == "forty rows"


def test_restoring_leaves_a_file_that_is_already_there(store, tmp_path):
    """The case that has to stay cheap: a host keeping its own disk, where every turn
    after the first finds nothing to do.
    """
    from kingfisher.infrastructure.session_store import restore_into

    store.save("s1", {"memory/notes.md": b"from the store"})
    live = tmp_path / "live"
    (live / "memory").mkdir(parents=True)
    (live / "memory" / "notes.md").write_text("written this turn", encoding="utf-8")

    assert restore_into(store, "s1", live) == ()
    assert (live / "memory" / "notes.md").read_text(encoding="utf-8") == "written this turn"


def test_keeping_skips_a_file_that_has_gone(store, tmp_path):
    """The list was taken a moment ago and `execute` can delete between then and now."""
    from kingfisher.infrastructure.session_store import keep_from

    live = tmp_path / "live"
    (live / "derived").mkdir(parents=True)
    (live / "derived" / "here.md").write_text("here", encoding="utf-8")

    keep_from(store, "s1", live, ["derived/here.md", "derived/deleted.md"])

    assert store.fetch("s1") == {"derived/here.md": b"here"}
