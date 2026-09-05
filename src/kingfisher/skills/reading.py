"""Reading a skill document for the one thing kingfisher needs from it.

deepagents owns this format and decides what a skill means. What kingfisher has
to know is the name, because that is what a request activates and what the
directory must be called. `spec` holds the format's vocabulary -- where the
header ends, what the file is called, which exception a mistake raises -- and
opens nothing.

Apart from `spec` rather than folded into it, and the reason is a rule rather
than taste. A domain module may name a kind's `spec` and nothing else of it, on
the stated grounds that a spec is format vocabulary with no adapter behind it.
This reaches `infrastructure.documents` for the YAML step, so putting it in
`spec` would make that sentence false while leaving it written down --
see `test_domain_imports_only_the_standard_library_and_itself`.

The subagent format has the same two files for the same reason.
"""

from __future__ import annotations

from kingfisher.domain import fields
from kingfisher.infrastructure import documents
from kingfisher.skills.spec import FILENAME, SkillError, split


def name_from(text: str, source: str = FILENAME) -> str:
    """A skill's declared name, which is also its directory name.

    Three functions until this one, in two packages: an envelope opener in
    `infrastructure`, a wrapper beside it, and the field check in `spec`. The
    opener called back into `spec.split` to find the header and the wrapper
    called back into `spec` to check the name, so reading a skill's name left
    this package and returned twice. Each of the three had exactly one caller.

    A missing header and an undecodable one stay separate mistakes and say so
    separately: someone who wrote no `---` is not looking for the same line as
    someone whose frontmatter will not parse.

    The body is read and dropped, which is not waste -- `split` is what knows
    where the header ends, and there is no way to have the one without the
    other. deepagents reads the body; kingfisher never has.
    """
    parts = split(text)
    if parts is None:
        msg = f"{source}: expected YAML frontmatter delimited by ---"
        raise SkillError(msg)

    document = documents.decode(parts[0])
    if isinstance(document, str):
        msg = f"{source}: cannot read frontmatter ({document})"
        raise SkillError(msg)

    name = fields.text(document.get("name"))
    if not name:
        msg = f"{source}: frontmatter is missing required field 'name'"
        raise SkillError(msg)
    if "/" in name or name in {".", ".."}:
        # It becomes a directory name, so a path separator here would write
        # outside the directory the caller believes it is filling.
        msg = f"{source}: {name!r} is not usable as a directory name"
        raise SkillError(msg)
    return name
