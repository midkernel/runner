"""Validate that report.md is a real agent artifact — never a stub."""

from __future__ import annotations

import re
from pathlib import Path

STUB_MARKERS = (
    "this is a stub",
    "stub success",
    "placeholder report",
    "fabricated succeeded",
    "executor_unconfigured",
    "ai gateway is not configured",
    "midkernel stub",
    "todo: write findings",
    "lorem ipsum",
)

MIN_REPORT_BYTES = 80
FINDINGS_RE = re.compile(r"\bfindings(?:\s+count)?\s*[:\-]\s*(\d+)\b", re.I)


class ReportError(RuntimeError):
    """report.md is missing, empty, or a stub."""


def read_report(path: Path) -> str:
    if not path.is_file():
        raise ReportError(f"report.md was not produced at {path}")
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise ReportError("report.md is empty")
    return text


def validate_report(text: str) -> str:
    lowered = text.lower()
    for marker in STUB_MARKERS:
        if marker in lowered:
            raise ReportError(f"report.md looks like a stub ({marker!r}); refusing to upload")
    encoded = text.encode("utf-8")
    if len(encoded) < MIN_REPORT_BYTES:
        raise ReportError(
            f"report.md is too short ({len(encoded)} bytes); refusing to treat it as a real review"
        )
    if FINDINGS_RE.search(text):
        return text
    if re.search(r"^#{1,3}\s+\S", text, re.M):
        return text
    # Agent wrote a paragraph review without a heading — accept if substantial.
    if len(text.split()) >= 40:
        return text
    raise ReportError("report.md does not look like a security review (no Findings/heading/body)")


def findings_count(text: str) -> int | None:
    match = FINDINGS_RE.search(text)
    return int(match.group(1)) if match else None


def persist_report(path: Path, text: str) -> str:
    validated = validate_report(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(validated + ("" if validated.endswith("\n") else "\n"), encoding="utf-8")
    return validated
