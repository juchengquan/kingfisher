from __future__ import annotations

from kingfisher.adapters.backend import build_backend, shell_env


def test_shell_env_carries_no_credentials(cfg):
    """The allowlist is the security story: the shell can run tools, not read keys."""
    env = shell_env(cfg)
    assert cfg.api_key not in env.values()
    assert not any("KEY" in name or "TOKEN" in name or "SECRET" in name for name in env)


def test_shell_env_supplies_a_usable_toolchain(cfg):
    """LocalShellBackend defaults to an EMPTY env -- without PATH nothing resolves."""
    env = shell_env(cfg)
    assert env["PATH"]
    assert "/usr/bin" in env["PATH"]


def test_home_points_at_the_workspace_not_the_real_home(cfg):
    """So ~/.aws, ~/.ssh and ~/.config are not where the agent's tooling looks."""
    assert shell_env(cfg)["HOME"] == str(cfg.workspace)


def test_backend_is_rooted_at_the_workspace(cfg):
    """The default backend owns the workspace root; virtual paths anchor to it."""
    backend = build_backend(cfg)
    assert str(cfg.workspace.resolve()) == str(backend.default.cwd)


def test_data_is_routed_so_the_deny_rule_is_legal(cfg):
    """deepagents refuses permissions on an execution backend unless every rule
    path is scoped to a route -- routing /data/ is what makes Q21 possible."""
    backend = build_backend(cfg)
    assert "/data/" in backend.routes
    assert str((cfg.workspace / "data").resolve()) == str(backend.routes["/data/"].cwd)
