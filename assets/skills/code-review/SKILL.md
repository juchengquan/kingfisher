---
name: code-review
description: Reviewing a diff or a set of source files for defects — correctness, error handling, and tests — and reporting findings with enough evidence to act on.
---

# Code review

## When to use

A task asks you to review, audit, or find problems in code. Not for writing new
code, and not for explaining what code does.

## Procedure

1. **Read before judging.** Find the call sites of anything you intend to
   criticise. A function that looks wrong in isolation is often correct given
   how it is used, and the reverse is more common still.
2. **Rank by consequence, not by count.** One silent data-loss path outranks
   twenty naming quibbles. Report the quibbles last or not at all.
3. **Give a failure scenario per finding.** Concrete inputs or state, and the
   wrong output or crash they produce. A finding you cannot write a scenario for
   is a guess — either verify it or drop it.
4. **Check the tests too.** A test that passes whatever the code does is worse
   than no test: it reports safety that is not there. Look for assertions that
   cannot fail, and for the branch nobody covered.
5. **Separate defect from preference.** Say which you are reporting. Reviews
   lose their authority when the two are mixed.

## Reporting

Per finding: file and line, one sentence on what is wrong, and the failure
scenario. State clearly when you found nothing — a review that invents findings
to look thorough is worse than one that reports a clean result.
