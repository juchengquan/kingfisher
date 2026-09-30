"""Middleware kingfisher installs itself, on every graph it builds.

The other kind is what a definition names: `kinds.middlewares` holds it and
`infrastructure.harness.declared_middleware` builds it. Nothing here is in that
registry, and nothing here may join it. A name a definition can write is a name it
can leave out, and these are what hold a request to its capabilities -- an agent
that omitted `ToolAllowlist` would be offered every tool the deployment has.

No re-exports: each module is imported by name, so taking one class does not
load the other two modules.
"""
