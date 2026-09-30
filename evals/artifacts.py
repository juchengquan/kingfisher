"""Reading a finished run's artifacts.

The smoke asks for `result.json` in its own task text, so this knows the name.
Nothing in the package does.
"""

from __future__ import annotations

import json

#: Where the smoke asks for its structured result, as `RunResult.artifacts` names it.
RESULT = "derived/result.json"


def load_result(content: bytes | None) -> dict | None:
    """The smoke's result, from the bytes `Kingfisher.artifact` fetched, if it parses."""
    if content is None:
        return None
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
