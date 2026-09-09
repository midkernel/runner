"""Cap OpenRouter completion tokens so in-task GOAL hunters do not 402.

GOAL ``cmtufzqzo0003k004mt2w0m9c``: kimi-openrouter bin + ``config.toml``,
``key_set=yes``. OpenRouter rejected ``max_tokens`` up to **131072**
(wallet could afford ~68k–120k) with ``in_flight_budget_exhausted``.

kimi-cli 1.49 ``openai_legacy`` does not send ``max_tokens``. OpenRouter then
reserves the model max (131072). ``max_context_size`` (262144) must never be
copied into ``max_tokens`` — half of that is also 131072.

Default is **32768** (safe under the 68k floor). Hard ceiling **65536**.
Never default 131072.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_MAX_TOKENS = 32_768
MAX_SAFE_MAX_TOKENS = 65_536
UNSAFE_OPENROUTER_DEFAULT = 131_072

MAX_TOKENS_ENV_KEYS = (
    "MIDKERNEL_OPENROUTER_MAX_TOKENS",
    "OPENROUTER_MAX_TOKENS",
    "KIMI_MODEL_MAX_COMPLETION_TOKENS",
    "KIMI_MODEL_MAX_TOKENS",
)

MAX_TOKEN_CLI_FLAGS = frozenset(
    {
        "--max-tokens",
        "--max-completion-tokens",
        "--max_tokens",
        "--max_completion_tokens",
    }
)

SITECUSTOMIZE_NAME = "sitecustomize.py"
SITECUSTOMIZE_DIRNAME = "py_path"


def clamp_max_tokens(value: int) -> int:
    """Return a wallet-safe completion cap. Never 131072 as a default."""
    if value <= 0:
        return DEFAULT_MAX_TOKENS
    if value >= UNSAFE_OPENROUTER_DEFAULT:
        return DEFAULT_MAX_TOKENS
    return min(value, MAX_SAFE_MAX_TOKENS)


def parse_max_tokens(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None


def resolve_max_tokens(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    for key in MAX_TOKENS_ENV_KEYS:
        parsed = parse_max_tokens(env.get(key))
        if parsed is not None:
            return clamp_max_tokens(parsed)
    return DEFAULT_MAX_TOKENS


def max_tokens_env(max_tokens: int | None = None) -> dict[str, str]:
    cap = DEFAULT_MAX_TOKENS if max_tokens is None else clamp_max_tokens(max_tokens)
    text = str(cap)
    return {
        "MIDKERNEL_OPENROUTER_MAX_TOKENS": text,
        "OPENROUTER_MAX_TOKENS": text,
        "KIMI_MODEL_MAX_COMPLETION_TOKENS": text,
        "KIMI_MODEL_MAX_TOKENS": text,
    }


def sitecustomize_dir(share_dir: Path) -> Path:
    return Path(share_dir) / SITECUSTOMIZE_DIRNAME


def sitecustomize_source() -> str:
    return (
        "# Midkernel: cap openai_legacy max_tokens. Do not treat max_context_size\n"
        "# as the completion budget (GOAL cmtufzqzo0003k004mt2w0m9c 402).\n"
        "try:\n"
        "    from midkernel_runner.openrouter_tokens import install_openai_legacy_max_tokens_cap\n"
        "    install_openai_legacy_max_tokens_cap()\n"
        "except Exception:\n"
        "    pass\n"
    )


def write_openrouter_sitecustomize(share_dir: Path) -> Path:
    directory = sitecustomize_dir(share_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SITECUSTOMIZE_NAME
    path.write_text(sitecustomize_source(), encoding="utf-8")
    return path


def prepend_pythonpath(environ: dict[str, str], directory: Path) -> dict[str, str]:
    added = str(directory)
    current = (environ.get("PYTHONPATH") or "").strip()
    parts = [part for part in current.split(os.pathsep) if part and part != added]
    environ["PYTHONPATH"] = os.pathsep.join([added, *parts]) if parts else added
    return environ


def clamp_max_token_cli_flags(argv: list[str], max_tokens: int) -> list[str]:
    """Rewrite ``--max-tokens N`` if present. Do not invent the flag (kimi 1.49).

    Never copy ``max_context_size`` / 131072 into the value.
    """
    cap = clamp_max_tokens(max_tokens)
    out: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        flag = arg.split("=", 1)[0]
        if flag in MAX_TOKEN_CLI_FLAGS:
            if "=" in arg:
                parsed = parse_max_tokens(arg.split("=", 1)[1])
                out.append(f"{flag}={cap if parsed is None else clamp_max_tokens(parsed)}")
                index += 1
                continue
            out.append(arg)
            if index + 1 < len(argv):
                parsed = parse_max_tokens(argv[index + 1])
                out.append(str(cap if parsed is None else clamp_max_tokens(parsed)))
                index += 2
                continue
            index += 1
            continue
        out.append(arg)
        index += 1
    return out


def install_openai_legacy_max_tokens_cap(environ: Mapping[str, str] | None = None) -> bool:
    """Patch kosong ``OpenAILegacy`` so requests send a capped ``max_tokens``.

    kimi-cli 1.49 builds ``openai_legacy`` with empty generation kwargs.
    OpenRouter then reserves the model max (131072) and 402s on a thin wallet.
    The front must not substitute ``max_context_size``.
    """
    cap = resolve_max_tokens(environ)
    try:
        from kosong.contrib.chat_provider.openai_legacy import OpenAILegacy
    except ImportError:
        return False

    init = OpenAILegacy.__init__
    if getattr(init, "_midkernel_max_tokens_capped", False):
        return True

    def wrapped_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        current = self._generation_kwargs.get("max_tokens")
        if current is None or current >= UNSAFE_OPENROUTER_DEFAULT or current > cap:
            self._generation_kwargs["max_tokens"] = cap

    wrapped_init._midkernel_max_tokens_capped = True  # type: ignore[attr-defined]
    OpenAILegacy.__init__ = wrapped_init  # type: ignore[method-assign]

    original_with = OpenAILegacy.with_generation_kwargs

    def wrapped_with(self, **kwargs):
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            try:
                kwargs["max_tokens"] = clamp_max_tokens(int(kwargs["max_tokens"]))
            except (TypeError, ValueError):
                kwargs["max_tokens"] = cap
        return original_with(self, **kwargs)

    OpenAILegacy.with_generation_kwargs = wrapped_with  # type: ignore[method-assign]
    return True
