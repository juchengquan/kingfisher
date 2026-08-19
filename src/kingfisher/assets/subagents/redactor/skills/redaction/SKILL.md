---
name: redaction
description: How to quote from a file that may contain credentials. Use before putting any file's contents into an answer someone else will read.
---

# Quoting from a file that may hold secrets

This skill ships inside `redactor`'s own folder, so the `redactor` delegate is
told about it and nothing else is. A skill in the shared `skills/` directory is
one any request may activate; this one arrives with the delegate or not at all.

## The procedure

1. `mask_secrets` first, always. Never `read_file` a file you are about to quote
   from — the point is that the unmasked text never enters the transcript.
2. Read the header it returns. `0 masked` and `12 masked` are different answers
   and the caller needs to know which they got.
3. Quote the smallest span that makes your point. A masked file is safer than an
   unmasked one, not safe.
4. Say what you masked. "Three lines contained credentials" is information; a
   clean-looking excerpt that quietly dropped them is a misleading one.

## What this does not do

The patterns are crude, and they are meant to be. Anything shaped unlike an
`api_key = …` line survives them — a base64 blob, a private key body, a
credential in prose. Treat the output as *less* dangerous, never as safe, and
say so when you report it.
