import pytest

from midkernel_runner.config import load_config
from midkernel_runner.playbook import (
    SECURITY_REVIEW_FALLBACK,
    PlaybookError,
    load_playbook_prompt,
    playbook_raw_url,
    strip_frontmatter,
)


SAMPLE = """---
name: security-review
slug: security-review
---

Perform a /security-review on this project
"""


def test_strip_frontmatter():
    assert strip_frontmatter(SAMPLE) == "Perform a /security-review on this project"


def test_raw_url():
    cfg = load_config({"RUN_ID": "r", "GITHUB_OWNER": "o", "GITHUB_NAME": "n"})
    assert playbook_raw_url(cfg) == (
        "https://raw.githubusercontent.com/midkernel/playbooks/main/security-review.md"
    )


def test_fetch_ok():
    cfg = load_config({"RUN_ID": "r", "GITHUB_OWNER": "o", "GITHUB_NAME": "n"})
    prompt = load_playbook_prompt(cfg, fetch=lambda url: SAMPLE)
    assert prompt == "Perform a /security-review on this project"


def test_fallback_on_fetch_error():
    cfg = load_config({"RUN_ID": "r", "GITHUB_OWNER": "o", "GITHUB_NAME": "n"})

    def boom(_url):
        raise PlaybookError("offline")

    assert load_playbook_prompt(cfg, fetch=boom) == SECURITY_REVIEW_FALLBACK


def test_other_slug_does_not_fallback():
    cfg = load_config(
        {
            "RUN_ID": "r",
            "GITHUB_OWNER": "o",
            "GITHUB_NAME": "n",
            "PLAYBOOK_SLUG": "solana-validator-security",
        }
    )

    def boom(_url):
        raise PlaybookError("offline")

    with pytest.raises(PlaybookError):
        load_playbook_prompt(cfg, fetch=boom)
