from midkernel_runner.kimi_graph_bin import (
    apply_front_max_tokens_env,
    drop_config_flags,
    model_from_argv,
    prepare_config_file,
    resolve_real_bin,
    rewrite_kimi_argv,
)
from midkernel_runner.openrouter_tokens import (
    DEFAULT_MAX_TOKENS,
    UNSAFE_OPENROUTER_DEFAULT,
)


PLAYBOOKS_CONFIG = """
default_model = "midkernel"
default_yolo = true

[providers.openrouter]
type = "openai_legacy"
base_url = "https://openrouter.ai/api/v1"
api_key = "OVERRIDE_VIA_ENV"

[models.midkernel]
provider = "openrouter"
model = "moonshotai/kimi-k3"
max_context_size = 262144
""".strip()

# Playbooks prepare still writes context only. Runner must add max_tokens
# so OpenRouter does not reserve the model max (131072).


def test_model_from_argv_reads_agentflow_flag():
    assert model_from_argv(["--print", "--model", "moonshotai/kimi-k3", "-p", "hi"]) == (
        "moonshotai/kimi-k3"
    )
    assert model_from_argv(["--model=anthropic/claude-sonnet-4.5"]) == "anthropic/claude-sonnet-4.5"
    assert model_from_argv(["--print", "-p", "hi"]) is None


def test_drop_config_flags_removes_playbooks_toml():
    argv = [
        "--print",
        "--model",
        "moonshotai/kimi-k3",
        "--config",
        PLAYBOOKS_CONFIG,
        "-p",
        "write THREAT_MODEL.md",
    ]
    assert drop_config_flags(argv) == [
        "--print",
        "--model",
        "moonshotai/kimi-k3",
        "-p",
        "write THREAT_MODEL.md",
    ]


def test_rewrite_injects_config_file_for_threat_model():
    """GOAL threat-model: agentflow --model slug + playbooks --config midkernel-only."""
    rewritten = rewrite_kimi_argv(
        [
            "--print",
            "--output-format",
            "stream-json",
            "--yolo",
            "-p",
            "write THREAT_MODEL.md",
            "--model",
            "moonshotai/kimi-k3",
            "--config",
            PLAYBOOKS_CONFIG,
        ],
        config_file="/workspace/.midkernel/kimi/config.toml",
    )
    assert rewritten[:2] == ["--config-file", "/workspace/.midkernel/kimi/config.toml"]
    assert "--config" not in rewritten
    assert PLAYBOOKS_CONFIG not in rewritten
    assert rewritten[rewritten.index("--model") + 1] == "moonshotai/kimi-k3"


def test_rewrite_leaves_version_probe_alone():
    assert rewrite_kimi_argv(["--version"], config_file="/x/config.toml") == ["--version"]


def test_resolve_real_bin_avoids_self():
    assert resolve_real_bin({"MIDKERNEL_GRAPH_KIMI_BIN": "/opt/midkernel/kimi.bin"}) == (
        "/opt/midkernel/kimi.bin"
    )
    assert resolve_real_bin({"MIDKERNEL_GRAPH_KIMI_BIN": "/tmp/kimi-openrouter"}) == (
        "/opt/midkernel/kimi.bin"
    )


def test_prepare_config_file_includes_requested_model(tmp_path):
    share = tmp_path / "kimi"
    path = prepare_config_file(
        ["--model", "anthropic/claude-sonnet-4.5"],
        environ={
            "KIMI_SHARE_DIR": str(share),
            "OPENROUTER_API_KEY": "sk-or-v1-x",
        },
    )
    assert path == str(share / "config.toml")
    text = (share / "config.toml").read_text(encoding="utf-8")
    assert "anthropic/claude-sonnet-4.5" in text
    assert "midkernel" in text
    assert "openai_legacy" in text
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in text
    assert f"max_tokens = {UNSAFE_OPENROUTER_DEFAULT}" not in text
    assert "max_context_size = 262144" in text
    assert text.count("max_tokens = 262144") == 0


def test_rewrite_clamps_max_tokens_cli_and_never_uses_context():
    rewritten = rewrite_kimi_argv(
        [
            "--print",
            "--model",
            "moonshotai/kimi-k3",
            "--max-tokens",
            str(UNSAFE_OPENROUTER_DEFAULT),
        ],
        config_file="/tmp/config.toml",
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    assert rewritten[rewritten.index("--max-tokens") + 1] == str(DEFAULT_MAX_TOKENS)
    assert str(UNSAFE_OPENROUTER_DEFAULT) not in rewritten
    assert "262144" not in rewritten


def test_rewrite_does_not_inject_max_tokens_flag():
    """kimi-cli 1.49 has no --max-tokens; inventing it would crash the hunter."""
    rewritten = rewrite_kimi_argv(
        ["--print", "--model", "moonshotai/kimi-k3", "-p", "hunt"],
        config_file="/tmp/config.toml",
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    assert "--max-tokens" not in rewritten
    assert "--max-completion-tokens" not in rewritten


def test_front_exports_capped_tokens_and_sitecustomize(tmp_path):
    share = tmp_path / "kimi"
    env = {
        "KIMI_SHARE_DIR": str(share),
        "OPENROUTER_API_KEY": "sk-or-v1-x",
    }
    cap = apply_front_max_tokens_env(env)
    assert cap == DEFAULT_MAX_TOKENS
    assert env["KIMI_MODEL_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert env["OPENROUTER_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)
    assert (share / "py_path" / "sitecustomize.py").is_file()
    assert env["PYTHONPATH"].startswith(str(share / "py_path"))
    assert env["KIMI_MAX_TOKENS"] == str(DEFAULT_MAX_TOKENS)


def test_prepare_config_file_preserves_wrap_kimi_proxy(tmp_path):
    """Playbooks wrap_kimi localhost injection must survive kimi-openrouter rewrite."""
    share = tmp_path / "kimi"
    share.mkdir()
    proxy = "http://127.0.0.1:54321/api/v1"
    (share / "config.toml").write_text(
        "\n".join(
            [
                "[providers.openrouter]",
                'type = "openai_legacy"',
                f'base_url = "{proxy}"',
                'api_key = "sk-or-v1-x"',
                "",
                "[models.midkernel]",
                'provider = "openrouter"',
                'model = "moonshotai/kimi-k3"',
                "max_context_size = 262144",
            ]
        ),
        encoding="utf-8",
    )
    path = prepare_config_file(
        ["--model", "moonshotai/kimi-k3"],
        environ={
            "KIMI_SHARE_DIR": str(share),
            "OPENROUTER_API_KEY": "sk-or-v1-x",
            "OPENAI_BASE_URL": proxy,
        },
    )
    assert path == str(share / "config.toml")
    text = (share / "config.toml").read_text(encoding="utf-8")
    assert f'base_url = "{proxy}"' in text
    assert "openrouter.ai" not in text
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in text


def test_prepare_config_file_preserves_proxy_from_existing_toml(tmp_path):
    share = tmp_path / "kimi"
    share.mkdir()
    proxy = "http://127.0.0.1:9999/api/v1"
    (share / "config.toml").write_text(
        f'[providers.openrouter]\nbase_url = "{proxy}"\napi_key = "sk-or-v1-x"\n',
        encoding="utf-8",
    )
    prepare_config_file(
        ["--model", "moonshotai/kimi-k3"],
        environ={
            "KIMI_SHARE_DIR": str(share),
            "OPENROUTER_API_KEY": "sk-or-v1-x",
            "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
        },
    )
    text = (share / "config.toml").read_text(encoding="utf-8")
    assert f'base_url = "{proxy}"' in text
    assert "openrouter.ai" not in text
    assert f"max_tokens = {DEFAULT_MAX_TOKENS}" in text
