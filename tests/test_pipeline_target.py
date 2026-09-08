"""Inspect example graph construction with no ECS or agent execution."""
import json
from pathlib import Path
import runpy
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipelines" / "security-review.py"
ACCOUNT = "123456789012"  # Synthetic fixture; never deploy.


def load_example(monkeypatch, account):
    if account is None:
        monkeypatch.delenv("MIDKERNEL_APPROVED_AWS_ACCOUNT_ID", raising=False)
    else:
        monkeypatch.setenv("MIDKERNEL_APPROVED_AWS_ACCOUNT_ID", account)
    calls = []
    class Graph:
        def __init__(self, *args, **kwargs):
            calls.append(("graph", args, kwargs))
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def to_json(self): return "{}"
    stub = types.ModuleType("agentflow")
    stub.Graph = Graph
    stub.kimi = lambda **kwargs: calls.append(("kimi", (), kwargs))
    monkeypatch.setitem(sys.modules, "agentflow", stub)
    return calls


def test_pipeline_uses_kimi_and_configured_ecs_image(monkeypatch):
    calls = load_example(monkeypatch, ACCOUNT)
    runpy.run_path(str(PIPELINE))
    assert calls[0] == ("graph", ("midkernel-security-review",), {"working_dir": "."})
    assert len(calls) == 2
    kind, _, node = calls[1]
    assert kind == "kimi"
    assert node["tools"] == "read_only"
    assert node["task_id"] == "security-review"
    assert node["target"] == {
        "kind": "ecs", "region": "us-east-1", "cluster": "midkernel-dev",
        "image": f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:latest",
        "assign_public_ip": True, "subnets": [], "security_groups": [],
    }


@pytest.mark.parametrize("account", [None, "", "123", "1" * 13, ACCOUNT + "\n", " " + ACCOUNT, "１２３４５６７８９０１２"])
def test_bad_or_missing_account_fails_before_graph_creation(monkeypatch, account):
    calls = load_example(monkeypatch, account)
    with pytest.raises(ValueError, match="MIDKERNEL_APPROVED_AWS_ACCOUNT_ID"):
        runpy.run_path(str(PIPELINE))
    assert calls == []


def test_task_definition_requires_explicit_private_rendering():
    task = json.loads((ROOT / "examples/task-definition.json").read_text())
    assert task["executionRoleArn"] == "REPLACE_EXECUTION_ROLE_ARN"
    assert task["taskRoleArn"] == "REPLACE_TASK_ROLE_ARN"
    assert task["containerDefinitions"][0]["image"] == "REPLACE_AGENT_IMAGE"
