from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend

from kingfisher.infrastructure.harness.agent import read_only_permissions
from kingfisher.infrastructure.harness.backend import (
    WorkspaceScopedBackend,
    build_backend,
    shell_env,
)
from kingfisher.infrastructure.harness.runlog import log_path
from kingfisher.infrastructure.workspace.sessions import ensure_session_layout
from kingfisher.layout import (
    BUNDLED_SKILLS_ROUTE,
    ROUTES,
    denied_read_scopes,
    denied_scopes,
    routed_paths,
)


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
    """So ~/.aws, ~/.ssh and ~/.config are not where the agent's tooling looks."""
    assert shell_env(cfg, session_dir)["HOME"] == str(session_dir / ".home")
    assert shell_env(cfg, session_dir)["HOME"] != str(cfg.workspace)


def test_a_session_that_was_never_made_is_refused(cfg, tmp_path):
    """This function used to create what it needed, and that is why `.home` and
    `skills/uploaded` were made in one file and listed in none.
    """
    bare = tmp_path / "never-made"
    bare.mkdir()

    with pytest.raises(ValueError, match="ensure_session_layout"):
        build_backend(cfg, bare)


def test_every_name_a_backend_needs_is_named_in_the_refusal(cfg, tmp_path):
    """A message saying only "missing layout" would send a reader to the source to find
    out which names it meant.
    """
    bare = tmp_path / "never-made"
    bare.mkdir()
    (bare / "data").mkdir()

    wanted = r"missing derived, memory, runs, \.home, \.tmp, \.harness, skills/uploaded"
    with pytest.raises(ValueError, match=wanted):
        build_backend(cfg, bare)


def test_backend_is_rooted_at_the_session(cfg, session_dir):
    """One session is one root: virtual paths anchor there, so /data means this
    session's data and no path leads to another session's.
    """
    backend = build_backend(cfg, session_dir)
    assert str(session_dir.resolve()) == str(backend.default.cwd)


def test_data_is_routed_so_the_deny_rule_is_legal(cfg, session_dir):
    """deepagents refuses permissions on an execution backend unless every rule path is
    scoped to a route -- routing /data/ is what makes Q21 possible.
    """
    backend = build_backend(cfg, session_dir)
    assert "/data/" in backend.routes
    assert str((session_dir / "data").resolve()) == str(backend.routes["/data/"].cwd)


def test_skills_is_routed_for_the_same_reason(cfg, session_dir):
    """A request that activates a subset of the skills needs deny rules for the rest,
    and those rules are rejected unless /skills/ is a route too.
    """
    backend = build_backend(cfg, session_dir)
    assert "/skills/" in backend.routes
    assert str((cfg.workspace / "skills").resolve()) == str(backend.routes["/skills/"].cwd)


def test_every_route_the_layout_declares_is_one_the_backend_mounts(cfg, session_dir):
    """The table and what backs it are in two modules, so something has to tie them."""
    backend = build_backend(cfg, session_dir)
    declared = set(routed_paths())
    generated = {r for r in backend.routes if r.startswith(BUNDLED_SKILLS_ROUTE)}

    assert declared <= set(backend.routes), "declared in the layout, mounted nowhere"
    assert set(backend.routes) - generated == declared, (
        "mounted by the builder, absent from kingfisher.layout.ROUTES -- add it there, "
        "or it has no deny rule and nothing says it exists"
    )


def test_the_deny_rules_are_the_two_the_layout_declares(cfg, session_dir):
    """Pinned rather than derived twice."""
    assert denied_scopes() == ("/.harness/**", "/data/**", "/skills/**")
    assert denied_read_scopes() == ("/.harness/**",)
    assert [p.paths for p in read_only_permissions()] == [
        ["/.harness/**"], ["/data/**"], ["/skills/**"], ["/.harness/**"],
    ]
    assert {p.mode for p in read_only_permissions()} == {"deny"}
    assert {tuple(p.operations) for p in read_only_permissions()} == {("write",), ("read",)}


def test_derived_is_unrouted_and_the_table_says_so(cfg, session_dir):
    """The absence used to be the only record of it."""
    backend = build_backend(cfg, session_dir)
    unrouted = {r.path for r in ROUTES if not r.routed}

    assert unrouted == {"/derived/", "/runs/"}
    assert not (unrouted & set(backend.routes)), "an unrouted path was mounted"


def test_a_host_path_to_a_file_tool_is_refused_not_mirrored(cfg, session_dir):
    """The observed bug: it succeeded, and the file was not where it looked."""
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
    """Pins the coupling to a private deepagents method."""
    assert hasattr(CompositeBackend, "_get_backend_and_key")
    assert WorkspaceScopedBackend._get_backend_and_key is not CompositeBackend._get_backend_and_key


def test_tmpdir_is_the_session_s_own(cfg, session_dir):
    """One shared scratch directory was swept by nothing, counted against nothing, and
    readable by every other session's shell. Per session, `reap` and `session_bytes`
    already cover it and neither fence has to grant anything extra.
    """
    assert shell_env(cfg, session_dir)["TMPDIR"] == str(session_dir / ".tmp")


def test_two_sessions_do_not_share_a_tmpdir(cfg, session_dir, workspace):
    """The cross-session channel this closed: what one caller derived sat where another
    caller's agent could read it."""
    other = ensure_session_layout(workspace / "sessions" / "second")

    assert shell_env(cfg, session_dir)["TMPDIR"] != shell_env(cfg, other)["TMPDIR"]


def test_tmpdir_is_created_private(cfg, session_dir):
    """The mode the shared scratch directory had, kept rather than quietly widened.

    Not a boundary on its own -- `derived/` sits beside it at whatever the umask gave
    it -- and `ensure_session_layout` says so where it does this.
    """
    assert (session_dir / ".tmp").stat().st_mode & 0o077 == 0


def test_state_dir_defaults_and_relocates(cfg, tmp_path):
    """The sandbox profile moves with `state_dir`, and the agent addresses neither."""
    assert cfg.state_dir == cfg.workspace / ".kingfisher"

    relocated = replace(cfg, state_root=tmp_path / "state")
    assert relocated.state_dir == tmp_path / "state"


def test_the_run_log_is_the_session_s_own(session_dir):
    """It was `<state_dir>/runs/<id>.jsonl`, which nothing deleted when the session
    went: one file per session that had ever existed, kept for good."""
    assert log_path(session_dir) == session_dir / ".harness" / "runlog.jsonl"


def test_a_refused_host_path_reaches_the_agent_as_a_tool_error(cfg, session_dir):
    """The guard exists to correct the model mid-turn, and its message names the virtual
    path to use.
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

    A delegate is built with the parent's backend and inherits none of the parent's
    middleware, so `reject_host_path` fired for it exactly as it fires for the parent
    and the correction had nothing to turn it into. Measured before the fix:
    `HostPathError` out of the delegate's graph, killing the run.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir
    from tests.unit.test_delegation_ceiling import _subagent_graphs

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
    """`reap` removes session directories and nothing else."""
    from kingfisher.infrastructure.workspace.sessions import LocalSessionDirs

    home = Path(shell_env(cfg, session_dir)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / ".cache").mkdir()
    (home / ".cache" / "big.bin").write_bytes(b"x" * 1000)

    assert LocalSessionDirs().remove_tree(session_dir) is None
    assert not home.exists(), "the cache outlived the session it belonged to"


def test_what_tools_cache_counts_against_the_session_quota(cfg, session_dir):
    """`session_max_bytes` measures a session."""
    from kingfisher.infrastructure.workspace.sessions import session_bytes

    before = session_bytes(session_dir)
    home = Path(shell_env(cfg, session_dir)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "cached.bin").write_bytes(b"x" * 5000)

    assert session_bytes(session_dir) >= before + 5000


def test_home_is_not_shared_between_two_sessions(cfg, workspace):
    """One session's cached tokens or tool config must not be another's."""
    from kingfisher.infrastructure.workspace.sessions import ensure_session_layout

    first = ensure_session_layout(workspace / "sessions" / "one")
    second = ensure_session_layout(workspace / "sessions" / "two")

    assert shell_env(cfg, first)["HOME"] != shell_env(cfg, second)["HOME"]


def test_the_catalogue_is_named_because_the_shell_cannot_derive_it(cfg, session_dir):
    """Every other virtual path becomes a shell path by dropping the slash."""
    env = shell_env(cfg, session_dir)

    assert env["KINGFISHER_SKILLS"] == str(cfg.skills_dir)
    assert not Path(env["KINGFISHER_SKILLS"]).is_relative_to(session_dir)


def test_the_home_directory_exists_before_a_command_runs(cfg, session_dir):
    """A `HOME` that does not exist is worse than none: tools fall back to somewhere
    unpredictable rather than failing.
    """
    from kingfisher.infrastructure.harness.backend import agent_home, build_backend

    build_backend(cfg, session_dir)

    assert agent_home(session_dir).is_dir()


# -- one file, listed once ------------------------------------------------
#
# Three of the routes point *inside* the default backend's own root: `/data`,
# `/memory` and `/skills/uploaded` are real directories under the session.
# `CompositeBackend` merges every backend's answer, so each of those files was
# found twice -- once by the route and once by the default walking past it.
# `/skills` never showed it, because it points at the catalogue, somewhere else
# entirely.

def _rows(result):
    """The matches in a listing, insisting it actually succeeded."""
    assert result.matches is not None, result.error
    return result.matches


#: Every route that lives under the session root, and so was doubled.
INSIDE_THE_ROOT = {
    "/data": ("data",),
    "/memory": ("memory",),
    "/skills/uploaded": ("skills", "uploaded"),
}


@pytest.mark.parametrize(("route", "parts"), INSIDE_THE_ROOT.items(), ids=INSIDE_THE_ROOT)
def test_a_routed_file_is_globbed_once(cfg, session_dir, route, parts):
    """Measured before this: `--data orders.csv` reached the model as
    `['/data/orders.csv', '/data/orders.csv']`, on every pattern tried.
    """
    backend = build_backend(cfg, session_dir)
    where = session_dir.joinpath(*parts)
    where.mkdir(parents=True, exist_ok=True)
    (where / "probe.txt").write_text("needle\n", encoding="utf-8")

    found = [one["path"] for one in _rows(backend.glob(f"{route}/probe.txt"))]

    assert found == [f"{route}/probe.txt"]


@pytest.mark.parametrize(("route", "parts"), INSIDE_THE_ROOT.items(), ids=INSIDE_THE_ROOT)
def test_a_routed_file_is_grepped_once(cfg, session_dir, route, parts):
    """From the root, which is where the two answers meet."""
    backend = build_backend(cfg, session_dir)
    where = session_dir.joinpath(*parts)
    where.mkdir(parents=True, exist_ok=True)
    (where / "probe.txt").write_text("needle\n", encoding="utf-8")

    found = [one["path"] for one in _rows(backend.grep("needle", path="/"))]

    assert found == [f"{route}/probe.txt"]


def test_a_file_matching_twice_still_reports_both(cfg, session_dir):
    """The half that says this is deduplication and not collapsing."""
    backend = build_backend(cfg, session_dir)
    data = session_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "probe.txt").write_text("needle one\nquiet\nneedle two\n", encoding="utf-8")

    matches = _rows(backend.grep("needle", path="/data"))

    assert [one["line"] for one in matches] == [1, 3]
    assert [one["text"] for one in matches] == ["needle one", "needle two"]


def test_two_different_files_are_both_still_listed(cfg, session_dir):
    """The other half: nothing is dropped for being similar, only for being the same
    thing twice.
    """
    backend = build_backend(cfg, session_dir)
    data = session_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in ("one.txt", "two.txt"):
        (data / name).write_text("needle\n", encoding="utf-8")

    found = sorted(one["path"] for one in _rows(backend.glob("/data/*.txt")))

    assert found == ["/data/one.txt", "/data/two.txt"]


def test_a_hard_failure_is_passed_through_rather_than_emptied(cfg, session_dir):
    """`matches` is `None` on a hard failure and `[]` on a search that found nothing,
    and the two say different things.
    """
    from dataclasses import dataclass

    from kingfisher.infrastructure.harness.backend import _once

    @dataclass
    class Failed:
        error: str | None = "broke"
        matches: list | None = None
        truncated: bool = False

    assert _once(Failed(), key=lambda one: one).matches is None
