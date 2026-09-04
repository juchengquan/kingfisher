"""`kingfisher`: fill a workspace, see what is in it, and check it over.

Four verbs. It said "two verbs, and deliberately no more" until this line was
written, by which point there were five -- the sentence justifying the restraint
outlived the restraint, and nobody adding the third or fourth went back to it.
That is the failure worth naming rather than the count: a charter nothing
enforces is a claim, and this one had been false for weeks.

The rule that produced "two" was that the command exists for what the library
cannot do for itself. It was replaced -- see *The command line* in
`docs/decisions.md` -- because it measures the library's completeness rather than
the user's, and under it the one thing a person installs this to do is the one
thing the command cannot.

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
