"""Upload report.md to the Midkernel artifacts bucket."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from midkernel_runner.config import RunConfig


class ArtifactError(RuntimeError):
    """S3 upload failed."""


class ObjectClient(Protocol):
    def put_object(self, **kwargs) -> dict: ...


def upload_report(
    config: RunConfig,
    report_path: Path,
    *,
    client: ObjectClient | None = None,
) -> str:
    body = report_path.read_bytes()
    if not body:
        raise ArtifactError("refusing to upload an empty report.md")

    s3 = client
    if s3 is None:
        import boto3

        s3 = boto3.client("s3", region_name=config.aws_region)

    try:
        s3.put_object(
            Bucket=config.artifacts_bucket,
            Key=config.artifact_key,
            Body=body,
            ContentType="text/markdown; charset=utf-8",
            ServerSideEncryption="AES256",
        )
    except Exception as exc:
        raise ArtifactError(
            f"Failed to upload {config.s3_uri}. The ECS task role must allow "
            "s3:PutObject on midkernel-dev-artifacts. No stub was uploaded."
        ) from exc
    return config.s3_uri
