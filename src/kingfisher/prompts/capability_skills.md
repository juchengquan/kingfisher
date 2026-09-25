## Skills

`/skills` holds reusable procedures, one directory per skill. Read a skill's
`SKILL.md` when its subject matches the task at hand rather than working from memory,
and prefer an existing procedure over inventing a new one.

`/skills` is also the one place where dropping the leading slash is wrong. The catalogue
is shared by every session, so it sits a level above yours, and `skills/` from the shell
names a directory inside your session that does not exist. To run a
script a skill ships, take its path after `/skills/` and reach it as
`"$KINGFISHER_SKILLS/<that path>"`, which the shell exports for you and which holds
wherever the catalogue is deployed.
