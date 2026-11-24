"""Validation and formatting helpers for the Research Showcase and Opportunities Hub."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

DISCIPLINES = {
    "neuro": "Neuroscience",
    "med": "Medicine",
    "math": "Mathematics",
    "inter": "Interdisciplinary",
}
OPPORTUNITY_CATEGORIES = {
    "competition": "Competition",
    "olympiad": "Olympiad",
    "research": "Research",
    "programme": "Programme",
}

TITLE_MAX = 100
DESCRIPTION_MIN = 40
DESCRIPTION_MAX = 1500
MAX_PENDING_PER_USER = 3

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ValidationError(ValueError):
    """User-facing validation failure."""


def clean_text(value: str) -> str:
    return _CONTROL.sub("", value).strip()


def validate_url(raw: str, *, required: bool = False) -> Optional[str]:
    """Accept only well-formed http(s) URLs. Returns the cleaned URL or None."""
    raw = clean_text(raw or "")
    if not raw:
        if required:
            raise ValidationError("A link is required.")
        return None
    if len(raw) > 400 or any(c.isspace() for c in raw):
        raise ValidationError("That link looks invalid (no spaces, max 400 characters).")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
        raise ValidationError("Links must start with http:// or https:// and include a domain.")
    return raw


def validate_submission(title: str, description: str, link: str) -> tuple[str, str, Optional[str]]:
    title, description = clean_text(title), clean_text(description)
    if not title or len(title) > TITLE_MAX:
        raise ValidationError(f"Title is required (max {TITLE_MAX} characters).")
    if not (DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX):
        raise ValidationError(
            f"Description must be {DESCRIPTION_MIN}-{DESCRIPTION_MAX} characters "
            f"(yours: {len(description)})."
        )
    return title, description, validate_url(link)


def validate_opportunity(title: str, url: str, category: str, description: Optional[str]) -> tuple[str, str, str, Optional[str]]:
    title = clean_text(title)
    if not (3 <= len(title) <= 100):
        raise ValidationError("Title must be 3-100 characters.")
    if category not in OPPORTUNITY_CATEGORIES:
        raise ValidationError("Unknown category.")
    clean_url = validate_url(url, required=True)
    desc = clean_text(description or "")[:300] or None
    return title, clean_url, category, desc  # type: ignore[return-value]
