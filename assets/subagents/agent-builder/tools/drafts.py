"""Draft an agent file for the user a turn runs for, from what they can use, and save
it only once it passes the check startup would make of it.

Everything here asks `runtime.context.reachable`, which answers for this caller under
this turn's grant, so a draft can name nothing they could not use themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from langchain.tools import ToolRuntime

from kingfisher import ToolContext

if TYPE_CHECKING:
    from kingfisher import Reachable

KB = 1024

#: Where a saved draft goes: the folder the caller is handed back when the turn ends.
OUTPUTS = "/outputs"


class _Block(str):
    """Text the agent format takes only as a literal block."""

    __slots__ = ()


class _Dumper(yaml.SafeDumper):
    """`yaml.SafeDumper`, writing a `_Block` as `|`."""


_Dumper.add_representer(
    _Block,
    lambda dumper, text: dumper.represent_scalar("tag:yaml.org,2002:str", text, style="|"),
)


def _yaml(document: object) -> str:
    # `allow_unicode`: without it, PyYAML quotes a prompt holding one `é` or `—`, and the
    # format refuses a quoted prompt.
    return yaml.dump(document, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=KB)


def _reach(runtime: ToolRuntime[ToolContext]) -> Reachable:
    reachable = runtime.context.reachable
    if reachable is None:
        msg = "no caller to draft for: this tool runs only in a turn kingfisher built"
        raise RuntimeError(msg)
    return reachable


def _size(count: int) -> str:
    """A byte count as a person reads one."""
    if count < KB:
        return f"{count} B"
    return f"{count / KB:.1f} KB"


def usable_definitions(runtime: ToolRuntime[ToolContext]) -> str:
    """List what an agent drafted for this user may name, kind by kind, each with what
    it says about itself. `agents` are names already taken. A name not listed here is
    refused when the draft is saved."""
    listing = _reach(runtime).listing()
    return _yaml(
        {
            "agents": dict(listing.agents),
            "builtin_tools": list(listing.builtin_tools),
            "tools": dict(listing.tools),
            "skills": dict(listing.skills),
            "subagents": dict(listing.subagents),
            "middlewares": dict(listing.middlewares),
            "models": list(listing.models),
            "default_model": listing.default_model,
        }
    )


def copy_agent(name: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Show an agent this user can use as an agent file, to start a draft from: one of
    the `agents` usable_definitions lists, never a subagent. Every list holds only what
    this user can use, and the prompt is the one they are sent. Its name is taken:
    rename it before saving."""
    copy = _reach(runtime).agent(name)
    if copy is None:
        msg = (
            f"no agent called {name!r} that this user can use; usable_definitions lists "
            "them under `agents`, and a subagent cannot be copied"
        )
        raise LookupError(msg)
    # PyYAML quotes text holding a tab or a line ending in a space, and the format refuses
    # a quoted prompt: both cleared, which a model reads the same.
    prompt = str(copy["system_prompt"]).expandtabs(4)
    copy["system_prompt"] = _Block("\n".join(line.rstrip() for line in prompt.split("\n")))
    return _yaml(copy)


def _problems(draft: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Every problem the check finds in `draft`, a line each, or nothing."""
    return "\n".join(f"- {one}" for one in _reach(runtime).check_agent(draft))


def _saved_as(draft: str) -> str:
    return f"{OUTPUTS}/{str(yaml.safe_load(draft)['name']).strip()}.yaml"


def check_agent_draft(draft: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Check an agent file you drafted as save_agent_draft would, and save nothing.
    `draft` is the whole file. Check as often as you need: this is the way to find out
    what is accepted, since every save lands in the user's outputs."""
    if problems := _problems(draft, runtime):
        return f"would not be saved:\n{problems}"
    return f"passes: save_agent_draft would save it to {_saved_as(draft)}"


def save_agent_draft(draft: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Check an agent file you drafted and save it to `/outputs/<its name>.yaml`, where
    the user is handed it. `draft` is the whole file. Saved only when it passes;
    otherwise nothing is saved and every problem is listed, to fix and save again.
    Saving a name again replaces that draft."""
    if problems := _problems(draft, runtime):
        msg = f"not saved:\n{problems}"
        raise ValueError(msg)
    save_as = _saved_as(draft)
    name = save_as.removeprefix(f"{OUTPUTS}/")
    data = draft.encode("utf-8")

    (sent,) = runtime.context.backend.upload_files([(save_as, data)])
    if sent.error:
        msg = f"not saved to {save_as}: {sent.error}"
        raise OSError(msg)
    return f"Saved {name} (YAML, {_size(len(data))}) to {save_as}"


TOOLS = [usable_definitions, copy_agent, check_agent_draft, save_agent_draft]
