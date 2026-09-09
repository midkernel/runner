import os
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "scripts" / "entrypoint.sh"


def _bin(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _run_entrypoint(tmp_path: Path, args: list[str], extra_env: dict[str, str]):
    _bin(tmp_path, "midkernel-runner", "echo RUNNER_RAN; echo CLONE=${MIDKERNEL_CLONE_TARGET:-unset}")
    _bin(tmp_path, "midkernel-node-prepare", "echo PREPARE_RAN >&2; exit 1")
    _bin(tmp_path, "agentflow", "echo AGENTFLOW_HELP")
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}/usr/bin{os.pathsep}/bin",
    }
    for key in (
        "MIDKERNEL_NODE_READY",
        "MIDKERNEL_CLONE_TARGET",
        "MIDKERNEL_AGENTFLOW_TARGET",
        "MIDKERNEL_REQUIRE_REPORT",
        "MIDKERNEL_NODE_ENV",
        "RUN_ID",
    ):
        env.pop(key, None)
    env.update(extra_env)
    return subprocess.run(
        ["sh", str(ENTRYPOINT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_default_cmd_with_run_id_skips_node_prepare(tmp_path):
    """GOAL cmtue7rv90003l104ysmh21eu: entrypoint must not clone-prepare."""
    result = _run_entrypoint(
        tmp_path,
        ["midkernel-default"],
        {"RUN_ID": "cmtue7rv90003l104ysmh21eu"},
    )
    assert result.returncode == 0, result.stderr
    assert "RUNNER_RAN" in result.stdout
    assert "PREPARE_RAN" not in result.stderr
    assert "PREPARE_RAN" not in result.stdout


def test_empty_argv_with_run_id_skips_node_prepare(tmp_path):
    result = _run_entrypoint(tmp_path, [], {"RUN_ID": "cmtue7rv90003l104ysmh21eu"})
    assert result.returncode == 0, result.stderr
    assert "RUNNER_RAN" in result.stdout
    assert "PREPARE_RAN" not in result.stderr


def test_explicit_midkernel_runner_skips_node_prepare(tmp_path):
    result = _run_entrypoint(
        tmp_path,
        ["midkernel-runner"],
        {"RUN_ID": "cmtue7rv90003l104ysmh21eu"},
    )
    assert result.returncode == 0, result.stderr
    assert "RUNNER_RAN" in result.stdout
    assert "PREPARE_RAN" not in result.stderr


def test_bash_c_still_sources_node_env(tmp_path):
    node_env = tmp_path / "node-env.sh"
    node_env.write_text("echo NODE_ENV_SOURCED\n", encoding="utf-8")
    result = _run_entrypoint(
        tmp_path,
        ["-c", "echo AFTER_C"],
        {
            "RUN_ID": "cmtue7rv90003l104ysmh21eu",
            "MIDKERNEL_NODE_ENV": str(node_env),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "NODE_ENV_SOURCED" in result.stdout
    assert "AFTER_C" in result.stdout
    assert "RUNNER_RAN" not in result.stdout
