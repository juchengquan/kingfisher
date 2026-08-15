from __future__ import annotations

import os

import pytest

from kingfisher.adapters.workspace_fs import protect_data, writable_data


def test_data_becomes_read_only_to_the_os(workspace):
    """The layer the deny rule cannot provide: the kernel enforces this against
    `execute` too, which tool-level permissions never covered."""
    with writable_data(workspace) as data:
        (data / "input.csv").write_text("a,b\n1,2\n")

    protect_data(workspace)

    with pytest.raises(PermissionError):
        (workspace / "data" / "input.csv").write_text("clobbered")


def test_directory_write_bit_is_dropped_so_files_cannot_be_deleted(workspace):
    """Deletion is governed by the directory's write bit, not the file's."""
    with writable_data(workspace) as data:
        (data / "input.csv").write_text("x")
    protect_data(workspace)

    assert not os.access(workspace / "data", os.W_OK)


def test_writable_data_restores_protection_afterwards(workspace):
    with writable_data(workspace) as data:
        (data / "new.csv").write_text("y")
        assert os.access(data, os.W_OK)

    assert not os.access(workspace / "data", os.W_OK)


def test_protect_data_is_idempotent(workspace):
    protect_data(workspace)
    protect_data(workspace)
    assert not os.access(workspace / "data", os.W_OK)
