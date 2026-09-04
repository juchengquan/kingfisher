# assets

Definitions this deployment runs, fetched from somewhere else. Point
`KINGFISHER_ASSETS` here once there is something in it.

Until then `.env.example` points at `../assets_examples`, so a fresh checkout
can seed without editing anything. That is the one confusing part of this
arrangement and it is deliberate: the variable names a *role* — where
definitions are copied from — rather than this directory, and the example
demonstrates the role with the set this repository ships.

## The two directories, and why they are two

**`assets_examples/`** is ours. One working agent, skill, subagent and tool,
each demonstrating a distinct feature of the formats. It is committed, and about
four hundred lines of tests check that every file in it parses, loads and runs.
It is a curriculum rather than a bag of assets — it is named for the thing it is
an example *of*, so that it sorts beside this directory and finding either finds
both. That was the whole argument for the name: this pair is the one part of the
arrangement a reader has to meet as a pair.

One folder under `assets_examples/` is not a definition and is not copied by
`seed`: `middleware/`. `DEFINITION_KINDS` is the fields of `Definitions` —
agents, skills, subagents, tools — and `seed` walks exactly those. Middleware
is deliberately not among them: a middleware name selects code the
*deployment* wrote, and one read out of the workspace would be code the agent
can edit, wrapped around the agent that edited it. So the class is imported by
whatever constructs `Kingfisher` and the name is all a definition ever says. See
`assets_examples/middleware/call_cap.py`.

**`assets/`** — here — is theirs. A skill fetched from another project arrives
with its own `LICENSE.txt` and its own idea of what it is for. Everything under
this directory is ignored by git except this file, and that ignore rule is the
point of the directory existing at all: an MIT repository should not absorb
somebody else's terms as a side effect of `git add -A`.

## Using both

`seed` takes one directory, and merges rather than replacing, so two sources is
two commands in the order you choose:

    kingfisher seed --from ./assets_examples
    kingfisher seed --from ./assets

Files that collide are reported; files that do not are left alone.
