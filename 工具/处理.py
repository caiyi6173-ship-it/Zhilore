# -*- coding: utf-8 -*-
"""Build section-level links, concept hubs, and the hierarchical web index."""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from 知识库IO import 安全名, 原子写入, 拼frontmatter, 拆frontmatter, 写JSON若变化


工具目录 = Path(__file__).resolve().parent
项目根 = 工具目录.parent
笔记库 = 项目根 / "笔记库"
文章库 = 笔记库 / "wiki" / "articles"
概念库 = 笔记库 / "wiki" / "concepts"
集合库 = 笔记库 / "wiki" / "collections"
索引路径 = 笔记库 / "_索引.json"
图谱路径 = 笔记库 / "_图谱.json"
相关上限 = 6


def 日志(message: str) -> None:
    print(message, flush=True)


def 相对路径(path: Path) -> str:
    return path.relative_to(笔记库).as_posix()


def 摘要(body: str, length: int = 150) -> str:
    body = re.sub(r"^>\s*\[!info\][\s\S]*?(?=\n\n)", "", body.strip())
    body = 语义正文(body)
    text = re.sub(r"```[\s\S]*?```", " ", body)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
    text = re.sub(r"<https?://[^>]+>|https?://\S+", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*`_|-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:length]


def 语义正文(body: str) -> str:
    """Exclude generated navigation and links from readability statistics."""
    return re.split(r"\n(?:<!--AUTO:RELATED-->\s*)?## (?:本文脉络|跨文章关联)\s*\n", body, maxsplit=1)[0]


def 清标签(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values else []
    result, seen = [], set()
    for value in values:
        tag = str(value or "").strip().lstrip("#")
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


def 读取分节() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(文章库.glob("*/*/*.md")):
        if path.name == "_meta.json" or path.name.startswith("00 "):
            continue
        fm, body = 拆frontmatter(path.read_text(encoding="utf-8"))
        if fm.get("type") != "section":
            continue
        records.append({
            "路径": path,
            "相对": 相对路径(path),
            "fm": fm,
            "body": body,
            "标题": str(fm.get("title") or path.stem),
            "文章": str(fm.get("article") or path.parent.name),
            "文章目录": path.parent,
            "收藏夹": str(fm.get("collection") or path.parent.parent.name),
            "标签": 清标签(fm.get("tags")),
            "序号": int(fm.get("order") or 0),
            "字数": len(re.sub(r"\s", "", 语义正文(body))),
            "摘要": 摘要(body),
        })
    return records


def 交集权重(left: list[str], right: list[str]) -> int:
    return len({x.lower() for x in left} & {x.lower() for x in right})


def 计算分节关联(records: list[dict[str, Any]]) -> None:
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for other in records:
        for tag in other["标签"]:
            by_tag[tag.lower()].append(other)

    for record in records:
        candidates: dict[str, tuple[int, dict[str, Any]]] = {}
        record_tags = {tag.lower() for tag in record["标签"]}
        for tag in record_tags:
            for other in by_tag.get(tag, ()):
                if other["文章目录"] == record["文章目录"]:
                    continue
                score = len(record_tags & {item.lower() for item in other["标签"]}) * 4
                previous = candidates.get(other["标题"])
                if previous is None or score > previous[0]:
                    candidates[other["标题"]] = (score, other)
        scored = [(score, title, other) for title, (score, other) in candidates.items()]
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected: list[tuple[int, str, dict[str, Any]]] = []
        per_article: Counter[str] = Counter()
        for item in scored:
            article_name = item[2]["文章"]
            if per_article[article_name] >= 2:
                continue
            per_article[article_name] += 1
            selected.append(item)
            if len(selected) >= 相关上限:
                break
        record["相关"] = [title for _, title, _ in selected]
        record["相关记录"] = [item[2] for item in selected]


def 去掉自动关联(body: str) -> str:
    return re.sub(
        r"\n*(?:<!--AUTO:RELATED-->\s*)?## 跨文章关联[\s\S]*?(?=\n## |\Z)",
        "",
        body,
    ).rstrip() + "\n"


def 写回分节(records: list[dict[str, Any]], dry_run: bool) -> int:
    changed = 0
    for record in records:
        fm = dict(record["fm"])
        fm["tags"] = record["标签"]
        fm["related"] = record["相关"]
        body = 去掉自动关联(record["body"])
        if record["相关"]:
            lines = ["", "<!--AUTO:RELATED-->", "", "## 跨文章关联", ""]
            for other in record["相关记录"]:
                shared = "、".join(sorted(set(record["标签"]) & set(other["标签"])))
                reason = f"共享标签：{shared}" if shared else "相关线索"
                lines.append(f"- [[{other['标题']}]]（{reason}）")
            body += "\n".join(lines) + "\n"
        content = 拼frontmatter(fm, body)
        record["body"] = body
        if not dry_run and 原子写入(record["路径"], content):
            changed += 1
        elif dry_run and content != record["路径"].read_text(encoding="utf-8"):
            changed += 1
    return changed


def 聚合文章(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["文章目录"]].append(record)

    articles: list[dict[str, Any]] = []
    for directory, sections in grouped.items():
        sections.sort(key=lambda x: x["序号"])
        moc_path = directory / "00 MOC.md"
        fm, body = 拆frontmatter(moc_path.read_text(encoding="utf-8")) if moc_path.exists() else ({}, "")
        first = sections[0]
        tags = 清标签(fm.get("tags")) or sorted({tag for section in sections for tag in section["标签"]})
        article = {
            "标题": str(fm.get("article") or first["文章"]),
            "MOC标题": str(fm.get("title") or f"{first['文章']} MOC"),
            "路径": 相对路径(moc_path) if moc_path.exists() else "",
            "目录": directory,
            "收藏夹": first["收藏夹"],
            "作者": str(fm.get("author") or ""),
            "类型": str(fm.get("source_type") or ""),
            "原链接": str(fm.get("source") or ""),
            "标签": tags,
            "字数": sum(section["字数"] for section in sections),
            "分节": sections,
            "相关": [],
            "摘要": sections[0]["摘要"] if sections else 摘要(body, 220),
            "fm": fm,
            "body": body,
            "处理方式": str(fm.get("processing") or ""),
        }
        articles.append(article)
    for article in articles:
        related_articles = []
        for other in articles:
            if other is article:
                continue
            score = 交集权重(article["标签"], other["标签"])
            if score:
                related_articles.append((score, other["标题"], other))
        related_articles.sort(key=lambda item: (-item[0], item[1]))
        article["相关"] = [item[1] for item in related_articles[:相关上限]]
    return sorted(articles, key=lambda x: (x["收藏夹"], x["标题"]))


def 写回文章MOC(articles: list[dict[str, Any]], dry_run: bool) -> None:
    if dry_run:
        return
    by_title = {article["标题"]: article for article in articles}
    for article in articles:
        path = article["目录"] / "00 MOC.md"
        if not path.exists():
            continue
        fm, body = 拆frontmatter(path.read_text(encoding="utf-8"))
        fm["tags"] = article["标签"]
        fm["related_articles"] = article["相关"]
        body = re.sub(r"\n*## 跨文章关联[\s\S]*?(?=\n## |\Z)", "", body).rstrip()
        if article["相关"]:
            related_lines = ["", "## 跨文章关联", ""]
            for other_title in article["相关"]:
                other = by_title.get(other_title)
                if not other:
                    continue
                shared = "、".join(sorted(set(article["标签"]) & set(other["标签"])))
                related_lines.append(f"- [[{other['MOC标题']}]]（共享标签：{shared}）")
            body += "\n" + "\n".join(related_lines)
        原子写入(path, 拼frontmatter(fm, body))


def 生成概念页(records: list[dict[str, Any]], dry_run: bool) -> int:
    if dry_run:
        return 0
    概念库.mkdir(parents=True, exist_ok=True)
    tag_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for tag in record["标签"]:
            tag_records[tag].append(record)

    shared_tags = {
        tag for tag, items in tag_records.items()
        if len({record["文章"] for record in items}) >= 2
    }
    desired = set()
    for tag, items in sorted(tag_records.items(), key=lambda item: (-len(item[1]), item[0])):
        if tag not in shared_tags:
            continue
        title = f"标签：{tag}"
        path = 概念库 / f"{安全名(title)}.md"
        desired.add(path.stem)
        lines = [
            f"# {title}",
            "",
            f"这个概念在 {len(items)} 个文章分节中出现。相同标签的分节即使来自不同收藏夹，也在这里汇成一条检索线。",
            "",
            "## 出现位置",
            "",
        ]
        for item in sorted(items, key=lambda x: (x["收藏夹"], x["文章"], x["序号"])):
            lines.append(f"- [[{item['标题']}]] · {item['收藏夹']} / {item['文章']}")
        related_tags = Counter(other for item in items for other in item["标签"] if other != tag)
        if related_tags:
            lines.extend(["", "## 共现标签", ""])
            lines.append(" ".join(
                f"[[标签：{name}]]" if name in shared_tags else f"#{name}"
                for name, _ in related_tags.most_common(12)
            ))
        previous, _ = 拆frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
        front = {
            "schema": "zhihu-kb/concept-v1",
            "type": "concept",
            "title": title,
            "status": "developing",
            "tags": [tag],
            "created_at": previous.get("created_at") or datetime.now().isoformat(timespec="seconds"),
        }
        原子写入(path, 拼frontmatter(front, "\n".join(lines)))
    for stale in 概念库.glob("*.md"):
        if str(stale.stem) not in desired:
            stale.unlink()
    return len(shared_tags)


def 生成集合页(articles: list[dict[str, Any]], dry_run: bool) -> None:
    if dry_run:
        return
    集合库.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        grouped[article["收藏夹"]].append(article)
    for collection, items in sorted(grouped.items()):
        lines = [
            f"# {collection} MOC",
            "",
            f"{len(items)} 篇文章，按文章文件夹组织；每篇文章内部由 MOC 和分节文件形成阅读线。",
            "",
        ]
        for article in sorted(items, key=lambda x: x["标题"]):
            lines.append(f"## [[{article['MOC标题']}|{article['标题']}]]")
            lines.append("")
            lines.append(f"- 标签：{'、'.join(article['标签']) or '无'}")
            lines.append(f"- 分节数：{len(article['分节'])}")
            for section in article["分节"]:
                lines.append(f"  - [[{section['标题']}]]")
            lines.append("")
        path = 集合库 / f"{安全名(collection)} MOC.md"
        previous, _ = 拆frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
        front = {
            "schema": "zhihu-kb/collection-v1",
            "type": "collection",
            "title": f"{collection} MOC",
            "tags": sorted({tag for item in items for tag in item["标签"]}),
            "created_at": previous.get("created_at") or datetime.now().isoformat(timespec="seconds"),
        }
        原子写入(path, 拼frontmatter(front, "\n".join(lines)))
    desired = {安全名(f"{collection} MOC") for collection in grouped}
    for stale in 集合库.glob("*.md"):
        if str(stale.stem) not in desired:
            stale.unlink()


def 生成根入口(articles: list[dict[str, Any]], records: list[dict[str, Any]], dry_run: bool) -> None:
    if dry_run:
        return
    wiki = 笔记库 / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    collections = sorted({article["收藏夹"] for article in articles})
    lines = [
        "# Wiki Index",
        "",
        "## Start Here",
        "",
        *[f"- [[{collection} MOC]]" for collection in collections],
        "",
        "## 概念入口",
        "",
        *[f"- [[标签：{tag}]]" for tag in sorted(tag for tag in {record_tag for record in records for record_tag in record["标签"]} if (概念库 / f"{安全名(f'标签：{tag}')}.md").exists())[:40]],
        "",
        "## 文章",
        "",
        *[f"- [[{article['MOC标题']}]]" for article in articles],
        "",
        "## Layers",
        "",
        "1. `.raw/sources/`: immutable source evidence",
        "2. `wiki/articles/<collection>/<article>/`: article folders and section files",
        "3. `wiki/concepts/`: cross-article tag hubs",
        "4. `wiki/collections/`: collection navigation",
    ]
    front = {
        "type": "meta",
        "title": "Wiki Index",
        "status": "evergreen",
        "tags": ["meta", "index"],
    }
    path = wiki / "index.md"
    body = "\n".join(lines)
    previous, previous_body = 拆frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
    unchanged = (previous_body.strip() == body.strip()
                 and {key: value for key, value in previous.items() if key != "updated"} == front)
    front["updated"] = previous.get("updated") if unchanged else datetime.now().strftime("%Y-%m-%d")
    原子写入(path, 拼frontmatter(front, body))


def 生成索引(records: list[dict[str, Any]], articles: list[dict[str, Any]]) -> None:
    tag_counts = Counter(tag for record in records for tag in record["标签"])
    collection_counts = Counter(article["收藏夹"] for article in articles)

    # Concept/collection/index pages are searchable and clickable wiki targets.
    auxiliary_paths = list(概念库.glob("*.md")) + list(集合库.glob("*.md"))
    root_index = 笔记库 / "wiki" / "index.md"
    if root_index.exists():
        auxiliary_paths.append(root_index)
    entries = []
    for path in sorted(auxiliary_paths):
        fm, body = 拆frontmatter(path.read_text(encoding="utf-8"))
        title = str(fm.get("title") or path.stem)
        note_type = str(fm.get("type") or "入口")
        entries.append({
            "标题": title, "路径": 相对路径(path), "文章": "",
            "收藏夹": "概念" if path.parent == 概念库 else "导航",
            "类型": "概念" if note_type == "concept" else "导航",
            "作者": "", "原链接": "", "发布时间": "", "收藏时间": "",
            "赞同数": 0, "评论数": 0, "字数": len(re.sub(r"\s", "", body)),
            "阅读时长": "", "标签": 清标签(fm.get("tags")), "相关": [],
            "摘要": 摘要(body, 220), "层级": "入口",
        })

    article_json = []
    for article in articles:
        article_json.append({
            "标题": article["标题"], "MOC标题": article["MOC标题"], "路径": article["路径"],
            "收藏夹": article["收藏夹"], "作者": article["作者"], "类型": article["类型"],
            "原链接": article["原链接"], "字数": article["字数"], "标签": article["标签"],
            "相关": article["相关"], "摘要": article.get("摘要") or 摘要(article["body"], 220),
            "分节": [{
                "标题": section["标题"], "路径": section["相对"], "标签": section["标签"],
                "摘要": section["摘要"], "序号": section["序号"], "字数": section["字数"],
                "相关": section["相关"],
            } for section in article["分节"]],
        })

    graph_nodes: list[dict[str, Any]] = []
    graph_edges = []
    article_by_title = {article["标题"]: article for article in articles}
    for article in articles:
        graph_nodes.append({"id": article["路径"], "标题": article["标题"], "类型": "文章", "字数": article["字数"]})
        for section in article["分节"]:
            graph_nodes.append({"id": section["相对"], "标题": section["标题"], "类型": "分节", "字数": section["字数"]})
            graph_edges.append({"源": article["路径"], "目标": section["相对"], "类型": "文章线"})
            for tag in section["标签"]:
                graph_edges.append({"源": section["相对"], "目标": f"tag:{tag}", "类型": "标签"})
        for target in article["相关"]:
            target_article = article_by_title.get(target)
            if target_article:
                graph_edges.append({"源": article["路径"], "目标": target_article["路径"], "类型": "相关"})
    for tag, count in tag_counts.most_common():
        graph_nodes.append({"id": f"tag:{tag}", "标题": tag, "类型": "标签", "数量": count})
        concept_path = 概念库 / f"{安全名(f'标签：{tag}')}.md"
        if concept_path.exists():
            graph_nodes.append({"id": 相对路径(concept_path), "标题": f"标签：{tag}", "类型": "概念", "字数": 0})
            graph_edges.append({"源": f"tag:{tag}", "目标": 相对路径(concept_path), "类型": "概念页"})

    output = {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "统计": {
            "文章数": len(articles),
            "笔记数": len(records),
            "收藏夹数": len(collection_counts),
            "标签数": len(tag_counts),
            "总字数": sum(article["字数"] for article in articles),
        },
        "收藏夹": [
            {"名称": name, "数量": count, "字数": sum(x["字数"] for x in articles if x["收藏夹"] == name)}
            for name, count in collection_counts.most_common()
        ],
        "标签": [{"名称": tag, "数量": count} for tag, count in tag_counts.most_common()],
        "文章": article_json,
        "入口": entries,
    }
    写JSON若变化(索引路径, output)
    写JSON若变化(图谱路径, {
        "生成时间": output["生成时间"],
        "节点": graph_nodes,
        "边": graph_edges,
    })


def 主流程(dry_run: bool = False) -> dict[str, Any]:
    records = 读取分节()
    计算分节关联(records)
    写回分节(records, dry_run)
    articles = 聚合文章(records)
    写回文章MOC(articles, dry_run)
    生成概念页(records, dry_run)
    生成集合页(articles, dry_run)
    生成根入口(articles, records, dry_run)
    if not dry_run:
        生成索引(records, articles)
    return {
        "文章数": len(articles),
        "笔记数": len(records),
        "标签数": len({tag for record in records for tag in record["标签"]}),
        "总字数": sum(article["字数"] for article in articles),
    }


def 主函数() -> None:
    parser = argparse.ArgumentParser(description="生成文章/分节/标签关联索引")
    parser.add_argument("--不写回", action="store_true", help="只统计，不修改笔记、索引或图谱")
    args = parser.parse_args()
    stats = 主流程(args.不写回)
    if not stats["笔记数"]:
        日志("没有分节笔记。先运行：python 知识重构.py")
        return
    日志("")
    日志("=" * 46)
    for key, value in stats.items():
        日志(f"  {key:<8}{value}")
    日志("=" * 46)
    日志("只读检查完成：未写入笔记、索引或图谱" if args.不写回 else f"索引已写入：{索引路径}")


if __name__ == "__main__":
    主函数()
