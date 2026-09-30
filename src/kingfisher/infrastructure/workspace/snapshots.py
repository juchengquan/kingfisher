"""The agent a session opened with, kept for the turns that come after."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kingfisher.kinds.documents import DefinitionText
from kingfisher.layout import HARNESS, PINNED_AGENT

#: Where a session keeps the agent it opened with: inside the session, under
#: `.harness`.
#:
#: It was `<state_dir>/agents/<id>.yaml`, on the reasoning that the state
#: directory is the one root the agent never addresses -- a run able to rewrite
#: this could change the instructions it is running under, halfway through the
#: conversation those instructions produced. The reasoning holds and the place
#: was wrong twice over. Nothing deleted the file when the session went, so a
#: workspace kept one per session that had ever existed; and the state directory
#: defaults inside the workspace, which `writable_roots` makes writable, so the
#: shell could reach it anyway.
#:
#: `.harness` is out of reach on purpose rather than by position: denied to the
#: file tools by the route `kingfisher.layout` declares, and to the shell by the
#: sandbox. And it goes when the session goes.
AGENT_SNAPSHOT = f"{HARNESS}/{PINNED_AGENT}"


def remember_agent(harness: Any, document: str) -> None:
    """Keep the agent definition this session opened with.

    Written once and never rewritten: a later turn naming the same agent is built
    from what the session started with, not from whatever the catalogue says by
    then. A deploy mid-conversation is ordinary; an agent's prompt changing under
    a history that already happened is not.
    """
    if harness.read(PINNED_AGENT) is not None:
        return
    harness.write(PINNED_AGENT, document.encode("utf-8"))


def agent_started_with(harness: Any) -> DefinitionText | None:
    """The agent document this session opened with, or `None` if it kept none.

    `None` covers two ordinary cases: a session that named no agent, and a
    deployment whose repository cannot hand over the document it parsed.
    """
    held = harness.read(PINNED_AGENT)
    return None if held is None else DefinitionText(held.decode("utf-8"), Path(AGENT_SNAPSHOT))
