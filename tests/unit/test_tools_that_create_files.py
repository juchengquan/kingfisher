"""A tool that creates a file saves it, says where in one line, and never says it
saved a file that was refused.

Against the backend a build hands a tool, so a refused target is refused by the turn's
own rules. A bare backend refuses nothing, and a tool that ignores `.error` passes
against it.
"""

from __future__ import annotations

import csv
import io
import re
from xml.etree import ElementTree

import pytest

from kingfisher import backend_at
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.kinds.tools.catalogue import LocalToolRepository, tool_name
from tests.conftest import on_backend, repository_root

SALES = (
    "region,sales\n"
    "north,10\n"
    "south,25.5\n"
    "east,7\n"
    "west,12\n"
    "R&D <lab>,3\n"
    "central,0\n"
    "islands,\n"
    "offshore,n/a\n"
)

#: Each tool, the arguments of one ordinary call, where it saves when not told, and a
#: `save_as` to tell it.
CALLS = {
    "bar_chart": (
        {"file_path": "/inputs/sales.csv"},
        "/outputs/chart.svg",
        "/scratchpad/try1.svg",
    ),
    "csv_filter": (
        {"file_path": "/inputs/sales.csv", "column": "region", "value": "north"},
        "/outputs/filtered.csv",
        "/scratchpad/north.csv",
    ),
}

LINE = re.compile(
    r"Saved (?P<name>\S+) \((?P<kind>[^,]+), (?P<size>\d+(?:\.\d)?) (?P<unit>B|KB|MB)\) "
    r"to (?P<path>/\S+)(?: — (?P<note>.+))?"
)


@pytest.fixture
def tools(shipped):
    return {tool_name(t): t for t in LocalToolRepository(shipped / "tools").tools}


@pytest.fixture
def handed(cfg, session_dir, fake_model):
    """The backend a turn hands its tools, with `SALES` in `/inputs` to work from."""
    (session_dir / "inputs" / "sales.csv").write_text(SALES, encoding="utf-8")
    return build_agent(cfg, backend=backend_at(cfg, session_dir), model=fake_model).context.backend


def _saved(backend, path: str) -> bytes | None:
    (got,) = backend.download_files([path])
    return got.content


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_file_goes_to_the_tools_default_when_save_as_is_left_out(name, tools, handed):
    """A default the result does not name is a file the agent has to go looking for."""
    args, default, _ = CALLS[name]

    said = on_backend(tools[name], handed, **args)

    assert _saved(handed, default)
    assert f" to {default} " in said


@pytest.mark.parametrize("name", sorted(CALLS))
def test_save_as_is_where_the_file_goes(name, tools, handed):
    """A `save_as` the tool ignored would leave every draft overwriting the last."""
    args, default, elsewhere = CALLS[name]

    said = on_backend(tools[name], handed, **args, save_as=elsewhere)

    assert _saved(handed, elsewhere)
    assert _saved(handed, default) is None, "saved to the default as well"
    assert f" to {elsewhere} " in said


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_refused_target_is_a_failure_and_nothing_is_written(name, tools, handed):
    """`upload_files` hands a refusal back on the response rather than raising it, so a
    tool that never reads `.error` answers "Saved" for a file that does not exist.
    """
    args, default, _ = CALLS[name]
    refused = f"/inputs/x{default[default.rindex('.') :]}"

    with pytest.raises(OSError, match=f"not saved to {refused}: permission_denied"):
        on_backend(tools[name], handed, **args, save_as=refused)

    assert _saved(handed, refused) is None
    assert _saved(handed, default) is None, "fell back to the default instead"


@pytest.mark.parametrize(("name", "kind", "unit"), [
    ("bar_chart", "SVG image", "KB"),
    ("csv_filter", "CSV", "B"),
])
def test_the_result_is_one_line_naming_the_file_its_kind_size_and_place(
    name, kind, unit, tools, handed
):
    """The line is all the model is told, so a size that is not the file's, or a path
    that is not where it went, is believed.
    """
    args, default, _ = CALLS[name]

    said = on_backend(tools[name], handed, **args)

    found = LINE.fullmatch(said)
    assert found, said
    data = _saved(handed, default)
    assert data is not None
    assert (found["name"], found["kind"], found["path"]) == (
        default.rsplit("/", 1)[1],
        kind,
        default,
    )
    # Both units, one per tool: the chart is past a kilobyte and the filtered rows are not.
    assert found["unit"] == unit
    shown = f"{len(data) / 1024:.1f}" if unit == "KB" else str(len(data))
    assert found["size"] == shown
    assert found["note"], "no note, though each tool has something to say"


def test_the_chart_is_an_svg_a_viewer_can_open(tools, handed):
    """A label holding `&` or `<` written as it stands makes a file no viewer opens,
    and the tool still answers "Saved".
    """
    said = on_backend(tools["bar_chart"], handed, file_path="/inputs/sales.csv")

    data = _saved(handed, "/outputs/chart.svg")
    assert data is not None
    root = ElementTree.fromstring(data)  # noqa: S314 -- our own tool's output
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    svg = "{http://www.w3.org/2000/svg}"
    labels = [one.text for one in root.iter(f"{svg}text")]
    assert "R&D <lab>" in labels
    # One bar per row with a number, `central`'s zero among them; the two without
    # one are left out and counted.
    assert len(list(root.iter(f"{svg}rect"))) == 6
    assert "6 bars" in said
    assert "2 row(s) with no number left out" in said


def test_the_filter_keeps_exactly_the_rows_whose_cell_equals_the_value(tools, handed, session_dir):
    """A match by prefix or by case keeps a row nobody asked for, a short row read past
    its end stops the export, and an export is believed without being read.
    """
    (session_dir / "inputs" / "orders.csv").write_text(
        "id,region,status\n"
        "1,north,open\n"
        "2,south,open\n"
        "3, north ,closed\n"
        "4,North,open\n"
        "5,northeast,open\n"
        "6\n",
        encoding="utf-8",
    )

    said = on_backend(
        tools["csv_filter"], handed, file_path="/inputs/orders.csv", column="region", value="north"
    )

    data = _saved(handed, "/outputs/filtered.csv")
    assert data is not None
    assert list(csv.reader(io.StringIO(data.decode("utf-8")))) == [
        ["id", "region", "status"],
        ["1", "north", "open"],
        ["3", " north ", "closed"],
    ]
    assert "2 of 6 rows" in said


def test_a_file_already_at_the_target_is_replaced(tools, handed):
    """A tool that refused a taken default would fail every second call that left
    `save_as` out.
    """
    on_backend(
        tools["csv_filter"], handed, file_path="/inputs/sales.csv", column="region", value="north"
    )

    said = on_backend(
        tools["csv_filter"], handed, file_path="/inputs/sales.csv", column="region", value="south"
    )

    assert said.startswith("Saved filtered.csv ")
    assert _saved(handed, "/outputs/filtered.csv") == b"region,sales\nsouth,25.5\n"


def test_the_guides_recipe_says_saved_only_for_a_file_that_was(handed):
    """The guide's example is what gets copied, and its `.error` check is the line a
    copy is likeliest to lose. Run as written, so a cut there goes red here.
    """
    guide = (repository_root() / "docs" / "guides" / "tools.md").read_text(encoding="utf-8")
    section = guide.split("\n## A tool that creates a file\n", 1)[1].split("\n## ", 1)[0]
    source = re.findall(r"```python\n(.*?)```", section, re.DOTALL)[0]
    namespace: dict = {}
    exec(compile(source, "tools.md", "exec"), namespace)  # noqa: S102 -- our own guide
    (save_table,) = namespace["TOOLS"]
    table = {
        "columns": ["region", "sales"],
        "rows": [["north", "10"], ["south", "25.5"], ["east", "7"]],
    }

    with pytest.raises(OSError, match="permission_denied") as refused:
        on_backend(save_table, handed, **table, save_as="/inputs/table.csv")
    said = on_backend(save_table, handed, **table)

    assert _saved(handed, "/inputs/table.csv") is None
    assert _saved(handed, "/outputs/table.csv")
    # The two lines the guide quotes are the ones this code produces, the failure as
    # a tool's exception reaches the model.
    assert said in guide
    assert f"Error: OSError: {refused.value}" in guide
