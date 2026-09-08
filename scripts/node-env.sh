#!/bin/sh
# Sourced by BASH_ENV for agentflow's `bash -c` node launch, and by the
# image entrypoint. Idempotent. Never prints secret values.
if [ -n "${MIDKERNEL_NODE_READY:-}" ]; then
  return 0 2>/dev/null || true
fi

if command -v midkernel-node-prepare >/dev/null 2>&1; then
  if midkernel-node-prepare; then
    MIDKERNEL_NODE_READY=1
    export MIDKERNEL_NODE_READY
  else
    echo "midkernel: node prepare failed" >&2
    if [ -n "${RUN_ID:-}" ]; then
      # BASH_ENV and the image entrypoint both source this file. Abort the
      # node when Midkernel scan env is present and secrets/clone failed.
      exit 1
    fi
  fi
fi

_midkernel_on_exit() {
  status=$?
  if [ -n "${RUN_ID:-}" ] && command -v midkernel-publish-report >/dev/null 2>&1; then
    midkernel-publish-report --require || true
  fi
  return "$status"
}

if [ -n "${RUN_ID:-}" ] && [ -z "${MIDKERNEL_PUBLISH_TRAP:-}" ]; then
  MIDKERNEL_PUBLISH_TRAP=1
  export MIDKERNEL_PUBLISH_TRAP
  trap '_midkernel_on_exit' EXIT
fi
