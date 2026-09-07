"""Reading `groups.yaml`: the group names this deployment declares."""

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
