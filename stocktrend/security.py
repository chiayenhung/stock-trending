"""Security checks for external source metadata."""

from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlsplit

from .errors import SafetyViolation


def validate_public_https_url(value: Optional[str]) -> None:
    if value is None:
        return
    parsed = urlsplit(value)
    if parsed.scheme != "https":
        raise SafetyViolation("source URL must use https")
    if parsed.username or parsed.password:
        raise SafetyViolation("source URL must not contain credentials")
    if not parsed.hostname:
        raise SafetyViolation("source URL must include a host")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in ("localhost", "localhost.localdomain"):
        raise SafetyViolation("local source URL is prohibited")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise SafetyViolation("non-public source URL is prohibited")
