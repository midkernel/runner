#!/bin/sh
# PATH wrapper around the real kimi-cli binary.
# agentflow ECS runs `kimi --print --output-format stream-json --yolo -p ...`.
# Prepare OpenRouter + task-role secrets, then run the real CLI. After the
# process returns, publish report.md when RUN_ID is set.
set -eu

REAL="${KIMI_REAL_BIN:-/opt/midkernel/kimi.bin}"
if [ ! -x "$REAL" ]; then
  echo "midkernel: real kimi binary missing at $REAL" >&2
  exit 127
fi

if [ -z "${MIDKERNEL_NODE_READY:-}" ]; then
  if ! command -v midkernel-node-prepare >/dev/null 2>&1 || ! midkernel-node-prepare; then
    echo "midkernel: node prepare failed before kimi" >&2
    if [ -n "${RUN_ID:-}" ]; then
      exit 1
    fi
  else
    MIDKERNEL_NODE_READY=1
    export MIDKERNEL_NODE_READY
  fi
fi

set +e
"$REAL" "$@"
status=$?
set -e

if [ -n "${RUN_ID:-}" ]; then
  if command -v midkernel-publish-report >/dev/null 2>&1; then
    if ! midkernel-publish-report --require; then
      if [ "$status" -eq 0 ]; then
        status=1
      fi
    fi
  fi
fi

exit "$status"
