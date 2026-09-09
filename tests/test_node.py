import os

from midkernel_runner.config import load_optional_run_context
from midkernel_runner.node import prepare_node
from midkernel_runner.publish import candidate_report_paths, find_report_path, publish_report
from midkernel_runner.report import persist_report


def test_optional_context_absent_without_run_id():
    assert load_optional_run_context({"GITHUB_OWNER": "o", "GITHUB_NAME": "n"}) is None


def test_optional_context_present():
    ctx = load_optional_run_context(
        {"RUN_ID": "r1", "GITHUB_OWNER": "midkernel", "GITHUB_NAME": "playbooks"}
    )
    assert ctx["RUN_ID"] == "r1"


def test_prepare_node_writes_kimi_config_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KIMI_API_KEY", "sk-or-v1-from-agentflow")
    work = tmp_path / "workspace"
    prepared = prepare_node(
        environ={
            "KIMI_API_KEY": "sk-or-v1-from-agentflow",
            "HOME": str(tmp_path),
            "WORKDIR": str(work),
        }
    )
    assert prepared.kimi_config_path is not None
    text = prepared.kimi_config_path.read_text(encoding="utf-8")
    assert "openai_legacy" in text
    assert "openrouter.ai" in text
    assert prepared.kimi_config_path == work / ".midkernel" / "kimi" / "config.toml"
    assert (tmp_path / ".kimi" / "config.toml").is_file()


def test_prepare_node_clone_target_false_injects_secrets(tmp_path, monkeypatch):
    """Playbooks prepare needs GITHUB_TOKEN + OpenRouter without filling WORKDIR."""
    home = tmp_path / "home"
    work = tmp_path / "workspace"
    (work / ".midkernel" / "playbooks").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    def boom(*_a, **_k):
        raise AssertionError("must not clone target when clone_target=False")

    monkeypatch.setattr("midkernel_runner.node.clone_repository", boom)
    env = {
        "RUN_ID": "cmtuavpvs0003ib04bfyr7roc",
        "GITHUB_OWNER": "acme",
        "GITHUB_NAME": "target",
        "HOME": str(home),
        "WORKDIR": str(work),
        "MIDKERNEL_LOCAL": "1",
        "OPENROUTER_API_KEY": "sk-or-v1-graph",
        "GITHUB_TOKEN": "ghs_from_app",
        "MIDKERNEL_AGENTFLOW_TARGET": "local",
        "MIDKERNEL_CLONE_TARGET": "0",
    }
    prepared = prepare_node(environ=env, clone_target=False)
    assert prepared.cloned is False
    assert prepared.secrets is not None
    assert prepared.secrets.openrouter_api_key == "sk-or-v1-graph"
    assert prepared.secrets.github_token == "ghs_from_app"
    assert prepared.kimi_config_path is not None
    assert "openrouter.ai" in prepared.kimi_config_path.read_text(encoding="utf-8")
    assert os.environ["GITHUB_TOKEN"] == "ghs_from_app"
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-v1-graph"
    assert os.environ["OPENAI_API_KEY"] == "sk-or-v1-graph"
    assert os.environ["KIMI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert os.environ["KIMI_MODEL_NAME"] == "moonshotai/kimi-k3"
    assert os.environ["KIMI_SHARE_DIR"] == str(work / ".midkernel" / "kimi")
    assert prepared.kimi_config_path == work / ".midkernel" / "kimi" / "config.toml"


def test_prepare_node_clone_target_false_allows_nonempty_workspace(tmp_path, monkeypatch):
    """Fargate /workspace may already have .midkernel from the image (#6 / GOAL)."""
    home = tmp_path / "home"
    work = tmp_path / "workspace"
    (work / ".midkernel" / "playbooks").mkdir(parents=True)
    (work / "preexisting.txt").write_text("from image\n", encoding="utf-8")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    def boom(*_a, **_k):
        raise AssertionError("must not clone into a non-empty workdir on the graph path")

    monkeypatch.setattr("midkernel_runner.node.clone_repository", boom)
    env = {
        "RUN_ID": "cmtue7rv90003l104ysmh21eu",
        "GITHUB_OWNER": "acme",
        "GITHUB_NAME": "target",
        "HOME": str(home),
        "WORKDIR": str(work),
        "MIDKERNEL_LOCAL": "1",
        "OPENROUTER_API_KEY": "sk-or-v1-graph",
        "GITHUB_TOKEN": "ghs_from_app",
    }
    prepared = prepare_node(environ=env, clone_target=False)
    assert prepared.cloned is False
    assert prepared.secrets is not None
    assert prepared.secrets.openrouter_api_key == "sk-or-v1-graph"
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-v1-graph"
    assert (work / "preexisting.txt").is_file()


def test_prepare_node_honors_clone_target_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work = tmp_path / "workspace"
    (work / ".midkernel").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setattr(
        "midkernel_runner.node.clone_repository",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("env said skip clone")),
    )
    env = {
        "RUN_ID": "r-graph",
        "GITHUB_OWNER": "acme",
        "GITHUB_NAME": "target",
        "HOME": str(home),
        "WORKDIR": str(work),
        "MIDKERNEL_LOCAL": "1",
        "OPENROUTER_API_KEY": "sk-or-v1-x",
        "GITHUB_TOKEN": "ghs_x",
        "MIDKERNEL_CLONE_TARGET": "0",
    }
    prepared = prepare_node(environ=env)
    assert prepared.cloned is False
    assert prepared.secrets is not None
    assert prepared.secrets.github_token == "ghs_x"


def test_publish_skips_without_midkernel_env(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_OWNER", raising=False)
    assert publish_report(require=False) is None


def test_publish_uploads_found_report(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    persist_report(
        outputs / "report.md",
        "# Security review of o/n\n\n"
        "Fixture tree used only to exercise publish.\n\nFindings: 0\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    # Call through the module with a patched upload.
    from midkernel_runner import publish as publish_mod

    monkeypatch.setattr(
        publish_mod,
        "upload_report",
        lambda config, path, **kw: f"s3://{config.artifacts_bucket}/{config.artifact_key}",
    )
    env = {
        "RUN_ID": "run42",
        "GITHUB_OWNER": "o",
        "GITHUB_NAME": "n",
        "OUTPUTS_DIR": str(outputs),
        "WORKDIR": str(tmp_path),
    }
    monkeypatch.setenv("RUN_ID", "run42")
    monkeypatch.setenv("GITHUB_OWNER", "o")
    monkeypatch.setenv("GITHUB_NAME", "n")
    monkeypatch.setenv("OUTPUTS_DIR", str(outputs))
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    uri = publish_mod.publish_report(require=True)
    assert uri == "s3://midkernel-dev-artifacts/runs/run42/report.md"


def test_find_report_prefers_outputs(tmp_path):
    outputs = tmp_path / "out"
    outputs.mkdir()
    (outputs / "report.md").write_text("# ok\n\nFindings: 0\n" + ("word " * 20), encoding="utf-8")
    found = find_report_path(str(outputs), str(tmp_path))
    assert found == outputs / "report.md"
    assert candidate_report_paths(str(outputs), str(tmp_path))[0] == outputs / "report.md"
