"""Invoke OpenCode as a one-shot harness against the cloned repo.

Why OpenCode-only (not an agentflow multi-node graph) for security-review:
the public playbook is kind=single-skill / one-shot. agentflow's ECS runner
is a *control plane* that RunTasks images; Midkernel already owns that on
the app side. Running agentflow-inside-the-task to launch nested Fargate
tasks would ignore Midkernel's explicit networking and double Spot cost.
agentflow also has no OpenCode adapter (codex/claude/kimi/pi only).

This image still installs agentflow so it can be the ECR target image for
future multi-node playbooks (`target.image` = midkernel-agentflow-agents).
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

OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "opencode")


class OpenCodeError(RuntimeError):
    """OpenCode did not produce a real report."""


def write_opencode_auth(home: Path, api_key: str) -> Path:
    auth_dir = home / ".local" / "share" / "opencode"
    auth_dir.mkdir(parents=True, exist_ok=True)
    auth_path = auth_dir / "auth.json"
    auth_path.write_text(
        json.dumps({"openrouter": {"type": "api", "key": api_key}}, indent=2) + "\n",
        encoding="utf-8",
    )
    auth_path.chmod(0o600)
    return auth_path


def write_opencode_config(home: Path, config: RunConfig) -> Path:
    cfg_dir = home / ".config" / "opencode"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "opencode.json"
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": config.openrouter_model,
        "permission": {
            "*": "allow",
        },
    }
    cfg_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return cfg_path


def build_prompt(config: RunConfig, playbook_body: str) -> str:
    threat = config.threat_pin or "(none)"
    return "\n".join(
        [
            "You are Midkernel Scan executing a public playbook as a one-shot review on AWS ECS Fargate.",
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


def _looks_like_report(text: str) -> bool:
    try:
        validate_report(text)
        return True
    except ReportError:
        return False


def run_opencode(
    config: RunConfig,
    secrets: HarnessSecrets,
    playbook_body: str,
    *,
    run: callable = subprocess.run,
    which: callable = shutil.which,
) -> str:
    if which(OPENCODE_BIN) is None and OPENCODE_BIN == "opencode":
        raise OpenCodeError(
            "opencode is not on PATH. The image must install the OpenCode CLI "
            "(not an AI Gateway shortcut, not a fake report writer)."
        )

    home = Path(os.environ.get("HOME") or "/home/agent")
    write_opencode_auth(home, secrets.openrouter_api_key)
    write_opencode_config(home, config)
    Path(config.outputs_dir).mkdir(parents=True, exist_ok=True)
    report_path = Path(config.report_path)
    if report_path.exists():
        report_path.unlink()

    prompt = build_prompt(config, playbook_body)
    cmd = [
        OPENCODE_BIN,
        "run",
        "--dir",
        config.repo_dir,
        "--model",
        config.openrouter_model,
        "--variant",
        config.openrouter_variant,
        "--title",
        f"midkernel {config.playbook_slug} {config.run_id}",
        "--auto",
        prompt,
    ]

    env = os.environ.copy()
    env["OPENROUTER_API_KEY"] = secrets.openrouter_api_key
    env["HOME"] = str(home)
    # Never point OpenCode at Vercel AI Gateway / Bedrock.
    env.pop("AI_GATEWAY_API_KEY", None)
    env.pop("VERCEL_OIDC_TOKEN", None)
    env.pop("AWS_BEDROCK_REGION", None)

    result = run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=config.timeout_seconds,
        cwd=config.repo_dir,
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        # Still accept a written report if the agent finished the file
        # then exited non-zero on a later tool error.
        if report_path.is_file():
            try:
                return persist_report(report_path, report_path.read_text(encoding="utf-8"))
            except ReportError:
                pass
        raise OpenCodeError(
            "OpenCode exited non-zero without a valid report.md. "
            f"exit={result.returncode} stderr={stderr[-2000:]}"
        )

    if report_path.is_file():
        return persist_report(report_path, report_path.read_text(encoding="utf-8"))

    if stdout and _looks_like_report(stdout):
        return persist_report(report_path, stdout)

    raise OpenCodeError(
        "OpenCode finished but did not produce a valid report.md. "
        "Refusing to invent or upload a stub success report."
    )
