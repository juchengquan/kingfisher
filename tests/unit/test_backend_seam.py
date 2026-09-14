"""The seam a deployment puts its own filesystem under the agent through."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher import Kingfisher
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import _backend_for
from kingfisher.infrastructure.harness.backend import WorkspaceScopedBackend
from tests.conftest import StubCheckpointer, an_agent
from tests.unit.test_run import StubAgent


class Substitute:
    """Something a deployment might return that is not kingfisher's backend."""

    def __init__(self, wrapping: object = None) -> None:
        self.wrapping = wrapping


def test_a_deployment_function_decides_what_the_agent_runs_against(cfg, session_dir):
    """Without this the seam does nothing: the returned backend has to be the one used,
    not merely a value kingfisher computed and dropped.
    """
    mine = Substitute()

    assert (
        _backend_for(
            cfg,
            session_dir,
            None,
            Definitions.from_config(cfg),
            backend_from=lambda default, where: mine,
        )
        is mine
    )


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
    """`backend=` and `backend_from=` compose rather than contradict, so a test can
    exercise the seam against a stub without a real session behind it.
    """
    stub = Substitute()

    result = _backend_for(
        cfg,
        session_dir,
        stub,
        Definitions.from_config(cfg),
        backend_from=lambda default, where: Substitute(wrapping=default),
    )

    assert isinstance(result, Substitute)
    assert result.wrapping is stub


def test_a_function_with_no_session_to_root_it_at_is_refused(cfg):
    """It is called *with* the session, so there is nothing to pass and no honest
    value to invent -- `None` would reach a deployment as a path it would join names
    onto.
    """
    with pytest.raises(ValueError, match="session_dir to pass it"):
        _backend_for(
            cfg,
            None,
            Substitute(),
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
        Kingfisher(cfg, backend_from=Substitute(), threads=StubCheckpointer())  # ty: ignore[invalid-argument-type]


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
