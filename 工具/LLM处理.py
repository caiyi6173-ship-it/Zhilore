# -*- coding: utf-8 -*-
"""OpenAI-compatible LLM adapter for article decomposition.

The adapter follows the action-loop planner style: schema-valid JSON output,
bounded retries, timeout, redaction, and a deterministic local fallback. The
API key is read only from the process environment or an explicitly passed env
file, and is never written to project output.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = "zhihu-kb/article-split-v1"
PROMPT_VERSION = "zhihu-kb/article-splitter-v1"


def 载入env文件(path: str) -> None:
    """Load OPENAI_* settings without overriding variables already set."""
    if not path or not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key.startswith("OPENAI_") and key not in os.environ:
                os.environ[key] = value


def 敏感信息脱敏(text: str) -> str:
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[EMAIL]", text or "", flags=re.I)
    text = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[PHONE]", text)
    return text


def 清理标签(values: Any, limit: int = 6) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,，、\s]+", values)
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = re.sub(r"#+", "", str(value or "")).strip()
        tag = re.sub(r"\s+", "-", tag)
        tag = re.sub(r"[<>:\"/\\|?*]", "-", tag).strip("-_")
        if not tag or len(tag) > 24 or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        result.append(tag)
        if len(result) >= limit:
            break
    return result


def _清Markdown(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:16000]


def 规范化LLM结果(payload: Any, 标题: str, 正文: str, 已有标签: list[str] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("LLM output is not an object")
    sections_raw = payload.get("sections")
    if not isinstance(sections_raw, list) or not sections_raw:
        raise ValueError("LLM output lacks sections")

    sections = []
    for item in sections_raw:
        if not isinstance(item, dict):
            continue
        section_title = str(item.get("title") or "").strip()
        markdown = _清Markdown(item.get("markdown"))
        if not section_title or not markdown:
            continue
        tags = 清理标签(item.get("tags"), 5)
        if not tags:
            tags = 清理标签(已有标签, 2) or ["未分类"]
        sections.append({
            "标题": section_title[:60],
            "正文": markdown,
            "标签": tags,
            "要点": [str(x).strip()[:160] for x in (item.get("key_points") or []) if str(x).strip()][:5],
        })

    if not 2 <= len(sections) <= 12:
        raise ValueError("LLM section count must be between 2 and 12")

    return {
        "schema": SCHEMA_VERSION,
        "摘要": str(payload.get("summary") or "").strip()[:1200] or 正文[:180],
        "标签": 清理标签(payload.get("article_tags"), 8) or 清理标签(已有标签, 5) or ["知乎"],
        "sections": sections,
        "模式": "llm",
    }


def 本地拆分(标题: str, 正文: str, 已有标签: list[str] | None = None) -> dict[str, Any]:
    """Deterministic fallback used when an LLM is unavailable or invalid."""
    lines = (正文 or "").replace("\r\n", "\n").strip().split("\n")
    blocks: list[tuple[str, list[str]]] = []
    current_title = "全文"
    current: list[str] = []

    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        match = None if in_fence else re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if match:
            if current and any(x.strip() for x in current):
                blocks.append((current_title, current))
            current_title = match.group(2).strip()
            current = []
        else:
            current.append(line)
    if current and any(x.strip() for x in current):
        blocks.append((current_title, current))

    if len(blocks) < 2:
        paragraphs = re.split(r"\n\s*\n", 正文.strip()) or [正文]
        chunks = max(2, min(5, (len(paragraphs) + 2) // 3))
        size = max(1, (len(paragraphs) + chunks - 1) // chunks)
        blocks = [
            (f"第 {i + 1} 部分", paragraphs[i * size:(i + 1) * size])
            for i in range(chunks)
            if paragraphs[i * size:(i + 1) * size]
        ]

    base_tags = 清理标签(已有标签, 6) or ["知乎"]
    sections = []
    for index, (section_title, content) in enumerate(blocks, 1):
        text = "\n".join(content).strip()
        if not text:
            continue
        candidate = [tag for tag in base_tags if tag.lower() in f"{section_title}\n{text}".lower()]
        sections.append({
            "标题": section_title[:60] or f"第 {index} 部分",
            "正文": text,
            "标签": candidate[:2] or base_tags[:2],
            "要点": [],
        })

    if len(sections) == 1:
        text = sections[0]["正文"]
        middle = len(text) // 2
        boundary = text.rfind("\n", 0, middle) if "\n" in text[:middle + 1000] else middle
        boundary = max(1, boundary)
        sections = [
            {"标题": "前半部分", "正文": text[:boundary].strip(), "标签": base_tags[:2], "要点": []},
            {"标题": "后半部分", "正文": text[boundary:].strip(), "标签": base_tags[:2], "要点": []},
        ]

    return {
        "schema": SCHEMA_VERSION,
        "摘要": re.sub(r"\s+", " ", 正文)[:180],
        "标签": base_tags,
        "sections": sections,
        "模式": "local-fallback",
    }


@dataclass
class LLM配置:
    enabled: bool
    api_key: str
    base_url: str
    model: str
    timeout: int
    attempts: int


class LLM客户端:
    def __init__(self, env_file: str | None = None):
        if env_file:
            载入env文件(env_file)
        self.配置 = LLM配置(
            enabled=os.environ.get("OPENAI_ENABLED", "true").lower() != "false",
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip(),
            timeout=max(5, int(os.environ.get("OPENAI_TIMEOUT_MS", "120000")) // 1000),
            attempts=max(1, min(4, int(os.environ.get("OPENAI_MAX_ATTEMPTS", "2")))),
        )

    @property
    def 可用(self) -> bool:
        return self.配置.enabled and bool(self.配置.api_key)

    def 分析文章(self, 标题: str, 正文: str, 已有标签: list[str] | None = None) -> dict[str, Any]:
        if not self.可用:
            return 本地拆分(标题, 正文, 已有标签)

        system = (
            "你是知乎文章知识架构师。把文章拆成可独立阅读、可互相关联的 Obsidian 分节。"
            "只输出 JSON，schema: {summary:string,article_tags:string[],sections:[{title:string,"
            "markdown:string,tags:string[],key_points:string[]]}。要求 3-8 个 sections；"
            "保留原文事实、数据和论证，不虚构；每个 section 2-5 个具体标签；"
            "markdown 不重复一级标题；中文输出。"
        )
        user = json.dumps({
            "title": 标题,
            "existing_tags": 已有标签 or [],
            "content": 敏感信息脱敏(正文)[:24000],
        }, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        body = json.dumps({
            "model": self.配置.model,
            "messages": messages,
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.配置.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.配置.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.配置.attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.配置.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                content = payload["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                result = 规范化LLM结果(parsed, 标题, 正文, 已有标签)
                result["模型"] = self.配置.model
                result["提示词版本"] = PROMPT_VERSION
                return result
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
                last_error = exc
                if attempt < self.配置.attempts:
                    time.sleep(0.5 * attempt)

        print(f"[LLM] 拆分失败，使用本地规则降级：{last_error}", flush=True)
        return 本地拆分(标题, 正文, 已有标签)
