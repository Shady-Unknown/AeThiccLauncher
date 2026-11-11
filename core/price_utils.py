"""Price parsing and formatting in integer cents.

The old catalog stores formatted strings (``"$59.99"``, ``"26.25 USD"``).
New entries also carry ``final_cents``/``initial_cents``/``currency``/
``region``. These helpers keep both worlds readable and make phrases and
stats currency-aware.
"""
from __future__ import annotations

import re

# ISO code -> (symbol, decimals, symbol_before)
_CURRENCIES = {
    "USD": ("$", 2, True),
    "EUR": ("€", 2, False),
    "GBP": ("£", 2, True),
    "JPY": ("¥", 0, True),
    "CNY": ("¥", 2, True),
    "KRW": ("₩", 0, True),
    "CAD": ("CA$", 2, True),
    "AUD": ("A$", 2, True),
    "BRL": ("R$", 2, True),
    "MXN": ("MX$", 2, True),
    "INR": ("₹", 2, True),
    "PLN": ("zł", 2, False),
    "SEK": ("kr", 2, False),
    "NOK": ("kr", 2, False),
    "DKK": ("kr", 2, False),
    "CHF": ("CHF", 2, True),
    "TRY": ("₺", 2, True),
    "RUB": ("₽", 2, False),
    "UAH": ("₴", 2, False),
    "ZAR": ("R", 2, True),
}

_SYMBOL_TO_ISO = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₩": "KRW",
    "₹": "INR",
    "₺": "TRY",
    "₽": "RUB",
    "₴": "UAH",
    "R$": "BRL",
}


def _decimal_separator(text: str) -> str | None:
    """Return the separator that acts as decimal separator in ``text``."""
    last_dot = text.rfind(".")
    last_comma = text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        return "." if last_dot > last_comma else ","
    if last_comma >= 0:
        digits_after = len(text) - last_comma - 1
        # "1,234" is thousands; "59,99" is decimal.
        return "," if digits_after in (1, 2) else None
    if last_dot >= 0:
        digits_after = len(text) - last_dot - 1
        return "." if digits_after in (1, 2) else None
    return None


def parse_price_to_cents(text) -> int | None:
    """Parse a formatted price into integer cents.

    Accepts ``"$59.99"``, ``"59,99 €"``, ``"26.25 USD"``, ``"1.234,56"`` and
    returns ``None`` when no number is found.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(round(float(text) * 100))
    raw = str(text).strip()
    if not raw:
        return None
    match = re.search(r"[-+]?\d[\d\s.,]*", raw)
    if not match:
        return None
    number = match.group(0).replace(" ", "").replace("\u00a0", "")
    sign = -1 if number.startswith("-") else 1
    number = number.lstrip("+-")
    decimal = _decimal_separator(number)
    if decimal:
        thousands = "," if decimal == "." else "."
        integer, fraction = number.rsplit(decimal, 1)
        integer = integer.replace(thousands, "")
        fraction = (fraction + "00")[:2]
        value = int(integer or 0) * 100 + int(fraction)
    else:
        value = int(number.replace(",", "").replace(".", "") or 0) * 100
    return sign * value


def currency_symbol(currency: str, fallback: str = "") -> str:
    info = _CURRENCIES.get((currency or "").upper())
    return info[0] if info else (fallback or currency or "")


def format_cents(cents, currency: str = "USD", locale: str | None = None) -> str:
    """Format integer cents for display.

    ``locale`` is accepted for future use; the current output is intentionally
    stable and independent from the OS locale.
    """
    try:
        value = int(cents)
    except (TypeError, ValueError):
        return ""
    code = (currency or "USD").upper()
    symbol, decimals, symbol_before = _CURRENCIES.get(code, (code, 2, True))
    sign = "-" if value < 0 else ""
    value = abs(value)
    if decimals == 0:
        amount = f"{value // 100}"
    else:
        amount = f"{value // 100}.{value % 100:02d}"
    if locale and locale.lower().startswith(("it", "de", "fr", "es", "pt", "nl")):
        amount = amount.replace(".", ",")
    if symbol_before:
        return f"{sign}{symbol}{amount}"
    return f"{sign}{amount} {symbol}"


def detect_currency(text: str, default: str = "USD") -> str:
    raw = str(text or "")
    upper = raw.upper()
    for code in _CURRENCIES:
        if code in upper:
            return code
    for symbol, code in _SYMBOL_TO_ISO.items():
        if symbol in raw:
            return code
    return default
