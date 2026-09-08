#!/bin/sh
# Midkernel Scan container entrypoint. Logs go to stdout → CloudWatch /agentflow.
set -eu

echo "midkernel-runner: starting pid=$$"

if [ "${1:-}" = "agentflow" ] || [ "${1:-}" = "opencode" ] || [ "${1:-}" = "bash" ] || [ "${1:-}" = "sh" ]; then
  exec "$@"
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec midkernel-runner
