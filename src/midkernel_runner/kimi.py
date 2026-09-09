"""Kimi CLI harness + OpenRouter wiring for agentflow ECS nodes.

agentenv/agentflow's Kimi adapter launches:

    kimi --print --output-format stream-json --yolo -p <prompt> [--model ...]

For OpenRouter, kimi-cli 1.49 uses an ``openai_legacy`` provider and reads
``OPENAI_API_KEY`` (not ``KIMI_API_KEY``). This module writes
``$KIMI_SHARE_DIR/config.toml`` (in-task: ``$WORKDIR/.midkernel/kimi``) and
``~/.kimi/config.toml`` with OpenRouter aliases so Midkernel, playbooks
``midkernel``, and stock agentflow ``--model`` values resolve. When
``--model`` is missing from playbooks ``--config``, kimi-cli falls back to
an empty ``type=kimi`` provider; ``KIMI_BASE_URL`` + ``KIMI_MODEL_NAME``
keep ``create_llm`` from returning None (``LLM not set``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from midkernel_runner.config import RunConfig
from midkernel_runner.report import ReportError, persist_report, validate_report
from midkernel_runner.secrets import HarnessSecrets

KIMI_BIN = os.environ.get("KIMI_BIN", "kimi")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_SLUG = "moonshotai/kimi-k3"
DEFAULT_CONTEXT = 262_144


class KimiError(RuntimeError):
    """Kimi CLI did not produce a real report."""


def openrouter_slug(model: str) -> str:
    value = (model or "").strip()
    if value.startswith("openrouter/"):
        return value[len("openrouter/") :]
    return value or DEFAULT_OPENROUTER_SLUG


def kimi_model_aliases(model: str) -> list[str]:
    slug = openrouter_slug(model)
    # ``midkernel`` is the alias playbooks --config uses as default_model.
    # agentflow still passes ``--model <openrouter slug>``. Both must resolve.
    aliases = ["kimi-k3", "midkernel", slug, f"openrouter/{slug}"]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in aliases:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def graph_kimi_share_dir(workdir: str | Path) -> Path:
    """HOME-independent kimi-cli share dir for in-task graph nodes.

    Playbooks ``prepare`` rewrites ``$HOME/.kimi/config.toml`` to a
    ``midkernel``-only file, and ``BASH_ENV=/dev/null`` skips node-env
    prepare. kimi-cli 1.49 reads ``KIMI_SHARE_DIR/config.toml`` (else
    ``~/.kimi/config.toml``). Pin the share dir under WORKDIR so every
    Kimi node (review, threat-model, hunters, judges) sees the same
    OpenRouter config regardless of HOME/cwd.
    """
    return Path(workdir) / ".midkernel" / "kimi"


def render_kimi_openrouter_config(api_key: str, model: str) -> str:
    slug = openrouter_slug(model)
    aliases = kimi_model_aliases(model)
    default_model = aliases[0]
    lines = [
        f'default_model = "{default_model}"',
        "default_yolo = true",
        "telemetry = false",
        "",
        "[providers.openrouter]",
        'type = "openai_legacy"',
        f'base_url = "{OPENROUTER_BASE_URL}"',
        f'api_key = "{api_key}"',
        "",
    ]
    for name in aliases:
        lines.extend(
            [
                f'[models."{name}"]' if "/" in name else f"[models.{name}]",
                'provider = "openrouter"',
                f'model = "{slug}"',
                f"max_context_size = {DEFAULT_CONTEXT}",
                "",
            ]
        )
    return "\n".join(lines)


def kimi_config_path(home: Path | None = None, *, share_dir: Path | None = None) -> Path:
    if share_dir is not None:
        return Path(share_dir) / "config.toml"
    if home is not None:
        return Path(home) / ".kimi" / "config.toml"
    override = os.environ.get("KIMI_SHARE_DIR")
    if override:
        return Path(override) / "config.toml"
    root = Path(os.environ.get("HOME") or "/home/agent")
    return root / ".kimi" / "config.toml"


def write_kimi_openrouter_config(
    api_key: str,
    model: str,
    *,
    home: Path | None = None,
    share_dir: Path | None = None,
) -> Path:
    path = kimi_config_path(home, share_dir=share_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_kimi_openrouter_config(api_key, model), encoding="utf-8")
    path.chmod(0o600)
    return path


def export_kimi_openrouter_env(
    environ: dict[str, str],
    api_key: str,
    *,
    model: str | None = None,
    share_dir: str | Path | None = None,
) -> dict[str, str]:
    """Env kimi.bin needs when playbooks exec it with BASH_ENV=/dev/null.

    * ``openai_legacy`` (file / ``--config`` hit): ``OPENAI_API_KEY`` + ``OPENAI_BASE_URL``.
    * Dummy ``type=kimi`` fallback (``--model`` not in ``--config``): kimi-cli
      1.49 builds an empty Moonshot provider unless ``KIMI_BASE_URL`` and
      ``KIMI_MODEL_NAME`` are set; otherwise ``create_llm`` returns None and
      stdout is ``LLM not set``.
    """
    out = dict(environ)
    slug = openrouter_slug(
        model or out.get("OPENROUTER_MODEL") or out.get("MODEL") or DEFAULT_OPENROUTER_SLUG
    )
    out["OPENROUTER_API_KEY"] = api_key
    out["OPENAI_API_KEY"] = api_key
    out["OPENAI_BASE_URL"] = OPENROUTER_BASE_URL
    out["KIMI_API_KEY"] = api_key
    out["MOONSHOT_API_KEY"] = api_key
    out["KIMI_BASE_URL"] = OPENROUTER_BASE_URL
    out["KIMI_MODEL_NAME"] = slug
    out.setdefault("OPENROUTER_MODEL", slug)
    if share_dir is not None:
        out["KIMI_SHARE_DIR"] = str(share_dir)
    out.pop("AI_GATEWAY_API_KEY", None)
    out.pop("VERCEL_OIDC_TOKEN", None)
    out.pop("AWS_BEDROCK_REGION", None)
    return out


def build_prompt(config: RunConfig, playbook_body: str) -> str:
    threat = config.threat_pin or "(none)"
    return "\n".join(
        [
            "You are Midkernel Scan running as an agentflow ECS node with the Kimi CLI harness.",
            "Follow the playbook body exactly:",
            playbook_body,
            "",
            f"Repository: {config.github_owner}/{config.github_name}",
            f"Run id: {config.run_id}",
            f"Profile: {config.scan_profile}",
            f"Threat pin (not a fourth profile): {threat}",
            "",
            "Review the project in the current working directory.",
            "Write a concrete markdown security review to exactly this path:",
            config.report_path,
            "",
            "The report MUST include:",
            "- a short summary heading",
            "- findings with severity and evidence paths (or an explicit empty result)",
            "- a line `Findings: N` with the integer count (0 if none)",
            "",
            "Do not invent files, CVEs, or secrets that are not in the tree.",
            "If the tree is thin, say so.",
            "Do not write a stub, placeholder, or success-without-review report.",
            "When finished, the file at the path above must exist and be the full report.",
        ]
    )


def extract_text_from_kimi_stdout(stdout: str) -> str:
    text = (stdout or "").strip()
    if not text:
        return ""
    try:
        validate_report(text)
        return text
    except ReportError:
        pass

    chunks: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("text", "message", "content", "delta"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
            elif isinstance(value, dict):
                inner = value.get("text") or value.get("content")
                if isinstance(inner, str) and inner.strip():
                    chunks.append(inner.strip())
        event = payload.get("event") or payload.get("type")
        if event in {"final", "result", "assistant"} and isinstance(payload.get("data"), str):
            chunks.append(payload["data"].strip())
    joined = "\n".join(chunks).strip()
    return joined


def run_kimi(
    config: RunConfig,
    secrets: HarnessSecrets,
    playbook_body: str,
    *,
    run: callable = subprocess.run,
    which: callable = shutil.which,
) -> str:
    binary = KIMI_BIN
    if which(binary) is None and binary == "kimi":
        raise KimiError(
            "kimi is not on PATH. The image must install kimi-cli "
            "(agentflow's Kimi harness, OpenRouter)."
        )

    home = Path(os.environ.get("HOME") or "/home/agent")
    write_kimi_openrouter_config(secrets.openrouter_api_key, config.openrouter_model, home=home)
    Path(config.outputs_dir).mkdir(parents=True, exist_ok=True)
    report_path = Path(config.report_path)
    if report_path.exists():
        report_path.unlink()

    prompt = build_prompt(config, playbook_body)
    model_flag = kimi_model_aliases(config.openrouter_model)[0]
    cmd = [
        binary,
        "--print",
        "--output-format",
        "stream-json",
        "--yolo",
        "-p",
        prompt,
        "--model",
        model_flag,
    ]

    env = export_kimi_openrouter_env(
        os.environ.copy(),
        secrets.openrouter_api_key,
        model=config.openrouter_model,
        share_dir=home / ".kimi",
    )
    env["HOME"] = str(home)

    workdir = config.repo_dir if Path(config.repo_dir).is_dir() else config.workdir
    result = run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=config.timeout_seconds,
        cwd=workdir,
    )
    stdout = result.stdout or ""
    stderr = (result.stderr or "").strip()

    if report_path.is_file():
        try:
            return persist_report(report_path, report_path.read_text(encoding="utf-8"))
        except ReportError:
            if result.returncode != 0:
                raise KimiError(
                    "Kimi wrote an invalid report.md. "
                    f"exit={result.returncode} stderr={stderr[-2000:]}"
                ) from None

    extracted = extract_text_from_kimi_stdout(stdout)
    if extracted:
        try:
            return persist_report(report_path, extracted)
        except ReportError:
            pass

    if result.returncode != 0:
        raise KimiError(
            "Kimi exited non-zero without a valid report.md. "
            f"exit={result.returncode} stderr={stderr[-2000:]}"
        )

    raise KimiError(
        "Kimi finished but did not produce a valid report.md. "
        "Refusing to invent or upload a stub success report."
    )
