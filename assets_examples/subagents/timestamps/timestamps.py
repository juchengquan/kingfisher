"""A delegate that normalises the dates it finds, and says which ones it could not.

**A portable subagent, and the shape is the point.** Everything else under
`subagents/` is either a YAML document this deployment wrote or a Python module
assembling a graph itself. This is the third shape: a declaration in Python, with
no `build`, carrying its own tool and its own skill.

It exists because of what it *cannot* say. A portable definition may not name a
tool, a skill, a delegate, a middleware, a model or a source id -- every one of
those is a lookup in some particular deployment's catalogue, and a definition
that travels has never seen the deployment it will be installed into. What is
left is what a definition can answer on its own: who it is, what it does, which
of deepagents' own tools it wants, and what it brought with it.

That constraint is the feature. Copy this module into an installed package,
export `SUBAGENTS` from it, and a deployment reaches it by writing one file:

    # subagents/acme.py
    from acme_agents import SUBAGENTS

Nothing else changes. Kingfisher discovers no packages and names none -- the
deployment's own file is the whole of the opt-in, which is why an installed
package cannot quietly add a delegate to anybody's catalogue.

**What it carries is its own and reaches nothing else.** `iso_timestamp` below is
not in this workspace's `tools/`, so no agent can be granted it and no request can
narrow it away; it reaches this delegate and stops. The same goes for the
`iso-8601` skill beside this file. That is what an imported subagent being
*atomic* means: you use it, or you do not, and there is no third option where you
take it apart.

**`builtin_tools` is the exception, and deliberately.** Those are deepagents'
tools rather than this definition's, so a request that withheld `execute` still
withholds it here. A package cannot grant itself a shell.

If a delegate needs this deployment's `sql_query`, it is not portable and should
be a YAML document in `subagents/` -- that format exists for exactly that, and is
reviewable by people who do not read Python.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import tool

#: Where this definition's own skills are, resolved by the definition rather than
#: by kingfisher. A package installed into site-packages knows where its files
#: are and nothing else does, so the path is the definition's to supply -- and it
#: must be absolute, since a relative one would resolve against whatever directory
#: kingfisher happened to be started in.
HERE = Path(__file__).parent

#: `03/04/2026` is 3 April in most of the world and 4 March in the United States,
#: and nothing in the string says which. Reported rather than guessed: a date
#: silently read the wrong way round is the failure this delegate exists to stop,
#: and a confident wrong answer is worse than a flagged one.
AMBIGUOUS = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")

ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@tool
def iso_timestamp(written: str) -> str:
    """Normalise one date to ISO 8601, or say why it cannot be normalised."""
    text = written.strip()
    if ISO.match(text):
        return text
    if found := AMBIGUOUS.match(text):
        first, second, year = found.groups()
        if int(first) > 12:  # noqa: PLR2004 -- the twelfth month, named where it is read
            return f"{year}-{int(second):02d}-{int(first):02d}"
        if int(second) > 12:  # noqa: PLR2004
            return f"{year}-{int(first):02d}-{int(second):02d}"
        return (
            f"ambiguous: {text!r} is either {year}-{int(second):02d}-{int(first):02d} "
            f"or {year}-{int(first):02d}-{int(second):02d}, and the string does not say"
        )
    return f"unrecognised: {text!r} is not a date this tool knows how to read"


SUBAGENTS = [
    {
        "name": "timestamps",
        "description": (
            "Finds dates in a file, normalises them to ISO 8601, and reports the ones "
            "that are genuinely ambiguous rather than guessing. Use before comparing "
            "or sorting dates that came from more than one source."
        ),
        # Reading and searching, and nothing that writes: this delegate reports on
        # what it found and changes nothing.
        "builtin_tools": ["read_file", "ls", "glob", "grep"],
        "bundle": {
            # The tools themselves, not their names. A name would be a lookup in the
            # deployment's catalogue, which this definition has never seen.
            "tools": [iso_timestamp],
            # The directory, which deepagents mounts. Skills are files it reads, so
            # this half stays a path for a carried bundle exactly as for a folder --
            # what differs is who resolved it.
            "skills": HERE / "skills",
        },
        # Keys of your own, which nothing in a run reads. A package shipping
        # delegates is the case this is most useful for: the deployment installing
        # it did not write the definition and has no other way to record where it
        # came from.
        "metadata": {"ships_with": "kingfisher examples", "portable": True},
        "system_prompt": (
            "You normalise dates.\n\n"
            "Given a file, find every date in it and report each one in ISO 8601.\n\n"
            "Follow the `iso-8601` skill. It is short and it is the whole procedure.\n\n"
            "Use `iso_timestamp` for every date rather than converting in your head. "
            "It is the only thing here that can tell an ambiguous date from a clear "
            "one, and telling them apart is the job.\n\n"
            "Report ambiguous dates as ambiguous. Do not pick the likelier reading "
            "and move on: a caller who cannot tell a converted date from a guessed "
            "one will eventually treat the second as the first.\n\n"
            "Be terse. A list of dates, what each became, and the ones that could "
            "not be settled."
        ),
    }
]
