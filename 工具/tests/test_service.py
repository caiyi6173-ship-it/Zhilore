# -*- coding: utf-8 -*-
"""Regression coverage for the local API's read boundary and search cache."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_knowledge_pipeline import 工具目录, 载入模块


class 服务回归测试(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "vault"
        self.root.mkdir()
        self.moc = "wiki/articles/测试/文章/00 MOC.md"
        self.section = "wiki/articles/测试/文章/01 中文 分节.md"
        self.asset = "wiki/articles/测试/文章/assets/示意 图.png"
        for relative, content in {
            self.moc: "---\ntags: [RAG]\n---\n文章总览。",
            self.section: "---\ntags: [RAG]\n---\n缓存正文 needle。",
            ".raw/sources/原文.md": "PRIVATE_RAW_CONTENT",
            ".raw/sources/隐藏.png": "PRIVATE_RAW_IMAGE",
            "wiki/articles/测试/文章/_analysis.json": '{"private": true}',
        }.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (self.root / self.asset).parent.mkdir(parents=True)
        (self.root / self.asset).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        (self.root / self.asset).with_suffix(".svg").write_text("<svg/>", encoding="utf-8")
        self.outside = Path(self.temp.name) / "outside.md"
        self.outside.write_text("OUTSIDE_CONTENT", encoding="utf-8")
        self.index = {
            "统计": {"文章数": 1, "笔记数": 1},
            "文章": [{"标题": "文章", "MOC标题": "文章 MOC", "路径": self.moc,
                      "收藏夹": "测试", "标签": ["RAG"],
                      "分节": [{"标题": "文章 · 分节", "路径": self.section, "标签": ["RAG"]}]}],
            "入口": [],
        }
        (self.root / "_索引.json").write_text(json.dumps(self.index, ensure_ascii=False), encoding="utf-8")
        (self.root / "_图谱.json").write_text('{"节点": [], "边": []}', encoding="utf-8")
        self.service = 载入模块("回归服务", 工具目录 / "服务.py")
        self.service.笔记库 = str(self.root)
        self.service.索引文件 = str(self.root / "_索引.json")
        self.service.图谱文件 = str(self.root / "_图谱.json")
        self.service.载入索引()
        self.client = TestClient(self.service.app)
        self.addCleanup(self.client.close)

    def test_索引与图谱分别提供且正文不内嵌(self):
        response = self.client.get("/api/索引")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.index)
        self.assertNotIn("needle", response.text)
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertEqual(self.client.get("/api/图谱").json(), {"节点": [], "边": []})

    def test_空库返回结构完整的空索引而不是空对象(self):
        # 索引文件缺失 = 还没跑过 处理.py。返回 {} 会让前端误报"无法连接本地知识库"。
        self.service.索引文件 = str(self.root / "不存在的索引.json")
        self.service.载入索引()
        response = self.client.get("/api/索引")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.service.空索引)
        self.assertEqual(response.json()["文章"], [])
        self.assertEqual(response.json()["入口"], [])
        self.assertEqual(response.json()["统计"]["笔记数"], 0)

    def test_中文空格路径的笔记和图片正常(self):
        note = self.client.get("/api/笔记", params={"路径": self.section})
        self.assertEqual(note.status_code, 200)
        self.assertIn("needle", note.json()["内容"])
        image = self.client.get("/笔记库/" + self.asset)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertTrue(image.content.startswith(b"\x89PNG"))

    def test_阻止原文目录穿越和绝对路径(self):
        for relative in (
            ".raw/sources/原文.md", "wiki/../.raw/sources/原文.md",
            "wiki/../../outside.md", "wiki\\..\\.raw\\sources\\原文.md",
            str(self.outside), "wiki/../../vault-other/outside.md", "wiki/\x00.md",
        ):
            with self.subTest(path=relative):
                response = self.client.get("/api/笔记", params={"路径": relative})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("PRIVATE_RAW_CONTENT", response.text)
                self.assertNotIn("OUTSIDE_CONTENT", response.text)

    def test_笔记接口只接受Markdown(self):
        for relative in (self.asset, "wiki/articles/测试/文章/_analysis.json"):
            with self.subTest(path=relative):
                self.assertEqual(self.client.get("/api/笔记", params={"路径": relative}).status_code, 404)

    def test_图片接口拒绝原文和非图片(self):
        for relative in (self.moc, "wiki/articles/测试/文章/_analysis.json"):
            self.assertEqual(self.client.get("/笔记库/" + relative).status_code, 404)
        self.assertEqual(self.client.get("/笔记库/wiki/%2e%2e/.raw/sources/隐藏.png").status_code, 400)
        self.assertEqual(self.client.get("/笔记库/.raw/sources/隐藏.png").status_code, 400)

    def test_SVG隔离活动内容(self):
        response = self.client.get("/笔记库/" + self.asset.replace(".png", ".svg"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertIn("sandbox", response.headers.get("content-security-policy", ""))

    def test_解析符号链接后仍验证边界(self):
        link = self.root / "wiki" / "outside.md"
        # Resolve the link deterministically, without requiring Windows symlink privileges.
        original = os.path.realpath
        def resolved(value):
            return str(self.outside) if os.path.normpath(value) == str(link) else original(value)
        with patch.object(self.service.os.path, "realpath", side_effect=resolved):
            response = self.client.get("/api/笔记", params={"路径": "wiki/outside.md"})
        self.assertEqual(response.status_code, 400)

    def test_搜索复用正文缓存直到重新载入(self):
        (self.root / self.section).write_text("freshword", encoding="utf-8")
        with patch("builtins.open", side_effect=AssertionError("search must not read disk")):
            self.assertEqual(self.service.搜索(q="needle", 上限=30)["总数"], 1)
            self.assertEqual(self.service.搜索(q="freshword", 上限=30)["总数"], 0)
        self.service.载入索引()
        self.assertEqual(self.service.搜索(q="needle", 上限=30)["总数"], 0)
        self.assertEqual(self.service.搜索(q="freshword", 上限=30)["总数"], 1)

    def test_井号标签搜索覆盖分节且精确匹配(self):
        result = self.client.get("/api/搜索", params={"q": "#rag"}).json()
        self.assertEqual({record["路径"] for record in result["结果"]}, {self.moc, self.section})
        self.assertEqual(self.client.get("/api/搜索", params={"q": "#ra"}).json()["总数"], 0)

    def test_搜索缓存不读取被污染索引中的原文(self):
        records = [{"标题": "异常", "路径": "wiki/../.raw/sources/原文.md"}]
        self.assertEqual(self.service.建搜索文档(records), [])

    def test_重扫失败释放互斥锁且不热载入(self):
        with patch.object(self.service.subprocess, "run", side_effect=TimeoutError("test timeout")), \
             patch.object(self.service, "载入索引") as reload_index:
            result = self.client.post("/api/重扫")
        self.assertFalse(result.json()["成功"])
        reload_index.assert_not_called()
        self.assertFalse(self.service.重扫锁.locked())

    def test_重扫子进程中文输出强制UTF8(self):
        script = self.root / "重扫测试.py"
        script.write_text("print('重扫完成：中文输出')\n", encoding="utf-8")
        with patch.object(self.service, "重构脚本", str(script)), \
             patch.object(self.service, "处理脚本", str(script)), \
             patch.dict(os.environ, {"PYTHONIOENCODING": "ascii"}):
            result = self.client.post("/api/重扫").json()
        self.assertTrue(result["成功"])
        self.assertEqual(result["输出"].count("重扫完成：中文输出"), 2)
        self.assertNotIn("\ufffd", result["输出"])

    def test_重扫互斥避免重复执行(self):
        self.service.重扫锁.acquire()
        try:
            with patch.object(self.service.subprocess, "run") as run:
                self.assertEqual(self.client.post("/api/重扫").status_code, 409)
                run.assert_not_called()
        finally:
            self.service.重扫锁.release()


if __name__ == "__main__":
    unittest.main()
