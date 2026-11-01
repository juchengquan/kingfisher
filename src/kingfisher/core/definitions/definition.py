"""What an agent and a subagent both are, declared once.

`test_format_parity` holds the two readers to each other. Where the kinds part, the
field's comment says so, in this one place, so the difference cannot drift.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from kingfisher.core.access import ALL, Audience, Capabilities, Selection, narrowed_for

from .prompts import Slots, told


# `kw_only` lets a subclass add a required field after these defaulted ones.
@dataclass(frozen=True, kw_only=True)
class SpecBase:
    """The fields an agent and a subagent declare identically."""

    name: str
    description: str
    #: What this definition instructs with. No default, so code cannot build a spec that no
    #: file may write; a compiled subagent, whose graph carries its prompt, passes `""`.
    #: Each kind checks blankness itself.
    system_prompt: str
    #: The `{{name}}` blanks in `system_prompt` and how each caller's grant fills them, read
    #: from its long form. Only `prompt_for` may hand the text on: unfilled, the model is
    #: sent the braces.
    slots: Slots = field(default_factory=dict, metadata={"derived": True})
    #: The two tool axes, granted apart because they are offered apart: built-ins come with
    #: deepagents, `tools` is what the definitions offer. In one list, asking for an offered
    #: tool would give up every built-in. `None` grants none, as on every grant field here: a
    #: forgotten line must fail closed, not hand over the shell and every tool added later.
    builtin_tools: Selection = None
    tools: Selection = None
    #: Where each `where::what` entry in `tools:` claimed its tool lives, by name. Apart
    #: from `tools`, which everything downstream keys on by name; only the checker reads it.
    tool_sources: Mapping[str, str] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: Skills this definition is told about; absent means none. A subagent also refuses
    #: `["*"]`: its body is its procedure, and its caller's index would crowd a narrow context.
    skills: Selection = None
    #: Definitions this one may consult, by name, from the same catalogue; absent means
    #: none. A subagent may not write `["*"]`, which would include itself and so loop.
    subagents: Selection = None
    #: Middleware this definition runs with, by name. A name selects code the deployment
    #: wrote, which is why a request may narrow it and never add to it.
    middlewares: Selection = None
    #: What each `middlewares:` entry wrote under `settings:`. Apart from the names, which
    #: are all that granting and narrowing operate on.
    middleware_settings: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: The model this definition runs, granted rather than free because it decides where
    #: the prompt goes and whose credentials pay. `None` falls back differently: an agent
    #: runs the deployment's `default:`, a subagent whatever summoned it.
    wanted: str | None = field(default=None, metadata={"derived": True})
    #: The caller's own keys, carried and never interpreted: reading any would make this a
    #: field with rules.
    metadata: Mapping[str, object] = field(default_factory=dict)
    #: Who may reach this definition: open a session on an agent, or reach a subagent
    #: wherever it is used.
    source_ids: Audience = ALL

    def prompt_for(self, held: frozenset[str] | None) -> str:
        """What this definition instructs a caller holding `held` with, its slots filled."""
        return told(self.system_prompt, self.slots, held)

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this definition holds, narrowed to what one caller reaches.

        Only the axes both kinds answer alike: a subagent leaves `endpoints`, `models` and
        `memory` unset, and an agent's override sets them.
        """
        reached = narrowed_for(self, held)
        return Capabilities(
            builtin_tools=self.builtin_tools,
            tools=reached["tools"],
            skills=reached["skills"],
            subagents=reached["subagents"],
            middlewares=self.middlewares,
        )
