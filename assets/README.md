# assets

The definitions this repository ships -- agents, middlewares, skills, subagents and
tools. `KINGFISHER_ASSETS` points here, and `seed` copies them into a deployment's
definitions folder:

    kingfisher seed --all --from ./assets

`--all` because several definitions here name middleware or carry a lock,
`general` among them, and a plain `seed` leaves those behind. The middleware
they name is in `middlewares/` and arrives in the same run.

Everything here is committed, and it is held to working. The tests parse every
file here, load every tool, build an agent from what `general` declares, and
render the skills index each subagent is actually handed, so a definition that
stops working fails the suite rather than quietly rotting.

Rendering the index is the part worth naming. Granting a skill and being offered
one are different facts, a definition shows only the first, and nothing you can
read in this directory distinguishes a subagent that holds a procedure from one
handed an empty index.

What none of it covers is a model. Nothing here is called against an endpoint,
which is what keeps the suite cheap enough to run on every commit and is equally
its edge: a definition can parse, build, and still be a bad prompt.

## Definitions from another project

Keep them outside this checkout, and seed them with a second command:

    kingfisher seed --from ~/vendor-definitions

Not in here, for two reasons. A skill fetched from another project arrives with
its own `LICENSE.txt`, and in an MIT repository it is one `git add -A` from being
committed under the wrong terms. And the suite reads every file in this
directory as one of ours, so somebody else's skill would be held to our tests and
could fail a change that never touched it. `seed` merges rather than replacing,
so the two sources are two commands in whichever order you choose, and files
that collide are reported.

## Plugins

`plugins/desk.py` is not a definition, and `seed` does not copy it: it is an
example `TurnInstructions`, telling every graph of a turn today's date and who it
is working with. No shipped definition needs it; to try it with the command:

    PYTHONPATH=. KINGFISHER_PLUGINS=assets.plugins.desk:build_plugins

## Middleware

`middlewares/` is a definition kind like the rest, and `seed` copies it. What a
definition *names* is still only a name: `summarize-by-count` selects code the
deployment wrote, and nothing a request carries can widen that.
`docs/guides/middleware.md` carries the argument, and
`middlewares/summarize_by_count.py` is the example it points at.
