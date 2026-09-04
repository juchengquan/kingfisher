"""The domain layer: kingfisher's own vocabulary, and the rules over it.

A skill, a subagent, a session, a set of capabilities, what a turn returns,
which sessions have expired. Nothing in here knows that deepagents exists or
that a filesystem does — a rule that needs a value is passed the value, and a
rule that genuinely needs a primitive takes a port from `domain.ports`.

Enforced rather than remembered: a domain module may import the standard
library and `kingfisher.domain`, nothing else. See
`tests/unit/test_architecture.py`.
"""
