"""Cap OpenRouter completion tokens so in-task GOAL hunters do not 402.

GOAL ``cmtufzqzo0003k004mt2w0m9c``: kimi-openrouter bin + ``config.toml``,
``key_set=yes``. OpenRouter rejected ``max_tokens`` up to **131072**
(wallet could afford ~68k–120k) with ``in_flight_budget_exhausted``.

QA ``cmtulxq7v0003l2046bhhc3yl`` surface-split then 402'd
``openrouter_key_limit``: the wire sent ``max_tokens=32768`` but the
``$10``/mo key could only afford ~13k–25k.

kimi-cli 1.49 ``openai_legacy`` does not send ``max_tokens``. OpenRouter then
reserves the model max (131072). ``max_context_size`` (262144) must never be
copied into ``max_tokens`` — half of that is also 131072.

Default is **16384** (under the ~13k–25k key-limit floor). Hard allowed max
**65536**. Any value ``> 65536`` or ``>= 131072`` becomes **16384** — never
``min(value, 65536)`` (that would turn 80000 into 65536).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_MAX_TOKENS = 16_384
MAX_SAFE_MAX_TOKENS = 65_536
UNSAFE_OPENROUTER_DEFAULT = 131_072
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# First-wins order — lockstep with playbooks.
MAX_TOKENS_ENV_KEYS = (
    "MIDKERNEL_OPENROUTER_MAX_TOKENS",
    "OPENROUTER_MAX_TOKENS",
    "KIMI_MAX_TOKENS",
    "KIMI_MODEL_MAX_TOKENS",
    "KIMI_MODEL_MAX_COMPLETION_TOKENS",
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
INJECTION_PROXY_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_CONFIG_BASE_URL_PREFIX = "base_url"


class OpenRouterMaxTokensCapError(RuntimeError):
    """sitecustomize / OpenAILegacy max_tokens patch did not install."""


def clamp_max_tokens(value: int) -> int:
    """Return a wallet-safe completion cap. Never 131072 as a default.

    Allowed range is 1..65536. Values ``> 65536`` or ``>= 131072`` become
    16384 — not ``min(value, 65536)``, which would turn 80000 into 65536.
    """
    if value <= 0:
        return DEFAULT_MAX_TOKENS
    if value > MAX_SAFE_MAX_TOKENS or value >= UNSAFE_OPENROUTER_DEFAULT:
        return DEFAULT_MAX_TOKENS
    return value


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
    return {key: text for key in MAX_TOKENS_ENV_KEYS}


def is_injection_proxy_url(url: str | None) -> bool:
    """True when playbooks wrap_kimi already pointed traffic at localhost."""
    text = (url or "").strip()
    if not text:
        return False
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if host in INJECTION_PROXY_HOSTS:
        return True
    lowered = text.lower()
    return "127.0.0.1" in lowered or "localhost" in lowered or "[::1]" in lowered


def read_config_base_url(path: Path) -> str | None:
    """Read ``base_url`` from an existing kimi ``config.toml`` if present."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith(_CONFIG_BASE_URL_PREFIX):
            continue
        _, _, rest = line.partition("=")
        value = rest.strip().strip('"').strip("'")
        if value:
            return value
    return None


def resolve_provider_base_url(
    environ: Mapping[str, str] | None = None,
    *,
    explicit: str | None = None,
    config_path: Path | None = None,
) -> str:
    """Keep wrap_kimi's localhost proxy. Never rewrite it to OpenRouter."""
    candidates: list[str] = []
    if explicit and explicit.strip():
        candidates.append(explicit.strip())
    env = os.environ if environ is None else environ
    for key in ("OPENAI_BASE_URL", "KIMI_BASE_URL"):
        value = (env.get(key) or "").strip()
        if value:
            candidates.append(value)
    if config_path is not None:
        existing = read_config_base_url(Path(config_path))
        if existing:
            candidates.append(existing)
    for url in candidates:
        if is_injection_proxy_url(url):
            return url.rstrip("/")
    return OPENROUTER_BASE_URL


def sitecustomize_dir(share_dir: Path) -> Path:
    return Path(share_dir) / SITECUSTOMIZE_DIRNAME


def sitecustomize_source() -> str:
    return (
        "# Midkernel: cap openai_legacy max_tokens (default 16384). Do not treat\n"
        "# max_context_size as the completion budget (GOAL cmtufzqzo0003k004mt2w0m9c\n"
        "# 402; QA cmtulxq7v0003l2046bhhc3yl openrouter_key_limit at 32768).\n"
        "# Fail loud if the OpenAILegacy cap does not install — a swallowed\n"
        "# error would leave 131072 on the wire. Also strip max_completion_tokens.\n"
        "# After the cap: record official OpenRouter gen- ids (never invent USD).\n"
        "# 429 retries live in playbooks wrap_kimi. Account 20 RPM is OpenRouter tier.\n"
        "from midkernel_runner.openrouter_tokens import install_openai_legacy_max_tokens_cap\n"
        "from midkernel_runner.openrouter_generations import install_openai_legacy_generation_id_capture\n"
        "install_openai_legacy_max_tokens_cap(required=True)\n"
        "install_openai_legacy_generation_id_capture()\n"
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


def apply_max_tokens_to_kwargs(kwargs: dict, cap: int) -> dict:
    """Force outbound chat.completions to send a clamped ``max_tokens``.

    Strip ``max_completion_tokens`` if present so it cannot reserve 131072.
    ``max_tokens`` is always set to the clamped value.
    """
    raw = kwargs.get("max_tokens")
    if raw is None:
        raw = kwargs.get("max_completion_tokens")
    kwargs.pop("max_completion_tokens", None)

    safe = cap
    if raw is not None:
        try:
            safe = clamp_max_tokens(int(raw))
        except (TypeError, ValueError):
            safe = cap
    if safe > cap:
        safe = cap
    kwargs["max_tokens"] = safe
    return kwargs


def _ensure_generation_cap(provider: object, cap: int) -> None:
    generation = getattr(provider, "_generation_kwargs", None)
    if not isinstance(generation, dict):
        return
    apply_max_tokens_to_kwargs(generation, cap)


def _record_create_result(result: object) -> object:
    """Best-effort official gen- id capture. Never changes the completion."""
    try:
        from midkernel_runner.openrouter_generations import wrap_openai_create_result

        return wrap_openai_create_result(result, source="openai_legacy")
    except Exception:
        return result


def _patch_completions_create(provider: object, cap: int) -> None:
    client = getattr(provider, "client", None)
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    create = getattr(completions, "create", None)
    if create is None:
        raise OpenRouterMaxTokensCapError(
            "OpenAILegacy client.chat.completions.create is missing; "
            "refusing to leave max_tokens=131072 on the wire"
        )
    if getattr(create, "_midkernel_max_tokens_capped", False):
        return

    def wrapped_create(*args, **kwargs):
        apply_max_tokens_to_kwargs(kwargs, cap)
        return _record_create_result(create(*args, **kwargs))

    wrapped_create._midkernel_max_tokens_capped = True  # type: ignore[attr-defined]
    completions.create = wrapped_create


def install_openai_legacy_max_tokens_cap(
    environ: Mapping[str, str] | None = None,
    *,
    required: bool = False,
) -> bool:
    """Patch kosong ``OpenAILegacy`` so requests send a capped ``max_tokens``.

    kimi-cli 1.49 builds ``openai_legacy`` with empty generation kwargs.
    OpenRouter then reserves the model max (131072) and 402s on a thin wallet.
    The front must not substitute ``max_context_size``.

    When ``required`` is true (sitecustomize on the kimi.bin PYTHONPATH),
    failure is fatal. A silent skip would put 131072 on the wire.
    """
    cap = resolve_max_tokens(environ)
    try:
        from kosong.contrib.chat_provider.openai_legacy import OpenAILegacy
    except ImportError:
        if required:
            raise OpenRouterMaxTokensCapError(
                "OpenAILegacy max_tokens cap did not install (kosong "
                "openai_legacy missing). Refusing to send uncapped "
                "max_tokens=131072."
            ) from None
        return False

    init = OpenAILegacy.__init__
    if getattr(init, "_midkernel_max_tokens_capped", False):
        return True

    def wrapped_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        _ensure_generation_cap(self, cap)
        _patch_completions_create(self, cap)

    wrapped_init._midkernel_max_tokens_capped = True  # type: ignore[attr-defined]
    OpenAILegacy.__init__ = wrapped_init  # type: ignore[method-assign]

    original_with = OpenAILegacy.with_generation_kwargs

    def wrapped_with(self, **kwargs):
        apply_max_tokens_to_kwargs(kwargs, cap)
        updated = original_with(self, **kwargs)
        _ensure_generation_cap(updated, cap)
        _patch_completions_create(updated, cap)
        return updated

    OpenAILegacy.with_generation_kwargs = wrapped_with  # type: ignore[method-assign]

    original_generate = getattr(OpenAILegacy, "generate", None)
    if original_generate is not None and not getattr(
        original_generate, "_midkernel_max_tokens_capped", False
    ):

        async def wrapped_generate(self, *args, **kwargs):
            _ensure_generation_cap(self, cap)
            _patch_completions_create(self, cap)
            return await original_generate(self, *args, **kwargs)

        wrapped_generate._midkernel_max_tokens_capped = True  # type: ignore[attr-defined]
        OpenAILegacy.generate = wrapped_generate  # type: ignore[method-assign]

    return True
