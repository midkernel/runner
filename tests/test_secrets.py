import pytest

from midkernel_runner.config import load_config
from midkernel_runner.secrets import (
    SecretsError,
    load_harness_secrets,
    parse_github_token_secret,
    parse_openrouter_secret,
)


def test_parse_openrouter_raw():
    assert parse_openrouter_secret("  sk-or-v1-abc  ") == "sk-or-v1-abc"


def test_parse_openrouter_json():
    assert parse_openrouter_secret('{"apiKey":"sk-or-v1-xyz"}') == "sk-or-v1-xyz"
    assert parse_openrouter_secret('{"OPENROUTER_API_KEY":"sk-or-v1-env"}') == "sk-or-v1-env"


def test_parse_github_raw_and_json():
    assert parse_github_token_secret("ghs_install") == "ghs_install"
    assert parse_github_token_secret('{"token":"github_pat_1"}') == "github_pat_1"
    assert parse_github_token_secret('{"installationToken":"ghs_2"}') == "ghs_2"


def test_parse_json_missing_key():
    with pytest.raises(SecretsError, match="missing a usable key"):
        parse_openrouter_secret('{"foo":"bar"}')


class FakeSM:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_secret_value(self, *, SecretId):
        return {"SecretString": self.mapping[SecretId]}


def _cfg():
    return load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "midkernel",
            "GITHUB_NAME": "playbooks",
        }
    )


def test_load_from_secrets_manager():
    client = FakeSM(
        {
            "midkernel/dev/harness/openrouter-api-key": "sk-or-v1-sm",
            "midkernel/dev/harness/github-token": '{"token":"ghs_sm"}',
        }
    )
    secrets = load_harness_secrets(_cfg(), environ={}, client=client)
    assert secrets.openrouter_api_key == "sk-or-v1-sm"
    assert secrets.github_token == "ghs_sm"


def test_per_run_github_token_overrides_sm():
    client = FakeSM(
        {
            "midkernel/dev/harness/openrouter-api-key": "sk-or-v1-sm",
            "midkernel/dev/harness/github-token": "ghs_static",
        }
    )
    secrets = load_harness_secrets(
        _cfg(),
        environ={"GITHUB_TOKEN": "ghs_install_from_app"},
        client=client,
    )
    assert secrets.github_token == "ghs_install_from_app"
    assert secrets.openrouter_api_key == "sk-or-v1-sm"


def test_local_env_skips_sm():
    secrets = load_harness_secrets(
        _cfg(),
        environ={
            "MIDKERNEL_LOCAL": "1",
            "OPENROUTER_API_KEY": "sk-or-v1-local",
            "GITHUB_TOKEN": "ghp_local",
        },
        client=FakeSM({}),
    )
    assert secrets.openrouter_api_key == "sk-or-v1-local"
    assert secrets.github_token == "ghp_local"
