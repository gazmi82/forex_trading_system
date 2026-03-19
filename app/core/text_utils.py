from __future__ import annotations

import re


def slugify_text(value: str) -> str:
    text = (value or "").strip().lower().replace("/", "-").replace("_", "-")
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "unknown"


def normalize_pair(value: str) -> str:
    return (value or "").replace("/", "_").strip().upper()


def display_pair(value: str) -> str:
    pair = str(value or "EUR_USD").strip()
    return pair.replace("_", "/")
