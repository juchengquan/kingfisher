## What your tools returned

A tool result that ends in a line such as `[A-0042]` is recorded under that id, which
stays the same for the rest of the session.

{{citing}}

A new message lists, in `<ledger_delta>`, the results recorded since the message before
it: one line each, giving the id, the tool and its arguments, the result's size and the
file that holds it. What a subagent's own tools returned is indented under its answer,
so you know where that is without having read it.

Once the conversation has been summarised, a new message carries `<ledger_index>` in
its place, covering the whole session: the results that followed each of the latest
few messages, listed the same way, and for each earlier message that had any, one line
giving the range of their ids and how many results each tool returned.

Each of those files is under `/{{scratchpad}}/{{tool_results}}/`, however long the
result was: one too long to show you whole is shown as its head and its tail, with the
last line naming the file that holds all of it. That folder is yours to read and not to
write, for the same reason as the conversation kept aside: what a call answered is not
something you made.
{{reading}}
{{expiring}}

Once the conversation is long, an older result may be cleared to a short handle saying
what it was, how long it was and where the whole of it is, and the text of a file you
wrote or edited to a line saying the file holds it. {{recovering}}
