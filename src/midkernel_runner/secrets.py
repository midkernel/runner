"""Load harness secrets from Secrets Manager via the ECS task role.

Expected secret names (infra placeholders, values written out of band):

* ``midkernel/dev/harness/openrouter-api-key``
* ``midkernel/dev/harness/github-token``

Accepted ``SecretString`` formats (first match wins):

OpenRouter
  * raw key (``sk-or-v1-...`` or any non-JSON string)
  * JSON ``{"apiKey":"..."}``, ``{"api_key":"..."}``, ``{"OPENROUTER_API_KEY":"..."}``,
    or ``{"key":"..."}``

GitHub token
  * raw PAT / installation token (``ghp_``, ``ghs_``, ``github_pat_``, or any
    non-JSON string the GitHub API accepts as ``Authorization: Bearer``)
  * JSON ``{"token":"..."}``, ``{"github_token":"..."}``, ``{"GITHUB_TOKEN":"..."}``,
    or ``{"installationToken":"..."}``

Never bake secrets into the image. Local/dev can pass ``OPENROUTER_API_KEY``
and ``GITHUB_TOKEN`` to skip Secrets Manager (``MIDKERNEL_LOCAL=1``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from midkernel_runner.config import RunConfig


class SecretsError(RuntimeError):
    """Could not obtain a required harness secret."""


class SecretsClient(Protocol):
    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class HarnessSecrets:
    openrouter_api_key: str
    github_token: str


def parse_secret_string(secret_string: str, *, keys: tuple[str, ...], label: str) -> str:
    text = secret_string.strip()
    if not text:
        raise SecretsError(f"{label} secret is empty")
    if text[0] != "{":
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SecretsError(f"{label} secret looks like JSON but is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SecretsError(f"{label} secret JSON must be an object")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise SecretsError(
        f"{label} secret JSON is missing a usable key (tried: {', '.join(keys)})"
    )


def parse_openrouter_secret(secret_string: str) -> str:
    return parse_secret_string(
        secret_string,
        keys=("apiKey", "api_key", "OPENROUTER_API_KEY", "key"),
        label="openrouter-api-key",
    )


def parse_github_token_secret(secret_string: str) -> str:
    return parse_secret_string(
        secret_string,
        keys=("token", "github_token", "GITHUB_TOKEN", "installationToken", "installation_token"),
        label="github-token",
    )


def _from_environ(environ: dict[str, str]) -> HarnessSecrets | None:
    key = (environ.get("OPENROUTER_API_KEY") or "").strip()
    token = (environ.get("GITHUB_TOKEN") or "").strip()
    if key and token:
        return HarnessSecrets(openrouter_api_key=key, github_token=token)
    return None


def load_harness_secrets(
    config: RunConfig,
    *,
    environ: dict[str, str] | None = None,
    client: SecretsClient | None = None,
) -> HarnessSecrets:
    env = dict(os.environ if environ is None else environ)
    local = (env.get("MIDKERNEL_LOCAL") or "").strip() in {"1", "true", "TRUE", "yes"}
    from_env = _from_environ(env)
    if from_env and (local or client is None and env.get("MIDKERNEL_FORCE_SECRETS_MANAGER") != "1"):
        # Prefer explicit env when both are set (local docker / tests).
        # On ECS, the task role fetch is the source of truth unless env was
        # injected by the control plane for a per-run installation token.
        if local or env.get("GITHUB_TOKEN_SOURCE", "") == "env":
            return from_env

    fetcher = client
    if fetcher is None:
        import boto3

        fetcher = boto3.client("secretsmanager", region_name=config.aws_region)

    try:
        openrouter_raw = fetcher.get_secret_value(SecretId=config.openrouter_secret_id)["SecretString"]
        github_raw = fetcher.get_secret_value(SecretId=config.github_secret_id)["SecretString"]
    except Exception as exc:  # boto ClientError and missing versions
        if from_env:
            # Installation token (or PAT) already in env; OpenRouter still
            # required. If SM is down but env has both, use env.
            return from_env
        raise SecretsError(
            f"GetSecretValue failed for harness secrets ({config.openrouter_secret_id}, "
            f"{config.github_secret_id}). The ECS task role must be allowed "
            "secretsmanager:GetSecretValue. Do not invent a report."
        ) from exc

    if not openrouter_raw:
        raise SecretsError("openrouter-api-key SecretString is missing (no AWSCURRENT version?)")
    if not github_raw:
        raise SecretsError("github-token SecretString is missing (no AWSCURRENT version?)")

    openrouter_key = parse_openrouter_secret(str(openrouter_raw))
    # Per-run installation token from the app wins over the static SM PAT
    # when the control plane injects GITHUB_TOKEN.
    github_token = (env.get("GITHUB_TOKEN") or "").strip() or parse_github_token_secret(str(github_raw))
    return HarnessSecrets(openrouter_api_key=openrouter_key, github_token=github_token)
