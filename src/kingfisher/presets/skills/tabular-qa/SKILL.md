---
name: tabular-qa
description: Profiling and quality-checking a delimited data file — row and value counts, duplicate and blank detection, outlier rules, and normalising inconsistent categorical values before counting them.
---

# Tabular QA

## When to use

A task asks you to profile, summarise, count or quality-check a CSV or similar
delimited file.

## Procedure

1. **Look before computing.** Read the first few lines to learn the real column
   names and separators rather than assuming them.
2. **Compute once, in one script.** Write a single script that emits every figure
   you need as JSON, run it, and read the output. Do not compute figures by eye
   from a partial read, and do not run one command per figure.
3. **Normalise before counting distinct values.** Strip surrounding whitespace and
   compare case-insensitively; report the count of canonical values, not raw
   strings.
4. **Duplicates** means rows identical across every column. Report the number of
   *extra* copies (total rows minus distinct rows), and say so — "14 duplicates"
   is ambiguous between that and the number of groups.
5. **Blanks** are empty cells where a value is expected. Count rows, and name the
   column.
6. **Outliers**: state the rule you used before applying it. An order-of-magnitude
   gap from the next-largest value, or a value outside an IQR fence, both qualify;
   pick one and say which.

## Reporting

State the rule alongside any judgement-based figure. If a quantity is ambiguous
under more than one reasonable definition, give the definition you used rather
than the bare number.
