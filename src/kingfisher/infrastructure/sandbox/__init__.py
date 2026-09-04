"""Keeping `execute` inside the workspace, and the two kernels that can enforce it.

A subpackage because the three modules are one subject read at three depths, and
because that subject is the only one in `infrastructure/` where being wrong is a
security failure rather than a bug. `confinement` decides the policy, `fence` and
`bubblewrap` are the two mechanisms that carry it, and which of the two is
available is a property of the host rather than of kingfisher.

Deliberately holds no deepagents import, which is what makes the grouping
possible at all: the backend that *applies* a confinement lives in
`harness/backend.py` and stays there, because only that package may import the
framework. So the line here is not "everything about the sandbox" -- it is
everything about the sandbox that does not need the agent runtime to say it.

No re-exports. Each module is imported by name, so the subpackage is a place
rather than a second surface to keep in step with the first.
"""
