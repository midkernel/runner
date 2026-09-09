"""Midkernel scan helper.

Default image CMD is ``midkernel-default`` → this module when ``RUN_ID``
is set. Native agentflow per-node launch still uses ``bash -c`` + PATH
``kimi``; ``BASH_ENV`` + the kimi wrapper prepare OpenRouter and publish.

When midkernel/playbooks publishes ``pipelines/<slug>.py``, run that graph
in-task (``MIDKERNEL_AGENTFLOW_TARGET=local``) so ``_node_io.py`` uploads
``graph.json`` + ``nodes/*`` next to ``report.md``. The old single-kimi +
``<slug>.md`` path is the fallback when no pipeline file exists (or
``MIDKERNEL_FORCE_MD_KIMI=1``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from midkernel_runner.artifacts import ArtifactError, upload_report
from midkernel_runner.clone import CloneError
from midkernel_runner.config import ConfigError, load_config
from midkernel_runner.graph import GraphError, run_playbooks_graph, should_run_playbooks_graph
from midkernel_runner.kimi import KimiError, run_kimi
from midkernel_runner.mode import apply_graph_mode_flags
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
        use_graph = should_run_playbooks_graph(config)
        if use_graph:
            # Pin flags before prepare so any BASH_ENV / node-prepare in this
            # process skips clone-into-/workspace. Entrypoint no longer runs
            # midkernel-node-prepare on the default CMD path.
            apply_graph_mode_flags()
            LOG.info(
                "playbooks graph pipelines/%s.py (in-task node I/O → graph.json)",
                config.playbook_slug,
            )
            prepared = prepare_node(clone_target=False)
            if prepared.secrets is None:
                raise NodePrepareError("harness secrets were not loaded")
            LOG.info("prepared node (kimi OpenRouter + task-role secrets; values not logged)")
            try:
                return run_playbooks_graph(config)
            except GraphError as exc:
                # Confirmed-missing pipelines stay on md+kimi. An unknown probe
                # still tries the graph; if the clone has no pipelines/<slug>.py,
                # fall back instead of failing a markdown-only playbook.
                detail = str(exc)
                if "cloned playbooks missing" in detail:
                    LOG.warning("playbooks graph unavailable, md+kimi fallback: %s", detail)
                else:
                    raise

        prepared = prepare_node(clone_target=True)
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
        GraphError,
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
