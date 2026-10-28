from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import GlobResult, GrepResult

from kingfisher.core.agent.assembly import read_only_permissions
from kingfisher.core.agent.host_paths import (
    HOST_ROOTS,
    HostPathError,
    reject_host_path,
)
from kingfisher.core.layout import (
    BUNDLED_SKILLS_ROUTE,
    DEFAULT_LAYOUT,
    SCRATCH_ROUTE,
)
from kingfisher.plugins.sessions.backend import WorkspaceScopedBackend, backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from kingfisher.plugins.sessions.sandbox import confinement
from kingfisher.plugins.sessions.sandbox.bubblewrap import bubblewrap_available
from kingfisher.plugins.sessions.shell import shell_env
from tests.unit.test_bundles import (
    OWN_TOOL_AND_SKILL,
    with_private_skill,
    workspace_with_bundle,
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
    assert shell_env(cfg, session_dir)["HOME"] == str(session_dir / "scratchpad")
    assert shell_env(cfg, session_dir)["HOME"] != str(cfg.workspace)


def test_a_session_that_was_never_made_is_refused(cfg, tmp_path):
    """This function used to create what it needed, and that is why `.home` and
    `.tmp` were made in one file and listed in none.
    """
    bare = tmp_path / "never-made"
    bare.mkdir()

    with pytest.raises(ValueError, match="ensure_session_layout"):
        backend_at(cfg, bare)


def test_every_name_a_backend_needs_is_named_in_the_refusal(cfg, tmp_path):
    """A message saying only "missing layout" would send a reader to the source to find
    out which names it meant.
    """
    bare = tmp_path / "never-made"
    bare.mkdir()
    (bare / "inputs").mkdir()

    wanted = (
        r"missing outputs, memory, scratchpad, \.harness, "
        r"scratchpad/tool_results, scratchpad/conversation_history, "
        r"scratchpad/large_tool_results;"
    )
    with pytest.raises(ValueError, match=wanted):
        backend_at(cfg, bare)


def test_backend_is_rooted_at_the_session(cfg, session_dir):
    """One session is one root: virtual paths anchor there, so /inputs means this
    session's inputs and no path leads to another session's.
    """
    backend = backend_at(cfg, session_dir)
    assert str(session_dir.resolve()) == str(backend.default.cwd)


def test_inputs_is_routed_so_the_deny_rule_is_legal(cfg, session_dir):
    """deepagents refuses permissions on an execution backend unless every rule path is
    scoped to a route -- routing /inputs/ is what makes Q21 possible.
    """
    backend = backend_at(cfg, session_dir)
    assert "/inputs/" in backend.routes
    assert str((session_dir / "inputs").resolve()) == str(backend.routes["/inputs/"].cwd)


def test_skills_is_routed_for_the_same_reason(cfg, session_dir):
    """A request that activates a subset of the skills needs deny rules for the rest,
    and those rules are rejected unless /skills/ is a route too.
    """
    backend = backend_at(cfg, session_dir)
    assert "/skills/" in backend.routes
    assert str((cfg.definitions / "skills").resolve()) == str(backend.routes["/skills/"].cwd)


def test_every_route_the_layout_declares_is_one_the_backend_mounts(cfg, session_dir):
    """The table and what backs it are in two modules, so something has to tie them."""
    backend = backend_at(cfg, session_dir)
    declared = set(DEFAULT_LAYOUT.routed_paths())
    generated = {r for r in backend.routes if r.startswith(BUNDLED_SKILLS_ROUTE)}

    assert declared <= set(backend.routes), "declared in the layout, mounted nowhere"
    assert set(backend.routes) - generated == declared, (
        "mounted by the builder, absent from `Layout.routes` -- add it there, "
        "or it has no deny rule and nothing says it exists"
    )


def test_the_deny_rules_are_the_ones_the_layout_declares(cfg, session_dir):
    """Pinned rather than derived twice."""
    assert DEFAULT_LAYOUT.denied_scopes() == (
        "/.harness/**",
        "/inputs/**",
        "/scratchpad/blobs/**",
        "/scratchpad/conversation_history/**",
        "/scratchpad/large_tool_results/**",
        "/scratchpad/tool_results/**",
        "/skills/**",
    )
    assert DEFAULT_LAYOUT.denied_read_scopes() == ("/.harness/**",)
    rules = read_only_permissions(DEFAULT_LAYOUT)
    assert [p.paths for p in rules] == [
        ["/.harness/**"],
        ["/inputs/**"],
        ["/scratchpad/blobs/**"],
        ["/scratchpad/conversation_history/**"],
        ["/scratchpad/large_tool_results/**"],
        ["/scratchpad/tool_results/**"],
        ["/skills/**"],
        ["/.harness/**"],
    ]
    assert {p.mode for p in rules} == {"deny"}
    assert {tuple(p.operations) for p in rules} == {("write",), ("read",)}


def test_outputs_is_unrouted_and_the_table_says_so(cfg, session_dir):
    """The absence used to be the only record of it."""
    backend = backend_at(cfg, session_dir)
    unrouted = {r.path for r in DEFAULT_LAYOUT.routes if not r.routed}

    assert unrouted == {"/outputs/", "/scratchpad/"}
    assert not (unrouted & set(backend.routes)), "an unrouted path was mounted"


def test_a_host_path_to_a_file_tool_is_refused_not_mirrored(cfg, session_dir):
    """The observed bug: it succeeded, and the file was not where it looked."""
    backend = backend_at(cfg, session_dir)
    host_path = f"{cfg.workspace}/sessions/s1/scratchpad/report.md"

    with pytest.raises(ValueError, match="is a host path"):
        backend.write(host_path, "content")

    assert not (cfg.workspace / "Users").exists()
    assert not (cfg.workspace / str(cfg.workspace).lstrip("/")).exists()


def test_the_backend_is_scoped_to_the_workspace_and_not_to_one_session(cfg, session_dir):
    """The wiring, named rather than inferred from behaviour. `workspace` was handed the
    session directory -- the two were one value, because the session is the only prefix
    the refusal's suggestion can be built from -- which left the clause covering "a host
    path inside the workspace" covering one session.
    """
    backend = backend_at(cfg, session_dir)

    assert backend.workspace == cfg.workspace
    assert backend._session_dir == session_dir


#: A workspace outside every `HOST_ROOTS` prefix. `/workspace` is what the shipped
#: `Dockerfile` sets, and it is the shape no test had: a pytest `tmp_path` always sits
#: under `/private/` or `/tmp/`, so a path anywhere in it was refused by the host-root
#: clause for an unrelated reason and the workspace clause was never reached.
CONTAINER = Path("/workspace")


@pytest.mark.parametrize(
    "probe",
    [
        "sessions/another/outputs/secret.txt",
        "skills/report/SKILL.md",
        "models.yaml",
        "agents/analyst.yaml",
    ],
)
def test_a_workspace_the_host_roots_do_not_name_still_refuses_its_own_paths(probe):
    """Everything in the workspace is a host path to a file tool, not just this session.

    Driven against `reject_host_path` rather than a built backend, because a backend
    needs its directories to exist and `/workspace` is not a path a test may make. That
    is the same reason this went unnoticed: the only workspace shape the suite builds is
    one the host-root clause happens to catch.
    """
    session = CONTAINER / "sessions" / "s1"

    with pytest.raises(HostPathError, match="is a host path"):
        reject_host_path(str(CONTAINER / probe), CONTAINER, session_dir=session)


def test_only_a_path_in_this_session_is_offered_a_virtual_one(cfg, session_dir):
    """The two refusals differ, and the difference is the whole reason the session is
    passed separately: `/sessions/another/...` has no virtual spelling to suggest, so
    offering one would send the model to a path that resolves somewhere else again.
    """
    backend = backend_at(cfg, session_dir)

    with pytest.raises(HostPathError, match=r"Use '/scratchpad/report\.md' instead"):
        backend.write(f"{session_dir}/scratchpad/report.md", "x")

    with pytest.raises(HostPathError) as raised:
        backend.write(f"{cfg.workspace}/sessions/another/scratchpad/report.md", "x")

    assert "Use '/" not in str(raised.value)
    assert "Use the shell for host paths" in str(raised.value)


def test_the_refusal_names_the_path_that_was_meant(cfg, session_dir):
    """An error the model can act on beats one it can only apologise for."""
    backend = backend_at(cfg, session_dir)

    with pytest.raises(ValueError, match=r"Use '/scratchpad/report\.md' instead"):
        backend.write(f"{session_dir}/scratchpad/report.md", "content")


#: The prefixes a file tool must refuse, written here as well as in `HOST_ROOTS`, and
#: on purpose. The rule below drives whatever that tuple holds, so it covers an entry
#: added later -- and for the same reason it cannot notice one *removed*: deleting four
#: of the eight deleted four of its cases and left it green. A deny-list's contents are
#: the claim worth pinning, so they are claimed twice and compared.
REFUSED_PREFIXES = frozenset({
    "/Users/", "/home/", "/tmp/", "/private/", "/etc/", "/var/", "/usr/", "/opt/",
})


def test_the_host_roots_are_the_prefixes_this_repository_says_they_are():
    """So a prefix dropped from the tuple fails here rather than quietly removing the
    case that would have caught it.
    """
    assert set(HOST_ROOTS) == REFUSED_PREFIXES
    assert all(root.startswith("/") and root.endswith("/") for root in HOST_ROOTS)


def test_other_host_roots_are_refused_too(cfg, session_dir):
    """`/tmp/scratch.py` is the example the file tools' section warns about by name.

    One backend for all of them: every case refuses, so nothing is written and no
    iteration can leave anything for the next.

    Over `HOST_ROOTS` itself rather than a list written beside it. Four of the eight
    were driven, and deleting the other four -- `/home/`, `/private/`, `/usr/` and
    `/opt/` -- left the suite green, with `/usr/local/share/notes.md` then written into
    a mirror of the host tree inside the session.
    """
    backend = backend_at(cfg, session_dir)

    for host_path in [f"{root}probe.md" for root in HOST_ROOTS]:
        with pytest.raises(ValueError, match="is a host path") as refused:
            backend.write(host_path, "content")

        # The path it offers has to be one the agent can write to. It named
        # `/runs/<session>/<turn>/` for as long as that directory was gone.
        assert f"{SCRATCH_ROUTE}<name>" in str(refused.value), host_path


def test_virtual_paths_still_work(cfg, session_dir):
    """The guard must not cost the agent its ordinary vocabulary."""
    backend = backend_at(cfg, session_dir)

    for virtual_path in ("/scratchpad/report.md", "/outputs/x.csv"):
        backend.write(virtual_path, "content")

        assert backend.read(virtual_path), virtual_path


def test_every_read_and_write_path_resolves_through_the_guarded_hook():
    """Pins the coupling to a private deepagents method.

    If an upgrade renames `_get_backend_and_key` the override stops being called and the
    guard disappears silently, so this fails the build instead.
    """
    assert hasattr(CompositeBackend, "_get_backend_and_key")
    assert WorkspaceScopedBackend._get_backend_and_key is not CompositeBackend._get_backend_and_key


def test_scratch_is_the_session_s_own(cfg, session_dir):
    """One shared scratch directory was swept by nothing, counted against nothing, and
    readable by every other session's shell. Per session, `reap` and `session_bytes`
    already cover it and neither fence has to grant anything extra.
    """
    assert shell_env(cfg, session_dir)["TMPDIR"] == str(session_dir / "scratchpad")


def test_two_sessions_do_not_share_a_tmpdir(cfg, session_dir, workspace):
    """The cross-session channel this closed: what one caller derived sat where another
    caller's agent could read it."""
    other = ensure_session_layout(workspace / "sessions" / "second", DEFAULT_LAYOUT)

    assert shell_env(cfg, session_dir)["TMPDIR"] != shell_env(cfg, other)["TMPDIR"]


def test_scratch_is_created_private(cfg, session_dir):
    """The mode the shared scratch directory had, kept rather than quietly widened.

    Not a boundary on its own -- `outputs/` sits beside it at whatever the umask gave
    it -- and `ensure_session_layout` says so where it does this.
    """
    assert (session_dir / "scratchpad").stat().st_mode & 0o077 == 0


def test_a_refused_host_path_reaches_the_agent_as_a_tool_error(cfg, session_dir):
    """The guard exists to correct the model mid-turn, and its message names the virtual
    path to use.

    `awrap_tool_call` is a copy of `wrap_tool_call`, which `test_async_twins.py` holds:
    driven by no test, making it re-raise instead of correcting once left the whole suite
    green, and an async turn is the one a service runs.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.core.agent.assembly import build_agent
    from tests.conftest import FakeToolCallingModel

    host_path = f"{cfg.workspace}/sessions/s1/scratchpad/notes.md"
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
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=responses)).graph
    asked = {"messages": [{"role": "user", "content": "go"}]}
    out = agent.invoke(asked, config={"recursion_limit": 12})

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is a host path" in transcript  # the correction reached the model
    # Not the host path's own tail: that is echoed back in the refusal, and
    # asserting on it passes whatever the correction says.
    assert f"{SCRATCH_ROUTE}<name>" in transcript  # including what to use instead
    assert out["messages"][-1].content == "retried and finished"  # the run survived


def test_a_subagent_gets_the_correction_too(cfg, session_dir):
    """The same guard, one level down, where the same backend raises.

    A subagent is built with the parent's backend and inherits none of the parent's
    middleware, so `reject_host_path` fired for it exactly as it fires for the parent
    and the correction had nothing to turn it into. Measured before the fix:
    `HostPathError` out of the subagent's graph, killing the run.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.core.access.capabilities import Capabilities
    from kingfisher.core.agent.assembly import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "writer.yaml").write_text(
        'name: writer\ndescription: Writes a file.\nbuiltin_tools: ["*"]\ntools: ["*"]\n'
        "system_prompt: |\n  You write files.\n",
        encoding="utf-8",
    )

    host_path = f"{cfg.workspace}/sessions/s1/scratchpad/notes.md"
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
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(subagents=("writer",)),
    ).graph
    out = _subagent_graphs(graph)["writer"].invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )

    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is a host path" in transcript  # the correction reached the subagent
    assert f"{SCRATCH_ROUTE}<name>" in transcript  # including what to use instead
    assert out["messages"][-1].content == "retried and finished"  # the run survived


# -- the agent's HOME ------------------------------------------------------


def test_what_tools_cache_under_home_is_disposed_of_with_the_session(cfg, session_dir):
    """`reap` removes session directories and nothing else."""
    from kingfisher.plugins.sessions.folders import LocalSessionDirs

    home = Path(shell_env(cfg, session_dir)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / ".cache").mkdir()
    (home / ".cache" / "big.bin").write_bytes(b"x" * 1000)

    assert LocalSessionDirs().remove_tree(session_dir) is None
    assert not home.exists(), "the cache outlived the session it belonged to"


def test_what_tools_cache_counts_against_the_session_quota(cfg, session_dir):
    """`session_max_bytes` measures a session."""
    from kingfisher.plugins.sessions.folders import session_bytes

    before = session_bytes(session_dir)
    home = Path(shell_env(cfg, session_dir)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "cached.bin").write_bytes(b"x" * 5000)

    assert session_bytes(session_dir) >= before + 5000


def test_home_is_not_shared_between_two_sessions(cfg, workspace):
    """One session's cached tokens or tool config must not be another's."""
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    first = ensure_session_layout(workspace / "sessions" / "one", DEFAULT_LAYOUT)
    second = ensure_session_layout(workspace / "sessions" / "two", DEFAULT_LAYOUT)

    assert shell_env(cfg, first)["HOME"] != shell_env(cfg, second)["HOME"]


def test_the_catalogue_is_named_because_the_shell_cannot_derive_it(cfg, session_dir):
    """Every other virtual path is reached from `$KINGFISHER_SESSION_ROOT`."""
    env = shell_env(cfg, session_dir)

    assert env["KINGFISHER_SKILLS"] == str(cfg.skills_dir)
    assert not Path(env["KINGFISHER_SKILLS"]).is_relative_to(session_dir)


#: A script under the macOS fence starts this interpreter, which the GitHub macOS runner's
#: `sandbox-exec` refuses to start, as `draws_in_the_fence` says of the same runner.
_STARTS_PYTHON_FENCED = pytest.mark.skipif(
    os.environ.get("CI") == "true" and platform.system() == "Darwin",
    reason="the macOS runner's sandbox refuses to start the venv's interpreter",
)


def _every_sandbox(*on_auto: Any) -> list[Any]:
    """Every setting kingfisher's own shell runs under on this host, `on_auto` marking
    `auto`, which is whichever fence the host has: Landlock, bubblewrap or sandbox-exec.
    """
    no_bwrap = pytest.mark.skipif(
        not bubblewrap_available(), reason="no bwrap, or this host cannot make a namespace"
    )
    marks = {confinement.AUTO: on_auto, confinement.BUBBLEWRAP: (no_bwrap,)}
    return [pytest.param(mode, marks=marks.get(mode, ())) for mode in confinement.MODES]


def _reads_its_inputs(cfg, session_dir, sandbox: str) -> tuple[Any, str]:
    """Kingfisher's own backend under `sandbox`, and the text of an input file it holds.

    The text is not the file's name, so a failed `cat` quoting its argument back cannot
    pass for a read.
    """
    body = f"read-{sandbox}-{os.getpid()}"
    (session_dir / cfg.layout.inputs / "given.csv").write_text(body, encoding="utf-8")
    shell = confinement.ShellSettings(sandbox=sandbox)
    return backend_at(cfg, session_dir, shell=shell), body


@pytest.mark.parametrize("sandbox", _every_sandbox())
def test_a_path_spelt_from_the_session_root_survives_a_cd(cfg, session_dir, sandbox):
    """Taught to drop the slash, the agent's `inputs/x` missed once a command moved the
    working directory. Driven under each setting, since each fence hands the shell its
    environment its own way; the relative read after the same `cd` is the control, or a
    shell whose `cd` did nothing would pass.
    """
    backend, body = _reads_its_inputs(cfg, session_dir, sandbox)
    inputs = cfg.layout.inputs

    rooted = backend.execute(f'cd / && cat "$KINGFISHER_SESSION_ROOT/{inputs}/given.csv"')
    relative = backend.execute(f"cd / && cat {inputs}/given.csv")

    assert rooted.exit_code == 0, rooted.output
    assert body in rooted.output
    assert body not in relative.output
    assert relative.exit_code != 0


@pytest.mark.parametrize("sandbox", _every_sandbox(_STARTS_PYTHON_FENCED))
def test_a_script_finds_a_session_file_through_its_environment(cfg, session_dir, sandbox):
    """The prompt tells a script to read the root with `os.environ`, never a pasted path;
    a shell that did not hand the variable to what it runs would leave that a `KeyError`.
    """
    backend, body = _reads_its_inputs(cfg, session_dir, sandbox)
    script = session_dir / cfg.layout.scratchpad / "read.py"
    script.write_text(
        "import os\n"
        "os.chdir('/')\n"
        "root = os.environ['KINGFISHER_SESSION_ROOT']\n"
        f"print(open(os.path.join(root, {cfg.layout.inputs!r}, 'given.csv')).read())\n",
        encoding="utf-8",
    )

    ran = backend.execute(
        f'cd / && python "$KINGFISHER_SESSION_ROOT/{cfg.layout.scratchpad}/read.py"'
    )

    assert ran.exit_code == 0, ran.output
    assert body in ran.output


def test_the_home_directory_exists_before_a_command_runs(cfg, session_dir):
    """A `HOME` that does not exist is worse than none: tools fall back to somewhere
    unpredictable rather than failing.
    """
    from kingfisher.plugins.sessions.backend import backend_at

    backend_at(cfg, session_dir)

    assert Path(shell_env(cfg, session_dir)["HOME"]).is_dir()


def test_a_session_opened_without_a_catalogue_reads_the_definitions_once(cfg, monkeypatch):
    """`backend_at` handed `shell_env` the catalogue it was given rather than the one it
    had read, so an opening with none read the definitions a second time for the shell."""
    from kingfisher.core.definitions.catalogue import Definitions
    from kingfisher.plugins.sessions.backend import LocalSessions

    reads = []
    from_roots = Definitions.from_roots.__func__

    def counted(cls, *args, **kwargs):
        reads.append(args)
        return from_roots(cls, *args, **kwargs)

    # Counted at `from_roots`, which every reading of a definitions folder goes through,
    # and driven through `open`: a count taken at `backend_at` would miss a read moved
    # up into the opening.
    monkeypatch.setattr(Definitions, "from_roots", classmethod(counted))
    LocalSessions().open(cfg, "one")

    assert len(reads) == 1


# -- one file, listed once ------------------------------------------------
#
# Two of the readable routes point *inside* the default backend's own root:
# `/inputs` and `/memory` are real directories under the session.
# `CompositeBackend` merges every backend's answer, so each of those files was
# found twice -- once by the route and once by the default walking past it.
# `/skills` never showed it, because it points at the catalogue, somewhere else
# entirely.

def _rows(result):
    """The matches in a listing, insisting it actually succeeded."""
    assert result.matches is not None, result.error
    return result.matches


def _entries(result):
    """The same for a directory listing, which answers in a different field."""
    assert result.entries is not None, result.error
    return result.entries


def _asked(backend, name: str, *args, **kwargs):
    """One file tool.

    The three sync overrides were once written and the three async ones were not, for a
    release: `aglob` handed the model a file twice and `als` the whole root twice, and a
    suite that only ever asked the sync half could not see it. `test_async_twins.py`
    holds each async one to its sync twin now.
    """
    return getattr(backend, name)(*args, **kwargs)


#: Every readable route that lives under the session root, and so was doubled.
INSIDE_THE_ROOT = {
    "/inputs": ("inputs",),
    "/memory": ("memory",),
}


def _a_bundle_with_a_skill(cfg) -> None:
    """A bundle shipping a skill, mounted two levels under `/skills/`, and one skill in
    the catalogue beside it as the control.

    `inside.md` sits at the bundle's own root, where a pattern anchored to `/skills`
    cannot reach: without it, forwarding an anchored pattern to the route unchanged
    matches nothing there anyway and the rule below passes either way.
    """
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
    with_private_skill(cfg)
    catalogue = cfg.definitions / "skills" / "tidying"
    catalogue.mkdir(parents=True, exist_ok=True)
    (catalogue / "SKILL.md").write_text(
        "---\nname: tidying\ndescription: Tidies. Samples nothing.\n---\n", encoding="utf-8"
    )
    (cfg.definitions / "subagents" / "surveyor" / "skills" / "inside.md").write_text(
        "at the bundle's root", encoding="utf-8"
    )


def test_a_scoped_search_reaches_a_route_below_the_path(cfg, session_dir):
    """A bundle's skills are mounted under `/skills/subagents/<bundle>/`, and the
    composite routes a scoped search to the one backend owning the longest matching
    prefix -- so a search of `/skills`, which is where the prompt sends an agent looking
    for a procedure, answered with the catalogue's skills and silently without any
    bundle's. Measured: found by a search of the session, missed by one of `/skills`.

    The catalogue's skill is the control, for two reasons: a search answering nothing
    would pass the first half, and a search that reached only the bundle would pass it
    too.
    """
    _a_bundle_with_a_skill(cfg)
    backend = backend_at(cfg, session_dir)
    bundled = "/skills/subagents/surveyor/sampling/SKILL.md"

    found = _asked(backend, "glob", "**/SKILL.md", "/skills")
    said = _asked(backend, "grep", "Samples rows", "/skills")
    globbed = [one["path"] for one in found.matches]
    grepped = [one["path"] for one in said.matches]

    assert bundled in globbed, "a bundled skill is unfindable from the folder holding it"
    assert "/skills/tidying/SKILL.md" in globbed, "and the control: the catalogue's too"
    assert grepped == [bundled]


def test_a_scoped_search_survives_a_route_whose_folder_is_not_there(cfg, session_dir):
    """`/scratchpad/blobs/` and `/scratchpad/large_tool_results/` are mounted and never
    made, and asking a route below the path searched now asks them too.

    Measured, which is why nothing here skips an error: a missing root answers a search
    with no matches and no error, where `ls` calls it `path_not_found`. This is what says
    so from the agent's side, so a backend that starts failing a search of a folder it
    does not have is caught here rather than in a deployment.
    """
    (session_dir / "scratchpad" / "notes.txt").write_text("a note", encoding="utf-8")
    backend = backend_at(cfg, session_dir)

    globbed = _asked(backend, "glob", "**/*", SCRATCH_ROUTE)
    grepped = _asked(backend, "grep", "a note", SCRATCH_ROUTE)

    assert globbed.error is None and grepped.error is None
    assert [one["path"] for one in globbed.matches] == [f"{SCRATCH_ROUTE}notes.txt"]
    assert [one["path"] for one in grepped.matches] == [f"{SCRATCH_ROUTE}notes.txt"]


class _Failing:
    """A route backend whose searches fail, which no backend kingfisher ships does.

    Measured: a `FilesystemBackend` whose root was never made answers a search with no
    matches and no error -- only `ls` calls it `path_not_found` -- so a deployment's own
    route is the only thing that can fail one, and a stub is the only way to drive it.
    """

    def glob(self, pattern: str, path: str | None = None) -> Any:
        return GlobResult(error="the sandbox is gone", matches=None)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        return self.glob(pattern, path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        return GrepResult(error="the sandbox is gone", matches=None)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        return self.grep(pattern, path, glob, max_count=max_count)


def test_a_route_below_the_path_whose_search_fails_is_answered_not_swallowed(
    cfg, session_dir
):
    """Asking the routes below a path means a route the caller never named can fail the
    search, and the alternative is worse: an answer holding what the other routes
    returned and no word of the one that did not is the partial answer `_listing` stopped
    giving. deepagents' root search returns the error too.

    Mounted into the backend the real builder made, so everything else about it is real.
    """
    backend = backend_at(cfg, session_dir)
    backend.routes[f"{SCRATCH_ROUTE}theirs/"] = _Failing()
    backend.sorted_routes = sorted(
        backend.routes.items(), key=lambda one: len(one[0]), reverse=True
    )

    globbed = _asked(backend, "glob", "**/*", SCRATCH_ROUTE)
    grepped = _asked(backend, "grep", "anything", SCRATCH_ROUTE)

    assert globbed.error == "the sandbox is gone"
    assert grepped.error == "the sandbox is gone"


class _Unasked:
    """A route below the searched path that answers if asked, and counts each time."""

    asked = 0

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        _Unasked.asked += 1
        return GrepResult(matches=[{"path": "/x.txt", "line": 1, "text": pattern}])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        return self.grep(pattern, path, glob, max_count=max_count)


def test_a_routed_file_is_globbed_once(cfg, session_dir):
    """Measured before this: `--input orders.csv` reached the model as
    `['/inputs/orders.csv', '/inputs/orders.csv']`, on every pattern tried.
    """
    backend = backend_at(cfg, session_dir)
    for route, parts in INSIDE_THE_ROOT.items():
        where = session_dir.joinpath(*parts)
        where.mkdir(parents=True, exist_ok=True)
        (where / "probe.txt").write_text("needle\n", encoding="utf-8")

        found = [
            one["path"] for one in _rows(_asked(backend, "glob", f"{route}/probe.txt"))
        ]

        assert found == [f"{route}/probe.txt"], route


def test_a_routed_file_is_grepped_once(cfg, session_dir):
    """From the root, which is where the two answers meet."""
    backend = backend_at(cfg, session_dir)
    for route, parts in INSIDE_THE_ROOT.items():
        where = session_dir.joinpath(*parts)
        where.mkdir(parents=True, exist_ok=True)
        (where / "probe.txt").write_text("needle\n", encoding="utf-8")

        found = [
            one["path"] for one in _rows(_asked(backend, "grep", "needle", path="/"))
        ]

        # Taken away again before the next route: this greps from the root, so a probe
        # left behind is a second match and the answer stops being about one route.
        (where / "probe.txt").unlink()

        assert found == [f"{route}/probe.txt"]


def test_a_directory_still_lists_what_it_actually_holds(cfg, session_dir):
    """A directory lists what it holds one level at a time, and a filter keeping only
    direct children can answer nothing at all, which leaves the model no way to its files.
    """
    backend = backend_at(cfg, session_dir)
    inside = session_dir / "outputs" / "deep"
    inside.mkdir(parents=True, exist_ok=True)
    (inside / "probe.txt").write_text("needle\n", encoding="utf-8")

    assert [one["path"] for one in _entries(_asked(backend, "ls", "/outputs"))] == [
        "/outputs/deep/"
    ]
    assert [one["path"] for one in _entries(_asked(backend, "ls", "/outputs/deep"))] == [
        "/outputs/deep/probe.txt"
    ]


def test_a_file_matching_twice_still_reports_both(cfg, session_dir):
    """The half that says this is deduplication and not collapsing."""
    backend = backend_at(cfg, session_dir)
    inputs = session_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "probe.txt").write_text("needle one\nquiet\nneedle two\n", encoding="utf-8")

    matches = _rows(backend.grep("needle", path="/inputs"))

    assert [one["line"] for one in matches] == [1, 3]
    assert [one["text"] for one in matches] == ["needle one", "needle two"]


def test_two_different_files_are_both_still_listed(cfg, session_dir):
    """The other half: nothing is dropped for being similar, only for being the same
    thing twice.
    """
    backend = backend_at(cfg, session_dir)
    inputs = session_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for name in ("one.txt", "two.txt"):
        (inputs / name).write_text("needle\n", encoding="utf-8")

    found = sorted(one["path"] for one in _rows(backend.glob("/inputs/*.txt")))

    assert found == ["/inputs/one.txt", "/inputs/two.txt"]


def test_a_hard_failure_is_passed_through_rather_than_emptied(cfg, session_dir):
    """`matches` is `None` on a hard failure and `[]` on a search that found nothing,
    and the two say different things.
    """
    from dataclasses import dataclass

    from kingfisher.plugins.sessions.backend import _once

    @dataclass
    class Failed:
        error: str | None = "broke"
        matches: list | None = None
        truncated: bool = False

    assert _once(Failed(), key=lambda one: one).matches is None


def test_an_unconfined_shell_says_what_it_means_for_the_other_sessions(cfg, session_dir):
    """Most programs running kingfisher never run `doctor`; this warning is what they see.
    It named what the host was exposed to and not that each session could reach the rest.
    """
    from kingfisher.plugins import ShellSettings, backend_at

    with pytest.warns(UserWarning, match="read and change the other sessions' files"):
        backend_at(cfg, session_dir, shell=ShellSettings(sandbox="off"))
