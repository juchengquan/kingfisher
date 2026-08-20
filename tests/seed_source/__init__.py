"""A tiny asset pack, for tests about seeding rather than about content.

`resources.files` needs a real package and `tests/` is an implicit namespace
one, which is why this file exists. With it, the seeding tests reach their
fixtures through the same `opened()` path a shipped pack uses, instead of
monkeypatching the function under test.

Deliberately dull, and that is the point. Pointing these tests at the shipped
presets made them fail for reasons that had nothing to do with seeding: adding
a third subagent preset broke a test about *reporting withheld capabilities*,
and a preset count broke another about grants. A test of the seeder should
break when the seeder breaks.
"""
