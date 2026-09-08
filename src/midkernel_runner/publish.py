"""Upload report.md to the Midkernel artifacts bucket when RUN_ID is set."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from midkernel_runner.artifacts import ArtifactError, upload_report
from midkernel_runner.config import ConfigError, load_config, load_optional_run_context
from midkernel_runner.report import ReportError, persist_report

LOG = logging.getLogger("midkernel.publish")

class PublishError(RuntimeError):
    """Could not publish a Midkernel report artifact."""


def candidate_report_paths(config_outputs_dir: str | None = None, workdir: str | None = None) -> list[Path]:
    paths: list[Path] = []
    outputs = config_outputs_dir or os.environ.get("OUTPUTS_DIR") or "/outputs"
    work = workdir or os.environ.get("WORKDIR") or "/workspace"
    explicit = os.environ.get("MIDKERNEL_REPORT_PATH")
    if explicit:
        paths.append(Path(explicit))
    paths.extend(
        [
            Path(outputs) / "report.md",
            Path(work) / "report.md",
            Path(work) / "repo" / "report.md",
            Path.cwd() / "report.md",
            Path("/outputs/report.md"),
            Path("/workspace/report.md"),
        ]
    )
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def find_report_path(outputs_dir: str | None = None, workdir: str | None = None) -> Path | None:
    for path in candidate_report_paths(outputs_dir, workdir):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def publish_report(*, require: bool | None = None) -> str | None:
    env = dict(os.environ)
    context = load_optional_run_context(env)
    must = bool(env.get("RUN_ID", "").strip()) if require is None else require
    if context is None:
        if must:
            raise PublishError("RUN_ID/GITHUB_* are required to publish Midkernel report.md")
        return None

    try:
        config = load_config(env)
    except ConfigError as exc:
        raise PublishError(str(exc)) from exc

    found = find_report_path(config.outputs_dir, config.workdir)
    if found is None:
        if must:
            raise PublishError(
                f"report.md was not written (looked under {config.outputs_dir} and {config.workdir}). "
                "Refusing to upload a stub."
            )
        return None

    try:
        persist_report(Path(config.report_path), found.read_text(encoding="utf-8"))
        return upload_report(config, Path(config.report_path))
    except (ReportError, ArtifactError) as exc:
        raise PublishError(str(exc)) from exc


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )
    require = "--require" in sys.argv or bool(os.environ.get("RUN_ID", "").strip())
    try:
        uri = publish_report(require=require)
    except PublishError as exc:
        LOG.error("publish failed: %s", exc)
        sys.exit(1)
    if uri:
        LOG.info("uploaded %s", uri)
    sys.exit(0)


if __name__ == "__main__":
    main()
