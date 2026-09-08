"""ECS task entrypoint: secrets → clone → OpenCode → report.md → S3."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from midkernel_runner.artifacts import ArtifactError, upload_report
from midkernel_runner.clone import CloneError, clone_repository
from midkernel_runner.config import ConfigError, load_config
from midkernel_runner.opencode import OpenCodeError, run_opencode
from midkernel_runner.playbook import PlaybookError, load_playbook_prompt
from midkernel_runner.report import ReportError
from midkernel_runner.secrets import SecretsError, load_harness_secrets

LOG = logging.getLogger("midkernel.runner")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )


def run() -> int:
    _configure_logging()
    try:
        config = load_config()
    except ConfigError as exc:
        LOG.error("invalid run environment: %s", exc)
        return 2

    LOG.info(
        "start run_id=%s repo=%s/%s playbook=%s profile=%s model=%s variant=%s",
        config.run_id,
        config.github_owner,
        config.github_name,
        config.playbook_slug,
        config.scan_profile,
        config.openrouter_model,
        config.openrouter_variant,
    )
    LOG.info("artifacts dest %s", config.s3_uri)

    try:
        secrets = load_harness_secrets(config)
        LOG.info("loaded harness secrets via task role / env (values not logged)")
        prompt = load_playbook_prompt(config)
        LOG.info("playbook prompt loaded (%d chars)", len(prompt))
        clone_repository(config, secrets)
        LOG.info("cloned github.com/%s/%s", config.github_owner, config.github_name)
        report = run_opencode(config, secrets, prompt)
        LOG.info("opencode produced report.md (%d bytes)", len(report.encode("utf-8")))
        uri = upload_report(config, Path(config.report_path))
        LOG.info("uploaded %s", uri)
    except (SecretsError, PlaybookError, CloneError, OpenCodeError, ReportError, ArtifactError) as exc:
        LOG.error("run failed: %s", exc)
        return 1
    except Exception as exc:  # unexpected
        LOG.exception("unexpected failure: %s", exc)
        return 1

    LOG.info("done run_id=%s", config.run_id)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
