"""Keeping `execute` inside the workspace, and the two kernels that can enforce it.

A subpackage because the three modules are one subject read at three depths, and
because that subject is the only one in `infrastructure/` where being wrong is a
security failure rather than a bug. `confinement` decides the policy, `fence` and
`bubblewrap` are the two mechanisms that carry it, and which of the two is available
is a property of the host rather than of kingfisher.
"""
