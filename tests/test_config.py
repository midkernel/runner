from midkernel_runner.config import (
    ConfigError,
    GRAPH_SHELL_OVERHEAD_SECONDS,
    PROFILE_TIMEOUT_SECONDS,
    default_run_timeout_seconds,
    load_config,
    parse_goal_count,
    serial_kimi_node_budget,
)
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
    assert cfg.generations_artifact_key == "runs/run_abc-1/openrouter-generations.json"
    assert cfg.generations_s3_uri == (
        "s3://midkernel-dev-artifacts/runs/run_abc-1/openrouter-generations.json"
    )
    assert cfg.openrouter_model == "google/gemini-3.8-flash"
    assert cfg.openrouter_secret_id == "midkernel/dev/harness/openrouter-api-key"
    assert cfg.github_secret_id == "midkernel/dev/harness/github-token"
    assert cfg.timeout_seconds == 3600
    assert cfg.run_timeout_seconds == 3600 + GRAPH_SHELL_OVERHEAD_SECONDS


def test_profile_timeout_and_threat():
    cfg = load_config(_base(SCAN_PROFILE="low", THREAT_PIN="reentrancy"))
    assert cfg.scan_profile == "low"
    assert cfg.timeout_seconds == 1800
    assert cfg.timeout_seconds == 30 * 60
    assert cfg.run_timeout_seconds == 30 * 60 + GRAPH_SHELL_OVERHEAD_SECONDS
    assert cfg.threat_pin == "reentrancy"


def test_low_profile_node_timeout_is_1800():
    """QA cmtutkn8k0003id04hs5s8j7z: hunter-1 exit 124 after 900s."""
    assert PROFILE_TIMEOUT_SECONDS["low"] == 1800
    cfg = load_config(_base(SCAN_PROFILE="low"))
    assert cfg.timeout_seconds == 1800


def test_profile_hard_budgets_james_lock():
    """James lock 2026-09-10 / QA cmtuvv61w0003gm0az74grqv2: raise balanced/max hard."""
    assert PROFILE_TIMEOUT_SECONDS["low"] == 30 * 60
    assert PROFILE_TIMEOUT_SECONDS["balanced"] == 60 * 60
    assert PROFILE_TIMEOUT_SECONDS["max"] == 2 * 60 * 60
    assert PROFILE_TIMEOUT_SECONDS["low"] != PROFILE_TIMEOUT_SECONDS["balanced"]
    balanced = load_config(_base(SCAN_PROFILE="balanced"))
    maximum = load_config(_base(SCAN_PROFILE="max"))
    assert balanced.timeout_seconds == 3600
    assert balanced.run_timeout_seconds == 3600 + GRAPH_SHELL_OVERHEAD_SECONDS
    assert maximum.timeout_seconds == 7200
    assert maximum.run_timeout_seconds == 7200 + GRAPH_SHELL_OVERHEAD_SECONDS


def test_max_timeout():
    cfg = load_config(_base(SCAN_PROFILE="max"))
    assert cfg.timeout_seconds == 2 * 60 * 60
    assert cfg.run_timeout_seconds == 2 * 60 * 60 + GRAPH_SHELL_OVERHEAD_SECONDS


def test_missing_run_id():
    with pytest.raises(ConfigError, match="RUN_ID"):
        load_config({"GITHUB_OWNER": "a", "GITHUB_NAME": "b"})


def test_bad_profile():
    with pytest.raises(ConfigError, match="SCAN_PROFILE"):
        load_config(_base(SCAN_PROFILE="turbo"))


def test_prefix_without_slash():
    cfg = load_config(_base(ARTIFACTS_PREFIX="runs"))
    assert cfg.artifact_key == "runs/run_abc-1/report.md"
    assert cfg.generations_artifact_key == "runs/run_abc-1/openrouter-generations.json"


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
    assert cfg.generations_artifact_key == "runs/run_abc-1/openrouter-generations.json"


def test_openrouter_model_env_overrides_default():
    cfg = load_config(_base(OPENROUTER_MODEL="moonshotai/kimi-k3"))
    assert cfg.openrouter_model == "moonshotai/kimi-k3"
    cfg = load_config(_base(MODEL="anthropic/claude-sonnet-4.6"))
    assert cfg.openrouter_model == "anthropic/claude-sonnet-4.6"


def test_optional_run_context():
    from midkernel_runner.config import load_optional_run_context

    assert load_optional_run_context(_base()) is not None
    assert load_optional_run_context({"KIMI_API_KEY": "x"}) is None


def test_serial_kimi_budget_goal_vs_single_review():
    assert serial_kimi_node_budget("security-review") == 1
    assert serial_kimi_node_budget("solana-validator-security") == 1
    assert serial_kimi_node_budget("goal-security-review") == 12
    assert serial_kimi_node_budget("goal-security-review", hunters=1) == 7
    assert serial_kimi_node_budget("goal-security-review", hunters=6) == 12
    assert serial_kimi_node_budget("goal-security-review", hunters=99) == 12


def test_parse_goal_count_matches_playbooks():
    assert parse_goal_count(None) == 6
    assert parse_goal_count("") == 6
    assert parse_goal_count("3") == 3
    assert parse_goal_count("0") == 1
    assert parse_goal_count("99") == 6
    assert parse_goal_count("nope") == 6


def test_default_run_timeout_scales_goal_not_one_node():
    """One node timeout is not a whole-run budget. Formula is unchanged."""
    low = PROFILE_TIMEOUT_SECONDS["low"]
    assert low == 1800
    review = default_run_timeout_seconds(low, "security-review")
    goal = default_run_timeout_seconds(low, "goal-security-review")
    assert review == low + GRAPH_SHELL_OVERHEAD_SECONDS
    assert goal == low * 12 + GRAPH_SHELL_OVERHEAD_SECONDS
    assert goal == 22500
    assert goal > 900
    assert goal >= 90 * 60
    # threat-model + goal-author alone already ~13m on cmtuschdc0003lb04ktzewa7k
    assert goal > 13 * 60 + low


def test_low_goal_keeps_per_node_1800_and_raises_wall_clock():
    cfg = load_config(
        _base(
            SCAN_PROFILE="low",
            PLAYBOOK="goal-security-review",
            OPENROUTER_MODEL="deepseek/deepseek-v4-flash-0731",
        )
    )
    assert cfg.timeout_seconds == 1800
    assert cfg.run_timeout_seconds == 22500
    assert cfg.run_timeout_seconds > cfg.timeout_seconds
    assert cfg.openrouter_model == "deepseek/deepseek-v4-flash-0731"


def test_goal_count_shrinks_run_timeout_not_node_timeout():
    cfg = load_config(
        _base(SCAN_PROFILE="low", PLAYBOOK="goal-security-review", GOAL_COUNT="1")
    )
    assert cfg.timeout_seconds == 1800
    assert cfg.run_timeout_seconds == 1800 * 7 + GRAPH_SHELL_OVERHEAD_SECONDS


def test_agent_timeout_is_per_node_not_wall_clock():
    cfg = load_config(
        _base(
            SCAN_PROFILE="low",
            PLAYBOOK="goal-security-review",
            AGENT_TIMEOUT_SECONDS="600",
        )
    )
    assert cfg.timeout_seconds == 600
    assert cfg.run_timeout_seconds == 600 * 12 + GRAPH_SHELL_OVERHEAD_SECONDS


def test_agent_run_timeout_overrides_computed_wall_clock():
    cfg = load_config(
        _base(
            SCAN_PROFILE="low",
            PLAYBOOK="goal-security-review",
            AGENT_RUN_TIMEOUT_SECONDS="5400",
        )
    )
    assert cfg.timeout_seconds == 1800
    assert cfg.run_timeout_seconds == 5400


def test_balanced_and_max_goal_scale_with_profile():
    """Whole-run wall = node_timeout × serial kimi budget + overhead (James lock)."""
    balanced = load_config(_base(SCAN_PROFILE="balanced", PLAYBOOK="goal-security-review"))
    maximum = load_config(_base(SCAN_PROFILE="max", PLAYBOOK="goal-security-review"))
    assert balanced.timeout_seconds == 60 * 60
    assert balanced.run_timeout_seconds == 60 * 60 * 12 + GRAPH_SHELL_OVERHEAD_SECONDS
    assert balanced.run_timeout_seconds == 44100
    assert maximum.timeout_seconds == 2 * 60 * 60
    assert maximum.run_timeout_seconds == 2 * 60 * 60 * 12 + GRAPH_SHELL_OVERHEAD_SECONDS
    assert maximum.run_timeout_seconds == 87300
    assert default_run_timeout_seconds(PROFILE_TIMEOUT_SECONDS["balanced"], "goal-security-review") == 44100
    assert default_run_timeout_seconds(PROFILE_TIMEOUT_SECONDS["max"], "goal-security-review") == 87300


def test_invalid_timeout_env_rejected():
    with pytest.raises(ConfigError, match="AGENT_TIMEOUT_SECONDS"):
        load_config(_base(AGENT_TIMEOUT_SECONDS="nope"))
    with pytest.raises(ConfigError, match="AGENT_RUN_TIMEOUT_SECONDS"):
        load_config(_base(AGENT_RUN_TIMEOUT_SECONDS="0"))
