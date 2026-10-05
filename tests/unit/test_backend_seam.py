"""The filesystem a deployment names, and what happens when it names none."""

from __future__ import annotations

import pytest
from deepagents.backends import CompositeBackend

from kingfisher import Kingfisher
from kingfisher.application.run import run, stream
from kingfisher.config import ConfigError
from kingfisher.domain.ports import CommandResult, CommandRunner
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import _backend_for
from kingfisher.infrastructure.harness.backend import (
    DefaultBackends,
    WorkspaceScopedBackend,
    backend_at,
    default_backends,
)
from kingfisher.infrastructure.harness.backend_contract import refuse_unusable_backend
from kingfisher.infrastructure.steps import drive
from kingfisher.layout import DATA
from tests.conftest import StubCheckpointer, an_agent, harness_in
from tests.unit.test_run import StubAgent
from tests.unit.test_session_files import Elsewhere as KeptElsewhere


class Substitute(WorkspaceScopedBackend):
    """What a deployment returns when it adjusts what it built: its own type, over the
    shell and the routes `default_backends` made.

    A plain object will not do, and that is the seam's shape rather than this file's
    convenience -- `refuse_unusable_backend` runs on whatever an `open` returns, so a
    test returning something arbitrary would exercise a path no deployment has.
    """

    def __init__(self, given: WorkspaceScopedBackend) -> None:
        super().__init__(given.default, given.routes, workspace=given.workspace)
        self.wrapping = given


class NotABackend:
    """What a deployment returns when it builds one from nothing: whatever methods it
    wrote, and none of the inheritance deepagents decides by.
    """


class Elsewhere(CommandRunner):
    """A runner that ships its commands off this machine, which is the case where
    losing it matters: the confinement names paths on *this* host.
    """

    @property
    def local(self) -> bool:
        return False

    def run(
        self, command: str, *, timeout: int | None = None
    ) -> CommandResult:  # pragma: no cover -- wired, never driven: no turn is run here
        raise NotImplementedError


def test_a_service_with_no_filesystem_named_is_refused(cfg):
    """The whole change, in one line. Silence used to mean kingfisher picked, so a
    deployment could wire the entire service without learning there was a sandbox in
    it at all.
    """
    with pytest.raises(ValueError, match="does not pick the filesystem"):
        Kingfisher(cfg, threads=StubCheckpointer())


def test_a_pre_built_graph_is_refused_without_backends_named(cfg):
    """A graph carries the backend its agent runs on, and nothing kingfisher can open.
    Accepted alone, kingfisher guessed a directory on this host for the data it places
    and the files it collects -- somewhere a graph whose backend runs elsewhere never
    looks.
    """
    with pytest.raises(ValueError, match="A pre-built graph needs them too"):
        Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())


@pytest.mark.parametrize("door", [run, stream])
def test_the_one_liners_refuse_a_graph_without_backends_named(cfg, door):
    """Their default is a guess about the one thing only a graph's builder knows."""
    with pytest.raises(ValueError, match="A pre-built graph needs them too"):
        door(Request("t"), cfg=cfg, graph=StubAgent("ok"), checkpointer=StubCheckpointer())


def test_a_pre_built_graph_reaches_its_session_through_the_backends_named(cfg, tmp_path):
    """What the guess cost: beside a graph, a request's data went into
    `<workspace>/sessions/<id>` whatever the deployment kept its sessions in.
    """
    source = tmp_path / "in.csv"
    source.write_text("alpha\n")
    remote = tmp_path / "remote"
    kf = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=KeptElsewhere(remote), threads=StubCheckpointer()
    )

    session_id = kf.run(Request("t", data=(source,))).session_id

    assert (remote / session_id / DATA / "in.csv").read_text() == "alpha\n"
    assert not (cfg.workspace / "sessions" / session_id / DATA / "in.csv").exists()


def test_a_plain_factory_function_is_refused_with_the_way_forward(cfg):
    """What a deployment written before `SessionBackends` passes. It makes a backend
    and answers nothing about which sessions there are, so it is refused where it is
    wired, naming the class to build on rather than failing at the first `reap`.
    """

    def mine(cfg_, session_id, /, *, catalogue=None, runner=None):  # pragma: no cover
        return default_backends.open(cfg_, session_id, catalogue=catalogue, runner=runner)

    with pytest.raises(TypeError, match="subclass DefaultBackends"):
        Kingfisher(cfg, backends=mine, threads=StubCheckpointer())  # ty: ignore[invalid-argument-type]


def test_a_backend_instance_is_refused_where_session_backends_belong(cfg):
    """The mistake this parameter's shape exists to discourage, refused anyway: one
    backend is rooted at one session, so sharing an instance shares a filesystem
    between every caller -- which is what a deployment replacing the backend is
    usually separating.
    """
    with pytest.raises(TypeError, match="rooted at a session"):
        Kingfisher(cfg, backends=NotABackend(), threads=StubCheckpointer())  # ty: ignore[invalid-argument-type]


def test_a_backend_is_opened_per_turn_for_the_session_it_is_for(cfg, session_dir):
    """A backend is rooted at a session, so opening one once at construction
    would be one filesystem for every caller -- the leak a deployment replaces the
    backend to avoid, written where nothing at the call site looks wrong.
    """
    an_agent(cfg)
    seen: list[str] = []

    class Mine(DefaultBackends):
        def open(self, cfg_, session_id, /, *, catalogue=None, runner=None):
            seen.append(session_id)
            return super().open(cfg_, session_id, catalogue=catalogue, runner=runner)

    service = Kingfisher(cfg, backends=Mine())
    prepared = drive(service._prepare_steps(Request("go", agent="only")))

    assert seen == [prepared.session.id]


def test_open_is_handed_the_catalogue_and_the_runner_this_deployment_wired(
    cfg, session_dir
):
    """Either one missing costs a deployment something with no symptom: without the
    catalogue a session sees none of the bundles' skills, and without the runner its
    commands run under kingfisher's own fence rather than wherever the deployment
    sends them. The backend that comes back is well-formed either way, which is why
    `open`'s signature is typed rather than merely documented.
    """
    an_agent(cfg)
    seen: list[dict[str, object]] = []
    runner = Elsewhere()

    class Mine(DefaultBackends):
        def open(self, cfg_, session_id, /, *, catalogue=None, runner=None):
            seen.append({"catalogue": catalogue, "runner": runner})
            return super().open(cfg_, session_id, catalogue=catalogue)

    service = Kingfisher(cfg, backends=Mine(), runner=lambda _where: runner)
    drive(service._prepare_steps(Request("go", agent="only")))

    assert seen[0]["catalogue"] is service.catalogue
    assert seen[0]["runner"] is runner


def test_what_open_returns_is_what_the_agent_is_built_on(cfg, session_dir):
    """Without this the seam does nothing. Read off the record of what the build
    attached rather than from anything kingfisher reports about itself: a service
    that computed the backend and then dropped it would satisfy every other test in
    this file, and the record is what `create_deep_agent` was called with.
    """
    an_agent(cfg)
    made: list[Substitute] = []

    class Mine(DefaultBackends):
        def open(self, cfg_, session_id, /, *, catalogue=None, runner=None):
            made.append(
                Substitute(super().open(cfg_, session_id, catalogue=catalogue, runner=runner))
            )
            return made[-1]

    asked = Request("go", agent="only")
    service = Kingfisher(cfg, backends=Mine())
    built = service._graph_for(
        asked,
        service.grants,
        agent=drive(service._agent_for(asked, harness_in(session_dir))),
        held=None,
        files=drive(service._files_for(session_dir.name, session_dir)),
    )

    assert built.backend is made[0]


def test_a_backend_deepagents_will_not_give_a_shell_is_refused(cfg, session_dir):
    """The failure with no symptom, and the reason a check runs at every build.

    deepagents decides what a backend is with `isinstance` against its own abstract
    base class, so one implementing `execute` correctly and inheriting nothing is
    handed no shell at all: the tool leaves the roster, the model is told execution is
    unavailable if it reaches for it, and the deployment hears nothing.
    """
    with pytest.raises(ConfigError, match="not recognised by deepagents"):
        _backend_for(cfg, session_dir, NotABackend(), Definitions.from_config(cfg))


def test_a_backend_routing_nothing_a_deny_rule_needs_is_refused(cfg, session_dir):
    """Otherwise this fails at `create_deep_agent` in words that never say kingfisher.

    `FilesystemMiddleware` refuses `permissions=` outright on a backend that executes
    unless every rule sits under one of its routes, and kingfisher passes a deny rule
    for every scope the layout refuses writes under.
    """
    routeless = CompositeBackend(
        default=backend_at(cfg, session_dir).default, routes={}
    )

    with pytest.raises(ConfigError, match="routes nothing covering"):
        _backend_for(cfg, session_dir, routeless, Definitions.from_config(cfg))


def test_the_backend_kingfisher_builds_satisfies_what_it_refuses_others_for(
    cfg, session_dir
):
    """The control on the two refusals above: these checks run on *every* build, so one
    the default cannot pass takes every agent in this repository with it, and one no
    backend could fail passes whatever it is pointed at.

    Driven through `refuse_unusable_backend` against the real default rather than
    asserting on a backend of this test's own making, which would go on passing
    whatever the checks became.
    """
    refuse_unusable_backend(backend_at(cfg, session_dir))


def test_the_harness_still_builds_its_own_for_a_caller_with_only_a_session(
    cfg, session_dir
):
    """`kingfisher --list` reaches `build_agent` with a session and no backend, and has
    no deployment behind it to have named one. Requiring one at `Kingfisher` is not
    the same as requiring one here, and this is what holds the two apart.
    """
    assert isinstance(
        _backend_for(cfg, session_dir, None, Definitions.from_config(cfg)),
        WorkspaceScopedBackend,
    )


def test_a_harness_build_with_neither_is_still_refused(cfg):
    """The one case with no answer available: nothing to root a backend at, and nothing
    supplied to use instead.
    """
    with pytest.raises(ValueError, match="session_dir to root a backend at"):
        _backend_for(cfg, None, None, Definitions.from_config(cfg))


def test_the_one_liner_keeps_a_default_where_the_constructor_refuses_one(monkeypatch):
    """`run` and `stream` are conveniences over a *default* `Kingfisher`, and that is
    the difference worth keeping: the constructor is where a deployment says what its
    agents run on, and these two are what spares a caller from saying it -- unless
    the caller hands over a graph, which
    `test_the_one_liners_refuse_a_graph_without_backends_named` holds.
    """
    import importlib

    helpers = importlib.import_module("kingfisher.application.run")
    handed: list[object] = []

    class Recording:
        def __init__(self, cfg, **wiring) -> None:
            handed.append(wiring["backends"])

        def run(self, request) -> None:
            return None

        def stream(self, request) -> None:
            return None

    monkeypatch.setattr(helpers, "Kingfisher", Recording)
    helpers.run("t")
    helpers.stream("t")

    assert handed == [default_backends, default_backends]
