"""Home policy retains explicit child and managed environment compatibility."""
import os

import pytest


@pytest.mark.parametrize("mode,expected_profile", [("real", False), ("profile", True), ("auto", True)])
def test_child_modes_override_owner(tmp_path, monkeypatch, mode, expected_profile):
    from hermes_constants import get_subprocess_home
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope

    real = tmp_path / "real"
    profile = tmp_path / "target"
    real.mkdir()
    (profile / "home").mkdir(parents=True)
    monkeypatch.setattr("hermes_constants.is_container", lambda: True)
    child = {"HERMES_HOME": str(profile), "HOME": str(profile / "home"),
             "HERMES_REAL_HOME": str(real), "TERMINAL_HOME_MODE": mode}
    token = set_terminal_scope({"TERMINAL_HOME_MODE": "profile" if mode == "real" else "real"})
    try:
        assert get_subprocess_home(child) == str(profile / "home" if expected_profile else real)
    finally:
        reset_terminal_scope(token)


def test_no_scope_empty_mode_keeps_process_fallback(tmp_path, monkeypatch):
    from hermes_constants import get_subprocess_home

    profile = tmp_path / "profile"
    (profile / "home").mkdir(parents=True)
    monkeypatch.setenv("TERMINAL_HOME_MODE", "profile")
    assert get_subprocess_home({"HERMES_HOME": str(profile), "TERMINAL_HOME_MODE": ""}) == str(profile / "home")


def test_explicit_child_mode_beats_bound_owner(tmp_path, monkeypatch):
    from hermes_constants import get_subprocess_home
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope

    real = tmp_path / "real"
    profile = tmp_path / "child"
    real.mkdir()
    (profile / "home").mkdir(parents=True)
    monkeypatch.setattr("hermes_constants.is_container", lambda: True)
    child = {"HERMES_HOME": str(profile), "HOME": str(profile / "home"),
             "HERMES_REAL_HOME": str(real), "TERMINAL_HOME_MODE": "real"}
    before = dict(os.environ)
    token = set_terminal_scope({"TERMINAL_HOME_MODE": "profile"})
    try:
        assert get_subprocess_home(child) == str(real)
    finally:
        reset_terminal_scope(token)
    assert dict(os.environ) == before


def test_managed_env_home_mode_reaches_bound_owner(tmp_path, monkeypatch):
    from hermes_cli import managed_scope
    from hermes_constants import get_subprocess_home
    from tools.terminal_scope import build_profile_terminal_scope, reset_terminal_scope, set_terminal_scope

    real = tmp_path / "real"
    profile = tmp_path / "profile"
    managed = tmp_path / "managed"
    real.mkdir()
    (profile / "home").mkdir(parents=True)
    managed.mkdir()
    (managed / ".env").write_text("TERMINAL_HOME_MODE=real\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HOME", str(real))
    monkeypatch.setenv("HERMES_REAL_HOME", str(real))
    monkeypatch.setenv("TERMINAL_HOME_MODE", "profile")
    monkeypatch.setattr("hermes_constants.is_container", lambda: True)
    managed_scope.invalidate_managed_cache()
    before = dict(os.environ)
    token = set_terminal_scope(build_profile_terminal_scope(profile))
    try:
        assert (get_subprocess_home() or str(real)) == str(real)
    finally:
        reset_terminal_scope(token)
        managed_scope.invalidate_managed_cache()
    assert dict(os.environ) == before


@pytest.mark.parametrize("scrub", [True, False])
@pytest.mark.parametrize("source", ["base", "extra"])
@pytest.mark.parametrize("mode", ["real", "profile"])
def test_factory_explicit_child_home_policy(tmp_path, monkeypatch, scrub, source, mode):
    from tools.environments.local import build_subprocess_env
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope

    real = tmp_path / "real"
    target = tmp_path / "target"
    real.mkdir()
    (target / "home").mkdir(parents=True)
    monkeypatch.setattr("hermes_constants.is_container", lambda: True)
    # Equal to ambient on purpose: equality cannot establish provenance.
    monkeypatch.setenv("TERMINAL_HOME_MODE", mode)
    child = {"HERMES_HOME": str(target), "HOME": str(target / "home"),
             "HERMES_REAL_HOME": str(real), "TERMINAL_HOME_MODE": mode}
    token = set_terminal_scope({"TERMINAL_HOME_MODE": "profile" if mode == "real" else "real"})
    try:
        result = build_subprocess_env(**{source: child}, scrub_secrets=scrub)
        assert result["HERMES_HOME"] == str(target)
        assert result["HOME"] == str(real if mode == "real" else target / "home")
    finally:
        reset_terminal_scope(token)


def test_factory_target_home_does_not_inherit_owner_override(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.environments.local import build_subprocess_env
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope

    owner, target = tmp_path / "owner", tmp_path / "target"
    (owner / "home").mkdir(parents=True)
    (target / "home").mkdir(parents=True)
    monkeypatch.setenv("TERMINAL_HOME_MODE", "real")
    home_token = set_hermes_home_override(owner)
    scope_token = set_terminal_scope({"TERMINAL_HOME_MODE": "real"})
    try:
        result = build_subprocess_env(extra={"HERMES_HOME": str(target), "TERMINAL_HOME_MODE": "profile"})
        assert result["HERMES_HOME"] == str(target)
        assert result["HOME"] == str(target / "home")
        # Owner-only builds continue to use the owner's frozen policy and identity.
        assert build_subprocess_env()["HERMES_HOME"] == str(owner)
    finally:
        reset_terminal_scope(scope_token)
        reset_hermes_home_override(home_token)


def test_factory_snapshot_does_not_authorize_launch_mode(tmp_path, monkeypatch):
    from tools.environments.local import build_subprocess_env
    from tools.terminal_scope import reset_terminal_scope, set_terminal_scope

    profile = tmp_path / "profile"
    (profile / "home").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("TERMINAL_HOME_MODE", "real")
    token = set_terminal_scope({"TERMINAL_HOME_MODE": "profile"})
    try:
        assert build_subprocess_env(base=dict(os.environ), base_is_snapshot=True)["HOME"] == str(profile / "home")
        assert build_subprocess_env()["HOME"] == str(profile / "home")
    finally:
        reset_terminal_scope(token)


def test_no_scope_unscrubbed_extra_remains_last(tmp_path):
    from tools.environments.local import build_subprocess_env

    profile = tmp_path / "profile"
    (profile / "home").mkdir(parents=True)
    child = {"HERMES_HOME": str(profile), "HOME": str(tmp_path / "custom"),
             "TERMINAL_HOME_MODE": "profile"}
    assert build_subprocess_env(extra=child, scrub_secrets=False)["HOME"] == str(tmp_path / "custom")
