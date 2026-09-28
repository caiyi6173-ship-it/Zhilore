# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

工具目录 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(工具目录))

from 官方CLI import 官方CLI客户端


class 假CLI(官方CLI客户端):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.可执行文件 = "fake"
        self.超时秒 = 1

    def 调用(self, args):
        self.calls.append(args)
        return self.responses.pop(0)


class 官方CLI适配测试(unittest.TestCase):
    def test_收藏夹列表字段适配(self):
        cli = 假CLI([{
            "Code": 0,
            "Data": {"Items": [{
                "UrlToken": 123,
                "Url": "https://www.zhihu.com/collection/123",
                "Title": "测试收藏夹",
                "Description": "描述",
                "IsPublic": True,
            }]},
        }])
        items = cli.列出收藏夹()
        self.assertEqual(items[0]["名称"], "测试收藏夹")
        self.assertEqual(items[0]["URLToken"], 123)
        self.assertTrue(items[0]["公开"])

    def test_收藏夹条目分页及字段适配(self):
        cli = 假CLI([
            {
                "Code": 0,
                "Data": {
                    "Items": [{
                        "ContentType": "answer",
                        "Url": "https://www.zhihu.com/question/1/answer/2",
                        "CreatedAt": 100,
                        "FavTime": 200,
                        "LikeCount": 3,
                        "CommentCount": 4,
                        "FavoriteCount": 5,
                        "Title": "回答标题",
                        "Summary": "回答摘要",
                        "Author": {"Name": "作者", "UrlToken": "author"},
                    }],
                    "Paging": {"IsEnd": False, "NextOffset": "20", "Totals": 2},
                },
            },
            {
                "Code": 0,
                "Data": {
                    "Items": [{
                        "ContentType": "article",
                        "Url": "https://zhuanlan.zhihu.com/p/3",
                        "Title": "文章标题",
                        "Summary": "文章摘要",
                    }],
                    "Paging": {"IsEnd": True, "Totals": 2},
                },
            },
        ])
        items = cli.收藏夹条目(123)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["type"], "answer")
        self.assertEqual(items[0]["author"]["name"], "作者")
        self.assertIn("20", cli.calls[1])

    def test_官方条目复用保存笔记结构(self):
        spec = importlib.util.spec_from_file_location("抓取模块", 工具目录 / "抓取.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp_dir:
            module.笔记库 = temp_dir
            module.原始收件箱 = Path(temp_dir) / ".raw" / "inbox"
            crawler = module.知乎抓取器(选项={"增量更新": False, "下载图片": False, "请求间隔秒": 0})

            class 单条CLI:
                def 收藏夹条目(self, token):
                    return [{
                        "type": "answer",
                        "url": "https://www.zhihu.com/question/1/answer/2",
                        "title": "官方回答",
                        "summary": "这是开放平台摘要。",
                        "created_at": 100,
                        "fav_time": 200,
                        "like_count": 3,
                        "comment_count": 4,
                        "favorite_count": 5,
                        "author": {"name": "作者"},
                    }]

            count = crawler.抓取官方收藏夹({"名称": "测试", "URLToken": 123}, 单条CLI())
            self.assertEqual(count, 1)
            note = (Path(temp_dir) / ".raw" / "inbox" / "测试" / "官方回答.md").read_text(encoding="utf-8")
            self.assertIn("类型: 回答", note)
            self.assertIn("原链接: https://www.zhihu.com/question/1/answer/2", note)
            self.assertIn("正文来源: 知乎开放平台摘要", note)
            self.assertIn("这是开放平台摘要。", note)


if __name__ == "__main__":
    unittest.main()
