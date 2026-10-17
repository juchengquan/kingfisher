"""Agents: registering what a workspace's `agents/` directory provides.

`spec` is the format's vocabulary, `reading` parses a document, `catalogue` finds them
on disk. Building the graph is `core.agent.assembly`'s, which is why this package reaches
no framework. No re-exports: each module is imported by name.
"""
