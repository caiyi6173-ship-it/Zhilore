# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


工具目录 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(工具目录))

from LLM处理 import 本地拆分, 清理标签


def 载入模块(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class LLM处理测试(unittest.TestCase):
    def test_标签清理保留分隔符(self):
        self.assertEqual(清理标签(["Claude Code", "RAG", "claude code"]), ["Claude-Code", "RAG"])

    def test_本地拆分不把代码块标题当章节(self):
        text = """# 标题

正文一。

```markdown
# 项目约定
```

## 真章节

正文二。
"""
        result = 本地拆分("标题", text, ["测试"])
        self.assertEqual([x["标题"] for x in result["sections"]], ["标题", "真章节"])


class 知识流水线测试(unittest.TestCase):
    def test_重构文章为文件夹并归档原文(self):
        with tempfile.TemporaryDirectory() as temp:
            module = 载入模块("重构模块", 工具目录 / "知识重构.py")
            module.笔记库 = Path(temp)
            module.原始库 = module.笔记库 / ".raw" / "sources"
            module.收件箱 = module.笔记库 / ".raw" / "inbox"
            module.文章库 = module.笔记库 / "wiki" / "articles"
            source_dir = module.笔记库 / "AI技术"
            source_dir.mkdir(parents=True)
            source = source_dir / "测试文章.md"
            source.write_text("---\n标题: 测试文章\n原链接: https://example.com\n标签:\n- RAG\n---\n\n第一段。\n\n## 策略\n\n第二段。\n", encoding="utf-8")

            analysis = 本地拆分("测试文章", "第一段。\n\n## 策略\n\n第二段。", ["RAG"])
            output = module.输出文章(source, "AI技术", analysis, "test-hash")
            module.归档原文(source, "AI技术", "hash")

            self.assertTrue((output / "00 MOC.md").exists())
            self.assertGreaterEqual(len(list(output.glob("[0-9][0-9] *.md"))), 2)
            self.assertTrue(module.原始库.joinpath("AI技术", "测试文章.md").exists())
            self.assertFalse(source.exists())

    def test_处理生成分节关联和概念页(self):
        with tempfile.TemporaryDirectory() as temp:
            module = 载入模块("处理模块", 工具目录 / "处理.py")
            module.笔记库 = Path(temp)
            module.文章库 = module.笔记库 / "wiki" / "articles"
            module.概念库 = module.笔记库 / "wiki" / "concepts"
            module.集合库 = module.笔记库 / "wiki" / "collections"
            module.索引路径 = module.笔记库 / "_索引.json"
            module.图谱路径 = module.笔记库 / "_图谱.json"

            for article, section in (("文章A", "切分"), ("文章B", "检索")):
                directory = module.文章库 / "AI技术" / article
                directory.mkdir(parents=True)
                for order, tag in ((1, "RAG"), (2, "工程")):
                    path = directory / f"{order:02d} {tag}.md"
                    front = {
                        "type": "section", "title": f"{article} · {tag}", "article": article,
                        "collection": "AI技术", "order": order, "tags": ["RAG", tag],
                        "source": "https://example.com",
                    }
                    path.write_text(module.拼frontmatter(front, f"{article} 的 {tag} 内容。"), encoding="utf-8")
                moc = directory / "00 MOC.md"
                moc.write_text(module.拼frontmatter({
                    "type": "article", "title": f"{article} MOC", "article": article,
                    "collection": "AI技术", "source": "https://example.com", "tags": ["RAG"],
                }, f"# {article}"), encoding="utf-8")

            stats = module.主流程()
            self.assertEqual(stats["文章数"], 2)
            index = json.loads(module.索引路径.read_text(encoding="utf-8"))
            self.assertEqual(index["统计"]["文章数"], 2)
            self.assertTrue(any("跨文章关联" in note for note in [p.read_text(encoding="utf-8") for p in module.文章库.glob("*/*/*.md")]))
            self.assertTrue((module.概念库 / "标签：RAG.md").exists())


class 引擎严格模式测试(unittest.TestCase):
    """模型不可用时，--严格 必须把原文留在原地，绝不把降级结果写进知识库。"""

    def _跑(self, temp: str, 严格: bool):
        module = 载入模块("严格模式重构模块", 工具目录 / "知识重构.py")
        库 = Path(temp)
        module.笔记库 = 库
        module.原始库 = 库 / ".raw" / "sources"
        module.收件箱 = 库 / ".raw" / "inbox"
        module.文章库 = 库 / "wiki" / "articles"
        源 = 库 / "AI技术"
        源.mkdir(parents=True)
        原文 = 源 / "文章.md"
        原文.write_text(
            "---\n标题: 文章\n原链接: https://example.com\n标签:\n- RAG\n---\n\n第一段。\n\n## 策略\n\n第二段。\n",
            encoding="utf-8")

        class 降级客户端:
            可用 = True
            配置 = SimpleNamespace(model="zhida-thinking-1p5")

            @staticmethod
            def 分析文章(标题, 正文, 已有标签=None):
                result = 本地拆分(标题, 正文, 已有标签)
                result["降级原因"] = "HTTP 429（限流）"
                return result

        argv = ["知识重构.py", "--引擎", "直答"] + (["--严格"] if 严格 else [])
        with patch.object(module, "建引擎", return_value=(降级客户端(), "知乎直答（测试）")), \
                patch("sys.argv", argv):
            module.主函数()
        return module, 原文

    def test_严格模式跳过降级结果并保留原文(self):
        with tempfile.TemporaryDirectory() as temp:
            module, 原文 = self._跑(temp, 严格=True)
            self.assertEqual(list(module.文章库.rglob("00 MOC.md")), [], "降级结果不能落盘")
            self.assertTrue(原文.exists(), "原文必须留在原地，等额度恢复后重跑")
            self.assertTrue(not module.原始库.exists() or not list(module.原始库.rglob("*.md")),
                            "降级时不应归档原文")

    def test_非严格模式照常落盘并归档(self):
        with tempfile.TemporaryDirectory() as temp:
            module, 原文 = self._跑(temp, 严格=False)
            self.assertEqual(len(list(module.文章库.rglob("00 MOC.md"))), 1)
            self.assertFalse(原文.exists(), "非严格模式照常归档原文")


if __name__ == "__main__":
    unittest.main()