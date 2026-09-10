from midkernel_runner.clone import clone_url_with_token
from midkernel_runner.config import load_config
from midkernel_runner.artifacts import publish_openrouter_generations, upload_report
from midkernel_runner.openrouter_generations import persist_generations_document
from midkernel_runner.report import persist_report


def test_clone_url_redacts_via_x_access_token():
    url = clone_url_with_token("https://github.com/midkernel/playbooks.git", "ghs_secret")
    assert url == "https://x-access-token:ghs_secret@github.com/midkernel/playbooks.git"


def test_clone_into_existing_empty_workdir(tmp_path):
    from midkernel_runner.clone import clone_repository
    from midkernel_runner.secrets import HarnessSecrets

    cfg = load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "midkernel",
            "GITHUB_NAME": "playbooks",
            "WORKDIR": str(tmp_path),
        }
    )
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / ".git").mkdir()
        return Result()

    dest = clone_repository(cfg, HarnessSecrets("k", "ghs_x"), run=fake_run)
    assert dest == tmp_path
    assert str(tmp_path) in calls[0]
    assert "--depth" in calls[0]


def test_clone_skips_when_git_present(tmp_path):
    from midkernel_runner.clone import clone_repository
    from midkernel_runner.secrets import HarnessSecrets

    (tmp_path / ".git").mkdir()
    cfg = load_config(
        {
            "RUN_ID": "r1",
            "GITHUB_OWNER": "midkernel",
            "GITHUB_NAME": "playbooks",
            "WORKDIR": str(tmp_path),
        }
    )

    def boom(*a, **k):
        raise AssertionError("should not clone")

    assert clone_repository(cfg, HarnessSecrets("k", "t"), run=boom) == tmp_path


class FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {}


def test_upload_key_and_encryption(tmp_path):
    cfg = load_config({"RUN_ID": "run42", "GITHUB_OWNER": "o", "GITHUB_NAME": "n"})
    report = tmp_path / "report.md"
    persist_report(
        report,
        "# Security review of o/n\n\n"
        "Fixture tree used only to exercise the S3 key and SSE-S3 headers.\n\n"
        "Findings: 0\n",
    )
    client = FakeS3()
    uri = upload_report(cfg, report, client=client)
    assert uri == "s3://midkernel-dev-artifacts/runs/run42/report.md"
    call = client.calls[0]
    assert call["Bucket"] == "midkernel-dev-artifacts"
    assert call["Key"] == "runs/run42/report.md"
    assert call["ServerSideEncryption"] == "AES256"
    assert call["ContentType"].startswith("text/markdown")


def test_upload_openrouter_generations_sibling_json(tmp_path):
    cfg = load_config({"RUN_ID": "run42", "GITHUB_OWNER": "o", "GITHUB_NAME": "n"})
    path = tmp_path / "openrouter-generations.json"
    persist_generations_document(
        {
            "runId": "run42",
            "source": "openrouter",
            "generationId": "gen-abc",
            "generationIds": ["gen-abc"],
            "openrouterGenerationIds": ["gen-abc"],
            "nodes": [],
        },
        path,
    )
    client = FakeS3()
    from midkernel_runner.artifacts import upload_openrouter_generations

    uri = upload_openrouter_generations(cfg, path, client=client)
    assert uri == "s3://midkernel-dev-artifacts/runs/run42/openrouter-generations.json"
    call = client.calls[0]
    assert call["Key"] == "runs/run42/openrouter-generations.json"
    assert call["ServerSideEncryption"] == "AES256"
    assert call["ContentType"].startswith("application/json")
    assert b"gen-abc" in call["Body"]
    assert b"amountCents" not in call["Body"]
    assert b"usd" not in call["Body"]


def test_publish_openrouter_generations_skips_when_empty(tmp_path, monkeypatch):
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
    client = FakeS3()
    assert publish_openrouter_generations(cfg, client=client) is None
    assert client.calls == []
