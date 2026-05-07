"""Shared site configuration for generated links and local hosting."""
from __future__ import annotations

import os
from urllib.parse import quote

DEFAULT_PUBLIC_BASE_URL = "https://buffetagent.shawny-project42.com"


def public_base_url() -> str:
    """Return the externally visible buffetAgent base URL."""
    return (
        os.environ.get("BUFFET_PUBLIC_BASE_URL")
        or DEFAULT_PUBLIC_BASE_URL
    ).strip().rstrip("/")


def public_url(path: str = "") -> str:
    """Build a public URL, preserving already encoded path separators."""
    base = public_base_url()
    path = (path or "").strip()
    if not path:
        return base
    if path.startswith("/"):
        path = path[1:]
    return f"{base}/{path}"


def public_file_url(relative_path: str) -> str:
    """Build a public URL for a generated HTML file with safe URL quoting."""
    rel = (relative_path or "").replace("\\", "/").lstrip("/")
    return public_url(quote(rel))

