"""The directory a deployment runs out of: laying it out, filling it, adding to it.

Nine modules and one subject. Six of them are what `fs` used to be, split along
the lines its own history already kept: `layout` makes the tree `domain.layout`
describes and places the furniture that ships with it; `sessions` is one
session's directory and the ports over it; `permissions` owns the write bits on
`/data` and is the only thing allowed to change them; `placement` copies a
caller's files into `/data` or a turn's input; `snapshots` keeps the agent a
session opened with, under the one root the agent never addresses; `backing`
reads what the workspace is sitting on.

The other three are unchanged. `seeding` copies a reviewed set of definitions
in; `uploads` unpacks the definitions one request brought with it; `files`
fetches the content a request named rather than carried.

Every one of them writes into the same tree, and several have a rule about *not*
destroying what another put there.

`fs` was split rather than grown because the file had reached 714 lines while
being six jobs with almost nothing between them -- of 21 commits, most touched
one region and none touched more than four. `workspace_fs` became `fs` before
that, when the suffix stopped earning its place beside a directory saying the
same word.

Deliberately not here: `domain/layout.py`, which is the layout as *data* and
belongs to the domain, and the session store, which is about a session
outliving this machine rather than about the tree on it. The line is what
touches the workspace directory, not what knows its shape.

No re-exports. Each module is imported by name, so the subpackage is a place
rather than a second surface that could disagree with the first.
"""
