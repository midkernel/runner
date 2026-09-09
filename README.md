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

When `RUN_ID` is set and midkernel/playbooks has ``pipelines/<PLAYBOOK>.py``, **`midkernel-runner` clones playbooks and runs the Python graph in-task** (`MIDKERNEL_AGENTFLOW_TARGET=local`, `MIDKERNEL_NODE_IO=1`, `WORKDIR=/workspace`, `MIDKERNEL_KIMI_BIN=/opt/midkernel/kimi.bin`). That is what writes:

```text
s3://midkernel-dev-artifacts/runs/<RUN_ID>/report.md
s3://midkernel-dev-artifacts/runs/<RUN_ID>/graph.json
s3://midkernel-dev-artifacts/runs/<RUN_ID>/nodes/<nodeId>/prompt.md
s3://midkernel-dev-artifacts/runs/<RUN_ID>/nodes/<nodeId>/output.md
s3://midkernel-dev-artifacts/runs/<RUN_ID>/nodes/<nodeId>/meta.json
```

Dogfood `cmtu8jtu00003l1043oi0at41` only uploaded `report.md` because this image used to fetch `<slug>.md` and run one kimi — `_node_io.py` never ran. `MIDKERNEL_FORCE_MD_KIMI=1` restores that fallback. `MIDKERNEL_FORCE_GRAPH=1` forces the graph path.

When no pipeline file exists, the node still writes **`report.md`** (prefer `/outputs/report.md`) via the single-kimi helper.

Uploaded via the task role (`s3:PutObject`, SSE-S3). Helpers:

- `midkernel-publish-report` — find + validate + upload (md+kimi fallback)
- `scripts/ecs-in-task.sh` on playbooks — `agentflow run pipelines/${PLAYBOOK}.py`
- `kimi` PATH wrapper — prepare OpenRouter, run real kimi, then publish `report.md` only for single-kimi / per-node ECS (`RUN_ID` set and not `MIDKERNEL_AGENTFLOW_TARGET=local`)
- In-task graphs: `apply_graph_env` exports `MIDKERNEL_CLONE_TARGET=0`, `MIDKERNEL_REQUIRE_REPORT=0`, prepends `$WORKDIR/.midkernel/bin/kimi`, and points **`MIDKERNEL_KIMI_BIN` at `$WORKDIR/.midkernel/bin/kimi-openrouter`** (playbooks `_node_io` execs that, not PATH). The front drops playbooks `--config` (only `models.midkernel`) and injects `--config-file $KIMI_SHARE_DIR/config.toml` with aliases for `midkernel` **and** the node's `--model` (review / threat-model `moonshotai/kimi-k3`, judge-b, hunters). Real binary is `MIDKERNEL_GRAPH_KIMI_BIN=/opt/midkernel/kimi.bin`. Also re-exports:
  - `KIMI_SHARE_DIR=$WORKDIR/.midkernel/kimi` + OpenRouter `config.toml`
  - `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENROUTER_API_KEY` (`openai_legacy`)
  - `KIMI_API_KEY` / `KIMI_BASE_URL` / `KIMI_MODEL_NAME` (kimi-cli 1.49 dummy `type=kimi` fallback if `--config` still misses)
  - **`max_tokens = 32768`** on every model alias (never 131072). GOAL `cmtufzqzo0003k004mt2w0m9c` hunters 402'd `in_flight_budget_exhausted` because kimi-cli 1.49 `openai_legacy` omitted `max_tokens` and OpenRouter reserved the model max (131072; wallet could afford ~68k–120k). The front does **not** copy `max_context_size` (262144) into `max_tokens`. Override via first-wins `MIDKERNEL_OPENROUTER_MAX_TOKENS` / `OPENROUTER_MAX_TOKENS` / `KIMI_MAX_TOKENS` / `KIMI_MODEL_MAX_TOKENS` / `KIMI_MODEL_MAX_COMPLETION_TOKENS` (hard allowed max 65536; any value `>65536` or `>=131072` → 32768, not `min(value, 65536)`). Playbooks `prepare` / `render_kimi_openrouter_config` write the same `max_tokens = 32768` line — keep it in lockstep. If wrap_kimi already set `OPENAI_BASE_URL` / config `base_url` to a localhost injection proxy, kimi-openrouter **must not** rewrite it to `https://openrouter.ai/api/v1`. sitecustomize / OpenAILegacy patch **fails loud** if it does not install, strips `max_completion_tokens`, and always sets outbound `max_tokens` to the clamped value.

**Playbooks coordination:** `_node_io.graph_runtime_env()` / `kimi_io_env()` do not need to strip these keys (LocalRunner copies `os.environ` then overlays node env). Harden playbooks by passing `KIMI_SHARE_DIR`, `KIMI_BASE_URL`, `KIMI_MODEL_NAME`, `OPENAI_*`, and `OPENROUTER_*` through node env. Separately, playbooks `extra_args=["--config", …]` only defines `models.midkernel` while agentflow adds `--model moonshotai/kimi-k3` — that mismatch is what produced `LLM not set` on `cmtudf0470003jp040742shv6` / `cmtudm8f20003i90462yr3vxq`. Runner now covers the fallback; playbooks should still add the OpenRouter slug aliases (or drop the mismatched `--model`) so `--config` itself resolves.
- After cloning playbooks, runner `chmod +x` + `--version` short-circuit on `pipelines/_node_io.py` so agentflow `kimi_ready` (`<executable> --version` in the prepared local shell) execs `MIDKERNEL_KIMI_BIN` instead of wrap_kimi. PATH `kimi --version` also execs the real binary with no prepare/publish
- `BASH_ENV=/opt/midkernel/node-env.sh` — same prepare when agentflow uses `bash -c`; graph mode skips target clone and the report EXIT trap (`prepare_node(clone_target=False)` still injects GITHUB_TOKEN + OpenRouter)
- **Entrypoint / default CMD:** `scripts/entrypoint.sh` does **not** source `node-env.sh` (does **not** run `midkernel-node-prepare`) when `CMD` is `midkernel-default` / `midkernel-runner` and `RUN_ID` is set. GOAL `cmtue7rv90003l104ysmh21eu` died in ~46s with `Clone destination is not empty: /workspace` because entrypoint prepared with `MIDKERNEL_CLONE_TARGET` unset (`should_clone_target()=True`) before `midkernel-runner` could pin graph flags. Runner now calls `apply_graph_mode_flags()` (sets `MIDKERNEL_CLONE_TARGET=0`, `MIDKERNEL_AGENTFLOW_TARGET=local`, `MIDKERNEL_REQUIRE_REPORT=0`) **before** `prepare_node(clone_target=False)`. agentflow `bash -c` still sources `node-env.sh`.

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
| `OPENROUTER_MAX_TOKENS` | no | first-wins: `MIDKERNEL_OPENROUTER_MAX_TOKENS`, `OPENROUTER_MAX_TOKENS`, `KIMI_MAX_TOKENS`, `KIMI_MODEL_MAX_TOKENS`, `KIMI_MODEL_MAX_COMPLETION_TOKENS` | in-task default **32768**, hard allowed max **65536**; `>65536` or `>=131072` → 32768 (not `min(value, 65536)`) |
| `AWS_REGION` | no | — | default `us-east-1` |
| `OPENROUTER_SECRET_ID` | no | — | `midkernel/dev/harness/openrouter-api-key` |
| `GITHUB_TOKEN_SECRET_ID` | no | — | `midkernel/dev/harness/github-token` |
| `MIDKERNEL_KIMI_BIN` | no | — | real kimi-cli (`/opt/midkernel/kimi.bin`); graph path always sets this |
| `MIDKERNEL_AGENTFLOW_TARGET` | no | — | `local` for in-task graphs (no nested RunTask) |
| `MIDKERNEL_CLONE_TARGET` | no | — | `0` on the graph path so prepare injects secrets without cloning into `$WORKDIR` |
| `MIDKERNEL_REQUIRE_REPORT` | no | — | `0` on the graph path so PATH `kimi` / BASH_ENV do not require `report.md` after every node |

A **generic** agentflow node (no `RUN_ID`) still runs `kimi` with OpenRouter if a key is already in the environment.

## Suggested ECS task definition

`examples/task-definition.json` / `examples/runtask.json`. Family `midkernel-dev-scan` or app-registered `midkernel-agentflow-agents`. Logs: `/agentflow`. Capacity: Fargate Spot preferred.

| Profile | cpu | memory |
| --- | --- | --- |
| `low` | `1024` | `2048` |
| `balanced` | `2048` | `4096` |
| `max` | `4096` | `8192` |

When the app RunTasks **without** a command override and `RUN_ID` is set, `CMD midkernel-default` execs `midkernel-runner` **without** an entrypoint clone-prepare. Runner then runs the graph when `pipelines/<PLAYBOOK>.py` exists (clone playbooks → `agentflow run` → `graph.json` + `nodes/*` + `report.md`). Native agentflow overrides that with `bash -c` + `kimi`.

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
