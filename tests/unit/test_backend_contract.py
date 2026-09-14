"""The kit a deployment runs against the backend it supplied."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from deepagents.backends.protocol import ReadResult

from kingfisher.infrastructure.harness.backend import (
    WorkspaceScopedBackend,
    build_backend,
)
from kingfisher.infrastructure.harness.backend_contract import (
    BACKEND_CONTRACT,
    execution_support,
    filesystem_consistency,
    host_path_refusal,
    route_coverage,
)


def test_kingfishers_own_backend_keeps_the_contract(cfg, session_dir):
    """The kit, against the implementation it was written from.

    Driven rather than inspected, and against the real builder rather than a
    `CompositeBackend` assembled here: a kit that checked a fixture of its own making
    would pass with every route wrong.
    """
    for check in BACKEND_CONTRACT:
        check(lambda: build_backend(cfg, session_dir))


def test_the_contract_is_not_quietly_empty():
    """A kit with no checks passes whatever it was meant to check."""
    assert len(BACKEND_CONTRACT) >= 4
    assert all(callable(check) for check in BACKEND_CONTRACT)


class NotRecognised:
    """A backend written the way a reader of the protocol would write one.

    Every method present and correct, by delegation, and inheriting nothing. This is
    the shape the first check exists for -- and the reason it cannot be a duck-typed
    check itself, since by any structural measure this object is a backend.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_a_backend_that_inherits_nothing_is_caught(cfg, session_dir):
    """The silent one: deepagents would drop `execute` and tell only the model."""
    real = build_backend(cfg, session_dir)

    with pytest.raises(AssertionError, match="not recognised by deepagents"):
        execution_support(lambda: NotRecognised(real))


def test_a_backend_missing_a_route_a_deny_rule_names_is_caught(cfg, session_dir):
    """Without a route under it, `/data/**` is not a rule deepagents will accept, and
    the graph does not build at all.
    """
    real = build_backend(cfg, session_dir)
    thinned = WorkspaceScopedBackend(
        default=real.default,
        routes={path: at for path, at in real.routes.items() if path != "/data/"},
        workspace=session_dir,
    )

    with pytest.raises(AssertionError, match="/data/"):
        route_coverage(lambda: thinned)


def test_a_shell_that_cannot_see_what_the_tools_wrote_is_caught(cfg, session_dir):
    """The failure the whole route table was kept kingfisher's to prevent: files the
    agent can read and cannot run anything over.
    """

    class Blind(WorkspaceScopedBackend):
        def execute(self, command: str, **kwargs: Any) -> Any:
            outcome = super().execute(command, **kwargs)
            return replace(outcome, output="")

    real = build_backend(cfg, session_dir)
    blind = Blind(default=real.default, routes=dict(real.routes), workspace=session_dir)

    with pytest.raises(AssertionError, match="one filesystem"):
        filesystem_consistency(lambda: blind)


def test_a_host_path_refused_with_the_wrong_type_is_caught(cfg, session_dir):
    """`HostPathGuard` catches one type. Refuse with another and a correction the
    model could have acted on arrives as a failed turn instead.
    """

    class WrongRefusal(WorkspaceScopedBackend):
        def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
            if file_path.startswith("/etc/"):
                raise ValueError(file_path)
            return super().read(file_path, offset, limit)

    real = build_backend(cfg, session_dir)
    wrong = WrongRefusal(
        default=real.default, routes=dict(real.routes), workspace=session_dir
    )

    with pytest.raises(AssertionError, match="HostPathGuard does not catch"):
        host_path_refusal(lambda: wrong)


def test_a_backend_that_allows_host_paths_is_not_asked_to_refuse_them(cfg, session_dir):
    """The conditional half. A sandbox of its own has no host to confuse its paths
    with, and requiring it to refuse `/etc/passwd` would be requiring it to refuse its
    own filesystem.
    """

    class Allows(WorkspaceScopedBackend):
        def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
            if file_path.startswith("/etc/"):
                return ReadResult(file_data={"content": "root:x:0:0", "encoding": "utf-8"})
            return super().read(file_path, offset, limit)

    real = build_backend(cfg, session_dir)
    allows = Allows(default=real.default, routes=dict(real.routes), workspace=session_dir)

    host_path_refusal(lambda: allows)


def test_the_kit_is_run_against_the_real_tree_not_a_composite_of_its_own(cfg, session_dir):
    """The control above builds with `build_backend`, and deleting that would leave a
    kit asserting on routes it had assembled itself -- green against any mistake the
    real builder makes.
    """
    real = build_backend(cfg, session_dir)

    assert isinstance(real, WorkspaceScopedBackend)
    assert "/data/" in real.routes
