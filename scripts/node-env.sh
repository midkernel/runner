#!/bin/sh
# Sourced by BASH_ENV for agentflow's `bash -c` node launch.
# The image entrypoint does *not* source this on CMD midkernel-default
# (GOAL cmtue7rv90003l104ysmh21eu: clone-into-nonempty /workspace before
# midkernel-runner). Idempotent. Never prints secret values.
#
# In-task playbooks graphs: skip target clone (MIDKERNEL_CLONE_TARGET=0)
# and do not require report.md after every bash -c. Intermediate nodes
# do not write report.md; BASH_ENV aborting on clone-into-nonempty
# WORKDIR is what killed cmtuavpvs0003ib04bfyr7roc before output/meta.
if [ -n "${MIDKERNEL_NODE_READY:-}" ]; then
  return 0 2>/dev/null || true
fi

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

if command -v midkernel-node-prepare >/dev/null 2>&1; then
  if midkernel-node-prepare; then
    MIDKERNEL_NODE_READY=1
    export MIDKERNEL_NODE_READY
  else
    echo "midkernel: node prepare failed" >&2
    if [ -n "${RUN_ID:-}" ]; then
      # BASH_ENV sources this file (entrypoint default CMD does not).
      # Abort the node when Midkernel scan env is present and secrets/clone failed.
      exit 1
    fi
  fi
fi

_midkernel_on_exit() {
  status=$?
  if _midkernel_require_report && command -v midkernel-publish-report >/dev/null 2>&1; then
    midkernel-publish-report --require || true
  fi
  return "$status"
}

if _midkernel_require_report && [ -z "${MIDKERNEL_PUBLISH_TRAP:-}" ]; then
  MIDKERNEL_PUBLISH_TRAP=1
  export MIDKERNEL_PUBLISH_TRAP
  trap '_midkernel_on_exit' EXIT
fi
