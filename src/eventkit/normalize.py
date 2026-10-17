"""Deterministic normalization and small syntax checks for roster fields."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .model import Attendee


_EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_EMAIL_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# This is deliberately a small, explicit list.  EK06 is a suggestion for
# common consumer domains, not a claim that every domain outside this list is
# wrong.
COMMON_EMAIL_DOMAINS: tuple[str, ...] = (
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "live.com",
    "msn.com",
    "aol.com",
    "mail.com",
    "ymail.com",
    "gmx.com",
    "zoho.com",
)


def _text(value: str | None) -> str:
    return "" if value is None else str(value)


def normalize_name(value: str | None) -> str:
    """Trim, collapse whitespace, and NFC-normalize a name.

    Case and spelling are intentionally preserved.
    """

    return unicodedata.normalize("NFC", " ".join(_text(value).split()))


def normalize_email(value: str | None) -> str:
    """Return the comparison form of an email: trimmed and lower-case."""

    return _text(value).strip().lower()


def normalize_phone(value: str | None) -> str:
    """Keep ASCII digits and preserve a plus only when it leads the input."""

    text = _text(value).strip()
    prefix = "+" if text.startswith("+") else ""
    digits = "".join(character for character in text if "0" <= character <= "9")
    return prefix + digits


def normalize_institution(value: str | None) -> str:
    """Normalize institution whitespace and Unicode for comparisons."""

    return unicodedata.normalize("NFC", " ".join(_text(value).split()))


def normalize_attendee(attendee: Attendee) -> Attendee:
    """Return an attendee with all comparison forms populated."""

    return Attendee(
        row_number=attendee.row_number,
        name=attendee.name,
        email=attendee.email,
        phone=attendee.phone,
        institution=attendee.institution,
        normalized_name=normalize_name(attendee.name),
        normalized_email=normalize_email(attendee.email),
        normalized_phone=normalize_phone(attendee.phone),
        normalized_institution=normalize_institution(attendee.institution),
    )


def is_valid_email(value: str | None) -> bool:
    """Apply a conservative, dependency-free email syntax check."""

    email = normalize_email(value)
    if email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not _EMAIL_LOCAL_RE.fullmatch(local)
    ):
        return False
    return bool(_EMAIL_DOMAIN_RE.fullmatch(domain) and len(domain) <= 253)


def email_domain(value: str | None) -> str:
    """Return a normalized domain, or an empty string when no ``@`` exists."""

    email = normalize_email(value)
    return email.rsplit("@", 1)[1] if "@" in email else ""


def _domain_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def email_domain_suggestion(value: str | None) -> str | None:
    """Suggest a nearby common email domain for a valid-looking address.

    The exact common domains are ignored.  A suggestion requires a close
    spelling (at least 0.80 similarity and no more than two characters of
    length difference), which avoids flagging arbitrary organization domains.
    """

    email = normalize_email(value)
    if not is_valid_email(email):
        return None
    domain = email_domain(email)
    if domain in COMMON_EMAIL_DOMAINS:
        return None

    candidates = (
        candidate
        for candidate in COMMON_EMAIL_DOMAINS
        if abs(len(candidate) - len(domain)) <= 2
    )
    best: str | None = None
    best_ratio = 0.80
    for candidate in candidates:
        ratio = _domain_similarity(domain, candidate)
        if ratio >= best_ratio:
            if best is None or ratio > best_ratio or candidate < best:
                best = candidate
                best_ratio = ratio
    return best


# A descriptive alias makes the intent clear at validation call sites.
likely_common_domain_typo = email_domain_suggestion
