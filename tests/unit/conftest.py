from __future__ import annotations

from dataclasses import replace
from typing import Any

from tests.conftest import subagents_dir


def define(cfg, body: str, name: str = "reviewer") -> None:
    """One subagent definition, in the file `name` names."""
    (cfg.definitions / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.definitions / "subagents" / f"{name}.yaml").write_text(body, encoding="utf-8")


def define_each(cfg, *definitions: str) -> None:
    """Each subagent definition, in a file named by its own first line."""
    directory = subagents_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    for body in definitions:
        name = body.split("\n")[0].removeprefix("name: ").strip()
        (directory / f"{name}.yaml").write_text(body, encoding="utf-8")


def offer_skills(cfg, *names: str) -> None:
    """A skill for each name, in the shared skills folder."""
    for name in names:
        (cfg.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A procedure.\n---\nBody.\n", encoding="utf-8"
        )


def lock_skills(cfg, text: str) -> None:
    """`skill_locks.yaml`, beside the skills folder as a deployment keeps it."""
    (cfg.skills_dir.parent / "skill_locks.yaml").write_text(text, encoding="utf-8")


def _marked(cfg: Any, model: str | None = None, **fields: Any) -> Any:
    """`cfg`, with one model's entry -- the default's unless named -- carrying `fields`."""
    models = cfg.models
    name = model or models.default
    entry = replace(models.models[name], **fields)
    return replace(cfg, models=replace(models, models={**models.models, name: entry}))
