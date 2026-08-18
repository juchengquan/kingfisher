"""Dispatch subagents from inside the interpreter, in a loop.

The "dynamic subagents" half of deepagents' interpreter: rather than the model
emitting one `task` tool call at a time, JavaScript in the sandbox loops over a
computed list and dispatches for each item.

    uv run python spikes/dynamic_subagents.py

Two things it needs, and neither is obvious until it fails:

  * `KINGFISHER_INTERPRETER=true`. This script sets the flag itself.
  * the *async* path. `task()` inside the REPL awaits, so a sync `SqliteSaver`
    raises `does not support async methods` partway through a workflow that has
    already run. Hence `arun` and `async_checkpointer` below.

Streamed rather than drained, because the fan-out happens inside a single
`eval` call: without it the whole workflow is a silent pause and then an
answer. Exits non-zero if the fan-out did not happen, so it is usable as a
check rather than only as a demonstration.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

# The repo root, so `main` imports. A spike is run as a script, so `sys.path`
# starts at `spikes/` -- `kingfisher` resolves because the package is
# installed, and `main.py` is not part of it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import Progress

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
---
name: namer
description: Names a colour for a single word. Replies with one word.
---
You reply with exactly one colour word and nothing else.
"""


async def main() -> int:
    load_dotenv()

    from kingfisher import Kingfisher, from_env
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.domain.request import Request
    from kingfisher.domain.subagent import SUFFIX
    from kingfisher.infrastructure.harness.checkpointing import async_checkpointer

    cfg = replace(from_env(), interpreter_enabled=True)

    # A delegate to fan out to. Written beside the catalogue rather than into
    # it permanently -- this is a demonstration, not a definition anyone asked
    # to keep.
    cfg.catalogue_roots["subagents"].mkdir(parents=True, exist_ok=True)
    definition = cfg.catalogue_roots["subagents"] / f"namer{SUFFIX}"
    existed = definition.exists()
    if not existed:
        definition.write_text(NAMER, encoding="utf-8")

    try:
        async with async_checkpointer(cfg) as threads:
            service = Kingfisher(cfg, threads=threads)
            session = service.start_session()
            print(f"session   : {session}")
            print(f"catalogue : {cfg.catalogue_roots["subagents"]}")
            print(f"task      : fan out over {list(WORDS)}\n", flush=True)

            request = Request(
                TASK,
                session_id=session,
                # `eval` to run the loop, `task` so the loop may dispatch.
                # Withhold `task` and the sandbox cannot delegate at all,
                # which is the point of gating it.
                capabilities=Capabilities(tools=("eval", "task"), subagents=("namer",)),
            )

            # Streamed rather than drained: the fan-out happens inside one
            # `eval` call, so without this the whole workflow is a silent
            # pause followed by an answer. What arrives live is the code the
            # model wrote, then its prose a word at a time.
            #
            # `Progress` is the driver's, so this and `main.py` agree about
            # when a newline is owed between tagged lines and model text.
            progress = Progress(sys.stdout)
            result = None
            async for event in service.astream(request):
                result = progress.write(event) or result
            progress.close()
    finally:
        if not existed:
            definition.unlink(missing_ok=True)

    if result is None:  # pragma: no cover -- astream always ends with `finished`
        print("the run produced no result", file=sys.stderr)
        return 1

    named = [word for word in WORDS if word in result.answer.lower()]
    if len(named) != len(WORDS):
        print(f"\nonly {len(named)}/{len(WORDS)} words came back; the fan-out did not run",
              file=sys.stderr)
        return 1
    print(f"\nall {len(WORDS)} dispatched from inside the sandbox")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
