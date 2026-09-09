import os
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "kimi-wrapper.sh"


def _run_wrapper(tmp_path, extra_env, *, real_exit=0):
    real = tmp_path / "kimi.bin"
    real.write_text(f"#!/bin/sh\necho ran-real\nexit {real_exit}\n", encoding="utf-8")
    real.chmod(0o755)
    publish = tmp_path / "midkernel-publish-report"
    publish.write_text("#!/bin/sh\necho PUBLISH_CALLED >&2\nexit 1\n", encoding="utf-8")
    publish.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "MIDKERNEL_KIMI_BIN": str(real),
        "KIMI_REAL_BIN": str(real),
        "MIDKERNEL_NODE_READY": "1",
        "RUN_ID": "cmtuavpvs0003ib04bfyr7roc",
    }
    for key in (
        "MIDKERNEL_AGENTFLOW_TARGET",
        "MIDKERNEL_REQUIRE_REPORT",
        "MIDKERNEL_SKIP_REPORT_PUBLISH",
        "MIDKERNEL_CLONE_TARGET",
    ):
        env.pop(key, None)
    env.update(extra_env)
    return subprocess.run(
        ["sh", str(WRAPPER), "--print"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_wrapper_skips_report_require_in_graph_mode(tmp_path):
    result = _run_wrapper(tmp_path, {"MIDKERNEL_AGENTFLOW_TARGET": "local"})
    assert result.returncode == 0, result.stderr
    assert "ran-real" in result.stdout
    assert "PUBLISH_CALLED" not in result.stderr


def test_wrapper_skips_report_require_when_flag_off(tmp_path):
    result = _run_wrapper(tmp_path, {"MIDKERNEL_REQUIRE_REPORT": "0"})
    assert result.returncode == 0, result.stderr
    assert "PUBLISH_CALLED" not in result.stderr


def test_wrapper_requires_report_for_single_kimi(tmp_path):
    result = _run_wrapper(tmp_path, {})
    assert result.returncode == 1
    assert "PUBLISH_CALLED" in result.stderr
    assert "ran-real" in result.stdout


def test_wrapper_prefers_midkernel_kimi_bin(tmp_path):
    other = tmp_path / "other.bin"
    other.write_text("#!/bin/sh\necho OTHER\nexit 0\n", encoding="utf-8")
    other.chmod(0o755)
    result = _run_wrapper(
        tmp_path,
        {
            "MIDKERNEL_AGENTFLOW_TARGET": "local",
            "MIDKERNEL_KIMI_BIN": str(other),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "OTHER" in result.stdout
