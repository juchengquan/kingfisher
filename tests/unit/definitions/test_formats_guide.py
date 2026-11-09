"""`docs/guides/formats.md` against the code: its tables name the real tools and fields,
every definition it shows loads, and the calls it shows build."""

from __future__ import annotations

import pytest

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.plugins import backend_at
from tests.conftest import a_subagent, repository_root, subagents_dir

REPO = repository_root()


@pytest.fixture(scope="session")
def formats_doc():
    """`docs/guides/formats.md` -- the format reference these tests check against."""
    return REPO / "docs" / "guides" / "formats.md"


def _materialise(readme: str, cfg) -> None:
    """Write the README's own inline examples into a definitions folder."""
    import re

    for section in re.split(r"\n## ", readme):
        directory = (
            cfg.catalogue_roots["agents"]
            if section.startswith("Agents")
            else subagents_dir(cfg)
        )
        for block in re.findall(r"```yaml\n(.*?)```", section, re.DOTALL):
            if block.startswith("name:"):
                name = block.split("\n")[0].removeprefix("name:").strip()
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"{name}.yaml").write_text(block, encoding="utf-8")

    for block in re.findall(r"```markdown\n(.*?)```", readme, re.DOTALL):
        if not block.startswith("---"):
            continue
        header = block.split("---")[1]
        name = next(
            line.removeprefix("name:").strip()
            for line in header.splitlines()
            if line.startswith("name:")
        )
        folder = cfg.skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(block, encoding="utf-8")


def test_the_readme_tool_table_matches_the_real_tool_surface(cfg, session_dir, formats_doc):
    """The table is the reference a caller builds an allowlist from, so a stale row is a
    CapabilityError someone has to debug.
    """
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel, dispatched

    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")])).graph
    # Only the tools table -- the file has other tables, and scooping up their
    # first columns too is how the first draft of this test "passed" nothing.
    readme = (formats_doc).read_text(encoding="utf-8")
    table = readme.split("## Tools")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == set(dispatched(graph))


def test_the_readme_call_is_valid(cfg, session_dir, formats_doc):
    """Exactly the capabilities the README shows, built for real -- against the
    definitions the README itself writes out.
    """
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    _materialise((formats_doc).read_text(encoding="utf-8"), cfg)

    wired = replace(cfg, skills_enabled=True)
    build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            builtin_tools=("read_file", "ls", "glob", "grep", "execute", "task"),
            skills=("code-review",),
            subagents=("reviewer",),
        ),
    )


def test_the_readme_subagent_table_matches_the_real_field_set(formats_doc):
    """The table is where a contributor learns which fields exist, and now that an
    unlisted one is an error, a stale row is a definition that will not load.
    """
    from kingfisher.core.definitions.subagents.spec import KNOWN

    readme = (formats_doc).read_text(encoding="utf-8")
    table = readme.split("## Subagents")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == KNOWN


def test_the_readme_links_into_no_asset_tree(formats_doc):
    """The page has to stand on its own, because the files are leaving."""
    import re

    readme = (formats_doc).read_text(encoding="utf-8")
    targets = [t for _, t in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme)]

    into_assets = [t for t in targets if t.split("/")[0] in {"skills", "subagents", "tools"}]
    assert not into_assets, f"README links into an asset tree: {into_assets}"


def test_every_complete_definition_in_the_readme_parses(formats_doc):
    """The README shows a whole definition before it shows the field table, and a
    documented example that does not load is worse than none -- it is copied, it
    fails, and the format gets blamed.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.core.definitions.agents.reading import read
    from kingfisher.core.definitions.documents import DefinitionText

    readme = (formats_doc).read_text(encoding="utf-8")
    # Split on the top-level headings, so each block is read by the format whose
    # section it sits in.
    sections = re.split(r"\n## ", readme)
    blocks = [
        (section.startswith("Agents"), body)
        for section in sections
        for body in re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
        if body.startswith("name:")
    ]

    assert blocks, "the README opens the section with a whole definition"
    assert any(is_agent for is_agent, _ in blocks), "the agents section shows one too"
    for is_agent, block in blocks:
        if is_agent:
            read(DefinitionText(block, _Path("readme.yaml")))
        else:
            a_subagent(block, "readme.yaml")


#: What a fenced block has to mention to be a definition fragment rather than a
#: `models.yaml` example, which the same page also shows.
DEFINITION_FIELDS = ("tools:", "skills:", "subagents:", "middlewares:", "builtin_tools:")


def test_every_field_fragment_in_the_readme_parses_too(formats_doc):
    """The other half of the page, and the half that rotted.

    Its neighbour above reads only blocks starting with `name:`, and says so -- the
    fragments showing one field are not definitions. Nothing read them, so when the
    audienced fields changed shape on 2026-09-03 every fragment showing the old one
    stayed on the page, correct-looking and wrong, and the whole suite passed.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.core.definitions.agents.reading import read
    from kingfisher.core.definitions.documents import DefinitionText

    required = {
        "name": "name: probe\n",
        "description": "description: A probe.\n",
        "system_prompt": "system_prompt: |\n  Do the task.\n",
    }
    fragments = [
        (section.startswith("Agents"), body)
        for section in re.split(r"\n## ", formats_doc.read_text(encoding="utf-8"))
        for body in re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
        if not body.startswith("name:") and any(f in body for f in DEFINITION_FIELDS)
    ]

    assert fragments, "no field fragments found -- this asserts nothing"
    for is_agent, fragment in fragments:
        # Only what it leaves out: a whole definition shown under its path writes its own,
        # and a key written twice is refused.
        written = set(re.findall(r"^(\w+):", fragment, re.MULTILINE))
        whole = "".join(line for key, line in required.items() if key not in written) + fragment
        if is_agent:
            read(DefinitionText(whole, _Path("readme.yaml")))
        else:
            a_subagent(whole, "readme.yaml")


# -- the one preset that consults another ---------------------------------


def test_the_readme_run_on_example_is_valid(cfg, session_dir, formats_doc):
    """The second call the README shows, built for real."""
    from langchain_core.messages import AIMessage

    from kingfisher import RunOn
    from tests.conftest import FakeToolCallingModel

    _materialise((formats_doc).read_text(encoding="utf-8"), cfg)

    build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            subagents=("reviewer", "second-opinion"), models=("cheap-model",)
        ),
        run_on={"second-opinion": RunOn("cheap-model")},
    )
