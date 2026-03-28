from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation


def parse_italian_number(text: str | None) -> Decimal | None:
    """Parse an Italian-formatted number string into a Decimal.

    Handles:
    - Thousands separator: '.' (e.g. 8.035,71)
    - Decimal separator: ',' (e.g. 1765,69)
    - Trailing minus for deductions: '1765,69-' -> -1765.69
    - Multiple decimals: '8,00000' -> 8.00000
    """
    if not text or not text.strip():
        return None
    s = text.strip()

    # Detect and remove trailing minus
    negative = s.endswith("-")
    if negative:
        s = s[:-1].strip()

    if not s:
        return None

    # Remove thousands separator (dots that are followed by exactly 3 digits
    # before the next dot or comma or end), then replace comma with dot
    # Simple approach: remove all dots, replace comma with dot
    s = s.replace(".", "").replace(",", ".")

    try:
        result = Decimal(s)
    except InvalidOperation:
        return None

    return -result if negative else result


def compute_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def split_competenze_ritenute(
    amount: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Split a signed amount into (competenze, ritenute).

    Positive -> competenze, Negative -> ritenute (stored as positive).
    """
    if amount is None:
        return None, None
    if amount >= 0:
        return amount, None
    return None, abs(amount)
