"""Reading `groups.yaml`: the group names this deployment declares.

The `infrastructure` half of a split `kingfisher.domain.access` states: that
module owns the rule and may not read a file, this one owns the file and owns
no rule. The same seam `Models` and `kingfisher.infrastructure.model_catalogue`
sit on, for the same reason -- a domain module imports the standard library and
`kingfisher.domain`, nothing else, and a test enforces it.

`safe_load`, for the reason `model_catalogue` gives: this document is
operator-authored rather than caller-supplied, but it is read at startup and
`yaml.load` would let a crafted file construct arbitrary objects before
anything else runs.

**An absent file is no policy; an unreadable one is a refusal.** The two are
not the same and must never collapse into each other: a deployment that never
wrote a policy has none, and a deployment whose policy will not parse has one
it cannot honour. Reading the second as the first is the failure this whole
area exists to avoid -- a server that comes up wide open because a file had a
tab in it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from kingfisher.domain.access import AccessError, Groups, parse

if TYPE_CHECKING:
    from pathlib import Path


def load(path: Path) -> Groups | None:
    """The policy at `path`, or `None` if there is no file there."""
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path}: is not valid YAML -- {exc}"
        raise AccessError(msg) from exc
    except OSError as exc:
        msg = f"{path}: cannot be read -- {exc}"
        raise AccessError(msg) from exc

    if document is None:
        msg = (
            f"{path}: is empty. A policy file that exists but says nothing is "
            f"not the same as no policy -- delete it, or give it a 'groups' section"
        )
        raise AccessError(msg)
    if not isinstance(document, dict):
        msg = f"{path}: is a mapping of sections, not {type(document).__name__}"
        raise AccessError(msg)
    return parse(document, source=path.name)
