"""The port contracts, as checks a deployment can run against its own adapter.

Four of them, one per port a deployment realistically replaces:
`SESSION_STORE_CONTRACT`, `FILE_STORE_CONTRACT`, `SESSION_ROOT_CONTRACT` and
`COMMAND_RUNNER_CONTRACT`.

**A factory, not a store.** Every check builds its own and most of them write to it,
so one shared instance would make them depend on each other's leftovers and on the
order they ran in.

**No test framework is imported here**, which is what lets this live in the library
rather than in a second distribution: `pip install kingfisher` gains a module and no
test dependency, and the checks run from unittest, pytest, or a loop in a script. The
cost is that failures cannot lean on pytest's assertion rewriting -- it only applies
to test modules and registered plugins, not to a library somebody imported -- so
every check raises `AssertionError` with the whole story in the message rather than
leaving a bare `assert` to say nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.domain.references import UnknownReferenceError, UnsafeReferenceError

# `Mapping` and `Path` at runtime rather than under `TYPE_CHECKING`: both are
# `isinstance` arguments rather than only annotations. The mistakes they catch --
# a file store handing back bare bytes, a session root yielding a `str` -- are
# invisible to an annotation nobody runs.

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.domain.ports import (
        CommandRunner,
        FileStore,
        SessionRoot,
        SessionStore,
    )

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


def _raises(call: Callable[[], object], expected: type[Exception], *, doing: str) -> None:
    """Run `call` and require exactly `expected`.

    The *type* is part of the contract rather than a detail of the shipped
    adapters, and `kingfisher_service/errors.py` is where that becomes true: it
    maps each of these to its own status and anything unrecognised to 500. So a
    store raising a plain `ValueError` for a hostile ref, or a `FileNotFoundError`
    for a missing one, turns a caller's bad request into an operator's page.
    Both are exported from `kingfisher` precisely so an adapter outside this
    package can raise the same ones.
    """
    try:
        call()
    except expected:
        return
    except Exception as wrong:
        msg = (
            f"{doing}: expected {expected.__name__}, got "
            f"{type(wrong).__name__}: {wrong}. Import it from `kingfisher` -- the "
            f"service maps that type to a status of its own and anything else to 500"
        )
        raise AssertionError(msg) from wrong
    msg = f"{doing}: expected {expected.__name__}, nothing was raised"
    raise AssertionError(msg)


def _refused(call: Callable[[], object], *, doing: str) -> None:
    """`_raises` for the one type the session store deals in."""
    _raises(call, UnsafeReferenceError, doing=doing)


def _must_be(value: object, kind: type, *, doing: str, why: str) -> None:
    """Require `value` to be a `kind`, saying what the shape is for."""
    if isinstance(value, kind):
        return
    msg = f"{doing}: {why}. Got {type(value).__name__}"
    raise AssertionError(msg)


# -- what a store holds -----------------------------------------------------


def what_was_saved_comes_back(make: Factory) -> None:
    """The whole point, and the shape: paths relative to the session root."""
    store = make()
    kept: Mapping[str, bytes] = {"derived/report.md": b"hello", "memory/notes.md": b"note"}
    store.save("s1", kept)

    _equal(dict(store.fetch("s1")), dict(kept), doing="fetching what was just saved")


def a_session_never_seen_is_empty_rather_than_an_error(make: Factory) -> None:
    """A first turn has nothing to restore, and that is the common case."""
    store = make()

    _equal(dict(store.fetch("never-opened")), {}, doing="fetching an unknown session")


def saving_merges_rather_than_mirrors(make: Factory) -> None:
    """What lets a caller send only the files that changed."""
    store = make()
    store.save("s1", {"derived/a.md": b"one", "derived/b.md": b"two"})
    store.save("s1", {"derived/a.md": b"changed"})

    _equal(
        dict(store.fetch("s1")),
        {"derived/a.md": b"changed", "derived/b.md": b"two"},
        doing="saving twice, the second call naming one file of two",
    )


def nesting_survives_a_round_trip(make: Factory) -> None:
    """A session's keys nest several levels -- uploaded definitions land under a folder
    per delegate, and `/derived` is whatever the agent decided to make.
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
    """Checked on all four methods, because all four take the id."""
    for bad in ESCAPING_IDS:
        store = make()
        _refused(lambda: store.fetch(bad), doing=f"fetch({bad!r})")  # noqa: B023
        _refused(lambda: store.save(bad, {"a.md": b"x"}), doing=f"save({bad!r}, ...)")  # noqa: B023
        _refused(lambda: store.knows(bad), doing=f"knows({bad!r})")  # noqa: B023
        _refused(lambda: store.forget(bad), doing=f"forget({bad!r})")  # noqa: B023


def a_filename_that_climbs_out_is_refused(make: Factory) -> None:
    """The second half. A key is a path, and a path from anywhere can climb."""
    for bad in ESCAPING_KEYS:
        store = make()
        _refused(lambda: store.save("s1", {bad: b"x"}), doing=f"save('s1', {{{bad!r}: ...}})")  # noqa: B023


# -- what a store admits to knowing -----------------------------------------


def a_store_knows_what_it_kept(make: Factory) -> None:
    """`knows` had no test anywhere before this kit, and it is the method the port calls
    *"a security question rather than a convenience one"*.
    """
    store = make()
    store.save("s1", {"derived/a.md": b"one"})

    _true(store.knows("s1"), doing="knows() for a session just saved")


def a_store_does_not_know_what_it_never_kept(make: Factory) -> None:
    """The half that is a security fault when it is wrong."""
    store = make()
    store.save("s1", {"derived/a.md": b"one"})

    _false(store.knows("never-opened"), doing="knows() for an id never saved")


def forgetting_removes_everything_and_says_nothing_twice(make: Factory) -> None:
    """`reap`'s side of the port, and the only granularity it ever needs."""
    store = make()
    store.save("s1", {"derived/a.md": b"one"})
    store.forget("s1")
    store.forget("s1")

    _equal(dict(store.fetch("s1")), {}, doing="fetching after forget")
    _false(store.knows("s1"), doing="knows() after forget")


def forgetting_one_session_leaves_the_others(make: Factory) -> None:
    """`reap` sweeps expired sessions one at a time while others are live."""
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


# -- the file store ---------------------------------------------------------
#
# A different argument, and the difference is the port rather than a
# preference. `SessionStore` writes, so each check above builds an empty one and
# fills it. `FileStore` is one method and that method reads: there is no way for
# a check to put a file where a store will find it, because the port deliberately
# has no verb for doing so -- kingfisher never writes to a file store, it only
# resolves what a caller already put there.
#
# So the deployment plants, by whatever means its own store has, and hands over
# what it planted.


@dataclass(frozen=True)
class Planted:
    """One ref a store resolves, and what it resolves to."""

    store: FileStore
    #: A ref this store resolves.
    ref: str
    #: Exactly what `fetch(ref)` must return.
    contents: Mapping[str, bytes]
    #: A ref this store does not hold.
    missing: str = "kingfisher-contract-no-such-ref"


def what_the_ref_names_comes_back(planted: Planted) -> None:
    """The whole point. A mapping, keyed by path relative to the ref, because
    one ref may name a small bundle rather than a single file."""
    got = planted.store.fetch(planted.ref)

    _equal(dict(got), dict(planted.contents), doing=f"fetch({planted.ref!r})")


def the_result_is_bytes_under_string_keys(planted: Planted) -> None:
    """The shape, checked apart from the value, because the likely wrong guess returns
    the right *content* in the wrong container.
    """
    doing = f"fetch({planted.ref!r})"
    got = planted.store.fetch(planted.ref)
    _must_be(
        got,
        Mapping,
        doing=doing,
        why="one ref may name a bundle, so the answer is always {path: bytes} -- "
        "even for a single file",
    )
    for key, value in got.items():
        _must_be(key, str, doing=doing, why="files are keyed by their path, as a string")
        _must_be(
            value,
            bytes,
            doing=f"{doing}[{key!r}]",
            why="values are bytes -- a store that decodes corrupts the first file "
            "that is not text",
        )


def a_ref_the_store_does_not_hold_is_refused(planted: Planted) -> None:
    """`UnknownReferenceError`, and the type is the contract.

    The port says so outright -- *"a bare `FileNotFoundError` cannot be told
    from the deployment's own disk being wrong, and would answer 500 to a
    caller's typo"*. `kingfisher_service/errors.py` is where that becomes true:
    it maps this type to 404 and anything unrecognised to 500, so the difference
    between a mistyped ref and a page for the on-call is this exception's class.
    """
    _raises(
        lambda: planted.store.fetch(planted.missing),
        UnknownReferenceError,
        doing=f"fetch({planted.missing!r}), a ref the store does not hold",
    )


def a_ref_that_names_somewhere_else_is_refused(planted: Planted) -> None:
    """`UnsafeReferenceError`, for a ref that climbs out or names an absolute path."""
    for bad in ESCAPING_REFS:
        _raises(
            lambda: planted.store.fetch(bad),  # noqa: B023
            UnsafeReferenceError,
            doing=f"fetch({bad!r})",
        )


#: Refs that name somewhere other than the store's own contents.
ESCAPING_REFS = ("../outside.csv", "/etc/passwd", "..")

#: Every check a `FileStore` must pass. Shorter than the session store's because
#: the port is: one method, and half of what it must get right is which
#: exception it raises.
FILE_STORE_CONTRACT: tuple[Callable[[Planted], None], ...] = (
    what_the_ref_names_comes_back,
    the_result_is_bytes_under_string_keys,
    a_ref_the_store_does_not_hold_is_refused,
    a_ref_that_names_somewhere_else_is_refused,
)


# -- the session root -------------------------------------------------------
#
# A factory again, like the session store: these checks call `hold` themselves
# and several want a provider that has not been used yet.
#
# These do more than read. A check creates directories inside what it is handed,
# because that is what kingfisher does with it -- `ensure_session_layout` runs
# `mkdir(parents=True)` inside the yielded path before anything else touches the
# session. A provider that cannot be written into cannot serve a turn, and there
# is no way to find that out without writing.

#: Session ids the checks hold. Two, because the property that matters most is
#: that they do not collide.
CONTRACT_SESSIONS = ("kingfisher-contract-a", "kingfisher-contract-b")


def hold_yields_a_path(make: Callable[[], SessionRoot]) -> None:
    """A `Path`, not a string."""
    with make().hold(CONTRACT_SESSIONS[0]) as directory:
        _must_be(
            directory,
            Path,
            doing="hold(...)",
            why="the yielded value is a pathlib.Path, which kingfisher joins names onto",
        )


def kingfisher_can_lay_a_session_out_inside_it(make: Callable[[], SessionRoot]) -> None:
    """The directory need not exist, and must be creatable."""
    with make().hold(CONTRACT_SESSIONS[0]) as directory:
        probe = Path(directory) / "data" / "nested"
        try:
            probe.mkdir(parents=True, exist_ok=True)
        except OSError as refused:
            msg = (
                f"hold(...) yielded {directory}, which kingfisher cannot lay a session "
                f"out inside ({refused}). It runs mkdir(parents=True) there before the "
                f"turn starts"
            )
            raise AssertionError(msg) from refused


def a_child_of_the_session_stays_inside_it(make: Callable[[], SessionRoot]) -> None:
    """A session cannot be composed out of links to shared content."""
    with make().hold(CONTRACT_SESSIONS[0]) as directory:
        root = Path(directory).resolve()
        child = Path(directory) / "data"
        child.mkdir(parents=True, exist_ok=True)
        if root not in child.resolve().parents:
            msg = (
                f"hold(...) yielded a directory whose child {child.name!r} resolves to "
                f"{child.resolve()}, outside the session root {root}. Containment is "
                f"checked against the resolved root, so every access to it is refused"
            )
            raise AssertionError(msg)


def two_sessions_are_two_directories(make: Callable[[], SessionRoot]) -> None:
    """The isolation the whole port rests on."""
    root = make()
    first, second = CONTRACT_SESSIONS
    with root.hold(first) as one, root.hold(second) as two:
        if Path(one).resolve() == Path(two).resolve():
            msg = (
                f"hold({first!r}) and hold({second!r}) both yielded "
                f"{Path(one).resolve()}. Two sessions in one directory is two callers "
                f"reading each other's files"
            )
            raise AssertionError(msg)


def a_session_can_be_held_again(make: Callable[[], SessionRoot]) -> None:
    """Once per turn, and a session has many turns."""
    root = make()
    with root.hold(CONTRACT_SESSIONS[0]) as first:
        first_path = Path(first).resolve()
    with root.hold(CONTRACT_SESSIONS[0]) as second:
        _equal(
            Path(second).resolve(),
            first_path,
            doing=f"holding {CONTRACT_SESSIONS[0]!r} a second time",
        )


def a_failed_turn_still_leaves_the_hold(make: Callable[[], SessionRoot]) -> None:
    """*"Released when the turn ends however it ended"*, and the half that bites."""
    held = make().hold(CONTRACT_SESSIONS[0])
    held.__enter__()
    # Asked of `__exit__` directly rather than by raising inside a `with`, which
    # is the same question one layer down and answers it without inventing an
    # exception type to throw. A correct manager either re-raises what it was
    # handed or returns something falsy; only `True` means swallowed.
    try:
        swallowed = held.__exit__(ValueError, ValueError("a turn that failed"), None)
    except ValueError:
        return
    if swallowed:
        msg = (
            "hold(...).__exit__ returned True, so an exception raised inside the "
            "block would be swallowed. A turn that failed would be reported as one "
            "that succeeded, and whatever was held would still be held"
        )
        raise AssertionError(msg)


#: Every check a `SessionRoot` must pass. These create directories inside what
#: the provider yields, because that is what kingfisher does with it.
SESSION_ROOT_CONTRACT: tuple[Callable[[Callable[[], SessionRoot]], None], ...] = (
    hold_yields_a_path,
    kingfisher_can_lay_a_session_out_inside_it,
    a_child_of_the_session_stays_inside_it,
    two_sessions_are_two_directories,
    a_session_can_be_held_again,
    a_failed_turn_still_leaves_the_hold,
)


# -- the command runner -----------------------------------------------------
#
# These run commands. There is no way to check that a runner runs things without
# running things, and a runner that ships them to another machine will be as
# slow here as it is in a turn -- one of the checks waits for a timeout on
# purpose. Run them where you would run an integration test.
#
# They assume a POSIX-ish shell, which `execute` already assumes: the prompt
# hands the model shell commands and the backend passes them through.

#: What the checks run. Kept together so a deployment whose runner needs
#: something else can see exactly what it is being asked for.
SUCCEEDS = "echo kingfisher-contract"
FAILS = "exit 3"
SLEEPS = "sleep 30"


def a_runner_says_where_it_runs(make: Callable[[], CommandRunner]) -> None:
    """`local` decides whether the fence is applied, so it is read before a command is."""
    runner = make()
    declared = getattr(runner, "local", True)
    _must_be(
        declared,
        bool,
        doing="reading `local` off the runner",
        why="it says whether the command runs on this machine, and a non-boolean is "
        "truthy in ways that quietly keep or lose the fence",
    )


def a_command_that_works_reports_that_it_worked(make: Callable[[], CommandRunner]) -> None:
    """Exit code zero, and the output the command wrote."""
    result = make().run(SUCCEEDS)

    _equal(result.exit_code, 0, doing=f"run({SUCCEEDS!r}).exit_code")
    if "kingfisher-contract" not in result.output:
        msg = (
            f"run({SUCCEEDS!r}).output does not contain what the command printed: "
            f"{result.output!r}. The model reads this as the tool result"
        )
        raise AssertionError(msg)


def a_command_that_fails_is_a_result_and_not_an_exception(
    make: Callable[[], CommandRunner],
) -> None:
    """A non-zero exit is ordinary. The agent runs commands that fail and reads
    the code to decide what to do next, so raising here turns a normal tool
    result into a turn that ended."""
    result = make().run(FAILS)

    _equal(result.exit_code, 3, doing=f"run({FAILS!r}).exit_code")


def a_result_is_shaped_the_way_a_caller_reads_it(make: Callable[[], CommandRunner]) -> None:
    """`exit_code` is not optional, and that is a decision the port records:
    *"the harness's equivalent allows `None`, and a caller deciding whether a
    command worked has nothing to do with that but guess."*"""
    result = make().run(SUCCEEDS)

    _must_be(
        result.exit_code,
        int,
        doing=f"run({SUCCEEDS!r}).exit_code",
        why="it is always a number -- None would leave a caller guessing whether the "
        "command worked",
    )
    _must_be(
        result.output,
        str,
        doing=f"run({SUCCEEDS!r}).output",
        why="output is text, decoded by the runner",
    )
    _must_be(
        getattr(result, "truncated", False),
        bool,
        doing=f"run({SUCCEEDS!r}).truncated",
        why="a caller cannot tell a cut result from a short one unless the runner says",
    )


def a_timeout_is_a_result_and_not_an_exception(make: Callable[[], CommandRunner]) -> None:
    """The one most likely to be got wrong, because every timeout API raises.

    `subprocess.run(timeout=...)` raises `TimeoutExpired`, so a runner written the
    obvious way propagates it -- and the port says otherwise: *"a timeout is a
    result, not an exception: `exit_code` 124, the shell's own, with output saying
    so. Raising would make every runner's failure the model's problem rather than a
    tool result it can read and retry."*

    124 rather than any non-zero code, because it is what `timeout(1)` returns and
    the number a reader of the output will recognise.
    """
    try:
        result = make().run(SLEEPS, timeout=1)
    except Exception as raised:
        msg = (
            f"run({SLEEPS!r}, timeout=1) raised {type(raised).__name__}: {raised}. A "
            f"timeout is a result -- exit_code 124 -- so the model can read it and "
            f"retry rather than the turn ending"
        )
        raise AssertionError(msg) from raised

    _equal(result.exit_code, 124, doing=f"run({SLEEPS!r}, timeout=1).exit_code")


#: Every check a `CommandRunner` must pass. These run commands, and one waits
#: for a timeout.
COMMAND_RUNNER_CONTRACT: tuple[Callable[[Callable[[], CommandRunner]], None], ...] = (
    a_runner_says_where_it_runs,
    a_command_that_works_reports_that_it_worked,
    a_command_that_fails_is_a_result_and_not_an_exception,
    a_result_is_shaped_the_way_a_caller_reads_it,
    a_timeout_is_a_result_and_not_an_exception,
)
