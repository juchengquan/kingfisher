"""Middleware the definitions folder defines, as opposed to middleware a deployment wires.

Safe only while the definition roots are not writable by the agent's shell: a middleware
wraps the agent, so a copy it could edit would be a cap the capped thing could rewrite.
No re-exports, which would make this package and its `catalogue` import each other.
"""
