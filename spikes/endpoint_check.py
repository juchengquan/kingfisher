"""Spike: validate the riskiest runtime assumptions against a real endpoint.

Fake-model tests cannot catch any of these — they are facts about the gateway
and the host, not about wiring:

  1. The gateway speaks the API style we think it does, and supports tool calling.
  2. A pre-built model instance bypasses deepagents' OpenAI provider profile,
     which would otherwise force `use_responses_api=True` and hit /v1/responses.
  3. An explicit shell env allowlist is sufficient for the toolchain to resolve
     (LocalShellBackend defaults to an EMPTY env -- not even PATH).
  4. usage_metadata is populated, including the cache fields we plan to log.

Run:  uv run python spikes/endpoint_check.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage

load_dotenv()

MODEL_ID = os.environ.get("KINGFISHER_MODEL", "MiniMax-M3")
TIMEOUT_S = 120
MAX_TOKENS = 4096

# Deliberately forces one `execute` call and one `write_file` call, so a single
# run exercises the shell env allowlist and the virtual->real path mapping.
TASK = (
    "Do exactly two things, then stop.\n"
    "1. Use the shell to run: python3 -c \"print(6*7)\"\n"
    "2. Write the single number it printed into the file /answer.txt\n"
    "Then reply with just that number."
)

SYSTEM_PROMPT = (
    "You are a spike harness. Do exactly what is asked, using tools rather than "
    "reasoning from memory. File paths are virtual and rooted at the workspace."
)


def shell_env(workspace: Path) -> dict[str, str]:
    """The Q10 allowlist: enough to resolve the toolchain, no credentials.

    PATH includes the active interpreter's bin dir so `python3` resolves under
    `uv run` without inheriting the parent environment.
    """
    return {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(workspace),  # not the real home -- no ~/.aws, no ~/.ssh
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    }


def build_model(style: str):
    """Always return a pre-built instance -- never a `provider:model` string."""
    if style == "anthropic":
        from langchain_anthropic import ChatAnthropic

        base_url, key = os.environ["ANTHROPIC_BASE_URL"], os.environ["ANTHROPIC_API_KEY"]
        return ChatAnthropic(
            model=MODEL_ID,
            base_url=base_url,
            api_key=key,
            max_tokens=MAX_TOKENS,
            timeout=TIMEOUT_S,
        )

    from langchain_openai import ChatOpenAI

    base_url, key = os.environ["OPENAI_BASE_URL"], os.environ["OPENAI_API_KEY"]
    return ChatOpenAI(
        model=MODEL_ID,
        base_url=base_url,
        api_key=key,
        max_tokens=MAX_TOKENS,
        timeout=TIMEOUT_S,
        use_responses_api=False,  # THE trap: provider profile would force True
    )


@dataclass
class Result:
    style: str
    ok: bool = False
    error: str = ""
    tools_called: list[str] = field(default_factory=list)
    answer: str = ""
    file_written: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    usage_present: bool = False


def collect_usage(messages, r: Result) -> None:
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for tc in m.tool_calls or []:
            r.tools_called.append(tc["name"])
        um = getattr(m, "usage_metadata", None)
        if not um:
            continue
        r.usage_present = True
        r.tokens_in += um.get("input_tokens", 0) or 0
        r.tokens_out += um.get("output_tokens", 0) or 0
        details = um.get("input_token_details") or {}
        r.cache_read += details.get("cache_read", 0) or 0
        r.cache_creation += details.get("cache_creation", 0) or 0


def run_style(style: str, workspace: Path) -> Result:
    r = Result(style=style)
    try:
        backend = LocalShellBackend(
            root_dir=str(workspace),
            env=shell_env(workspace),  # explicit allowlist; default would be {}
        )
        agent = create_deep_agent(
            model=build_model(style),
            backend=backend,
            system_prompt=SYSTEM_PROMPT,
            middleware=[TodoListMiddleware()],
        )
        out = agent.invoke(
            {"messages": [{"role": "user", "content": TASK}]},
            config={"recursion_limit": 25},
        )
        messages = out["messages"]
        collect_usage(messages, r)
        r.answer = (messages[-1].text or "").strip()
        r.file_written = (workspace / "answer.txt").exists()
        r.ok = True
    except Exception as exc:  # noqa: BLE001 -- a spike reports, it does not raise
        r.error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(limit=3)
    return r


def main() -> int:
    styles = tuple(os.environ.get("KINGFISHER_STYLES", "anthropic,openai").split(","))
    print(f"model={MODEL_ID} styles={styles}\n")
    results: list[Result] = []
    for style in styles:
        workspace = Path(tempfile.mkdtemp(prefix=f"kf-spike-{style}-"))
        (workspace / "data").mkdir()
        print(f"--- {style}  workspace={workspace}")
        results.append(run_style(style, workspace))
        print()

    print("=" * 68)
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        print(f"[{status}] {r.style}")
        if not r.ok:
            print(f"       error       : {r.error}")
            continue
        print(f"       tools called: {r.tools_called or 'NONE -- tool calling may be unsupported'}")
        print(f"       answer ({len(r.answer)} chars):")
        print(f"         {r.answer[:600]!r}")
        print(f"       file on disk: {r.file_written}")
        print(f"       usage       : in={r.tokens_in} out={r.tokens_out} "
              f"cache_read={r.cache_read} cache_creation={r.cache_creation}")
        if not r.usage_present:
            print("       WARNING: no usage_metadata -- Q18 per-step cost logging is blind here")
    return 0 if any(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
