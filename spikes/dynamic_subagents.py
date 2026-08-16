"""Dispatch subagents from inside the interpreter, in a loop.

The "dynamic subagents" half of deepagents' interpreter: rather than the model
emitting one `task` tool call at a time, JavaScript in the sandbox loops over a
computed list and dispatches for each item.

    uv run python spikes/dynamic_subagents.py

Two things it needs, and neither is obvious until it fails:

  * `KINGFISHER_INTERPRETER=true`, plus the optional dependency
    (`uv sync --extra interpreter`). This script sets the flag itself.
  * the *async* path. `task()` inside the REPL awaits, so a sync `SqliteSaver`
    raises `does not support async methods` partway through a workflow that has
    already run. Hence `arun` and `async_checkpointer` below.

Exits non-zero if the workflow did not actually fan out, so it is usable as a
check rather than only as a demonstration.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace

from dotenv import load_dotenv

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
system_prompt: |
  You reply with exactly one colour word and nothing else.
"""


async def main() -> int:
    load_dotenv()

    from kingfisher import Kingfisher, from_env
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.domain.request import Request
    from kingfisher.infrastructure.checkpointing import async_checkpointer

    cfg = replace(from_env(), interpreter_enabled=True)

    # A delegate to fan out to. Written beside the catalogue rather than into
    # it permanently -- this is a demonstration, not a definition anyone asked
    # to keep.
    cfg.subagents_dir.mkdir(parents=True, exist_ok=True)
    definition = cfg.subagents_dir / "namer.yaml"
    existed = definition.exists()
    if not existed:
        definition.write_text(NAMER, encoding="utf-8")

    try:
        async with async_checkpointer(cfg) as threads:
            service = Kingfisher(cfg, threads=threads)
            session = service.start_session()
            print(f"session   : {session}")
            print(f"catalogue : {cfg.subagents_dir}")
            print(f"task      : fan out over {list(WORDS)}\n", flush=True)

            result = await service.arun(
                Request(
                    TASK,
                    session_id=session,
                    # `eval` to run the loop, `task` so the loop may dispatch.
                    # Withhold `task` and the sandbox cannot delegate at all,
                    # which is the point of gating it.
                    capabilities=Capabilities(
                        tools=("eval", "task"), subagents=("namer",)
                    ),
                )
            )
    finally:
        if not existed:
            definition.unlink(missing_ok=True)

    print(result.answer)

    named = [word for word in WORDS if word in result.answer.lower()]
    if len(named) != len(WORDS):
        print(f"\nonly {len(named)}/{len(WORDS)} words came back; the fan-out did not run",
              file=sys.stderr)
        return 1
    print(f"\nall {len(WORDS)} dispatched from inside the sandbox")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
