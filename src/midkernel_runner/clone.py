"""Clone the target GitHub repository using the harness github-token."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from midkernel_runner.config import RunConfig
from midkernel_runner.secrets import HarnessSecrets


class CloneError(RuntimeError):
    """git clone failed."""


def clone_url_with_token(https_url: str, token: str) -> str:
    if not https_url.startswith("https://"):
        raise CloneError("Only https GitHub clone URLs are supported")
    # Installation tokens and PATs both work as x-access-token.
    rest = https_url.removeprefix("https://")
    return f"https://x-access-token:{token}@{rest}"


def clone_repository(
    config: RunConfig,
    secrets: HarnessSecrets,
    *,
    run: callable = subprocess.run,
) -> Path:
    dest = Path(config.repo_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").exists():
        return dest
    if any(dest.iterdir()):
        raise CloneError(f"Clone destination is not empty: {dest}")

    url = clone_url_with_token(config.github_clone_url, secrets.github_token)
    cmd = ["git", "clone", "--depth", "1"]
    if config.github_ref:
        cmd.extend(["--branch", config.github_ref])
    cmd.extend([url, str(dest)])

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Do not leak the token via git's trace logs.
    env.pop("GIT_TRACE", None)
    env.pop("GIT_CURL_VERBOSE", None)

    result = run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").replace(secrets.github_token, "***")
        raise CloneError(f"git clone failed for {config.github_owner}/{config.github_name}: {stderr.strip()}")
    return dest
