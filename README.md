# midkernel/runner

ECR image that **agentenv/agentflow** launches as each ECS Fargate **node/agent**.

```text
489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents
```

This repo owns the **agent container**. Midkernel app / playbooks are the control plane. Infra already exists in `midkernel/infra` — do not recreate it here.

## What this image is

A valid [agentenv/agentflow](https://github.com/agentenv/agentflow) ECS node image: `bash`, `kimi`, and `agentflow` on `PATH`, plus `/workspace` and `/outputs`.

agentflow's ECS runner **overrides** the image entrypoint with:

```text
entryPoint: ["bash", "-c"]
command:    ["<auth_setup> && kimi --print --output-format stream-json --yolo -p …"]
```

(`agentflow/runners/ecs.py` at pin `09df0175`).

Hard lock is **OpenRouter**. Preferred harness is **Kimi CLI 1.49.0** (same pin as agentflow's bundled Dockerfile), wired to OpenRouter. OpenCode was only an example and is **not** installed — it is optional/unnecessary and is not prioritized. For `openai_legacy` providers, kimi-cli reads `OPENAI_API_KEY`. The node writes `~/.kimi/config.toml` and exports:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | kimi-cli OpenRouter (`openai_legacy`) |
| `OPENROUTER_API_KEY` | Midkernel / app contract |
| `KIMI_API_KEY` / `MOONSHOT_API_KEY` | agentflow `agent_auth_setup` for `kimi` |

Default model: `moonshotai/kimi-k3` (app `SCAN_MODEL_BY_PROFILE`). Config aliases also accept `kimi-k3` and `openrouter/moonshotai/kimi-k3` so agentflow `--model` resolves.

Fargate tasks are unprivileged. This image does **not** copy agentflow's DinD Dockerfile.

## `target.image` (app + playbooks)

Use this ECR repo as `target.image` on every Midkernel `kind: "ecs"` node. Do **not** use agentflow zero-config (`{"kind":"ecs","region":"us-east-1"}`).

```python
from agentflow import Graph, kimi

IMAGE = "489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:latest"

with Graph("midkernel-security-review", working_dir=".") as g:
    kimi(
        task_id="security-review",
        prompt="Perform a /security-review on this project. Write /outputs/report.md.",
        model="moonshotai/kimi-k3",
        target={
            "kind": "ecs",
            "region": "us-east-1",
            "cluster": "midkernel-dev",
            "image": IMAGE,
            "assign_public_ip": True,
            "subnets": ["…infra public_subnet_ids…"],
            "security_groups": ["…infra ecs_security_group_id…"],
        },
    )
```

See `pipelines/security-review.py`. Pin `:latest` or `:<sha>` from CI.

Stock agentflow ECS does not set `taskRoleArn`. Midkernel app must `RegisterTaskDefinition` with:

- `executionRoleArn`: `midkernel-dev-ecsTaskExecutionRole`
- `taskRoleArn`: `midkernel-dev-ecsTaskRole` (S3 + `GetSecretValue`)

## Artifact contract

Nodes write **`report.md`** (prefer `/outputs/report.md`). When `RUN_ID` is set, the node uploads to:

```text
s3://midkernel-dev-artifacts/runs/<RUN_ID>/report.md
```

via the task role (`s3:PutObject`, SSE-S3). Helpers:

- `midkernel-publish-report` — find + validate + upload
- `kimi` PATH wrapper — prepare OpenRouter, run real kimi, then publish if `RUN_ID` is set
- `BASH_ENV=/opt/midkernel/node-env.sh` — same prepare when agentflow uses `bash -c`

Missing, empty, or stub reports are **not** uploaded; the process exits non-zero.

## Secrets

Task role `GetSecretValue` (already on `midkernel-dev-ecsTaskRole`):

- `midkernel/dev/harness/openrouter-api-key`
- `midkernel/dev/harness/github-token`

Formats: raw string or JSON (`apiKey` / `token` / …). See `src/midkernel_runner/secrets.py`.

Per-run `GITHUB_TOKEN` from the app (installation token) wins over the static SM PAT.

`MIDKERNEL_LOCAL=1` uses `OPENROUTER_API_KEY` + `GITHUB_TOKEN` from the environment (laptop only).

The image never reads `AI_GATEWAY_API_KEY` / `VERCEL_OIDC_TOKEN`.

## Environment contract

Aligned with `midkernel/app` `src/lib/agentflow-contract.ts`. App names and runner names are both accepted.

| Variable | Required for Scan | App alias | Notes |
| --- | --- | --- | --- |
| `RUN_ID` | yes (artifacts) | — | `[A-Za-z0-9._:-]{1,128}` |
| `GITHUB_OWNER` | yes (clone) | — | |
| `GITHUB_NAME` | yes (clone) | — | |
| `PLAYBOOK_SLUG` | no | `PLAYBOOK` | default `security-review` |
| `SCAN_PROFILE` | no | `PROFILE` | `low` \| `balanced` \| `max` |
| `THREAT_PIN` | no | `THREAT` | max 80 chars |
| `GITHUB_REF` | no | — | shallow clone `--branch` |
| `GITHUB_TOKEN` | no | — | per-run installation token |
| `ARTIFACTS_BUCKET` | no | — | default `midkernel-dev-artifacts` |
| `ARTIFACTS_PREFIX` | no | — | default `runs/` |
| `ARTIFACTS_KEY` | no | — | exact S3 key if the app sets it |
| `OPENROUTER_MODEL` | no | `MODEL` | default `moonshotai/kimi-k3` |
| `AWS_REGION` | no | — | default `us-east-1` |
| `OPENROUTER_SECRET_ID` | no | — | `midkernel/dev/harness/openrouter-api-key` |
| `GITHUB_TOKEN_SECRET_ID` | no | — | `midkernel/dev/harness/github-token` |

A **generic** agentflow node (no `RUN_ID`) still runs `kimi` with OpenRouter if a key is already in the environment.

## Suggested ECS task definition

`examples/task-definition.json` / `examples/runtask.json`. Family `midkernel-dev-scan` or app-registered `midkernel-agentflow-agents`. Logs: `/agentflow`. Capacity: Fargate Spot preferred.

| Profile | cpu | memory |
| --- | --- | --- |
| `low` | `1024` | `2048` |
| `balanced` | `2048` | `4096` |
| `max` | `4096` | `8192` |

When the app RunTasks **without** a command override and `RUN_ID` is set, `CMD midkernel-default` runs the Kimi scan helper (clone playbook repo → kimi → upload). Native agentflow overrides that with `bash -c` + `kimi`.

## CI / publish

`.github/workflows/ci.yml` is unchanged: PR/push unit tests + `docker build`; `main` / `workflow_dispatch` OIDC push `:<sha>` and `:latest` to ECR.

GitHub immutable OIDC `sub` for this repo (created 2026-09-04): `repo:midkernel@324066512/runner@1357082961:*`. Infra must trust that prefix. This repo does not change IAM.

## Local

```bash
pip install -e ".[dev]"
pytest -q
```

```bash
docker build -t midkernel-agentflow-agents:local .
docker run --rm midkernel-agentflow-agents:local agentflow --help
docker run --rm midkernel-agentflow-agents:local kimi --help
```

## Layout

```
src/midkernel_runner/   # node prepare, Kimi/OpenRouter, report, S3
scripts/                # agentflow entrypoint, BASH_ENV, kimi wrapper
pipelines/              # target.image graph for app/playbooks
examples/               # task def + RunTask shapes
Dockerfile
```

MIT.
