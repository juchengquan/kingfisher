---
name: iso-8601
description: How to report a date once it has been read, and what to do with one that cannot be read unambiguously.
---

# Reporting dates

One line per date, in the order they appear in the file:

    <line number>  <as written>  ->  <ISO 8601, or "ambiguous">

## The rules

**Run `iso_timestamp` on every date, including ones that look obvious.** A date
that already reads as ISO may still be `2026-13-01`, and the eye does not catch
that as reliably as the tool does.

**An ambiguous date is a result, not a failure.** `03/04/2026` is 3 April in most
of the world and 4 March in the United States. Report both readings and say the
string does not settle it. Do not pick by majority, by the other dates in the
file, or by what the surrounding text implies -- if the file's own convention is
knowable, say so as a separate observation and leave the date flagged.

**Say where each date came from.** A caller correcting an ambiguous date needs the
line, not the value.

**Do not edit the file.** This procedure reports; whoever asked decides what to
change.
