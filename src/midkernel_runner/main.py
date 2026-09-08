"""Midkernel scan helper: secrets → clone → Kimi → report.md → S3.

The default image CMD is ``agentflow --help`` (valid agentflow ECS node).
This helper runs when the app RunTasks the image with Midkernel scan env
and no command override. Native agentflow launches ``kimi`` via ``bash -c``;
``BASH_ENV`` + the ``kimi`` wrapper still prepare OpenRouter and publish.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from midkernel_runner.artifacts import ArtifactError, upload_report
from midkernel_runner.clone import CloneError
from midkernel_runner.config import ConfigError, load_config
from midkernel_runner.kimi import KimiError, run_kimi
from midkernel_runner.node import NodePrepareError, prepare_node
from midkernel_runner.playbook import PlaybookError, load_playbook_prompt
from midkernel_runner.report import ReportError

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
        "start run_id=%s repo=%s/%s playbook=%s profile=%s model=%s harness=kimi",
        config.run_id,
        config.github_owner,
        config.github_name,
        config.playbook_slug,
        config.scan_profile,
        config.openrouter_model,
    )
    LOG.info("artifacts dest %s", config.s3_uri)

    try:
        prepared = prepare_node()
        if prepared.secrets is None:
            raise NodePrepareError("harness secrets were not loaded")
        LOG.info("prepared node (kimi OpenRouter + task-role secrets; values not logged)")
        prompt = load_playbook_prompt(config)
        LOG.info("playbook prompt loaded (%d chars)", len(prompt))
        if prepared.cloned:
            LOG.info("cloned github.com/%s/%s", config.github_owner, config.github_name)
        report = run_kimi(config, prepared.secrets, prompt)
        LOG.info("kimi produced report.md (%d bytes)", len(report.encode("utf-8")))
        uri = upload_report(config, Path(config.report_path))
        LOG.info("uploaded %s", uri)
    except (
        NodePrepareError,
        PlaybookError,
        CloneError,
        KimiError,
        ReportError,
        ArtifactError,
    ) as exc:
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
