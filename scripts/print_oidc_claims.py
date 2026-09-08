#!/usr/bin/env python3
"""Print non-secret GitHub Actions OIDC JWT claims. Never prints the raw token."""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

# Keep this list tight — do not add actor, emails, or other identifying extras.
CLAIM_KEYS = (
    "iss",
    "aud",
    "sub",
    "repository",
    "repository_owner",
    "ref",
    "workflow",
    "job_workflow_ref",
)

# Same audience aws-actions/configure-aws-credentials@v4 requests by default.
AUDIENCE = "sts.amazonaws.com"


def b64url_json_decode(segment: str) -> object:
    pad = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + pad))


def claims_from_jwt(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("OIDC token is not a three-part JWT")
    payload = b64url_json_decode(parts[1])
    if not isinstance(payload, dict):
        raise ValueError("OIDC JWT payload is not an object")
    return {key: payload.get(key) for key in CLAIM_KEYS}


def request_id_token() -> str:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    req_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not req_token:
        raise SystemExit(
            "ACTIONS_ID_TOKEN_REQUEST_URL / ACTIONS_ID_TOKEN_REQUEST_TOKEN missing. "
            "Need job permissions: { id-token: write }."
        )
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{sep}audience={AUDIENCE}",
        headers={"Authorization": f"bearer {req_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"OIDC token request failed: HTTP {exc.code}") from None
    token = body.get("value")
    if not isinstance(token, str):
        raise SystemExit("OIDC token response missing a JWT value")
    return token


def main() -> int:
    printed = json.dumps(claims_from_jwt(request_id_token()), indent=2)
    print(printed)
    print(
        "Compare `sub` to IAM StringLike on "
        "arn:aws:iam::489470371031:role/midkernel-github-actions. "
        "Name-only `repo:midkernel/runner:*` does not match immutable "
        "`repo:midkernel@<owner_id>/runner@<repo_id>:*`.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
