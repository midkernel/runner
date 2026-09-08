"""Fetch a public Midkernel playbook and return the prompt body."""

from __future__ import annotations

import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from midkernel_runner.config import RunConfig

USER_AGENT = "midkernel-runner"
FRONTMATTER_RE = re.compile(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?")

# Exact public midkernel/playbooks security-review.md body (skill line).
SECURITY_REVIEW_FALLBACK = "Perform a /security-review on this project"


class PlaybookError(RuntimeError):
    """Playbook prompt could not be resolved."""


def playbook_raw_url(config: RunConfig) -> str:
    path = f"{config.playbook_slug}.md"
    return (
        f"https://raw.githubusercontent.com/{config.playbooks_owner}/"
        f"{config.playbooks_name}/{config.playbooks_ref}/{path}"
    )


def strip_frontmatter(markdown: str) -> str:
    trimmed = markdown.replace("\ufeff", "").lstrip()
    match = FRONTMATTER_RE.match(trimmed)
    if not match:
        return markdown.strip()
    return trimmed[match.end() :].strip()


def fetch_playbook_markdown(url: str, *, timeout: int = 30) -> str:
    request = Request(url, headers={"Accept": "text/markdown, text/plain", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise PlaybookError(f"Playbook fetch returned HTTP {response.status} for {url}")
            return response.read().decode("utf-8")
    except HTTPError as exc:
        raise PlaybookError(f"Playbook fetch HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise PlaybookError(f"Playbook fetch failed for {url}: {exc.reason}") from exc


def load_playbook_prompt(config: RunConfig, *, fetch=fetch_playbook_markdown) -> str:
    url = playbook_raw_url(config)
    try:
        body = strip_frontmatter(fetch(url))
        if body:
            return body
    except PlaybookError:
        if config.playbook_slug != "security-review":
            raise
    if config.playbook_slug == "security-review":
        return SECURITY_REVIEW_FALLBACK
    raise PlaybookError(f"Playbook {config.playbook_slug} has an empty body")
