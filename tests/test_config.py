from midkernel_runner.config import ConfigError, load_config
import pytest


def _base(**overrides):
    env = {
        "RUN_ID": "run_abc-1",
        "GITHUB_OWNER": "midkernel",
        "GITHUB_NAME": "playbooks",
    }
    env.update(overrides)
    return env


def test_defaults():
    cfg = load_config(_base())
    assert cfg.playbook_slug == "security-review"
    assert cfg.scan_profile == "balanced"
    assert cfg.artifacts_bucket == "midkernel-dev-artifacts"
    assert cfg.artifact_key == "runs/run_abc-1/report.md"
    assert cfg.s3_uri == "s3://midkernel-dev-artifacts/runs/run_abc-1/report.md"
    assert cfg.openrouter_model == "google/gemini-3.8-flash"
    assert cfg.openrouter_secret_id == "midkernel/dev/harness/openrouter-api-key"
    assert cfg.github_secret_id == "midkernel/dev/harness/github-token"


def test_profile_timeout_and_threat():
    cfg = load_config(_base(SCAN_PROFILE="low", THREAT_PIN="reentrancy"))
    assert cfg.scan_profile == "low"
    assert cfg.timeout_seconds == 15 * 60
    assert cfg.threat_pin == "reentrancy"


def test_max_timeout():
    cfg = load_config(_base(SCAN_PROFILE="max"))
    assert cfg.timeout_seconds == 60 * 60


def test_missing_run_id():
    with pytest.raises(ConfigError, match="RUN_ID"):
        load_config({"GITHUB_OWNER": "a", "GITHUB_NAME": "b"})


def test_bad_profile():
    with pytest.raises(ConfigError, match="SCAN_PROFILE"):
        load_config(_base(SCAN_PROFILE="turbo"))


def test_prefix_without_slash():
    cfg = load_config(_base(ARTIFACTS_PREFIX="runs"))
    assert cfg.artifact_key == "runs/run_abc-1/report.md"


def test_app_env_aliases():
    cfg = load_config(
        _base(
            PLAYBOOK="solana-validator-security",
            PROFILE="max",
            THREAT="runtime",
            MODEL="moonshotai/kimi-k3",
            ARTIFACTS_KEY="runs/run_abc-1/report.md",
        )
    )
    assert cfg.playbook_slug == "solana-validator-security"
    assert cfg.scan_profile == "max"
    assert cfg.threat_pin == "runtime"
    assert cfg.openrouter_model == "moonshotai/kimi-k3"
    assert cfg.artifact_key == "runs/run_abc-1/report.md"


def test_openrouter_model_env_overrides_default():
    cfg = load_config(_base(OPENROUTER_MODEL="moonshotai/kimi-k3"))
    assert cfg.openrouter_model == "moonshotai/kimi-k3"
    cfg = load_config(_base(MODEL="anthropic/claude-sonnet-4.6"))
    assert cfg.openrouter_model == "anthropic/claude-sonnet-4.6"


def test_optional_run_context():
    from midkernel_runner.config import load_optional_run_context

    assert load_optional_run_context(_base()) is not None
    assert load_optional_run_context({"KIMI_API_KEY": "x"}) is None
