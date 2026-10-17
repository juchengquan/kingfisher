"""Middleware kingfisher installs itself, on every graph it builds.

None of it may join the registry `core.definitions.middlewares` holds: a name a definition can
write is one it can leave out, and an agent that omitted `ToolAllowlist` would get every tool.
No re-exports, so taking one class does not load the other modules.
"""
