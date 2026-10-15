"""The two tools that profile a CSV, driven against the shapes a CSV really holds.

Their coverage was one well-formed file, and a profile's whole job is to describe a
file nobody has looked at yet -- so the shapes worth describing, two columns sharing a
name and a row that is not the header's width, were reached by no test at all.

Through the backend a build hands a tool, with the file really on disk, because both
tools read one through `download_files` and a profile of a file this test made up in
memory proves nothing about the path the model takes.
"""

from __future__ import annotations

import pytest

from kingfisher import backend_at
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.kinds.tools.catalogue import LocalToolRepository, tool_name
from tests.conftest import on_backend


@pytest.fixture
def tools(shipped):
    return {tool_name(t): t for t in LocalToolRepository(shipped / "tools").tools}


@pytest.fixture
def profile(tools, cfg, session_dir, fake_model):
    """Profile one file's text, through the real backend."""
    backend = build_agent(
        cfg, backend=backend_at(cfg, session_dir), model=fake_model
    ).context.backend

    def call(text: str, which: str = "csv_profile") -> str:
        (session_dir / "inputs" / "one.csv").write_text(text, encoding="utf-8")
        return on_backend(tools[which], backend, file_path="/inputs/one.csv")

    return call


#: The same name twice, and a different number of filled cells in each, so a reader
#: merging them cannot produce either column's answer by accident.
TWICE = "region,sales,region\nnorth,10,south\n,20,west\n"


def test_two_columns_sharing_a_name_are_two_columns(profile):
    """Gathered by name, both `region` columns appended into one list: two rows came
    back as four filled cells and four distinct values, the same line was printed once
    per spelling with nothing to tell them apart, and a column half empty read as a
    quarter missing. Gathered by position each says its own count.
    """
    said = profile(TWICE)

    assert said.splitlines() == [
        "3 columns, 2 rows",
        "region (column 1): text, 1 filled, 1 missing (50%), 1 distinct",
        "sales: number, 2 filled, 0 missing (0%), 2 distinct",
        "region (column 3): text, 2 filled, 0 missing (0%), 2 distinct",
    ]


def test_a_name_the_file_uses_once_is_reported_as_the_file_wrote_it(profile):
    """The control for the column numbers above: appending one to every name would
    satisfy that rule and rename every column of every well-formed file.
    """
    said = profile("region,sales\nnorth,10\n")

    assert said.splitlines()[1].startswith("region: ")
    assert "column 1" not in said


def test_the_two_tools_agree_about_how_many_columns_there_are(profile):
    """`csv_columns` listed both names while `csv_profile` measured one column and
    counted three, so the two tools described the same file differently -- which is
    the disagreement a shared `_read` exists to prevent.
    """
    named = profile(TWICE, which="csv_columns").splitlines()
    profiled = profile(TWICE).splitlines()

    assert len(named) == 3
    assert profiled[0].startswith(f"{len(named)} columns")
    assert len(profiled) == 1 + len(named)


def test_a_row_that_is_not_the_headers_width_is_counted(profile):
    """A row with a cell the header has no column for lost it in silence, and the
    answer a model plans by said the file was rectangular. The cells past the header
    still cannot be profiled -- they belong to no column -- so the count is what says
    the file is not the shape the rest of the answer describes.
    """
    said = profile("a,b\n1,2\n3,4,5\n6\n")

    assert said.splitlines()[0] == "2 columns, 3 rows (2 row(s) not 2 cells wide)"


def test_a_file_whose_rows_all_fit_says_nothing_about_width(profile):
    """The other half: a note on every answer tells a model to go and look at a file
    that was fine.
    """
    assert profile("a,b\n1,2\n3,4\n") == (
        "2 columns, 2 rows\na: number, 2 filled, 0 missing (0%), 2 distinct\n"
        "b: number, 2 filled, 0 missing (0%), 2 distinct"
    )


def test_an_empty_file_is_said_rather_than_profiled(profile):
    """Both tools, because the header is read before either looks at a column and an
    empty file has none -- the shape that would otherwise be a `StopIteration` out of
    a tool.
    """
    assert profile("") == "/inputs/one.csv: empty"
    assert profile("", which="csv_columns") == "/inputs/one.csv: empty"
