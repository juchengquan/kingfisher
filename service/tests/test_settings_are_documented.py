"""Every setting the service reads, and where a deployment reads about it."""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from kingfisher_service import config as config_module
from kingfisher_service.config import PREFIX, ServiceConfig

from tests.conftest import repository_root

#: The only page these appear on. They are deliberately not in `.env.example` --
#: that file covers the library and says so -- which leaves one place for a
#: setting to be missing from and nothing that noticed when one was.
GUIDE = Path("docs/guides/configuration.md")


def _suffixes_read() -> set[str]:
    """What `from_env` looks up, read out of the module rather than listed here.

    Asked of the module rather than spelled as a path, the way the library's own
    rule is: a path goes stale at a rename and says so on main rather than at
    review.
    """
    source = Path(config_module.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'read\("([A-Z_]+)"', source))


def test_the_scan_sees_every_setting_there_is():
    """The guard the rule below cannot give itself.

    Each name is built as `PREFIX + suffix` at lookup time, so no whole setting
    name appears in that module and this has to match half of one. A pattern that
    found none of them -- or all but one -- would leave the documentation rule
    below passing over whatever it happened to catch, which is how
    `FILE_STORE_FACTORY` could have gone missing from both the guide and its own
    check at once.

    Held against the dataclass because a field and a reading are one setting seen
    twice: either without the other is the defect.
    """
    declared = {field.name.upper() for field in fields(ServiceConfig)}

    assert _suffixes_read() == declared, (
        f"read but not a field: {sorted(_suffixes_read() - declared)}; "
        f"a field but never read: {sorted(declared - _suffixes_read())}"
    )


def test_every_setting_the_service_reads_is_in_the_guide():
    """A deployment cannot find a setting it is never shown, and grep does not help
    here: `PREFIX + suffix` means neither a reader nor a search sees one spelled
    out anywhere in this package.

    `KINGFISHER_SERVICE_FILE_STORE_FACTORY` was read and undocumented -- on the
    page that opens by calling itself the list of what exists -- and the library
    half of the same question has not drifted once, because a test holds it.
    """
    guide = (repository_root() / GUIDE).read_text(encoding="utf-8")
    shown = set(re.findall(rf"`({PREFIX}[A-Z_]+)`", guide))
    read = {f"{PREFIX}{suffix}" for suffix in _suffixes_read()}

    assert read == shown, (
        f"read by the service but not in {GUIDE}: {sorted(read - shown)}; "
        f"in {GUIDE} but read by nothing: {sorted(shown - read)}"
    )
