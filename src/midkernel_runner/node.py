"""Prepare an agentflow ECS node: secrets, Kimi/OpenRouter, optional clone.

agentflow's ECS runner overrides the image ENTRYPOINT with ``bash -c`` and
then runs ``kimi --print ...``. This module is invoked from:

* ``BASH_ENV`` (so ``bash -c`` still prepares the node)
* the ``kimi`` wrapper on PATH
* ``midkernel-runner`` when the app RunTasks this image without a command
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from midkernel_runner.clone import CloneError, clone_repository
from midkernel_runner.config import ConfigError, RunConfig, load_config, load_optional_run_context
from midkernel_runner.kimi import (
    export_kimi_openrouter_env,
    graph_kimi_share_dir,
    write_kimi_openrouter_config,
)
from midkernel_runner.mode import should_clone_target
from midkernel_runner.secrets import HarnessSecrets, SecretsError, load_harness_secrets

LOG = logging.getLogger("midkernel.node")


class NodePrepareError(RuntimeError):
    """Node environment could not be prepared."""


@dataclass(frozen=True)
class PreparedNode:
    secrets: HarnessSecrets | None
    config: RunConfig | None
    kimi_config_path: Path | None
    cloned: bool


def apply_openrouter_env(
    api_key: str,
    *,
    model: str | None = None,
    share_dir: Path | None = None,
) -> None:
    for key, value in export_kimi_openrouter_env(
        {}, api_key, model=model, share_dir=share_dir
    ).items():
        os.environ[key] = value


def prepare_node(
    *,
    environ: dict[str, str] | None = None,
    clone_target: bool | None = None,
) -> PreparedNode:
    env = dict(os.environ if environ is None else environ)
    if clone_target is None:
        clone_target = should_clone_target(env)
    context = load_optional_run_context(env)
    secrets: HarnessSecrets | None = None
    config: RunConfig | None = None
    kimi_path: Path | None = None
    cloned = False

    existing_key = (
        (env.get("OPENROUTER_API_KEY") or env.get("OPENAI_API_KEY") or env.get("KIMI_API_KEY") or "")
        .strip()
    )

    if context is not None:
        try:
            config = load_config(env)
        except ConfigError as exc:
            raise NodePrepareError(str(exc)) from exc
        try:
            secrets = load_harness_secrets(config, environ=env)
        except SecretsError as exc:
            if existing_key and (env.get("GITHUB_TOKEN") or "").strip():
                secrets = HarnessSecrets(
                    openrouter_api_key=existing_key,
                    github_token=(env.get("GITHUB_TOKEN") or "").strip(),
                )
            else:
                raise NodePrepareError(str(exc)) from exc
    elif existing_key:
        secrets = HarnessSecrets(
            openrouter_api_key=existing_key,
            github_token=(env.get("GITHUB_TOKEN") or "").strip(),
        )

    if secrets and secrets.openrouter_api_key:
        model = (env.get("OPENROUTER_MODEL") or env.get("MODEL") or "moonshotai/kimi-k3").strip()
        home = Path(env.get("HOME") or os.environ.get("HOME") or "/home/agent")
        workdir = (config.workdir if config is not None else None) or env.get("WORKDIR") or "/workspace"
        share = graph_kimi_share_dir(workdir)
        # WORKDIR share dir is what in-task kimi.bin reads (KIMI_SHARE_DIR).
        # Also write ~/.kimi for BASH_ENV / PATH wrapper / single-kimi.
        kimi_path = write_kimi_openrouter_config(
            secrets.openrouter_api_key, model, share_dir=share
        )
        write_kimi_openrouter_config(secrets.openrouter_api_key, model, home=home)
        apply_openrouter_env(secrets.openrouter_api_key, model=model, share_dir=share)
        if secrets.github_token:
            os.environ["GITHUB_TOKEN"] = secrets.github_token

    if clone_target and config is not None and secrets is not None and secrets.github_token:
        dest = Path(config.repo_dir)
        if not (dest / ".git").exists():
            try:
                clone_repository(config, secrets)
                cloned = True
            except CloneError as exc:
                raise NodePrepareError(str(exc)) from exc

    return PreparedNode(
        secrets=secrets,
        config=config,
        kimi_config_path=kimi_path,
        cloned=cloned,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )
    try:
        prepared = prepare_node()
    except NodePrepareError as exc:
        LOG.error("node prepare failed: %s", exc)
        sys.exit(1)
    if prepared.kimi_config_path:
        LOG.info("kimi OpenRouter config %s", prepared.kimi_config_path)
    if prepared.cloned and prepared.config:
        LOG.info("cloned github.com/%s/%s", prepared.config.github_owner, prepared.config.github_name)
    sys.exit(0)


if __name__ == "__main__":
    main()
