import asyncio
import sys
import types
from datetime import datetime, timezone

import pytest

from midkernel_runner.openrouter_retry import (
    CONCURRENCY_ENV_KEYS,
    DEFAULT_OPENROUTER_429_RETRIES,
    DEFAULT_OPENROUTER_RPM,
    MAX_GRAPH_CONCURRENCY,
    apply_graph_concurrency_env,
    acquire_openrouter_slot,
    concurrency_env,
    http_status_from_error,
    install_openai_legacy_429_retry,
    invoke_openai_create_with_429_retry,
    openrouter_429_delay_seconds,
    openrouter_retry_env,
    parse_graph_concurrency,
    parse_retry_after,
    resolve_429_retries,
    resolve_graph_concurrency,
    resolve_openrouter_rpm,
    retry_after_from_error,
    should_retry_openrouter,
    throttle_lock_path,
)


def test_retry_defaults_match_playbooks_wrap_kimi():
    assert DEFAULT_OPENROUTER_429_RETRIES == 8
    assert resolve_429_retries({}) == 8
    assert resolve_429_retries({"MIDKERNEL_OPENROUTER_429_RETRIES": "12"}) == 12
    assert resolve_429_retries({"OPENROUTER_429_RETRIES": "3"}) == 3
    assert resolve_429_retries({"MIDKERNEL_OPENROUTER_429_RETRIES": "99"}) == 16
    assert resolve_429_retries({"MIDKERNEL_OPENROUTER_429_RETRIES": "0"}) == 0
    assert resolve_429_retries({"MIDKERNEL_OPENROUTER_429_RETRIES": "nope"}) == 8


def test_rpm_default_is_under_new_account_20():
    assert DEFAULT_OPENROUTER_RPM == 12
    assert DEFAULT_OPENROUTER_RPM < 20
    assert resolve_openrouter_rpm({}) == 12
    assert resolve_openrouter_rpm({"MIDKERNEL_OPENROUTER_RPM": "0"}) == 0
    assert resolve_openrouter_rpm({"OPENROUTER_RPM": "8"}) == 8
    assert resolve_openrouter_rpm({"MIDKERNEL_OPENROUTER_RPM": "99"}) == 60


def test_retry_after_seconds_and_http_date():
    assert parse_retry_after("4") == 4.0
    assert parse_retry_after("") is None
    now = datetime(2026, 9, 10, 18, 0, tzinfo=timezone.utc)
    assert parse_retry_after("Thu, 10 Sep 2026 18:00:30 GMT", now=now) == 30.0
    assert openrouter_429_delay_seconds(0, "12") == 12.0
    assert openrouter_429_delay_seconds(0, "120") == 90.0
    assert openrouter_429_delay_seconds(0) == 1.0
    assert openrouter_429_delay_seconds(3) == 8.0
    assert openrouter_429_delay_seconds(10) == 90.0


def test_should_retry_429_never_402():
    assert should_retry_openrouter(429, 0, 8) is True
    assert should_retry_openrouter(429, 7, 8) is True
    assert should_retry_openrouter(429, 8, 8) is False
    assert should_retry_openrouter(402, 0, 8) is False
    assert should_retry_openrouter(500, 0, 8) is False
    assert should_retry_openrouter(None, 0, 8) is False


class _RateLimit(Exception):
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.response = types.SimpleNamespace(headers={"Retry-After": retry_after} if retry_after else {})


def test_http_status_and_retry_after_from_openai_shaped_error():
    err = _RateLimit(429, "7")
    assert http_status_from_error(err) == 429
    assert retry_after_from_error(err) == "7"
    assert http_status_from_error(ValueError("nope")) is None


def test_invoke_retries_429_honors_retry_after_skips_402():
    sleeps: list[float] = []
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RateLimit(429, "2")
        return {"ok": True}

    result = invoke_openai_create_with_429_retry(
        create,
        (),
        {},
        environ={"MIDKERNEL_OPENROUTER_RPM": "0", "MIDKERNEL_OPENROUTER_429_RETRIES": "8"},
        sleep=sleeps.append,
    )
    assert result == {"ok": True}
    assert calls["n"] == 3
    assert sleeps == [2.0, 2.0]

    calls["n"] = 0

    def payment_required(**kwargs):
        calls["n"] += 1
        raise _RateLimit(402)

    with pytest.raises(_RateLimit) as caught:
        invoke_openai_create_with_429_retry(
            payment_required,
            (),
            {},
            environ={"MIDKERNEL_OPENROUTER_RPM": "0"},
            sleep=sleeps.append,
        )
    assert caught.value.status_code == 402
    assert calls["n"] == 1


def test_invoke_async_create_retries_429():
    sleeps: list[float] = []
    calls = {"n": 0}

    async def create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _RateLimit(429, "1")
        return {"ok": True}

    result = asyncio.run(
        invoke_openai_create_with_429_retry(
            create,
            (),
            {},
            environ={"MIDKERNEL_OPENROUTER_RPM": "0"},
            sleep=sleeps.append,
        )
    )
    assert result == {"ok": True}
    assert calls["n"] == 2
    assert sleeps == [1.0]


def test_shared_rpm_throttle_spaces_two_slots(tmp_path):
    env = {"WORKDIR": str(tmp_path), "MIDKERNEL_OPENROUTER_RPM": "12"}
    lock = throttle_lock_path(env)
    assert lock == tmp_path / ".midkernel" / "openrouter-rpm.lock"
    sleeps: list[float] = []
    clock = {"t": 1000.0}

    def now():
        return clock["t"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    assert acquire_openrouter_slot(env, clock=now, sleep=fake_sleep) == 0.0
    waited = acquire_openrouter_slot(env, clock=now, sleep=fake_sleep)
    assert waited == pytest.approx(5.0)
    assert sleeps == [5.0]


def test_concurrency_passthrough_does_not_invent_default():
    assert parse_graph_concurrency(None) is None
    assert parse_graph_concurrency("") is None
    assert parse_graph_concurrency("nope") is None
    assert parse_graph_concurrency("0") is None
    assert parse_graph_concurrency("2") == 2
    assert parse_graph_concurrency("99") == MAX_GRAPH_CONCURRENCY
    assert resolve_graph_concurrency({}) is None
    assert resolve_graph_concurrency({"CONCURRENCY": "3"}) == 3
    assert (
        resolve_graph_concurrency(
            {"CONCURRENCY": "2", "AGENTFLOW_CONCURRENCY": "6", "GRAPH_CONCURRENCY": "1"}
        )
        == 2
    )
    assert resolve_graph_concurrency({"GRAPH_CONCURRENCY": "4"}) == 4
    env = apply_graph_concurrency_env({"GOAL_COUNT": "6"})
    assert "CONCURRENCY" not in env
    env = apply_graph_concurrency_env({"AGENTFLOW_CONCURRENCY": "2", "GOAL_COUNT": "6"})
    assert env["CONCURRENCY"] == "2"
    assert env["AGENTFLOW_CONCURRENCY"] == "2"
    assert env["GRAPH_CONCURRENCY"] == "2"
    assert env["MIDKERNEL_CONCURRENCY"] == "2"
    aliases = concurrency_env(2)
    assert tuple(aliases) == CONCURRENCY_ENV_KEYS
    retry = openrouter_retry_env({})
    assert retry["MIDKERNEL_OPENROUTER_429_RETRIES"] == "8"
    assert retry["MIDKERNEL_OPENROUTER_RPM"] == "12"


def _install_fake_openai_legacy(fail_times=0):
    calls: list[dict] = []
    remaining = {"n": fail_times}

    class RateLimit(Exception):
        status_code = 429

        def __init__(self):
            self.response = types.SimpleNamespace(headers={"Retry-After": "1"})

    class Completions:
        async def create(self, **kwargs):
            calls.append(dict(kwargs))
            if remaining["n"] > 0:
                remaining["n"] -= 1
                raise RateLimit()
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
            return await self.client.chat.completions.create(model="google/gemini-3.8-flash")

    package = types.ModuleType("kosong")
    contrib = types.ModuleType("kosong.contrib")
    provider = types.ModuleType("kosong.contrib.chat_provider")
    legacy = types.ModuleType("kosong.contrib.chat_provider.openai_legacy")
    legacy.OpenAILegacy = OpenAILegacy
    sys.modules["kosong"] = package
    sys.modules["kosong.contrib"] = contrib
    sys.modules["kosong.contrib.chat_provider"] = provider
    sys.modules["kosong.contrib.chat_provider.openai_legacy"] = legacy
    return OpenAILegacy, calls


def test_openai_legacy_install_retries_429(monkeypatch):
    OpenAILegacy, calls = _install_fake_openai_legacy(fail_times=1)
    monkeypatch.setenv("MIDKERNEL_OPENROUTER_RPM", "0")
    assert install_openai_legacy_429_retry() is True
    provider = OpenAILegacy()
    sleeps: list[float] = []
    monkeypatch.setattr("midkernel_runner.openrouter_retry._sleep", sleeps.append)
    assert asyncio.run(provider.generate()) == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [1.0]
