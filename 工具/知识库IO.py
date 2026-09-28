# -*- coding: utf-8 -*-
"""Shared, dependency-light Markdown IO primitives for the knowledge vault."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml


def 拆frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return data if isinstance(data, dict) else {}, text[match.end():]


def 拼frontmatter(data: dict[str, Any], body: str) -> str:
    head = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=10000)
    return f"---\n{head}---\n\n{body.strip()}\n"


def 安全名(name: str, limit: int = 72) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(name or "")).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return value[:limit].rstrip(" .") or "未命名"


def 原子写入(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return True


def 读JSON(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def 写JSON若变化(path: Path, payload: dict[str, Any]) -> bool:
    existing = 读JSON(path, None)
    semantic = {key: value for key, value in payload.items() if key != "生成时间"}
    if isinstance(existing, dict) and {key: value for key, value in existing.items() if key != "生成时间"} == semantic:
        return False
    return 原子写入(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
