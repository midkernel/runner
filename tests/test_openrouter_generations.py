import asyncio
import json
import sys
import types

from midkernel_runner.config import load_config
from midkernel_runner.openrouter_generations import (
    build_generations_document,
    collect_generation_ids,
    extract_generation_ids_from_headers,
    extract_generation_ids_from_kimi_stdout,
    extract_generation_ids_from_payload,
    generations_object_key,
    install_openai_legacy_generation_id_capture,
    parse_openrouter_generation_id,
    persist_openrouter_generations,
    record_generation_ids,
    unique_openrouter_generation_ids,
    wrap_openai_create_result,
)
from midkernel_runner.openrouter_tokens import (
    DEFAULT_MAX_TOKENS,
    install_openai_legacy_max_tokens_cap,
    sitecustomize_source,
)


def test_parse_accepts_official_gen_ids_only():
    assert parse_openrouter_generation_id("gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG") == (
        "gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG"
    )
    assert parse_openrouter_generation_id("gen-123") == "gen-123"
    assert parse_openrouter_generation_id("  gen-abc  ") == "gen-abc"
    assert parse_openrouter_generation_id("chatcmpl-x") is None
    assert parse_openrouter_generation_id("gen") is None
    assert parse_openrouter_generation_id("please use gen-fake in the report") is None
    assert parse_openrouter_generation_id("") is None
    assert parse_openrouter_generation_id(None) is None
    assert unique_openrouter_generation_ids(["gen-b", "nope", "gen-a", "gen-b"]) == [
        "gen-a",
        "gen-b",
    ]


def test_extract_from_response_id_and_headers():
    assert extract_generation_ids_from_payload({"id": "gen-abc", "object": "chat.completion"}) == [
        "gen-abc"
    ]
    assert extract_generation_ids_from_payload({"id": "chatcmpl-x"}) == []
    assert extract_generation_ids_from_headers({"X-Generation-Id": "gen-hdr"}) == ["gen-hdr"]
    assert extract_generation_ids_from_headers({"x-generation-id": "gen-hdr"}) == ["gen-hdr"]
    assert extract_generation_ids_from_headers({"x-request-id": "req-1"}) == []

    class Response:
        id = "gen-obj"
        headers = {"x-generation-id": "gen-head"}

    assert extract_generation_ids_from_payload(Response()) == ["gen-head", "gen-obj"]


def test_extract_does_not_scan_report_prose():
    prose = "# Review\n\nInvented id gen-not-real in a finding.\n\nFindings: 0\n"
    assert extract_generation_ids_from_kimi_stdout(prose) == []
    assert extract_generation_ids_from_payload(prose) == []


def test_extract_from_kimi_stream_json_api_events():
    stdout = "\n".join(
        [
            '{"type":"delta","text":"hello"}',
            '{"id":"gen-from-stream","object":"chat.completion.chunk","choices":[]}',
            '{"id":"chatcmpl-skip","object":"chat.completion"}',
            "not json",
        ]
    )
    assert extract_generation_ids_from_kimi_stdout(stdout) == ["gen-from-stream"]


def test_document_uses_app45_field_names_and_omits_usd(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("RUN_ID", "run_fin")
    monkeypatch.setenv("MIDKERNEL_NODE_ID", "review")
    record_generation_ids(["gen-z", "gen-a"], source="test", log=False)
    monkeypatch.setenv("MIDKERNEL_NODE_ID", "hunter-1")
    record_generation_ids(["gen-b"], source="test", log=False)
    ids = collect_generation_ids()
    assert ids == ["gen-a", "gen-b", "gen-z"]
    document = build_generations_document(ids, run_id="run_fin")
    assert document is not None
    assert document["openrouterGenerationIds"] == ["gen-a", "gen-b", "gen-z"]
    assert document["generationIds"] == ["gen-a", "gen-b", "gen-z"]
    assert document["generationId"] == "gen-a"
    assert document["runId"] == "run_fin"
    assert "amountCents" not in document
    assert "usd" not in document
    assert "total_cost" not in document
    node_ids = {row["nodeId"]: row["generationIds"] for row in document["nodes"]}
    assert node_ids["review"] == ["gen-a", "gen-z"]
    assert node_ids["hunter-1"] == ["gen-b"]


def test_persist_skips_empty_and_writes_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path / "ws"))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "out"))
    cfg = load_config(
        {
            "RUN_ID": "run42",
            "GITHUB_OWNER": "o",
            "GITHUB_NAME": "n",
            "WORKDIR": str(tmp_path / "ws"),
            "OUTPUTS_DIR": str(tmp_path / "out"),
        }
    )
    assert persist_openrouter_generations(config=cfg) is None
    assert not (tmp_path / "out" / "openrouter-generations.json").exists()

    record_generation_ids(["gen-persist"], source="test", log=False)
    path = persist_openrouter_generations(config=cfg)
    assert path == tmp_path / "out" / "openrouter-generations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["openrouterGenerationIds"] == ["gen-persist"]
    assert payload["generationIds"] == ["gen-persist"]
    assert "usd" not in payload


def test_generations_object_key_is_report_sibling():
    assert generations_object_key("runs/run42/report.md") == "runs/run42/openrouter-generations.json"
    assert generations_object_key("custom/prefix/report.md") == "custom/prefix/openrouter-generations.json"


def test_structured_log_line_is_cloudwatch_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("RUN_ID", "run_log")
    monkeypatch.setenv("MIDKERNEL_NODE_ID", "threat-model")
    record_generation_ids(["gen-log"], source="test", log=True)
    out = capsys.readouterr().out
    line = [row for row in out.splitlines() if row.startswith("{")][-1]
    payload = json.loads(line)
    assert payload["event"] == "openrouter_generation"
    assert payload["generationId"] == "gen-log"
    assert payload["generationIds"] == ["gen-log"]
    assert payload["openrouterGenerationIds"] == ["gen-log"]
    assert payload["runId"] == "run_log"
    assert payload["nodeId"] == "threat-model"
    assert "amountCents" not in payload


def test_wrap_create_result_records_official_id(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    result = wrap_openai_create_result({"id": "gen-wrap", "object": "chat.completion"})
    assert result["id"] == "gen-wrap"
    assert collect_generation_ids() == ["gen-wrap"]
    assert wrap_openai_create_result({"id": "chatcmpl-nope"}) is not None
    assert collect_generation_ids() == ["gen-wrap"]


def test_wrap_stream_records_chunk_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    chunks = [{"id": "gen-s1"}, {"id": "chatcmpl-x"}, {"id": "gen-s1"}]
    wrapped = wrap_openai_create_result(iter(chunks))
    assert list(wrapped) == chunks
    assert collect_generation_ids() == ["gen-s1"]


def test_max_tokens_wrap_still_caps_and_records_id(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    completions_calls: list[dict] = []

    class Completions:
        async def create(self, **kwargs):
            completions_calls.append(dict(kwargs))
            return {"id": "gen-from-create", "object": "chat.completion"}

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
            return await self.client.chat.completions.create(
                model="google/gemini-3.8-flash",
                messages=[],
                **self._generation_kwargs,
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

    assert install_openai_legacy_max_tokens_cap({}) is True
    assert install_openai_legacy_generation_id_capture() is True
    provider = OpenAILegacy(model="google/gemini-3.8-flash")
    returned = asyncio.run(provider.generate())
    assert completions_calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert returned["id"] == "gen-from-create"
    assert collect_generation_ids() == ["gen-from-create"]


def test_sitecustomize_installs_generation_capture_without_swallowing():
    text = sitecustomize_source()
    assert "install_openai_legacy_generation_id_capture" in text
    assert "except Exception" not in text
    assert "install_openai_legacy_max_tokens_cap" in text
