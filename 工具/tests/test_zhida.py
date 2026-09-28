# -*- coding: utf-8 -*-
"""知乎直答适配器契约测试：全部用合成响应，不联网、不使用任何真实凭证。"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from _环境隔离 import 仅环境

工具目录 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(工具目录))

from 知乎直答 import 提取JSON, 规范来源序号, 知乎直答客户端

分节JSON = json.dumps({
    "summary": "把收藏变成可检索结构的摘要。",
    "article_tags": ["知识管理", "索引"],
    "sections": [
        {"title": "索引的价值", "markdown": "零散内容需要可检索的结构。", "tags": ["知识管理"], "key_points": ["结构优于堆积"]},
        {"title": "三个步骤", "markdown": "归类、写摘要、建关联。", "tags": ["索引"], "key_points": ["关联形成网络"]},
    ],
}, ensure_ascii=False)


class 假响应:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def 直答回复(text: str, **message_extra) -> dict:
    message = {"role": "assistant", "content": text, **message_extra}
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 1,
            "model": "zhida-thinking-1p5",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


def 环境(secret: str = "s" * 40, **extra) -> dict:
    """只给测试需要的最小环境。

    以前这里会把宿主机的全部环境变量复制进来，宿主机若有个别超长变量
    （实测本机有个 48 万字符的变量），`patch.dict(..., clear=True)` 还原时会
    直接抛 ValueError，测试结果就与代码无关。隔离改用 `_环境隔离.仅环境()`。
    """
    值 = {"ZHIHU_ACCESS_SECRET": secret}
    值.update(extra)
    return 值


def 取数据(用户消息: str) -> dict:
    """按契约从用户消息里取回数据 JSON：指令与结尾要求都不含花括号。"""
    return json.loads(用户消息[用户消息.index("{"):用户消息.rindex("}") + 1])


class 提取JSON测试(unittest.TestCase):
    """实测直答会把 JSON 包在 ```json 围栏里，解析必须容错。"""

    def test_解析代码围栏(self):
        self.assertEqual(提取JSON('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(提取JSON('```\n{"a": 1}\n```'), {"a": 1})

    def test_解析带前后解释的JSON(self):
        self.assertEqual(提取JSON('好的，结果如下：\n{"a": 1}\n希望有帮助'), {"a": 1})

    def test_解析纯JSON(self):
        self.assertEqual(提取JSON('{"a": 1}'), {"a": 1})
        self.assertEqual(提取JSON('  {"a": [1, 2]}  '), {"a": [1, 2]})

    def test_无法解析时返回None而不是抛错(self):
        for bad in ("", "   ", None, 123, {"a": 1}, "没有 JSON 内容", '```json\n{坏}\n```'):
            with self.subTest(bad=bad):
                self.assertIsNone(提取JSON(bad))


class 规范来源序号测试(unittest.TestCase):
    """模型写 sources 的形态五花八门，直接按 int 过滤会把原文链接全丢掉（实测 4 个块 0 条来源）。"""

    def test_数字数组保持原样(self):
        self.assertEqual(规范来源序号([1, 2, 5], 25), [1, 2, 5])

    def test_字符串数字与分隔符(self):
        self.assertEqual(规范来源序号(["1", "2"], 25), [1, 2])
        self.assertEqual(规范来源序号("1,2,3", 25), [1, 2, 3])
        self.assertEqual(规范来源序号("第 1 条、第 2 条", 25), [1, 2])
        self.assertEqual(规范来源序号("3-6", 25), [3, 4, 5, 6])

    def test_越界重复与空值都被过滤(self):
        self.assertEqual(规范来源序号([2, 2, 99, 0, "3", True], 25), [2, 3])
        self.assertEqual(规范来源序号([], 25), [])
        self.assertEqual(规范来源序号(None, 25), [])


class 直答客户端测试(unittest.TestCase):
    def test_缺少凭证时不可用并降级到本地规则(self):
        with 仅环境(环境(secret="")):
            client = 知乎直答客户端()
            self.assertFalse(client.可用)
            result = client.分析文章("标题", "第一段。\n\n## 小节\n\n第二段。", ["RAG"])
        self.assertEqual(result["模式"], "local-fallback")

    def test_禁用开关与未授权模型回落(self):
        with 仅环境(环境(ZHIHU_ZHIDA_ENABLED="false")):
            self.assertFalse(知乎直答客户端().可用)
        with 仅环境(环境(ZHIHU_ZHIDA_MODEL="gpt-4.1-mini")):
            self.assertEqual(知乎直答客户端().配置.model, "zhida-thinking-1p5")

    def test_请求只带三个保证字段且鉴权头正确(self):
        捕获 = {}

        def 假urlopen(request, timeout=None):
            捕获["url"] = request.full_url
            捕获["body"] = json.loads(request.data.decode("utf-8"))
            捕获["auth"] = request.get_header("Authorization")
            捕获["ts"] = request.get_header("X-request-timestamp")
            捕获["content_type"] = request.get_header("Content-type")
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                result = client.分析文章("标题", "正文。" * 50, ["RAG"])

        self.assertEqual(捕获["url"], "https://developer.zhihu.com/v1/chat/completions")
        self.assertEqual(set(捕获["body"]), {"model", "messages", "stream"})
        self.assertIs(捕获["body"]["stream"], False)
        self.assertEqual([m["role"] for m in 捕获["body"]["messages"]], ["system", "user"])
        self.assertEqual(捕获["auth"], "Bearer " + "s" * 40)
        self.assertTrue(str(捕获["ts"]).isdigit())
        self.assertEqual(捕获["content_type"], "application/json")
        self.assertEqual(result["模式"], "llm")
        self.assertEqual(result["引擎"], "直答")
        self.assertEqual(result["模型"], "zhida-thinking-1p5")
        self.assertEqual([s["标题"] for s in result["sections"]], ["索引的价值", "三个步骤"])

    def test_只用最终内容_忽略思考过程(self):
        payload = 直答回复(分节JSON, reasoning_content='思考中…{"sections":[]}')
        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", lambda request, timeout=None: 假响应(payload)):
                result = client.分析文章("标题", "正文。" * 50)
        self.assertEqual(len(result["sections"]), 2)

    def test_限流429退避后重试成功(self):
        次数 = {"n": 0}

        def 假urlopen(request, timeout=None):
            if "/api/v1/quota" in request.full_url:
                # 额度还有剩余 → 属于真限流，应该退避重试。
                return 假响应({"Code": 0, "Data": [{"APIID": "zhida_openai", "RemainingQuota": 5}]})
            次数["n"] += 1
            if 次数["n"] == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate limit",
                                             {"Retry-After": "0"}, None)
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境(ZHIHU_ZHIDA_MAX_ATTEMPTS="3")):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen), patch("知乎直答.time.sleep") as 睡:
                result = client.分析文章("标题", "正文。" * 50)

        self.assertEqual(次数["n"], 2)
        self.assertEqual(result["引擎"], "直答")
        self.assertTrue(睡.called, "限流后必须退避再试，不能直接降级")

    def test_额度用尽时429不重试也不白等(self):
        import io
        调用 = {"chat": 0, "quota": 0}

        def 假urlopen(request, timeout=None):
            if "/api/v1/quota" in request.full_url:
                调用["quota"] += 1
                return 假响应({"Code": 0, "Data": [{"APIID": "zhida_openai", "RemainingQuota": 0}]})
            调用["chat"] += 1
            raise urllib.error.HTTPError(request.full_url, 429, "rate limit", {}, io.BytesIO(b"{}"))

        with 仅环境(环境(ZHIHU_ZHIDA_MAX_ATTEMPTS="3")):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen), patch("知乎直答.time.sleep") as 睡:
                result = client.分析文章("标题", "正文。" * 50)

        self.assertEqual(调用["chat"], 1, "额度用尽是确定性状态，重试只是白等")
        self.assertEqual(调用["quota"], 1)
        self.assertFalse(睡.called)
        self.assertIn("额度已用尽", result["降级原因"])
        self.assertEqual(result["模式"], "local-fallback")

    def test_剩余额度从官方额度接口读取(self):
        请求过的 = []

        def 假urlopen(request, timeout=None):
            请求过的.append(request.full_url)
            return 假响应({"Code": 0, "Message": "success", "Data": [
                {"APIID": "user_data", "RemainingQuota": 978},
                {"APIID": "zhida_openai", "RemainingQuota": 7},
            ]})

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                self.assertEqual(client.剩余额度(), 7)
                # 30 秒内复用缓存，不重复打额度接口。
                self.assertEqual(client.剩余额度(), 7)

        self.assertEqual(请求过的, ["https://developer.zhihu.com/api/v1/quota?APIIDs=zhida_openai"])

    def test_额度查不到时不挡住主流程(self):
        def 假urlopen(request, timeout=None):
            raise OSError("network down")

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                self.assertIsNone(client.剩余额度())

    def test_用户消息里带明确指令_避免被当成问答(self):
        捕获 = {}

        def 假urlopen(request, timeout=None):
            捕获["body"] = json.loads(request.data.decode("utf-8"))
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                client.分析文章("标题", "正文。" * 50)

        消息 = 捕获["body"]["messages"][1]["content"]
        self.assertTrue(消息.startswith("这是一个数据转换任务"), "只给数据不给任务时，直答会当成提问来回答")
        self.assertIn("不要回答、不要解释", 消息)
        self.assertEqual(取数据(消息)["title"], "标题")

    def test_输出不是JSON时带纠错提示重试(self):
        请求过的 = []

        def 假urlopen(request, timeout=None):
            请求过的.append(json.loads(request.data.decode("utf-8"))["messages"][1]["content"])
            if len(请求过的) == 1:
                return 假响应(直答回复("您的问题中没有包含具体的提问内容，只提供了收藏列表。"))
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                result = client.分析文章("标题", "正文。" * 50)

        self.assertEqual(len(请求过的), 2)
        self.assertNotIn("左花括号", 请求过的[0])
        self.assertIn("左花括号", 请求过的[1], "重试要追加格式纠错，而不是原样重发")
        self.assertTrue(请求过的[1].startswith("这是一个数据转换任务"), "原始指令与数据不能丢")
        self.assertEqual(result["模式"], "llm")

    def test_重试时刷新时间戳(self):
        时间戳 = []

        def 假urlopen(request, timeout=None):
            时间戳.append(request.get_header("X-request-timestamp"))
            if len(时间戳) == 1:
                raise urllib.error.HTTPError(request.full_url, 503, "busy", None, None)
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境(ZHIHU_ZHIDA_MAX_ATTEMPTS="2")):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen), patch("知乎直答.time.sleep"), \
                    patch("知乎直答.time.time", side_effect=[1800000000, 1800000007]):
                client.分析文章("标题", "正文。" * 50)

        self.assertEqual(时间戳, ["1800000000", "1800000007"], "沿用旧时间戳会鉴权失败")

    def test_凭证无效不重试直接降级(self):
        次数 = {"n": 0}

        def 假urlopen(request, timeout=None):
            次数["n"] += 1
            raise urllib.error.HTTPError(request.full_url, 401, "bad secret", None, None)

        with 仅环境(环境(ZHIHU_ZHIDA_MAX_ATTEMPTS="3")):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                result = client.分析文章("标题", "第一段。\n\n## 小节\n\n第二段。", ["RAG"])

        self.assertEqual(次数["n"], 1, "401 是凭证问题，重试没有意义")
        self.assertEqual(result["模式"], "local-fallback")
        self.assertIn("401", result["降级原因"])

    def test_输出不是合法JSON时降级且标记原因(self):
        with 仅环境(环境(ZHIHU_ZHIDA_MAX_ATTEMPTS="1")):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", lambda request, timeout=None: 假响应(直答回复("我无法完成这个任务。"))):
                result = client.分析文章("标题", "第一段。\n\n## 小节\n\n第二段。", ["RAG"])

        self.assertEqual(result["模式"], "local-fallback")
        self.assertIn("不是合法 JSON", result["降级原因"])

    def test_分节数量不合法时降级(self):
        payload = 直答回复(json.dumps({
            "summary": "只有一节", "article_tags": ["RAG"],
            "sections": [{"title": "唯一一节", "markdown": "正文", "tags": ["RAG"]}],
        }, ensure_ascii=False))
        with 仅环境(环境(ZHIHU_ZHIDA_MAX_ATTEMPTS="1")):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", lambda request, timeout=None: 假响应(payload)):
                result = client.分析文章("标题", "第一段。\n\n## 小节\n\n第二段。", ["RAG"])

        self.assertEqual(result["模式"], "local-fallback")
        self.assertGreaterEqual(len(result["sections"]), 2)

    def test_超长正文被截断(self):
        捕获 = {}

        def 假urlopen(request, timeout=None):
            捕获["body"] = json.loads(request.data.decode("utf-8"))
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                client.分析文章("标题", "正" * 60000)

        内容 = 取数据(捕获["body"]["messages"][1]["content"])["content"]
        self.assertEqual(len(内容), 24000)

    def test_系统提示要求只用原文事实(self):
        捕获 = {}

        def 假urlopen(request, timeout=None):
            捕获["body"] = json.loads(request.data.decode("utf-8"))
            return 假响应(直答回复(分节JSON))

        with 仅环境(环境()):
            client = 知乎直答客户端()
            with patch("urllib.request.urlopen", 假urlopen):
                client.分析文章("标题", "正文。" * 50)

        系统 = 捕获["body"]["messages"][0]["content"]
        self.assertIn("绝不补充原文没有的信息", 系统)
        self.assertIn("不要引用外部资料", 系统)


if __name__ == "__main__":
    unittest.main()
