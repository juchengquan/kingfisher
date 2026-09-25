"""A delegate that orders version strings, and says which ones are not versions.

**Written to deepagents' own `SubAgent`, and typed as one.** `timestamps` is the
same portable shape in kingfisher's words; this is the shape a package written for
deepagents arrives in, from somebody who has never heard of kingfisher. Nothing
below is kingfisher's. The annotation is deepagents' TypedDict
and `ty` holds the dict to it, so a key only kingfisher understands fails the type
check here before anything loads it.

It loads unchanged. A package exporting `versions_subagent` is taken by a deployment
writing one file of its own:

    # subagents/acme.py
    from acme_agents import versions_subagent

    SUBAGENTS = [versions_subagent]

**The type has no word for two things `timestamps` says.** Without `builtin_tools`
this delegate gets every built-in the request granted -- narrowed still, so a
request that withheld `execute` withholds it here, but one that granted
`write_file` hands it over, and a delegate that should only read has only its
prompt asking it to. Without `metadata` it cannot record where it came from.
Needing either is the point to stop writing to the TypedDict: add the key, drop the
annotation, and you have `timestamps`.

**And it has words kingfisher refuses.** `model`, `middleware`, `interrupt_on`,
`permissions` and `response_format` are all `SubAgent` keys, and a spec writing
any of them does not load; nor does a `dict` in `tools`, which the TypedDict
allows and which reads here as a tool named rather than carried. What is left is
what a package can mean without having seen the deployment it lands in.
"""

from __future__ import annotations

import re
from pathlib import Path

from deepagents import SubAgent

HERE = Path(__file__).parent

#: SemVer 2.0.0's grammar as semver.org publishes it, plus a leading `v`: that is
#: how tags spell versions, and a delegate asked to order tags that refused every
#: one of them would be no use. ASCII, because `\d` alone also matches digits from
#: other scripts, which the specification does not allow and `int` would read.
SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$",
    re.ASCII,
)

type Identifier = tuple[int, int, str]
type Precedence = tuple[int, int, int, int, tuple[Identifier, ...]]


def _identifier(part: str) -> Identifier:
    """One pre-release identifier, ranked the way SemVer ranks them.

    A number compares as a number and below any word, so `alpha.2` comes before
    `alpha.10` -- comparing the strings puts them the other way round, which is the
    mistake this tool exists to stop.
    """
    return (0, int(part), "") if part.isdigit() else (1, 0, part)


def _precedence(found: re.Match[str]) -> Precedence:
    major, minor, patch, pre, _build = found.groups()
    core = (int(major), int(minor), int(patch))
    if pre is None:
        # A release outranks every pre-release of itself: `1.0.0-rc.1` < `1.0.0`.
        return (*core, 1, ())
    # Tuples compare field by field and a shorter one ranks first when the rest
    # agree, which is SemVer's rule for `alpha` < `alpha.1` as it stands.
    return (*core, 0, tuple(_identifier(part) for part in pre.split(".")))


def order_versions(versions: list[str]) -> str:
    """Order version strings by Semantic Versioning 2.0.0 precedence, lowest first.

    Pass every version in one call. Versions of equal precedence -- ones differing
    only in build metadata, which SemVer ignores -- are marked as ties rather than
    ordered, and anything that is not a version is listed separately, exactly as
    written, rather than repaired into one.
    """
    read: list[tuple[Precedence, str]] = []
    unread: list[str] = []
    for written in versions:
        if found := SEMVER.match(written.strip()):
            read.append((_precedence(found), written.strip()))
        else:
            unread.append(written)
    read.sort(key=lambda pair: pair[0])

    lines = ["lowest first:"] if read else []
    for i, (rank, text) in enumerate(read):
        tied = i > 0 and read[i - 1][0] == rank
        lines.append(f"  {text}" + ("  (same precedence as the line above)" if tied else ""))
    if unread:
        lines.append("not versions:")
        lines.extend(f"  {one!r}" for one in unread)
    return "\n".join(lines) or "no versions given"


versions_subagent: SubAgent = {
    "name": "versions",
    "description": (
        "Orders version strings by Semantic Versioning precedence and reports the ones "
        "that are not versions at all. Use before picking the latest release, sorting "
        "tags, or saying one version is newer than another."
    ),
    # A plain function, as deepagents' own examples pass one: the docstring is the
    # description the model reads and the annotations are the schema.
    "tools": [order_versions],
    # Directories on this host, as strings because that is the TypedDict's type.
    # Not deepagents' `/skills/...`, which is a route inside one deployment's
    # backend and which kingfisher refuses by name -- a package cannot know the
    # routes of whoever imports it, so it says where its files are and kingfisher
    # mounts them.
    "skills": [str(HERE / "skills")],
    "system_prompt": (
        "You order software versions.\n\n"
        "Given versions -- written in the request, or found in files you are pointed "
        "at -- report them in precedence order.\n\n"
        "Follow the `semver` skill. It is short and it is the whole procedure.\n\n"
        "Use `order_versions` for every set, including two that look obvious. "
        "`1.10.0` comes after `1.9.0`, `1.0.0-rc.1` before `1.0.0`, and `alpha.10` "
        "after `alpha.2`, and each of those reads the wrong way round at a glance.\n\n"
        "Report what is not a version as not a version. Do not pad `1.2` into "
        "`1.2.0` or drop a suffix to make a string parse: a caller who cannot tell a "
        "version you read from one you repaired will act on the repair.\n\n"
        "Change nothing. You may have been handed tools that write; this job never "
        "needs them.\n\n"
        "Be terse. The order, the ties, and what could not be read."
    ),
}

SUBAGENTS = [versions_subagent]
