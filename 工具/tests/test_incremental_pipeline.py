# -*- coding: utf-8 -*-
"""Regression coverage for no-write previews, stable output and analysis caching."""
from __future__ import annotations

import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_knowledge_pipeline import 工具目录, 载入模块
from LLM处理 import 本地拆分
from 知识库IO import 原子写入, 写JSON若变化, 拆frontmatter, 拼frontmatter


def 文件快照(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class 增量流水线回归测试(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pipeline = 载入模块("增量处理", 工具目录 / "处理.py")
        for name, relative in {
            "笔记库": ".", "文章库": "wiki/articles", "概念库": "wiki/concepts",
            "集合库": "wiki/collections", "索引路径": "_索引.json", "图谱路径": "_图谱.json",
        }.items():
            setattr(self.pipeline, name, self.root / relative)
        for title in ("文章A", "文章B"):
            directory = self.pipeline.文章库 / "测试" / title
            directory.mkdir(parents=True)
            (directory / "00 MOC.md").write_text(拼frontmatter({
                "type": "article", "title": title + " MOC", "article": title,
                "collection": "测试", "tags": ["RAG"],
            }, "# " + title), encoding="utf-8")
            (directory / "01 分节.md").write_text(拼frontmatter({
                "type": "section", "title": title + " · 分节", "article": title,
                "collection": "测试", "order": 1, "tags": ["RAG", "仅" + title],
            }, title + " 的内容"), encoding="utf-8")

    def test_不写回不创建或修改任何文件(self):
        before = 文件快照(self.root)
        stats = self.pipeline.主流程(dry_run=True)
        self.assertEqual(stats["文章数"], 2)
        self.assertEqual(文件快照(self.root), before)
        self.assertFalse(self.pipeline.索引路径.exists())
        self.assertFalse(self.pipeline.图谱路径.exists())
        self.assertFalse(self.pipeline.概念库.exists())
        with patch("sys.argv", ["处理.py", "--不写回"]), patch.object(self.pipeline, "日志") as log:
            self.pipeline.主函数()
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any("只读检查完成" in message for message in messages))
        self.assertFalse(any("已写入" in message for message in messages))
        self.assertEqual(文件快照(self.root), before)

    def test_已生成库的不写回仍完全只读(self):
        self.pipeline.主流程()
        before = 文件快照(self.root)
        self.pipeline.主流程(dry_run=True)
        self.assertEqual(文件快照(self.root), before)

    def test_重复处理跨天也不改内容或时间(self):
        self.pipeline.主流程()
        before = 文件快照(self.root)
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2035, 1, 2, 12, 0, 0, tzinfo=tz)
        with patch.object(self.pipeline, "datetime", Later):
            self.pipeline.主流程()
        self.assertEqual(文件快照(self.root), before)

    def test_仅跨文章标签建概念页且共现标签无断链(self):
        self.pipeline.主流程()
        concepts = list(self.pipeline.概念库.glob("*.md"))
        self.assertEqual(len(concepts), 1)
        all_texts = [p.read_text(encoding="utf-8") for p in (self.root / "wiki").rglob("*.md")]
        titles = {拆frontmatter(text)[0].get("title") for text in all_texts}
        for text in all_texts:
            for target in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", text):
                self.assertIn(target, titles)
        self.assertIn("#仅文章A", concepts[0].read_text(encoding="utf-8"))

    def test_清空文章后不保留过期索引或图谱(self):
        self.pipeline.主流程()
        for file in self.pipeline.文章库.rglob("*.md"):
            file.unlink()
        self.pipeline.主流程()
        index = json.loads(self.pipeline.索引路径.read_text(encoding="utf-8"))
        graph = json.loads(self.pipeline.图谱路径.read_text(encoding="utf-8"))
        self.assertEqual(index["统计"]["文章数"], 0)
        self.assertEqual(index["文章"], [])
        self.assertEqual(graph["节点"], [])
        self.assertEqual(graph["边"], [])
        self.assertEqual(list(self.pipeline.概念库.glob("*.md")), [])

    def test_紧凑索引不重复存正文与图谱(self):
        self.pipeline.主流程()
        index = json.loads(self.pipeline.索引路径.read_text(encoding="utf-8"))
        self.assertNotIn("笔记", index)
        self.assertNotIn("图谱", index)
        section = index["文章"][0]["分节"][0]
        self.assertNotIn("正文", section)
        graph = json.loads(self.pipeline.图谱路径.read_text(encoding="utf-8"))
        nodes = {node["id"] for node in graph["节点"]}
        self.assertTrue(all(edge["源"] in nodes and edge["目标"] in nodes for edge in graph["边"]))


class 共享IO与缓存回归测试(unittest.TestCase):
    def test_相同内容不写回且不留下临时文件(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "note.md"
            self.assertTrue(原子写入(target, "正文\n"))
            before = 文件快照(Path(temp))
            self.assertFalse(原子写入(target, "正文\n"))
            self.assertEqual(文件快照(Path(temp)), before)

    def test_索引仅生成时间变化时保留原文件(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "index.json"
            self.assertTrue(写JSON若变化(target, {"生成时间": "old", "文章": []}))
            before = 文件快照(Path(temp))
            self.assertFalse(写JSON若变化(target, {"生成时间": "new", "文章": []}))
            self.assertEqual(文件快照(Path(temp)), before)

    def test_错误形状的JSON可恢复(self):
        with TemporaryDirectory() as temp:
            target = Path(temp) / "index.json"
            for previous in ("[]", '"invalid"', "null", "not-json"):
                with self.subTest(previous=previous):
                    target.write_text(previous, encoding="utf-8")
                    self.assertTrue(写JSON若变化(target, {"文章": []}))
                    self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"文章": []})

    def test_frontmatter支持CRLF并拒绝非映射(self):
        self.assertEqual(拆frontmatter("---\r\ntitle: 示例\r\n---\r\n正文"), ({"title": "示例"}, "正文"))
        self.assertEqual(拆frontmatter("---\n- item\n---\n正文"), ({}, "正文"))

    def test_分析缓存按原文哈希复用(self):
        module = 载入模块("缓存重构", 工具目录 / "知识重构.py")
        analysis = 本地拆分("示例", "## 章节\n\n正文", ["RAG"])
        with TemporaryDirectory() as temp:
            target = Path(temp) / "_analysis.json"
            target.write_text(json.dumps(module.缓存分析(analysis, "hash")), encoding="utf-8")
            restored = module.读取缓存分析(target, "hash")
            self.assertEqual(restored["sections"], analysis["sections"])
            self.assertEqual(restored["标签"], analysis["标签"])
            self.assertIsNone(module.读取缓存分析(target, "different-hash"))

    def test_MOC摘要清理截断图片而不修改分析缓存(self):
        module = 载入模块("摘要重构", 工具目录 / "知识重构.py")
        self.assertEqual(module.原文摘要("> [!info] 来源 <https://example.com>"), "来源 https://example.com")
        for image in ("![示意](assets/image.png)", "![示意](assets/未完成"):
            with self.subTest(image=image):
                self.assertEqual(module.原文摘要("说明 " + image + "\n下一段"), "说明 下一段")
        with TemporaryDirectory() as temp:
            module.笔记库 = Path(temp)
            module.文章库 = module.笔记库 / "wiki" / "articles"
            source = module.笔记库 / "示例.md"
            body = "## 章节一\n\n第一段。\n\n## 章节二\n\n第二段。"
            source.write_text(body, encoding="utf-8")
            analysis = 本地拆分("示例", body, ["测试"])
            analysis["摘要"] = "正文要点。 ![示意](assets/未完成"
            output = module.输出文章(source, "测试", analysis, "hash")
            moc = (output / "00 MOC.md").read_text(encoding="utf-8")
            self.assertIn("> 正文要点。\n", moc)
            self.assertNotIn("![", moc)
            self.assertNotIn("assets/未完成", moc)
            self.assertIn("## 阅读线", moc)
            cache = json.loads((output / "_analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(cache["summary"], analysis["摘要"])

    def test_无效分析缓存触发重建而非跳过(self):
        module = 载入模块("坏缓存重构", 工具目录 / "知识重构.py")
        with TemporaryDirectory() as temp:
            target = Path(temp) / "_analysis.json"
            for payload in ([], {"source_sha256": "hash", "sections": []},
                            {"source_sha256": "hash", "sections": [{"title": "缺正文"}]}):
                with self.subTest(payload=payload):
                    target.write_text(json.dumps(payload), encoding="utf-8")
                    self.assertIsNone(module.读取缓存分析(target, "hash"))


if __name__ == "__main__":
    unittest.main()
