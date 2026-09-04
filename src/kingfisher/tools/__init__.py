"""Tools: registering what a workspace's `tools/` directory provides.

`spec` is what a tool is and how a reference to one is written, `catalogue`
finds and imports the modules that define them, and `harness` resolves which a
run may call and hands them to the graph. One job, and nothing outside this
directory needs to know how it is done.

**It does not share with `skills` or `subagents`.** All three resolve a
`source::name` and all three do it their own way -- `spec.split_reference` here,
`registry.split_qualified` there, differing today by one call that strips a
trailing slash. Written once and shared, that difference would have to be argued
past two other kinds; written apart, each changes on its own. The duplication is
the price of that, and it is the point rather than an oversight.

What stays shared is the vocabulary of *grants*: `SEPARATOR` and `_bare` live in
`domain.capabilities`, because a grant is spelled the same way whatever it names.

One thing here is not registration and stays anyway. `Offering.permitted`
answers "of what I registered, which may this request call" -- it reads the
registry to do it, so it is a question only this module can answer.
`ceiling` was its neighbour and is not: it manipulates two `Selection`s and
never touches the registry, so it went to `domain.capabilities` where the rest
of that arithmetic lives.

No re-exports. Each module is imported by name.
"""
