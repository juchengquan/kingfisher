"""What the agent is told about the shell, which only the shell can say."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend, LocalShellBackend

from kingfisher import CommandResult, DefaultBackends, ShellPrompt, backend_at
from kingfisher.infrastructure.harness.backend import shell_prompt_of
from kingfisher.infrastructure.prompting import kingfishers_shell, render_system_prompt
from kingfisher.layout import Layout

#: What a command can see of the claims kingfisher's own wording makes.
PROBE = 'echo "pwd=$(pwd)"; echo "tmp=$TMPDIR"; echo "skills=$KINGFISHER_SKILLS"'

#: Phrases only kingfisher's own shell may be described with.
ITS_OWN = ("runs on the host", "out of the shell's reach", "$TMPDIR", "$KINGFISHER_SKILLS")

THEIRS = ShellPrompt(
    prompt="## The shell\n\nIt runs in a sandbox holding this session at `/workspace`, so "
    "`/{{inputs}}/x` is `/workspace/{{inputs}}/x` there.",
)


@dataclass(frozen=True)
class Plain:
    """The runner a deployment writes first: it runs the command it is handed, and is
    handed nothing else. Not local, so no fence of this host's is put in front of it.
    """

    @property
    def local(self) -> bool:
        return False

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        done = subprocess.run(  # noqa: S602 -- `execute` is a shell by definition
            command, shell=True, capture_output=True, text=True, timeout=timeout, check=False
        )
        return CommandResult(
            output=done.stdout + done.stderr, exit_code=done.returncode, truncated=False
        )


def _seen(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _told(shell: ShellPrompt | None, **kwargs: object) -> str:
    return " ".join(render_system_prompt(skills_enabled=True, shell=shell, **kwargs).split())


def test_kingfishers_own_shell_is_described_by_what_holds_of_it(cfg, session_dir):
    """Its wording says the shell starts in the session root with `TMPDIR` the
    scratchpad and `KINGFISHER_SKILLS` set. Driven rather than read off `shell_env`,
    because the claim is about what a command sees.
    """
    backend = backend_at(cfg, session_dir)
    seen = _seen(backend.execute(PROBE).output)

    assert shell_prompt_of(backend) is kingfishers_shell()
    assert Path(seen["pwd"]).resolve() == session_dir.resolve()
    assert Path(seen["tmp"]).resolve() == (session_dir / "scratchpad").resolve()
    assert seen["skills"]
    for phrase in ITS_OWN:
        assert phrase in _told(kingfishers_shell()), phrase


def test_a_runner_s_shell_is_not_described_as_kingfishers_own(cfg):
    """A runner is handed the command and nothing else, so the wording that was sent
    to every deployment told the agent of a session root, a `TMPDIR` and a
    `KINGFISHER_SKILLS` its shell did not have. The runner here is the plainest one a
    deployment writes, and the premise is driven with it rather than assumed.
    """
    backend = DefaultBackends(runner=lambda _where: Plain()).open(cfg, "s")
    seen = _seen(backend.execute(PROBE).output)

    assert Path(seen["pwd"]).resolve() != (cfg.workspace / "sessions" / "s").resolve()
    assert not seen["skills"]
    assert shell_prompt_of(backend) is None
    told = _told(None)
    for phrase in ITS_OWN:
        assert phrase not in told, phrase
    assert "check what the shell sees" in told


def test_a_runner_s_own_wording_is_what_the_agent_is_told(cfg):
    """And it is filled from the layout as kingfisher's own is, so a renamed folder
    cannot leave the deployment's sentence naming the old one.
    """
    backend = DefaultBackends(runner=lambda _where: Plain(), shell_prompt=THEIRS).open(cfg, "s")
    told = _told(shell_prompt_of(backend), layout=Layout(inputs="in"))

    assert shell_prompt_of(backend) is THEIRS
    assert "`/in/x` is `/workspace/in/x` there" in told
    assert "{{" not in told


def test_wording_without_a_runner_is_refused(cfg, session_dir):
    """Kingfisher's own shell is described by what holds of it, so wording handed in for
    it could only be wrong -- and a deployment wanting to add to every prompt has
    `deployment_prompt`, which the refusal names.
    """
    with pytest.raises(TypeError, match="deployment_prompt"):
        DefaultBackends(shell_prompt=THEIRS)
    with pytest.raises(TypeError, match="deployment_prompt"):
        backend_at(cfg, session_dir, shell_prompt=THEIRS)


class Sandbox(LocalShellBackend):
    """A deployment's own shell, saying what it sees."""

    shell_prompt = THEIRS


class Silent(LocalShellBackend):
    """A deployment's own shell, saying nothing."""


def test_a_sandbox_of_a_deployment_s_own_is_read_for_what_it_says(cfg, session_dir):
    """Read off the shell, which is what knows: the sandbox that says something is
    taken at its word, and the subclass of `DefaultBackends` that swaps in one saying
    nothing is described neutrally -- on the session backends, it would have inherited
    kingfisher's wording and been told of a shell it does not have.
    """
    routes = backend_at(cfg, session_dir).routes

    class Swapped(DefaultBackends):
        def open(self, cfg_, session_id, /, *, catalogue=None):
            mine = super().open(cfg_, session_id, catalogue=catalogue)
            return CompositeBackend(default=Silent(root_dir=str(session_dir)), routes=mine.routes)

    said = CompositeBackend(default=Sandbox(root_dir=str(session_dir)), routes=routes)

    assert shell_prompt_of(said) is THEIRS
    assert shell_prompt_of(Sandbox(root_dir=str(session_dir))) is THEIRS
    assert shell_prompt_of(Swapped().open(cfg, "s")) is None


def test_a_shell_prompt_that_is_not_one_is_refused(session_dir):
    """A string set where the wording goes would otherwise fail on the first turn, as an
    attribute error from inside the prompt's assembly.
    """

    class Mistaken(LocalShellBackend):
        shell_prompt = "It runs in a sandbox."

    with pytest.raises(TypeError, match="ShellPrompt"):
        shell_prompt_of(Mistaken(root_dir=str(session_dir)))


#: Ways a deployment really writes a blank wrong. `inptus` is a letter out of place;
#: the rest are the shapes the scan could not see at all -- a space inside the braces,
#: a hyphen where the field has an underscore, and a name with a dot in it. The scan
#: matched `\w+`, so those three were refused by nothing *and* filled by nothing: they
#: reached the model as braces, in wording a deployment had customised and been told
#: nothing about. Measured on all four before the pattern was widened.
MISSPELT = ["{{inptus}}", "{{ inputs }}", "{{tool-results}}", "{{inputs.dir}}", "{{}}"]


@pytest.mark.parametrize("blank", MISSPELT)
@pytest.mark.parametrize("part", ["prompt", "skills", "execute"])
def test_a_blank_no_layout_fills_is_refused_when_the_wording_is_made(part, blank):
    """At the deployment's start, where a typo is cheap, rather than on its first turn --
    and the refusal names the blanks there are.

    Every part, because each is read by a different reader and the scan runs per part:
    held on `execute` alone, the same mistake in `prompt` reached the agent's own
    section.
    """
    with pytest.raises(ValueError, match=r"which no layout fills"):
        ShellPrompt(**{part: f"- `/{blank}/x` is `x`."})


def test_every_blank_a_layout_fills_is_accepted_in_every_part():
    """The control: a refusal that fired on everything would pass the rule above, and
    the five names are the whole of what a deployment may write.
    """
    every = "".join(
        f"{{{{{leaf}}}}}" for leaf in ("inputs", "outputs", "memory", "scratchpad", "tool_results")
    )

    ShellPrompt(prompt=every, skills=every, execute=every)


def test_a_part_left_out_is_said_neutrally_and_an_empty_one_says_nothing():
    """Left out, the agent is still told something true about the shell; set to `""`, a
    deployment that has said it elsewhere is not contradicted.
    """
    neutral_skills = "The shell may not see `/skills` at all."

    assert neutral_skills in _told(ShellPrompt(prompt="Mine."))
    assert neutral_skills not in _told(ShellPrompt(prompt="Mine.", skills=""))
    assert "## The shell" not in _told(ShellPrompt(prompt=""))
    assert neutral_skills not in " ".join(
        render_system_prompt(shell=ShellPrompt(prompt="Mine.")).split()
    ), "skills are off"
