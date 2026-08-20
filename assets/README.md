# assets

Definitions this deployment runs, fetched from somewhere else. Point
`KINGFISHER_ASSETS` here once there is something in it.

Until then `.env.example` points at `../examples`, so a fresh checkout can seed
without editing anything. That is the one confusing part of this arrangement and
it is deliberate: the variable names a *role* — where definitions are copied
from — rather than this directory, and the example demonstrates the role with
the set this repository ships.

## The two directories, and why they are two

**`examples/`** is ours. One working agent, skill, subagent and tool, each
demonstrating a distinct feature of the formats. It is committed, and about four
hundred lines of tests check that every file in it parses, loads and runs. It is
a curriculum rather than a bag of assets, which is why it is not called this.

**`assets/`** — here — is theirs. A skill fetched from another project arrives
with its own `LICENSE.txt` and its own idea of what it is for. Everything under
this directory is ignored by git except this file, and that ignore rule is the
point of the directory existing at all: an MIT repository should not absorb
somebody else's terms as a side effect of `git add -A`.

## Using both

`seed` takes one directory, and merges rather than replacing, so two sources is
two commands in the order you choose:

    kingfisher seed --from ./examples
    kingfisher seed --from ./assets

Files that collide are reported; files that do not are left alone.
