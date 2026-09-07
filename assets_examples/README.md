# assets_examples

This repository's worked set: every kind the formats define, each file
demonstrating a distinct feature of one. It is committed, and it is held to
working. The tests parse every file here, load every tool, build an agent from
what `assistant` declares, and render the skills index each delegate is actually
handed, so a feature that stops being demonstrated fails the suite rather than
quietly rotting.

Rendering the index is the part worth naming. Granting a skill and being offered
one are different facts, a definition shows only the first, and nothing you can
read in this directory distinguishes a delegate that holds a procedure from one
handed an empty index.

What none of it covers is a model. Nothing here is called against an endpoint,
which is what keeps the suite cheap enough to run on every commit and is equally
its edge: a definition can parse, build, and still be a bad prompt.

It is a curriculum rather than a bag of assets. Read it, copy what you need, and
change the copy:

    kingfisher seed --from ./assets_examples

## Where yours go

`../assets`. This directory is ours; that one is yours -- definitions you
fetched or wrote, arriving with their own licence and their own idea of what
they are for. Everything under it is ignored by git except its README, and that
ignore rule is the point of it existing: an MIT repository should not absorb
somebody else's terms as a side effect of `git add -A`.

`KINGFISHER_ASSETS` names a *role* -- where definitions are copied from -- rather
than either directory, which is the one genuinely confusing part of this
arrangement. `.env.example` points it here so a checkout works untouched; point
it at `../assets` once there is something in there. `seed` merges rather than
replaces, so using both is two commands in whichever order you choose:

    kingfisher seed --from ./assets_examples
    kingfisher seed --from ./assets

`assets/README.md` says all of this from the other side, at more length.

## One folder here is not a definition

`middleware/`. `seed` walks exactly the definition kinds and middleware is
deliberately not among them, because a middleware name selects code the
*deployment* wrote. `middleware/call_cap.py` gives the argument in place, and
`assets/README.md` gives it again next to the seeding rule it comes from.
