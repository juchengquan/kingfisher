"""Where a session's files go when the machine may not keep them.

The missing half of a symmetry the domain already commits to: `FileStore` is how
bytes arrive, and until now nothing was how they leave. `artifacts()` hands back
a list of paths, which serves a caller sharing the host and nobody else.

This file used to hold the port's contract inline, above a fixture hardwired to
`LocalSessionStore`, under a docstring saying *"everything here should read the
same against a bucket"*. It could not: nothing outside this repository could run
a line of it. The contract now lives in `kingfisher.testing` where a deployment
can import it, and what stays here is the two things that are genuinely local --
proving the kit against the implementation it was written from, and testing
`restore_into` and `keep_from`, which are kingfisher's own functions over a store
rather than anything a store has to provide.
"""

from __future__ import annotations

import pytest

from kingfisher.infrastructure.session_store import LocalSessionStore
from kingfisher.testing import SESSION_STORE_CONTRACT


@pytest.fixture
def store(tmp_path):
    return LocalSessionStore(tmp_path / "kept")


@pytest.mark.parametrize("check", SESSION_STORE_CONTRACT, ids=lambda c: c.__name__)
def test_the_local_store_keeps_the_port_contract(check, tmp_path):
    """The kit, run against the store it was extracted from.

    Which is the only thing that keeps the kit honest. A contract nothing
    satisfies is a contract nobody has read, and a deployment's first sight of a
    failing check should not be the first time anybody ran it.

    A counter rather than a shared directory, because several checks build more
    than one store and two of them landing on the same root would let one
    check's leftovers answer another's `fetch`.
    """
    made = 0

    def make():
        nonlocal made
        made += 1
        return LocalSessionStore(tmp_path / f"kept-{made}")

    check(make)


def test_the_contract_is_not_quietly_empty():
    """The kit is a tuple somebody maintains by hand, so it can be emptied by an
    edit that looks like tidying -- and every parametrised test above would then
    pass by not existing. This is the guard that notices."""
    assert len(SESSION_STORE_CONTRACT) >= 12
    assert all(callable(check) for check in SESSION_STORE_CONTRACT)


# -- kingfisher's own functions over a store --------------------------------
#
# Not part of the contract, and the distinction took a moment to see: these
# exercise `restore_into` and `keep_from`, which any store is *passed to*. A
# deployment's store does not implement them and cannot fail them except by
# failing the contract above first.


def test_a_session_survives_losing_its_directory(store, tmp_path):
    """The prototype's whole claim, at the level the port can be tested.

    A turn produced files, the machine went away, and a new directory has
    nothing in it. `restore_into` is what stands between that and a session
    which has forgotten its own work.
    """
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
    """The case that has to stay cheap: a host keeping its own disk, where every
    turn after the first finds nothing to do.

    Also the case that has to stay *correct*. A file present locally and
    different in the store means a turn was interrupted between writing and
    saving, and nothing here can tell that from a file this turn has not saved
    yet -- so the local copy wins and the store catches up at the end.
    """
    from kingfisher.infrastructure.session_store import restore_into

    store.save("s1", {"memory/notes.md": b"from the store"})
    live = tmp_path / "live"
    (live / "memory").mkdir(parents=True)
    (live / "memory" / "notes.md").write_text("written this turn", encoding="utf-8")

    assert restore_into(store, "s1", live) == ()
    assert (live / "memory" / "notes.md").read_text(encoding="utf-8") == "written this turn"


def test_keeping_skips_a_file_that_has_gone(store, tmp_path):
    """The list was taken a moment ago and `execute` can delete between then and
    now. Failing a turn's persistence over one absent file is a worse answer
    than keeping the rest."""
    from kingfisher.infrastructure.session_store import keep_from

    live = tmp_path / "live"
    (live / "derived").mkdir(parents=True)
    (live / "derived" / "here.md").write_text("here", encoding="utf-8")

    keep_from(store, "s1", live, ["derived/here.md", "derived/deleted.md"])

    assert store.fetch("s1") == {"derived/here.md": b"here"}
