"""The agent a session opened with, kept for the turns that come after."""

from __future__ import annotations

from pathlib import Path

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


def agent_snapshot(session_dir: Path) -> Path:
    """The path a session's agent definition is kept at."""
    return Path(session_dir) / HARNESS / PINNED_AGENT


def remember_agent(session_dir: Path, document: str) -> None:
    """Keep the agent definition this session opened with.

    Written once and never rewritten: a later turn naming the same agent is built
    from what the session started with, not from whatever the catalogue says by
    then. A deploy mid-conversation is ordinary; an agent's prompt changing under
    a history that already happened is not.
    """
    path = agent_snapshot(session_dir)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def agent_started_with(session_dir: Path) -> str | None:
    """The agent document this session opened with, or `None` if it kept none.

    `None` covers two ordinary cases: a session that named no agent, and a
    deployment whose repository cannot hand over the document it parsed.
    """
    path = agent_snapshot(session_dir)
    return path.read_text(encoding="utf-8") if path.is_file() else None
