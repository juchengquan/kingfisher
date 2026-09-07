"""The directory a deployment runs out of: laying it out, filling it, adding to it.

Nine modules and one subject. `layout` makes the tree `kingfisher.layout`
describes and places the furniture that ships with it; `sessions` is one session's
directory and the ports over it; `permissions` owns the write bits on `/data` and
is the only thing allowed to change them; `placement` copies a caller's files into
`/data` or a turn's input; `snapshots` keeps the agent a session opened with,
under the one root the agent never addresses; `backing` reads what the workspace
is sitting on; `seeding` copies a reviewed set of definitions in; `uploads`
unpacks the definitions one request brought with it; `files` fetches the content a
request named rather than carried.

Every one of them writes into the same tree, and several have a rule about *not*
destroying what another put there.

Deliberately not here: `kingfisher.layout`, which is the layout as *data*, and the
session store, which is about a session outliving this machine rather than about
the tree on it. The line is what touches the workspace directory, not what knows
its shape.

No re-exports. Each module is imported by name, so the subpackage is a place
rather than a second surface that could disagree with the first.
"""
