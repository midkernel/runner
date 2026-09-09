from midkernel_runner.kimi_graph_bin import (
    drop_config_flags,
    model_from_argv,
    prepare_config_file,
    resolve_real_bin,
    rewrite_kimi_argv,
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
