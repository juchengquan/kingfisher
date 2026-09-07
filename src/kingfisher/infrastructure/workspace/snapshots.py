"""The agent a session opened with, kept for the turns that come after."""

from __future__ import annotations

from pathlib import Path

#: Where a session keeps the agent it opened with, under `state_dir`.
#:
#: That root is the one place the agent itself never addresses. A run able to
#: rewrite this could change the instructions it is running under, halfway
#: through the conversation those instructions produced.
AGENT_SNAPSHOTS = "agents"


def agent_snapshot(state_dir: Path, session_id: str) -> Path:
    """The path a session's agent definition is kept at."""
    return Path(state_dir) / AGENT_SNAPSHOTS / f"{session_id}.yaml"


def remember_agent(state_dir: Path, session_id: str, document: str) -> None:
    """Keep the agent definition this session opened with."""
    path = agent_snapshot(state_dir, session_id)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def agent_started_with(state_dir: Path, session_id: str) -> str | None:
    """The agent document this session opened with, or `None` if it kept none."""
    path = agent_snapshot(state_dir, session_id)
    return path.read_text(encoding="utf-8") if path.is_file() else None
