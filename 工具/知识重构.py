# -*- coding: utf-8 -*-
"""Rebuild single-file articles into Obsidian-style article folders.

Input evidence is preserved under `笔记库/.raw/sources/`. Generated knowledge:

    笔记库/wiki/articles/<collection>/<article>/
        00 MOC.md
        01 <section>.md
        02 <section>.md
        assets/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from LLM处理 import LLM客户端, 本地拆分
from 知识库IO import 安全名, 原子写入, 拼frontmatter, 拆frontmatter
from 知乎直答 import 知乎直答客户端


工具目录 = Path(__file__).resolve().parent
项目根 = 工具目录.parent
笔记库 = 项目根 / "笔记库"
原始库 = 笔记库 / ".raw" / "sources"
收件箱 = 笔记库 / ".raw" / "inbox"
文章库 = 笔记库 / "wiki" / "articles"
SCHEMA = "zhihu-kb/article-v1"


def 日志(message: str) -> None:
    print(message, flush=True)


def 建引擎(选择: str, env_file: str, 强制本地: bool):
    """Pick a splitting engine; `None` means the deterministic local rules.

    `auto` prefers 知乎直答 (this project's own platform credential), then any
    OpenAI-compatible endpoint, then local rules. Both drop-in clients expose the
    same `可用` / `分析文章` surface, so nothing downstream needs to care.
    """
    if 强制本地 or 选择 == "本地":
        return None, "本地规则（--本地）"
    if 选择 in ("auto", "直答"):
        client = 知乎直答客户端(env_file)
        if client.可用:
            return client, f"知乎直答（{client.配置.model}）"
        if 选择 == "直答":
            return None, "本地规则（直答不可用：缺少 ZHIHU_ACCESS_SECRET）"
    if 选择 in ("auto", "openai"):
        client = LLM客户端(env_file)
        if client.可用:
            return client, f"OpenAI 兼容（{client.配置.model}）"
    return None, "本地规则（没有可用的模型凭证）"


def 原文摘要(body: str, length: int = 180) -> str:
    # Cached/offline summaries may end inside an image target after truncation.
    text = re.sub(r"!\[[^\]\r\n]*\]\([^\r\n)]*(?:\)|$)", "", body, flags=re.M)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"<(https?://[^<>\s]+)>", r"\1", text)
    text = re.sub(r"\[![\w-]+\]", "", text)
    text = re.sub(r"[#>*`_|-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:length]


def 相对原始路径(collection: str, filename: str) -> str:
    return f".raw/sources/{安全名(collection, 48)}/{filename}"


def 缓存分析(analysis: dict[str, Any], content_hash: str) -> dict[str, Any]:
    return {
        "source_sha256": content_hash,
        "schema": analysis.get("schema"),
        "summary": analysis.get("摘要"),
        "article_tags": analysis.get("标签"),
        "sections": [{
            "title": section["标题"],
            "markdown": section["正文"],
            "tags": section["标签"],
            "key_points": section.get("要点", []),
        } for section in analysis.get("sections", [])],
        "processing": analysis.get("模式"),
        "model": analysis.get("模型"),
        "engine": analysis.get("引擎"),
        "prompt_version": analysis.get("提示词版本"),
    }


def 读取缓存分析(path: Path, content_hash: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("source_sha256") != content_hash:
            return None
        if not isinstance(payload.get("sections"), list) or not payload["sections"]:
            return None
        return {
            "schema": payload.get("schema"),
            "摘要": payload.get("summary") or "",
            "标签": payload.get("article_tags") or [],
            "sections": [{
                "标题": section["title"],
                "正文": section["markdown"],
                "标签": section.get("tags") or [],
                "要点": section.get("key_points") or [],
            } for section in payload.get("sections", [])],
            "模式": payload.get("processing") or "cache",
            "模型": payload.get("model"),
            "引擎": payload.get("engine"),
            "提示词版本": payload.get("prompt_version"),
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def 发现原始文章(include_archived: bool = False) -> list[tuple[Path, str]]:
    """Find legacy single notes plus newly captured inbox notes."""
    result: list[tuple[Path, str]] = []
    if not 笔记库.exists():
        return result

    ignored = {".raw", "wiki", "assets", ".obsidian", "__pycache__"}
    for directory in sorted(p for p in 笔记库.iterdir() if p.is_dir() and p.name not in ignored):
        for path in sorted(directory.glob("*.md")):
            if path.name.startswith("_") or path.name.startswith("."):
                continue
            result.append((path, directory.name))

    if 收件箱.exists():
        for path in sorted(收件箱.rglob("*.md")):
            if path.name.startswith("_"):
                continue
            collection = path.parent.name
            if collection == "inbox":
                collection = "未分类"
            result.append((path, collection))
    if include_archived and 原始库.exists():
        for path in sorted(原始库.rglob("*.md")):
            if path.name.startswith("_"):
                continue
            result.append((path, path.parent.name))
    return result


def 迁移图片(markdown: str, source_dir: Path, article_dir: Path) -> str:
    """Copy referenced local assets into the article folder and rewrite links."""
    pattern = re.compile(r"(!\[[^\]]*\]\()([^)\s]+(?:%20|\+)[^)]*|[^)\s]+)(\))")

    def replace(match: re.Match[str]) -> str:
        prefix, raw_path, suffix = match.groups()
        path = raw_path.strip("<>")
        if re.match(r"^[a-z]+://", path, re.I):
            return match.group(0)
        source = Path(path)
        if not source.is_absolute():
            source = source_dir / source
        source = source.resolve()
        if not source.exists() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            return match.group(0)
        relative_old = source.parent.name
        destination_dir = article_dir / "assets" / 安全名(relative_old, 48)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)
        relative = os.path.relpath(destination, article_dir).replace("\\", "/")
        return f"{prefix}{relative}{suffix}"

    return pattern.sub(replace, markdown)


def 原始资源目录(source_path: Path, collection: str) -> Path:
    """Archived source notes keep image links relative to the legacy folder."""
    if ".raw" in source_path.parts:
        if (source_path.parent / "assets").is_dir():
            return source_path.parent
        return 笔记库 / 安全名(collection, 48)
    return source_path.parent


def 输出文章(source_path: Path, collection: str, analysis: dict[str, Any], content_hash: str) -> Path:
    text = source_path.read_text(encoding="utf-8")
    fm, body = 拆frontmatter(text)
    标题 = str(fm.get("标题") or source_path.stem)
    article_dir = 文章库 / 安全名(collection, 48) / 安全名(标题)
    previous_times: dict[str, str] = {}
    if article_dir.exists():
        # Force rebuilds must remove stale section files whose titles changed.
        if not article_dir.resolve().is_relative_to(文章库.resolve()):
            raise RuntimeError(f"unsafe article directory: {article_dir}")
        for old_path in article_dir.glob("*.md"):
            old_fm, _ = 拆frontmatter(old_path.read_text(encoding="utf-8"))
            old_title = str(old_fm.get("title") or "")
            old_time = str(old_fm.get("created_at") or "")
            if old_title and old_time:
                previous_times[old_title] = old_time
        shutil.rmtree(article_dir)
    article_dir.mkdir(parents=True, exist_ok=True)

    section_records: list[dict[str, Any]] = []
    ordered = analysis["sections"]
    for index, section in enumerate(ordered):
        section_title = str(section["标题"])
        unique_title = f"{标题} · {section_title}"
        filename = f"{index + 1:02d} {安全名(section_title, 52)}.md"
        section_path = article_dir / filename
        markdown = 迁移图片(str(section["正文"]), 原始资源目录(source_path, collection), article_dir)

        nav = ["## 本文脉络", ""]
        if index > 0:
            nav.append(f"- 上一节：[[{标题} · {ordered[index - 1]['标题']}]]")
        nav.append(f"- 返回总览：[[{标题} MOC]]")
        if index < len(ordered) - 1:
            nav.append(f"- 下一节：[[{标题} · {ordered[index + 1]['标题']}]]")

        front = {
            "schema": SCHEMA,
            "type": "section",
            "title": unique_title,
            "article": 标题,
            "collection": collection,
            "order": index + 1,
            "source": str(fm.get("原链接") or ""),
            "source_ref": 相对原始路径(collection, source_path.name),
            "tags": section["标签"],
            "key_points": section.get("要点") or [],
            "created_at": previous_times.get(unique_title) or datetime.now().isoformat(timespec="seconds"),
            "processing": analysis["模式"],
        }
        原子写入(section_path, 拼frontmatter(front, markdown + "\n\n" + "\n".join(nav)))
        section_records.append({
            "title": unique_title,
            "file": filename,
            "tags": section["标签"],
            "summary": 原文摘要(markdown, 120),
        })

    article_tags = analysis.get("标签") or []
    summary = 原文摘要(str(analysis.get("摘要") or ""), 1200) or 原文摘要(body)
    moc_lines = [
        f"# {标题} MOC",
        "",
        f"> {summary}",
        "",
        "## 阅读线",
        "",
    ]
    for record in section_records:
        moc_lines.append(f"- [[{record['title']}|{record['title'].split(' · ', 1)[-1]}]] — {record['summary']}")
    moc_lines.extend(["", "## 标签网络", ""])
    moc_lines.append(" ".join(f"#{tag}" for tag in article_tags))
    moc_lines.extend(["", "## 来源", ""])
    moc_lines.append(f"- 原文：{fm.get('原链接') or '未记录'}")
    moc_lines.append(f"- 作者：{fm.get('作者') or '未知'}")
    moc_lines.append(f"- 收藏夹：{collection}")

    moc_front = {
        "schema": SCHEMA,
        "type": "article",
        "title": f"{标题} MOC",
        "article": 标题,
        "collection": collection,
        "source": str(fm.get("原链接") or ""),
        "author": str(fm.get("作者") or ""),
        "source_type": str(fm.get("类型") or ""),
        "tags": article_tags,
        "sections": [record["title"] for record in section_records],
        "created_at": previous_times.get(f"{标题} MOC") or datetime.now().isoformat(timespec="seconds"),
        "processing": analysis["模式"],
    }
    原子写入(article_dir / "00 MOC.md", 拼frontmatter(moc_front, "\n".join(moc_lines)))
    原子写入(article_dir / "_meta.json", json.dumps({
        "schema": SCHEMA,
        "title": 标题,
        "collection": collection,
        "sections": section_records,
        "processing": analysis["模式"],
        "model": analysis.get("模型"),
        "engine": analysis.get("引擎"),
    }, ensure_ascii=False, separators=(",", ":")))
    原子写入(article_dir / "_analysis.json", json.dumps(
        缓存分析(analysis, content_hash), ensure_ascii=False, separators=(",", ":")
    ))
    return article_dir


def 归档原文(source_path: Path, collection: str, content_hash: str) -> Path:
    destination = 原始库 / 安全名(collection, 48) / source_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    same_file = destination.resolve() == source_path.resolve()
    if not same_file and not destination.exists():
        shutil.copy2(source_path, destination)
    raw_text = source_path.read_text(encoding="utf-8")
    fm, body = 拆frontmatter(raw_text)
    body = 迁移图片(body, 原始资源目录(source_path, collection), destination.parent)
    fm["source_sha256"] = content_hash
    fm["knowledge_schema"] = SCHEMA
    fm["archived_at"] = fm.get("archived_at") or datetime.now().isoformat(timespec="seconds")
    原子写入(destination, 拼frontmatter(fm, body))
    if not same_file:
        source_path.unlink()
    return destination


def 主函数() -> None:
    parser = argparse.ArgumentParser(description="用 LLM 把知乎文章重构成文章文件夹知识库")
    parser.add_argument("--env-file", default="", help="可选：加载 ZHIHU_* / OPENAI_* 配置的 env 文件路径")
    parser.add_argument("--引擎", default="auto", choices=["auto", "直答", "openai", "本地"],
                        help="拆分引擎：auto 优先知乎直答，其次 OpenAI 兼容，最后本地规则")
    parser.add_argument("--本地", action="store_true", help="强制使用本地规则拆分")
    parser.add_argument("--强制", action="store_true", help="原文未变化时也重建")
    parser.add_argument("--重新分析", action="store_true", help="忽略 LLM 结果缓存，重新调用模型/本地拆分")
    parser.add_argument("--包含归档", action="store_true", help="重建 .raw/sources 中已归档文章")
    parser.add_argument("--限制", type=int, default=0, help="最多处理 N 篇，0 表示全部")
    parser.add_argument("--间隔", type=float, default=0.0, help="每篇之间的等待秒数，避免触发平台限流")
    parser.add_argument("--严格", action="store_true",
                        help="模型调用失败降级时跳过该篇不落盘，留待额度恢复后用同一条命令重跑")
    args = parser.parse_args()

    sources = 发现原始文章(args.包含归档)
    if not sources:
        日志("没有待重构的原始文章。")
        return

    client, 模式 = 建引擎(args.引擎, args.env_file, args.本地)
    日志(f"发现 {len(sources)} 篇原始文章；处理模式：{模式}")

    文章库.mkdir(parents=True, exist_ok=True)
    成功 = 0
    待重试 = 0
    for 序号, (path, collection) in enumerate(sources, 1):
        if args.限制 and 成功 >= args.限制:
            break
        text = path.read_text(encoding="utf-8")
        fm, body = 拆frontmatter(text)
        标题 = str(fm.get("标题") or path.stem)
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        article_dir = 文章库 / 安全名(collection, 48) / 安全名(标题)
        analysis_path = article_dir / "_analysis.json"
        cached_analysis = None if args.重新分析 else 读取缓存分析(analysis_path, content_hash)
        if not args.强制 and cached_analysis and (article_dir / "00 MOC.md").exists():
            日志(f"  [{序号}/{len(sources)}] 跳过（未变化）：{标题}")
            continue

        try:
            日志(f"  [{序号}/{len(sources)}] 重构：{标题}")
            调用了模型 = False
            if cached_analysis:
                analysis = cached_analysis
            elif client and client.可用:
                调用了模型 = True
                analysis = client.分析文章(标题, body, fm.get("标签") or [])
            else:
                analysis = 本地拆分(标题, body, fm.get("标签") or [])
            if args.严格 and 调用了模型 and analysis.get("模式") != "llm":
                # Never cache a degraded result: leave the source in place so the
                # next run retries it once the platform quota recovers.
                待重试 += 1
                日志(f"    跳过（模型未成功，--严格 不落盘）：{analysis.get('降级原因') or '未知原因'}")
                if args.间隔:
                    time.sleep(args.间隔)
                continue
            output = 输出文章(path, collection, analysis, content_hash)
            归档原文(path, collection, content_hash)
            成功 += 1
            日志(f"    完成：{output.relative_to(笔记库)}")
        except Exception as exc:
            日志(f"    失败：{exc}")
        if args.间隔 and client is not None and client.可用:
            time.sleep(args.间隔)

    日志(f"重构完成：{成功} 篇。原文已归档到 {原始库}")
    if 待重试:
        日志(f"有 {待重试} 篇因模型未成功而未落盘，等额度恢复后用同一条命令重跑即可。")


if __name__ == "__main__":
    主函数()
