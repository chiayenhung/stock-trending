from __future__ import annotations

import pytest

from stocktrend.errors import SafetyViolation
from stocktrend.security import validate_public_https_url


def test_public_https_url_is_allowed() -> None:
    validate_public_https_url("https://www.sec.gov/Archives/example.json")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/data",
        "https://localhost/data",
        "https://127.0.0.1/data",
        "https://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/data",
    ],
)
def test_unsafe_source_url_is_rejected(url: str) -> None:
    with pytest.raises(SafetyViolation):
        validate_public_https_url(url)
