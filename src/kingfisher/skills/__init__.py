"""Skills: registering what a workspace's `skills/` directory provides.

A module rather than a layer, and the distinction is the point. `spec` is what a
skill definition says, `catalogue` finds them on disk, `registry` decides which ones
a run will actually have, and `backend` mounts them where the agent can read them.
All four are one job -- *take what the assets provide and make it available to a run*
-- and nothing outside this directory needs to know how it is done.

**It does not share with `tools` or `subagents`, and that is deliberate.** All three
resolve a `source::name`, and all three do it their own way. Written once and shared,
a change for one kind would have to be argued past the other two; written three
times, each kind changes on its own. The duplication is the price of that, paid
knowingly -- see `docs/decisions.md`.
"""
