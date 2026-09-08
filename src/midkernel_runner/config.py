"""Environment contract for a Midkernel Scan ECS task."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

SCAN_PROFILES = ("low", "balanced", "max")
DEFAULT_PLAYBOOK_SLUG = "security-review"
DEFAULT_PROFILE = "balanced"
DEFAULT_BUCKET = "midkernel-dev-artifacts"
DEFAULT_PREFIX = "runs/"
DEFAULT_REGION = "us-east-1"
DEFAULT_PLAYBOOKS_OWNER = "midkernel"
DEFAULT_PLAYBOOKS_NAME = "playbooks"
DEFAULT_PLAYBOOKS_REF = "main"

# OpenRouter slug for Kimi K3. OpenCode composes provider/model as
# openrouter/<openrouter-slug>. `--variant max` is the Kimi K3 Max effort.
DEFAULT_OPENROUTER_MODEL = "openrouter/moonshotai/kimi-k3"
DEFAULT_OPENROUTER_VARIANT = "max"

DEFAULT_OPENROUTER_SECRET = "midkernel/dev/harness/openrouter-api-key"
DEFAULT_GITHUB_SECRET = "midkernel/dev/harness/github-token"

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

PROFILE_VARIANT = {
    "low": "low",
    "balanced": "medium",
    "max": "max",
}

PROFILE_TIMEOUT_SECONDS = {
    "low": 15 * 60,
    "balanced": 30 * 60,
    "max": 60 * 60,
}

# Suggested Fargate sizes for Eng/IT (documented, not enforced here).
PROFILE_FARGATE = {
    "low": {"cpu": "1024", "memory": "2048"},
    "balanced": {"cpu": "2048", "memory": "4096"},
    "max": {"cpu": "4096", "memory": "8192"},
}

THREAT_PIN_MAX_LENGTH = 80


class ConfigError(ValueError):
    """Invalid or missing run environment."""


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    github_owner: str
    github_name: str
    playbook_slug: str
    scan_profile: str
    threat_pin: str | None
    artifacts_bucket: str
    artifacts_prefix: str
    openrouter_model: str
    openrouter_variant: str
    aws_region: str
    openrouter_secret_id: str
    github_secret_id: str
    playbooks_owner: str
    playbooks_name: str
    playbooks_ref: str
    github_ref: str | None
    workdir: str
    outputs_dir: str
    timeout_seconds: int

    @property
    def repo_dir(self) -> str:
        return os.path.join(self.workdir, "repo")

    @property
    def report_path(self) -> str:
        return os.path.join(self.outputs_dir, "report.md")

    @property
    def artifact_key(self) -> str:
        prefix = self.artifacts_prefix if self.artifacts_prefix.endswith("/") else f"{self.artifacts_prefix}/"
        return f"{prefix}{self.run_id}/report.md"

    @property
    def s3_uri(self) -> str:
        return f"s3://{self.artifacts_bucket}/{self.artifact_key}"

    @property
    def github_clone_url(self) -> str:
        return f"https://github.com/{self.github_owner}/{self.github_name}.git"


def _require(name: str, env: dict[str, str | None]) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ConfigError(f"Required environment variable {name} is missing or empty")
    return value


def _optional(name: str, env: dict[str, str | None], default: str | None = None) -> str | None:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default


def load_config(environ: dict[str, str] | None = None) -> RunConfig:
    env: dict[str, str | None] = dict(os.environ if environ is None else environ)

    run_id = _require("RUN_ID", env)
    if not RUN_ID_RE.match(run_id):
        raise ConfigError("RUN_ID must be 1-128 chars of [A-Za-z0-9._:-]")

    owner = _require("GITHUB_OWNER", env)
    name = _require("GITHUB_NAME", env)
    if not OWNER_RE.match(owner):
        raise ConfigError("GITHUB_OWNER is not a valid GitHub owner")
    if not REPO_RE.match(name):
        raise ConfigError("GITHUB_NAME is not a valid GitHub repository name")

    slug = _optional("PLAYBOOK_SLUG", env, DEFAULT_PLAYBOOK_SLUG) or DEFAULT_PLAYBOOK_SLUG
    if not SLUG_RE.match(slug):
        raise ConfigError("PLAYBOOK_SLUG must be a lowercase kebab-case slug")

    profile = (_optional("SCAN_PROFILE", env, DEFAULT_PROFILE) or DEFAULT_PROFILE).lower()
    if profile not in SCAN_PROFILES:
        raise ConfigError(f"SCAN_PROFILE must be one of {', '.join(SCAN_PROFILES)}")

    threat = _optional("THREAT_PIN", env)
    if threat and len(threat) > THREAT_PIN_MAX_LENGTH:
        raise ConfigError(f"THREAT_PIN exceeds {THREAT_PIN_MAX_LENGTH} characters")

    prefix = _optional("ARTIFACTS_PREFIX", env, DEFAULT_PREFIX) or DEFAULT_PREFIX
    model = _optional("OPENROUTER_MODEL", env, DEFAULT_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL
    if "/" not in model:
        raise ConfigError("OPENROUTER_MODEL must be provider/model (e.g. openrouter/moonshotai/kimi-k3)")

    variant = _optional("OPENROUTER_VARIANT", env) or PROFILE_VARIANT[profile]
    timeout = int(_optional("AGENT_TIMEOUT_SECONDS", env, str(PROFILE_TIMEOUT_SECONDS[profile])) or PROFILE_TIMEOUT_SECONDS[profile])

    return RunConfig(
        run_id=run_id,
        github_owner=owner,
        github_name=name,
        playbook_slug=slug,
        scan_profile=profile,
        threat_pin=threat,
        artifacts_bucket=_optional("ARTIFACTS_BUCKET", env, DEFAULT_BUCKET) or DEFAULT_BUCKET,
        artifacts_prefix=prefix,
        openrouter_model=model,
        openrouter_variant=variant,
        aws_region=_optional("AWS_REGION", env, DEFAULT_REGION) or DEFAULT_REGION,
        openrouter_secret_id=_optional("OPENROUTER_SECRET_ID", env, DEFAULT_OPENROUTER_SECRET)
        or DEFAULT_OPENROUTER_SECRET,
        github_secret_id=_optional("GITHUB_TOKEN_SECRET_ID", env, DEFAULT_GITHUB_SECRET) or DEFAULT_GITHUB_SECRET,
        playbooks_owner=_optional("PLAYBOOKS_OWNER", env, DEFAULT_PLAYBOOKS_OWNER) or DEFAULT_PLAYBOOKS_OWNER,
        playbooks_name=_optional("PLAYBOOKS_NAME", env, DEFAULT_PLAYBOOKS_NAME) or DEFAULT_PLAYBOOKS_NAME,
        playbooks_ref=_optional("PLAYBOOKS_REF", env, DEFAULT_PLAYBOOKS_REF) or DEFAULT_PLAYBOOKS_REF,
        github_ref=_optional("GITHUB_REF", env),
        workdir=_optional("WORKDIR", env, "/workspace") or "/workspace",
        outputs_dir=_optional("OUTPUTS_DIR", env, "/outputs") or "/outputs",
        timeout_seconds=timeout,
    )
