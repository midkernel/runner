import os
import subprocess
from pathlib import Path

import pytest

from midkernel_runner.config import load_config
from midkernel_runner.graph import (
    GraphError,
    apply_graph_env,
    prepare_playbooks_kimi_executable,
    pipeline_raw_url,
    run_playbooks_graph,
    should_run_playbooks_graph,
)


_GRAPH_ENV_KEYS = (
    "WORKDIR",
    "OUTPUTS_DIR",
    "PATH",
    "MIDKERNEL_NODE_IO",
    "MIDKERNEL_AGENTFLOW_TARGET",
    "MIDKERNEL_KIMI_BIN",
    "MIDKERNEL_CLONE_TARGET",
    "MIDKERNEL_REQUIRE_REPORT",
    "KIMI_SHARE_DIR",
    "KIMI_BASE_URL",
    "KIMI_MODEL_NAME",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "KIMI_API_KEY",
)


def _isolate_os_graph_env(monkeypatch):
    """run_playbooks_graph writes apply_graph_env onto os.environ."""
    for key in _GRAPH_ENV_KEYS:
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.delenv(key, raising=False)


def _cfg(**overrides):
    env = {
        "RUN_ID": "cmtu8jtu00003l1043oi0at41",
        "GITHUB_OWNER": "acme",
        "GITHUB_NAME": "target",
        "PLAYBOOK": "goal-security-review",
        "WORKDIR": "/tmp/midkernel-graph-test-work",
        "OUTPUTS_DIR": "/tmp/midkernel-graph-test-out",
    }
    env.update(overrides)
    return load_config(env)


def test_pipeline_url_uses_playbooks_contract():
    assert pipeline_raw_url(_cfg()) == (
        "https://raw.githubusercontent.com/midkernel/playbooks/main/"
        "pipelines/goal-security-review.py"
    )


def test_should_run_graph_when_pipeline_exists():
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 200) is True
    assert "goal-security-review.py" in pipeline_raw_url(_cfg())


def test_should_not_run_graph_when_missing():
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 404) is False


def test_should_run_graph_when_probe_is_not_a_clean_404():
    """raw.githubusercontent.com HEAD/GET 403 must not fall back to md+kimi."""
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 403) is True
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 405) is True
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 500) is True


def test_force_md_kimi_wins(monkeypatch):
    monkeypatch.setenv("MIDKERNEL_FORCE_MD_KIMI", "1")
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 200) is False


def test_force_graph_wins(monkeypatch):
    monkeypatch.setenv("MIDKERNEL_FORCE_GRAPH", "1")
    assert should_run_playbooks_graph(_cfg(), fetch_status=lambda url: 404) is True


def test_apply_graph_env_exports_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("MIDKERNEL_NODE_IO", raising=False)
    monkeypatch.delenv("MIDKERNEL_AGENTFLOW_TARGET", raising=False)
    cfg = _cfg(WORKDIR=str(tmp_path / "ws"), OUTPUTS_DIR=str(tmp_path / "out"))
    env = apply_graph_env(cfg, environ={})
    assert env["WORKDIR"] == str(tmp_path / "ws")
    assert env["MIDKERNEL_NODE_IO"] == "1"
    assert env["MIDKERNEL_AGENTFLOW_TARGET"] == "local"
    assert env["MIDKERNEL_KIMI_BIN"] == "/opt/midkernel/kimi.bin"
    assert env["MIDKERNEL_CLONE_TARGET"] == "0"
    assert env["MIDKERNEL_REQUIRE_REPORT"] == "0"
    assert (tmp_path / "ws" / ".midkernel").is_dir()
    shim = tmp_path / "ws" / ".midkernel" / "bin" / "kimi"
    assert shim.is_file()
    assert oct(shim.stat().st_mode)[-3:] == "755"
    text = shim.read_text(encoding="utf-8")
    assert "/opt/midkernel/kimi.bin" in text
    assert "/usr/local/bin/kimi" not in text or "Never fall through" in text
    assert env["PATH"].split(os.pathsep)[0] == str(shim.parent)
    assert env["KIMI_SHARE_DIR"] == str(tmp_path / "ws" / ".midkernel" / "kimi")
    assert "OPENROUTER_API_KEY" not in env


def test_apply_graph_env_exports_openrouter_for_kimi_bin(tmp_path):
    """In-task _node_io execs kimi.bin with BASH_ENV=/dev/null — LLM env must be on the dict."""
    cfg = _cfg(WORKDIR=str(tmp_path / "ws"), OUTPUTS_DIR=str(tmp_path / "out"))
    env = apply_graph_env(
        cfg,
        environ={
            "OPENROUTER_API_KEY": "sk-or-v1-graph",
            "OPENROUTER_MODEL": "moonshotai/kimi-k3",
            "PATH": "/usr/bin",
        },
    )
    share = tmp_path / "ws" / ".midkernel" / "kimi"
    assert env["KIMI_SHARE_DIR"] == str(share)
    assert env["OPENAI_API_KEY"] == "sk-or-v1-graph"
    assert env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["KIMI_API_KEY"] == "sk-or-v1-graph"
    assert env["KIMI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["KIMI_MODEL_NAME"] == "moonshotai/kimi-k3"
    config_path = share / "config.toml"
    assert config_path.is_file()
    text = config_path.read_text(encoding="utf-8")
    assert "openai_legacy" in text
    assert "midkernel" in text
    assert "moonshotai/kimi-k3" in text


def test_apply_graph_env_path_shim_beats_wrapper(tmp_path):
    """agentflow `kimi` on PATH must not resolve to the report wrapper."""
    cfg = _cfg(WORKDIR=str(tmp_path / "ws"), OUTPUTS_DIR=str(tmp_path / "out"))
    wrapper_dir = tmp_path / "image-bin"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "kimi"
    wrapper.write_text("#!/bin/sh\necho WRAPPER\nexit 1\n")
    wrapper.chmod(0o755)
    env = apply_graph_env(
        cfg,
        environ={"PATH": str(wrapper_dir)},
    )
    first = Path(env["PATH"].split(os.pathsep)[0]) / "kimi"
    assert first.resolve() != wrapper.resolve()
    assert first.is_file()
    assert "MIDKERNEL_KIMI_BIN" in first.read_text(encoding="utf-8")


def test_run_playbooks_graph_invokes_agentflow(tmp_path, monkeypatch):
    _isolate_os_graph_env(monkeypatch)
    playbooks = tmp_path / "playbooks"
    (playbooks / "pipelines").mkdir(parents=True)
    (playbooks / "pipelines" / "goal-security-review.py").write_text("print('ok')\n")
    (playbooks / "scripts").mkdir()
    # No executable ecs-in-task.sh → fall back to agentflow run
    cfg = _cfg(WORKDIR=str(tmp_path / "ws"), OUTPUTS_DIR=str(tmp_path / "out"))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        seen["env"] = kwargs.get("env") or {}

        class Result:
            returncode = 0

        return Result()

    code = run_playbooks_graph(
        cfg,
        run=fake_run,
        which=lambda name: "/usr/bin/agentflow" if name == "agentflow" else None,
        clone=lambda _c, _d: playbooks,
    )
    assert code == 0
    assert seen["cmd"][0] == "/usr/bin/agentflow"
    assert seen["cmd"][1] == "run"
    assert seen["cmd"][2].endswith("goal-security-review.py")
    assert seen["cwd"] == str(playbooks)
    assert seen["env"].get("MIDKERNEL_KIMI_BIN") == "/opt/midkernel/kimi.bin"
    assert seen["env"].get("MIDKERNEL_CLONE_TARGET") == "0"
    assert seen["env"].get("MIDKERNEL_REQUIRE_REPORT") == "0"
    assert seen["env"].get("MIDKERNEL_AGENTFLOW_TARGET") == "local"
    shim_dir = str(tmp_path / "ws" / ".midkernel" / "bin")
    assert seen["env"]["PATH"].split(os.pathsep)[0] == shim_dir
    assert seen["env"].get("KIMI_SHARE_DIR") == str(tmp_path / "ws" / ".midkernel" / "kimi")


def test_run_playbooks_graph_prefers_ecs_in_task(tmp_path, monkeypatch):
    _isolate_os_graph_env(monkeypatch)
    playbooks = tmp_path / "playbooks"
    (playbooks / "pipelines").mkdir(parents=True)
    (playbooks / "pipelines" / "goal-security-review.py").write_text("print('ok')\n")
    script = playbooks / "scripts"
    script.mkdir()
    helper = script / "ecs-in-task.sh"
    helper.write_text("#!/bin/bash\nexit 0\n")
    helper.chmod(0o755)
    cfg = _cfg(WORKDIR=str(tmp_path / "ws"), OUTPUTS_DIR=str(tmp_path / "out"))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd

        class Result:
            returncode = 0

        return Result()

    run_playbooks_graph(
        cfg,
        run=fake_run,
        which=lambda name: "/usr/bin/agentflow",
        clone=lambda _c, _d: playbooks,
    )
    assert seen["cmd"] == ["bash", str(helper)]


def test_run_playbooks_graph_requires_agentflow():
    cfg = _cfg()
    with pytest.raises(GraphError, match="agentflow"):
        run_playbooks_graph(cfg, which=lambda _name: None, clone=lambda _c, _d: Path("/x"))


_NODE_IO_MAIN = '''#!/usr/bin/env python3
import os
import sys

def real_kimi_bin():
    return os.environ.get("MIDKERNEL_KIMI_BIN", "/opt/midkernel/kimi.bin")

def wrap_kimi(argv):
    raise SystemExit("wrap_kimi must not run for --version")

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"start", "finish", "init", "spawn-hunters"}:
        return 0
    return wrap_kimi(argv)

if __name__ == "__main__":
    raise SystemExit(main())
'''


def test_prepare_playbooks_kimi_executable_answers_version(tmp_path):
    """agentflow kimi_ready execs `_node_io.py --version` in a local shell."""
    root = tmp_path / "playbooks"
    (root / "pipelines").mkdir(parents=True)
    helper = root / "pipelines" / "_node_io.py"
    helper.write_text(_NODE_IO_MAIN, encoding="utf-8")
    helper.chmod(0o644)
    real = tmp_path / "kimi.bin"
    real.write_text("#!/bin/sh\necho kimi-cli-1.49.0\nexit 0\n", encoding="utf-8")
    real.chmod(0o755)

    patched = prepare_playbooks_kimi_executable(root)
    assert patched == helper
    assert os.access(helper, os.X_OK)
    assert 'argv[0] in {"--version"' in helper.read_text(encoding="utf-8")
    # Idempotent.
    prepare_playbooks_kimi_executable(root)
    assert helper.read_text(encoding="utf-8").count("os.execvp") == 1

    result = subprocess.run(
        [str(helper), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MIDKERNEL_KIMI_BIN": str(real)},
    )
    assert result.returncode == 0, result.stderr
    assert "kimi-cli-1.49.0" in result.stdout
    assert "wrap_kimi must not run" not in result.stderr


def test_prepare_playbooks_kimi_executable_missing_is_noop(tmp_path):
    assert prepare_playbooks_kimi_executable(tmp_path / "empty") is None
