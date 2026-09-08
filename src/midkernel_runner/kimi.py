"""Kimi CLI harness + OpenRouter wiring for agentflow ECS nodes.

agentenv/agentflow's Kimi adapter launches:

    kimi --print --output-format stream-json --yolo -p <prompt> [--model ...]

For OpenRouter, kimi-cli 1.49 uses an ``openai_legacy`` provider and reads
``OPENAI_API_KEY`` (not ``KIMI_API_KEY``). This module writes ``~/.kimi/config.toml``
with OpenRouter aliases so both Midkernel and stock agentflow ``--model`` values
resolve.
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
    aliases = ["kimi-k3", slug, f"openrouter/{slug}"]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in aliases:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


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


def kimi_config_path(home: Path | None = None) -> Path:
    override = os.environ.get("KIMI_SHARE_DIR")
    if override:
        return Path(override) / "config.toml"
    root = Path(home or os.environ.get("HOME") or "/home/agent")
    return root / ".kimi" / "config.toml"


def write_kimi_openrouter_config(
    api_key: str,
    model: str,
    *,
    home: Path | None = None,
) -> Path:
    path = kimi_config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_kimi_openrouter_config(api_key, model), encoding="utf-8")
    path.chmod(0o600)
    return path


def export_kimi_openrouter_env(environ: dict[str, str], api_key: str) -> dict[str, str]:
    """openai_legacy reads OPENAI_API_KEY; agentflow auth_setup also sets KIMI_*."""
    out = dict(environ)
    out["OPENROUTER_API_KEY"] = api_key
    out["OPENAI_API_KEY"] = api_key
    out["OPENAI_BASE_URL"] = OPENROUTER_BASE_URL
    out.setdefault("KIMI_API_KEY", api_key)
    out.setdefault("MOONSHOT_API_KEY", api_key)
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
            "(agentflow's Kimi harness). OpenCode is deferred."
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

    env = export_kimi_openrouter_env(os.environ.copy(), secrets.openrouter_api_key)
    env["HOME"] = str(home)
    env["KIMI_SHARE_DIR"] = str(home / ".kimi")

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
