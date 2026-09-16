#!/usr/bin/env python3
"""Require explicit post-bootstrap activation before main may publish images."""
import os
from pathlib import Path

REPOSITORY = "midkernel/runner"
ROLE = "arn:aws:iam::489470371031:role/midkernel-github-publish-runner"


def publication_enabled(configured_role, repository, ref, event):
    if repository != REPOSITORY or ref != "refs/heads/main" or event not in {"push", "workflow_dispatch"}:
        raise ValueError("Image publication is limited to this repository's main branch")
    if not configured_role:
        return False
    if configured_role != ROLE:
        raise ValueError("ECR_PUBLISHER_ROLE_ARN must identify this repository's dedicated publisher role")
    return True


def main():
    enabled = publication_enabled(os.environ.get("ECR_PUBLISHER_ROLE_ARN", ""),
                                  os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF"],
                                  os.environ["GITHUB_EVENT_NAME"])
    message = ("Dedicated image publication is activated; credentials and publication must still succeed."
               if enabled else "Image publication is awaiting infrastructure bootstrap and explicit ECR_PUBLISHER_ROLE_ARN activation. Existing images remain unchanged. See docs/publishing.md.")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"enabled={str(enabled).lower()}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
        summary.write(message + "\n")
    print(message if enabled else "::notice::" + message)


if __name__ == "__main__":
    main()
