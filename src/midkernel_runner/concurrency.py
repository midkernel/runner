"""Pass app GOAL_CONCURRENCY / CONCURRENCY into playbooks graph emit.

Playbooks #17 first-wins ``GOAL_CONCURRENCY``, then ``CONCURRENCY``, then
``GRAPH_CONCURRENCY``. Picker is ``1 | 2 | 4 | 6``; any int ``1..6`` is
accepted. Missing / invalid is left unset here — playbooks defaults to 2.

Runner does not invent a cap and does not retry OpenRouter 429s. Client
retries live in playbooks ``wrap_kimi``. The 20 RPM new-account limit
(``openrouter_new_account``, run ``cmtun51000003l704q7lyyjrf``) is an
OpenRouter account tier. Extra API keys do not raise it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Lockstep with playbooks GOAL_CONCURRENCY_ENV_NAMES.
CONCURRENCY_ENV_KEYS = (
    "GOAL_CONCURRENCY",
    "CONCURRENCY",
    "GRAPH_CONCURRENCY",
)
# Extra app/runner spellings still resolve, then rewrite the playbooks names.
CONCURRENCY_RESOLVE_KEYS = (
    *CONCURRENCY_ENV_KEYS,
    "AGENTFLOW_CONCURRENCY",
    "MIDKERNEL_CONCURRENCY",
)

DEFAULT_GOAL_CONCURRENCY = 2
GOAL_CONCURRENCY_PICKER = (1, 2, 4, 6)
MAX_GRAPH_CONCURRENCY = 6


def parse_graph_concurrency(raw: str | None) -> int | None:
    """Return 1..6 when *raw* is a valid override. ``None`` if unset/invalid.

    Does not invent playbooks' default 2. Invalid values stay unset so
    playbooks ``goal_concurrency_cap()`` can apply that default.
    """
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip(), 10)
    except ValueError:
        return None
    if value < 1:
        return None
    return min(value, MAX_GRAPH_CONCURRENCY)


def resolve_graph_concurrency(environ: Mapping[str, str] | None = None) -> int | None:
    """First-wins GOAL_CONCURRENCY / CONCURRENCY / GRAPH_CONCURRENCY (+ aliases)."""
    env = os.environ if environ is None else environ
    for key in CONCURRENCY_RESOLVE_KEYS:
        parsed = parse_graph_concurrency(env.get(key))
        if parsed is not None:
            return parsed
    return None


def concurrency_env(value: int) -> dict[str, str]:
    """Write the playbooks emit names so Graph() sees GOAL_CONCURRENCY."""
    text = str(max(1, min(int(value), MAX_GRAPH_CONCURRENCY)))
    return {key: text for key in CONCURRENCY_ENV_KEYS}


def apply_graph_concurrency_env(environ: dict[str, str]) -> dict[str, str]:
    """Copy a task-env override onto GOAL_CONCURRENCY / CONCURRENCY / GRAPH_CONCURRENCY.

    No-op when the app left every alias unset. Playbooks then defaults to 2.
    """
    resolved = resolve_graph_concurrency(environ)
    if resolved is None:
        return environ
    environ.update(concurrency_env(resolved))
    return environ
