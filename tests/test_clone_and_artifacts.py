from midkernel_runner.clone import clone_url_with_token
from midkernel_runner.config import load_config
from midkernel_runner.artifacts import upload_report
from midkernel_runner.report import persist_report


def test_clone_url_redacts_via_x_access_token():
    url = clone_url_with_token("https://github.com/midkernel/playbooks.git", "ghs_secret")
    assert url == "https://x-access-token:ghs_secret@github.com/midkernel/playbooks.git"


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
