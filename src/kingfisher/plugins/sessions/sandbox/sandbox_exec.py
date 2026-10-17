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

from kingfisher.core.layout import DEFAULT_LAYOUT, HARNESS_OWNED, SANDBOX_PROFILE
from kingfisher.core.session.model import sessions_root


def profile(  # noqa: PLR0913 -- one parameter per thing the rules name, and each is
    # a different question: what to deny, what to re-allow, what to carve back out,
    # and the two paths the profile has to know about itself
    *,
    home: Path,
    workspace: Path,
    readable: tuple[Path, ...],
    writable: tuple[Path, ...],
    itself: Path,
    protected: tuple[Path, ...] = (),
    protected_files: tuple[Path, ...] = (),
    session_inputs: str = DEFAULT_LAYOUT.inputs,
    saved_results: tuple[str, ...] = DEFAULT_LAYOUT.saved_results,
) -> str:
    """A `sandbox-exec` profile denying the operator's home, minus what runs code.

    Deny-the-home rather than allow-only-the-workspace, deliberately. An allow-list
    is the stronger shape and the one to grow into, but it fails closed on every path
    a workload happens to need -- fonts, certificates, a homebrew prefix -- and this
    is on by default. Denying the one directory where a person's credentials actually
    live closes the measured hole at a fraction of the breakage risk.

    The re-allowed paths are not a convenience. A virtualenv's `python3` is
    typically a symlink onto an interpreter installed under the home -- uv puts it
    in `~/.local/share/uv/python/...` -- so denying the home without re-allowing
    `sys.base_prefix` leaves the agent unable to run Python at all. Found by doing
    it.

    Writes take the opposite shape: an allow-list, denied from `/` down. `system.md`
    already tells the agent to stay inside the workspace and to write scratch under
    `$TMPDIR` rather than a literal `/tmp`, and both were ignored inside a single
    observed run -- it wrote `/tmp/preview.pdf` and twice tried `pip install`, which
    failed only because this venv has no `pip`. Prose the model overrides is not a
    boundary; this is the same rules with the kernel behind them.

    The known cost, and it is now measured rather than guessed at: a program that
    writes to the operating system's own temp directory stops working, even when
    everything it was *told* to write is inside the workspace.

    Headless Chrome is the proven case. Unsandboxed it renders a PDF in 2.0s; under
    this profile it fails in 0.4s with `Failed to create a ProcessSingleton for your
    profile directory`. The cause is this rule and no other -- the same profile with
    writes unrestricted works, and adding socket permissions does not help. No flag
    avoids it: `--user-data-dir` inside the workspace still fails, because Chrome
    reaches `/private/var/folders/<user>` regardless.

    Left broken deliberately. Nothing here needs a browser -- not this codebase, and
    not the skills that ship as examples. Chrome appeared once, when an agent
    improvised it to look at its own HTML output, and an agent spawning a
    network-capable browser is nearer to what a boundary is for than to something
    worth widening one to keep.

    The fix, if a tool anyone actually depends on ever needs it, is one line: allow
    writes to this user's own temp folder -- the parent of `tempfile.gettempdir()`,
    not all of `/private/var/folders`. Verified to make Chrome work while `.env`,
    the home and the repository stay refused. It is not here because it should be
    added for a dependency, not for a guess.

    `itself` is the path this text will be written to, and it is required rather
    than defaulted because a caller who forgets it gets no boundary at all:
    `sandbox-exec -f` re-reads the file for every command, so a shell that can
    write it runs the next command under rules of its own. It was writable --
    `state_dir` defaults inside the workspace and `writable_roots` returns the
    whole workspace, so the rules sat in the region they declared writable, and
    two commands took the home and the definition roots back.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_sb(home)}))",
    ]
    lines += [f"(allow file-read* (subpath {_sb(p)}))" for p in readable]
    # Re-allowing a directory inside the home re-opens the destination and not
    # the way to it, and something has to say the way is walkable. Metadata on
    # the exact directories in between, never a subpath: that is `stat`, which
    # is all a walk needs, and it leaks nothing -- the home stays unlistable and
    # every file in it stays unreadable. See `traversable`.
    lines += [
        f"(allow file-read-metadata (path {_sb(p)}))" for p in traversable(home, readable)
    ]
    lines.append('(deny file-write* (subpath "/"))')
    lines += [f"(allow file-write* (subpath {_sb(p)}))" for p in writable]
    # `2>/dev/null` is in half the commands an agent writes, and stdout and
    # stderr are themselves entries here.
    lines.append('(allow file-write* (subpath "/dev"))')
    # Last, because sandbox-exec takes the last matching rule. `protected` names
    # directories that must stay read-only even though they sit inside somewhere
    # writable -- which is the default layout for the skills catalogue, since it
    # lives in the workspace unless a deployment relocates it. Written after the
    # allows rather than instead of them: the workspace has to stay writable, and
    # only this carve-out inside it does not.
    lines += [f"(deny file-write* (subpath {_sb(p)}))" for p in protected]
    # And the files a deployment writes itself, by `path` for the reason the profile's
    # own rule below gives: rename-over and unlink are `file-write*` against the name.
    # `source_ids.yaml` is who may reach what, so a shell that could edit it could grant
    # itself -- or the next caller -- anything, and it lands at the next start.
    lines += [f"(deny file-write* (path {_sb(p)}))" for p in protected_files]
    # After those, because it is the rule that keeps the rest enforceable, and
    # `path` rather than `subpath` so a relocated `TMPDIR` beside it stays
    # writable. It covers more than an overwrite: append, unlink and
    # rename-over are all `file-write*` against this name, and rename-over is
    # the one a `subpath` deny on the parent directory would have been reached
    # for to catch.
    # Before the `itself` and `_session_folder_denial` denies, so those stay the last matching
    # rules for their own paths. This denies writes to the `sessions/` directory node,
    # and each session's folder node -- the directories above a `.harness` that the
    # regex guards it by position. Without it the shell could rename an enclosing
    # folder, write the pin where the pattern no longer reaches, and rename it back;
    # measured doing exactly that before this line. It denies the node and not its
    # contents, so the session's own work stays writable.
    lines.append(_sessions_denial(workspace))
    lines += [_enclosing_denial(workspace, folder) for folder in _enclosing(saved_results)]
    lines.append(f"(deny file-write* (path {_sb(itself)}))")
    for folder in (session_inputs, DEFAULT_LAYOUT.harness, *saved_results):
        lines.append(_session_folder_denial(workspace, folder))
    return "\n".join(lines) + "\n"


def fenced_sessions(workspace: Path) -> Path:
    """Where this profile's rules say the sessions are: the real path.

    Four rules name it -- two that allow and two that deny -- and they have to name
    the same path, which is why they ask here rather than each spelling it. The
    profile matches real paths, and resolving the workspace does not resolve a
    `sessions` *under* it: an operator moving sessions onto another volume with one
    symlink put every session outside every rule. Measured, with the workspace where
    one normally lives, under the home folder the profile denies: every command in the
    session failed at `cd`, `shell-init: error retrieving current directory`, so the
    agent could not read its own files, let alone write them.

    Both halves or neither, and that is the reason for one function. Granting the real
    path without denying under it is worse than the break it fixes: measured, with the
    allows resolved and these two patterns left on the unresolved path, `inputs` was
    writable, `rm -rf inputs` took the caller's folder, and `.harness` -- the pinned
    agent, the conversation, a paused turn -- was writable too, all exit 0.

    One link deep is as far as a static profile reaches. A single session that is
    itself a link still lands outside these rules, and `doctor`'s `session paths` row
    is what says so, because a profile naming the sessions that exist when it is
    written is the thing `_session_folder_denial` explains this design avoids.
    """
    return sessions_root(workspace).resolve()


def _sessions_denial(workspace: Path) -> str:
    """Deny writes to the directories a `.harness` is guarded by the position of.

    `_session_folder_denial` matches `.harness` by where it sits under `sessions/`, so an
    enclosing directory that moves takes `.harness` to a path the pattern no longer
    reaches. The folders between the workspace and a `.harness` are `sessions/` and each
    `sessions/<id>/`; the workspace root itself cannot be renamed, its parent being
    denied already. This denies writes to those nodes -- which is what rename and unlink
    are, a `file-write*` against the name -- and `$` stops it reaching their contents, so
    a session's own folders stay writable.
    """
    root = re.escape(str(fenced_sessions(workspace)))
    return f"(deny file-write* (regex {_sb_regex(f'^{root}(/[^/]+)?$')}))"


def _enclosing(saved_results: tuple[str, ...]) -> tuple[str, ...]:
    """The folders inside a session that a saved-results folder sits in, once each."""
    found: dict[str, None] = {}
    for folder in saved_results:
        for parent in reversed(PurePosixPath(folder).parents[:-1]):
            found.setdefault(str(parent), None)
    return tuple(found)


def _enclosing_denial(workspace: Path, folder: str) -> str:
    """Deny writes to every session's `folder` itself and not to what it holds.

    `folder` is the scratchpad, which the shell may write and the saved results sit in.
    `_session_folder_denial` finds those by their position under it, so without this
    the shell renamed the scratchpad away, wrote through the new name and renamed it
    back -- measured, a row's file held `FORGED` and every command exited 0. The node
    and not its contents, as `_sessions_denial` leaves a session's own folders: `$`
    stops it reaching the files and folders the agent makes there.
    """
    root = re.escape(f"{fenced_sessions(workspace)}/")
    written = f"^{root}[^/]+/{re.escape(folder)}$"
    return f"(deny file-write* (regex {_sb_regex(written)}))"


def _session_folder_denial(workspace: Path, folder: str) -> str:
    """Refuse every session's `folder` at once: `sessions/*/<folder>`.

    A regex rather than one `subpath` per session, and that is what keeps the
    profile static. A profile naming the sessions that exist when it is written would
    have to be rewritten as sessions arrive, under a new name each time, and a turn
    could be bound by one written before its own session existed.

    The character class is `[^/]+`, so it matches one session and not a path
    walking through several: `sessions/a/.harness` is denied and
    `sessions/a/outputs/.harness` is not this rule's business.

    The session segment is required, and `_sessions_denial` above is why it can be. It
    was optional once, so that `sessions/inputs` was denied too -- a folder the listing
    would read as a session of its own. But creating or renaming anything directly under
    `sessions/` is a write against that node, which the neighbour already refuses; what
    the optional segment added was a collision with a legal session id. A session called
    `inputs` had its whole tree denied and every command in it failed with EPERM --
    measured -- while `names_a_session` admits the name, and says in as many words that
    it refuses a dot-name because `.harness` is one of these folders. One of the two
    names was guarded there and the other was not.

    Four folders ask for this. `.harness` is what kingfisher reads back and trusts, and
    `inputs` is the caller's, which `layout.py` calls never re-derivable from the
    workspace and which nothing versions. `inputs` had only its write bits dropped,
    and the shell owns those files -- measured, `chmod u+w` then a write replaced the
    caller's file, and `rm -rf inputs` took the folder. The other two are the folders a
    ledger row can name, which a later turn reads back as what a tool returned; they
    sit a folder deeper, under the scratchpad, which is what `_enclosing_denial` holds.
    """
    root = re.escape(f"{fenced_sessions(workspace)}/")
    written = f"^{root}[^/]+/{re.escape(folder)}(/|$)"
    return f"(deny file-write* (regex {_sb_regex(written)}))"


def _sb(path: Path) -> str:
    """A path as a sandbox-profile string literal."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


#: What a `"` in a path has to become inside a `#"..."` literal, because it cannot be
#: written there by any spelling. Measured against sandbox-exec: bare, it ends the
#: literal -- `Error reading string`, exit 65 -- and so does `\\"`; `\\x22`, `\\042` and
#: `\\u0022` all parse and then match nothing, as does `.{1}`, so what compiles these is
#: a small dialect and not PCRE. `.`, `[[:punct:]]` and this one match it. This one,
#: because `.` would also match a `/` and let one pattern reach across a directory.
QUOTE_IN_A_PATTERN = "[^/]"


def _sb_regex(pattern: str) -> str:
    """A regex as a sandbox-profile literal: `#"..."`.

    Raw, which is the difference from the string literal `_sb` writes: a backslash
    reaches the regex engine as a backslash. So `re.escape`'s output goes in untouched,
    and doubling it for the literal -- the obvious reading, tried first -- broke every
    ordinary path, since `pytest-of-...` holds a `-` that `re.escape` escapes.

    A `"` is legal in a macOS directory name, and left bare it did not cost the fence,
    it cost the deployment: measured, a workspace called `my "work" stuff` made
    sandbox-exec exit 65 on every command, reads included, saying `unbound variable:
    work"` -- a fragment of the operator's own directory name and nothing about a
    profile.

    What the substitution gives up is exactness in one character position: the pattern
    also matches a sibling path spelt with some other character there. It can only deny
    more, never less, and what it reaches is another workspace's `sessions/*/inputs` or
    `.harness`, which these rules would deny if they were asked about it.
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


def profile_path(workspace: Path, rules: str) -> Path:
    """Where the sandbox profile holding `rules` is written: named for them.

    By content rather than under one name, because processes configured differently
    may share a workspace -- several each serving one session -- and one name would be
    one file each rewrote with its own rules, every turn bound by whichever wrote last.
    The same rules are the same file, so turns of one process still share theirs.

    In the harness's own folder rather than anywhere configurable.
    `KINGFISHER_STATE_DIR` used to move it, along with the run logs, claims, pinned
    agents and scratch that shared that directory; those are inside their sessions now,
    and a setting that relocates one generated file is a knob with nothing behind it.
    """
    digest = hashlib.sha256(rules.encode()).hexdigest()[:16]
    stem, _, suffix = SANDBOX_PROFILE.rpartition(".")
    return Path(workspace) / HARNESS_OWNED / f"{stem}.{digest}.{suffix}"


def wrapper(profile_path: Path) -> Callable[[str], str]:
    """What runs a command under the profile at `profile_path`."""

    def wrap(command: str) -> str:
        # The inner `/bin/sh -c` is what deepagents would have run anyway; the
        # quoting keeps the agent's command one argument rather than letting its
        # operators reach the outer shell.
        return f"sandbox-exec -f {shlex.quote(str(profile_path))} /bin/sh -c {shlex.quote(command)}"

    return wrap


def write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text`, never leaving a partial file behind.

    A profile is rewritten every time a backend is built, and `sandbox-exec -f`
    reads it at the start of every command -- so a plain write, which truncates
    before it fills, gives a concurrent turn a window in which the file it is
    about to be bound by is half a profile. The content is identical between
    turns, every input to `profile` coming from one `Config`, so what this
    prevents is a torn read rather than a disagreement.

    Beside the profile rather than in a temp directory: `os.replace` is atomic only
    within a filesystem, and a workspace may be on any of them. The reason used to be
    given as `KINGFISHER_STATE_DIR` moving this file, which `profile_path` records as
    gone -- the argument survives the setting, because the temp directory is still not
    guaranteed to be the workspace's filesystem.
    """
    # Per thread as well as per process. Every thread in a process shares one pid,
    # so a pid-named scratch is one file that concurrent turns all write and then
    # race to rename -- the winner renames it and the losers find theirs gone.
    # Measured: four of eight concurrent turns died that way, on the arrangement
    # `decisions.md` recommends to a caller who wants turns to overlap.
    scratch = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}")
    scratch.write_text(text, encoding="utf-8")
    scratch.replace(path)
