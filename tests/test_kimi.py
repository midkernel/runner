import os

from midkernel_runner.config import load_config
from midkernel_runner.kimi import (
    KimiError,
    OPENROUTER_BASE_URL,
    build_prompt,
    export_kimi_openrouter_env,
    extract_text_from_kimi_stdout,
    graph_kimi_share_dir,
    kimi_model_aliases,
    openrouter_slug,
    render_kimi_openrouter_config,
    run_kimi,
    write_kimi_openrouter_config,
)
from midkernel_runner.openrouter_tokens import DEFAULT_MAX_TOKENS, UNSAFE_OPENROUTER_DEFAULT
from midkernel_runner.secrets import HarnessSecrets


def test_openrouter_slug_strips_provider_prefix():
    assert openrouter_slug("openrouter/moonshotai/kimi-k3") == "moonshotai/kimi-k3"
    assert openrouter_slug("moonshotai/kimi-k3") == "moonshotai/kimi-k3"
    assert openrouter_slug("") == "google/gemini-3.8-flash"


def test_model_aliases_cover_agentflow_and_app():
    aliases = kimi_model_aliases("moonshotai/kimi-k3")
    assert "kimi-k3" in aliases
    assert "midkernel" in aliases
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
    assert "[models.midkernel]" in text
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in text
    assert f"max_tokens = {UNSAFE_OPENROUTER_DEFAULT}" not in text
    assert text.count("max_tokens =") == text.count("max_context_size =")


def test_export_sets_openai_key_for_legacy_provider():
    env = export_kimi_openrouter_env({"AI_GATEWAY_API_KEY": "nope"}, "sk-or-v1-x")
    assert env["OPENAI_API_KEY"] == "sk-or-v1-x"
    assert env["OPENROUTER_API_KEY"] == "sk-or-v1-x"
    assert env["KIMI_API_KEY"] == "sk-or-v1-x"
    assert env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["KIMI_BASE_URL"] == OPENROUTER_BASE_URL
    assert env["KIMI_MODEL_NAME"] == "google/gemini-3.8-flash"
    assert env["KIMI_MODEL_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["KIMI_MODEL_MAX_COMPLETION_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["KIMI_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["OPENROUTER_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["MIDKERNEL_OPENROUTER_429_RETRIES"] == "8"
    assert env["MIDKERNEL_OPENROUTER_RPM"] == "12"
    assert "CONCURRENCY" not in env
    assert "AI_GATEWAY_API_KEY" not in env


def test_export_honors_openrouter_model_env():
    env = export_kimi_openrouter_env(
        {"OPENROUTER_MODEL": "moonshotai/kimi-k3"},
        "sk-or-v1-x",
    )
    assert env["KIMI_MODEL_NAME"] == "moonshotai/kimi-k3"
    assert env["OPENROUTER_MODEL"] == "moonshotai/kimi-k3"


def test_export_sets_dummy_kimi_fallback_and_share_dir(tmp_path):
    """kimi-cli 1.49: unmatched --model → type=kimi needs KIMI_BASE_URL + KIMI_MODEL_NAME."""
    share = graph_kimi_share_dir(tmp_path)
    env = export_kimi_openrouter_env(
        {},
        "sk-or-v1-x",
        model="openrouter/moonshotai/kimi-k3",
        share_dir=share,
    )
    assert env["KIMI_BASE_URL"] == OPENROUTER_BASE_URL
    assert env["KIMI_MODEL_NAME"] == "moonshotai/kimi-k3"
    assert env["KIMI_SHARE_DIR"] == str(share)
    assert env["OPENROUTER_MODEL"] == "moonshotai/kimi-k3"
    assert env["KIMI_MODEL_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["KIMI_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["PYTHONPATH"].split(os.pathsep)[0].endswith("py_path")


def test_export_does_not_clobber_wrap_kimi_injection_proxy():
    proxy = "http://127.0.0.1:54321/api/v1"
    env = export_kimi_openrouter_env(
        {"OPENAI_BASE_URL": proxy, "AI_GATEWAY_API_KEY": "nope"},
        "sk-or-v1-x",
    )
    assert env["OPENAI_BASE_URL"] == proxy
    assert env["KIMI_BASE_URL"] == proxy
    assert "openrouter.ai" not in env["OPENAI_BASE_URL"]


def test_config_toml_preserves_localhost_proxy_base_url():
    proxy = "http://127.0.0.1:4242/api/v1"
    text = render_kimi_openrouter_config(
        "sk-or-v1-test",
        "moonshotai/kimi-k3",
        environ={"OPENAI_BASE_URL": proxy},
    )
    assert f'base_url = "{proxy}"' in text
    assert "openrouter.ai" not in text
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in text


def test_config_toml_honors_safe_override_and_rejects_131072():
    text = render_kimi_openrouter_config(
        "sk-or-v1-test",
        "moonshotai/kimi-k3",
        environ={"OPENROUTER_MAX_TOKENS": "65536"},
    )
    assert "max_tokens = 65536" in text
    unsafe = render_kimi_openrouter_config(
        "sk-or-v1-test",
        "moonshotai/kimi-k3",
        max_tokens=UNSAFE_OPENROUTER_DEFAULT,
    )
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in unsafe
    assert f"max_tokens = {UNSAFE_OPENROUTER_DEFAULT}" not in unsafe


def test_write_config_mode_600(tmp_path):
    path = write_kimi_openrouter_config("sk-or-v1-test", "moonshotai/kimi-k3", home=tmp_path)
    assert path == tmp_path / ".kimi" / "config.toml"
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in path.read_text(encoding="utf-8")
    assert (tmp_path / ".kimi" / "py_path" / "sitecustomize.py").is_file()


def test_write_config_share_dir_beats_home(tmp_path):
    share = graph_kimi_share_dir(tmp_path / "ws")
    path = write_kimi_openrouter_config(
        "sk-or-v1-test",
        "moonshotai/kimi-k3",
        home=tmp_path / "home",
        share_dir=share,
    )
    assert path == share / "config.toml"
    text = path.read_text(encoding="utf-8")
    assert "midkernel" in text
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in text
    assert (share / "py_path" / "sitecustomize.py").is_file()


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
