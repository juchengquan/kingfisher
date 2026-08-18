from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend

from kingfisher.config import ConfigError
from kingfisher.infrastructure.harness.backend import (
    WorkspaceScopedBackend,
    build_backend,
    prepare_scratch,
    shell_env,
)
from kingfisher.infrastructure.harness.checkpointing import checkpoint_db_path
from kingfisher.infrastructure.harness.runlog import log_path


def test_shell_env_carries_no_credentials(cfg, session_dir):
    """The allowlist is the security story: the shell can run tools, not read keys."""
    env = shell_env(cfg, session_dir)
    assert cfg.models.resolve()[1].api_key not in env.values()
    assert not any("KEY" in name or "TOKEN" in name or "SECRET" in name for name in env)


def test_shell_env_supplies_a_usable_toolchain(cfg, session_dir):
    """LocalShellBackend defaults to an EMPTY env -- without PATH nothing resolves."""
    env = shell_env(cfg, session_dir)
    assert env["PATH"]
    assert "/usr/bin" in env["PATH"]


def test_home_points_at_this_session_not_the_real_home(cfg, session_dir):
    """So ~/.aws, ~/.ssh and ~/.config are not where the agent's tooling looks.

    Per session rather than the workspace, so what tools cache under `~` is
    disposed of by `reap` and counted by `session_max_bytes` -- neither of which
    reached it while it sat at the workspace root.
    """
    assert shell_env(cfg, session_dir)["HOME"] == str(session_dir / ".home")
    assert shell_env(cfg, session_dir)["HOME"] != str(cfg.workspace)


def test_backend_is_rooted_at_the_session(cfg, session_dir):
    """One session is one root: virtual paths anchor there, so /data means
    this session's data and no path leads to another session's."""
    backend = build_backend(cfg, session_dir)
    assert str(session_dir.resolve()) == str(backend.default.cwd)


def test_data_is_routed_so_the_deny_rule_is_legal(cfg, session_dir):
    """deepagents refuses permissions on an execution backend unless every rule
    path is scoped to a route -- routing /data/ is what makes Q21 possible."""
    backend = build_backend(cfg, session_dir)
    assert "/data/" in backend.routes
    assert str((session_dir / "data").resolve()) == str(backend.routes["/data/"].cwd)


def test_skills_is_routed_for_the_same_reason(cfg, session_dir):
    """A request that activates a subset of the skills needs deny rules for the
    rest, and those rules are rejected unless /skills/ is a route too. Caught by
    a live run, not by a unit test -- the wiring tests spy on create_deep_agent
    and so never reach deepagents' own validation."""
    backend = build_backend(cfg, session_dir)
    assert "/skills/" in backend.routes
    assert str((cfg.workspace / "skills").resolve()) == str(backend.routes["/skills/"].cwd)


def test_a_host_path_to_a_file_tool_is_refused_not_mirrored(cfg, session_dir):
    """The observed bug: it succeeded, and the file was not where it looked.

    Passing `<workspace>/runs/s/t/report.md` produced
    `<workspace>/Users/.../runs/s/t/report.md`, so `load_result` never found
    the deliverable.
    """
    backend = build_backend(cfg, session_dir)
    host_path = f"{cfg.workspace}/runs/s1/t001/report.md"

    with pytest.raises(ValueError, match="is a host path"):
        backend.write(host_path, "content")

    assert not (cfg.workspace / "Users").exists()
    assert not (cfg.workspace / str(cfg.workspace).lstrip("/")).exists()


def test_the_refusal_names_the_path_that_was_meant(cfg, session_dir):
    """An error the model can act on beats one it can only apologise for."""
    backend = build_backend(cfg, session_dir)

    with pytest.raises(ValueError, match=r"Use '/runs/t001/report\.md' instead"):
        backend.write(f"{session_dir}/runs/t001/report.md", "content")


@pytest.mark.parametrize(
    "host_path",
    ["/tmp/scratch.py", "/Users/someone/notes.md", "/etc/passwd", "/var/log/x"],
)
def test_other_host_roots_are_refused_too(cfg, host_path, session_dir):
    """`/tmp/scratch.py` is the example system.md warns about by name."""
    backend = build_backend(cfg, session_dir)

    with pytest.raises(ValueError, match="is a host path"):
        backend.write(host_path, "content")


@pytest.mark.parametrize("virtual_path", ["/runs/s1/t001/report.md", "/derived/x.csv"])
def test_virtual_paths_still_work(cfg, virtual_path, session_dir):
    """The guard must not cost the agent its ordinary vocabulary."""
    backend = build_backend(cfg, session_dir)
    backend.write(virtual_path, "content")

    assert backend.read(virtual_path)


def test_every_read_and_write_path_resolves_through_the_guarded_hook():
    """Pins the coupling to a private deepagents method.

    The guard lives in `_get_backend_and_key` because every path-addressed
    operation resolves through it. If an upgrade renames it, the override stops
    being called and the guard silently disappears — so this fails the build
    instead.
    """
    assert hasattr(CompositeBackend, "_get_backend_and_key")
    assert WorkspaceScopedBackend._get_backend_and_key is not CompositeBackend._get_backend_and_key


def test_scratch_defaults_inside_the_workspace(cfg, session_dir):
    """Unset means self-contained: scratch is disposed of with the workspace."""
    assert cfg.scratch_dir == cfg.workspace / ".kingfisher" / "tmp"
    assert shell_env(cfg, session_dir)["TMPDIR"] == str(cfg.scratch_dir)


def test_scratch_can_be_relocated(cfg, tmp_path, session_dir):
    """One fixed location per machine, e.g. /tmp, is a config change."""
    relocated = replace(cfg, scratch_root=tmp_path / "kingfisher-scratch")

    assert relocated.scratch_dir == tmp_path / "kingfisher-scratch"
    assert shell_env(relocated, session_dir)["TMPDIR"] == str(relocated.scratch_dir)


def test_prepare_scratch_creates_a_private_directory(cfg, tmp_path):
    """0700: /tmp is world-writable, and scratch derives from /data."""
    relocated = replace(cfg, scratch_root=tmp_path / "scratch")

    created = prepare_scratch(relocated)

    assert created.is_dir()
    assert created.stat().st_mode & 0o077 == 0


def test_prepare_scratch_tightens_an_existing_loose_directory(cfg, tmp_path):
    """Every workspace made before this check has a 0755 scratch directory.

    `mkdir(mode=...)` is ignored when the directory already exists, so without
    the chmod those directories would stay world-readable forever — or, if this
    raised instead, stop working entirely.
    """
    existing = tmp_path / "existing"
    existing.mkdir()
    existing.chmod(0o755)

    prepare_scratch(replace(cfg, scratch_root=existing))

    assert existing.stat().st_mode & 0o077 == 0


def test_prepare_scratch_refuses_something_that_is_not_a_directory(cfg, tmp_path):
    """A symlink in /tmp is the classic way to redirect someone else's writes."""
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(ConfigError, match="symlink or not a directory"):
        prepare_scratch(replace(cfg, scratch_root=link))


def test_state_dir_defaults_and_relocates(cfg, tmp_path):
    """Logs and the thread db move together; the agent addresses neither."""
    assert cfg.state_dir == cfg.workspace / ".kingfisher"

    relocated = replace(cfg, state_root=tmp_path / "state")
    assert relocated.state_dir == tmp_path / "state"
    assert checkpoint_db_path(relocated) == tmp_path / "state" / "threads.db"
    assert log_path(relocated.state_dir, "s1") == tmp_path / "state" / "runs" / "s1.jsonl"


def test_scratch_follows_a_relocated_state_dir(cfg, tmp_path):
    """Scratch defaults *under* state, so moving state moves scratch with it."""
    relocated = replace(cfg, state_root=tmp_path / "state")

    assert relocated.scratch_dir == tmp_path / "state" / "tmp"


def test_a_refused_host_path_reaches_the_agent_as_a_tool_error(cfg, session_dir):
    """The guard exists to correct the model mid-turn, and its message names
    the virtual path to use. That only works if the message arrives.

    It raised straight out of the tool call instead, killing the run: three
    live smoke runs died this way, on `analyze.py`, on a skill's SKILL.md, and
    again with skills off. deepagents converts `ValueError` raised during path
    *validation*, but `backend.write()` is called outside that guard.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel

    host_path = f"{cfg.workspace}/runs/s1/t001/notes.md"
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "write_file", "args": {"file_path": host_path, "content": "x"}, "id": "c1"}
            ],
        ),
        AIMessage(content="retried and finished"),
    ]

    agent = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses))
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is a host path" in transcript  # the correction reached the model
    assert "/runs/s1/t001/notes.md" in transcript  # including what to use instead
    assert out["messages"][-1].content == "retried and finished"  # the run survived


def test_a_delegate_gets_the_correction_too(cfg, session_dir):
    """The same guard, one level down, where the same backend raises.

    A delegate is built with the parent's backend and inherits none of the
    parent's middleware, so `reject_host_path` fired for it exactly as it fires
    for the parent and the correction had nothing to turn it into. Measured
    before the fix: `HostPathError` out of the delegate's graph, killing the run.

    Worse than it looks from the parent's side. This needs no workspace tools at
    all -- `write_file` is a built-in, and a delegate that leaves
    `builtin_tools` out has every one of them.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir
    from tests.test_delegation_ceiling import _subagent_graphs

    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "writer.yaml").write_text(
        "name: writer\ndescription: Writes a file.\nsystem_prompt: |\n  You write files.\n",
        encoding="utf-8",
    )

    host_path = f"{cfg.workspace}/runs/s1/t001/notes.md"
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "write_file", "args": {"file_path": host_path, "content": "x"}, "id": "c1"}
            ],
        ),
        AIMessage(content="retried and finished"),
    ]

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(subagents=("writer",)),
    )
    out = _subagent_graphs(graph)["writer"].invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is a host path" in transcript  # the correction reached the delegate
    assert "/runs/s1/t001/notes.md" in transcript  # including what to use instead
    assert out["messages"][-1].content == "retried and finished"  # the run survived


# -- the agent's HOME ------------------------------------------------------


def test_what_tools_cache_under_home_is_disposed_of_with_the_session(cfg, session_dir):
    """`reap` removes session directories and nothing else. With `HOME` at the
    workspace root, `~/.cache/uv` and `~/Library/Caches/pip` accumulated beside
    `skills/` and were never swept -- 59MB in one real workspace.
    """
    from kingfisher.infrastructure.workspace_fs import LocalSessionDirs

    home = Path(shell_env(cfg, session_dir)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / ".cache").mkdir()
    (home / ".cache" / "big.bin").write_bytes(b"x" * 1000)

    assert LocalSessionDirs().remove_tree(session_dir) is None
    assert not home.exists(), "the cache outlived the session it belonged to"


def test_what_tools_cache_counts_against_the_session_quota(cfg, session_dir):
    """`session_max_bytes` measures a session. A cache above every session was
    invisible to it, so a session could hold a gigabyte of wheels and report
    nothing."""
    from kingfisher.infrastructure.workspace_fs import session_bytes

    before = session_bytes(session_dir)
    home = Path(shell_env(cfg, session_dir)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "cached.bin").write_bytes(b"x" * 5000)

    assert session_bytes(session_dir) >= before + 5000


def test_home_is_not_shared_between_two_sessions(cfg, workspace):
    """One session's cached tokens or tool config must not be another's."""
    from kingfisher.infrastructure.workspace_fs import ensure_session_layout

    first = ensure_session_layout(workspace / "sessions" / "one")
    second = ensure_session_layout(workspace / "sessions" / "two")

    assert shell_env(cfg, first)["HOME"] != shell_env(cfg, second)["HOME"]


def test_the_catalogue_is_named_because_the_shell_cannot_derive_it(cfg, session_dir):
    """Every other virtual path becomes a shell path by dropping the slash.
    `/skills` is the exception -- it is shared, so it lives above the session --
    and it used to be reachable as `$HOME/skills` only because `HOME` was the
    workspace. Moving `HOME` into the session broke that, so the catalogue is
    named outright.
    """
    env = shell_env(cfg, session_dir)

    assert env["KINGFISHER_SKILLS"] == str(cfg.skills_dir)
    assert not Path(env["KINGFISHER_SKILLS"]).is_relative_to(session_dir)


def test_the_home_directory_exists_before_a_command_runs(cfg, session_dir):
    """A `HOME` that does not exist is worse than none: tools fall back to
    somewhere unpredictable rather than failing."""
    from kingfisher.infrastructure.harness.backend import agent_home, build_backend

    build_backend(cfg, session_dir)

    assert agent_home(session_dir).is_dir()
