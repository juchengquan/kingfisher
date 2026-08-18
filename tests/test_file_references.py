"""Files a caller names but cannot hand over.

`Request.inputs` and `data` are host paths, which a remote caller does not have.
So the same two arrive as ids resolved by a `FileStore` the deployment wired --
the decision `skill_refs` made one phase earlier, for the same reason: kingfisher
never receives a payload over its own wire.

The refusals matter more than the happy path. A ref is whatever a caller wrote,
so most of what a store does is decline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher import (
    Kingfisher,
    LocalFileStore,
    Request,
    UnknownReferenceError,
    UnsafeReferenceError,
)
from kingfisher.domain.references import within
from kingfisher.infrastructure.files import MissingStoreError, fetch_refs
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    (root / "sales.csv").write_bytes(b"a,b\n1,2\n")
    (root / "notes.txt").write_bytes(b"hello")
    return root


@pytest.fixture
def service(cfg, store):
    return Kingfisher(
        cfg,
        graph=StubAgent("ok"),
        threads=StubCheckpointer(),
        files=LocalFileStore(store),
    )


# -- the rule both stores share --------------------------------------------


@pytest.mark.parametrize("name", ["a.csv", "sub/a.csv", "./a.csv"])
def test_an_ordinary_name_lands_under_the_root(name):
    assert Path("/root") in within(Path("/root"), name).parents


@pytest.mark.parametrize(
    "name",
    ["../a", "a/../../b", "/etc/passwd", "C:\\Windows\\x", "", ".", ".."],
    ids=["parent", "climbing", "absolute", "windows", "empty", "dot", "dotdot"],
)
def test_a_name_that_leaves_the_root_is_refused(name):
    """One function rather than a check each adapter remembers, because both
    stores take a caller's id and join it onto a directory."""
    with pytest.raises(UnsafeReferenceError):
        within(Path("/root"), name)


def test_the_rule_never_asks_the_filesystem(tmp_path):
    """Lexical by necessity: the domain may not touch the filesystem, and
    `resolve` is a syscall. Demonstrated on a root that does not exist -- if
    this consulted the disk it could not answer at all.

    The cost of that is real and is the adapter's to cover: a symlink inside the
    root pointing outside passes this check, which is why
    `test_the_local_store_refuses_a_symlink_pointing_out_of_it` exists.
    """
    nowhere = tmp_path / "does" / "not" / "exist"

    assert within(nowhere, "a.csv") == nowhere / "a.csv"
    assert not nowhere.exists()


# -- the shipped adapter ---------------------------------------------------


def test_the_local_store_returns_what_it_was_asked_for(store):
    assert LocalFileStore(store).fetch("sales.csv") == {"sales.csv": b"a,b\n1,2\n"}


def test_the_local_store_refuses_a_name_that_climbs_out(store, tmp_path):
    (tmp_path / "secret").write_bytes(b"not yours")

    with pytest.raises(UnsafeReferenceError):
        LocalFileStore(store).fetch("../secret")


def test_the_local_store_refuses_a_symlink_pointing_out_of_it(store, tmp_path):
    """The check `within` cannot make. The name is unremarkable and the file is
    somewhere else entirely, which is only visible by asking the filesystem --
    so it is asked here, in the layer allowed to."""
    (tmp_path / "secret").write_bytes(b"not yours")
    (store / "innocent.csv").symlink_to(tmp_path / "secret")

    with pytest.raises(UnsafeReferenceError):
        LocalFileStore(store).fetch("innocent.csv")


def test_a_missing_reference_is_not_an_oserror(store):
    """A caller who named a file that is not there gets something they can act
    on. A bare `FileNotFoundError` cannot be told from this deployment's disk
    being wrong, and answers 500 to a typo."""
    with pytest.raises(UnknownReferenceError):
        LocalFileStore(store).fetch("nope.csv")


def test_a_directory_is_not_a_file(store):
    (store / "folder").mkdir()

    with pytest.raises(UnknownReferenceError):
        LocalFileStore(store).fetch("folder")


# -- resolution happens while the request can still be refused -------------


def test_a_bad_reference_leaves_no_turn_behind(service, cfg):
    """The rule `check_placeable` exists for, spelled for ids. `--input` naming
    a missing file once stranded `t001`; a ref that will not resolve is the same
    bug in a different vocabulary."""
    session_id = service.start_session()
    runs = cfg.workspace / "sessions" / session_id / "runs"

    with pytest.raises(UnknownReferenceError):
        service.run(Request("go", session_id=session_id, data_refs=("nope.csv",)))

    assert not runs.exists() or list(runs.iterdir()) == []


def test_a_bad_reference_places_none_of_the_good_ones(service, cfg):
    """Everything is fetched before anything is written, so a request that is
    going to fail does not half-succeed."""
    session_id = service.start_session()
    data = cfg.workspace / "sessions" / session_id / "data"

    with pytest.raises(UnknownReferenceError):
        service.run(
            Request("go", session_id=session_id, data_refs=("sales.csv", "nope.csv"))
        )

    assert list(data.iterdir()) == []


def test_a_bad_reference_gives_the_claim_back(service, cfg):
    """`_admit` releases on the way out, so a refused request does not wedge the
    session until its claim ages out."""
    session_id = service.start_session()

    with pytest.raises(UnknownReferenceError):
        service.run(Request("go", session_id=session_id, input_refs=("nope.txt",)))

    assert not (cfg.state_dir / "claims" / session_id).exists()
    assert service.run(Request("again", session_id=session_id)).turn_id


def test_naming_files_by_id_without_a_store_is_a_deployment_error(cfg):
    """Told apart from a ref that does not resolve, because one is a wiring
    mistake nobody outside can fix and the other is a bad request."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session_id = service.start_session()

    with pytest.raises(MissingStoreError):
        service.run(Request("go", session_id=session_id, data_refs=("sales.csv",)))


def test_a_request_with_no_references_never_asks_the_store(cfg, store):
    """A store that would raise is not consulted, so wiring one has no effect on
    requests that do not use it."""

    class Explodes:
        def fetch(self, file_id):
            pytest.fail("a request naming no references must not consult the store")

    service = Kingfisher(
        cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), files=Explodes()
    )
    session_id = service.start_session()

    assert service.run(Request("go", session_id=session_id)).answer == "ok"


# -- where the content lands -----------------------------------------------


def test_a_data_reference_survives_into_the_next_turn(service, cfg):
    session_id = service.start_session()

    service.run(Request("go", session_id=session_id, data_refs=("sales.csv",)))
    service.run(Request("again", session_id=session_id))

    data = cfg.workspace / "sessions" / session_id / "data"
    assert (data / "sales.csv").read_bytes() == b"a,b\n1,2\n"


def test_an_input_reference_leaves_with_its_turn(service):
    session_id = service.start_session()

    first = service.run(Request("go", session_id=session_id, input_refs=("notes.txt",)))
    second = service.run(Request("again", session_id=session_id))

    assert (first.run_dir / "input" / "notes.txt").read_bytes() == b"hello"
    assert not (second.run_dir / "input").exists()


def test_paths_and_references_land_side_by_side(service, cfg, tmp_path):
    """Both forms of the same thing. `inputs` stays a host path for CLI and
    library callers; a ref is the remote spelling, and neither knows about the
    other by the time it lands."""
    local = tmp_path / "local.csv"
    local.write_bytes(b"x")
    session_id = service.start_session()

    service.run(
        Request("go", session_id=session_id, data=(local,), data_refs=("sales.csv",))
    )

    data = cfg.workspace / "sessions" / session_id / "data"
    assert sorted(p.name for p in data.iterdir()) == ["local.csv", "sales.csv"]


def test_data_is_left_read_only_after_a_reference_is_written(service, cfg):
    """`place_data` re-hardens `/data` on its way out, and taking the write bits
    twice would leave a window where it is writable for no reason -- so both
    forms go through one `writable_data` block."""
    session_id = service.start_session()

    service.run(Request("go", session_id=session_id, data_refs=("sales.csv",)))

    data = cfg.workspace / "sessions" / session_id / "data"
    assert not data.stat().st_mode & 0o200


def test_a_store_key_that_climbs_out_is_refused(cfg):
    """The store is deployment-wired, but its *keys* can come from wherever a
    caller uploaded. They are the untrusted half even when the store is not."""

    class Hostile:
        def fetch(self, file_id):
            return {"../../escaped.txt": b"x"}

    service = Kingfisher(
        cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), files=Hostile()
    )
    session_id = service.start_session()

    with pytest.raises(UnsafeReferenceError):
        service.run(Request("go", session_id=session_id, data_refs=("anything",)))


def test_nothing_is_fetched_when_nothing_is_named():
    assert fetch_refs(Request("go"), None).empty


def test_a_hostile_key_is_refused_before_a_turn_exists(cfg):
    """Why the check in `fetch_refs` is not redundant with the one in the
    writers, which is not obvious and a mutation showed it.

    Both are `within`, so removing either alone changes nothing -- the other
    catches it. But they guard different moments. `place_data` runs during
    admission, while `place_inputs` runs in `_open_turn`, *after* the turn
    directory exists. A hostile input key caught only there would leave one
    behind, which is the whole ordering `_Admitted` protects.
    """

    class Hostile:
        def fetch(self, file_id):
            return {"../../escaped.txt": b"x"}

    service = Kingfisher(
        cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), files=Hostile()
    )
    session_id = service.start_session()
    runs = cfg.workspace / "sessions" / session_id / "runs"

    with pytest.raises(UnsafeReferenceError):
        service.run(Request("go", session_id=session_id, input_refs=("anything",)))

    assert not runs.exists() or list(runs.iterdir()) == []


@pytest.mark.parametrize("name", ["../escaped.txt", "/etc/passwd"])
def test_the_writers_refuse_a_hostile_key_on_their_own(tmp_path, name):
    """The other half of the pair above, tested where nothing else can cover
    for it. These are the calls that actually touch the disk, so the guard is
    here as well as at the fetch -- one is about ordering, this one is about the
    syscall."""
    from kingfisher.infrastructure.workspace_fs import (
        ensure_session_layout,
        place_data,
        place_inputs,
    )

    session = tmp_path / "session"
    ensure_session_layout(session)
    turn = tmp_path / "turn"
    turn.mkdir()

    with pytest.raises(UnsafeReferenceError):
        place_data((), session, contents={name: b"x"})
    with pytest.raises(UnsafeReferenceError):
        place_inputs((), turn / "input", contents={name: b"x"})
