import base64
import json

import pytest

from pathlib import Path
import importlib.util

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "print_oidc_claims.py"
_SPEC = importlib.util.spec_from_file_location("print_oidc_claims", _SCRIPT)
assert _SPEC and _SPEC.loader
print_oidc_claims = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(print_oidc_claims)


def _jwt(payload: dict) -> str:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


def test_claims_from_jwt_keeps_allowlisted_keys_only():
    token = _jwt(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "sts.amazonaws.com",
            "sub": "repo:midkernel@324066512/runner@1357082961:ref:refs/heads/main",
            "repository": "midkernel/runner",
            "repository_owner": "midkernel",
            "ref": "refs/heads/main",
            "workflow": "CI",
            "job_workflow_ref": "midkernel/runner/.github/workflows/ci.yml@refs/heads/main",
            "actor": "must-not-appear",
            "email": "secret@example.com",
        }
    )
    claims = print_oidc_claims.claims_from_jwt(token)
    assert list(claims) == list(print_oidc_claims.CLAIM_KEYS)
    assert claims["sub"] == "repo:midkernel@324066512/runner@1357082961:ref:refs/heads/main"
    assert claims["aud"] == "sts.amazonaws.com"
    assert "actor" not in claims
    assert "email" not in claims


def test_claims_from_jwt_rejects_non_jwt():
    with pytest.raises(ValueError, match="three-part JWT"):
        print_oidc_claims.claims_from_jwt("not-a-jwt")
