# The verb that runs a task

**Status:** proposed, not built.
**Date:** 2026-09-04

`kingfisher` has five verbs and none of them runs a task. A person who installs
this can fill a workspace, look at one, diagnose one, and start a server. To use
it they must open an editor.

This proposes `kingfisher run`, and reverses the charter that excluded it.

## The charter being reversed

`pyproject.toml`, above the console script:

> Seeding a workspace and looking at one, which are **the two things the library
> cannot do for itself**: running a task is `kingfisher.run`, and serving one is
> the script above. Deliberately not a general CLI.

The rule is *the CLI exists for what the library cannot do*, and it has held the
surface at five verbs. It is the wrong test, and the reason is that it measures
the library's completeness rather than the user's. `kingfisher.run` means an
editor, four lines, and knowing that a bare task string is refused because a
request must name an agent. For someone who has just installed this, that is not
a filled gap -- it is the gap. "The library can already do it, in Python" is
true of every command-line tool ever written.

**The replacement test: what does a person at a terminal need to do here.**
Under it, `run` is the first verb rather than the excluded one, and the other
four exist to get somebody to it.

The charter's second half is separate and stays true. `tests/integration/driver.py`
runs the eval smoke on a bare invocation and spends real money, which "is a fine
default for a driver and a wrong one for a stranger's first command". A verb with
a required task argument cannot be invoked by accident, so the objection does not
reach `run`.

## Decisions

**R1. Six flags, and the line is drawn at capability grants.** `task`,
`--agent`, `--session`, `--input`, `--data`, `--as`. The driver has thirteen
run-relevant flags; the eight that narrow what a request may activate stay out.

`--data` is not optional and the driver's own docstring is why: "`/data` is
read-only to the agent, and `--data` is the supported way to write there --
copying files in by hand fails, and working around that with `sudo` leaves files
the harness cannot manage." Without it a CLI user has no way to hand the agent a
file at all, and "profile this CSV" is the first thing anyone will try.

The capability flags stay out for three reasons. They put the driver in the
wheel, which is what the old charter feared and would this time be earned.
Narrowing what a request activates is a *deployment* concern with two better
homes -- the service clamps with `grants`, and an agent file declares what it
holds. And `--without-*` resolves against what the workspace offers *now* and
freezes the result, which is a subtlety worth explaining to somebody wiring a
service and not worth putting in front of a first run.

The cost is stated rather than hidden: you cannot say "run this without the
shell" from the command line. Write an agent that declares it, which is a
decision better reviewed than retyped.

**R2. Progress to stderr, the answer to stdout.** The driver writes both to one
stream, which is right for something you watch and wrong for something you
compose with:

    kingfisher run "profile this" --agent analyst --data sales.csv > answer.md

has to put a clean answer in the file while the tool calls still scroll past.
`2>/dev/null` gives silence; a pipe gets the answer alone. `Progress` already
takes its stream as a constructor argument, so the split is latent in the design
and costs one line.

Rejected: a `--quiet` flag. It makes the caller ask for correct behaviour, and
does nothing for whoever forgets it.

**R3. The exit code is the only machine-readable thing `run` has**, so it
carries `stop_reason`. stdout is prose; there is no `--json` and no wire format
here, which is what makes this different from the same question on the service.

| code | means |
|---|---|
| 0 | `stop_reason == "end_turn"` |
| 1 | the turn ran and stopped early -- `max_duration`, `max_steps` |
| 2 | it never ran |

`1` matches what `doctor` and `list` already mean by it: what you asked about is
not clean. The case it exists for is

    kingfisher run "write the report" --agent analyst > report.md && publish report.md

which must not publish a truncated report.

Rejected: a code per reason. Two codes to encode what one stderr line says, in a
vocabulary that grows every time `STOP_REASONS` does -- and that set was
designed to grow.

**R4. Seven more errors get caught, or `run` hands strangers a traceback.**
`main` catches `AccessError` and `ConfigError`. `run` can also raise
`CapabilityError`, `QuotaExceededError`, `SessionBusyError`, `SkillError`,
`SubagentError`, `UnknownReferenceError`, `UnknownSessionError`,
`UnsafeReferenceError` and `UploadError`. All map to 2: every one is "what you
gave me is wrong" -- an agent you cannot reach, a session id that is not there, a
file reference that does not resolve.

`SessionBusyError` is the exception worth wording carefully. It is transient --
another turn holds the session and retrying works -- so `2` tells a script to
edit something when the honest answer is to wait. It does not earn its own code;
it earns a line that says so.

**R5. `--as` is required where a policy exists, and `UNSCOPED` must be typed.**
Measured, not assumed: on a workspace with a `groups.yaml`, `kf.run(...)` today
raises

    AccessError: this deployment has an access policy, so a call must say who is
    calling: pass groups=[...] with the caller's groups, or groups=UNSCOPED to
    run without one

so a `run` without `--as` would fail on every deployment that took access
control seriously.

`list --as` is exempt from that refusal, and its reason is scoped to reading: "a
listing is read-only and whoever runs it is on the host with the policy file in
front of them". `run` acts. So the flag is shared and the default is not: an
absent `--as` on `list` is the operator's view, and on `run` it is left to the
library to refuse.

The self-assertion objection -- `--as senior-analysts` is a claim nobody checks
-- dissolves on inspection. Anyone who can type it can edit `groups.yaml` in the
workspace they own, so it is the trust boundary the file already has, not a hole
beside it.

**R6. The driver keeps its job and loses four flags.** `--list`, `--seed`,
`--from` and `--all` go. Their justification is written down and was good --
"these flags stay because this is the driver you already have open" -- and it was
made when the driver was the only way to run a task. Once `run` ships, you do not
have it open.

Four flags duplicating shipped verbs are four things that can drift from them.
They do not today, because they call the same functions; nothing tests that.

The driver survives, and it has a second job `run` will never take: it is the
entry point for `evals/`, which has no `main` and no argparse of its own. It
imports `SMOKE_TASK`, places fixtures with `prepare_smoke`, and gates on
`checks`. It also keeps the eight capability flags and `--no-memory`, which R1
excluded from the wheel deliberately.

**R7. Two cleanups that are not about `run`.** The CLI package docstring opens
"Two verbs, and deliberately no more" and there are five, soon six -- the
sentence that justified the restraint was never revisited. And `help` goes:
`kingfisher help seed` is byte-for-byte `kingfisher seed --help`, and its only
unique contribution is a friendlier message for an unknown verb, which argparse
already answers with the valid choices listed.

## What this does not do

It does not make the driver shippable, move the smoke into `evals/`, or give
`run` a `--json` form. It does not add capability flags, and R1 says what that
costs.

It does not shorten the five steps between `pip install` and a first task -- a
workspace, a `models.yaml`, a key, a seed, and an agent. `run` makes the last of
those a command instead of a program; the other four are untouched.

## The order to build in

Independent slices, one green commit each, a pull request per slice off `main`.

1. **The two cleanups.** The stale charter, and `help`. Touches nothing `run`
   needs, so it can land first or last.
2. **`run`, at Tier 2.** The verb, the six flags, the output split, the exit
   codes, and the seven error mappings. One slice: an exit-code contract with no
   command to exercise it is not separable from the command.
3. **The driver's four flags.** After `run` lands, because until then the
   justification for keeping them is still true.
