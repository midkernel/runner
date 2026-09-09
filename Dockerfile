# Midkernel agentflow ECS node/agent image (linux/amd64).
#
# This is what agentenv/agentflow launches per Fargate node:
#   entryPoint: ["bash", "-c"]
#   command:    ["<auth_setup> && kimi --print --output-format stream-json --yolo -p ..."]
#
# Official agentflow image is DinD/privileged. Midkernel Fargate Spot tasks
# are unprivileged (no Docker socket). We keep the node contract (bash, kimi,
# agentflow, /workspace, /outputs) without DinD.
#
# Published to:
#   489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents
#
# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm

ARG AGENTFLOW_REF=09df0175ff2c88528c99b9f2c22f25b5e7622a8e
ARG KIMI_CLI_VERSION=1.49.0

LABEL org.opencontainers.image.title="Midkernel agentflow ECS node" \
      org.opencontainers.image.description="agentflow + Kimi CLI + OpenRouter for ECS Fargate agents"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/agent \
    PATH="/usr/local/bin:${PATH}" \
    WORKDIR=/workspace \
    OUTPUTS_DIR=/outputs \
    BASH_ENV=/opt/midkernel/node-env.sh \
    KIMI_REAL_BIN=/opt/midkernel/kimi.bin \
    MIDKERNEL_KIMI_BIN=/opt/midkernel/kimi.bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        jq \
        libstdc++6 \
    && pip install --no-cache-dir \
        "kimi-cli==${KIMI_CLI_VERSION}" \
        "agentflow @ git+https://github.com/agentenv/agentflow.git@${AGENTFLOW_REF}" \
    && useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir -p \
        /workspace \
        /outputs \
        /inputs \
        /reference \
        /agentflow-runtime \
        /opt/midkernel \
        /home/agent/.kimi \
    && chown -R agent:agent \
        /workspace /outputs /inputs /reference /agentflow-runtime /home/agent \
    && command -v bash \
    && command -v git \
    && command -v kimi \
    && command -v agentflow \
    && kimi --help >/dev/null \
    && agentflow --help >/dev/null \
    && REAL_KIMI="$(command -v kimi)" \
    && mv "$REAL_KIMI" /opt/midkernel/kimi.bin \
    && chmod 0755 /opt/midkernel/kimi.bin \
    && rm -rf /var/lib/apt/lists/* /tmp/*

WORKDIR /opt/midkernel/src
COPY --chown=agent:agent pyproject.toml README.md LICENSE ./
COPY --chown=agent:agent src ./src
COPY --chown=agent:agent pipelines ./pipelines
COPY scripts/entrypoint.sh /usr/local/bin/midkernel-agentflow-entrypoint
COPY scripts/node-env.sh /opt/midkernel/node-env.sh
COPY scripts/kimi-wrapper.sh /usr/local/bin/kimi

RUN chmod 0755 \
        /usr/local/bin/midkernel-agentflow-entrypoint \
        /opt/midkernel/node-env.sh \
        /usr/local/bin/kimi \
        /opt/midkernel/kimi.bin \
    && pip install --no-cache-dir . \
    && command -v midkernel-runner \
    && command -v midkernel-publish-report \
    && command -v midkernel-node-prepare \
    && test -x /opt/midkernel/kimi.bin \
    && test -x /usr/local/bin/kimi

USER agent
WORKDIR /workspace

ENTRYPOINT ["midkernel-agentflow-entrypoint"]
CMD ["midkernel-default"]
