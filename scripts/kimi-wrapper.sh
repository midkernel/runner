#!/bin/sh
# PATH wrapper around the real kimi-cli binary.
# agentflow ECS runs `kimi --print --output-format stream-json --yolo -p ...`.
# Prepare OpenRouter + task-role secrets, then run the real CLI.
#
# Single-kimi / per-node ECS: publish report.md when RUN_ID is set.
# In-task playbooks graphs (MIDKERNEL_AGENTFLOW_TARGET=local): do NOT
# require report.md — intermediate nodes never write it. Those graphs
# must call /opt/midkernel/kimi.bin via MIDKERNEL_KIMI_BIN (PATH shim
# installed by apply_graph_env). This wrapper is the last-resort guard
# if agentflow still resolves `kimi` here.
set -eu

_midkernel_require_report() {
  if [ -z "${RUN_ID:-}" ]; then
    return 1
  fi
  case "${MIDKERNEL_REQUIRE_REPORT:-}" in
    0|false|no|off|FALSE|NO|OFF) return 1 ;;
  esac
  case "${MIDKERNEL_SKIP_REPORT_PUBLISH:-}" in
    1|true|yes|on|TRUE|YES|ON) return 1 ;;
  esac
  case "${MIDKERNEL_AGENTFLOW_TARGET:-}" in
    local|in-task|task) return 1 ;;
  esac
  return 0
}

REAL="${MIDKERNEL_KIMI_BIN:-${KIMI_REAL_BIN:-/opt/midkernel/kimi.bin}}"
SELF=$(readlink -f "$0" 2>/dev/null || echo "$0")
REAL_RESOLVED=$(readlink -f "$REAL" 2>/dev/null || echo "$REAL")
if [ "$REAL_RESOLVED" = "$SELF" ]; then
  echo "midkernel: MIDKERNEL_KIMI_BIN must be /opt/midkernel/kimi.bin, not the PATH wrapper" >&2
  exit 127
fi
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

if _midkernel_require_report; then
  if command -v midkernel-publish-report >/dev/null 2>&1; then
    if ! midkernel-publish-report --require; then
      if [ "$status" -eq 0 ]; then
        status=1
      fi
    fi
  fi
fi

exit "$status"
