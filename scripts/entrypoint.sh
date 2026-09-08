#!/bin/sh
# agentflow-compatible node entrypoint.
#
# Stock agentflow ECS overrides this with: entryPoint=["bash","-c"]
# command=["export KIMI_API_KEY=... && kimi --print ..."]
# BASH_ENV + the kimi wrapper still prepare OpenRouter / publish report.md.
#
# When Midkernel app RunTasks this image without a command override and
# RUN_ID is set, run the Kimi scan helper.
set -eu

if [ -f /opt/midkernel/node-env.sh ]; then
  # shellcheck disable=SC1091
  . /opt/midkernel/node-env.sh
fi

if [ "$#" -eq 0 ] || [ "${1:-}" = "midkernel-default" ]; then
  if [ -n "${RUN_ID:-}" ]; then
    exec midkernel-runner
  fi
  exec agentflow --help
fi

if [ "${1:-}" = "-c" ]; then
  shift
  exec /bin/bash -c "$@"
fi

exec "$@"
