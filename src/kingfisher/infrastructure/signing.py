"""Telling what kingfisher wrote into a session from what something else did.

A MAC over the file's bytes, bound to the session and the file it was written as,
so a signature copied from another session's pin -- or from this session's
transcript onto its pin -- does not verify.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from kingfisher.config import SessionKey
from kingfisher.domain.session import SessionTamperedError

#: What a signature is written beside, as a suffix of the file it signs.
SIGNATURE = ".sig"

#: The format of what `sign` returns, recorded in it so a later format -- a
#: different MAC, or several keys accepted during a rotation -- is a new number
#: rather than a guess.
_VERSION = 1


def key_id(key: SessionKey) -> str:
    """Which key signed something, without saying anything about the key.

    Recorded so rotation can be added later without rewriting what is on disk: a
    verifier holding several keys picks the one this names.
    """
    return hashlib.sha256(b"kingfisher-session-key-id\0" + key.secret).hexdigest()[:16]


def _mac(key: SessionKey, session_id: str, name: str, content: bytes) -> str:
    bound = b"\0".join(
        (b"kingfisher-session-v1", session_id.encode("utf-8"), name.encode("utf-8"), content)
    )
    return hmac.new(key.secret, bound, hashlib.sha256).hexdigest()


def sign(key: SessionKey, session_id: str, name: str, content: bytes) -> bytes:
    """The signature to write beside `name`."""
    document = {"v": _VERSION, "key": key_id(key), "mac": _mac(key, session_id, name, content)}
    return json.dumps(document, sort_keys=True).encode("utf-8")


def verify(
    key: SessionKey, session_id: str, name: str, content: bytes, signature: bytes | None
) -> None:
    """Refuse `content` unless `signature` is one `sign` made for it, here.

    A missing signature is refused too. Unsigned cannot be told apart from a
    signature somebody deleted, so accepting it would reopen exactly what signing
    closes.
    """
    if signature is None:
        msg = f"{name} in session {session_id} is not signed"
        raise SessionTamperedError(msg)
    try:
        document = json.loads(signature)
        mac = document["mac"] if document.get("v") == _VERSION else ""
    except (ValueError, TypeError, KeyError):
        mac = ""
    if not isinstance(mac, str) or not hmac.compare_digest(
        mac, _mac(key, session_id, name, content)
    ):
        msg = f"{name} in session {session_id} is not what kingfisher wrote there"
        raise SessionTamperedError(msg)
