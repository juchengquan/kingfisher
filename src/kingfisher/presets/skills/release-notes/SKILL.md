---
name: release-notes
description: Turning a commit range or changelog into release notes written for the people who will read them, grouped by what changed for the user rather than by commit.
---

# Release notes

## When to use

A task asks for release notes, a changelog entry, or a summary of what changed
between two versions.

## Procedure

1. **Get the raw material first.** A commit range, a diff, or an existing
   changelog. Do not write notes from the version number and a guess.
2. **Group by what the reader gains or must do**, not by subsystem and not by
   commit order. Most commits do not deserve a line; several commits often
   deserve one line between them.
3. **Lead with breaking changes**, and say what the reader has to do about each.
   A breaking change buried under features is a support ticket.
4. **Drop the noise.** Merge commits, formatting passes, dependency bumps with
   no user-visible effect. If a section would be empty after that, omit the
   section rather than padding it.
5. **Apply the house format** in `/skills/release-notes/reference/format.md` —
   read it now if you have not already. It holds the section order, the tense,
   and the worked example.

## Reporting

Write the notes themselves as the deliverable, not a description of what the
notes would say. If the commit range contains nothing user-visible, say exactly
that in one line instead of manufacturing entries.
