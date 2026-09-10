"""Official OpenRouter generation ids (``gen-…``) for app observe.

App PR #45 attributes per-run OpenRouter $ only when it has official
generation ids and can ``GET /api/v1/generation?id=``. This module
captures real ids from harness/API responses and writes them where
observe can pull them:

* ``s3://$ARTIFACTS_BUCKET/runs/<RUN_ID>/openrouter-generations.json``
  (sibling of ``report.md``)
* ``$OUTPUTS_DIR/openrouter-generations.json``
* CloudWatch-safe structured stdout (``event=openrouter_generation``)

Field names lockstep with app#45: ``openrouterGenerationIds``,
``generationIds``, ``generationId``. Never invent ids or USD.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from midkernel_runner.config import RunConfig

LOG = logging.getLogger("midkernel.openrouter")

# Same shape as app ``parseOpenRouterGenerationId`` (``/^gen-[A-Za-z0-9_-]+$/``).
GENERATION_ID_RE = re.compile(r"^gen-[A-Za-z0-9_-]+$")
GENERATIONS_OBJECT_NAME = "openrouter-generations.json"
GENERATIONS_LEDGER_NAME = "openrouter-generations.jsonl"
HEADER_NAMES = ("x-generation-id", "x-openrouter-generation-id")
ID_KEYS = (
    "generationId",
    "generation_id",
    "openrouterGenerationId",
    "id",
)
LIST_KEYS = ("openrouterGenerationIds", "generationIds")
STRUCTURED_EVENT = "openrouter_generation"


class OpenRouterGenerationCaptureError(RuntimeError):
    """Generation-id capture did not install (non-fatal for the scan)."""


def parse_openrouter_generation_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if GENERATION_ID_RE.fullmatch(trimmed) else None


def unique_openrouter_generation_ids(values: object) -> list[str]:
    """Official ``gen-…`` ids only. Deduped, sorted — never invents."""
    ids: set[str] = set()
    items = values if isinstance(values, (list, tuple, set)) else [values]
    for item in items:
        parsed = parse_openrouter_generation_id(item)
        if parsed:
            ids.add(parsed)
    return sorted(ids)


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _headers_mapping(value: object) -> Mapping[str, Any] | None:
    headers = getattr(value, "headers", None)
    if headers is None and isinstance(value, Mapping):
        headers = value.get("headers")
    if headers is None:
        return None
    if isinstance(headers, Mapping):
        return headers
    try:
        return {str(key): headers[key] for key in headers}
    except (TypeError, KeyError):
        return None


def extract_generation_ids_from_headers(headers: object) -> list[str]:
    mapping = _headers_mapping(headers) if not isinstance(headers, Mapping) else headers
    if mapping is None:
        mapping = _as_mapping(headers)
    if mapping is None:
        return []
    found: list[str] = []
    for key, raw in mapping.items():
        if str(key).lower() not in HEADER_NAMES:
            continue
        if isinstance(raw, (list, tuple)):
            found.extend(unique_openrouter_generation_ids(raw))
        else:
            parsed = parse_openrouter_generation_id(raw)
            if parsed:
                found.append(parsed)
    return unique_openrouter_generation_ids(found)


def extract_generation_ids_from_payload(value: object, *, depth: int = 0) -> list[str]:
    """Pull official ids from OpenRouter/OpenAI response objects only.

    Does not regex-scan free text (that would invent ids from model output).
    """
    if value is None or depth > 6:
        return []
    found: list[str] = []
    found.extend(extract_generation_ids_from_headers(value))

    parsed = parse_openrouter_generation_id(getattr(value, "id", None))
    if parsed:
        found.append(parsed)
    parsed = parse_openrouter_generation_id(getattr(value, "generation_id", None))
    if parsed:
        found.append(parsed)

    mapping = _as_mapping(value)
    if mapping is not None:
        for key in ID_KEYS:
            parsed = parse_openrouter_generation_id(mapping.get(key))
            if parsed:
                found.append(parsed)
        for key in LIST_KEYS:
            found.extend(unique_openrouter_generation_ids(mapping.get(key)))
        inner = mapping.get("data")
        if inner is not None and inner is not value:
            found.extend(extract_generation_ids_from_payload(inner, depth=depth + 1))
        usage = mapping.get("usage")
        if isinstance(usage, Mapping):
            found.extend(unique_openrouter_generation_ids(usage.get("generation_id")))
        choices = mapping.get("choices")
        if isinstance(choices, list):
            for choice in choices[:8]:
                found.extend(extract_generation_ids_from_payload(choice, depth=depth + 1))

    if isinstance(value, (list, tuple)):
        for item in list(value)[:32]:
            found.extend(extract_generation_ids_from_payload(item, depth=depth + 1))

    return unique_openrouter_generation_ids(found)


def extract_generation_ids_from_kimi_stdout(stdout: str) -> list[str]:
    """Official ids from kimi ``stream-json`` API-shaped events only."""
    found: list[str] = []
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        found.extend(extract_generation_ids_from_payload(payload))
    return unique_openrouter_generation_ids(found)


def node_id_from_environ(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    for key in ("MIDKERNEL_NODE_ID", "AGENTFLOW_NODE_ID", "NODE_ID"):
        value = (env.get(key) or "").strip()
        if value:
            return value
    return None


def run_id_from_environ(environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    value = (env.get("RUN_ID") or "").strip()
    return value or None


def ledger_path(environ: Mapping[str, str] | None = None, *, workdir: str | Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = (env.get("MIDKERNEL_OPENROUTER_GENERATIONS_LEDGER") or "").strip()
    if override:
        return Path(override)
    root = Path(workdir or env.get("WORKDIR") or "/workspace")
    return root / ".midkernel" / GENERATIONS_LEDGER_NAME


def artifact_path(environ: Mapping[str, str] | None = None, *, outputs_dir: str | Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = (env.get("MIDKERNEL_OPENROUTER_GENERATIONS_PATH") or "").strip()
    if override:
        return Path(override)
    outputs = Path(outputs_dir or env.get("OUTPUTS_DIR") or "/outputs")
    return outputs / GENERATIONS_OBJECT_NAME


def generations_object_key(report_key: str) -> str:
    """Sibling of ``report.md`` under the same run prefix."""
    key = (report_key or "").strip().lstrip("/")
    if not key:
        return f"runs/{GENERATIONS_OBJECT_NAME}"
    parent, _, name = key.rpartition("/")
    if name == GENERATIONS_OBJECT_NAME:
        return key
    if parent:
        return f"{parent}/{GENERATIONS_OBJECT_NAME}"
    return GENERATIONS_OBJECT_NAME


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
    generation_id: str,
    *,
    node_id: str | None = None,
    source: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    return {
        "generationId": generation_id,
        "nodeId": node_id or node_id_from_environ(environ),
        "source": source,
        "at": utc_now(),
        "runId": run_id_from_environ(environ),
    }


def append_generation_records(
    records: list[dict[str, object]],
    *,
    environ: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> list[dict[str, object]]:
    if not records:
        return []
    dest = path or ledger_path(environ)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n" for record in records
    )
    with dest.open("a", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(payload)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            handle.write(payload)
    return records


def load_generation_records(
    *,
    environ: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> list[dict[str, object]]:
    dest = path or ledger_path(environ)
    if not dest.is_file():
        return []
    records: list[dict[str, object]] = []
    for raw_line in dest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        parsed = parse_openrouter_generation_id(payload.get("generationId") or payload.get("generation_id"))
        if not parsed:
            continue
        payload["generationId"] = parsed
        records.append(payload)
    return records


def record_generation_ids(
    values: object,
    *,
    node_id: str | None = None,
    source: str,
    environ: Mapping[str, str] | None = None,
    path: Path | None = None,
    log: bool = True,
) -> list[str]:
    ids = unique_openrouter_generation_ids(values)
    if not ids:
        return []
    nid = node_id or node_id_from_environ(environ)
    records = [_record(item, node_id=nid, source=source, environ=environ) for item in ids]
    append_generation_records(records, environ=environ, path=path)
    if log:
        log_generation_ids(ids, node_id=nid, environ=environ)
    return ids


def log_generation_ids(
    ids: list[str],
    *,
    node_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    official = unique_openrouter_generation_ids(ids)
    if not official:
        return
    payload = {
        "event": STRUCTURED_EVENT,
        "generationId": official[0],
        "generationIds": official,
        "openrouterGenerationIds": official,
        "nodeId": node_id or node_id_from_environ(environ),
        "runId": run_id_from_environ(environ),
    }
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    # CloudWatch-safe: one JSON object, no secrets, no invented USD.
    print(line, flush=True)
    LOG.info(
        "openrouter generationIds=%s nodeId=%s runId=%s",
        ",".join(official),
        payload["nodeId"] or "-",
        payload["runId"] or "-",
    )


def record_from_response(
    response: object,
    *,
    node_id: str | None = None,
    source: str = "response",
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    ids = extract_generation_ids_from_payload(response)
    return record_generation_ids(ids, node_id=node_id, source=source, environ=environ)


def _is_stream(value: object) -> bool:
    if value is None or isinstance(value, (str, bytes, dict, list, tuple)):
        return False
    if inspect.iscoroutine(value) or inspect.isawaitable(value):
        return False
    return hasattr(value, "__iter__") and not isinstance(value, Mapping)


def _wrap_stream(stream: object, *, source: str) -> object:
    if inspect.isasyncgen(stream) or hasattr(stream, "__aiter__"):

        async def agen() -> Any:
            async for chunk in stream:  # type: ignore[union-attr]
                record_from_response(chunk, source=source)
                yield chunk

        return agen()

    def gen() -> Iterator[Any]:
        for chunk in stream:  # type: ignore[union-attr]
            record_from_response(chunk, source=source)
            yield chunk

    return gen()


def wrap_openai_create_result(result: object, *, source: str = "openai_legacy") -> object:
    """Record official ids from a ``chat.completions.create`` return value.

    Preserves the original object/stream/awaitable. Does not invent ids.
    """
    if inspect.iscoroutine(result) or inspect.isawaitable(result):

        async def awaited() -> object:
            value = await result  # type: ignore[misc]
            return wrap_openai_create_result(value, source=source)

        return awaited()

    record_from_response(result, source=source)
    if _is_stream(result):
        return _wrap_stream(result, source=source)
    return result


def collect_generation_ids(
    *,
    environ: Mapping[str, str] | None = None,
    extra: object = None,
    stdout: str | None = None,
    path: Path | None = None,
) -> list[str]:
    found: list[str] = []
    for record in load_generation_records(environ=environ, path=path):
        parsed = parse_openrouter_generation_id(record.get("generationId"))
        if parsed:
            found.append(parsed)
    if extra is not None:
        found.extend(extract_generation_ids_from_payload(extra))
    if stdout:
        found.extend(extract_generation_ids_from_kimi_stdout(stdout))
    return unique_openrouter_generation_ids(found)


def build_generations_document(
    ids: list[str],
    *,
    run_id: str | None = None,
    records: list[dict[str, object]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object] | None:
    official = unique_openrouter_generation_ids(ids)
    if not official:
        return None
    rows = records if records is not None else load_generation_records(environ=environ)
    by_node: dict[str, list[str]] = {}
    for row in rows:
        parsed = parse_openrouter_generation_id(row.get("generationId"))
        if not parsed:
            continue
        nid = row.get("nodeId")
        key = nid if isinstance(nid, str) and nid.strip() else "run"
        by_node.setdefault(key, [])
        if parsed not in by_node[key]:
            by_node[key].append(parsed)
    nodes = [
        {
            "nodeId": nid,
            "generationId": node_ids[0],
            "generationIds": unique_openrouter_generation_ids(node_ids),
        }
        for nid, node_ids in sorted(by_node.items())
        if unique_openrouter_generation_ids(node_ids)
    ]
    return {
        "runId": run_id or run_id_from_environ(environ),
        "source": "openrouter",
        "generationId": official[0],
        "generationIds": official,
        "openrouterGenerationIds": official,
        "nodes": nodes,
    }


def persist_generations_document(
    document: Mapping[str, object],
    dest: Path,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def persist_openrouter_generations(
    *,
    config: RunConfig | None = None,
    environ: Mapping[str, str] | None = None,
    extra: object = None,
    stdout: str | None = None,
) -> Path | None:
    """Write the sibling artifact only when official ids exist."""
    env = os.environ if environ is None else environ
    ids = collect_generation_ids(environ=env, extra=extra, stdout=stdout)
    document = build_generations_document(
        ids,
        run_id=config.run_id if config is not None else run_id_from_environ(env),
        environ=env,
    )
    if document is None:
        return None
    outputs = config.outputs_dir if config is not None else None
    dest = artifact_path(env, outputs_dir=outputs)
    persist_generations_document(document, dest)
    return dest


def _patch_httpx_send() -> bool:
    """Capture ``X-Generation-Id`` from the raw HTTP response when present."""
    try:
        import httpx
    except ImportError:
        return False

    def _from_response(response: object) -> None:
        try:
            record_from_response(response, source="x-generation-id")
        except Exception:
            return

    client_send = getattr(httpx.Client, "send", None)
    if client_send is not None and not getattr(client_send, "_midkernel_generation_id", False):

        def wrapped_send(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
            response = client_send(self, request, *args, **kwargs)
            _from_response(response)
            return response

        wrapped_send._midkernel_generation_id = True  # type: ignore[attr-defined]
        httpx.Client.send = wrapped_send  # type: ignore[method-assign]

    async_send = getattr(getattr(httpx, "AsyncClient", None), "send", None)
    if async_send is not None and not getattr(async_send, "_midkernel_generation_id", False):

        async def wrapped_async_send(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
            response = await async_send(self, request, *args, **kwargs)
            _from_response(response)
            return response

        wrapped_async_send._midkernel_generation_id = True  # type: ignore[attr-defined]
        httpx.AsyncClient.send = wrapped_async_send  # type: ignore[method-assign]

    return True


def _patch_completions_create_for_ids(provider: object) -> bool:
    client = getattr(provider, "client", None)
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    create = getattr(completions, "create", None)
    if create is None:
        return False
    if getattr(create, "_midkernel_generation_id", False):
        return True

    def wrapped_create(*args, **kwargs):
        return wrap_openai_create_result(create(*args, **kwargs), source="openai_legacy")

    wrapped_create._midkernel_generation_id = True  # type: ignore[attr-defined]
    completions.create = wrapped_create
    return True


def install_openai_legacy_generation_id_capture(*, required: bool = False) -> bool:
    """Hook OpenAILegacy + httpx so official ``gen-…`` ids are recorded.

    Must not change ``max_tokens`` or request bodies. Capture failure is
    non-fatal unless ``required`` (tests). Missing ids → no artifact, no USD.
    """
    _patch_httpx_send()
    try:
        from kosong.contrib.chat_provider.openai_legacy import OpenAILegacy
    except ImportError:
        if required:
            raise OpenRouterGenerationCaptureError(
                "OpenAILegacy generation-id capture did not install (kosong openai_legacy missing)"
            ) from None
        return False

    init = OpenAILegacy.__init__
    if getattr(init, "_midkernel_generation_id", False):
        return True

    def wrapped_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        _patch_completions_create_for_ids(self)

    wrapped_init._midkernel_generation_id = True  # type: ignore[attr-defined]
    OpenAILegacy.__init__ = wrapped_init  # type: ignore[method-assign]

    original_with = getattr(OpenAILegacy, "with_generation_kwargs", None)
    if original_with is not None and not getattr(original_with, "_midkernel_generation_id", False):

        def wrapped_with(self, **kwargs):
            updated = original_with(self, **kwargs)
            _patch_completions_create_for_ids(updated)
            return updated

        wrapped_with._midkernel_generation_id = True  # type: ignore[attr-defined]
        OpenAILegacy.with_generation_kwargs = wrapped_with  # type: ignore[method-assign]

    original_generate = getattr(OpenAILegacy, "generate", None)
    if original_generate is not None and not getattr(original_generate, "_midkernel_generation_id", False):

        async def wrapped_generate(self, *args, **kwargs):
            _patch_completions_create_for_ids(self)
            return await original_generate(self, *args, **kwargs)

        wrapped_generate._midkernel_generation_id = True  # type: ignore[attr-defined]
        OpenAILegacy.generate = wrapped_generate  # type: ignore[method-assign]

    return True
