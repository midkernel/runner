"""OpenRouter 429 retry + shared RPM throttle for GOAL hunter fan-out.

Playbooks #12 serialized hunters after run ``cmtun51000003l704q7lyyjrf``
(``limit_source=openrouter_new_account``, 20 req/min). Playbooks #17 turns
fan-out back on (``concurrency=N``). Client code can wait out a 429; it
cannot raise the account RPM.

Lockstep with playbooks ``wrap_kimi``: retry **429** only (never 402),
honor ``Retry-After`` through 90s, else exponential backoff 1s…90s,
default 8 attempts. A shared file lock under ``WORKDIR/.midkernel``
spaces completions across in-task hunter processes so concurrency>1
does not burst the 20 RPM window.
"""

from __future__ import annotations

import fcntl
import inspect
import logging
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

LOG = logging.getLogger("midkernel.openrouter")

DEFAULT_OPENROUTER_429_RETRIES = 8
MAX_OPENROUTER_429_RETRIES = 16
OPENROUTER_429_BACKOFF_BASE_SECONDS = 1.0
OPENROUTER_429_BACKOFF_CAP_SECONDS = 90.0

# Under the new-account 20 RPM cap (cmtun51000003l704q7lyyjrf). 0 disables.
DEFAULT_OPENROUTER_RPM = 12
MAX_OPENROUTER_RPM = 60

RETRY_ENV_KEYS = (
    "MIDKERNEL_OPENROUTER_429_RETRIES",
    "OPENROUTER_429_RETRIES",
)
RPM_ENV_KEYS = (
    "MIDKERNEL_OPENROUTER_RPM",
    "OPENROUTER_RPM",
)

# App / runner / agentflow aliases. First-wins. Unset means "do not invent".
CONCURRENCY_ENV_KEYS = (
    "CONCURRENCY",
    "AGENTFLOW_CONCURRENCY",
    "GRAPH_CONCURRENCY",
    "MIDKERNEL_CONCURRENCY",
)

# Same ceiling as GOAL_COUNT / playbooks MAX_GOAL_HUNTERS.
MAX_GRAPH_CONCURRENCY = 6

_Sleep = Callable[[float], None]


def _sleep(seconds: float) -> None:
    """Indirection so tests can record backoff without waiting."""
    time.sleep(float(seconds))


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse ``Retry-After`` as delta-seconds or HTTP-date. ``None`` if unusable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    stamp = now if now is not None else datetime.now(timezone.utc)
    return max(0.0, (when - stamp).total_seconds())


def openrouter_429_delay_seconds(attempt: int, retry_after: str | None = None) -> float:
    """Seconds to wait before retry *attempt* (0-based). Prefer Retry-After."""
    parsed = parse_retry_after(retry_after)
    if parsed is not None:
        return min(parsed, OPENROUTER_429_BACKOFF_CAP_SECONDS)
    exp = OPENROUTER_429_BACKOFF_BASE_SECONDS * (2 ** max(0, int(attempt)))
    return min(exp, OPENROUTER_429_BACKOFF_CAP_SECONDS)


def _first_int(environ: Mapping[str, str] | None, keys: tuple[str, ...]) -> int | None:
    env = os.environ if environ is None else environ
    for key in keys:
        raw = (env.get(key) or "").strip()
        if not raw:
            continue
        try:
            return int(raw, 10)
        except ValueError:
            continue
    return None


def resolve_429_retries(environ: Mapping[str, str] | None = None) -> int:
    parsed = _first_int(environ, RETRY_ENV_KEYS)
    if parsed is None:
        return DEFAULT_OPENROUTER_429_RETRIES
    return max(0, min(MAX_OPENROUTER_429_RETRIES, parsed))


def resolve_openrouter_rpm(environ: Mapping[str, str] | None = None) -> int:
    """Shared chat/completions throttle. ``0`` disables. Default 12 RPM."""
    parsed = _first_int(environ, RPM_ENV_KEYS)
    if parsed is None:
        return DEFAULT_OPENROUTER_RPM
    if parsed <= 0:
        return 0
    return min(MAX_OPENROUTER_RPM, parsed)


def parse_graph_concurrency(raw: str | None) -> int | None:
    """Return 1..6 when *raw* is a valid override. ``None`` if unset/invalid.

    Does not invent a default — playbooks still owns ``Graph(concurrency=…)``
    from ``GOAL_COUNT`` when the app did not set CONCURRENCY.
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
    """First-wins CONCURRENCY / AGENTFLOW_CONCURRENCY / GRAPH_CONCURRENCY / MIDKERNEL_CONCURRENCY."""
    env = os.environ if environ is None else environ
    for key in CONCURRENCY_ENV_KEYS:
        parsed = parse_graph_concurrency(env.get(key))
        if parsed is not None:
            return parsed
    return None


def concurrency_env(value: int) -> dict[str, str]:
    text = str(max(1, min(int(value), MAX_GRAPH_CONCURRENCY)))
    return {key: text for key in CONCURRENCY_ENV_KEYS}


def openrouter_retry_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Bake resolved retry/RPM onto child env so playbooks wrap_kimi matches."""
    retries = str(resolve_429_retries(environ))
    rpm = str(resolve_openrouter_rpm(environ))
    out = {key: retries for key in RETRY_ENV_KEYS}
    out.update({key: rpm for key in RPM_ENV_KEYS})
    return out


def apply_graph_concurrency_env(environ: dict[str, str]) -> dict[str, str]:
    """Export a CONCURRENCY override when the app set one. Do not invent."""
    resolved = resolve_graph_concurrency(environ)
    if resolved is None:
        return environ
    environ.update(concurrency_env(resolved))
    return environ


def should_retry_openrouter(status: int | None, attempt: int, retries: int | None = None) -> bool:
    """Retry only HTTP 429, and only while *attempt* is still under the cap."""
    limit = DEFAULT_OPENROUTER_429_RETRIES if retries is None else int(retries)
    return status == 429 and 0 <= int(attempt) < max(0, limit)


def http_status_from_error(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            value = getattr(response, attr, None)
            if isinstance(value, int) and value > 0:
                return value
    return None


def retry_after_from_error(exc: BaseException) -> str | None:
    headers: Any = None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        for key in ("Retry-After", "retry-after"):
            value = getter(key)
            if value:
                return str(value)
    try:
        for key, value in headers.items():
            if str(key).lower() == "retry-after" and value:
                return str(value)
    except Exception:
        return None
    return None


def throttle_lock_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    share = (env.get("KIMI_SHARE_DIR") or "").strip()
    if share:
        share_path = Path(share)
        if share_path.name == "kimi" and share_path.parent.name == ".midkernel":
            return share_path.parent / "openrouter-rpm.lock"
        return share_path / "openrouter-rpm.lock"
    workdir = (env.get("WORKDIR") or "").strip()
    if workdir:
        return Path(workdir) / ".midkernel" / "openrouter-rpm.lock"
    return Path("/tmp/midkernel-openrouter-rpm.lock")


def acquire_openrouter_slot(
    environ: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], float] = time.time,
    sleep: _Sleep = _sleep,
) -> float:
    """Block until this process may send one OpenRouter completion.

    Shared across in-task hunter processes via an exclusive file lock.
    Returns seconds slept (0 if the slot was free). ``rpm=0`` is a no-op.
    """
    rpm = resolve_openrouter_rpm(environ)
    if rpm <= 0:
        return 0.0
    interval = 60.0 / float(rpm)
    path = throttle_lock_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw = handle.read().strip()
            last = 0.0
            if raw:
                try:
                    last = float(raw)
                except ValueError:
                    last = 0.0
            now = float(clock())
            wait = last + interval - now
            if wait > 0:
                sleep(wait)
                waited = wait
                now = float(clock())
            handle.seek(0)
            handle.truncate()
            handle.write(f"{now:.6f}".encode("ascii"))
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return waited


def invoke_openai_create_with_429_retry(
    create: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    sleep: _Sleep | None = None,
) -> Any:
    """Call *create* with shared RPM throttle + 429 Retry-After. Sync result or coroutine."""
    sleeper = _sleep if sleep is None else sleep
    retries = resolve_429_retries(environ)
    if inspect.iscoroutinefunction(create):
        return _invoke_async(create, args, kwargs, retries=retries, environ=environ, sleep=sleeper)

    acquire_openrouter_slot(environ, sleep=sleeper)
    attempt = 0
    while True:
        try:
            result = create(*args, **kwargs)
        except Exception as exc:
            if _should_sleep_retry(exc, attempt, retries):
                _sleep_retry(exc, attempt, sleeper)
                attempt += 1
                acquire_openrouter_slot(environ, sleep=sleeper)
                continue
            raise
        if inspect.isawaitable(result):
            return _await_result_with_retry(
                result,
                create,
                args,
                kwargs,
                attempt=attempt,
                retries=retries,
                environ=environ,
                sleep=sleeper,
            )
        return result


async def _invoke_async(
    create: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    retries: int,
    environ: Mapping[str, str] | None,
    sleep: _Sleep,
) -> Any:
    acquire_openrouter_slot(environ, sleep=sleep)
    attempt = 0
    while True:
        try:
            return await create(*args, **kwargs)
        except Exception as exc:
            if _should_sleep_retry(exc, attempt, retries):
                _sleep_retry(exc, attempt, sleep)
                attempt += 1
                acquire_openrouter_slot(environ, sleep=sleep)
                continue
            raise


async def _await_result_with_retry(
    first: Any,
    create: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    attempt: int,
    retries: int,
    environ: Mapping[str, str] | None,
    sleep: _Sleep,
) -> Any:
    pending = first
    current = attempt
    while True:
        try:
            return await pending
        except Exception as exc:
            if _should_sleep_retry(exc, current, retries):
                _sleep_retry(exc, current, sleep)
                current += 1
                acquire_openrouter_slot(environ, sleep=sleep)
                pending = create(*args, **kwargs)
                continue
            raise


def _should_sleep_retry(exc: BaseException, attempt: int, retries: int) -> bool:
    status = http_status_from_error(exc)
    if status == 402:
        return False
    return should_retry_openrouter(status, attempt, retries)


def _sleep_retry(exc: BaseException, attempt: int, sleep: _Sleep) -> None:
    retry_after = retry_after_from_error(exc)
    delay = openrouter_429_delay_seconds(attempt, retry_after)
    LOG.warning(
        "OpenRouter 429 retry %s sleep=%.1fs retry_after=%s",
        attempt + 1,
        delay,
        retry_after or "-",
    )
    sleep(delay)


def _patch_completions_create_429(
    provider: object,
    environ: Mapping[str, str] | None,
) -> None:
    client = getattr(provider, "client", None)
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    create = getattr(completions, "create", None)
    if create is None or getattr(create, "_midkernel_429_retry", False):
        return

    def wrapped_create(*args, **kwargs):
        # Read live process env (RPM / retries / WORKDIR lock). Do not
        # close over install-time {} from sitecustomize.
        return invoke_openai_create_with_429_retry(create, args, kwargs)

    wrapped_create._midkernel_429_retry = True  # type: ignore[attr-defined]
    completions.create = wrapped_create


def install_openai_legacy_429_retry(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Wrap ``OpenAILegacy`` chat.completions.create with 429 retry + RPM throttle.

    Installed after the max_tokens cap so retries re-send a clamped body.
    Missing ``openai_legacy`` is non-fatal (max_tokens install is the loud one).
    """
    try:
        from kosong.contrib.chat_provider.openai_legacy import OpenAILegacy
    except ImportError:
        return False

    init = OpenAILegacy.__init__
    if getattr(init, "_midkernel_429_retry", False):
        return True

    def wrapped_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        _patch_completions_create_429(self, environ)

    wrapped_init._midkernel_429_retry = True  # type: ignore[attr-defined]
    OpenAILegacy.__init__ = wrapped_init  # type: ignore[method-assign]

    original_with = getattr(OpenAILegacy, "with_generation_kwargs", None)
    if original_with is not None and not getattr(original_with, "_midkernel_429_retry", False):

        def wrapped_with(self, **kwargs):
            updated = original_with(self, **kwargs)
            _patch_completions_create_429(updated, environ)
            return updated

        wrapped_with._midkernel_429_retry = True  # type: ignore[attr-defined]
        OpenAILegacy.with_generation_kwargs = wrapped_with  # type: ignore[method-assign]

    return True
