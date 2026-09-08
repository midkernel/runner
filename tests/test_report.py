import pytest

from midkernel_runner.report import ReportError, findings_count, persist_report, validate_report


REAL = """# Security review of midkernel/playbooks

The tree is a thin public playbook registry. No executable services.

Findings: 0
"""


def test_accepts_real_report():
    assert "Findings: 0" in validate_report(REAL)
    assert findings_count(REAL) == 0


def test_rejects_stub():
    with pytest.raises(ReportError, match="stub"):
        validate_report("This is a stub success report for Midkernel.\nFindings: 0\n" + ("x" * 80))


def test_rejects_short():
    with pytest.raises(ReportError, match="too short"):
        validate_report("ok")


def test_rejects_gateway_placeholder():
    with pytest.raises(ReportError, match="stub"):
        validate_report(
            "AI Gateway is not configured. executor_unconfigured.\n" + ("word " * 40)
        )


def test_persist(tmp_path):
    path = tmp_path / "report.md"
    persist_report(path, REAL)
    assert path.read_text().startswith("# Security review")
