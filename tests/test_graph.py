from pathlib import Path

import pytest

from midkernel_runner.config import load_config
from midkernel_runner.graph import (
    GraphError,
    apply_graph_env,
    pipeline_raw_url,
    run_playbooks_graph,
    should_run_playbooks_graph,
)


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
    assert (tmp_path / "ws" / ".midkernel").is_dir()


def test_run_playbooks_graph_invokes_agentflow(tmp_path):
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


def test_run_playbooks_graph_prefers_ecs_in_task(tmp_path):
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
