"""The definition kinds, one directory each, as `DEFINITION_KINDS` names them.

Nothing is imported here: this file runs before every kind's own `__init__`, so a
re-export reaching deepagents would load it for every import of every kind. The loose
modules are shared helpers, not kinds, and `test_kinds_holds_exactly_the_kinds` names
them, because the kinds duplicate on purpose and a new helper must be a deliberate edit.
"""
