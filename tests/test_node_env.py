import os
import subprocess
from pathlib import Path

NODE_ENV = Path(__file__).resolve().parents[1] / "scripts" / "node-env.sh"


def test_node_env_skips_publish_trap_in_graph_mode():
    env = {
        **os.environ,
        "RUN_ID": "cmtuavpvs0003ib04bfyr7roc",
        "MIDKERNEL_AGENTFLOW_TARGET": "local",
        "MIDKERNEL_REQUIRE_REPORT": "0",
    }
    env.pop("MIDKERNEL_NODE_READY", None)
    env.pop("MIDKERNEL_PUBLISH_TRAP", None)
    # Isolate from image helpers so sourcing only evaluates the trap policy.
    env["PATH"] = "/usr/bin:/bin"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f". '{NODE_ENV}' && echo TRAP=${{MIDKERNEL_PUBLISH_TRAP:-unset}} READY=${{MIDKERNEL_NODE_READY:-unset}}",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "TRAP=unset" in result.stdout
    assert "READY=unset" in result.stdout
