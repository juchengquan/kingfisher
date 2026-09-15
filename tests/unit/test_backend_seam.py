"""The seam a deployment puts its own filesystem under the agent through."""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend

from kingfisher import Kingfisher
from kingfisher.config import ConfigError
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import _backend_for
from kingfisher.infrastructure.harness.backend import (
    WorkspaceScopedBackend,
    default_backend,
)
from kingfisher.infrastructure.harness.backend_contract import refuse_unusable_backend
from tests.conftest import StubCheckpointer, an_agent
from tests.unit.test_run import StubAgent


class Substitute(WorkspaceScopedBackend):
    """What a deployment returns when it adjusts what it was handed: its own type,
    over the shell and the routes kingfisher built.

    A plain object will no longer do, and that is the seam's shape rather than this
    file's convenience -- `refuse_unusable_backend` runs on whatever comes back, so a
    test returning something arbitrary would be testing a path no deployment has.
    """

    def __init__(self, given: WorkspaceScopedBackend) -> None:
        super().__init__(given.default, given.routes, workspace=given.workspace)
        self.wrapping = given


class NotABackend:
    """What a deployment returns when it builds one from nothing: whatever methods it
    wrote, and none of the inheritance deepagents decides by.
    """

    def __init__(self, wrapping: object = None) -> None:
        self.wrapping = wrapping


def test_a_deployment_function_decides_what_the_agent_runs_against(cfg, session_dir):
    """Without this the seam does nothing: the returned backend has to be the one used,
    not merely a value kingfisher computed and dropped.
    """
    made: list[Substitute] = []

    def mine(default: WorkspaceScopedBackend, where: Path) -> Substitute:
        made.append(Substitute(default))
        return made[-1]

    built = _backend_for(
        cfg, session_dir, None, Definitions.from_config(cfg), backend_from=mine
    )

    assert built is made[0]


def test_the_function_is_handed_the_backend_kingfisher_built(cfg, session_dir):
    """The whole reason this takes the default rather than only a session: a
    deployment that keeps host-path refusal and the route table keeps them by
    returning what it was given, and cannot do that if it was given nothing.
    """
    seen: list[tuple[object, Path]] = []

    def remember(default: object, where: Path) -> object:
        seen.append((default, where))
        return default

    built = _backend_for(
        cfg, session_dir, None, Definitions.from_config(cfg), backend_from=remember
    )

    assert len(seen) == 1
    handed, where = seen[0]
    assert isinstance(handed, WorkspaceScopedBackend)
    assert handed is built
    assert where == session_dir


def test_a_supplied_backend_is_what_the_function_receives(cfg, session_dir):
    """`backend=` and `backend_from=` compose rather than contradict: the one handed in
    is the one the function is handed, not a second one built behind its back.
    """
    supplied = default_backend(cfg, session_dir)

    result = _backend_for(
        cfg,
        session_dir,
        supplied,
        Definitions.from_config(cfg),
        backend_from=lambda default, where: Substitute(default),
    )

    assert isinstance(result, Substitute)
    assert result.wrapping is supplied


def test_a_function_with_no_session_to_root_it_at_is_refused(cfg):
    """It is called *with* the session, so there is nothing to pass and no honest
    value to invent -- `None` would reach a deployment as a path it would join names
    onto.
    """
    with pytest.raises(ValueError, match="session_dir to pass it"):
        _backend_for(
            cfg,
            None,
            NotABackend(),
            Definitions.from_config(cfg),
            backend_from=lambda default, where: default,
        )


def test_a_backend_instance_is_refused_where_a_callable_belongs(cfg):
    """The mistake this parameter's name exists to discourage, refused anyway: one
    backend is rooted at one session, so sharing an instance shares a filesystem
    between every caller -- which is what a deployment replacing the backend is
    usually separating.
    """
    with pytest.raises(TypeError, match="rooted at a session"):
        Kingfisher(cfg, backend_from=NotABackend(), threads=StubCheckpointer())  # ty: ignore[invalid-argument-type]


def test_a_pre_built_graph_and_a_backend_function_are_refused_together(cfg):
    """Either answer discards the other silently: `_graph_for` returns a pre-built
    graph before anything is built for the function to be handed.
    """
    with pytest.raises(ValueError, match="two answers"):
        Kingfisher(
            cfg,
            graph=StubAgent("ok"),
            backend_from=lambda default, where: default,
            threads=StubCheckpointer(),
        )


def test_every_turn_is_built_on_what_the_deployment_returned(cfg, session_dir):
    """The service is where a session directory is first known, so a seam that works
    in `build_agent` and is never passed down would pass every test above and do
    nothing in a deployment.
    """
    an_agent(cfg)
    seen: list[Path] = []

    def remember(default: object, where: Path) -> object:
        seen.append(where)
        return default

    service = Kingfisher(cfg, backend_from=remember)
    service._graph_for(Request("go", agent="only"), session_dir)

    assert seen == [session_dir]


def test_a_backend_deepagents_will_not_give_a_shell_is_refused(cfg, session_dir):
    """The failure with no symptom, which is the reason any of this runs at a build.

    deepagents decides what a backend is with `isinstance` against its own abstract
    base class, so one implementing `execute` correctly and inheriting nothing is
    handed no shell at all: `FilesystemMiddleware` drops the tool, the model is told
    execution is unavailable if it reaches for it, and the deployment hears nothing.
    """
    with pytest.raises(ConfigError, match="not recognised by deepagents"):
        _backend_for(
            cfg,
            session_dir,
            None,
            Definitions.from_config(cfg),
            backend_from=lambda default, where: NotABackend(),
        )


def test_a_backend_routing_nothing_a_deny_rule_needs_is_refused(cfg, session_dir):
    """Otherwise this fails at `create_deep_agent` in words that never say kingfisher.

    `FilesystemMiddleware` refuses `permissions=` outright on a backend that executes
    unless every rule sits under one of its routes, and kingfisher passes a deny rule
    for every scope the layout refuses writes under.
    """
    with pytest.raises(ConfigError, match="routes nothing covering"):
        _backend_for(
            cfg,
            session_dir,
            None,
            Definitions.from_config(cfg),
            backend_from=lambda default, where: CompositeBackend(
                default=default.default, routes={}
            ),
        )


def test_a_backend_handed_straight_to_the_harness_is_checked_too(cfg, session_dir):
    """The check sits where a backend is *resolved*, not where a deployment's function
    is called -- `backend=` reaches the same slot through a different door, and a check
    on one door only leaves the other open.
    """
    with pytest.raises(ConfigError, match="not recognised by deepagents"):
        _backend_for(cfg, session_dir, NotABackend(), Definitions.from_config(cfg))


def test_the_backend_kingfisher_builds_satisfies_what_it_refuses_others_for(
    cfg, session_dir
):
    """The control on the three refusals above: these checks run on *every* build, so
    one the default cannot pass takes every agent in this repository with it, and one
    no backend could fail passes whatever it is pointed at.

    Driven through `refuse_unusable_backend` against the real default rather than
    asserting on a backend of this test's own making, which would go on passing
    whatever the checks became.
    """
    refuse_unusable_backend(default_backend(cfg, session_dir))
