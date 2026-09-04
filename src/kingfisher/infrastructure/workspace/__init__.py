"""The directory a deployment runs out of: laying it out, filling it, adding to it.

Four modules and one subject. `fs` makes the layout `domain.layout` describes;
`seeding` copies a reviewed set of definitions into it; `uploads` unpacks the
definitions one request brought with it; `files` fetches the content a request
named rather than carried. Every one of them writes into the same tree, and
three of them have a rule about *not* destroying what another put there.

`workspace_fs` became `fs` on the way in. The suffix was doing the work this
directory now does, and `workspace.workspace_fs` would have said it twice.

Deliberately not here: `domain/layout.py`, which is the layout as *data* and
belongs to the domain, and the session store, which is about a session
outliving this machine rather than about the tree on it. The line is what
touches the workspace directory, not what knows its shape.

No re-exports. Each module is imported by name, so the subpackage is a place
rather than a second surface that could disagree with the first.
"""
