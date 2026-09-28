# -*- coding: utf-8 -*-
"""Local read-only API and static server for the knowledge vault."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


工具目录 = os.path.dirname(os.path.abspath(__file__))
项目根 = os.path.dirname(工具目录)
笔记库 = os.path.join(项目根, "笔记库")
网页目录 = os.path.join(项目根, "网页")
索引文件 = os.path.join(笔记库, "_索引.json")
图谱文件 = os.path.join(笔记库, "_图谱.json")
处理脚本 = os.path.join(工具目录, "处理.py")
重构脚本 = os.path.join(工具目录, "知识重构.py")

# 还没跑过 处理.py（或笔记库刚被清空）时索引文件不存在。
# 这里必须给出结构完整的空索引：前端拿到 {} 会判为"索引格式不正确"，
# 从而误报"无法连接本地知识库"，而实际只是还没有内容。
空索引: dict[str, Any] = {
    "生成时间": "",
    "统计": {"文章数": 0, "笔记数": 0, "收藏夹数": 0, "标签数": 0, "总字数": 0},
    "收藏夹": [],
    "标签": [],
    "文章": [],
    "入口": [],
}

app = FastAPI(title="网页版 Obsidian · 知乎收藏夹", docs_url=None, redoc_url=None)
重扫锁 = threading.Lock()

缓存: dict[str, Any] = {
    "索引": {},
    "搜索文档": [],
    "标题到笔记": {},
    "路径到笔记": {},
    "路径到文章": {},
    "时间": 0.0,
}


def 读JSON(path: str, fallback: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def 去frontmatter(text: str) -> str:
    match = re.match(r"^---\r?\n.*?\r?\n---\r?\n?", text, re.S)
    return text[match.end():] if match else text


def 展平索引(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the compact article tree to note records used by the client."""
    records: list[dict[str, Any]] = []
    for article in index.get("文章", []):
        common = {
            "收藏夹": article.get("收藏夹", ""),
            "作者": article.get("作者", ""),
            "原链接": article.get("原链接", ""),
        }
        records.append({
            **common,
            "标题": article.get("MOC标题", ""), "路径": article.get("路径", ""),
            "文章": article.get("标题", ""), "类型": "MOC", "字数": article.get("字数", 0),
            "标签": article.get("标签", []), "相关": article.get("相关", []),
            "摘要": article.get("摘要", ""), "层级": "文章",
            "分节数": len(article.get("分节", [])), "发布时间": "", "收藏时间": "",
            "赞同数": 0, "评论数": 0, "阅读时长": "",
        })
        for section in article.get("分节", []):
            records.append({
                **common,
                "标题": section.get("标题", ""), "路径": section.get("路径", ""),
                "文章": article.get("标题", ""), "类型": "分节",
                "字数": section.get("字数", 0), "标签": section.get("标签", []),
                "相关": section.get("相关", []), "摘要": section.get("摘要", ""),
                "层级": "分节", "序号": section.get("序号", 0),
                "发布时间": "", "收藏时间": "", "赞同数": 0, "评论数": 0, "阅读时长": "",
            })
    records.extend(index.get("入口", []))
    return records


def 建搜索文档(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for record in records:
        relative = str(record.get("路径") or "")
        try:
            path = 安全路径(relative, wiki_only=True)
        except HTTPException:
            continue
        if os.path.splitext(path)[1].lower() != ".md":
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = 去frontmatter(f.read())
        except (OSError, UnicodeError):
            body = ""
        title = str(record.get("标题") or "")
        tags = [str(tag) for tag in record.get("标签", [])]
        author = str(record.get("作者") or "")
        documents.append({
            "记录": record,
            "正文": body,
            "标题_": title.lower(),
            "标签_": [tag.lower() for tag in tags],
            "作者_": author.lower(),
            "正文_": body.lower(),
        })
    return documents


def 载入索引() -> None:
    index = 读JSON(索引文件, {})
    records = 展平索引(index)
    缓存["索引"] = index
    缓存["搜索文档"] = 建搜索文档(records)
    缓存["标题到笔记"] = {record["标题"]: record for record in records if record.get("标题")}
    缓存["路径到笔记"] = {record["路径"]: record for record in records if record.get("路径")}
    缓存["路径到文章"] = {
        path: article["路径"]
        for article in index.get("文章", [])
        for path in [article.get("路径", ""), *[section.get("路径", "") for section in article.get("分节", [])]]
        if path
    }
    缓存["时间"] = time.time()
    print(f"索引已载入：{index.get('统计', {}).get('笔记数', 0)} 个分节", flush=True)


def 安全路径(relative: str, *, wiki_only: bool = False) -> str:
    clean = relative.replace("\\", "/")
    if not clean or "\x00" in clean or os.path.isabs(clean):
        raise HTTPException(400, "路径不合法")
    # Validate the resolved target, not the prefix before .. or symlink expansion.
    root = os.path.realpath(os.path.join(笔记库, "wiki") if wiki_only else 笔记库)
    try:
        absolute = os.path.realpath(os.path.join(笔记库, clean))
        inside = os.path.normcase(os.path.commonpath([root, absolute])) == os.path.normcase(root)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise HTTPException(400, "路径不合法")
    return absolute


@app.get("/api/索引")
def 取索引():
    if not os.path.isfile(索引文件):
        return JSONResponse(空索引)
    return FileResponse(索引文件, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/api/图谱")
def 取图谱():
    if not os.path.isfile(图谱文件):
        return JSONResponse({"节点": [], "边": []})
    return FileResponse(图谱文件, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/api/笔记")
def 取笔记(路径: str = Query(...)):
    absolute = 安全路径(路径, wiki_only=True)
    if os.path.splitext(absolute)[1].lower() != ".md" or not os.path.isfile(absolute):
        raise HTTPException(404, "笔记不存在")
    with open(absolute, "r", encoding="utf-8") as f:
        text = f.read()
    return {"路径": 路径, "目录": os.path.dirname(路径.replace("\\", "/")), "内容": text}


@app.get("/api/搜索")
def 搜索(q: str = Query(""), 上限: int = Query(30, ge=1, le=100)):
    query = (q or "").strip().lower()
    if not query:
        return {"结果": [], "总数": 0}
    tag_query = query[1:].strip() if query.startswith("#") else None
    terms = [part for part in re.split(r"\s+", query) if part]
    results: list[tuple[int, dict[str, Any]]] = []

    for document in 缓存["搜索文档"]:
        record = document["记录"]
        title_hit = tag_query is None and query in document["标题_"]
        tags_hit = (tag_query in document["标签_"] if tag_query is not None
                    else any(query in tag for tag in document["标签_"]))
        author_hit = tag_query is None and query in document["作者_"]
        body_hit = tag_query is None and query in document["正文_"]
        terms_hit = tag_query is None and all(
            part in document["正文_"] or part in document["标题_"] for part in terms
        )
        if not (title_hit or tags_hit or author_hit or body_hit or terms_hit):
            continue

        body = document["正文"]
        position = document["正文_"].find(query)
        if position < 0 and terms:
            position = document["正文_"].find(terms[0])
        if position >= 0:
            start = max(0, position - 60)
            snippet = body[start:position + 90].replace("\n", " ").strip()
            if start > 0:
                snippet = "…" + snippet
        else:
            snippet = record.get("摘要", "")

        score = 100 * title_hit + 60 * tags_hit + 40 * author_hit + 20 * body_hit
        score += len(re.findall(re.escape(query), document["正文_"])) if query else 0
        results.append((score, {
            "标题": record.get("标题", ""), "路径": record.get("路径", ""),
            "收藏夹": record.get("收藏夹", ""), "文章": record.get("文章", ""),
            "类型": record.get("类型", ""), "作者": record.get("作者", ""),
            "标签": record.get("标签", []), "片段": snippet,
        }))

    results.sort(key=lambda item: (-item[0], item[1]["标题"]))
    return {"结果": [record for _, record in results[:上限]], "总数": len(results)}


@app.post("/api/重扫")
def 重扫():
    if not 重扫锁.acquire(blocking=False):
        raise HTTPException(409, "正在重新扫描，请稍后再试")
    try:
        return 执行重扫()
    finally:
        重扫锁.release()


def 执行重扫():
    command = [sys.executable, 重构脚本]
    env_file = os.environ.get("KNOWLEDGE_ENV_FILE", "").strip()
    if env_file:
        command.extend(["--env-file", env_file])
    commands = [command, [sys.executable, 处理脚本]]
    outputs: list[str] = []
    try:
        for current in commands:
            result = subprocess.run(
                current, cwd=工具目录, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=900, check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            outputs.append((result.stdout or "") + (result.stderr or ""))
            if result.returncode != 0:
                return {"成功": False, "输出": "\n".join(outputs)[-2000:]}
    except Exception as exc:
        return {"成功": False, "输出": f"执行失败：{exc}"}

    载入索引()
    return {
        "成功": True,
        "统计": 缓存["索引"].get("统计", {}),
        "输出": "\n".join(outputs)[-1600:],
    }


@app.get("/api/状态")
def 状态():
    return {
        "就绪": bool(缓存["索引"]),
        "统计": 缓存["索引"].get("统计", {}),
        "载入时间": 缓存["时间"],
        "笔记库": 笔记库,
    }


@app.get("/笔记库/{resource:path}")
def 取资源(resource: str):
    # Markdown and raw evidence must go through the API; only embedded assets are public.
    absolute = 安全路径(resource, wiki_only=True)
    if not os.path.isfile(absolute) or os.path.splitext(absolute)[1].lower() not in {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
    }:
        raise HTTPException(404, "资源不存在")
    return FileResponse(absolute, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


app.mount("/lib", StaticFiles(directory=os.path.join(网页目录, "lib")), name="lib")
app.mount("/", StaticFiles(directory=网页目录, html=True), name="网页")


def 主函数() -> None:
    parser = argparse.ArgumentParser(description="网页版 Obsidian")
    parser.add_argument("--端口", type=int, default=8099)
    parser.add_argument("--不开浏览器", action="store_true")
    args = parser.parse_args()

    载入索引()
    if not 缓存["索引"]:
        print("  笔记库还是空的：python 抓取.py && python 知识重构.py && python 处理.py", flush=True)

    url = f"http://127.0.0.1:{args.端口}"
    print(f"  知识库服务：{url}", flush=True)
    if not args.不开浏览器:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.端口, log_level="warning")


if __name__ == "__main__":
    主函数()
