"""What a backend `Sessions.open` returns promises, as checks.

Not shipped: kingfisher holds its own backend to these, and a deployment writing its own
copies them. Plain functions raising `AssertionError` and no pytest, so a suite of any
kind can run them. `make` returns a fresh `(Config, backend)` pair, as the sessions kit's
does, because the folders a backend must serve are that deployment's layout's. The first
two are also what kingfisher refuses at startup, in `usable_backend`; the rest write
files and run commands, which a library should not do at a build.
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from deepagents.backends import CompositeBackend

from kingfisher.plugins import (
    HostPathError,
    scratch_runs_of,
    unrouted_denials,
    why_no_shell,
)

if TYPE_CHECKING:
    from collections.abc import Callable




def execution_support(make: Callable[[], Any]) -> None:
    """The one failure with no symptom: deepagents drops `execute` and tells nobody."""
    _, backend = make()
    if (why := why_no_shell(backend)) is not None:
        raise AssertionError(why)


def route_coverage(make: Callable[[], Any]) -> None:
    """Otherwise the graph will not build, with a message that never says kingfisher."""
    cfg, backend = make()
    if (why := unrouted_denials(backend, cfg.layout)) is not None:
        raise AssertionError(why)


def filesystem_consistency(make: Callable[[], Any]) -> None:
    """The promise kingfisher's shell prompt makes the model, and the one nothing else tests.

    *"A virtual path becomes a shell path by putting that variable in front of it, slash
    included, and nothing in the workspace is out of the shell's reach"* -- written in a
    table the model reads every turn. Spelt relative here, since a deployment copying
    this check has a shell that need not export the variable. A backend routing a path
    somewhere the shell cannot follow leaves the agent able to read its inputs and unable
    to run anything over them, and the only symptom is a confused model. Both
    directions, because they fail separately: a route the shell cannot see, and a shell
    whose writes the tools cannot find.

    **The content and the filename are different tokens, and that is not tidiness.**
    Written with one token for both, `cat` failing prints the path it could not find
    -- so the check passed against a backend where the shell reached nothing at all,
    on the error message quoting its own argument back.
    """
    cfg, backend = make()
    if why_no_shell(backend) is not None:
        return
    body, name = uuid4().hex, uuid4().hex
    outputs = cfg.layout.outputs_route
    for virtual, write in (
        (f"{outputs}{name}-tools.txt", lambda path: backend.write(path, body)),
        (
            f"{outputs}{name}-shell.txt",
            lambda path: backend.execute(f"printf %s {body} > {path.lstrip('/')}"),
        ),
    ):
        write(virtual)
        seen = backend.execute(f"cat {virtual.lstrip('/')}")
        if body not in (seen.output or ""):
            msg = (
                f"{virtual} does not hold what was written to it when the shell reads "
                f"it as {virtual.lstrip('/')}: got {seen.output!r} (exit "
                f"{seen.exit_code}). The agent is told the two are one filesystem"
            )
            raise AssertionError(msg)
        result = backend.read(virtual)
        if body not in ((result.file_data or {}).get("content", "")):
            msg = (
                f"{virtual} does not hold what was written to it when the file tools "
                f"read it: got {result.error or result.file_data!r}. The agent is told "
                "the two are one filesystem"
            )
            raise AssertionError(msg)


def offloads_in_the_scratchpad(make: Callable[[], Any]) -> None:
    """deepagents' own files land where `system.md` tells the agent they are.

    The history the summarizer keeps goes under the backend's `artifacts_root`, which
    defaults to `/`. `WorkspaceScopedBackend` sets it, so a subclass passes; a
    `CompositeBackend` assembled without it puts that folder at the session root, where
    the agent is never sent to search. Read the way deepagents reads it, `isinstance`
    included, because a backend that is no `CompositeBackend` is `/` whatever attribute
    it carries.
    """
    cfg, backend = make()
    root = backend.artifacts_root if isinstance(backend, CompositeBackend) else "/"
    scratch = cfg.layout.scratch_route
    if root.rstrip("/") != scratch.rstrip("/"):
        msg = (
            f"{type(backend).__name__} has deepagents write the history it summarizes "
            f"under {root!r}, and the agent is told it is under {scratch}. Pass "
            f"artifacts_root={scratch!r} to the "
            "CompositeBackend, as the one backend_at builds does"
        )
        raise AssertionError(msg)


def shell_denied(make: Callable[[], Any]) -> None:
    """The agent's shell may not write under `/.harness` or `/inputs`.

    `/.harness` is what kingfisher reads back and trusts -- the pinned agent, the
    conversation, a paused turn -- and `/inputs` is the caller's input, which the agent
    is promised it cannot change. The file tools are refused both by the turn's
    permissions; the shell bypasses those, so on any backend but kingfisher's own this
    is the only thing that says the shell is refused too.

    Driven through `execute` rather than read off the backend's configuration, and
    both ways a shell writes: a new file, and over one kingfisher put there. The
    second is the one a pinned agent is rewritten by.

    **With the mode bits lifted first**, which is what this missed. kingfisher's own
    backend kept `/inputs` by dropping the write bits, and the shell runs as the uid
    that owns those files -- so a plain write was refused, this passed, and
    `chmod u+w` then the same write replaced the caller's file. A deployment author
    was told the shell was refused a folder it could take in two commands. The `chmod`
    is allowed to fail: on a backend that has none, or where something else refuses
    it, what is asserted is still only the file afterwards.
    """
    cfg, backend = make()
    if why_no_shell(backend) is not None:
        return
    token = uuid4().hex
    written: list[str] = []
    for route, what in (
        (cfg.layout.harness_route, "what kingfisher reads back and trusts"),
        (cfg.layout.inputs_route, "the caller's input"),
    ):
        kept, fresh = f"{route}{token}-kept", f"{route}{token}-fresh"
        backend.upload_files([(kept, b"as written")])
        here = route.strip("/")
        for path in (kept, fresh):
            inside = path.lstrip("/")
            backend.execute(
                f"chmod u+w {here} {inside} 2>/dev/null; printf %s {token} > {inside}"
            )
        after_kept, after_fresh = backend.download_files([kept, fresh])
        if after_kept.content != b"as written" or after_fresh.error is None:
            written.append(f"{route.rstrip('/')}, {what}")
    if written:
        msg = (
            f"the shell wrote under {'; and under '.join(written)}. Each is only safe "
            "where the backend keeps the shell out of it"
        )
        raise AssertionError(msg)


def host_path_refusal(make: Callable[[], Any]) -> None:
    """Conditional on purpose: refusing host paths at all is kingfisher's policy, not
    an obligation a replacement inherits.

    The refusal exists because the agent's paths are virtual and a real machine sits
    underneath, so naming one means the model confused the two views. Inside a sandbox
    of its own, `/etc/passwd` is an ordinary file and refusing it would be wrong -- so
    this asks nothing of a backend that allows it. What it does ask is that a backend
    which *does* refuse raises `HostPathError`, because `GuardedCallsMiddleware` catches that
    type and nothing else: refuse with anything else and a mistake the model could
    have corrected reaches it as a failed turn.
    """
    _, backend = make()
    try:
        backend.read("/etc/passwd")
    except HostPathError:
        return
    except Exception as refused:
        msg = (
            f"reading a host path was refused with {type(refused).__name__}, which "
            "GuardedCallsMiddleware does not catch -- it catches HostPathError and turns it "
            "into a correction the model can act on. Raise "
            "kingfisher.plugins.HostPathError, or do not "
            f"refuse. Got: {refused}"
        )
        raise AssertionError(msg) from refused


def scratch_runs(make: Callable[[], Any]) -> None:
    """What an offered tool is promised of a run, where a backend offers one at all.

    Optional, so a backend with no `run_in_scratch` is asked nothing; found the way the
    middleware finds it, through `scratch_runs_of`, since a composite forwards no method
    of its own and asked alone would let kingfisher's backend skip every line below.

    The outputs a run makes come back and the rest are named; nothing of this process's
    environment reaches the command; a link left as an output is not followed, since the
    files are read where no fence stands; `timeout` ends a run that would not; and so
    does `stop`, which `arun` sets when its caller is cancelled. The link is made before
    it is judged: a shell with no `ln` would pass that line having tried nothing.
    """
    _, backend = make()
    run = scratch_runs_of(backend)
    if run is None:
        return
    body = uuid4().hex

    ran = run(
        ["/bin/sh", "-c", "cat in.txt > out.txt"],
        {"in.txt": body.encode()},
        outputs=["out.txt", "never.txt"],
    )
    if ran.error is not None or ran.files.get("out.txt") != body.encode():
        msg = (
            f"a run did not bring back the file it made: error {ran.error!r}, files "
            f"{sorted(ran.files)!r}, output {ran.output!r}"
        )
        raise AssertionError(msg)
    if "never.txt" not in ran.missing:
        msg = "an output the command never made is not named in `missing`"
        raise AssertionError(msg)

    canary = f"KINGFISHER_CONTRACT_{body}"
    os.environ[canary] = body
    try:
        seen = run(["/usr/bin/env"], {}, outputs=[]).output
    finally:
        del os.environ[canary]
    if body in seen or "KINGFISHER_SKILLS" in seen:
        msg = f"a run's command saw an environment it was not given: {seen!r}"
        raise AssertionError(msg)

    linked = run(["/bin/ln", "-s", "/etc/hosts", "out.txt"], {}, outputs=["out.txt"])
    if linked.exit_code != 0:
        msg = f"the command could not make a link to test: {linked.output!r}"
        raise AssertionError(msg)
    if "out.txt" in linked.files:
        msg = (
            "an output that is a link was followed, and what it names read in the "
            "process outside the fence; open outputs refusing links"
        )
        raise AssertionError(msg)

    for how, keywords, stop in (
        ("timeout=1", {"timeout": 1}, None),
        ("a stop set after half a second", {}, threading.Event()),
    ):
        if stop is not None:
            threading.Timer(0.5, stop.set).start()
        started = time.monotonic()
        run(["/bin/sleep", "30"], {}, outputs=[], stop=stop, **keywords)
        if time.monotonic() - started > 10:
            msg = f"a run of `sleep 30` given {how} ran on past it"
            raise AssertionError(msg)


#: Every check a backend a deployment's `open` returned must pass, in the order a
#: deployment wants to read them: the silent failure first, then the one that stops
#: the graph building, then the promises the prompt makes, then what the shell must
#: not write, then the refusal, then what a tool's run is promised.
BACKEND_CHECKS: tuple[Callable[[Callable[[], Any]], None], ...] = (
    execution_support,
    route_coverage,
    filesystem_consistency,
    offloads_in_the_scratchpad,
    shell_denied,
    host_path_refusal,
    scratch_runs,
)


