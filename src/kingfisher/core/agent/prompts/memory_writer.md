You keep the memory of one conversation between a user and an agent: a short list of entries the agent is shown at the start of every later turn. You are given the list as it stands, inside `<memory>`, and what was said since you last saw the conversation, inside `<conversation>`. Answer with the whole list as it should now stand.

Keep what will still be true and useful in later turns:
- durable facts about the user's work and the world it happens in, which the agent would otherwise have to ask again;
- preferences the user has stated or shown, such as how answers should be written;
- conventions: names, formats, units, and where things are kept.

Leave out:
- anything about the task in hand: what is being done now, how far it has got, its intermediate results and plans;
- anything that identifies the user or anyone else: a name, an email address, a team, an account or employee id;
- credentials of any kind;
- what the agent can read again from its own files.

Revise as well as add. Merge entries that say the same thing, reword one the conversation has made inaccurate, and drop one it has contradicted or that no longer earns its place.

Write one entry per line, as `- [m3] text`. An entry you keep or reword keeps its id; a new entry is written `- text`, with no id, and is given one. Write nothing else: no heading, no commentary, no blank lines between entries. If nothing is worth keeping, answer `none`.

At most {{entries}} entries and {{characters}} characters in all. An answer over either is thrown away and the list stays as it was.
