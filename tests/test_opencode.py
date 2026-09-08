from midkernel_runner.config import load_config
from midkernel_runner.opencode import build_prompt, run_opencode, write_opencode_auth
from midkernel_runner.secrets import HarnessSecrets


def test_prompt_includes_playbook_and_path():
    cfg = load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "midkernel",
            "GITHUB_NAME": "playbooks",
            "THREAT_PIN": "authz",
        }
    )
    prompt = build_prompt(cfg, "Perform a /security-review on this project")
    assert "Perform a /security-review on this project" in prompt
    assert cfg.report_path in prompt
    assert "Findings: N" in prompt
    assert "authz" in prompt
    assert "Do not write a stub" in prompt


def test_auth_file(tmp_path):
    path = write_opencode_auth(tmp_path, "sk-or-v1-test")
    assert path.read_text().find("sk-or-v1-test") > 0
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_run_persists_stdout_when_file_missing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "midkernel",
            "GITHUB_NAME": "playbooks",
            "WORKDIR": str(tmp_path),
            "OUTPUTS_DIR": str(outputs),
        }
    )
    report_text = (
        "# Security review of midkernel/playbooks\n\n"
        "Thin markdown registry. No services.\n\nFindings: 0\n"
    )

    class Result:
        returncode = 0
        stdout = report_text
        stderr = ""

    secrets = HarnessSecrets(openrouter_api_key="sk-or-v1-x", github_token="ghs_x")
    text = run_opencode(
        cfg,
        secrets,
        "Perform a /security-review on this project",
        run=lambda *a, **k: Result(),
        which=lambda _: "/usr/bin/opencode",
    )
    assert "Findings: 0" in text
    assert (outputs / "report.md").is_file()


def test_run_refuses_stub_stdout(tmp_path, monkeypatch):
    (tmp_path / "repo").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg = load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "o",
            "GITHUB_NAME": "n",
            "WORKDIR": str(tmp_path),
            "OUTPUTS_DIR": str(tmp_path / "outputs"),
        }
    )

    class Result:
        returncode = 0
        stdout = "This is a stub success report.\n" + ("ok " * 40)
        stderr = ""

    from midkernel_runner.opencode import OpenCodeError
    import pytest

    with pytest.raises(OpenCodeError, match="valid report"):
        run_opencode(
            cfg,
            HarnessSecrets("k", "t"),
            "Perform a /security-review on this project",
            run=lambda *a, **k: Result(),
            which=lambda _: "/usr/bin/opencode",
        )
    assert not (tmp_path / "outputs" / "report.md").exists()
