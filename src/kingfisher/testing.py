"""The port contracts, as checks a deployment can run against its own adapter.

`SessionStore` is four methods over bytes and its docstring says a bucket is as
good an implementation as a directory. That invitation was unbacked: a
deployment writing one got excellent prose and no way to find out whether it had
got it right, including on the parts where being wrong is a security fault
rather than a bug. This is what makes the invitation checkable.

    from kingfisher.testing import SESSION_STORE_CONTRACT

    @pytest.mark.parametrize("check", SESSION_STORE_CONTRACT, ids=lambda c: c.__name__)
    def test_my_store_keeps_the_contract(check):
        check(lambda: S3SessionStore(bucket="kept", prefix="sessions/"))

**A factory, not a store.** Every check builds its own and most of them write to
it, so one shared instance would make them depend on each other's leftovers and
on the order they ran in.

**No test framework is imported here**, which is what lets this live in the
library rather than in a second distribution: `pip install kingfisher` gains a
module and no test dependency, and the checks run from unittest, pytest, or a
loop in a script. The cost is that failures cannot lean on pytest's assertion
rewriting -- it only applies to test modules and registered plugins, not to a
library somebody imported -- so every check raises `AssertionError` with the
whole story in the message rather than leaving a bare `assert` to say nothing.

Raised rather than asserted for a second reason: `python -O` strips `assert`
outright, and a conformance kit that silently passes while checking nothing is
worse than no kit. `AssertionError` and not a class of our own, so a runner
reports these as failures rather than errors -- and because a new `*Error` in
this package has to be classified as caller-facing or deployment-facing by
`test_every_error_is_classified_by_who_caused_it`, which a test-support type is
neither of.

The checks are the ones `tests/unit/test_session_store.py` had, minus three that
turned out to be about `restore_into` and `keep_from` -- kingfisher's own
functions over a store rather than anything a store must provide -- plus three
for `knows`, which had no test at all and is the method the port calls a
security question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.domain.references import UnsafeReferenceError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.domain.ports import SessionStore

    #: What a check is handed: something that builds a fresh, empty store.
    Factory = Callable[[], SessionStore]
    #: What a check is. Raises `AssertionError` when the store is wrong.
    Check = Callable[[Factory], None]


def _equal(got: object, want: object, *, doing: str) -> None:
    if got != want:
        msg = f"{doing}: expected {want!r}, got {got!r}"
        raise AssertionError(msg)


def _true(got: object, *, doing: str) -> None:
    if got is not True:
        msg = f"{doing}: expected True, got {got!r}"
        raise AssertionError(msg)


def _false(got: object, *, doing: str) -> None:
    if got is not False:
        msg = f"{doing}: expected False, got {got!r}"
        raise AssertionError(msg)


def _refused(call: Callable[[], object], *, doing: str) -> None:
    """Run `call` and require `UnsafeReferenceError`.

    The type is part of the contract rather than a detail of the local store.
    `kingfisher_service/errors.py` maps it to HTTP 400 and the CLI catches it by
    name, so a store raising a plain `ValueError` turns a refused path into a
    500 -- an operator paged for what is a caller's malformed request. It is
    exported as `kingfisher.UnsafeReferenceError` precisely so an adapter
    outside this package can raise the same one.
    """
    try:
        call()
    except UnsafeReferenceError:
        return
    except Exception as wrong:
        msg = (
            f"{doing}: expected UnsafeReferenceError, got "
            f"{type(wrong).__name__}: {wrong}. Import it from `kingfisher` -- the "
            f"service maps that type to 400 and anything else becomes a 500"
        )
        raise AssertionError(msg) from wrong
    msg = f"{doing}: expected UnsafeReferenceError, nothing was raised"
    raise AssertionError(msg)


# -- what a store holds -----------------------------------------------------


def what_was_saved_comes_back(make: Factory) -> None:
    """The whole point, and the shape: paths relative to the session root.

    The same vocabulary `artifacts()` returns, deliberately. A caller diffing
    one turn against the last needs names it can compare, and an absolute path
    names a machine rather than a file.
    """
    store = make()
    kept: Mapping[str, bytes] = {"derived/report.md": b"hello", "memory/notes.md": b"note"}
    store.save("s1", kept)

    _equal(dict(store.fetch("s1")), dict(kept), doing="fetching what was just saved")


def a_session_never_seen_is_empty_rather_than_an_error(make: Factory) -> None:
    """A first turn has nothing to restore, and that is the common case.

    Raising here would make every caller write the same `try` around the one
    path that always happens.
    """
    store = make()

    _equal(dict(store.fetch("never-opened")), {}, doing="fetching an unknown session")


def saving_merges_rather_than_mirrors(make: Factory) -> None:
    """What lets a caller send only the files that changed.

    A mirror would mean every save costs the whole session. The price of merging
    is that nothing here can delete -- which is what `forget` is for, and why
    deletion is a separate verb rather than an omission.
    """
    store = make()
    store.save("s1", {"derived/a.md": b"one", "derived/b.md": b"two"})
    store.save("s1", {"derived/a.md": b"changed"})

    _equal(
        dict(store.fetch("s1")),
        {"derived/a.md": b"changed", "derived/b.md": b"two"},
        doing="saving twice, the second call naming one file of two",
    )


def nesting_survives_a_round_trip(make: Factory) -> None:
    """A session's keys nest several levels -- uploaded definitions land under a
    folder per delegate, and `/derived` is whatever the agent decided to make.

    A store that flattened its keys would lose which folder a file belonged to,
    and one built on an object bucket is exactly the shape that might: prefixes
    are not directories, and a store splitting on the last separator quietly
    collapses two files into one.
    """
    store = make()
    deep: Mapping[str, bytes] = {"derived/reports/2026/q1/summary.md": b"deep"}
    store.save("s1", deep)

    _equal(dict(store.fetch("s1")), dict(deep), doing="saving a deeply nested key")


def bytes_are_returned_unchanged(make: Factory) -> None:
    """Not text. A session holds PDFs and images, and a store that decoded on the
    way through would corrupt the first one it met.

    All 256 byte values, because the ones that break are never the printable
    ones -- a store that round-trips through UTF-8, or through a JSON column,
    fails here and nowhere else.
    """
    store = make()
    payload = bytes(range(256))
    store.save("s1", {"data/raw.bin": payload})

    _equal(store.fetch("s1")["data/raw.bin"], payload, doing="round-tripping 256 byte values")


# -- what a store keeps apart -----------------------------------------------


def one_session_cannot_read_another(make: Factory) -> None:
    """The isolation `build_backend` gets from rooting a backend at a session,
    which a store has to provide for itself: there are no directories here to be
    unable to route across, only whatever the implementation does with an id."""
    store = make()
    store.save("s1", {"derived/mine.md": b"mine"})
    store.save("s2", {"derived/theirs.md": b"theirs"})

    _equal(dict(store.fetch("s1")), {"derived/mine.md": b"mine"}, doing="fetching one of two")
    _equal(dict(store.fetch("s2")), {"derived/theirs.md": b"theirs"}, doing="fetching the other")


#: Ids that name somewhere other than one session. `""` is here because an empty
#: id joined to a root *is* the root, so a store that skips the check hands back
#: every session it holds.
ESCAPING_IDS = ("../elsewhere", "/etc", "..", "")

#: Keys that climb out of the session they were saved under.
ESCAPING_KEYS = ("../outside.md", "/etc/passwd")


def a_session_id_that_names_somewhere_else_is_refused(make: Factory) -> None:
    """Checked on all four methods, because all four take the id.

    "The caller cannot reach this argument" is a claim about every call site
    rather than about the one in front of you. `Sessions._exists` already passes
    a *supplied* id to `knows`, so the claim is not even true today.
    """
    for bad in ESCAPING_IDS:
        store = make()
        _refused(lambda: store.fetch(bad), doing=f"fetch({bad!r})")  # noqa: B023
        _refused(lambda: store.save(bad, {"a.md": b"x"}), doing=f"save({bad!r}, ...)")  # noqa: B023
        _refused(lambda: store.knows(bad), doing=f"knows({bad!r})")  # noqa: B023
        _refused(lambda: store.forget(bad), doing=f"forget({bad!r})")  # noqa: B023


def a_filename_that_climbs_out_is_refused(make: Factory) -> None:
    """The second half. A key is a path, and a path from anywhere can climb.

    Separate from the id, because they arrive from different places: an id comes
    from the caller and a key from whatever kingfisher collected out of the
    session, so a store that checked only one is a store that checked the wrong
    one on some future call path.
    """
    for bad in ESCAPING_KEYS:
        store = make()
        _refused(lambda: store.save("s1", {bad: b"x"}), doing=f"save('s1', {{{bad!r}: ...}})")  # noqa: B023


# -- what a store admits to knowing -----------------------------------------


def a_store_knows_what_it_kept(make: Factory) -> None:
    """`knows` had no test anywhere before this kit, and it is the method the
    port calls *"a security question rather than a convenience one"*.

    `Sessions._exists` asks it whether a supplied id may resume, so it is the
    proof that a session belongs to whoever named it.
    """
    store = make()
    store.save("s1", {"derived/a.md": b"one"})

    _true(store.knows("s1"), doing="knows() for a session just saved")


def a_store_does_not_know_what_it_never_kept(make: Factory) -> None:
    """The half that is a security fault when it is wrong.

    *"A caller cannot make a store know an id it never saved."* A store
    answering `True` too readily -- one built on a bucket that reports a prefix
    as present, say -- lets a caller resume a session they invented, which is
    the whole of what the id is supposed to prove.
    """
    store = make()
    store.save("s1", {"derived/a.md": b"one"})

    _false(store.knows("never-opened"), doing="knows() for an id never saved")


def forgetting_removes_everything_and_says_nothing_twice(make: Factory) -> None:
    """`reap`'s side of the port, and the only granularity it ever needs.

    Idempotent because a janitor runs on its own schedule against a list it read
    earlier, so a session already gone is the ordinary case rather than a fault.
    """
    store = make()
    store.save("s1", {"derived/a.md": b"one"})
    store.forget("s1")
    store.forget("s1")

    _equal(dict(store.fetch("s1")), {}, doing="fetching after forget")
    _false(store.knows("s1"), doing="knows() after forget")


def forgetting_one_session_leaves_the_others(make: Factory) -> None:
    """`reap` sweeps expired sessions one at a time while others are live.

    A store implementing `forget` as a prefix delete gets this wrong the first
    time two ids share a prefix, and every test above would still pass.
    """
    store = make()
    store.save("s1", {"derived/a.md": b"one"})
    store.save("s1-extra", {"derived/b.md": b"two"})
    store.forget("s1")

    _equal(dict(store.fetch("s1-extra")), {"derived/b.md": b"two"}, doing="fetching a neighbour")
    _true(store.knows("s1-extra"), doing="knows() for a neighbour of a forgotten session")


#: Every check a `SessionStore` must pass, in the order a reader should meet
#: them: what it holds, what it keeps apart, what it admits to knowing.
#:
#: A tuple rather than a module-level scan, so adding a helper to this file
#: cannot silently become a contract term.
SESSION_STORE_CONTRACT: tuple[Check, ...] = (
    what_was_saved_comes_back,
    a_session_never_seen_is_empty_rather_than_an_error,
    saving_merges_rather_than_mirrors,
    nesting_survives_a_round_trip,
    bytes_are_returned_unchanged,
    one_session_cannot_read_another,
    a_session_id_that_names_somewhere_else_is_refused,
    a_filename_that_climbs_out_is_refused,
    a_store_knows_what_it_kept,
    a_store_does_not_know_what_it_never_kept,
    forgetting_removes_everything_and_says_nothing_twice,
    forgetting_one_session_leaves_the_others,
)
