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
from midkernel_runner.kimi import (
    export_kimi_openrouter_env,
    graph_kimi_share_dir,
    write_kimi_openrouter_config,
)
from midkernel_runner.mode import IMAGE_KIMI_BIN, apply_graph_mode_flags

LOG = logging.getLogger("midkernel.graph")
USER_AGENT = "midkernel-runner"

GRAPH_KIMI_SHIM = """#!/bin/sh
# In-task graph PATH shim. agentflow / playbooks may still exec `kimi`.
# Never fall through to /usr/local/bin/kimi (report-enforcing wrapper).
BIN="${{MIDKERNEL_KIMI_BIN:-{kimi_bin}}}"
exec "$BIN" "$@"
"""

# playbooks _node_io.wrap_kimi execs MIDKERNEL_KIMI_BIN (not PATH). This
# front rewrites --config so kimi.bin loads the OpenRouter file.
# Do not mention KIMI_REAL_BIN / midkernel-publish-report (is_report_md_wrapper).
GRAPH_KIMI_FRONT = """#!/bin/sh
exec python3 -m midkernel_runner.kimi_graph_bin "$@"
"""

# agentflow doctor kimi_ready: `[executable, "--version"]` in the prepared
# local shell. Playbooks sets executable to pipelines/_node_io.py, which
# (until playbooks handles it) fell through to wrap_kimi and failed ×12.
NODE_IO_VERSION_MARKER = 'argv[0] in {"--version"'
NODE_IO_VERSION_SNIPPET = '''    if argv and argv[0] in {"--version", "-V", "-h", "--help"}:
        binary = real_kimi_bin()
        os.execvp(binary, [binary, *argv])

'''
NODE_IO_ARGV_LINE = "    argv = list(sys.argv[1:] if argv is None else argv)\n"


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


def graph_kimi_shim_dir(config: RunConfig) -> Path:
    return Path(config.workdir) / ".midkernel" / "bin"


def install_graph_kimi_shim(config: RunConfig, kimi_bin: str) -> Path:
    """Put a ``kimi`` shim ahead of the image wrapper on PATH."""
    directory = graph_kimi_shim_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / "kimi"
    shim.write_text(GRAPH_KIMI_SHIM.format(kimi_bin=kimi_bin), encoding="utf-8")
    shim.chmod(0o755)
    return shim


def graph_kimi_front_path(config: RunConfig) -> Path:
    return graph_kimi_shim_dir(config) / "kimi-openrouter"


def install_graph_kimi_front(config: RunConfig) -> Path:
    """``MIDKERNEL_KIMI_BIN`` target: rewrite playbooks ``--config``, exec kimi.bin."""
    directory = graph_kimi_shim_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    front = graph_kimi_front_path(config)
    front.write_text(GRAPH_KIMI_FRONT, encoding="utf-8")
    front.chmod(0o755)
    return front


def apply_graph_env(config: RunConfig, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Export the playbooks node-I/O contract onto the process env.

    Intermediate graph nodes must not hit the PATH ``kimi`` wrapper
    (requires ``report.md``) or BASH_ENV target clone (WORKDIR already
    holds ``.midkernel/playbooks``).
    """
    env = os.environ if environ is None else environ
    env["WORKDIR"] = config.workdir
    env["OUTPUTS_DIR"] = config.outputs_dir
    apply_graph_mode_flags(env)
    pinned = (env.get("MIDKERNEL_KIMI_BIN") or "").strip()
    already_front = pinned.endswith("kimi-openrouter") or "kimi_graph_bin" in pinned
    if already_front:
        env["MIDKERNEL_GRAPH_KIMI_BIN"] = (
            (env.get("MIDKERNEL_GRAPH_KIMI_BIN") or "").strip() or IMAGE_KIMI_BIN
        )
    elif pinned and pinned != IMAGE_KIMI_BIN:
        env["MIDKERNEL_GRAPH_KIMI_BIN"] = pinned
    else:
        env["MIDKERNEL_GRAPH_KIMI_BIN"] = (
            (env.get("MIDKERNEL_GRAPH_KIMI_BIN") or "").strip() or IMAGE_KIMI_BIN
        )
    Path(config.workdir).mkdir(parents=True, exist_ok=True)
    Path(config.outputs_dir).mkdir(parents=True, exist_ok=True)
    (Path(config.workdir) / ".midkernel").mkdir(parents=True, exist_ok=True)
    front = install_graph_kimi_front(config)
    env["MIDKERNEL_KIMI_BIN"] = str(front)
    shim = install_graph_kimi_shim(config, env["MIDKERNEL_KIMI_BIN"])
    shim_dir = str(shim.parent)
    current_path = env.get("PATH") or os.environ.get("PATH") or ""
    parts = [part for part in current_path.split(os.pathsep) if part and part != shim_dir]
    env["PATH"] = os.pathsep.join([shim_dir, *parts])
    # Playbooks ecs-in-task / _node_io set BASH_ENV=/dev/null and exec
    # kimi.bin. Secrets loaded by prepare_node must stay visible as both
    # env vars (dummy type=kimi fallback) and KIMI_SHARE_DIR/config.toml
    # (openai_legacy when --config is absent or HOME was overwritten).
    share = graph_kimi_share_dir(config.workdir)
    env["KIMI_SHARE_DIR"] = str(share)
    api_key = (
        env.get("OPENROUTER_API_KEY") or env.get("OPENAI_API_KEY") or env.get("KIMI_API_KEY") or ""
    ).strip()
    if api_key:
        model = (
            env.get("OPENROUTER_MODEL") or env.get("MODEL") or config.openrouter_model
        ).strip()
        env.update(
            export_kimi_openrouter_env(env, api_key, model=model, share_dir=share)
        )
        write_kimi_openrouter_config(api_key, model, share_dir=share, environ=env)
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
    prepare_playbooks_kimi_executable(dest)
    return dest


def node_io_path(root: Path) -> Path:
    return Path(root) / "pipelines" / "_node_io.py"


def prepare_playbooks_kimi_executable(root: Path) -> Path | None:
    """Make playbooks ``_node_io.py`` pass agentflow ``kimi_ready``.

    Doctor execs ``<executable> --version`` (not PATH ``kimi``). The helper
    must be +x (direct exec) and must answer ``--version`` by exec'ing
    ``MIDKERNEL_KIMI_BIN`` / ``kimi.bin`` — not wrap_kimi/start_node.
    Idempotent if playbooks already has the probe.
    """
    path = node_io_path(root)
    if not path.is_file():
        return None
    path.chmod(path.stat().st_mode | 0o111)
    text = path.read_text(encoding="utf-8")
    if NODE_IO_VERSION_MARKER not in text:
        if NODE_IO_ARGV_LINE not in text:
            LOG.warning("playbooks _node_io.py missing main() argv line; cannot insert --version probe")
        else:
            path.write_text(text.replace(NODE_IO_ARGV_LINE, NODE_IO_ARGV_LINE + NODE_IO_VERSION_SNIPPET, 1), encoding="utf-8")
            LOG.info("patched playbooks _node_io.py to exec MIDKERNEL_KIMI_BIN on --version")
    return path


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
    prepare_playbooks_kimi_executable(root)
    pipeline = root / "pipelines" / f"{config.playbook_slug}.py"
    in_task = root / "scripts" / "ecs-in-task.sh"
    if in_task.is_file() and os.access(in_task, os.X_OK):
        cmd = ["bash", str(in_task)]
        cwd = str(root)
    else:
        cmd = [agentflow, "run", str(pipeline)]
        cwd = str(root)
    LOG.info(
        "agentflow graph playbook=%s pipeline=%s workdir=%s node_io=%s target=%s "
        "run_timeout=%ss node_timeout=%ss",
        config.playbook_slug,
        pipeline,
        config.workdir,
        os.environ.get("MIDKERNEL_NODE_IO"),
        os.environ.get("MIDKERNEL_AGENTFLOW_TARGET"),
        config.run_timeout_seconds,
        config.timeout_seconds,
    )
    try:
        result = run(
            cmd,
            check=False,
            cwd=cwd,
            env=os.environ.copy(),
            timeout=config.run_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise GraphError(
            f"playbooks graph exceeded run timeout {config.run_timeout_seconds}s "
            f"(per-node {config.timeout_seconds}s; "
            f"{config.playbook_slug} is a serial graph, not one kimi call)"
        ) from exc
    code = int(result.returncode)
    try:
        from midkernel_runner.artifacts import publish_openrouter_generations

        uri = publish_openrouter_generations(config)
        if uri:
            LOG.info("uploaded %s", uri)
    except Exception as exc:
        LOG.warning("openrouter generations publish skipped: %s", exc)
    if code != 0:
        raise GraphError(f"playbooks graph exited {code}")
    return 0
