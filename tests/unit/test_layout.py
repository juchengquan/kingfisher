"""The `Layout`: the per-session leaf names a deployment may rename, and the routes,
scopes and directories everything derives from them.

`Layout()` has to reproduce the module constants exactly, because the module still
spells the default everywhere a renamable layout is not threaded; a renamed `Layout`
has to move every derived path together, because the whole point is that a prompt, a
deny rule and a kernel fence never disagree about where a folder is.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher.core.agent.assembly import memory_is_denied, read_only_permissions
from kingfisher.core.agent.backend import memory_sources
from kingfisher.core.layout import MEMORY, OUTPUTS, Layout
from kingfisher.plugins.sessions.backend import backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout

#: One leaf renamed on every axis, so a test that passes by leaving a default in place
#: fails here instead. None of these is a reserved name or a collision.
MOVED = Layout(
    inputs="in", outputs="out", memory="mem", scratchpad="scratch", tool_results="results"
)


def _laid_out(cfg, layout: Layout, session_id: str = "m") -> Path:
    """One session under `layout`, made the way `backend_at` requires it."""
    return ensure_session_layout(cfg.workspace / "sessions" / session_id, layout)


# -- the default reproduces the module -----------------------------------------


def test_the_default_layout_is_the_module_constants():
    """`Layout()` and the module cannot drift: this is the pin that says so."""
    default = Layout()
    assert default.session_dirs == ("inputs", "outputs", "memory", "scratchpad")
    assert default.artifact_dirs == (OUTPUTS, MEMORY)
    assert (default.inputs_route, default.memory_route, default.outputs_route) == (
        "/inputs/",
        "/memory/",
        "/outputs/",
    )
    assert (default.scratch_route, default.tool_results_route) == (
        "/scratchpad/",
        "/scratchpad/tool_results/",
    )
    assert default.saved_results == ("scratchpad/tool_results",)
    assert default.kept_records == (
        "scratchpad/tool_results",
        "scratchpad/conversation_history",
        "scratchpad/blobs",
        "scratchpad/large_tool_results",
    )
    assert default.history == "scratchpad/conversation_history"
    assert default.offloads == "scratchpad/large_tool_results"
    # Made, unlike `blobs`: a fence binds only a folder that is already there.
    assert default.made_dirs[-2:] == (
        "scratchpad/conversation_history",
        "scratchpad/large_tool_results",
    )
    assert "scratchpad/blobs" not in default.made_dirs


# -- a renamed layout derives everything from its leaves ------------------------


def test_a_renamed_layout_spells_every_route_from_its_leaves():
    assert MOVED.inputs_route == "/in/"
    assert MOVED.memory_route == "/mem/"
    assert MOVED.outputs_route == "/out/"
    assert MOVED.scratch_route == "/scratch/"
    assert MOVED.tool_results_route == "/scratch/results/"
    assert MOVED.saved_results == ("scratch/results",)
    # Only the parent moves: the three leaves are deepagents' own spellings.
    assert MOVED.kept_records == (
        "scratch/results",
        "scratch/conversation_history",
        "scratch/blobs",
        "scratch/large_tool_results",
    )
    assert MOVED.history == "scratch/conversation_history"
    assert MOVED.offloads == "scratch/large_tool_results"
    assert MOVED.session_dirs == ("in", "out", "mem", "scratch")
    assert MOVED.artifact_dirs == ("out", "mem")


def test_a_renamed_layout_routes_and_denies_under_its_own_names():
    routed = MOVED.routed_paths()
    assert {"/in/", "/mem/", "/scratch/results/"} <= set(routed)
    # Every kept record is mounted, because a deny rule under no route is refused.
    assert set(MOVED.kept_record_routes) <= set(routed)
    # The fixed routes are still there, unmoved.
    assert {"/skills/", "/.harness/"} <= set(routed)
    # And the default spellings are gone, not left beside the renamed ones.
    assert "/inputs/" not in routed
    assert "/scratchpad/tool_results/" not in routed

    denied = MOVED.denied_scopes()
    assert {
        "/in/**",
        "/scratch/results/**",
        "/scratch/conversation_history/**",
        "/scratch/blobs/**",
        "/scratch/large_tool_results/**",
        "/skills/**",
        "/.harness/**",
    } == set(denied)
    assert "/inputs/**" not in denied
    # `.harness` is fixed, so the one read-denied scope does not move.
    assert MOVED.denied_read_scopes() == ("/.harness/**",)


# -- validation ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["sessions", "skills", "large_tool_results", "blobs"])
def test_a_reserved_name_is_refused(name):
    """Names kingfisher or deepagents already use for a session folder are refused."""
    with pytest.raises(ValueError, match="already uses"):
        Layout(inputs=name)


@pytest.mark.parametrize("name", ["a/b", ".hidden", "", "with space"])
def test_a_name_that_is_not_a_bare_folder_is_refused(name):
    with pytest.raises(ValueError, match="bare folder name"):
        Layout(memory=name)


def test_two_leaves_that_collide_are_refused():
    with pytest.raises(ValueError, match="collide"):
        Layout(inputs="same", outputs="same")


def test_a_name_with_a_trailing_newline_is_refused():
    """`$` matches before a final newline, so `data\n` passed as a bare folder name."""
    with pytest.raises(ValueError, match="bare folder name"):
        Layout(inputs="data\n")


def test_two_leaves_differing_only_in_case_collide():
    """On macOS `inputs="data"` and `outputs="DATA"` are one folder, so whatever the agent
    was handed and whatever it wrote shared it."""
    with pytest.raises(ValueError, match="collide"):
        Layout(inputs="data", outputs="DATA")
    with pytest.raises(ValueError, match="already uses"):
        Layout(inputs="Skills")


# -- the permission and memory helpers follow the layout -----------------------


def test_read_only_permissions_deny_the_renamed_scopes():
    scopes = [p.paths[0] for p in read_only_permissions(MOVED)]
    assert "/in/**" in scopes
    assert "/scratch/results/**" in scopes
    assert "/inputs/**" not in scopes


def test_the_memory_helpers_name_the_renamed_folder():
    assert memory_sources(MOVED) == ["/mem/AGENTS.md"]
    assert memory_is_denied(MOVED).paths == ["/mem/**"]


# -- the backend mounts the renamed routes -------------------------------------


# -- the correction fragments the model reads on a misstep follow the layout ---


def test_the_shell_path_rule_and_matcher_follow_a_renamed_layout():
    """`BuiltinToolsMiddleware` states the shell rule in `execute`'s description and spots a missed
    file-tool path in its output. Both name session folders, so both move with them.
    """
    from kingfisher.core.agent.middlewares.builtin_tools import _virtual, missed
    from kingfisher.core.agent.prompting import execute_line
    from kingfisher.plugins.sessions.shell import kingfishers_shell

    said = execute_line(kingfishers_shell(fenced=True), MOVED) or ""
    assert '`/in/data.csv` is `"$KINGFISHER_SESSION_ROOT/in/data.csv"`' in said
    virtual = _virtual(MOVED.session_dirs)
    assert missed("python: can't open file '/in/x.py'", virtual) == "/in/x.py"
    # The default leaf is no longer one it reads as a file-tool path.
    assert missed("cat: /inputs/x: No such file or directory", virtual) is None


def test_a_refused_tool_path_points_at_the_renamed_routes():
    from kingfisher.core.agent.session_paths import SessionPaths, refusal_text

    paths = SessionPaths(None, Path("/w/sessions"), MOVED)
    text = refusal_text(ValueError("nope"), paths)
    assert "`/in/<name>`" in text
    assert "`/out/<name>`" in text
    assert "/inputs/" not in text


def test_a_refused_write_points_at_the_renamed_folders():
    """The other pair, and not `refusal_text`'s: a write is refused *because* the path is
    kept, so `/inputs/<name>` is the one folder it may not offer.
    """
    from kingfisher.core.agent.session_paths import SessionPaths, where_to_write

    paths = SessionPaths(None, Path("/w/sessions"), MOVED)
    text = where_to_write("Error: permission denied for write on /in/x.txt", paths)

    assert "`/out/<name>`" in text
    assert "`/scratch/<name>`" in text
    assert "/outputs/" not in text
    assert "/in/<name>" not in text


def test_a_fence_refusal_note_points_at_the_renamed_folders():
    """The third of the corrections, and the one that names a folder as well as offering
    two: a renamed layout that reached the fence and not the note would have the shell
    refuse `/in/` and the note call it `/inputs/`.
    """
    from kingfisher.core.agent.middlewares.builtin_tools import kept_note

    kept, *_ = MOVED.shell_read_only
    said = kept_note(kept, MOVED)

    assert f"`/{kept}/` is not yours to write" in said
    assert "`/out/<name>`" in said
    assert "`/scratch/<name>`" in said
    assert "/outputs/" not in said


def test_a_refused_host_path_suggests_the_renamed_scratch(tmp_path):
    from kingfisher.core.agent.host_paths import HostPathError, reject_host_path

    workspace = tmp_path
    session = tmp_path / "sessions" / "s"
    with pytest.raises(HostPathError, match=r"/scratch/<name>"):
        reject_host_path(
            str(workspace / "models.yaml"),
            workspace,
            session_dir=session,
            scratch_route="/scratch/",
        )


def test_backend_at_mounts_and_lays_out_the_renamed_leaves(cfg):
    moved = replace(cfg, layout=MOVED)
    session = _laid_out(moved, MOVED)
    backend = backend_at(moved, session)

    assert {"/in/", "/mem/", "/scratch/results/"} <= set(backend.routes)
    assert "/memory/" not in backend.routes
    assert "/scratchpad/tool_results/" not in backend.routes
    # The route points where the leaf moved to, not where the default name was.
    assert Path(backend.routes["/mem/"].cwd) == (session / "mem").resolve()
    # deepagents reads its eviction root off this: the scratchpad moved, so it must too.
    assert backend.artifacts_root == "/scratch/"

    # And the directories exist on disk under the renamed leaves, not the defaults.
    assert (session / "mem" / "AGENTS.md").exists()
    for leaf in ("in", "out", "mem", "scratch"):
        assert (session / leaf).is_dir()
    for default in ("inputs", "outputs", "memory", "scratchpad"):
        assert not (session / default).exists()
