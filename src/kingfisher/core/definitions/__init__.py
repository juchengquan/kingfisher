"""The definition kinds: what a deployment authors, and what reads it.

One directory per kind, each holding the format's own vocabulary, the reader for
it, and the registry built from what was found -- which is what earns a kind a
package, as *The core and its plugins* in `docs/decisions.md` says.
`DEFINITION_KINDS` says which five exist, and `test_kinds_holds_exactly_the_kinds`
holds this directory to it in both directions, so a sixth cannot arrive by being
dropped here.

**Nothing is imported here, and that is the point rather than an omission.** This
file runs before any kind's own `__init__`, so a convenience re-export is paid for
by every import of every kind. One reaching a module that imports deepagents
puts it in front of all of them: `kingfisher.core.definitions.skills` takes a few
milliseconds and a few dozen modules, and took a second and three thousand behind such a
line -- so `kingfisher seed` would import the provider SDKs to read a directory. Each
module is imported by name instead, which is what `agents/__init__.py` already says about its own.

`importing.py` and `documents.py` are here and are not kinds. The first loads a
workspace's own Python without putting it on the import path, and the kind
catalogues that load Python read it; the second reads the YAML the agent and subagent
formats are written in. Both are named in that same test, so a third shared helper
is a deliberate edit rather than a drift -- the kinds duplicate on purpose, and a
folder with a helper in it is where that stops being true quietly.
"""
