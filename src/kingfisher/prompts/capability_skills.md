## Skills

`/skills` holds reusable procedures, one directory per skill. Read a skill's
`SKILL.md` when its subject matches the task at hand rather than working from memory,
and prefer an existing procedure over inventing a new one.

`/skills` is also the one place where dropping the leading slash is wrong, and it fails
quietly rather than loudly. The catalogue is shared by every session, so it sits a level
above yours; `skills/` from the shell is a *different, real* directory holding only this
session's uploads, so a read there comes back empty instead of erroring. To run a
script a skill ships, reach it as `"$KINGFISHER_SKILLS/<name>"`, which the shell
exports for you and which holds wherever the catalogue is deployed.
