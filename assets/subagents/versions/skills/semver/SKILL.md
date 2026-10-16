---
name: semver
description: How to report versions once they are ordered, and what to do with a string that is not a version or a tie that precedence cannot break.
---

# Reporting versions

The order `order_versions` gave, lowest first, one line per version:

    <as written>  <where it came from, if it came from a file>

Then the ties, then the strings that are not versions.

## The rules

**Run `order_versions` on the whole set in one call**, including a set of two
that looks obvious. The mistakes it prevents are the ones that look right:
`1.9.0` above `1.10.0`, `1.0.0` below `1.0.0-rc.1`, `beta.11` above `rc.1`.

**The highest version is not always the latest release.** `2.0.0-rc.1` outranks
`1.9.0`, so when a pre-release tops the list and the caller asked for the latest
*release*, name both: the highest version, and the highest with no pre-release
part.

**A tie is a result, not a failure.** `1.4.0+build.7` and `1.4.0+build.9` have the
same precedence -- SemVer ignores build metadata -- and nothing in the strings
says which is newer. Report them as tied. Do not break the tie by the build
number, by file order, or by date: if the caller needs one, that is their call.

**What is not a version stays as written.** `1.2`, `1.02.0` and `release-7` are
not SemVer. List them under *not versions* and do not pad, trim or reorder them
into something that parses -- a caller cannot tell a version you read from one
you repaired.

**Do not edit the file.** This procedure reports; whoever asked decides what to
change.
