"""Finding a workspace's own tools on disk.

The third of the store trio, and the odd one out. A skill is markdown and a
subagent is markdown with frontmatter; both are *data* the agent reads. A tool
is Python, imported into this process and called in it. There is no format to
parse here — only a module to import and a decision about what to refuse.

That difference is the whole design:

- **`TOOLS` is declared, never inferred.** Scanning a module for anything
  callable would guess at intent, and a helper promoted to a tool by accident
  is worse than one that never appears. The other formats make a definition
  state its own name; this makes a module state its own exports.
- **A module that will not import is an error, not a skipped file.** Quietly
  offering fewer tools than the workspace defines is the failure
  `CapabilityError` already exists to prevent, one layer down.
- **Nothing here is routed to the agent.** `/skills` and `/data` are backend
  routes; the tools directory deliberately is not one, so no file tool can
  reach it. An agent that could still write here is one holding `execute`,
  which already runs arbitrary code on the host — so this adds a way in that
  is no wider than the one already open, and none at all for a request without
  the shell.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

#: What a module must define: the tools it contributes, as a sequence.
EXPORT = "TOOLS"


class ToolError(ValueError):
    """A workspace's tool module could not be loaded, or should not be."""


def tool_name(tool: Any) -> str:
    """What a request names this tool by.

    `BaseTool` carries `.name`; a bare callable is named by the function. Both
    are accepted because `create_deep_agent` accepts both, and a definition
    should not have to know which one deepagents prefers this month.
    """
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or repr(tool)


def _import(path: Path) -> Any:
    """Import one file as a module, without putting it on the import path."""
    # A unique module name per file: two workspaces with a `maths.py` each must
    # not collide in `sys.modules`, and neither should a workspace file and a
    # real installed package.
    module_name = f"kingfisher_workspace_tools.{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover -- defensive
        msg = f"{path.name}: cannot be imported as a module"
        raise ToolError(msg)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        # The file name matters more than the traceback here: the reader is
        # someone who just added a tool, not someone debugging kingfisher.
        msg = f"{path.name}: {type(exc).__name__}: {exc}"
        raise ToolError(msg) from exc
    return module


def load_tools(directory: Path) -> tuple[Any, ...]:
    """Every tool the directory defines, in a stable order.

    Given the directory rather than a workspace to derive one from, for the
    same reason `skill_store.names` is: a catalogue may be deployed outside any
    workspace and shared by all of them.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return ()

    tools: list[Any] = []
    claimed: dict[str, str] = {}
    # Sorted so two workspaces holding the same files build the same agent.
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue

        module = _import(path)
        exported = getattr(module, EXPORT, None)
        if exported is None:
            msg = f"{path.name}: must define {EXPORT}, the tools it contributes"
            raise ToolError(msg)
        # A list or a tuple, and nothing looser. `BaseTool` is a pydantic model
        # and pydantic models are iterable, so `TOOLS = add` would pass a duck
        # test and then quietly iterate the tool's own fields.
        if not isinstance(exported, (list, tuple)):
            msg = (
                f"{path.name}: {EXPORT} must be a list or tuple of tools, "
                f"got {type(exported).__name__} -- write {EXPORT} = [my_tool]"
            )
            raise ToolError(msg)

        for tool in exported:
            name = tool_name(tool)
            if name in claimed:
                # `tools_by_name` is a dict, so the later one would take the
                # name in silence and the earlier tool would simply never run.
                msg = f"{path.name}: tool {name!r} is already defined by {claimed[name]}"
                raise ToolError(msg)
            claimed[name] = path.name
            tools.append(tool)

    return tuple(tools)


def names(directory: Path) -> tuple[str, ...]:
    """Tool names the directory offers. A listing, for `--list` and errors."""
    return tuple(tool_name(t) for t in load_tools(directory))
