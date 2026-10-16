"""A stand-in for a service the deployment has not wired yet.

Every other tool here does the thing it names. This one does not, and that is
what it is for: it answers with fixed text so a catalogue can offer
`web_search`, an agent can be written and reviewed against it, and the real
implementation can arrive later without a definition changing. The seam is the
name and the arguments, and both can be settled before anyone has chosen a
search vendor or bought a key for one.

Copy this file when you wire the real one. Keep the name, the arguments and the
shape of the result; replace the body and this docstring.
"""

from __future__ import annotations

from langchain_core.tools import tool

#: Reserved for documentation by RFC 2606, so a model that follows one of these
#: with `http_fetch` reaches a page that exists and belongs to nobody. An
#: invented domain is one somebody else can register.
HOST = "https://example.com"

#: Enough to show a list is a list. A placeholder that returned fifty would only
#: be spending context to say the same thing.
MAX_RESULTS = 5

#: Carried in the result text, not left to the description alone. The model
#: reads a description once, when it decides whether to call; what it quotes
#: into an answer is the result. A fake that reads as real at that point is how
#: invented facts reach a user with a citation attached.
MARKER = "PLACEHOLDER"


@tool
def web_search(query: str, limit: int = 3) -> str:
    """Search the web for a query and return ranked results with short summaries.

    PLACEHOLDER: returns fixed example results and never reaches the network.
    Treat what comes back as sample data rather than as findings, and say it was
    a placeholder if you use it in an answer.

    `limit` asks for fewer or more results, up to five.
    """
    wanted = query.strip()
    if not wanted:
        return "refused: a search needs a query"

    count = max(1, min(limit, MAX_RESULTS))
    slug = "-".join(wanted.lower().split())[:40]
    found = [
        f"{n}. Example result {n} for {wanted!r} -- {HOST}/{slug}/{n}\n"
        f"   Sample summary standing in for a search snippet."
        for n in range(1, count + 1)
    ]
    header = f"{MARKER}: {count} example result(s) for {wanted!r}, not a real search"
    return "\n".join([header, *found])


TOOLS = [web_search]
