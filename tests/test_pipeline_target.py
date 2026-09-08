from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "pipelines" / "security-review.py"


def test_pipeline_uses_kimi_and_ecr_target_image():
    text = PIPELINE.read_text(encoding="utf-8")
    assert "from agentflow import Graph, kimi" in text
    assert "kimi(" in text
    assert '"kind": "ecs"' in text
    assert "midkernel-agentflow-agents:latest" in text
    assert "489470371031.dkr.ecr.us-east-1.amazonaws.com" in text
    assert "opencode" not in text.lower()
