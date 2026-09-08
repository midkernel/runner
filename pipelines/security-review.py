"""Future multi-node shape — not used by the Midkernel Scan one-shot.

The public security-review playbook is a single skill. The ECS image default
CMD runs OpenCode directly (see src/midkernel_runner/opencode.py).

If Eng later wants agentflow to *orchestrate* additional playbooks, keep this
file as the graph that the *app control plane* would `agentflow run` from a
laptop/CI — not from inside the Fargate task (that would nest RunTask).

The image published to midkernel-agentflow-agents is the `target.image`.
"""

from agentflow import Graph, pi
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

# OpenRouter via Pi is agentflow's stock path. Midkernel Scan v0 uses OpenCode
# instead (Operator lock). This graph is documentation / a later opt-in.
with Graph("midkernel-security-review", working_dir=".") as g:
    pi(
        task_id="security-review",
        prompt="Perform a /security-review on this project",
        model="openrouter/moonshotai/kimi-k3",
        tools="read_only",
        target={
            "kind": "ecs",
            "region": "us-east-1",
            "cluster": "midkernel-dev",
            "image": IMAGE,
            "assign_public_ip": True,
            # Fill subnets / security_groups from infra outputs. Do not use
            # agentflow zero-config ({"kind":"ecs","region":"us-east-1"}).
            "subnets": [],
            "security_groups": [],
        },
    )

print(g.to_json())
