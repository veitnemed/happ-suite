"""Conservative country hints from provider node labels."""

from __future__ import annotations

import re


_COUNTRIES = {
    "RU": ("россия", "russia", "russian"),
    "FI": ("финляндия", "finland"),
    "IT": ("италия", "italy"),
    "DE": ("германия", "germany"),
    "NL": ("нидерланды", "netherlands", "holland"),
    "KZ": ("казахстан", "kazakhstan"),
    "AE": ("оаэ", "uae", "emirates"),
    "US": ("сша", "usa", "united states"),
    "EE": ("эстония", "estonia"),
}


def country_hint(name: str) -> str | None:
    """Return a country only when the node label explicitly names one."""
    folded = name.casefold()
    if re.search(r"(?<!\w)(ru|россия|russia|russian)(?!\w)", folded):
        return "RU"
    for country, words in _COUNTRIES.items():
        if any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", folded)
               for word in words):
            return country
    flags = [char for char in name if "\U0001f1e6" <= char <= "\U0001f1ff"]
    if len(flags) >= 2:
        code = "".join(chr(ord(char) - ord("\U0001f1e6") + ord("A")) for char in flags[:2])
        if code not in {"EU", "UN"}:
            return code
    return None
