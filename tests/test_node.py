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
    prepared = prepare_node(environ=dict(**{"KIMI_API_KEY": "sk-or-v1-from-agentflow", "HOME": str(tmp_path)}))
    assert prepared.kimi_config_path is not None
    text = prepared.kimi_config_path.read_text(encoding="utf-8")
    assert "openai_legacy" in text
    assert "openrouter.ai" in text


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
