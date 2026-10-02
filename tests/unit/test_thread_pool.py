"""One thread pool per process, at the size a deployment set."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.application.service import Kingfisher
from kingfisher.config import ConfigError
from kingfisher.infrastructure import threads
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent


@pytest.fixture
def no_pool_yet(monkeypatch):
    """A process that has not made its pool yet, which the suite's own has."""
    monkeypatch.setattr(threads._POOL, "executor", None)
    monkeypatch.setattr(threads._POOL, "size", None)


def _service(cfg, size: int) -> Kingfisher:
    return Kingfisher(
        replace(cfg, thread_pool_size=size), graph=StubAgent("ok"), threads=StubCheckpointer()
    )


def test_a_second_size_in_one_process_is_refused(cfg, no_pool_yet):
    """Ignored, whichever service was built first would size the pool for every other,
    and one that asked for more would queue behind a number it never chose.
    """
    first = _service(cfg, 8)
    # The control: the same size again is the same pool, not a refusal.
    assert _service(cfg, 8)._pool is first._pool
    with pytest.raises(ConfigError, match=r"holds 8 threads and this service asks for 16"):
        _service(cfg, 16)


def test_a_pool_of_no_threads_is_refused_by_its_setting(no_pool_yet):
    """`ThreadPoolExecutor` refuses zero too, in words that name no setting to change."""
    with pytest.raises(ConfigError, match="KINGFISHER_THREAD_POOL_SIZE must be at least 1"):
        threads.thread_pool(0)
