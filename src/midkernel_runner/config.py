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

# OpenRouter vendor/model slug (app + playbooks). Matches Pareto `balanced`.
# Kimi CLI config aliases also accept openrouter/<slug> when agentflow
# passes that as --model. Override with OPENROUTER_MODEL or MODEL.
DEFAULT_OPENROUTER_MODEL = "google/gemini-3.8-flash"

DEFAULT_OPENROUTER_SECRET = "midkernel/dev/harness/openrouter-api-key"
DEFAULT_GITHUB_SECRET = "midkernel/dev/harness/github-token"

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

# Per-node kimi / review budget. Playbooks `_midkernel.py` uses the same
# table for each GOAL hunter / judge. Do not reuse this as the ecs-in-task
# wall clock — GOAL is a long serial graph.
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

# Playbooks graphs: prepare shell (10m) + publish shell (5m).
GRAPH_SHELL_OVERHEAD_SECONDS = 15 * 60

# goal-security-review serial kimi nodes besides hunter-1..N:
# threat-model, goal-author, surface-split, judge-a, judge-b, assemble.
# Hunters are a depends_on chain (never parallel). QA
# cmtuschdc0003lb04ktzewa7k: PROFILE_TIMEOUT_SECONDS["low"]=900 was applied
# as AGENT_TIMEOUT_SECONDS for the entire ecs-in-task.sh. prepare +
# threat-model (~9m) + goal-author (~4.5m) left ~82s; surface-split was
# killed at exactly 900s (00:25:27Z → 00:40:27Z TimeoutExpired). Not an
# OpenRouter 402/429 and not a hang — the whole-run budget was one node.
GOAL_SECURITY_REVIEW_SLUG = "goal-security-review"
GOAL_FIXED_SERIAL_KIMI_NODES = 6
DEFAULT_GOAL_COUNT = 6
MAX_GOAL_HUNTERS = 6

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
    run_timeout_seconds: int
    artifacts_key_override: str | None = None

    @property
    def repo_dir(self) -> str:
        # agentflow ECS cwd is WORKDIR (/workspace). Clone there so `kimi`
        # sees the tree without a hidden repo/ subdirectory.
        return self.workdir

    @property
    def report_path(self) -> str:
        return os.path.join(self.outputs_dir, "report.md")

    @property
    def artifact_key(self) -> str:
        if self.artifacts_key_override:
            return self.artifacts_key_override.lstrip("/")
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


def _first(env: dict[str, str | None], *names: str, default: str | None = None) -> str | None:
    for name in names:
        value = _optional(name, env)
        if value:
            return value
    return default


def _positive_int(name: str, raw: str | None, default: int) -> int:
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def parse_goal_count(raw: str | None) -> int:
    """Playbooks GOAL_COUNT (default 6, max 6). Invalid values fall back."""
    if raw is None or not str(raw).strip():
        return DEFAULT_GOAL_COUNT
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_GOAL_COUNT
    return max(1, min(value, MAX_GOAL_HUNTERS))


def serial_kimi_node_budget(playbook_slug: str, hunters: int = DEFAULT_GOAL_COUNT) -> int:
    """How many serial kimi nodes the in-task graph can run.

    Single-review playbooks: one kimi node. GOAL: fixed kimi nodes plus
    hunter-1..N on a depends_on chain (playbooks must not fan out).
    """
    if playbook_slug == GOAL_SECURITY_REVIEW_SLUG:
        return GOAL_FIXED_SERIAL_KIMI_NODES + max(1, min(hunters, MAX_GOAL_HUNTERS))
    return 1


def default_run_timeout_seconds(
    node_timeout: int,
    playbook_slug: str,
    hunters: int = DEFAULT_GOAL_COUNT,
) -> int:
    """Whole-run wall clock for ``ecs-in-task.sh`` / ``agentflow run``.

    A single profile timeout (900s on low) is enough for one kimi node, not
    for GOAL. ``node_timeout * serial_kimi_node_budget + prepare/publish``
    is the budget that lets later nodes start after threat-model + goal-author.
    """
    return node_timeout * serial_kimi_node_budget(playbook_slug, hunters) + GRAPH_SHELL_OVERHEAD_SECONDS


def load_optional_run_context(environ: dict[str, str] | None = None) -> dict[str, str] | None:
    """Return a context dict when Midkernel scan env is present; else None.

    agentflow may launch this image with only Kimi credentials. Midkernel
    artifact/clone setup runs only when RUN_ID + GitHub repo are set.
    """
    env: dict[str, str | None] = dict(os.environ if environ is None else environ)
    run_id = _optional("RUN_ID", env)
    owner = _optional("GITHUB_OWNER", env)
    name = _optional("GITHUB_NAME", env)
    if run_id and owner and name:
        return {"RUN_ID": run_id, "GITHUB_OWNER": owner, "GITHUB_NAME": name}
    return None


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

    slug = _first(env, "PLAYBOOK_SLUG", "PLAYBOOK", default=DEFAULT_PLAYBOOK_SLUG) or DEFAULT_PLAYBOOK_SLUG
    if not SLUG_RE.match(slug):
        raise ConfigError("PLAYBOOK_SLUG must be a lowercase kebab-case slug")

    profile = (_first(env, "SCAN_PROFILE", "PROFILE", default=DEFAULT_PROFILE) or DEFAULT_PROFILE).lower()
    if profile not in SCAN_PROFILES:
        raise ConfigError(f"SCAN_PROFILE must be one of {', '.join(SCAN_PROFILES)}")

    threat = _first(env, "THREAT_PIN", "THREAT")
    if threat and len(threat) > THREAT_PIN_MAX_LENGTH:
        raise ConfigError(f"THREAT_PIN exceeds {THREAT_PIN_MAX_LENGTH} characters")

    prefix = _optional("ARTIFACTS_PREFIX", env, DEFAULT_PREFIX) or DEFAULT_PREFIX
    model = _first(env, "OPENROUTER_MODEL", "MODEL", default=DEFAULT_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL
    if "/" not in model:
        raise ConfigError("OPENROUTER_MODEL must be vendor/model (e.g. google/gemini-3.8-flash)")

    node_timeout = _positive_int(
        "AGENT_TIMEOUT_SECONDS",
        _optional("AGENT_TIMEOUT_SECONDS", env),
        PROFILE_TIMEOUT_SECONDS[profile],
    )
    hunters = parse_goal_count(_optional("GOAL_COUNT", env))
    computed_run = default_run_timeout_seconds(node_timeout, slug, hunters)
    run_timeout = _positive_int(
        "AGENT_RUN_TIMEOUT_SECONDS",
        _optional("AGENT_RUN_TIMEOUT_SECONDS", env),
        computed_run,
    )

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
        timeout_seconds=node_timeout,
        run_timeout_seconds=run_timeout,
        artifacts_key_override=_optional("ARTIFACTS_KEY", env),
    )
