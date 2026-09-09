"""Front for in-task ``kimi.bin`` so playbooks ``--config`` cannot drop the LLM.

Playbooks ``_node_io.wrap_kimi`` execs ``MIDKERNEL_KIMI_BIN`` with agentflow
argv. That includes ``--model moonshotai/kimi-k3`` (or judge-b's OpenRouter
slug) plus ``--config`` TOML that only defines ``models.midkernel``.
kimi-cli 1.49 then builds an empty ``type=kimi`` provider and prints
``LLM not set`` (GOAL ``cmtudm8f20003i90462yr3vxq`` threat-model,
security-review ``cmtudf0470003jp040742shv6`` review).

This process is what ``MIDKERNEL_KIMI_BIN`` points at in graph mode. It
rewrites argv to ``--config-file $KIMI_SHARE_DIR/config.toml`` (aliases
include ``midkernel`` and the requested ``--model``) and execs the real
binary. Do not mention ``KIMI_REAL_BIN`` or ``midkernel-publish-report``
in this file — playbooks ``is_report_md_wrapper`` greps those strings.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from midkernel_runner.kimi import (
    DEFAULT_OPENROUTER_SLUG,
    write_kimi_openrouter_config,
)
from midkernel_runner.mode import IMAGE_KIMI_BIN
from midkernel_runner.openrouter_tokens import (
    clamp_max_token_cli_flags,
    max_tokens_env,
    prepend_pythonpath,
    resolve_max_tokens,
    sitecustomize_dir,
    write_openrouter_sitecustomize,
)

PROBE_FLAGS = frozenset({"--version", "-V", "-h", "--help"})


def model_from_argv(argv: list[str]) -> str | None:
    for index, arg in enumerate(argv):
        if arg in {"--model", "-m"} and index + 1 < len(argv):
            value = argv[index + 1].strip()
            return value or None
        if arg.startswith("--model="):
            value = arg.split("=", 1)[1].strip()
            return value or None
    return None


def drop_config_flags(argv: list[str]) -> list[str]:
    """Remove ``--config`` / ``--config-file`` (playbooks midkernel-only TOML)."""
    out: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"--config", "--config-file"}:
            index += 2 if index + 1 < len(argv) else 1
            continue
        if arg.startswith("--config=") or arg.startswith("--config-file="):
            index += 1
            continue
        out.append(arg)
        index += 1
    return out


def rewrite_kimi_argv(
    argv: list[str],
    *,
    config_file: str | None,
    max_tokens: int | None = None,
) -> list[str]:
    if argv and argv[0] in PROBE_FLAGS:
        return list(argv)
    out = drop_config_flags(argv)
    # Clamp a caller-supplied completion flag. Do not invent --max-tokens
    # (kimi-cli 1.49 has no such option) and never copy max_context_size.
    if max_tokens is not None:
        out = clamp_max_token_cli_flags(out, max_tokens)
    if config_file:
        return ["--config-file", config_file, *out]
    return out


def resolve_real_bin(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    candidate = (env.get("MIDKERNEL_GRAPH_KIMI_BIN") or IMAGE_KIMI_BIN).strip()
    if not candidate or candidate.endswith("kimi-openrouter") or "kimi_graph_bin" in candidate:
        return IMAGE_KIMI_BIN
    return candidate


def prepare_config_file(argv: list[str], *, environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    share = (env.get("KIMI_SHARE_DIR") or "").strip()
    if not share:
        return None
    api_key = (
        env.get("OPENROUTER_API_KEY") or env.get("OPENAI_API_KEY") or env.get("KIMI_API_KEY") or ""
    ).strip()
    model = (
        model_from_argv(argv)
        or (env.get("KIMI_MODEL_NAME") or env.get("OPENROUTER_MODEL") or env.get("MODEL") or "")
        or DEFAULT_OPENROUTER_SLUG
    ).strip()
    if api_key:
        return str(
            write_kimi_openrouter_config(api_key, model, share_dir=Path(share), environ=env)
        )
    write_openrouter_sitecustomize(Path(share))
    candidate = Path(share) / "config.toml"
    return str(candidate) if candidate.is_file() else None


def apply_front_max_tokens_env(environ: dict[str, str] | None = None) -> int:
    """Export the capped completion budget onto the process that execs kimi.bin."""
    env = os.environ if environ is None else environ
    cap = resolve_max_tokens(env)
    env.update(max_tokens_env(cap))
    share = (env.get("KIMI_SHARE_DIR") or "").strip()
    if share:
        write_openrouter_sitecustomize(Path(share))
        prepend_pythonpath(env, sitecustomize_dir(Path(share)))
    return cap


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    real = resolve_real_bin()
    if args and args[0] in PROBE_FLAGS:
        os.execv(real, [real, *args])
    config_file = prepare_config_file(args)
    cap = apply_front_max_tokens_env()
    rewritten = rewrite_kimi_argv(args, config_file=config_file, max_tokens=cap)
    os.execv(real, [real, *rewritten])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
