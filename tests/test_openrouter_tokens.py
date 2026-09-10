import asyncio
import sys
import types

import pytest

from midkernel_runner.openrouter_tokens import (
    DEFAULT_MAX_TOKENS,
    MAX_SAFE_MAX_TOKENS,
    MAX_TOKENS_ENV_KEYS,
    UNSAFE_OPENROUTER_DEFAULT,
    OpenRouterMaxTokensCapError,
    apply_max_tokens_to_kwargs,
    clamp_max_token_cli_flags,
    clamp_max_tokens,
    install_openai_legacy_max_tokens_cap,
    is_injection_proxy_url,
    max_tokens_env,
    resolve_max_tokens,
    resolve_provider_base_url,
    sitecustomize_source,
    write_openrouter_sitecustomize,
)


def test_default_is_16384_never_131072():
    assert DEFAULT_MAX_TOKENS == 16_384
    assert resolve_max_tokens({}) == DEFAULT_MAX_TOKENS
    assert resolve_max_tokens({}) != UNSAFE_OPENROUTER_DEFAULT
    assert clamp_max_tokens(UNSAFE_OPENROUTER_DEFAULT) == DEFAULT_MAX_TOKENS
    assert clamp_max_tokens(262_144) == DEFAULT_MAX_TOKENS


def test_explicit_65536_is_allowed_ceiling():
    assert clamp_max_tokens(MAX_SAFE_MAX_TOKENS) == MAX_SAFE_MAX_TOKENS
    assert resolve_max_tokens({"OPENROUTER_MAX_TOKENS": "65536"}) == MAX_SAFE_MAX_TOKENS


def test_env_131072_is_treated_as_unsafe_default():
    assert resolve_max_tokens({"OPENROUTER_MAX_TOKENS": "131072"}) == DEFAULT_MAX_TOKENS
    assert resolve_max_tokens({"KIMI_MODEL_MAX_TOKENS": "131072"}) == DEFAULT_MAX_TOKENS
    assert resolve_max_tokens({"KIMI_MAX_TOKENS": "131072"}) == DEFAULT_MAX_TOKENS


def test_over_ceiling_becomes_16384_not_min_65536():
    """Playbooks lockstep: 80000 must not become 65536 via min(value, 65536)."""
    assert clamp_max_tokens(80_000) == DEFAULT_MAX_TOKENS
    assert clamp_max_tokens(80_000) != MAX_SAFE_MAX_TOKENS
    assert clamp_max_tokens(65_537) == DEFAULT_MAX_TOKENS
    assert resolve_max_tokens({"MIDKERNEL_OPENROUTER_MAX_TOKENS": "80000"}) == DEFAULT_MAX_TOKENS
    assert resolve_max_tokens({"OPENROUTER_MAX_TOKENS": "80000"}) == DEFAULT_MAX_TOKENS


def test_explicit_32768_is_allowed_under_ceiling():
    """Old default stays legal; only missing / unsafe values drop to 16384."""
    assert clamp_max_tokens(32_768) == 32_768
    assert resolve_max_tokens({"OPENROUTER_MAX_TOKENS": "32768"}) == 32_768


def test_env_first_wins_order_matches_playbooks():
    assert MAX_TOKENS_ENV_KEYS == (
        "MIDKERNEL_OPENROUTER_MAX_TOKENS",
        "OPENROUTER_MAX_TOKENS",
        "KIMI_MAX_TOKENS",
        "KIMI_MODEL_MAX_TOKENS",
        "KIMI_MODEL_MAX_COMPLETION_TOKENS",
    )
    assert (
        resolve_max_tokens(
            {
                "MIDKERNEL_OPENROUTER_MAX_TOKENS": "4096",
                "OPENROUTER_MAX_TOKENS": "8192",
                "KIMI_MAX_TOKENS": "16384",
                "KIMI_MODEL_MAX_TOKENS": "2048",
                "KIMI_MODEL_MAX_COMPLETION_TOKENS": "1024",
            }
        )
        == 4096
    )
    assert (
        resolve_max_tokens(
            {
                "OPENROUTER_MAX_TOKENS": "8192",
                "KIMI_MAX_TOKENS": "16384",
                "KIMI_MODEL_MAX_TOKENS": "2048",
            }
        )
        == 8192
    )
    assert (
        resolve_max_tokens(
            {
                "KIMI_MAX_TOKENS": "16384",
                "KIMI_MODEL_MAX_TOKENS": "2048",
                "KIMI_MODEL_MAX_COMPLETION_TOKENS": "1024",
            }
        )
        == 16384
    )
    assert (
        resolve_max_tokens(
            {
                "KIMI_MODEL_MAX_TOKENS": "2048",
                "KIMI_MODEL_MAX_COMPLETION_TOKENS": "1024",
            }
        )
        == 2048
    )
    assert resolve_max_tokens({"KIMI_MODEL_MAX_COMPLETION_TOKENS": "1024"}) == 1024


def test_kimi_max_tokens_alias_is_accepted():
    assert resolve_max_tokens({"KIMI_MAX_TOKENS": "4096"}) == 4096


def test_max_tokens_env_exports_all_aliases():
    env = max_tokens_env(DEFAULT_MAX_TOKENS)
    assert env["OPENROUTER_MAX_TOKENS"] == "16384"
    assert env["KIMI_MAX_TOKENS"] == "16384"
    assert env["KIMI_MODEL_MAX_TOKENS"] == "16384"
    assert env["KIMI_MODEL_MAX_COMPLETION_TOKENS"] == "16384"
    assert env["MIDKERNEL_OPENROUTER_MAX_TOKENS"] == "16384"


def test_cli_clamp_does_not_copy_context_size():
    out = clamp_max_token_cli_flags(
        ["--print", "--max-tokens", "262144", "-p", "hunt"],
        DEFAULT_MAX_TOKENS,
    )
    assert out[out.index("--max-tokens") + 1] == "16384"
    assert "262144" not in out
    assert "131072" not in out


def test_cli_clamp_over_ceiling_becomes_16384_not_65536():
    out = clamp_max_token_cli_flags(
        ["--max-completion-tokens", "80000"],
        DEFAULT_MAX_TOKENS,
    )
    assert out[out.index("--max-completion-tokens") + 1] == "16384"
    assert "80000" not in out
    assert "65536" not in out


def test_sitecustomize_fails_loud_and_does_not_swallow_errors(tmp_path):
    text = sitecustomize_source()
    assert "max_context_size" in text
    assert "cmtufzqzo0003k004mt2w0m9c" in text
    assert "cmtulxq7v0003l2046bhhc3yl" in text
    assert "16384" in text
    assert "except Exception" not in text
    assert "required=True" in text
    assert "max_completion_tokens" in text
    path = write_openrouter_sitecustomize(tmp_path)
    assert path.is_file()
    written = path.read_text(encoding="utf-8")
    assert "install_openai_legacy_max_tokens_cap" in written
    assert "install_openai_legacy_generation_id_capture" in written
    assert "except Exception" not in written


def test_install_required_fails_loud_without_openai_legacy(monkeypatch):
    monkeypatch.setitem(sys.modules, "kosong.contrib.chat_provider.openai_legacy", None)
    with pytest.raises(OpenRouterMaxTokensCapError, match="131072"):
        install_openai_legacy_max_tokens_cap(required=True)


def _install_fake_openai_legacy():
    completions_calls: list[dict] = []

    class Completions:
        async def create(self, **kwargs):
            completions_calls.append(dict(kwargs))
            return {"ok": True}

    class Chat:
        def __init__(self):
            self.completions = Completions()

    class Client:
        def __init__(self):
            self.chat = Chat()

    class OpenAILegacy:
        def __init__(self, *args, **kwargs):
            self._generation_kwargs = {}
            self.client = Client()

        def with_generation_kwargs(self, **kwargs):
            self._generation_kwargs.update(kwargs)
            return self

        async def generate(self, *args, **kwargs):
            generation_kwargs = {}
            generation_kwargs.update(self._generation_kwargs)
            return await self.client.chat.completions.create(
                model="moonshotai/kimi-k3",
                messages=[],
                **generation_kwargs,
            )

    package = types.ModuleType("kosong")
    contrib = types.ModuleType("kosong.contrib")
    provider = types.ModuleType("kosong.contrib.chat_provider")
    legacy = types.ModuleType("kosong.contrib.chat_provider.openai_legacy")
    legacy.OpenAILegacy = OpenAILegacy
    sys.modules["kosong"] = package
    sys.modules["kosong.contrib"] = contrib
    sys.modules["kosong.contrib.chat_provider"] = provider
    sys.modules["kosong.contrib.chat_provider.openai_legacy"] = legacy
    return OpenAILegacy, completions_calls


def test_monkeypatch_puts_max_tokens_16384_on_chat_completions_kwargs():
    """Outbound chat.completions.create must carry max_tokens=16384, never 131072."""
    OpenAILegacy, calls = _install_fake_openai_legacy()
    assert install_openai_legacy_max_tokens_cap({}) is True
    provider = OpenAILegacy(model="moonshotai/kimi-k3")
    assert provider._generation_kwargs["max_tokens"] == DEFAULT_MAX_TOKENS
    asyncio.run(provider.generate())
    assert calls, "chat.completions.create was not invoked"
    assert calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert calls[0]["max_tokens"] != UNSAFE_OPENROUTER_DEFAULT


def test_monkeypatch_clamps_131072_on_outbound_kwargs():
    OpenAILegacy, calls = _install_fake_openai_legacy()
    assert install_openai_legacy_max_tokens_cap({}) is True
    provider = OpenAILegacy(model="moonshotai/kimi-k3")
    provider._generation_kwargs["max_tokens"] = UNSAFE_OPENROUTER_DEFAULT
    asyncio.run(provider.generate())
    assert calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS


def test_apply_kwargs_always_sets_max_tokens_and_strips_max_completion_tokens():
    stripped = apply_max_tokens_to_kwargs(
        {"max_completion_tokens": UNSAFE_OPENROUTER_DEFAULT},
        DEFAULT_MAX_TOKENS,
    )
    assert stripped["max_tokens"] == DEFAULT_MAX_TOKENS
    assert "max_completion_tokens" not in stripped

    over_ceiling = apply_max_tokens_to_kwargs(
        {"max_completion_tokens": 80_000},
        DEFAULT_MAX_TOKENS,
    )
    assert over_ceiling["max_tokens"] == DEFAULT_MAX_TOKENS
    assert "max_completion_tokens" not in over_ceiling

    both = apply_max_tokens_to_kwargs(
        {
            "max_tokens": 4096,
            "max_completion_tokens": UNSAFE_OPENROUTER_DEFAULT,
        },
        DEFAULT_MAX_TOKENS,
    )
    assert both["max_tokens"] == 4096
    assert "max_completion_tokens" not in both

    empty = apply_max_tokens_to_kwargs({}, DEFAULT_MAX_TOKENS)
    assert empty["max_tokens"] == DEFAULT_MAX_TOKENS


def test_monkeypatch_strips_max_completion_tokens_131072():
    OpenAILegacy, calls = _install_fake_openai_legacy()
    assert install_openai_legacy_max_tokens_cap({}) is True
    provider = OpenAILegacy(model="moonshotai/kimi-k3")
    provider._generation_kwargs.pop("max_tokens", None)
    provider._generation_kwargs["max_completion_tokens"] = UNSAFE_OPENROUTER_DEFAULT
    asyncio.run(provider.generate())
    assert calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert "max_completion_tokens" not in calls[0]


def test_injection_proxy_url_detection():
    assert is_injection_proxy_url("http://127.0.0.1:54321/api/v1") is True
    assert is_injection_proxy_url("http://localhost:9/api/v1") is True
    assert is_injection_proxy_url("https://openrouter.ai/api/v1") is False
    assert is_injection_proxy_url("") is False


def test_resolve_provider_base_url_preserves_localhost_proxy(tmp_path):
    proxy = "http://127.0.0.1:4242/api/v1"
    assert (
        resolve_provider_base_url({"OPENAI_BASE_URL": proxy}) == proxy
    )
    config = tmp_path / "config.toml"
    config.write_text(
        '[providers.openrouter]\nbase_url = "http://127.0.0.1:9999/api/v1"\n',
        encoding="utf-8",
    )
    assert resolve_provider_base_url({}, config_path=config) == "http://127.0.0.1:9999/api/v1"
    assert resolve_provider_base_url({}) == "https://openrouter.ai/api/v1"
