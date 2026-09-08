from midkernel_runner.config import load_config
from midkernel_runner.kimi import (
    KimiError,
    build_prompt,
    export_kimi_openrouter_env,
    extract_text_from_kimi_stdout,
    kimi_model_aliases,
    openrouter_slug,
    render_kimi_openrouter_config,
    run_kimi,
    write_kimi_openrouter_config,
)
from midkernel_runner.secrets import HarnessSecrets


def test_openrouter_slug_strips_provider_prefix():
    assert openrouter_slug("openrouter/moonshotai/kimi-k3") == "moonshotai/kimi-k3"
    assert openrouter_slug("moonshotai/kimi-k3") == "moonshotai/kimi-k3"


def test_model_aliases_cover_agentflow_and_app():
    aliases = kimi_model_aliases("moonshotai/kimi-k3")
    assert "kimi-k3" in aliases
    assert "moonshotai/kimi-k3" in aliases
    assert "openrouter/moonshotai/kimi-k3" in aliases


def test_config_toml_is_openai_legacy_openrouter():
    text = render_kimi_openrouter_config("sk-or-v1-test", "moonshotai/kimi-k3")
    assert 'type = "openai_legacy"' in text
    assert 'base_url = "https://openrouter.ai/api/v1"' in text
    assert 'api_key = "sk-or-v1-test"' in text
    assert 'model = "moonshotai/kimi-k3"' in text
    assert '[models."moonshotai/kimi-k3"]' in text
    assert '[models."openrouter/moonshotai/kimi-k3"]' in text


def test_export_sets_openai_key_for_legacy_provider():
    env = export_kimi_openrouter_env({"AI_GATEWAY_API_KEY": "nope"}, "sk-or-v1-x")
    assert env["OPENAI_API_KEY"] == "sk-or-v1-x"
    assert env["OPENROUTER_API_KEY"] == "sk-or-v1-x"
    assert env["KIMI_API_KEY"] == "sk-or-v1-x"
    assert env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert "AI_GATEWAY_API_KEY" not in env


def test_write_config_mode_600(tmp_path):
    path = write_kimi_openrouter_config("sk-or-v1-test", "moonshotai/kimi-k3", home=tmp_path)
    assert path == tmp_path / ".kimi" / "config.toml"
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_prompt_requires_report_path():
    cfg = load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "midkernel",
            "GITHUB_NAME": "playbooks",
            "THREAT": "authz",
        }
    )
    prompt = build_prompt(cfg, "Perform a /security-review on this project")
    assert "Perform a /security-review on this project" in prompt
    assert cfg.report_path in prompt
    assert "Findings: N" in prompt
    assert "authz" in prompt
    assert "Kimi CLI" in prompt


def test_run_kimi_persists_file(tmp_path, monkeypatch):
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

    def fake_run(cmd, **kwargs):
        (outputs / "report.md").write_text(report_text, encoding="utf-8")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        assert cmd[0] == "kimi"
        assert "--print" in cmd
        assert "stream-json" in cmd
        assert "--yolo" in cmd
        return Result()

    text = run_kimi(
        cfg,
        HarnessSecrets("sk-or-v1-x", "ghs_x"),
        "Perform a /security-review on this project",
        run=fake_run,
        which=lambda _: "/opt/midkernel/kimi.bin",
    )
    assert "Findings: 0" in text
    assert (outputs / "report.md").is_file()


def test_run_kimi_extracts_stream_json(tmp_path, monkeypatch):
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
    body = (
        "# Security review of o/n\n\n"
        "Fixture used only to recover stream-json stdout.\n\nFindings: 0\n"
    )
    stream = "\n".join(
        [
            '{"type":"delta","text":"' + body.splitlines()[0] + '"}',
            '{"type":"final","text":"' + "\\n".join(body.splitlines()[1:]) + '"}',
        ]
    )

    class Result:
        returncode = 0
        stdout = stream
        stderr = ""

    text = run_kimi(
        cfg,
        HarnessSecrets("k", "t"),
        "Perform a /security-review on this project",
        run=lambda *a, **k: Result(),
        which=lambda _: "/opt/midkernel/kimi.bin",
    )
    assert "Findings: 0" in text


def test_run_kimi_refuses_stub(tmp_path, monkeypatch):
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

    import pytest

    with pytest.raises(KimiError, match="valid report"):
        run_kimi(
            cfg,
            HarnessSecrets("k", "t"),
            "Perform a /security-review on this project",
            run=lambda *a, **k: Result(),
            which=lambda _: "/opt/midkernel/kimi.bin",
        )


def test_extract_ignores_non_json_noise():
    assert extract_text_from_kimi_stdout("not json\n") == ""
