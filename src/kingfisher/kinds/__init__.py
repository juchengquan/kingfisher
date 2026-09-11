"""The definition kinds: what a deployment authors, and what reads it.

One directory per kind, each holding the format's own vocabulary, the reader for
it, and the registry built from what was found -- which is what earns a kind a
package, stated in *Not a module: middleware*. `DEFINITION_KINDS` says which five
exist, and `test_kinds_holds_exactly_the_kinds` holds this directory to it in
both directions, so a sixth cannot arrive by being dropped here.

**Nothing is imported here, and that is the point rather than an omission.** This
file runs before any kind's own `__init__`, so a convenience re-export is paid for
by every import of every kind. One reaching `kingfisher.kinds.skills.backend`
puts deepagents in front of all of them: `kingfisher.kinds.skills` is 6ms and 65
modules today, and 1,033ms and 3,124 behind such a line -- so `kingfisher seed`
would import three provider SDKs to read a directory. Each module is imported by
name instead, which is what `agents/__init__.py` already says about its own.

`importing.py` is here and is not a kind. It loads a workspace's own Python
without putting it on the import path, four kind catalogues are its only readers,
and it imports nothing from kingfisher at all. It is named in that same test, so
a second shared helper is a deliberate edit rather than a drift -- the kinds
duplicate on purpose, and a folder with a helper in it is where that stops being
true quietly.
"""
