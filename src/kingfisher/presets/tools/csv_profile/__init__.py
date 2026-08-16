"""A tool that outgrew one file, which is the reason to write a folder.

The other two presets are single files, because that is all they needed.
Profiling a CSV needs a shared notion of what a column *is* -- how a type is
guessed, what counts as missing -- and that notion is used by both tools here
and by neither of them alone. Written flat, it would have been a `_helpers.py`
hidden behind an underscore so the loader would skip it. Written as a package,
it is an ordinary module and `columns.py` says what it is.

The rule the loader follows is Python's own: a folder holding `__init__.py` is
one unit. Its exports are declared here, once, and nothing inside it is scanned
on its own -- so `columns.py` is a helper because it is not in `TOOLS`, not
because it is spelled with a leading underscore.

Nesting reaches no name. These are `csv_profile` and `csv_columns` to a request
and to the model, exactly as if they sat in `tools/` directly. The folder is for
whoever has to find this file again.
"""

from __future__ import annotations

from .profile import csv_columns, csv_profile

#: Declared, never inferred -- the same rule a flat module follows, stated in
#: one place for the whole package rather than once per file.
TOOLS = [csv_profile, csv_columns]
