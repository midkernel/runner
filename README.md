# midkernel/runner

Public Docker image that **ECS Fargate Spot** runs for a Midkernel Scan one-shot.

ECR:

`489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents`

This repo owns the **agent container**. The Midkernel app (upcoming ECS executor) is the control plane: `RunTask` + `PassRole` via `midkernel-dev-agentflow-control-plane`. Infra already exists in `midkernel/infra` — do not recreate it here.

## What runs inside the task

1. Read run context from environment (`RUN_ID`, repo, playbook, profile).
2. Load harness secrets with the **ECS task role** (`GetSecretValue`):
   - `midkernel/dev/harness/openrouter-api-key`
   - `midkernel/dev/harness/github-token`
3. Clone `github.com/$GITHUB_OWNER/$GITHUB_NAME` with that GitHub token.
4. Fetch the playbook prompt from public [`midkernel/playbooks`](https://github.com/midkernel/playbooks) (`<slug>.md`, default `security-review`).
5. Run **OpenCode** (`opencode run`) against the clone, OpenRouter only, default model **Kimi K3 Max** (`openrouter/moonshotai/kimi-k3` + `--variant max` on the `max` profile).
6. Require a real **`/outputs/report.md`**. Upload to  
   `s3://midkernel-dev-artifacts/runs/<RUN_ID>/report.md`.  
   If the report is missing, empty, or a stub, the container **exits non-zero** and uploads nothing.

Stdout is the CloudWatch stream (log group `/agentflow`).

## Why OpenCode one-shot (and still agentflow)

James asked for real [agentenv/agentflow](https://github.com/agentenv/agentflow) on real ECS Fargate Spot, OpenCode harness, OpenRouter only — no stubs, no AI Gateway shortcut.

**Scan `security-review` is a single-skill one-shot** (playbook frontmatter: `kind: single-skill`; body is “Perform a /security-review on this project”). It is not a multi-node graph.

agentflow’s `kind: "ecs"` runner is a **control plane**: it `RegisterTaskDefinition` + `RunTask` and streams `/agentflow/{node.id}`. Midkernel already has that role on the app side, plus explicit cluster/subnets/SG (infra forbids agentflow zero-config). Running agentflow *inside* this task to launch **nested** Fargate tasks would double Spot cost and ignore that contract.

agentflow’s adapters are Codex / Claude / Kimi CLI / Pi — **there is no OpenCode adapter**. Midkernel’s lock is OpenCode + OpenRouter.

So this image:

- **Does** real agent execution: OpenCode talks to OpenRouter (Kimi K3), reviews the cloned repo, writes `report.md`.
- **Installs** `agentflow` on `PATH` so the same ECR repo is a valid `target.image` for later multi-node playbooks (see `pipelines/security-review.py`).
- **Does not** copy agentflow’s DinD Dockerfile (privileged Docker). Fargate Spot tasks here are unprivileged; the task SG has no Docker socket.

The app’s current v0 executor uses Vercel AI Gateway. **This image does not.** It never reads `AI_GATEWAY_API_KEY` / `VERCEL_OIDC_TOKEN`.

## Environment contract

The app’s ECS executor should pass these on the container (or task override). Coordinate names with `midkernel/app`.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `RUN_ID` | yes | — | Artifact key segment. `[A-Za-z0-9._:-]{1,128}` |
| `GITHUB_OWNER` | yes | — | Target repo owner |
| `GITHUB_NAME` | yes | — | Target repo name |
| `PLAYBOOK_SLUG` | no | `security-review` | Fetched as `midkernel/playbooks/<slug>.md` |
| `SCAN_PROFILE` | no | `balanced` | `low` \| `balanced` \| `max` |
| `THREAT_PIN` | no | — | Optional pin, max 80 chars. Not a fourth profile |
| `GITHUB_REF` | no | default branch | Shallow clone `--branch` |
| `GITHUB_TOKEN` | no | from SM | Per-run **installation token** from the app wins over the static SM secret |
| `ARTIFACTS_BUCKET` | no | `midkernel-dev-artifacts` | |
| `ARTIFACTS_PREFIX` | no | `runs/` | Final key `runs/<RUN_ID>/report.md` |
| `OPENROUTER_MODEL` | no | `openrouter/moonshotai/kimi-k3` | OpenCode `provider/model` |
| `OPENROUTER_VARIANT` | no | profile map | `low`→`low`, `balanced`→`medium`, `max`→`max` (Kimi K3 Max effort) |
| `AWS_REGION` | no | `us-east-1` | |
| `OPENROUTER_SECRET_ID` | no | `midkernel/dev/harness/openrouter-api-key` | |
| `GITHUB_TOKEN_SECRET_ID` | no | `midkernel/dev/harness/github-token` | |
| `AGENT_TIMEOUT_SECONDS` | no | 15m / 30m / 60m | By profile |
| `MIDKERNEL_LOCAL` | no | — | `1` = use `OPENROUTER_API_KEY` + `GITHUB_TOKEN` from env (laptop only) |

### Secret formats

Secrets Manager `SecretString` (AWSCURRENT), never in git / never in the image.

**openrouter-api-key**

- Raw key (`sk-or-v1-...`), or
- JSON: `apiKey` / `api_key` / `OPENROUTER_API_KEY` / `key`

**github-token**

- Raw PAT or GitHub App **installation** token (`ghp_`, `ghs_`, `github_pat_`, …), or
- JSON: `token` / `github_token` / `GITHUB_TOKEN` / `installationToken`

Clone URL: `https://x-access-token:<token>@github.com/<owner>/<name>.git`.

## Suggested ECS task definition (Eng / IT)

No infra PR from this repo. Register (or have the app `RegisterTaskDefinition` per run) against **existing** roles, cluster, public subnets, and `/agentflow`.

See `examples/task-definition.json` and `examples/runtask.json`.

| Profile | cpu | memory | capacity |
| --- | --- | --- | --- |
| `low` | `1024` (1 vCPU) | `2048` | Fargate Spot preferred |
| `balanced` | `2048` | `4096` | same |
| `max` | `4096` | `8192` | same |

```text
family:              midkernel-dev-scan
networkMode:         awsvpc
compatibilities:     FARGATE
executionRoleArn:    arn:aws:iam::489470371031:role/midkernel-dev-ecsTaskExecutionRole
taskRoleArn:         arn:aws:iam::489470371031:role/midkernel-dev-ecsTaskRole
container name:      agent
image:               489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:<sha|latest>
awslogs-group:       /agentflow
awslogs-stream-prefix: scan
assignPublicIp:      ENABLED
cluster:             midkernel-dev
capacityProvider:    FARGATE_SPOT weight 4, FARGATE weight 1
```

Fill `subnets` / `securityGroups` from infra outputs (`public_subnet_ids`, `ecs_security_group_id`). Do **not** use agentflow zero-config (`{"kind":"ecs","region":"us-east-1"}`).

App RunTask should override `RUN_ID`, `GITHUB_OWNER`, `GITHUB_NAME`, `PLAYBOOK_SLUG`, `SCAN_PROFILE`, optional `THREAT_PIN` / `GITHUB_REF` / `GITHUB_TOKEN`.

## CI / publish

`.github/workflows/ci.yml`:

- PR / push: unit tests + `docker build` (no AWS).
- `main` and `workflow_dispatch`: OIDC assume `arn:aws:iam::489470371031:role/midkernel-github-actions`, push `:<sha>` and `:latest`.

**IT:** that role’s trust currently lists `midkernel/infra` and `midkernel/app`. Add `repo:midkernel/runner:*` before the first successful push. This repo does not change IAM.

Manual push (after `aws ecr get-login-password`):

```bash
docker build -t 489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:dev .
docker push 489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents:dev
```

## Local (no AWS)

```bash
pip install -e ".[dev]"
pytest -q
```

```bash
docker build -t midkernel-agentflow-agents:local .
docker run --rm \
  -e MIDKERNEL_LOCAL=1 \
  -e RUN_ID=local-1 \
  -e GITHUB_OWNER=midkernel \
  -e GITHUB_NAME=playbooks \
  -e OPENROUTER_API_KEY \
  -e GITHUB_TOKEN \
  midkernel-agentflow-agents:local
```

Local mode still requires a real OpenCode/OpenRouter pass to write `report.md`. S3 upload will fail without a task role — that is a failed run, not a stub upload.

## Layout

```
src/midkernel_runner/   # env, SM, clone, OpenCode, report, S3
scripts/entrypoint.sh
pipelines/              # documented agentflow graph; not the Scan CMD
examples/               # task def + RunTask shapes for the app
Dockerfile
```

MIT.
