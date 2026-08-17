"""The assets kingfisher ships, as a distribution of their own.

One working definition of each thing a request can activate — a skill, a
subagent, a tool. They lived inside the framework's wheel until it became clear
what they are: content a workspace rewrites on first contact with a real task,
which is not the same kind of thing as the code that loads it.

Nothing here is imported. The files are copied into a workspace by
`kingfisher seed`, and read from there — a tool is imported *from the
workspace*, never from this package. So this module exists only to make
`importlib.resources` able to find the tree beside it.

Registered under the `kingfisher.assets` entry-point group, which is how
kingfisher finds it. Kingfisher names no pack: it asks which are installed. A
pack you write yourself is discovered by exactly the same mechanism, and is no
less first-class than this one.
"""
