"""Tools: registering what a workspace's `tools/` directory provides.

`spec` is what a tool is and how a reference to one is written, `catalogue` finds and
imports the modules that define them, and `harness` resolves which a run may call and
hands them to the graph. One job, and nothing outside this directory needs to know
how it is done.

**It does not share with `skills` or `subagents`.** All three resolve a
`source::name` and all three do it their own way -- `spec.split_reference` here,
`registry.split_qualified` there, differing today by one call that strips a trailing
slash. Written once and shared, that difference would have to be argued past two
other kinds; written apart, each changes on its own. The duplication is the price of
that, and it is the point rather than an oversight.
"""
