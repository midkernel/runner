import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publisher_activation", ROOT / "scripts/check_publisher_activation.py")
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)
ACCOUNT = "123456789012"  # Synthetic fixture; never deploy.
OTHER_ACCOUNT = "210987654321"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/midkernel-github-publish-runner"
BAD_ACCOUNTS = ["", "123", "1" * 13, " " + ACCOUNT, ACCOUNT + "\n", "１２３４５６７８９０１２", "<APPROVED_ACCOUNT_ID>"]


def check(role, account="", repository=activation.REPOSITORY, ref="refs/heads/main", event="push"):
    return activation.publication_enabled(role, repository, ref, event, approved_account=account)


@pytest.mark.parametrize("account", ["", ACCOUNT, *BAD_ACCOUNTS[1:]])
def test_absent_role_remains_disabled_regardless_of_account(account):
    assert check("", account) is False


@pytest.mark.parametrize("account", [*BAD_ACCOUNTS, None, 123456789012])
def test_active_role_requires_independent_exact_ascii_account(account):
    with pytest.raises(ValueError):
        check(ROLE, account)


@pytest.mark.parametrize("role", [
    ROLE.replace(ACCOUNT, OTHER_ACCOUNT),
    ROLE.replace("midkernel-github-publish-runner", "midkernel-github-actions"),
    ROLE + "-other", ROLE.replace(":role/", ":role/path/"),
    ROLE.replace("arn:aws:", "arn:aws-us-gov:"), ROLE.replace(":iam:", ":sts:"),
    ROLE.replace(":role/", ":user/"), " " + ROLE, ROLE + "\n", " ", "true",
])
def test_only_exact_dedicated_role_in_approved_account_activates(role):
    with pytest.raises(ValueError):
        check(role, ACCOUNT)


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_valid_pair_activates_allowed_context(event):
    assert check(ROLE, ACCOUNT, event=event) is True


@pytest.mark.parametrize("role", ["", ROLE])
@pytest.mark.parametrize("context", [
    {"repository": "midkernel/another-repo"}, {"ref": "refs/heads/feature"},
    {"event": "pull_request"}, {"event": "pull_request_target"}, {"ref": "refs/tags/main"},
])
def test_context_is_checked_even_without_activation(role, context):
    with pytest.raises(ValueError):
        check(role, ACCOUNT, **context)


@pytest.mark.parametrize("role,account,enabled", [
    ("", "", False), ("", ACCOUNT, False), ("", "malformed", False),
    (ROLE, ACCOUNT, True), (ROLE, "", None), (ROLE, ACCOUNT + "\n", None),
    (ROLE.replace(ACCOUNT, OTHER_ACCOUNT), ACCOUNT, None),
])
def test_cli_binds_outputs_and_does_not_print_identities(tmp_path, role, account, enabled):
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    env = {
        "GITHUB_REPOSITORY": activation.REPOSITORY, "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "push", "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
        "ECR_PUBLISHER_ROLE_ARN": role, "MIDKERNEL_APPROVED_AWS_ACCOUNT_ID": account,
    }
    result = subprocess.run([sys.executable, str(ROOT / "scripts/check_publisher_activation.py")],
                            env=env, capture_output=True, text=True)
    outputs = output.read_text() if output.exists() else ""
    public_text = result.stdout + result.stderr + (summary.read_text() if summary.exists() else "")
    for identifier in [ACCOUNT, OTHER_ACCOUNT, ROLE, role]:
        if identifier:
            assert identifier not in public_text
    if enabled is None:
        assert result.returncode != 0
        assert outputs == ""
    elif enabled:
        assert result.returncode == 0
        assert outputs == f"enabled=true\nrole_arn={ROLE}\naccount_id={ACCOUNT}\n"
    else:
        assert result.returncode == 0
        assert outputs == "enabled=false\n"


def test_workflow_uses_validated_identity_before_publication():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    readiness = workflow.split("  publisher-readiness:\n", 1)[1].split("  image:\n", 1)[0]
    ecr = workflow.split("  ecr:\n", 1)[1]
    assert "github.repository == 'midkernel/runner'" in readiness
    assert "github.ref == 'refs/heads/main'" in readiness
    assert "ECR_PUBLISHER_ROLE_ARN: ${{ vars.ECR_PUBLISHER_ROLE_ARN }}" in readiness
    assert "MIDKERNEL_APPROVED_AWS_ACCOUNT_ID: ${{ vars.MIDKERNEL_APPROVED_AWS_ACCOUNT_ID }}" in readiness
    assert "role_arn: ${{ steps.activation.outputs.role_arn }}" in readiness
    assert "account_id: ${{ steps.activation.outputs.account_id }}" in readiness
    assert "id-token: write" not in readiness
    assert "configure-aws-credentials" not in readiness
    assert "needs: [test, image, publisher-readiness]" in ecr
    assert "if: needs.publisher-readiness.outputs.enabled == 'true'" in ecr
    assert "role-to-assume: ${{ needs.publisher-readiness.outputs.role_arn }}" in ecr
    assert "APPROVED_ACCOUNT_ID: ${{ needs.publisher-readiness.outputs.account_id }}" in ecr
    assert ecr.index("Verify approved ECR registry") < ecr.index("name: Build and push")
    assert "env.OIDC_ROLE" not in workflow
    assert "vars.ECR_PUBLISHER_ROLE_ARN" not in ecr


@pytest.mark.parametrize("registry,success", [
    (f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com", True),
    (f"{OTHER_ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com", False),
    (f"{ACCOUNT}.dkr.ecr.us-west-2.amazonaws.com", False),
    ("", False), ("$(exit 0)", False),
])
def test_registry_mismatch_stops_before_push(registry, success):
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    step = workflow.split("      - name: Verify approved ECR registry\n", 1)[1].split("      - uses:", 1)[0]
    script = "\n".join(line[10:] for line in step.split("        run: |\n", 1)[1].splitlines())
    result = subprocess.run(["/bin/bash", "-e", "-c", script + "\nprintf push_reachable"],
                            env={"APPROVED_ACCOUNT_ID": ACCOUNT, "AWS_REGION": "us-east-1", "ACTUAL_REGISTRY": registry},
                            capture_output=True, text=True)
    assert (result.returncode == 0) is success
    assert ("push_reachable" in result.stdout) is success
    assert ACCOUNT not in result.stdout + result.stderr
    assert OTHER_ACCOUNT not in result.stdout + result.stderr
