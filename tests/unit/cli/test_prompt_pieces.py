"""`kingfisher prompt --pieces` and `--piece`: the names a deployment may reword, and
kingfisher's own text to copy."""

from __future__ import annotations

import os

from kingfisher.cli.__main__ import main
from kingfisher.core.definitions.wording import FIXED, PROMPTS_FOLDER, read_wording, shipped
from kingfisher.plugins import LocalSessions

#: What the command's own sessions add: kingfisher's shell, worded under `sessions/`.
SHELL = LocalSessions().prompt_pieces()


def test_the_pieces_are_listed_before_any_deployment_exists(monkeypatch, tmp_path, capsys):
    """The names are wanted to write a deployment's first replacement; a listing that
    read the configuration first refused to say them until one was set up."""
    for name in [one for one in os.environ if one.startswith("KINGFISHER_")]:
        monkeypatch.delenv(name)
    # Where no `.env` is, so nothing configures one behind the test's back.
    monkeypatch.chdir(tmp_path)

    assert main(["prompt", "--pieces"]) == 0

    listed = capsys.readouterr().out.splitlines()
    # The shell's too: listed apart from kingfisher's, the pieces most agents read were
    # missing from the one listing a deployment is told to look in.
    assert [line.split("  ")[0] for line in listed] == sorted({*shipped(), *SHELL})
    assert {line.split("  ")[0] for line in listed if "fixed" in line} == FIXED


def test_every_piece_printed_is_a_replacement_the_folder_takes(tmp_path, capsys):
    """Printed with anything added or lost -- a heading, a stripped blank, a mangled
    brace -- the text a deployment copies to start from would be refused."""
    folder = tmp_path / PROMPTS_FOLDER
    pieces = {**shipped(), **SHELL}
    replaceable = sorted(set(pieces) - FIXED)
    for name in replaceable:
        assert main(["prompt", "--piece", name]) == 0
        path = folder / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(capsys.readouterr().out, encoding="utf-8")

    taken = read_wording(folder, SHELL)

    assert [taken.text(name) for name in replaceable] == [
        pieces[name].strip() for name in replaceable
    ]
