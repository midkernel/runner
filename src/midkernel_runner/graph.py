"""Run midkernel/playbooks Python graphs in-task (graph.json + nodes/*).

Dogfood ``cmtu8jtu00003l1043oi0at41`` only uploaded report.md because
``midkernel-runner`` fetched ``<slug>.md`` and ran one kimi. Playbooks
``_node_io.py`` never imported, so S3 had no ``graph.json`` / ``nodes/``.

When ``pipelines/<slug>.py`` exists on the playbooks repo, clone it and
``agentflow run`` with ``MIDKERNEL_AGENTFLOW_TARGET=local`` so the already
launched Fargate task writes the ReactFlow objects next to report.md.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from midkernel_runner.config import RunConfig

LOG = logging.getLogger("midkernel.graph")
USER_AGENT = "midkernel-runner"
IMAGE_KIMI_BIN = "/opt/midkernel/kimi.bin"


class GraphError(RuntimeError):
    """Playbooks graph could not be executed."""


def pipeline_raw_url(config: RunConfig) -> str:
    return (
        f"https://raw.githubusercontent.com/{config.playbooks_owner}/"
        f"{config.playbooks_name}/{config.playbooks_ref}/"
        f"pipelines/{config.playbook_slug}.py"
    )


def playbooks_clone_url(config: RunConfig) -> str:
    return f"https://github.com/{config.playbooks_owner}/{config.playbooks_name}.git"


def force_md_kimi(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get("MIDKERNEL_FORCE_MD_KIMI") or "").strip() == "1"


def force_graph(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get("MIDKERNEL_FORCE_GRAPH") or "").strip() == "1"


def probe_pipeline(config: RunConfig, *, fetch_status=None) -> str:
    """``found`` / ``missing`` (HTTP 404) / ``unknown`` (network or non-404).

    GitHub raw often rejects HEAD (403/405). A failed probe must not send a
    GOAL run back down the single-kimi path — that is how
    ``cmtu8jtu00003l1043oi0at41`` uploaded only ``report.md``.
    """
    url = pipeline_raw_url(config)
    if fetch_status is not None:
        code = int(fetch_status(url))
        if 200 <= code < 300:
            return "found"
        if code == 404:
            return "missing"
        return "unknown"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=15) as response:
            return "found" if 200 <= int(response.status) < 300 else "unknown"
    except HTTPError as exc:
        if exc.code == 404:
            return "missing"
        return "unknown"
    except URLError:
        return "unknown"


def pipeline_exists(config: RunConfig, *, fetch_status=None) -> bool:
    """True when playbooks publishes ``pipelines/<slug>.py`` (HTTP 200)."""
    return probe_pipeline(config, fetch_status=fetch_status) == "found"


def should_run_playbooks_graph(config: RunConfig, *, fetch_status=None) -> bool:
    if force_md_kimi():
        return False
    if force_graph():
        return True
    return probe_pipeline(config, fetch_status=fetch_status) != "missing"


def apply_graph_env(config: RunConfig, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Export the playbooks node-I/O contract onto the process env."""
    env = os.environ if environ is None else environ
    env["WORKDIR"] = config.workdir
    env["OUTPUTS_DIR"] = config.outputs_dir
    env["MIDKERNEL_NODE_IO"] = env.get("MIDKERNEL_NODE_IO") or "1"
    env["MIDKERNEL_AGENTFLOW_TARGET"] = env.get("MIDKERNEL_AGENTFLOW_TARGET") or "local"
    if Path(IMAGE_KIMI_BIN).is_file() and not (env.get("MIDKERNEL_KIMI_BIN") or "").strip():
        env["MIDKERNEL_KIMI_BIN"] = IMAGE_KIMI_BIN
    Path(config.workdir).mkdir(parents=True, exist_ok=True)
    Path(config.outputs_dir).mkdir(parents=True, exist_ok=True)
    (Path(config.workdir) / ".midkernel").mkdir(parents=True, exist_ok=True)
    return env


def playbooks_dir(config: RunConfig) -> Path:
    override = (os.environ.get("MIDKERNEL_PLAYBOOKS_DIR") or "").strip()
    if override:
        return Path(override)
    return Path(config.workdir) / ".midkernel" / "playbooks"


def clone_playbooks(
    config: RunConfig,
    dest: Path,
    *,
    run: callable = subprocess.run,
) -> Path:
    if (dest / "pipelines" / f"{config.playbook_slug}.py").is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        config.playbooks_ref,
        playbooks_clone_url(config),
        str(dest),
    ]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = run(cmd, check=False, capture_output=True, text=True, env=env, timeout=180)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise GraphError(f"git clone playbooks failed: {stderr}")
    pipeline = dest / "pipelines" / f"{config.playbook_slug}.py"
    if not pipeline.is_file():
        raise GraphError(f"cloned playbooks missing {pipeline}")
    return dest


def run_playbooks_graph(
    config: RunConfig,
    *,
    run: callable = subprocess.run,
    which: callable = shutil.which,
    clone=clone_playbooks,
) -> int:
    apply_graph_env(config)
    agentflow = which("agentflow")
    if not agentflow:
        raise GraphError("agentflow is not on PATH")
    root = clone(config, playbooks_dir(config))
    pipeline = root / "pipelines" / f"{config.playbook_slug}.py"
    in_task = root / "scripts" / "ecs-in-task.sh"
    if in_task.is_file() and os.access(in_task, os.X_OK):
        cmd = ["bash", str(in_task)]
        cwd = str(root)
    else:
        cmd = [agentflow, "run", str(pipeline)]
        cwd = str(root)
    LOG.info(
        "agentflow graph playbook=%s pipeline=%s workdir=%s node_io=%s target=%s",
        config.playbook_slug,
        pipeline,
        config.workdir,
        os.environ.get("MIDKERNEL_NODE_IO"),
        os.environ.get("MIDKERNEL_AGENTFLOW_TARGET"),
    )
    result = run(
        cmd,
        check=False,
        cwd=cwd,
        env=os.environ.copy(),
        timeout=config.timeout_seconds,
    )
    code = int(result.returncode)
    if code != 0:
        raise GraphError(f"playbooks graph exited {code}")
    return 0
