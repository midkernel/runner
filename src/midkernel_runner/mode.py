"""In-task playbooks graph vs single-kimi node mode.

``cmtuavpvs0003ib04bfyr7roc`` uploaded ``graph.json`` + every
``nodes/*/prompt.md`` then exited 1 in ~68s with no output/meta/report.md.

After runner#4 starts ``agentflow run``, every bash node sources
``BASH_ENV`` (``midkernel-node-prepare`` + report trap) and PATH ``kimi``
is ``scripts/kimi-wrapper.sh``. Both require ``report.md`` after *every*
invocation and the prepare CLI clones into ``$WORKDIR``. Playbooks graphs
already filled ``$WORKDIR/.midkernel`` and intermediate nodes (prepare,
threat-model, hunters, judges) do not write ``report.md``.

Graph/in-task mode must:

* call ``/opt/midkernel/kimi.bin`` (``MIDKERNEL_KIMI_BIN``), never the
  report-enforcing PATH wrapper
* skip target clone (``prepare_node(clone_target=False)``) so secrets
  still inject
* skip ``midkernel-publish-report --require`` until the playbooks
  publish node
"""

from __future__ import annotations

import os

IMAGE_KIMI_BIN = "/opt/midkernel/kimi.bin"
LOCAL_TARGETS = frozenset({"local", "in-task", "task"})
FALSEY = frozenset({"0", "false", "no", "off"})
TRUTHY = frozenset({"1", "true", "yes", "on"})


def env_flag(environ: dict[str, str] | None, name: str) -> bool | None:
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip().lower()
    if raw in FALSEY:
        return False
    if raw in TRUTHY:
        return True
    return None


def in_graph_mode(environ: dict[str, str] | None = None) -> bool:
    """True when playbooks runs in-task (``MIDKERNEL_AGENTFLOW_TARGET=local``)."""
    env = os.environ if environ is None else environ
    target = (env.get("MIDKERNEL_AGENTFLOW_TARGET") or "").strip().lower()
    return target in LOCAL_TARGETS


def apply_graph_mode_flags(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Pin graph-mode flags so prepare / BASH_ENV skip target clone + report trap.

    Must run as soon as midkernel-runner chooses the playbooks graph — before
    ``prepare_node`` and before any later ``bash -c`` sources ``node-env.sh``.
    ``apply_graph_env`` used to set these only after prepare, which is too
    late for the image entrypoint (GOAL ``cmtue7rv90003l104ysmh21eu``).
    """
    env = os.environ if environ is None else environ
    env["MIDKERNEL_NODE_IO"] = env.get("MIDKERNEL_NODE_IO") or "1"
    if not (env.get("MIDKERNEL_AGENTFLOW_TARGET") or "").strip():
        env["MIDKERNEL_AGENTFLOW_TARGET"] = "local"
    if not (env.get("MIDKERNEL_CLONE_TARGET") or "").strip():
        env["MIDKERNEL_CLONE_TARGET"] = "0"
    if not (env.get("MIDKERNEL_REQUIRE_REPORT") or "").strip():
        env["MIDKERNEL_REQUIRE_REPORT"] = "0"
    return env


def should_clone_target(environ: dict[str, str] | None = None) -> bool:
    """Whether ``midkernel-node-prepare`` should clone GITHUB_* into WORKDIR.

    Playbooks prepare clones to ``$WORKDIR/repo``. Re-cloning the target
    into ``$WORKDIR`` fails once ``.midkernel/playbooks`` exists
    (``Clone destination is not empty``) and BASH_ENV then ``exit 1``
    before the first node's wrap can upload output/meta.

    Default CMD no longer calls this CLI (entrypoint skips node-env).
    Graph mode / ``MIDKERNEL_CLONE_TARGET=0`` must still skip clone even
    when ``$WORKDIR`` is already non-empty.
    """
    flag = env_flag(environ, "MIDKERNEL_CLONE_TARGET")
    if flag is not None:
        return flag
    return not in_graph_mode(environ)


def should_require_report(environ: dict[str, str] | None = None) -> bool:
    """Whether PATH ``kimi`` / BASH_ENV must publish ``report.md``.

    Single-kimi / per-node ECS still require a report when ``RUN_ID`` is
    set. Multi-node graphs only publish from the playbooks publish node.
    """
    env = os.environ if environ is None else environ
    if not (env.get("RUN_ID") or "").strip():
        return False
    flag = env_flag(env, "MIDKERNEL_REQUIRE_REPORT")
    if flag is not None:
        return flag
    if env_flag(env, "MIDKERNEL_SKIP_REPORT_PUBLISH") is True:
        return False
    return not in_graph_mode(env)
