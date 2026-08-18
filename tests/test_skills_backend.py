"""A skills catalogue the agent can read, wherever it is held.

Skills were the one kind pinned to the filesystem, and not because the route
demanded a path — deepagents reads them through `BackendProtocol` — but because
`SkillRepository` could answer only with names, and a route needs file contents.
`files` closes that, and `skills_backend` mounts what it returns.

What is *not* closed, and is tested here so it stays visible: a skill's scripts
are run by the shell against `$KINGFISHER_SKILLS`, and a store has no path for
the shell to reach.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from kingfisher.domain.ports import SkillRepository
from kingfisher.infrastructure.catalogue import Definitions, catalogue_root
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.harness.backend import SKILLS_ROUTE, build_backend, shell_env
from kingfisher.infrastructure.harness.skills_backend import skills_backend

SKILL = "---\nname: {name}\ndescription: A skill.\n---\n\nbody of {name}\n"


@dataclass(frozen=True)
class InStore:
    """A skills repository with nothing on disk anywhere."""

    held: dict

    @property
    def names(self):
        return tuple(sorted(self.held))

    def files(self, name):
        return self.held[name]


def _held(*names):
    return InStore(
        {
            name: {"SKILL.md": SKILL.format(name=name)}
            for name in names
        }
    )


def _on_disk(root, *names):
    for name in names:
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")
    return LocalSkillRepository(root)


# -- the port ------------------------------------------------------------


def test_a_store_backed_repository_satisfies_the_port(tmp_path):
    """Both do, which is the point: `files` is on the port, so a deployment's
    own repository is not a special case anywhere downstream."""
    assert isinstance(_held("a"), SkillRepository)
    assert isinstance(_on_disk(tmp_path, "a"), SkillRepository)


def test_the_local_one_returns_every_file_a_skill_ships(tmp_path):
    """Not just the definition. A skill's scripts and data are what the shell
    runs, and a mount that carried only the markdown would advertise a skill the
    agent could read and not use."""
    repo = _on_disk(tmp_path, "demo")
    (tmp_path / "demo" / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (tmp_path / "demo" / "lib" / "nested").mkdir(parents=True)
    (tmp_path / "demo" / "lib" / "nested" / "data.csv").write_text("a,b\n", encoding="utf-8")

    files = repo.files("demo")

    assert set(files) == {"SKILL.md", "run.sh", "lib/nested/data.csv"}
    assert files["run.sh"] == "echo hi\n"


def test_an_unknown_name_raises_rather_than_returning_nothing(tmp_path):
    """A skill that is silently empty is the failure these modules keep
    refusing, and an empty mapping is exactly that shape."""
    repo = _on_disk(tmp_path, "demo")

    with pytest.raises(KeyError):
        repo.files("never-defined")


def test_a_directory_that_is_not_a_skill_is_not_one(tmp_path):
    """`names` already requires the definition file; `files` has to agree, or a
    caller could fetch a folder the listing never offered."""
    repo = _on_disk(tmp_path, "demo")
    (tmp_path / "just-a-folder").mkdir()

    assert repo.names == ("demo",)
    with pytest.raises(KeyError):
        repo.files("just-a-folder")


# -- the mount -----------------------------------------------------------


def test_the_agent_can_read_a_skill_held_in_a_store(cfg, session_dir):
    """The whole point. No directory anywhere, and the file tools still open
    it through the route deepagents reads."""
    catalogue = replace(Definitions.from_config(cfg), skills=_held("remote"))

    backend = build_backend(cfg, session_dir, catalogue=catalogue)

    assert "body of remote" in str(backend.read(f"{SKILLS_ROUTE}remote/SKILL.md"))


def test_the_listing_deepagents_reads_finds_it(cfg, session_dir):
    """`ls` on the route is how the skills middleware discovers what exists, so
    a mount that reads but does not list would offer the agent nothing."""
    catalogue = replace(Definitions.from_config(cfg), skills=_held("alpha", "beta"))

    backend = build_backend(cfg, session_dir, catalogue=catalogue)
    entries = {entry["path"] for entry in backend.ls(SKILLS_ROUTE).entries or []}

    assert any("alpha" in path for path in entries)
    assert any("beta" in path for path in entries)


def test_a_directory_backed_catalogue_still_gets_a_filesystem_mount(cfg, session_dir):
    """Not everything becomes a store. A directory already on disk is cheaper
    mounted directly -- no copy of every skill in memory -- and it is the only
    shape whose scripts the shell can run."""
    from deepagents.backends.filesystem import FilesystemBackend

    backend = build_backend(cfg, session_dir)

    assert isinstance(backend.routes[SKILLS_ROUTE], FilesystemBackend)


def test_a_store_mount_is_read_only_by_construction(cfg, session_dir):
    """Not by a permission someone remembers to add. A skill is the text the
    model is told to follow, so a writable skills route is one by which a
    request edits the instructions of every later request."""
    catalogue = replace(Definitions.from_config(cfg), skills=_held("remote"))
    backend = build_backend(cfg, session_dir, catalogue=catalogue)

    refused = backend.write(f"{SKILLS_ROUTE}remote/PWNED.md", "tampered")

    assert "read-only" in str(refused)
    assert "body of remote" in str(backend.read(f"{SKILLS_ROUTE}remote/SKILL.md"))


@pytest.mark.parametrize("operation", ["write", "edit", "delete", "upload"])
def test_the_async_half_refuses_too(cfg, session_dir, operation):
    """The half that gets forgotten, and the reason this file exists at all.

    `astream` is the async entry point, so an API deployment reaches `awrite`
    and never `write` -- and the four async overrides are four more chances to
    typo a refusal into a no-op. Untested, they were working; the point is that
    nothing would have said so.

    This codebase has the scar: overriding both `execute` and `aexecute` on
    `LocalShellBackend` nested the sandbox twice and thirteen tests still
    passed, because none of them drove the async path. That story is quoted in
    `skills_backend`'s own docstring as the reason it is built on upstream
    rather than hand-written, and then the async half went untested anyway.
    """
    import asyncio

    catalogue = replace(Definitions.from_config(cfg), skills=_held("remote"))
    backend = build_backend(cfg, session_dir, catalogue=catalogue)
    path = f"{SKILLS_ROUTE}remote/SKILL.md"

    async def attempt():
        if operation == "write":
            return await backend.awrite(f"{SKILLS_ROUTE}remote/PWNED.md", "tampered")
        if operation == "edit":
            return await backend.aedit(path, "body", "tampered")
        if operation == "delete":
            return await backend.adelete(path)
        return await backend.aupload_files([(f"{SKILLS_ROUTE}remote/up.md", b"y")])

    refused = asyncio.run(attempt())

    assert "read-only" in str(refused)
    assert "body of remote" in str(backend.read(path))


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_every_mutating_operation_is_refused(cfg, session_dir, operation):
    """`delete` included: a route the agent can empty is a route it can
    silence."""
    catalogue = replace(Definitions.from_config(cfg), skills=_held("remote"))
    backend = build_backend(cfg, session_dir, catalogue=catalogue)
    path = f"{SKILLS_ROUTE}remote/SKILL.md"

    if operation == "edit":
        refused = backend.edit(path, "body", "tampered")
    else:
        refused = backend.delete(path)

    assert "read-only" in str(refused)
    assert "body of remote" in str(backend.read(path))


# -- what a store cannot do ----------------------------------------------


def test_a_store_backed_catalogue_names_no_directory(cfg):
    """`catalogue_root` answers `None` rather than refusing, which is what let
    the mount above exist at all -- the missing directory is a fact about which
    backend to build, not a wiring error."""
    assert catalogue_root(_held("remote")) is None
    assert catalogue_root(Definitions.from_config(cfg).skills) == cfg.skills_dir


def test_the_shell_is_told_nothing_rather_than_told_a_lie(cfg, session_dir):
    """The limit this does not close, pinned so it stays visible.

    A skill's scripts are run by the shell against `$KINGFISHER_SKILLS`, and a
    store has no path. Setting the variable to something absent would turn "this
    deployment cannot run skill scripts" into `no such file or directory` on a
    path the operator never configured; unset, the failure names the variable.
    """
    catalogue = replace(Definitions.from_config(cfg), skills=_held("remote"))

    assert "KINGFISHER_SKILLS" not in shell_env(cfg, session_dir, catalogue=catalogue)
    assert "KINGFISHER_SKILLS" in shell_env(cfg, session_dir)


def test_a_skill_reaches_the_store_with_every_file_it_ships(tmp_path):
    """Straight through `skills_backend`, so the mount is checked apart from the
    wiring that chooses it."""
    repo = _on_disk(tmp_path, "demo")
    (tmp_path / "demo" / "run.sh").write_text("echo hi\n", encoding="utf-8")

    backend = skills_backend(repo)

    assert "body of demo" in str(backend.read("/demo/SKILL.md"))
    assert "echo hi" in str(backend.read("/demo/run.sh"))


def test_a_skill_shipping_something_binary_does_not_break_the_catalogue(tmp_path, cfg, session_dir):
    """The port says a binary file is decoded lossily rather than refused, and
    that is a choice worth pinning: a skill directory may hold a logo or a
    template beside its definition, and failing the whole catalogue over one of
    them would take every other skill down with it.

    Found by mutation testing -- flipping `errors="replace"` to `"strict"`
    changed nothing any test noticed, which meant the claim was prose only.
    """
    repo = _on_disk(tmp_path, "demo")
    (tmp_path / "demo" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe not utf-8")

    files = repo.files("demo")

    assert set(files) == {"SKILL.md", "logo.png"}
    assert "body of demo" in files["SKILL.md"], "the readable file is unharmed"

    # and the catalogue still mounts, which is the point of not refusing
    catalogue = replace(Definitions.from_config(cfg), skills=InStore({"demo": files}))
    backend = build_backend(cfg, session_dir, catalogue=catalogue)

    assert "body of demo" in str(backend.read(f"{SKILLS_ROUTE}demo/SKILL.md"))


def test_upstream_still_routes_async_uploads_through_the_sync_one():
    """Pinning the delegation this backend relies on instead of overriding it.

    `ReadOnlyStoreBackend` refuses `upload_files` and deliberately does *not*
    refuse `aupload_files`, because upstream implements the async half as
    `await asyncio.to_thread(self.upload_files, files)`. That is a fact about
    somebody else's beta code, so it is asserted rather than assumed: if a
    release gives `aupload_files` its own implementation, this fails and names
    the reason, where the alternative is a skills route that quietly accepts
    async uploads.

    The same check exists one module over for `LocalShellBackend.aexecute`, and
    for the same reason -- overriding both halves there nested the sandbox twice
    while thirteen tests passed.
    """
    import inspect

    from deepagents.backends.store import StoreBackend

    source = inspect.getsource(StoreBackend.aupload_files)

    assert "to_thread" in source, "upstream no longer delegates; override aupload_files"
    assert "self.upload_files" in source, "upstream delegates somewhere else now"
