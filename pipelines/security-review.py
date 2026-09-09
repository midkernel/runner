"""agentflow graph for Midkernel security-review.

The public playbook is still a single skill. Native Midkernel execution is
agentenv/agentflow launching this ECR image as ``target.image`` — one Kimi
node per scan (or more later). Do not nest RunTask from inside the Fargate
task.

Fill subnets / security_groups from infra outputs. Do not use agentflow
zero-config ({"kind":"ecs","region":"us-east-1"}).
"""

from agentflow import Graph, kimi
import os
import re


def require_approved_account(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{12}", value) is None:
        raise ValueError("MIDKERNEL_APPROVED_AWS_ACCOUNT_ID must be explicitly configured as 12 ASCII digits")
    return value


def ecs_agent_image() -> str:
    """Resolve the independently approved account only when constructing ECS targets."""
    account = require_approved_account(os.environ.get("MIDKERNEL_APPROVED_AWS_ACCOUNT_ID", ""))
    return f"{account}.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:latest"


IMAGE = ecs_agent_image()

ECS_TARGET = {
    "kind": "ecs",
    "region": "us-east-1",
    "cluster": "midkernel-dev",
    "image": IMAGE,
    "assign_public_ip": True,
    # Fill from midkernel/infra outputs. Do not use agentflow zero-config.
    "subnets": [],
    "security_groups": [],
}

with Graph("midkernel-security-review", working_dir=".") as g:
    kimi(
        task_id="security-review",
        prompt=(
            "Perform a /security-review on this project. "
            "Write the full review to /outputs/report.md including a "
            "`Findings: N` line."
        ),
        model="google/gemini-3.8-flash",
        tools="read_only",
        target=ECS_TARGET,
    )

print(g.to_json())
