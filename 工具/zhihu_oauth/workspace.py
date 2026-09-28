"""Per-identity summary snapshots. Never opens or imports the author's shared vault."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .collection_data import CREATION_SCOPE_NOTE, content_link
from .errors import OAuthError

WORKSPACE_LIMIT = 2000
BLOCK_LIMIT = 600
DEFAULT_DB = Path(__file__).resolve().parents[2] / "数据" / "用户工作区.sqlite3"
SUMMARY_NOTICE = "这是公开收藏内容的摘要快照，不是全文，也未经过大模型拆分。原文和最新状态请到知乎查看。"
BLOCK_NOTICE = "这是知乎直答按主题归并的知识块，来源是公开收藏摘要（不是全文），原文与最新状态请到知乎查看。"
SNAPSHOT_FIELDS = ("id", "type", "url", "title", "summary", "author", "created_at", "collected_at",
                   "like_count", "comment_count", "favorite_count")
BLOCK_FIELDS = ("title", "markdown", "summary", "tags", "key_points", "sources", "model")
MAX_BLOCK_MARKDOWN = 20000


class WorkspaceRepository:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("ZHIHU_WORKSPACE_DB") or DEFAULT_DB).resolve()

    @contextmanager
    def connection(self):
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("""CREATE TABLE IF NOT EXISTS summaries (
                uid TEXT NOT NULL, path TEXT NOT NULL, collection_id TEXT NOT NULL,
                collection_title TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (uid, path))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS blocks (
                uid TEXT NOT NULL, path TEXT NOT NULL, collection_id TEXT NOT NULL,
                collection_title TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (uid, path))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS creations (
                uid TEXT NOT NULL, path TEXT NOT NULL, content_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (uid, path))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS block_fingerprints (
                uid TEXT NOT NULL, collection_id TEXT NOT NULL, item_ids TEXT NOT NULL,
                PRIMARY KEY (uid, collection_id))""")
            with connection:
                yield connection
        except (sqlite3.Error, OSError):
            raise OAuthError("workspace_unavailable", 503) from None
        finally:
            if connection is not None:
                connection.close()

    def import_page(self, subject: str, collection: dict, items: list[dict]) -> dict:
        """Only called with freshly authorized, provider-normalized data, never browser JSON."""
        rows = []
        for item in items:
            identifier, _url = content_link(item["type"], item["url"])
            if identifier != item["id"]:
                raise OAuthError("upstream_protocol_error", 502)
            key = hashlib.sha256((collection["id"] + ":" + identifier).encode("utf-8")).hexdigest()
            path = "summaries/" + key + ".md"  # A database key, not a filesystem path.
            payload = json.dumps({name: item[name] for name in SNAPSHOT_FIELDS}, ensure_ascii=False)
            rows.append((subject, path, collection["id"], collection["title"], payload))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {row["path"] for row in db.execute("SELECT path FROM summaries WHERE uid = ?", (subject,))}
            new = {row[1] for row in rows} - existing
            if len(existing) + len(new) > WORKSPACE_LIMIT:
                raise OAuthError("workspace_limit_reached", 409)
            db.executemany("""INSERT INTO summaries VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uid, path) DO UPDATE SET collection_title = excluded.collection_title,
                payload = excluded.payload""", rows)
        return {"added": len(new), "updated": len({row[1] for row in rows}) - len(new),
                "total": len(existing) + len(new)}

    def replace_blocks(self, subject: str, collection: dict, blocks: list[dict]) -> dict:
        """Swap in one collection's freshly generated knowledge blocks.

        Re-running the pipeline must never accumulate duplicates, so the previous
        blocks of this collection are replaced atomically. Only whitelisted fields
        survive; nothing the model returns is stored verbatim beyond them.
        """
        rows = []
        用过: set[str] = set()
        for index, block in enumerate(blocks):
            title = str(block.get("title") or "").strip()[:120]
            markdown = str(block.get("markdown") or "").strip()[:MAX_BLOCK_MARKDOWN]
            if not title or not markdown:
                continue
            # 同一收藏夹内重名会让列表出现两个一模一样的东西（模型分批产出时常见），
            # 这里加序号区分；同样的输入重跑仍然得到同样的结果，保持幂等。
            基础, 序号 = title, 2
            while title.lower() in 用过:
                title = f"{基础}（{序号}）"[:120]
                序号 += 1
            用过.add(title.lower())
            payload = {
                "title": title,
                "markdown": markdown,
                "summary": str(block.get("summary") or "").strip()[:600],
                "tags": [str(tag).strip()[:40] for tag in (block.get("tags") or []) if str(tag).strip()][:8],
                "key_points": [str(p).strip()[:200] for p in (block.get("key_points") or []) if str(p).strip()][:6],
                "sources": [{"title": str(s.get("title") or "")[:200], "url": str(s.get("url") or "")[:2048]}
                            for s in (block.get("sources") or []) if isinstance(s, dict)][:20],
                "model": str(block.get("model") or "")[:60],
            }
            # 用序号而不是标题做键：不同批次可能产出同名知识块，用标题会互相覆盖。
            # 路径带序号（不是 hash）是为了让列表按收藏夹 + 产出顺序稳定排列。
            path = f"blocks/{collection['id']}-{index:03d}.md"
            rows.append((subject, path, collection["id"], collection["title"],
                         json.dumps(payload, ensure_ascii=False)))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM blocks WHERE uid = ? AND collection_id = ?", (subject, collection["id"]))
            others = db.execute("SELECT COUNT(*) AS n FROM blocks WHERE uid = ?", (subject,)).fetchone()["n"]
            if others + len(rows) > BLOCK_LIMIT:
                raise OAuthError("workspace_limit_reached", 409)
            db.executemany("INSERT OR REPLACE INTO blocks VALUES (?, ?, ?, ?, ?)", rows)
        return {"blocks": len(rows), "replaced_collection": collection["id"]}

    def delete(self, subject: str, *, path: str = "", collection_id: str = "") -> dict:
        """Remove one record by path, or every record of one collection.

        `uid = subject` keeps this scoped to the caller's own rows. Exactly one
        selector must be supplied — an empty selector would wipe the whole
        workspace, so it is rejected rather than guessed at.
        """
        if bool(path) == bool(collection_id):
            raise OAuthError("invalid_delete_request", 400)
        列, 值 = ("path", path) if path else ("collection_id", collection_id)
        统计 = {}
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for 表 in ("summaries", "blocks"):
                统计[表] = db.execute(f"DELETE FROM {表} WHERE uid = ? AND {列} = ?", (subject, 值)).rowcount
            # 创作没有收藏夹，图谱上的「我的创作」标签用固定 id "creations" 整组删除。
            if not path and collection_id == "creations":
                # 知识块已由上面的 blocks 循环按 collection_id 删除，这里只清创作与指纹。
                统计["creations"] = db.execute("DELETE FROM creations WHERE uid = ?", (subject,)).rowcount
                db.execute("DELETE FROM block_fingerprints WHERE uid = ? AND collection_id = ?",
                           (subject, "creations"))
        return {"deleted": 统计["summaries"] + 统计["blocks"],
                "摘要": 统计["summaries"], "知识块": 统计["blocks"],
                "路径": path, "收藏夹": collection_id}

    def import_creations(self, subject: str, items: list[dict]) -> dict:
        """Upsert one page of the user's own creations.

        Creations belong to no collection, so the path is keyed by the content id
        alone. Re-syncing overwrites instead of duplicating — the same contract as
        summaries, because a user may sync the same page twice.
        """
        rows = []
        for item in items:
            identifier, _url = content_link(item["type"], item["url"])
            if identifier != item["id"]:
                raise OAuthError("upstream_protocol_error", 502)
            key = hashlib.sha256(("creations:" + identifier).encode("utf-8")).hexdigest()
            path = "creations/" + key + ".md"  # A database key, not a filesystem path.
            payload = json.dumps({name: item[name] for name in SNAPSHOT_FIELDS}, ensure_ascii=False)
            rows.append((subject, path, str(item["type"]), payload))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {row["path"] for row in db.execute("SELECT path FROM creations WHERE uid = ?", (subject,))}
            new = {row[1] for row in rows} - existing
            if len(existing) + len(new) > WORKSPACE_LIMIT:
                raise OAuthError("workspace_limit_reached", 409)
            db.executemany("""INSERT INTO creations VALUES (?, ?, ?, ?)
                ON CONFLICT(uid, path) DO UPDATE SET payload = excluded.payload""", rows)
        return {"added": len(new), "updated": len({row[1] for row in rows}) - len(new),
                "total": len(existing) + len(new)}

    def creations(self, subject: str) -> list[dict]:
        """The author's own creations, deliberately kept out of `records()`.

        收藏是"别人写的、我收的"，创作是"我写的"。两者共用同一张卡片视图，
        但创作不并入收藏的图谱 / 索引，避免把两种语义织进同一张关系网。
        """
        with self.connection() as db:
            rows = db.execute("SELECT path, content_type, payload FROM creations WHERE uid = ? ORDER BY path",
                              (subject,)).fetchall()
        try:
            return [{"kind": "creation", "path": row["path"], "content_type": row["content_type"],
                     "item": json.loads(row["payload"])} for row in rows]
        except (ValueError, TypeError):
            raise OAuthError("workspace_unavailable", 503) from None

    def append_blocks(self, subject: str, collection: dict, blocks: list[dict]) -> dict:
        """增量入库：老块保留，新块追加。路径序号接着现有块往后排，避免互相覆盖。

        与 `replace_blocks` 的区别：replace 是"整夹重算"的幂等写法，append 是
        增量归并用的——已经归并过的条目不再送直答，新块追加进来即可。

        这里**刻意不按来源去重**：一个条目正常就会产出多个主题块（线上实测一条回答
        能拆出 3 块，每块都引用它），按来源去重会把正当的块误删。防重跑靠指纹
        （`已归并条目`）+ `块的来源网址` 兜底，不靠块级别的内容比对。
        """
        cid = collection["id"]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT path FROM blocks WHERE uid = ? AND collection_id = ?",
                                  (subject, cid)).fetchall()
            others = db.execute("SELECT COUNT(*) AS n FROM blocks WHERE uid = ?", (subject,)).fetchone()["n"]
            # 序号取"现有路径里的最大下标 + 1"，不能用 COUNT(*)——右键删过节点之后
            # COUNT 会变小，接着写就会撞上已有路径，把别人的块覆盖掉。
            下一个序号 = 0
            for row in existing:
                尾巴 = row["path"].rpartition("-")[2].removesuffix(".md")
                if 尾巴.isdigit():
                    下一个序号 = max(下一个序号, int(尾巴) + 1)
            rows: list[tuple] = []
            for block in blocks:
                title = str(block.get("title") or "").strip()[:120]
                markdown = str(block.get("markdown") or "").strip()[:MAX_BLOCK_MARKDOWN]
                if not title or not markdown:
                    continue
                payload = {
                    "title": title,
                    "markdown": markdown,
                    "summary": str(block.get("summary") or "").strip()[:600],
                    "tags": [str(tag).strip()[:40] for tag in (block.get("tags") or []) if str(tag).strip()][:8],
                    "key_points": [str(p).strip()[:200] for p in (block.get("key_points") or []) if str(p).strip()][:6],
                    "sources": [{"title": str(s.get("title") or "")[:200], "url": str(s.get("url") or "")[:2048]}
                                for s in (block.get("sources") or []) if isinstance(s, dict)][:20],
                    "model": str(block.get("model") or "")[:60],
                }
                path = f"blocks/{cid}-{下一个序号:03d}.md"
                下一个序号 += 1
                rows.append((subject, path, cid, collection["title"],
                             json.dumps(payload, ensure_ascii=False)))
            if others + len(rows) > BLOCK_LIMIT:
                raise OAuthError("workspace_limit_reached", 409)
            if rows:
                db.executemany("INSERT OR REPLACE INTO blocks VALUES (?, ?, ?, ?, ?)", rows)
        return {"blocks": len(rows)}

    def 块的来源网址(self, subject: str, collection_id: str) -> set[str]:
        """这个收藏夹里已有知识块引用过的来源网址。

        用途是**老数据兜底**：`block_fingerprints` 是 2026-09-15 才加的，升级前归并过
        的收藏夹只有块、没有指纹行，光看指纹会把老文章再送一遍直答。用块里的来源
        反查一次，就能认出"这些条目其实已经归并过了"，一次调用都不多花。
        """
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM blocks WHERE uid = ? AND collection_id = ?",
                              (subject, collection_id)).fetchall()
        网址: set[str] = set()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (ValueError, TypeError):
                continue
            for source in payload.get("sources") or []:
                url = str(source.get("url") or "").strip()
                if url:
                    网址.add(url)
        return 网址

    def 已归并条目(self, subject: str, collection_id: str) -> set[str]:
        """上次归并时已经用过的条目 id——增量归并靠它跳过老内容。"""
        with self.connection() as db:
            row = db.execute("SELECT item_ids FROM block_fingerprints WHERE uid = ? AND collection_id = ?",
                             (subject, collection_id)).fetchone()
        try:
            return set(json.loads(row["item_ids"])) if row else set()
        except (ValueError, TypeError):
            return set()

    def 记录归并条目(self, subject: str, collection_id: str, item_ids: list[str]) -> None:
        """把「这一批真的归并成功」的条目 id **累加**进指纹。

        必须是并集，不能覆盖：一次拉取拿到的条目集合可能比上次少（列表上限、分页
        未提供、上游波动、用户在知乎取消收藏…），覆盖写会让掉出快照的条目下次又
        被当成新内容重跑一遍——那正是"处理过的文章又被处理一次"。
        """
        新增 = {str(编号) for 编号 in item_ids if str(编号).strip()}
        if not 新增:
            return
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT item_ids FROM block_fingerprints WHERE uid = ? AND collection_id = ?",
                             (subject, collection_id)).fetchone()
            try:
                已有 = set(json.loads(row["item_ids"])) if row else set()
            except (ValueError, TypeError):
                已有 = set()
            db.execute("""INSERT INTO block_fingerprints VALUES (?, ?, ?)
                ON CONFLICT(uid, collection_id) DO UPDATE SET item_ids = excluded.item_ids""",
                       (subject, collection_id, json.dumps(sorted(已有 | 新增))))

    def records(self, subject: str) -> list[dict]:
        with self.connection() as db:
            summaries = db.execute("SELECT path, collection_id, collection_title, payload FROM summaries WHERE uid = ? ORDER BY path", (subject,)).fetchall()
            blocks = db.execute("SELECT path, collection_id, collection_title, payload FROM blocks WHERE uid = ? ORDER BY path", (subject,)).fetchall()
            creations = db.execute("SELECT path, payload FROM creations WHERE uid = ? ORDER BY path", (subject,)).fetchall()
        try:
            def 展开(rows, kind: str) -> list[dict]:
                return [{"kind": kind, "path": row["path"], "collection_id": row["collection_id"],
                         "collection_title": row["collection_title"], "item": json.loads(row["payload"])}
                        for row in rows]
            # 摘要快照在前、直答知识块在后；创作并入同一条流水线（用户要求），
            # 图谱 / 索引 / 检索都能看到，统一归到「我的创作」标签。
            创作 = [{"kind": "creation", "path": row["path"], "collection_id": "creations",
                     "collection_title": "我的创作", "item": json.loads(row["payload"])} for row in creations]
            return 展开(summaries, "summary") + 展开(blocks, "block") + 创作
        except (ValueError, TypeError):
            raise OAuthError("workspace_unavailable", 503) from None


def 行内标注(text: str) -> str:
    """Escape first, then add the two inline marks we support."""
    value = html.escape(text)
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    return value


def markdown转HTML(markdown: str) -> str:
    """Escape-first Markdown subset: headings, lists, paragraphs, bold, inline code.

    Block text comes from a model, so every character is escaped *before* any
    markup is added — the model cannot inject HTML, attributes or links here.
    """
    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = []
    bullets: list[str] = []
    paragraph: list[str] = []

    def 冲段落() -> None:
        if paragraph:
            parts.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    def 冲列表() -> None:
        if bullets:
            parts.append("<ul>" + "".join("<li>" + item + "</li>" for item in bullets) + "</ul>")
            bullets.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            冲列表()
            冲段落()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            冲列表()
            冲段落()
            level = min(6, len(heading.group(1)) + 1)  # h1 stays the page's own heading.
            parts.append(f"<h{level}>" + 行内标注(heading.group(2)) + f"</h{level}>")
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        if bullet:
            冲段落()
            bullets.append(行内标注(bullet.group(1)))
            continue
        冲列表()
        paragraph.append(行内标注(line))
    冲列表()
    冲段落()
    return "\n".join(parts) + "\n"


def safe_source_url(value) -> str:
    """Only absolute http(s) links survive; anything else is dropped, not escaped."""
    url = str(value or "").strip()
    if len(url) > 2048 or any(ord(char) < 32 for char in url):
        return ""
    return url if re.match(r"^https?://[^\s\"'<>]+$", url) else ""


def article_record(record: dict) -> dict:
    item = record["item"]
    folder = (record["collection_title"] or "未命名收藏夹") + " · " + record["collection_id"]
    return {"标题": item["title"] or "未命名公开内容", "MOC标题": item["title"] or "未命名公开内容",
            "路径": record["path"], "收藏夹": folder, "作者": item["author"] or "",
            "原链接": item["url"], "字数": len(item["summary"]), "摘要": item["summary"],
            "标签": [folder], "相关": [], "分节": [], "内容模式": "public_summary"}


def block_record(record: dict) -> dict:
    """A 直答 knowledge block rendered through the very same "article" view."""
    block = record["item"]
    # 创作的知识块（虚拟分组 creations）对外就叫「我的创作」，不带内部 id 后缀。
    folder = ("我的创作" if record["collection_id"] == "creations"
              else (record["collection_title"] or "未命名收藏夹") + " · " + record["collection_id"])
    title = str(block.get("title") or "").strip() or "未命名知识块"
    markdown = str(block.get("markdown") or "")
    sources = [s for s in (block.get("sources") or []) if isinstance(s, dict)]
    tags = [str(tag).strip() for tag in (block.get("tags") or []) if str(tag).strip()]
    first = next((safe_source_url(s.get("url")) for s in sources if safe_source_url(s.get("url"))), "")
    return {"标题": title, "MOC标题": title, "路径": record["path"], "收藏夹": folder, "作者": "",
            "原链接": first, "字数": len(markdown),
            "摘要": str(block.get("summary") or "").strip() or markdown[:180],
            "标签": tags[:8] + [folder], "相关": [], "分节": [],
            "要点": [str(p).strip() for p in (block.get("key_points") or []) if str(p).strip()][:6],
            "内容模式": "zhida_block"}


def creation_record(record: dict) -> dict:
    """把创作渲染成与 article_record 同构的视图，图谱 / 索引 / 检索共用。"""
    item = record["item"]
    标题 = item.get("title") or "未命名创作"
    return {"标题": 标题, "MOC标题": 标题, "路径": record["path"], "收藏夹": "我的创作", "作者": "",
            "原链接": item.get("url") or "", "字数": len(item.get("summary") or ""),
            "摘要": item.get("summary") or "", "标签": ["我的创作"], "相关": [], "分节": [],
            "内容模式": "creation"}


def 记录转文章(record: dict) -> dict:
    if record.get("kind") == "block":
        return block_record(record)
    if record.get("kind") == "creation":
        return creation_record(record)
    return article_record(record)


def workspace_index(records: list[dict]) -> dict:
    articles = [记录转文章(record) for record in records]
    folders = sorted({article["收藏夹"] for article in articles})
    blocks = sum(article["内容模式"] == "zhida_block" for article in articles)
    if blocks and blocks < len(articles):
        说明 = SUMMARY_NOTICE + " " + BLOCK_NOTICE
    elif blocks:
        说明 = BLOCK_NOTICE
    else:
        说明 = SUMMARY_NOTICE
    return {"文章": articles, "入口": [], "收藏夹": [{"名称": folder} for folder in folders],
            "标签": [{"名称": folder, "数量": sum(a["收藏夹"] == folder for a in articles)} for folder in folders],
            "统计": {"文章数": len(articles), "笔记数": blocks, "收藏夹数": len(folders), "标签数": len(folders)},
            "来源模式": "public_summary+zhida_block" if blocks else "public_summary", "说明": 说明}


def workspace_collections(records: list[dict]) -> dict:
    """把已收录内容按收藏夹分组，交给「知乎收藏」页渲染成卡片列表。

    纯读：不联网、不读 `笔记库/`、不需要重新采集——字段全部来自已有的
    `SNAPSHOT_FIELDS` 与知识块 payload。互动数字是**收录当时**的值，只作只读展示
    （开放平台没有写接口，赞同/评论这些做不了），要看最新状态就点「阅读原文」回知乎。
    """
    收藏夹表: dict[str, dict] = {}
    知识块数: dict[str, int] = {}

    def 取收藏夹(record: dict) -> dict:
        return 收藏夹表.setdefault(record["collection_id"], {
            "id": record["collection_id"],
            "名称": record["collection_title"] or "未命名收藏夹",
            "内容数": 0, "知识块数": 0, "最近收录": 0, "内容": [],
        })

    for record in records:
        item = record["item"]
        收藏夹 = 取收藏夹(record)
        if record.get("kind") == "block":
            来源 = [s for s in (item.get("sources") or []) if isinstance(s, dict)]
            收藏夹["知识块数"] += 1
            收藏夹["内容"].append({
                "模式": "知识块", "标题": str(item.get("title") or "").strip() or "未命名知识块",
                "作者": "", "摘要": str(item.get("summary") or "").strip(),
                "原文链接": next((safe_source_url(s.get("url")) for s in 来源 if safe_source_url(s.get("url"))), ""),
                "赞同数": "", "评论数": "", "收藏数": "", "收录时间": 0,
                "标签": [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()][:4],
                "来源数": len(来源),
            })
            continue
        收录时间 = int(item.get("collected_at") or 0)
        收藏夹["内容数"] += 1
        收藏夹["最近收录"] = max(收藏夹["最近收录"], 收录时间)
        收藏夹["内容"].append({
            "模式": "公开摘要", "标题": item.get("title") or "未命名公开内容",
            "作者": item.get("author") or "", "摘要": item.get("summary") or "",
            "原文链接": safe_source_url(item.get("url")),
            "赞同数": str(item.get("like_count") or ""), "评论数": str(item.get("comment_count") or ""),
            "收藏数": str(item.get("favorite_count") or ""), "收录时间": 收录时间,
            "标签": [], "来源数": 0,
        })

    列表 = sorted(收藏夹表.values(), key=lambda 夹: (-夹["最近收录"], 夹["id"]))
    for 夹 in 列表:
        # 收藏夹内最新收录的排前面；知识块没有收录时间，排在末尾但保持稳定顺序。
        夹["内容"].sort(key=lambda 条: (条["模式"] == "知识块", -条["收录时间"], 条["标题"]))
    return {"收藏夹": 列表,
            "统计": {"收藏夹数": len(列表),
                      "内容数": sum(夹["内容数"] for 夹 in 列表),
                      "知识块数": sum(夹["知识块数"] for 夹 in 列表)},
            "说明": "标题与摘要来自收录时的快照，不是全文；互动数字也是当时的值。看原文请点「阅读原文」回知乎。"}


def workspace_creations(records: list[dict]) -> dict:
    """把创作按内容类型分组（回答 / 文章 / 视频 / 想法 / 提问）。

    输出结构刻意与 `workspace_collections` **完全一致**，这样前端可以复用同一套卡片
    渲染，只是侧栏的"收藏夹"在这里换成了"内容类型"。
    """
    类型名 = {"answer": "回答", "article": "文章", "zvideo": "视频", "pin": "想法", "question": "提问"}
    顺序 = ("answer", "article", "zvideo", "pin", "question")
    分组表: dict[str, dict] = {}
    for record in records:
        item = record["item"]
        类型 = str(record.get("content_type") or item.get("type") or "")
        夹 = 分组表.setdefault(类型, {"id": 类型, "名称": 类型名.get(类型, 类型 or "未分类"),
                                    "内容数": 0, "知识块数": 0, "最近收录": 0, "内容": []})
        # 创作没有"收藏时间"，这里存的是创建时间，只用来排序。
        时间 = int(item.get("collected_at") or 0)
        夹["内容数"] += 1
        夹["最近收录"] = max(夹["最近收录"], 时间)
        夹["内容"].append({
            "模式": "创作", "标题": item.get("title") or "未命名创作",
            "作者": item.get("author") or "", "摘要": item.get("summary") or "",
            "原文链接": safe_source_url(item.get("url")),
            "赞同数": str(item.get("like_count") or ""), "评论数": str(item.get("comment_count") or ""),
            "收藏数": str(item.get("favorite_count") or ""), "收录时间": 时间,
            "标签": [], "来源数": 0,
        })
    列表 = sorted(分组表.values(), key=lambda 夹: (顺序.index(夹["id"]) if 夹["id"] in 顺序 else 99, 夹["id"]))
    for 夹 in 列表:
        夹["内容"].sort(key=lambda 条: (-条["收录时间"], 条["标题"]))
    return {"收藏夹": 列表,
            "统计": {"收藏夹数": len(列表), "内容数": sum(夹["内容数"] for 夹 in 列表), "知识块数": 0},
            "说明": CREATION_SCOPE_NOTE}


def workspace_graph(records: list[dict]) -> dict:
    index = workspace_index(records)
    # 节点带上收藏夹 id：「知识库图谱」右键删除整个收藏夹时要靠它定位；
    # 标题里虽然有 " · <id>"，但从展示字符串反解不可靠。
    收藏夹id = {(r["collection_title"] or "未命名收藏夹") + " · " + r["collection_id"]: r["collection_id"]
                for r in records}
    收藏夹id["我的创作"] = "creations"  # 创作标签的整组删除走这个固定 id。
    nodes = [{"id": "tag:" + tag["名称"], "标题": tag["名称"], "类型": "标签", "数量": tag["数量"],
              "收藏夹id": 收藏夹id.get(tag["名称"], "")} for tag in index["标签"]]
    edges = []
    for article in index["文章"]:
        nodes.append({"id": article["路径"], "标题": article["标题"], "路径": article["路径"],
                      "类型": "文章", "收藏夹": article["收藏夹"],
                      "收藏夹id": 收藏夹id.get(article["收藏夹"], "")})
        edges.append({"源": article["路径"], "目标": "tag:" + article["收藏夹"], "类型": "标签"})
    return {"节点": nodes, "边": edges}


def workspace_note(records: list[dict], path: str) -> dict:
    record = next((record for record in records if record["path"] == path), None)
    if record is None:
        raise OAuthError("note_unavailable", 404)
    if record.get("kind") == "block":
        return 知识块笔记(record)
    item = record["item"]
    # Keep untrusted titles and excerpts in raw HTML text nodes, not Markdown syntax.
    # No user-supplied attributes, images, links, or HTML are interpolated.
    summary = html.escape(item["summary"] or "接口未返回摘要，请前往知乎阅读原文。").replace("\r", "").replace("\n", "<br>")
    body = "<blockquote><p>" + SUMMARY_NOTICE + "</p></blockquote>\n\n"
    body += "<h2>公开摘要</h2>\n\n<div class=\"summary-excerpt\">" + summary + "</div>\n"
    return {"路径": path, "目录": "summaries", "内容": body}


def 知识块笔记(record: dict) -> dict:
    block = record["item"]
    body = "<blockquote><p>" + BLOCK_NOTICE + "</p></blockquote>\n\n"
    body += markdown转HTML(block.get("markdown") or "")
    points = [str(p).strip() for p in (block.get("key_points") or []) if str(p).strip()]
    if points:
        body += "\n<h2>要点</h2>\n<ul>" + "".join("<li>" + html.escape(p) + "</li>" for p in points) + "</ul>\n"
    sources = []
    for source in (block.get("sources") or []):
        if not isinstance(source, dict):
            continue
        url = safe_source_url(source.get("url"))
        if url:
            sources.append((url, str(source.get("title") or url)[:200]))
    if sources:
        body += "\n<h2>来源（公开摘要）</h2>\n<ul>" + "".join(
            '<li><a href="' + html.escape(url, quote=True) + '" rel="noopener noreferrer" target="_blank">'
            + html.escape(title) + "</a></li>" for url, title in sources[:20]) + "</ul>\n"
    return {"路径": record["path"], "目录": "blocks", "内容": body}


def workspace_search(records: list[dict], query: str, limit: int = 30) -> dict:
    query = query.strip().lower()
    if not query:
        return {"结果": [], "总数": 0}
    results = []
    for record in records:
        article = 记录转文章(record)
        haystack = " ".join((article["标题"], article["摘要"], article["作者"], article["收藏夹"])).lower()
        match = (query[1:] in article["收藏夹"].lower()) if query.startswith("#") else all(term in haystack for term in query.split())
        if match:
            类型 = "知识块" if article["内容模式"] == "zhida_block" else "公开摘要"
            results.append({**article, "类型": 类型, "片段": article["摘要"][:240], "文章": article["标题"]})
    return {"结果": results[:limit], "总数": len(results)}
