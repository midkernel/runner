"""Upload report.md and official OpenRouter generation ids to S3."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from midkernel_runner.config import RunConfig


class ArtifactError(RuntimeError):
    """S3 upload failed."""


class ObjectClient(Protocol):
    def put_object(self, **kwargs) -> dict: ...


def _s3_client(config: RunConfig, client: ObjectClient | None) -> ObjectClient:
    if client is not None:
        return client
    import boto3

    return boto3.client("s3", region_name=config.aws_region)


def upload_report(
    config: RunConfig,
    report_path: Path,
    *,
    client: ObjectClient | None = None,
) -> str:
    body = report_path.read_bytes()
    if not body:
        raise ArtifactError("refusing to upload an empty report.md")

    s3 = _s3_client(config, client)

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


def upload_openrouter_generations(
    config: RunConfig,
    generations_path: Path,
    *,
    client: ObjectClient | None = None,
) -> str:
    """Upload official ``gen-…`` ids only. Refuse empty / invented spend."""
    body = generations_path.read_bytes()
    if not body.strip():
        raise ArtifactError("refusing to upload an empty openrouter-generations.json")

    s3 = _s3_client(config, client)

    try:
        s3.put_object(
            Bucket=config.artifacts_bucket,
            Key=config.generations_artifact_key,
            Body=body,
            ContentType="application/json; charset=utf-8",
            ServerSideEncryption="AES256",
        )
    except Exception as exc:
        raise ArtifactError(
            f"Failed to upload {config.generations_s3_uri}. The ECS task role must allow "
            "s3:PutObject on midkernel-dev-artifacts. No generation ids were invented."
        ) from exc
    return config.generations_s3_uri


def publish_openrouter_generations(
    config: RunConfig,
    *,
    client: ObjectClient | None = None,
    extra: object = None,
    stdout: str | None = None,
) -> str | None:
    """Persist + upload official ids. No file and no USD when none were captured."""
    from midkernel_runner.openrouter_generations import persist_openrouter_generations

    path = persist_openrouter_generations(config=config, extra=extra, stdout=stdout)
    if path is None:
        return None
    return upload_openrouter_generations(config, path, client=client)
