#!/bin/sh
# agentflow-compatible node entrypoint.
#
# Stock agentflow ECS overrides this with: entryPoint=["bash","-c"]
# command=["export KIMI_API_KEY=... && kimi --print ..."]
# BASH_ENV + the kimi wrapper still prepare OpenRouter / publish report.md.
#
# When Midkernel app RunTasks this image without a command override and
# RUN_ID is set, run the Kimi scan helper.
#
# GOAL cmtue7rv90003l104ysmh21eu: sourcing node-env.sh *before*
# midkernel-runner ran midkernel-node-prepare with CLONE_TARGET unset.
# should_clone_target() defaulted True and git-cloned into /workspace
# (already non-empty from the image / .midkernel) → exit 1, no graph.json.
# Default CMD must not clone-prepare; midkernel-runner calls
# prepare_node(clone_target=False) for playbooks graphs.
set -eu

_midkernel_source_node_env() {
  node_env="${MIDKERNEL_NODE_ENV:-/opt/midkernel/node-env.sh}"
  if [ -f "$node_env" ]; then
    # shellcheck disable=SC1090
    . "$node_env"
  fi
}

if [ "$#" -eq 0 ] || [ "${1:-}" = "midkernel-default" ]; then
  if [ -n "${RUN_ID:-}" ]; then
    exec midkernel-runner
  fi
  exec agentflow --help
fi

# Same process as CMD midkernel-default — do not clone-prepare first.
if [ "${1:-}" = "midkernel-runner" ]; then
  exec "$@"
fi

_midkernel_source_node_env

if [ "${1:-}" = "-c" ]; then
  shift
  exec /bin/bash -c "$@"
fi

exec "$@"
