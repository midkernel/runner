from midkernel_runner.openrouter_tokens import (
    DEFAULT_MAX_TOKENS,
    MAX_SAFE_MAX_TOKENS,
    UNSAFE_OPENROUTER_DEFAULT,
    clamp_max_token_cli_flags,
    clamp_max_tokens,
    max_tokens_env,
    resolve_max_tokens,
    sitecustomize_source,
    write_openrouter_sitecustomize,
)


def test_default_is_32768_never_131072():
    assert DEFAULT_MAX_TOKENS == 32_768
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


def test_env_over_ceiling_clamps_to_65536():
    assert resolve_max_tokens({"MIDKERNEL_OPENROUTER_MAX_TOKENS": "80000"}) == MAX_SAFE_MAX_TOKENS


def test_max_tokens_env_exports_all_aliases():
    env = max_tokens_env(DEFAULT_MAX_TOKENS)
    assert env["OPENROUTER_MAX_TOKENS"] == "32768"
    assert env["KIMI_MODEL_MAX_TOKENS"] == "32768"
    assert env["KIMI_MODEL_MAX_COMPLETION_TOKENS"] == "32768"
    assert env["MIDKERNEL_OPENROUTER_MAX_TOKENS"] == "32768"


def test_cli_clamp_does_not_copy_context_size():
    out = clamp_max_token_cli_flags(
        ["--print", "--max-tokens", "262144", "-p", "hunt"],
        DEFAULT_MAX_TOKENS,
    )
    assert out[out.index("--max-tokens") + 1] == "32768"
    assert "262144" not in out
    assert "131072" not in out


def test_sitecustomize_does_not_use_max_context_as_max_tokens(tmp_path):
    text = sitecustomize_source()
    assert "max_context_size" in text
    assert "cmtufzqzo0003k004mt2w0m9c" in text
    path = write_openrouter_sitecustomize(tmp_path)
    assert path.is_file()
    assert "install_openai_legacy_max_tokens_cap" in path.read_text(encoding="utf-8")
