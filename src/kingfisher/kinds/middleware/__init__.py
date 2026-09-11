"""Middleware a workspace defines, as opposed to middleware a deployment wires.

The kind that could not exist until the definition roots stopped being writable
by the agent's shell: a middleware is wrapped *around* an agent, so a copy in the
directory the agent can edit was a cap the capped thing could rewrite.

A deployment still registers classes in its own program, which is the only way to
hand middleware a live object. `harness/middleware.py` builds what a definition
asked for from either source.

No re-exports, as next door: each module is imported by name. This file used to
carry five, which made `kinds.middleware` and `kinds.middleware.catalogue` import
each other -- benign, since a package importing its own submodule resolves the
same way every time, and the only cycle in the package all the same.
"""
