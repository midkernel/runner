"""agentflow graph for Midkernel security-review.

The public playbook is still a single skill. Native Midkernel execution is
agentenv/agentflow launching this ECR image as ``target.image`` — one Kimi
node per scan (or more later). Do not nest RunTask from inside the Fargate
task.

Fill subnets / security_groups from infra outputs. Do not use agentflow
zero-config ({"kind":"ecs","region":"us-east-1"}).
"""

from agentflow import Graph, kimi

IMAGE = "489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:latest"

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
        model="moonshotai/kimi-k3",
        tools="read_only",
        target=ECS_TARGET,
    )

print(g.to_json())
