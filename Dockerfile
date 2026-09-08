# Midkernel Scan agent image for ECS Fargate Spot (linux/amd64).
# Follows agentflow's agent-image idea (CLIs + agentflow on PATH) without DinD:
# Fargate tasks are not privileged and Midkernel SGs have no Docker socket.
#
# Published to:
#   489470371031.dkr.ecr.us-east-1.amazonaws.com/midkernel-agentflow-agents
#
# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm

ARG AGENTFLOW_REF=09df0175ff2c88528c99b9f2c22f25b5e7622a8e
ARG OPENCODE_VERSION=1.18.29

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/agent \
    PATH="/usr/local/bin:${PATH}" \
    WORKDIR=/workspace \
    OUTPUTS_DIR=/outputs

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        libstdc++6 \
        tar \
    && curl -fsSL \
        "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz" \
        | tar -xz -C /tmp \
    && install -m 0755 /tmp/opencode /usr/local/bin/opencode \
    && rm -f /tmp/opencode \
    && pip install --no-cache-dir \
        "agentflow @ git+https://github.com/agentenv/agentflow.git@${AGENTFLOW_REF}" \
    && useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir -p /workspace /outputs /opt/midkernel \
    && chown -R agent:agent /workspace /outputs /home/agent \
    && command -v git \
    && command -v opencode \
    && opencode --version \
    && command -v agentflow \
    && rm -rf /var/lib/apt/lists/* /tmp/*

WORKDIR /opt/midkernel
COPY --chown=agent:agent pyproject.toml README.md LICENSE ./
COPY --chown=agent:agent src ./src
COPY --chown=agent:agent scripts/entrypoint.sh /usr/local/bin/midkernel-entrypoint
COPY --chown=agent:agent pipelines ./pipelines

RUN chmod 0755 /usr/local/bin/midkernel-entrypoint \
    && pip install --no-cache-dir . \
    && command -v midkernel-runner

USER agent
WORKDIR /workspace

ENTRYPOINT ["midkernel-entrypoint"]
CMD ["midkernel-runner"]
