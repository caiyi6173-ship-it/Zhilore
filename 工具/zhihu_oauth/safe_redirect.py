"""Validated local destinations shared by login, route guards and callbacks."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

MAX_RETURN_TO = 512
LOGIN_PATH = "/login"
NON_PAGE_PREFIXES = ("/login", "/auth", "/api", "/assets", "/lib", "/oauth", "/workspace/assets", "/笔记库")


def _safe_layer(value: str) -> bool:
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return False
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return False
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or any(segment in (".", "..") for segment in parts.path.split("/")):
        return False
    # Also reject encoded/semicolon spellings of login routes and nested returnTo chains.
    canonical = "/".join(segment.split(";", 1)[0] for segment in parts.path.split("/") if segment).lower()
    canonical = "/" + canonical
    if any(canonical == prefix or canonical.startswith(prefix + "/") for prefix in NON_PAGE_PREFIXES):
        return False
    return "returnTo" not in parse_qs(parts.query, keep_blank_values=True)


def safe_return_path(raw: str | None, default: str) -> str:
    """Keep a local path, query and fragment verbatim; unsafe values use a trusted default.

    Every decoding layer is checked, but the result is never decoded for navigation.
    This rejects encoded slashes, traversal, backslashes and controls without
    changing a legitimate query or fragment. Keep login-redirect.js in parity.
    """
    if not isinstance(raw, str) or not raw or len(raw) > MAX_RETURN_TO:
        return default
    if re.search(r"%(?![0-9a-fA-F]{2})", raw):
        return default
    try:
        raw.encode("utf-8", errors="strict")
        value = raw
        for _ in range(8):
            if not _safe_layer(value):
                return default
            decoded = unquote(value, errors="strict")
            if decoded == value:
                return raw
            value = decoded
    except (ValueError, UnicodeError):
        pass
    return default


def login_url(return_to: str | None, default: str, result: str | None = None) -> str:
    values = {"returnTo": safe_return_path(return_to, default)}
    if result:
        values["result"] = result
    return LOGIN_PATH + "?" + urlencode(values)


def requested_return_path(query, default: str) -> str:
    values = query.getlist("returnTo")
    return safe_return_path(values[0], default) if len(values) == 1 else default


def request_return_path(request, default: str) -> str:
    # ASGI's decoded path would mistake an encoded '?' or '#' for a delimiter.
    raw_path = request.scope.get("raw_path")
    path = raw_path.decode("ascii") if raw_path else request.url.path
    query = request.url.query
    return safe_return_path(path + ("?" + query if query else ""), default)
