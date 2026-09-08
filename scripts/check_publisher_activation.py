#!/usr/bin/env python3
"""Require independently approved account configuration and explicit activation."""
import os
from pathlib import Path
import re

REPOSITORY = "midkernel/runner"


def require_approved_account(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{12}", value) is None:
        raise ValueError("MIDKERNEL_APPROVED_AWS_ACCOUNT_ID must be explicitly configured as 12 ASCII digits")
    return value


def publication_enabled(configured_role, repository, ref, event, *, approved_account=""):
    if repository != REPOSITORY or ref != "refs/heads/main" or event not in {"push", "workflow_dispatch"}:
        raise ValueError("Image publication is limited to this repository's main branch")
    if configured_role == "":
        return False
    account = require_approved_account(approved_account)
    expected_role = f"arn:aws:iam::{account}:role/midkernel-github-publish-runner"
    if configured_role != expected_role:
        raise ValueError("ECR_PUBLISHER_ROLE_ARN must identify this repository's dedicated publisher role in the independently approved account")
    return True


def main():
    role = os.environ.get("ECR_PUBLISHER_ROLE_ARN", "")
    account = os.environ.get("MIDKERNEL_APPROVED_AWS_ACCOUNT_ID", "")
    try:
        enabled = publication_enabled(
            role, os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_REF", ""),
            os.environ.get("GITHUB_EVENT_NAME", ""), approved_account=account,
        )
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    message = ("Dedicated image publication is activated; credentials and publication must still succeed."
               if enabled else "Image publication is awaiting infrastructure bootstrap and explicit ECR_PUBLISHER_ROLE_ARN activation. Existing images remain unchanged. See docs/publishing.md.")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"enabled={str(enabled).lower()}\n")
        if enabled:
            output.write(f"role_arn={role}\naccount_id={account}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
        summary.write(message + "\n")
    print(message if enabled else "::notice::" + message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
