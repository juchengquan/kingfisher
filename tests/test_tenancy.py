"""What one caller can reach, and what a turn is allowed to destroy."""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher
from kingfisher.domain.capabilities import ALL, UNRESTRICTED, Capabilities
from kingfisher.domain.request import Request
from kingfisher.domain.session import UnknownSessionError
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


def service(cfg, **kwargs):
    return Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer(), **kwargs)


# -- T2: a session id is a bearer credential ------------------------------


def test_a_request_cannot_create_a_session_by_naming_one(cfg):
    """The whole of T2. A service that forwarded an id from its own caller
    would otherwise let that caller choose -- or guess -- the name, and read
    somebody else's conversation and files."""
    kf = service(cfg)

    with pytest.raises(UnknownSessionError, match="no session 'someone-elses'"):
        kf.run(Request("go", session_id="someone-elses"))


def test_a_request_with_no_id_starts_one_and_says_which(cfg):
    kf = service(cfg)

    result = kf.run(Request("go"))

    assert result.session_id
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()


def test_the_id_it_hands_back_can_be_resumed(cfg):
    """Holding an id is how a caller proves the session is theirs."""
    kf = service(cfg)
    first = kf.run(Request("go"))

    second = kf.run(Request("again", session_id=first.session_id))

    assert second.session_id == first.session_id
    assert second.turn_id != first.turn_id


def test_minted_ids_are_not_guessable(cfg):
    """48 bits was enough to avoid collisions, which is all it was for, and
    far too few for something that opens a conversation and its files."""
    kf = service(cfg)

    minted = kf.run(Request("go")).session_id

    assert len(minted) == 32  # uuid4().hex, 128 bits


def test_the_service_may_name_a_session_even_though_a_request_may_not(cfg):
    """T2 is about who is asking, not about names. The service knows."""
    kf = service(cfg)

    kf.start_session("chosen-by-the-service")

    assert kf.run(Request("go", session_id="chosen-by-the-service")).session_id == (
        "chosen-by-the-service"
    )


# -- T3: grants clamp, uploads do not need clamping -----------------------


def test_a_request_cannot_widen_past_what_the_deployment_granted(cfg, session_dir):
    """`intersect` was implemented, tested and called by nothing. Now it runs."""
    kf = Kingfisher(cfg, threads=StubCheckpointer(), grants=Capabilities(tools=("read_file",)))

    allowed = kf.grants.intersect(Capabilities(tools=("read_file", "execute")))

    assert allowed.tools == ("read_file",)


def test_grants_are_unrestricted_by_default(cfg):
    """A deployment serving one caller is unaffected by any of this.

    `UNRESTRICTED`, not `Capabilities()`: a grant that said nothing about
    subagents would clamp away every request that named one, because for a
    *request* saying nothing means wiring none.
    """
    assert Kingfisher(cfg, threads=StubCheckpointer()).grants == UNRESTRICTED


def test_an_uploaded_definition_is_added_back_after_clamping(cfg):
    """A grant list is written before an upload exists and its name is
    unknowable then, so clamping against it would strip every upload rather
    than authorise it. The caller supplied the content; the tool clamp, which
    `including` does not touch, is what actually bounds it."""
    granted = Capabilities(skills=("tabular-qa",), tools=("read_file",))

    allowed = granted.intersect(Capabilities()).including(skills=("theirs",))

    assert allowed.skills is not None
    assert set(allowed.skills) == {"tabular-qa", "theirs"}
    assert allowed.tools == ("read_file",)  # untouched


def test_including_cannot_widen_an_unrestricted_set(cfg):
    """`ALL` already includes them; adding names would narrow it. And `None`
    asked for none, so an upload is not a way back through that door."""
    assert Capabilities().including(skills=("theirs",)).skills == ALL
    assert Capabilities(skills=None).including(skills=("theirs",)).skills is None


# -- lifecycle: disposal is asked for -------------------------------------


def test_a_turn_disposes_of_nothing(cfg):
    """Retention counted every caller's sessions together, so a busy caller
    evicted a quiet one on a turn that had nothing to do with it."""
    kf = service(cfg)
    quiet = kf.start_session()
    for _ in range(5):
        kf.run(Request("busy"))

    assert (cfg.workspace / "sessions" / quiet).is_dir()


def test_delete_session_removes_the_directory_and_the_thread(cfg):
    threads = StubCheckpointer()
    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads)
    session_id = kf.run(Request("go")).session_id

    assert kf.delete_session(session_id) is None
    assert not (cfg.workspace / "sessions" / session_id).exists()
    assert threads.deleted == [session_id]


def test_deleting_an_unknown_session_is_not_an_error(cfg):
    """A caller that retries a delete should not have to care."""
    assert service(cfg).delete_session("never-existed") is None


def test_reap_disposes_of_the_idle_and_leaves_the_rest(cfg):
    """Age, not count: how long a session has been idle is a property of that
    session alone, so one caller's traffic cannot evict another's."""
    import os

    kf = service(cfg)
    old, fresh = kf.start_session("old"), kf.start_session("fresh")
    os.utime(cfg.workspace / "sessions" / old, (1_000, 1_000))

    result = kf.reap(older_than_seconds=60, now=10_000)

    assert result.removed == ("old",)
    assert not (cfg.workspace / "sessions" / old).exists()
    assert (cfg.workspace / "sessions" / fresh).is_dir()


# -- threads that outlived their session -----------------------------------


class ListingCheckpointer(StubCheckpointer):
    """A store that can also say which threads it holds, as a real one can.

    `StubCheckpointer` deliberately cannot: `ThreadStore` is only "something
    that forgets a thread", and a sweep must still work when handed one. That
    case has its own test below.
    """

    def __init__(self, held: tuple[str, ...]) -> None:
        super().__init__()
        self.held = held

    # Named `list` because that is the saver's own method, which is what
    # `thread_ids` looks for. It shadows the builtin inside this class, so
    # `held` is annotated as a tuple rather than a `list[str]` that would
    # resolve to this method.
    def list(self, _config):
        from types import SimpleNamespace

        return [
            SimpleNamespace(config={"configurable": {"thread_id": t}}) for t in self.held
        ]


def test_a_thread_whose_session_is_gone_is_deleted(cfg):
    """`discard` takes the thread and the directory together, so a swept session
    leaves neither. A directory that goes any other way -- by hand, or one of
    the sessions that could not be removed until `remove_tree` learned to unlock
    `/data` -- left its thread forever, because nothing else looked. One real
    workspace held 132 of them and 1,894 checkpoints after every session had
    been reaped.
    """
    import time

    threads = ListingCheckpointer(held=("ghost-a", "ghost-b"))
    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads)

    result = kf.reap(older_than_seconds=0, now=time.time())

    assert set(result.orphans) == {"ghost-a", "ghost-b"}
    assert set(threads.deleted) == {"ghost-a", "ghost-b"}


def test_a_thread_whose_session_still_exists_is_left_alone(cfg):
    """The reconciliation must not eat live conversations."""
    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    live = kf.start_session()

    threads = ListingCheckpointer(held=(live, "ghost"))
    kf2 = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads)
    result = kf2.reap(older_than_seconds=10_000, now=1_000)  # nothing expired

    assert result.removed == ()
    assert result.orphans == ("ghost",)
    assert live not in threads.deleted
    assert (cfg.workspace / "sessions" / live).is_dir()


def test_orphans_are_reported_apart_from_sessions_this_sweep_ended(cfg):
    """They are not sessions this call decided to end; they are residue from
    ones that ended some other way, and a janitor's log should tell them apart.
    """
    import time

    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    doomed = kf.start_session()

    threads = ListingCheckpointer(held=("ghost",))
    result = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads).reap(
        older_than_seconds=0, now=time.time()
    )

    assert result.removed == (doomed,)
    assert result.orphans == ("ghost",)


def test_a_store_that_cannot_enumerate_still_sweeps(cfg):
    """`ThreadStore` is only `delete_thread`. Widening the port to make
    reconciliation possible would break every double, so a store that cannot
    answer is skipped rather than failing the sweep it was asked for.
    """
    import time

    kf = service(cfg)
    idle = kf.start_session()

    result = kf.reap(older_than_seconds=0, now=time.time())

    assert result.removed == (idle,)
    assert result.orphans == ()
