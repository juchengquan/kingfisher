"""The directory a deployment runs out of: laying it out, filling it, adding to it.

Nine modules and one subject. `layout` makes the tree `kingfisher.layout` describes
and places the furniture that ships with it; `sessions` is one session's directory
and the ports over it; `permissions` owns the write bits on `/data` and is the only
thing allowed to change them; `placement` copies a caller's files into `/data` or a
turn's input; `snapshots` keeps the agent a session opened with, under the one root
the agent never addresses; `backing` reads what the workspace is sitting on;
`seeding` copies a reviewed set of definitions in; `uploads` unpacks the definitions
one request brought with it; `files` fetches the content a request named rather than
carried.
"""
