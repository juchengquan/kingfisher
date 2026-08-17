"""`kingfisher`: seed a workspace, and see what is in it.

Two verbs, and deliberately no more. A pip-installed kingfisher already has
`Kingfisher`, `run` and `stream` for running a task, and `kingfisher-server` for
serving one; what it had no way to do was fill a workspace or look at one,
because both lived behind flags in `main.py`, which is a development driver and
is not in the wheel.

`main.py` keeps its flags and its default. Bare invocation there runs the eval
smoke -- a real model call against whatever key the deployment holds -- which is
right for a driver you use daily and indefensible for something a stranger
installs. Nothing was lifted out of it; this was written beside it.

Held to the same rule as `kingfisher.presentation`: `from kingfisher import X`, and
never `from kingfisher.infrastructure.y import Z`. Not tidiness. The claim this
package exists to serve is that finding and seeding packs is something *any*
caller can do -- so if the one command that seeds had to reach inside to do it,
the claim was never true and nothing would have said so. `test_architecture`
holds the line, and the two names this needed that were not public --
`offered` and `SKILL_LAYOUT` -- were exported rather than reached for.
"""
