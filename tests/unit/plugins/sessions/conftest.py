from __future__ import annotations

from pathlib import Path

from kingfisher.core.layout import HARNESS, RECENT_TURNS_FILE

#: A conversation as kept, for a tool or the shell to be refused rewriting or reading.
CONVERSATION = '{"role": "user", "content": "You help"}\n'


def kept_conversation(session_dir) -> Path:
    """Where the default backend keeps a session's conversation, on disk."""
    return Path(session_dir) / HARNESS / RECENT_TURNS_FILE


def keep_conversation(session_dir) -> Path:
    """`CONVERSATION`, kept for the session at `session_dir`, and the file it is in."""
    kept = kept_conversation(session_dir)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(CONVERSATION, encoding="utf-8")
    return kept
