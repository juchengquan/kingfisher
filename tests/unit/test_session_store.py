"""Where a session's files go when the machine may not keep them.

The missing half of a symmetry the domain already commits to: `FileStore` is how
bytes arrive, and until now nothing was how they leave. `artifacts()` hands back
a list of paths, which serves a caller sharing the host and nobody else.

These are about the port's contract rather than about a directory. Everything
here should read the same against a bucket.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.references import UnsafeReferenceError
from kingfisher.infrastructure.session_store import LocalSessionStore


@pytest.fixture
def store(tmp_path):
    return LocalSessionStore(tmp_path / "kept")


def test_what_was_saved_comes_back(store):
    """The whole point, and the shape: paths relative to the session root.

    The same vocabulary `artifacts()` returns, deliberately. A caller diffing
    one turn against the last needs names it can compare, and an absolute path
    names a machine rather than a file.
    """
    store.save("s1", {"derived/report.md": b"hello", "memory/notes.md": b"note"})

    assert store.fetch("s1") == {"derived/report.md": b"hello", "memory/notes.md": b"note"}


def test_a_session_the_store_has_never_seen_is_empty_rather_than_an_error(store):
    """A first turn has nothing to restore, and that is the common case.

    Raising here would make every caller write the same `try` around the one
    path that always happens.
    """
    assert store.fetch("never-opened") == {}


def test_saving_merges_rather_than_mirrors(store):
    """What lets a caller send only the files that changed.

    A mirror would mean every save costs the whole session, which is the cost
    the design is trying not to pay on each tool call. The price of merging is
    that nothing here can delete -- which is what `forget` is for, and why
    deletion is a separate verb rather than an omission.
    """
    store.save("s1", {"derived/a.md": b"one", "derived/b.md": b"two"})
    store.save("s1", {"derived/a.md": b"changed"})

    assert store.fetch("s1") == {"derived/a.md": b"changed", "derived/b.md": b"two"}


def test_forgetting_removes_everything_and_says_nothing_twice(store):
    """`reap`'s side of the port, and the only granularity it ever needs.

    Idempotent because a janitor runs on its own schedule against a list it
    read earlier, so a session already gone is the ordinary case rather than a
    fault.
    """
    store.save("s1", {"derived/a.md": b"one"})
    store.forget("s1")
    store.forget("s1")

    assert store.fetch("s1") == {}


def test_one_session_cannot_read_another(store):
    """The isolation `build_backend` gets from rooting a backend at a session,
    which a store has to provide for itself: there are no directories here to
    be unable to route across."""
    store.save("s1", {"derived/mine.md": b"mine"})
    store.save("s2", {"derived/theirs.md": b"theirs"})

    assert store.fetch("s1") == {"derived/mine.md": b"mine"}
    assert store.fetch("s2") == {"derived/theirs.md": b"theirs"}


@pytest.mark.parametrize("escape", ["../elsewhere", "/etc", "..", ""])
def test_a_session_id_that_names_somewhere_else_is_refused(store, escape):
    """Checked even though session ids are kingfisher's own today.

    "The caller cannot reach this argument" is a claim about every call site
    rather than about the one in front of you, and a store handed an id from a
    request later would be checked by nobody. `FileStore`'s refs are checked
    for exactly this reason and those *are* caller-supplied.
    """
    with pytest.raises(UnsafeReferenceError):
        store.fetch(escape)


@pytest.mark.parametrize("escape", ["../outside.md", "/etc/passwd"])
def test_a_filename_that_climbs_out_is_refused(store, escape):
    """The second half. A key is a path, and a path from anywhere can climb."""
    with pytest.raises(UnsafeReferenceError):
        store.save("s1", {escape: b"x"})


def test_nesting_survives_a_round_trip(store):
    """Sessions hold `subagents/redactor/skills/redaction/SKILL.md` and deeper.
    A store that flattened would lose which folder a definition belonged to."""
    deep = {"derived/reports/2026/q1/summary.md": b"deep"}
    store.save("s1", deep)

    assert store.fetch("s1") == deep


def test_bytes_are_returned_unchanged(store):
    """Not text. A session holds PDFs and images, and a store that decoded
    would corrupt the first one it met."""
    payload = bytes(range(256))
    store.save("s1", {"data/raw.bin": payload})

    assert store.fetch("s1")["data/raw.bin"] == payload


# -- what the store is for --------------------------------------------------


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
