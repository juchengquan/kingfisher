"""Dispatch subagents from inside the interpreter, in a loop.

The "dynamic subagents" half of deepagents' interpreter: rather than the model
emitting one `task` tool call at a time, JavaScript in the sandbox loops over a
computed list and dispatches for each item.

    uv run python spikes/dynamic_subagents.py

Three things it needs, and none of them is obvious until it fails:

  * the interpreter. This script turns it on in the config it builds, rather
    than asking the environment for it.
  * an agent of its own, written beside the delegate. A request must name an
    agent, and an agent's `subagents:` is the ceiling a request is clamped by --
    so a delegate no agent declares cannot be dispatched from inside the sandbox
    any more than from a tool call.
  * `eval` and `task` named on the *builtin* axis. Both are deepagents' own, so
    naming them under `tools:` grants neither and withholds every workspace tool
    besides.

Streamed rather than drained, because the fan-out happens inside a single
`eval` call: without it the whole workflow is a silent pause and then an
answer. Exits non-zero if the fan-out did not happen, so it is usable as a
check rather than only as a demonstration.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

# The repository root, so the driver imports. A spike is run as a script, so `sys.path`
# starts at `spikes/` -- `kingfisher` resolves because the package is
# installed, and the driver is not part of it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kingfisher.presentation.cli.progress import Progress

WORDS = ("sea", "grass", "sun")

TASK = f"""\
This is a workflow. Use the `eval` tool to run JavaScript that loops over the
array {list(WORDS)} and, for each word, dispatches the `namer` subagent with:

    await task({{ description: `Name a colour for: ${{word}}`, subagentType: "namer" }})

Collect the results into an array of {{word, colour}} objects and return it.
Then tell me that array. Do the loop inside `eval` -- do not call the task tool
once per word yourself, since the point is the fan-out from code.
"""

NAMER = """\
name: namer
description: Names a colour for a single word. Replies with one word.
builtin_tools: []
tools: []
system_prompt: |
  You reply with exactly one colour word and nothing else.
"""

FANOUT = """\
name: fanout
description: Dispatches the namer delegate from inside the sandbox.
builtin_tools: [eval, task]
subagents: [namer]
system_prompt: |
  You run workflows in the JavaScript sandbox. When a task asks for a loop,
  write the loop and dispatch from inside it rather than calling the task tool
  once per item yourself.
"""


def main() -> int:
    load_dotenv()

    from kingfisher import Kingfisher, config_from_env, default_backend
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.domain.request import Request
    from kingfisher.kinds.documents import SUFFIX

    cfg = replace(config_from_env(), interpreter_enabled=True)

    # An agent and a delegate to fan out to. Written beside the catalogue rather
    # than into it permanently -- these are a demonstration, not definitions
    # anybody asked to keep.
    written = []
    for kind, name, text in (
        ("subagents", f"namer{SUFFIX}", NAMER),
        ("agents", f"fanout{SUFFIX}", FANOUT),
    ):
        root = cfg.catalogue_roots[kind]
        root.mkdir(parents=True, exist_ok=True)
        definition = root / name
        if not definition.exists():
            definition.write_text(text, encoding="utf-8")
            written.append(definition)

    try:
        service = Kingfisher(cfg, backend=default_backend)
        subagents = cfg.catalogue_roots["subagents"]
        print(f"catalogue : {subagents}")
        print(f"task      : fan out over {list(WORDS)}\n", flush=True)

        request = Request(
            TASK,
            agent="fanout",
            # `eval` to run the loop, `task` so the loop may dispatch. Both are
            # built-ins. Withhold `task` and the sandbox cannot delegate at all,
            # which is the point of gating it.
            capabilities=Capabilities(
                builtin_tools=("eval", "task"), subagents=("namer",)
            ),
        )

        # Streamed rather than drained: the fan-out happens inside one
        # `eval` call, so without this the whole workflow is a silent
        # pause followed by an answer. What arrives live is the code the
        # model wrote, then its prose a word at a time.
        #
        # Synchronously, and `task()` awaiting inside the REPL is not an
        # objection to that: `eval`'s sync half runs the code on the REPL's own
        # worker loop, so the await has a loop to run on whether or not the
        # caller is one. The saver a session gets answers both halves.
        #
        # `Progress` is the shipped one, so this and the driver agree on
        # when a newline is owed between tagged lines and model text.
        progress = Progress(sys.stdout)
        result = None
        for event in service.stream(request):
            result = progress.write(event) or result
        progress.close()
    finally:
        for definition in written:
            definition.unlink(missing_ok=True)

    if result is None:  # pragma: no cover -- stream always ends with `finished`
        print("the run produced no result", file=sys.stderr)
        return 1

    print(f"\nsession   : {result.session_id}", file=sys.stderr)
    named = [word for word in WORDS if word in result.answer.lower()]
    if len(named) != len(WORDS):
        print(f"\nonly {len(named)}/{len(WORDS)} words came back; the fan-out did not run",
              file=sys.stderr)
        return 1
    print(f"\nall {len(WORDS)} dispatched from inside the sandbox")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
