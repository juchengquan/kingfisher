"""The macOS fence: the profile a session's shell runs under, written whole and named
by its rules."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import threading
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from kingfisher.core.layout import Layout

#: The sandbox profile's name, which `profile_path` puts a digest of its rules inside:
#: `shell.<digest>.sb`.
SANDBOX_PROFILE = "shell.sb"


def profile(  # noqa: PLR0913 -- one parameter per thing the rules name
    *,
    home: Path,
    sessions_root: Path,
    readable: tuple[Path, ...],
    writable: tuple[Path, ...],
    itself: Path,
    protected: tuple[Path, ...] = (),
    protected_files: tuple[Path, ...] = (),
    layout: Layout,
) -> str:
    """A `sandbox-exec` profile denying the operator's home, minus what runs code.

    Reads deny the home rather than allow only the workspace: an allow-list fails closed
    on every path a workload needs (fonts, certificates, a homebrew prefix), and this is
    on by default. `readable` is not a convenience: a venv's `python3` often links to an
    interpreter under the home, as uv's do.

    Writes are an allow-list, denied from `/` down, because the model ignores prose
    telling it to stay in the workspace. The cost: a program writing to the OS temp
    directory fails. Headless Chrome is the known case, left broken deliberately since
    nothing shipped needs a browser. If something depended on does, allow writes to the
    parent of `tempfile.gettempdir()`, not all of `/private/var/folders`.

    `itself` is required, not defaulted: `sandbox-exec -f` re-reads the file for every
    command, so a shell that can write it runs the next command under its own rules.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_sb(home)}))",
    ]
    lines += [f"(allow file-read* (subpath {_sb(p)}))" for p in readable]
    # Re-allowing a directory inside the home opens the destination, not the way to it.
    # Metadata on the exact directories between, never a subpath: `stat` is all a walk
    # needs, and the home stays unlistable.
    lines += [
        f"(allow file-read-metadata (path {_sb(p)}))" for p in traversable(home, readable)
    ]
    lines.append('(deny file-write* (subpath "/"))')
    lines += [f"(allow file-write* (subpath {_sb(p)}))" for p in writable]
    # `2>/dev/null` is in half the commands an agent writes, and stdout and
    # stderr are themselves entries here.
    lines.append('(allow file-write* (subpath "/dev"))')
    # After the allows, because sandbox-exec takes the last matching rule: read-only
    # carve-outs inside a writable workspace, such as the skills catalogue.
    lines += [f"(deny file-write* (subpath {_sb(p)}))" for p in protected]
    # By `path`: rename-over and unlink are `file-write*` against the name.
    # `source_ids.yaml` is who may reach what, so a shell editing it could grant itself
    # or the next caller anything at the next start.
    lines += [f"(deny file-write* (path {_sb(p)}))" for p in protected_files]
    # Before the `itself` and folder denies, so those stay the last match for their
    # paths. Without the node denies the shell could rename a folder above `.harness`,
    # write where the pattern no longer reaches, and rename it back.
    lines.append(_sessions_denial(sessions_root))
    lines += [
        _enclosing_denial(sessions_root, folder) for folder in _enclosing(layout.saved_results)
    ]
    # The rule that keeps the rest enforceable: append, unlink and rename-over are all
    # `file-write*` against this name.
    lines.append(f"(deny file-write* (path {_sb(itself)}))")
    for folder in layout.shell_read_only:
        lines.append(_session_folder_denial(sessions_root, folder))
    return "\n".join(lines) + "\n"


def fenced_sessions(sessions_root: Path) -> Path:
    """Where this profile's rules say the sessions are: the real path.

    The profile matches real paths, and resolving the workspace does not resolve a
    `sessions` symlinked under it. Every rule naming it asks here, because an allow
    resolved without its deny leaves `inputs` and `.harness` writable. One link deep is
    as far as a static profile reaches: `doctor`'s `session paths` row reports a session
    that is itself a link.
    """
    return Path(sessions_root).resolve()


def _sessions_denial(sessions_root: Path) -> str:
    """Deny writes to the `sessions/` and `sessions/<id>/` nodes, not their contents.

    `.harness` is guarded by its position under them, so renaming one would carry it out
    of the pattern's reach. Rename and unlink are writes against the node; `$` keeps a
    session's own folders writable.
    """
    root = re.escape(str(fenced_sessions(sessions_root)))
    return f"(deny file-write* (regex {_sb_regex(f'^{root}(/[^/]+)?$')}))"


def _enclosing(saved_results: tuple[str, ...]) -> tuple[str, ...]:
    """The folders inside a session that a saved-results folder sits in, once each."""
    found: dict[str, None] = {}
    for folder in saved_results:
        for parent in reversed(PurePosixPath(folder).parents[:-1]):
            found.setdefault(str(parent), None)
    return tuple(found)


def _enclosing_denial(sessions_root: Path, folder: str) -> str:
    """Deny writes to every session's `folder` node, not to what it holds.

    `folder` encloses the saved results, which are guarded by their position under it,
    so renaming it would let the shell write through the new name and rename it back.
    """
    root = re.escape(f"{fenced_sessions(sessions_root)}/")
    written = f"^{root}[^/]+/{re.escape(folder)}$"
    return f"(deny file-write* (regex {_sb_regex(written)}))"


def _session_folder_denial(sessions_root: Path, folder: str) -> str:
    """Refuse every session's `folder` at once: `sessions/*/<folder>`.

    A regex rather than a `subpath` per session keeps the profile static: one naming
    the sessions that exist could bind a turn by rules written before its session.
    `[^/]+` matches one session, not a path through several. The session segment is
    required: optional, it denied a session named `inputs` whole, and `_sessions_denial`
    already refuses anything made directly under `sessions/`.

    `.harness` is what kingfisher reads back and trusts. `inputs` is the caller's and
    nothing versions it; dropping its write bits is not enough, since the shell owns
    them. The saved results are what a later turn reads back as a tool's answer.
    """
    root = re.escape(f"{fenced_sessions(sessions_root)}/")
    written = f"^{root}[^/]+/{re.escape(folder)}(/|$)"
    return f"(deny file-write* (regex {_sb_regex(written)}))"


def _sb(path: Path) -> str:
    """A path as a sandbox-profile string literal."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


#: What a `"` in a path becomes inside a `#"..."` literal, where no escape spells it:
#: sandbox-exec's regex is a small dialect, not PCRE. Not `.`, which would also match a
#: `/` and let one pattern reach across a directory.
QUOTE_IN_A_PATTERN = "[^/]"


def _sb_regex(pattern: str) -> str:
    """A regex as a sandbox-profile literal: `#"..."`.

    Raw, unlike `_sb`'s literal: a backslash reaches the regex engine as one, so
    `re.escape`'s output goes in undoubled. A bare `"` would end the literal and fail
    every command; its substitute also matches a sibling path, so it can only deny more.
    """
    return '#"' + pattern.replace('"', QUOTE_IN_A_PATTERN) + '"'


def traversable(home: Path, readable: tuple[Path, ...]) -> tuple[Path, ...]:
    """The directories between the denied home and each root re-allowed inside it."""
    home = Path(home)
    found: dict[Path, None] = {}
    for root in readable:
        if root == home or not root.is_relative_to(home):
            continue
        for parent in root.parents:
            found.setdefault(parent, None)
            if parent == home:
                break
    return tuple(found)


def profile_path(kingfisher_dir: Path, rules: str) -> Path:
    """Where the sandbox profile holding `rules` is written: named for them.

    Processes configured differently may share a workspace, and under one name every
    turn would be bound by whichever wrote last.
    """
    digest = hashlib.sha256(rules.encode()).hexdigest()[:16]
    stem, _, suffix = SANDBOX_PROFILE.rpartition(".")
    return Path(kingfisher_dir) / f"{stem}.{digest}.{suffix}"


def wrapper(profile_path: Path) -> Callable[[str], str]:
    """What runs a command under the profile at `profile_path`."""

    def wrap(command: str) -> str:
        # The quoting keeps the agent's command one argument, so its operators cannot
        # reach the outer shell.
        return f"sandbox-exec -f {shlex.quote(str(profile_path))} /bin/sh -c {shlex.quote(command)}"

    return wrap


def write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text`, never leaving a partial file behind.

    `sandbox-exec -f` reads the profile at every command while backends rewrite it, so a
    truncating write would hand a concurrent turn half a profile. Beside it rather than
    in a temp directory: `os.replace` is atomic only within one filesystem.
    """
    # Per thread as well as per process: threads share a pid, and turns racing to
    # rename one pid-named scratch find theirs gone.
    scratch = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}")
    scratch.write_text(text, encoding="utf-8")
    scratch.replace(path)
