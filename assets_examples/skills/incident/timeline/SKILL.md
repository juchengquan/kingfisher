---
name: timeline
description: Building the timeline section of an incident writeup from raw log files — every warning and error in time order, repeats collapsed, silences marked — by running the script this skill ships rather than reading the logs by eye.
---

# Timeline

## When to use

A task asks for an incident's timeline, or for the first section of a
postmortem, and the evidence is one or more log files.

## Run the script

This skill ships a script, and running it is the procedure. It reads every line
of every log you name and prints the warnings and errors in time order, each
repeat collapsed into one line with a count, and every stretch in which no log
said anything:

    python3 "$KINGFISHER_SKILLS/incident/timeline/scripts/timeline.py" data/api.log data/worker.log

Those are shell paths: what the file tools call `/data/api.log` is `data/api.log`
in the shell. `$KINGFISHER_SKILLS` is where the shell finds this catalogue. If it
is unset, this deployment cannot run skill scripts — say so, and build the
timeline from `grep` over the same logs instead.

Add `--gap 10` to mark only silences of ten minutes or more; the default is five.

## Then

1. **Start from the first warning, not the first error.** The script lists both.
   A trigger usually shows as a warning minutes before anything fails, and a
   timeline that opens at the first error hides the window in which somebody
   could have noticed.
2. **Treat every marked silence as a finding.** A service that stopped logging
   was either down or unwatched, and the writeup should say which.
3. **Write each entry as time, what was observed, and where** — the file and
   line the script prints beside it. That is the shape the `postmortem` skill
   asks for.
4. **Quote from the output through `redactor` where you have it.** The script
   prints log text as it was logged, tokens included.

## What it does not do

It reads lines that begin with a timestamp such as `2026-09-01T14:03:22`. A log
in another shape produces no events, and the first line of the output says how
many lines were skipped — a large count means the format is wrong, not the
system quiet. It knows nothing of time zones, so logs from hosts on different
clocks interleave wrongly; check that before trusting the order.
