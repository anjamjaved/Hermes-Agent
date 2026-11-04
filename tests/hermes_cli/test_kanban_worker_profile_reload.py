"""Routed worker startup reloads its own terminal policy, not the dispatcher's."""
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("worker_mode,expected_mode", [("real", "real"), (None, "auto")])
def test_routed_spawn_reloads_profile_policy_without_changing_parent(
        tmp_path, monkeypatch, worker_mode, expected_mode):
    from hermes_cli import config, env_loader, kanban_db as kb, kanban_db_dispatch as dispatch
    from tools import process_registry
    import hermes_constants

    root = tmp_path / ".hermes"
    worker = root / "profiles" / "worker"
    worker.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (root / ".env").write_text("PROFILE_MARKER=default-dotenv\n", encoding="utf-8")
    (worker / ".env").write_text("PROFILE_MARKER=worker-dotenv\n", encoding="utf-8")
    (root / "config.yaml").write_text(
        'terminal:\n  backend: local\n  home_mode: profile\n  docker_image: default-image\n'
        '  docker_forward_env: [DEFAULT_VAR]\n', encoding="utf-8")
    (worker / "config.yaml").write_text(
        'terminal:\n  backend: docker\n  docker_image: worker-image\n'
        '  docker_forward_env: [WORKER_VAR]\n'
        + (f'  home_mode: {worker_mode}\n' if worker_mode else ''), encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, "_PINNED_PROCESS_HERMES_HOME", str(root))
    for key, value in {
        "HOME": str(tmp_path), "HERMES_HOME": str(root), "HERMES_KANBAN_HOME": str(root),
        "HERMES_RUNTIME_DIR": str(tmp_path / "runtime"), "TERMINAL_HOME_MODE": "profile",
        "TERMINAL_ENV": "local", "TERMINAL_DOCKER_IMAGE": "default-image",
        "TERMINAL_DOCKER_FORWARD_ENV": '["DEFAULT_VAR"]', "TERMINAL_UNRELATED": "keep",
    }.items():
        monkeypatch.setenv(key, value)

    captured = []
    def popen(argv, **kwargs):
        captured.append(dict(kwargs["env"]))
        return SimpleNamespace(pid=424242)

    monkeypatch.setattr(dispatch, "_restart_safe_worker_argv", lambda task, argv: argv)
    monkeypatch.setattr(process_registry, "systemd_user_bus_env", lambda env: env)
    monkeypatch.setattr(dispatch, "_open_worker_log", lambda task, board: open(os.devnull, "wb"))
    monkeypatch.setattr(subprocess, "Popen", popen)

    for assignee in ("default", "worker", "default"):
        task = kb.Task(id="t", title="t", body=None, assignee=assignee, status="claimed",
                       priority=0, created_by=None, created_at=0, started_at=None,
                       completed_at=None, workspace_kind="dir", workspace_path=None,
                       claim_lock=None, claim_expires=None, tenant=None)
        dispatch._default_spawn(task, str(workspace))

    assert [env["HERMES_HOME"] for env in captured] == [str(root), str(worker), str(root)]
    assert captured[0]["TERMINAL_HOME_MODE"] == captured[2]["TERMINAL_HOME_MODE"] == "profile"
    assert "TERMINAL_HOME_MODE" not in captured[1]
    assert all(env["TERMINAL_UNRELATED"] == "keep" for env in captured)
    assert os.environ["TERMINAL_HOME_MODE"] == "profile"

    # Model each child's startup in an isolated environment, using the actual dotenv and YAML bridge.
    for env, home, mode, backend, image, forwarded, marker in (
        (captured[0], root, "profile", "local", "default-image", ["DEFAULT_VAR"], "default-dotenv"),
        (captured[1], worker, expected_mode, "docker", "worker-image", ["WORKER_VAR"], "worker-dotenv"),
        (captured[2], root, "profile", "local", "default-image", ["DEFAULT_VAR"], "default-dotenv"),
    ):
        with patch.dict(os.environ, env, clear=True):
            assert home / ".env" in env_loader.load_hermes_dotenv(
                hermes_home=home, load_external_secrets=False)
            config.apply_terminal_config_to_env()
            assert os.environ["PROFILE_MARKER"] == marker
            assert os.environ["TERMINAL_HOME_MODE"] == mode
            assert os.environ["TERMINAL_ENV"] == backend
            assert os.environ["TERMINAL_DOCKER_IMAGE"] == image
            assert json.loads(os.environ["TERMINAL_DOCKER_FORWARD_ENV"]) == forwarded
    assert os.environ["TERMINAL_HOME_MODE"] == "profile"
