`/skills` is the one place where `$KINGFISHER_SESSION_ROOT` is wrong. The catalogue is
shared by every session, so it is not under your session root, and
`$KINGFISHER_SESSION_ROOT/skills` names a directory that does not exist. To run a
script a skill ships, take its path after `/skills/` and reach it as
`"$KINGFISHER_SKILLS/<that path>"`, which the shell exports for you and which holds
wherever the catalogue is deployed.
