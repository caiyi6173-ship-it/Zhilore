# -*- coding: utf-8 -*-
"""直答处理链路：调用次数控制、任务隔离、幂等写入、知识块渲染与端到端。

全部使用内存夹具与假直答客户端，不联网、不使用任何真实凭证。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from zhihu_oauth.app import create_app
from zhihu_oauth.collection_data import ContentPage
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.store import SessionStore
from zhihu_oauth.直答凭证 import 直答凭证库
from zhihu_oauth.workspace import (
    WorkspaceRepository, markdown转HTML, safe_source_url,
    workspace_graph, workspace_index, workspace_note, workspace_search,
)
from zhihu_oauth.直答处理 import 任务登记处, 运行任务, 预计调用次数
from 知乎直答 import 知乎直答客户端
from test_oauth import Clock, FakeProvider, TEST_SETTINGS, collection


def 摘要条目(identifier: int, index: int) -> dict:
    return {"id": f"answer:{identifier}{index}", "type": "answer",
            "url": f"https://www.zhihu.com/question/1/answer/{identifier}{index}",
            "title": f"标题{identifier}-{index}", "summary": f"摘要{identifier}-{index}。" * 4,
            "author": "作者", "created_at": 1, "collected_at": 1,
            "like_count": "0", "comment_count": "0", "favorite_count": "0"}


class 直答测试Provider(FakeProvider):
    """一个账号、一个公开收藏夹，条目数可指定。"""

    def __init__(self, clock):
        super().__init__(clock)
        self.pages = {}

    def 布置(self, token: str, identifier: int, 条数: int) -> None:
        self.items[token] = [collection(identifier, f"收藏夹{identifier}")]
        self.pages[(token, str(identifier), "0")] = ContentPage(
            [摘要条目(identifier, i) for i in range(条数)], "0", None, str(条数))

    async def collection_contents(self, token, identifier, offset):
        return self.pages[(token, str(identifier), str(offset))]

    async def creations(self, token, content_type="all", offset="0"):
        # 既有断言都围绕收藏夹编排；创作给空页，避免改变它们的预期。
        from zhihu_oauth.collection_data import ContentPage
        return ContentPage([], str(offset), None, "0")


class 假直答客户端:
    """记录每次调用的批大小；可指定某些批次失败。"""

    可用 = True

    class 配置:
        model = "zhida-thinking-1p5"

    def __init__(self, 每块引用: int = 1, 失败批次: tuple = (), 剩余额度值: int | None = 99,
                 凭证判定: bool | None = True):
        self.调用: list[tuple[str, int]] = []
        self.每块引用 = 每块引用
        self.失败批次 = set(失败批次)
        self.剩余额度值 = 剩余额度值
        self.凭证判定 = 凭证判定

    def 剩余额度(self):
        return self.剩余额度值

    def 凭证有效(self):
        return self.凭证判定

    def 归并摘要(self, 收藏夹, 条目):
        self.调用.append((收藏夹, len(条目)))
        if len(self.调用) in self.失败批次:
            return {"blocks": [], "错误": "HTTP 429"}
        blocks = []
        for start in range(0, len(条目), self.每块引用):
            group = 条目[start:start + self.每块引用]
            if not group:
                break
            blocks.append({
                "title": f"知识块{len(blocks) + 1}", "markdown": "## 小节\n\n正文 **加粗**。",
                "summary": "块摘要", "tags": ["标签A"], "key_points": ["要点一"],
                "sources": [{"title": item["title"], "url": item["url"]} for item in group],
                "model": "zhida-thinking-1p5",
            })
        return {"blocks": blocks, "错误": ""}


class 不可用客户端:
    可用 = False


class 预计调用次数测试(unittest.TestCase):
    def test_按批换算(self):
        self.assertEqual(预计调用次数(0), 0)
        self.assertEqual(预计调用次数(1), 1)
        self.assertEqual(预计调用次数(25), 1)
        self.assertEqual(预计调用次数(26), 2)
        self.assertEqual(预计调用次数(2000), 80)


class 直答凭证库测试(unittest.TestCase):
    """用户自备凭证：只进内存，按账号隔离，绝不回显。"""

    def test_格式从严(self):
        self.assertTrue(直答凭证库.合规("abcd1234"))
        self.assertTrue(直答凭证库.合规("a" * 256))
        for 坏值 in ("短", "", None, "含 空格 abcd", "中文abcd1234", "a" * 300, "abc\ndefg"):
            with self.subTest(值=坏值):
                self.assertFalse(直答凭证库.合规(坏值))

    def test_按账号隔离(self):
        库 = 直答凭证库()
        库.存("u1", "secret-u1-0000")
        self.assertEqual(库.取("u1"), "secret-u1-0000")
        self.assertTrue(库.已设置("u1"))
        self.assertEqual(库.取("u2"), "", "绝不能跨账号取到")
        库.清("u1")
        self.assertFalse(库.已设置("u1"))

    def test_不合规直接拒绝(self):
        库 = 直答凭证库()
        with self.assertRaises(ValueError):
            库.存("u1", "bad")
        self.assertFalse(库.已设置("u1"))


class 直答客户端测试_凭证归属(unittest.TestCase):
    def test_显式凭证优先于环境变量(self):
        with patch.dict(os.environ, {"ZHIHU_ACCESS_SECRET": "app-secret-0000"}, clear=True):
            client = 知乎直答客户端(access_secret="mine-secret-1234")
            self.assertEqual(client.配置.access_secret, "mine-secret-1234")
            self.assertTrue(client.自备凭证)
            self.assertEqual(知乎直答客户端().配置.access_secret, "app-secret-0000")
            self.assertFalse(知乎直答客户端().自备凭证)


class 任务登记处测试(unittest.TestCase):
    def test_同一账号不能并发启动(self):
        处 = 任务登记处()
        task = 处.开始("u1")
        with self.assertRaises(OAuthError) as caught:
            处.开始("u1")
        self.assertEqual(caught.exception.code, "task_already_running")
        处.结束(task)
        self.assertTrue(处.开始("u1").id)

    def test_查不到别人的任务(self):
        处 = 任务登记处()
        task = 处.开始("u1")
        with self.assertRaises(OAuthError) as caught:
            处.取("u2", task.id)
        self.assertEqual(caught.exception.code, "task_not_found")
        self.assertIs(处.取("u1", task.id), task)

    def test_不传id时优先返回进行中的任务(self):
        处 = 任务登记处()
        旧 = 处.开始("u1")
        处.结束(旧)
        新 = 处.开始("u1")
        self.assertIs(处.取或当前("u1"), 新)
        处.结束(新)
        self.assertIs(处.取或当前("u1"), 新)

    def test_任务结束后这个账号能再启动(self):
        # 运行任务只写终态、不回调登记处，所以"进行中"必须靠读取时惰性清理。
        处 = 任务登记处()
        task = 处.开始("u1")
        task.完成 = True
        self.assertTrue(处.开始("u1").id)


class 知识块存储测试(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="zhida-blocks-")
        self.addCleanup(self.dir.cleanup)
        self.repo = WorkspaceRepository(Path(self.dir.name) / "ws.sqlite3")
        self.夹 = {"id": "101", "title": "测试夹"}

    def test_同一收藏夹重跑不累积(self):
        块 = [{"title": "块一", "markdown": "正文一"}, {"title": "块二", "markdown": "正文二"}]
        self.repo.replace_blocks("u1", self.夹, 块)
        self.assertEqual(len(self.repo.records("u1")), 2)
        self.repo.replace_blocks("u1", self.夹, [块[0]])
        records = self.repo.records("u1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "block")

    def test_没有标题或正文的块被丢弃(self):
        self.repo.replace_blocks("u1", self.夹, [
            {"title": "", "markdown": "正文"}, {"title": "有标题", "markdown": "   "},
            {"title": "合法", "markdown": "正文"}])
        self.assertEqual([r["item"]["title"] for r in self.repo.records("u1")], ["合法"])

    def test_同夹内重名自动加序号(self):
        # 模型分批产出时常给出同名块；重名会让列表出现多个一模一样的东西。
        self.repo.replace_blocks("u1", self.夹, [
            {"title": "主题一", "markdown": "A"}, {"title": "主题一", "markdown": "B"},
            {"title": "主题一", "markdown": "C"}])
        self.assertEqual([r["item"]["title"] for r in self.repo.records("u1")],
                         ["主题一", "主题一（2）", "主题一（3）"])

    def test_账号之间互不可见(self):
        self.repo.replace_blocks("u1", self.夹, [{"title": "块", "markdown": "正文"}])
        self.assertEqual(self.repo.records("u2"), [])

    def test_与摘要快照共存(self):
        self.repo.import_page("u1", self.夹, [{
            "id": "answer:1010", "type": "answer", "url": "https://www.zhihu.com/question/1/answer/1010",
            "title": "摘要条目", "summary": "摘要正文", "author": "作者", "created_at": 1,
            "collected_at": 1, "like_count": "0", "comment_count": "0", "favorite_count": "0"}])
        self.repo.replace_blocks("u1", self.夹, [{"title": "块", "markdown": "正文"}])
        kinds = sorted(record["kind"] for record in self.repo.records("u1"))
        self.assertEqual(kinds, ["block", "summary"])


class 知识块渲染测试(unittest.TestCase):
    """模型输出是不可信输入，markdown 渲染必须先转义再标注。"""

    def test_渲染标题列表与行内标注(self):
        html = markdown转HTML("## 标题\n\n正文 **粗** `码`\n\n- 甲\n- 乙")
        self.assertIn("<h3>标题</h3>", html)
        self.assertIn("<strong>粗</strong>", html)
        self.assertIn("<code>码</code>", html)
        self.assertIn("<ul><li>甲</li><li>乙</li></ul>", html)

    def test_模型输出的HTML被转义(self):
        html = markdown转HTML('<img src=x onerror=alert(1)>\n\n<script>alert(2)</script>')
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertIn("&lt;img", html)

    def test_只放行http链接(self):
        self.assertEqual(safe_source_url("https://www.zhihu.com/x"), "https://www.zhihu.com/x")
        for bad in ("javascript:alert(1)", "data:text/html,x", "//evil.example", "", "  "):
            with self.subTest(bad=bad):
                self.assertEqual(safe_source_url(bad), "")

    def test_笔记里不出现不可信链接(self):
        record = {"kind": "block", "path": "blocks/x.md", "collection_id": "101", "collection_title": "夹",
                  "item": {"title": "块", "markdown": "正文", "summary": "摘要",
                           "sources": [{"title": "坏来源", "url": "javascript:alert(1)"},
                                       {"title": "好来源", "url": "https://www.zhihu.com/x"}]}}
        note = workspace_note([record], "blocks/x.md")
        self.assertNotIn("javascript:", note["内容"])
        self.assertIn("https://www.zhihu.com/x", note["内容"])
        self.assertEqual(note["目录"], "blocks")


class 端到端任务测试(unittest.TestCase):
    def setUp(self):
        self.时钟 = Clock()
        self.provider = 直答测试Provider(self.时钟)
        self.dir = tempfile.TemporaryDirectory(prefix="zhida-task-")
        self.addCleanup(self.dir.cleanup)
        self.workspace = WorkspaceRepository(Path(self.dir.name) / "ws.sqlite3")

    def 跑(self, client, 条数=60, 调用上限=90):
        self.provider.布置("mock-token-a", 101, 条数)
        task = 任务登记处().开始("stable-a")
        asyncio.run(运行任务(task, self.provider, self.workspace, client, "mock-token-a",
                            调用上限=调用上限, 间隔=0))
        return task

    def test_整夹处理并按批调用(self):
        client = 假直答客户端(每块引用=6)
        task = self.跑(client)
        self.assertEqual(task.阶段, "完成", task.失败)
        self.assertEqual(task.条目总数, 60)
        self.assertEqual(task.预计调用, 3)
        self.assertEqual(task.已用调用, 3)
        self.assertEqual([批 for _, 批 in client.调用], [25, 25, 10], "必须按批，不能逐条")
        records = self.workspace.records("stable-a")
        # 分批是 25/25/10，每批内再按 6 条一块：5 + 5 + 2 = 12
        self.assertEqual(len(records), 12)

    def test_知识块进入工作区四个视图(self):
        self.跑(假直答客户端(每块引用=6))
        records = self.workspace.records("stable-a")
        index = workspace_index(records)
        self.assertEqual(index["来源模式"], "public_summary+zhida_block")
        self.assertEqual(index["统计"]["笔记数"], 12)
        self.assertTrue(index["说明"].startswith("这是知乎直答"))
        graph = workspace_graph(records)
        self.assertTrue(graph["节点"] and graph["边"])
        note = workspace_note(records, records[0]["path"])
        self.assertIn("知识块", note["内容"])
        self.assertTrue(workspace_search(records, "知识块")["结果"])
        self.assertEqual(workspace_search(records, "知识块")["结果"][0]["类型"], "知识块")

    def test_预计超过上限时拒绝且零调用(self):
        client = 假直答客户端()
        task = self.跑(client, 调用上限=2)
        self.assertEqual(task.阶段, "失败")
        self.assertIn("超过上限", task.失败)
        self.assertEqual(client.调用, [], "超限必须在调用前就停下")
        self.assertEqual(self.workspace.records("stable-a"), [])

    def test_今日额度不够时零调用直接失败(self):
        # 60 条 → 预计 3 批；今日只剩 1 次，跑一半必然浪费额度，所以在动手前就停下。
        client = 假直答客户端(剩余额度值=1)
        task = self.跑(client)
        self.assertEqual(task.阶段, "失败")
        self.assertIn("今日直答额度剩余 1 次", task.失败)
        self.assertIn("没有发起任何调用", task.失败)
        self.assertEqual(client.调用, [])
        self.assertEqual(task.已用调用, 0)
        self.assertEqual(self.workspace.records("stable-a"), [])

    def test_额度够时正常处理并带上剩余额度(self):
        client = 假直答客户端(每块引用=6, 剩余额度值=10)
        task = self.跑(client)
        self.assertEqual(task.阶段, "完成", task.失败)
        self.assertEqual(task.今日剩余额度, 10)
        self.assertEqual(task.public()["今日剩余额度"], 10)

    def test_额度查不到时不挡住处理(self):
        client = 假直答客户端(每块引用=6, 剩余额度值=None)
        task = self.跑(client)
        self.assertEqual(task.阶段, "完成", task.失败)
        self.assertIsNone(task.今日剩余额度)

    def test_单个批次失败不影响其它批次写入(self):
        client = 假直答客户端(每块引用=1, 失败批次=(2,))
        task = self.跑(client)
        self.assertEqual(task.阶段, "完成")
        self.assertEqual(task.已用调用, 3)
        self.assertEqual(len(task.问题), 1)
        self.assertIn("HTTP 429", task.问题[0])
        self.assertTrue(self.workspace.records("stable-a"))

    def test_重跑同一账号不产生重复(self):
        client = 假直答客户端(每块引用=6)
        self.跑(client)
        first = len(self.workspace.records("stable-a"))
        self.跑(client)
        self.assertEqual(len(self.workspace.records("stable-a")), first)

    def test_失败批次的条目下次还会重试(self):
        """指纹只累加"这一批真的成功了"的条目：失败的批次不能被记成已归并，
        否则那批内容再也补不回来（等于静默丢内容）。"""
        client = 假直答客户端(每块引用=1, 失败批次=(2,))
        self.跑(client)
        已归并 = self.workspace.已归并条目("stable-a", "101")
        self.assertEqual(len(已归并), 35, "只记第 1、3 批（25+10）；第 2 批留给下次重试")
        client2 = 假直答客户端(每块引用=1)
        self.跑(client2)
        self.assertEqual([批 for _, 批 in client2.调用], [25], "只重试失败那批，已成功的 35 条不再送")
        self.assertEqual(len(self.workspace.已归并条目("stable-a", "101")), 60)

    def test_老数据没有指纹时按块里的来源反查_零调用(self):
        """线上真实事故（2026-09-15）：升级前归并过的收藏夹只有块、没有指纹行，
        升级后点一次处理又把老文章送了一遍，于是同一篇文章堆出两份知识块。
        现在要靠块里的来源网址反查——认出已经归并过的，一次调用都不多花。"""
        client = 假直答客户端(每块引用=6)
        self.跑(client)
        第一次 = len(self.workspace.records("stable-a"))
        self.assertEqual(第一次, 12)
        self.assertEqual([批 for _, 批 in client.调用], [25, 25, 10])

        # 模拟历史数据：有块、没指纹。
        with self.workspace.connection() as db:
            db.execute("DELETE FROM block_fingerprints WHERE uid = ?", ("stable-a",))
        self.assertEqual(self.workspace.已归并条目("stable-a", "101"), set())

        client2 = 假直答客户端(每块引用=6)
        task = self.跑(client2)
        self.assertEqual(client2.调用, [], "已经归并过的条目一条都不能再送直答")
        self.assertEqual(task.阶段, "失败")
        self.assertIn("没有需要归并的新内容", task.失败)
        self.assertEqual(len(self.workspace.records("stable-a")), 第一次, "块数不能翻倍")

    def test_老数据只有部分条目有块时只补没归并过的(self):
        self.provider.布置("mock-token-a", 101, 2)
        client = 假直答客户端(每块引用=1)
        task = 任务登记处().开始("stable-a")
        asyncio.run(运行任务(task, self.provider, self.workspace, client, "mock-token-a", 间隔=0))
        self.assertEqual([批 for _, 批 in client.调用], [2])
        # 只当"第 1 条归并过"：删掉引用第 2 条的那块，并清掉指纹。
        self.assertEqual(self.workspace.delete("stable-a", path="blocks/101-001.md")["知识块"], 1)
        with self.workspace.connection() as db:
            db.execute("DELETE FROM block_fingerprints WHERE uid = ?", ("stable-a",))

        client2 = 假直答客户端(每块引用=1)
        task2 = 任务登记处().开始("stable-a")
        asyncio.run(运行任务(task2, self.provider, self.workspace, client2, "mock-token-a", 间隔=0))
        self.assertEqual([批 for _, 批 in client2.调用], [1], "只补第 2 条，第 1 条不再送")
        self.assertEqual(task2.知识块数, 1)

    def test_预计次数按收藏夹分别向上取整(self):
        """42 + 28 条按夹分是 2+2=4 批；合并算成 ceil(70/25)=3 会低估额度。"""
        self.provider.items["mock-token-a"] = [collection(101, "夹一"), collection(202, "夹二")]
        for identifier, 条数 in ((101, 42), (202, 28)):
            self.provider.pages[("mock-token-a", str(identifier), "0")] = ContentPage(
                [摘要条目(identifier, i) for i in range(条数)], "0", None, str(条数))
        client = 假直答客户端()
        task = 任务登记处().开始("stable-a")
        asyncio.run(运行任务(task, self.provider, self.workspace, client, "mock-token-a", 间隔=0))
        self.assertEqual(task.预计调用, 4)
        self.assertEqual(task.已用调用, 4)
        self.assertEqual(task.收藏夹完成, 2)
        self.assertTrue(task.完成, "任务必须自己写终态，否则这个账号再也启动不了")

    def test_没有公开收藏夹时明确失败(self):
        self.provider.items["mock-token-a"] = []
        task = 任务登记处().开始("stable-a")
        asyncio.run(运行任务(task, self.provider, self.workspace, 假直答客户端(), "mock-token-a", 间隔=0))
        self.assertEqual(task.阶段, "失败")
        # 创作可以独立归并，所以无收藏夹不再是硬失败——只有连创作也没有时才停。
        self.assertIn("没有需要归并的新内容", task.失败)


class 路由测试(unittest.TestCase):
    """一体化入口：启动与查询都必须先登录，且同源带 CSRF。"""

    def setUp(self):
        self.时钟 = Clock()
        self.store = SessionStore(self.时钟)
        self.provider = 直答测试Provider(self.时钟)
        self.provider.布置("mock-token-a", 101, 3)
        self.dir = tempfile.TemporaryDirectory(prefix="zhida-route-")
        self.addCleanup(self.dir.cleanup)
        self.直答 = 假直答客户端(每块引用=1)
        self.client = self.建(lambda access_secret=None: self.直答)

    def 建(self, 工厂):
        knowledge = SimpleNamespace(app=FastAPI())

        @knowledge.app.get("/api/索引")
        async def 索引():
            return {"文章": [], "入口": []}

        app = create_app(TEST_SETTINGS, self.provider, self.store,
                         WorkspaceRepository(Path(self.dir.name) / "ws.sqlite3"),
                         default_return_to="/account", 直答工厂=工厂)
        client = TestClient(app, base_url=TEST_SETTINGS.origin)
        self.addCleanup(client.close)
        return client

    def 登录(self, client=None):
        client = client or self.client
        state = parse_qs(urlsplit(client.post("/auth/zhihu/start", json={},
                    headers={"Origin": TEST_SETTINGS.origin}).json()["authorization_url"]).query)["state"][0]
        client.get("/auth/zhihu/callback", params={"state": state, "authorization_code": "a"},
                   follow_redirects=False)
        return client

    def test_未登录不能启动也不能查状态(self):
        # POST 先过同源校验（缺 Origin 直接 403），带对 Origin 时才轮到登录校验。
        self.assertEqual(self.client.post("/api/直答处理", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/直答处理", json={},
                         headers={"Origin": TEST_SETTINGS.origin}).status_code, 401)
        self.assertEqual(self.client.get("/api/直答处理/状态").status_code, 401)

    def test_额度接口需要登录且只回数字不回凭证(self):
        self.assertEqual(self.client.get("/api/直答额度").status_code, 401)
        self.登录()
        响应 = self.client.get("/api/直答额度")
        self.assertEqual(响应.status_code, 200)
        数据 = 响应.json()
        self.assertTrue(数据["可用"])
        self.assertEqual(数据["剩余"], 99)
        self.assertEqual(数据["消耗方"], "应用凭证（开发者账号）")
        self.assertTrue(数据["AccessSecret地址"].startswith("https://developer.zhihu.com/"))
        self.assertNotIn(TEST_SETTINGS.access_secret, 响应.text)
        self.assertNotIn("mock-token-a", 响应.text)

    def test_额度读不到时返回null而不是报错(self):
        客户端 = self.建(lambda access_secret=None: 假直答客户端(剩余额度值=None))
        self.登录(客户端)
        响应 = 客户端.get("/api/直答额度")
        self.assertEqual(响应.status_code, 200)
        self.assertIsNone(响应.json()["剩余"])

    def 写头(self, client=None, **额外):
        客户端 = client or self.client
        # 写接口必须同源 + JSON Content-Type（防表单跨站）+ CSRF 令牌。
        头 = {"Origin": TEST_SETTINGS.origin, "Content-Type": "application/json",
             "X-CSRF-Token": self.csrf(客户端)}
        头.update(额外)
        return 头

    def test_凭证接口需要登录与CSRF(self):
        体 = {"access_secret": "my-secret-1234"}
        self.assertEqual(self.client.post("/api/直答凭证", json=体,
                         headers={"Origin": TEST_SETTINGS.origin}).status_code, 401)
        self.assertEqual(self.client.delete("/api/直答凭证",
                         headers={"Origin": TEST_SETTINGS.origin,
                                  "Content-Type": "application/json"}).status_code, 401)
        # 写接口还要求 JSON Content-Type（防表单跨站），缺了就是 403，轮不到登录校验。
        self.assertEqual(self.client.delete("/api/直答凭证",
                         headers={"Origin": TEST_SETTINGS.origin}).status_code, 403)
        self.登录()
        # 缺 CSRF → 403
        self.assertEqual(self.client.post("/api/直答凭证", json=体,
                         headers={"Origin": TEST_SETTINGS.origin}).status_code, 403)

    def test_保存自备凭证后额度归属变为自用且不回显(self):
        self.登录()
        响应 = self.client.post("/api/直答凭证", json={"access_secret": "my-secret-1234"},
                               headers=self.写头())
        self.assertEqual(响应.status_code, 200, 响应.text)
        self.assertTrue(响应.json()["已设置"])
        self.assertEqual(响应.json()["凭证"], "自用凭证")
        self.assertNotIn("my-secret-1234", 响应.text, "响应里绝不能回显凭证")
        额度 = self.client.get("/api/直答额度").json()
        self.assertEqual(额度["凭证"], "自用凭证")
        self.assertIn("自己", 额度["消耗方"])

    def test_自备凭证会传给直答客户端(self):
        记录 = []

        def 工厂(access_secret=None):
            记录.append(access_secret)
            return 假直答客户端(剩余额度值=7)

        客户端 = self.建(工厂)
        self.登录(客户端)
        客户端.post("/api/直答凭证", json={"access_secret": "my-secret-1234"}, headers=self.写头(客户端))
        客户端.get("/api/直答额度")
        self.assertIn("my-secret-1234", 记录, "后续直答调用必须带上用户自备凭证")

    def test_无效凭证被拒绝且不保存(self):
        客户端 = self.建(lambda access_secret=None: 假直答客户端(凭证判定=False))
        self.登录(客户端)
        响应 = 客户端.post("/api/直答凭证", json={"access_secret": "bad-secret-1"}, headers=self.写头(客户端))
        self.assertEqual(响应.status_code, 400)
        self.assertEqual(响应.json()["error"]["code"], "invalid_access_secret")
        self.assertEqual(客户端.get("/api/直答额度").json()["凭证"], "应用默认", "无效凭证不能留下")

    def test_凭证格式不合法时400(self):
        self.登录()
        响应 = self.client.post("/api/直答凭证", json={"access_secret": "短"}, headers=self.写头())
        self.assertEqual(响应.status_code, 400)
        self.assertEqual(响应.json()["error"]["code"], "invalid_credential_request")

    def test_无法判定时503且不误报无效(self):
        客户端 = self.建(lambda access_secret=None: 假直答客户端(凭证判定=None))
        self.登录(客户端)
        响应 = 客户端.post("/api/直答凭证", json={"access_secret": "my-secret-1234"}, headers=self.写头(客户端))
        self.assertEqual(响应.status_code, 503)
        self.assertEqual(响应.json()["error"]["code"], "credential_check_unavailable")

    def test_删除凭证回到应用默认(self):
        self.登录()
        self.client.post("/api/直答凭证", json={"access_secret": "my-secret-1234"}, headers=self.写头())
        self.assertEqual(self.client.get("/api/直答额度").json()["凭证"], "自用凭证")
        响应 = self.client.delete("/api/直答凭证", headers=self.写头())
        self.assertEqual(响应.status_code, 200)
        self.assertFalse(响应.json()["已设置"])
        self.assertEqual(self.client.get("/api/直答额度").json()["凭证"], "应用默认")

    def test_设置页需要登录且能看到表单(self):
        # 未登录 → 303 到登录页（不跟随重定向，否则看到的是登录页 200）
        self.assertEqual(self.client.get("/settings", follow_redirects=False).status_code, 303)
        self.登录()
        page = self.client.get("/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="access-secret"', page.text)
        self.assertIn('type="password"', page.text)
        self.assertIn('href="https://developer.zhihu.com/profile"', page.text)
        self.assertEqual(self.client.get("/assets/settings.js").status_code, 200)

    def test_没有直答凭证时额度接口也返回503(self):
        客户端 = self.建(lambda access_secret=None: 不可用客户端())
        self.登录(客户端)
        self.assertEqual(客户端.get("/api/直答额度").status_code, 503)

    def test_没有直答凭证时返回503(self):
        无凭证 = self.建(lambda access_secret=None: 不可用客户端())
        self.登录(无凭证)
        response = 无凭证.post("/api/直答处理", json={},
                            headers={"Origin": TEST_SETTINGS.origin, "X-CSRF-Token": self.csrf(无凭证)})
        self.assertEqual(response.status_code, 503)

    def csrf(self, client):
        return client.get("/api/me").json()["csrf_token"]

    def test_缺CSRF或跨源被拒(self):
        self.登录()
        csrf = self.csrf(self.client)
        self.assertEqual(self.client.post("/api/直答处理", json={},
                         headers={"Origin": TEST_SETTINGS.origin}).status_code, 403)
        self.assertEqual(self.client.post("/api/直答处理", json={},
                         headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf}).status_code, 403)

    def test_启动后能查到自己的任务(self):
        self.登录()
        response = self.client.post("/api/直答处理", json={},
                                    headers={"Origin": TEST_SETTINGS.origin,
                                             "X-CSRF-Token": self.csrf(self.client)})
        self.assertEqual(response.status_code, 200, response.text)
        task_id = response.json()["task"]["id"]
        self.assertTrue(task_id)
        # 不传 id 也应返回同一个任务，页面刷新后靠这个恢复进度。
        self.assertEqual(self.client.get("/api/直答处理/状态").json()["task"]["id"], task_id)
        self.assertEqual(self.client.get("/api/直答处理/状态", params={"task": task_id}).json()["task"]["id"], task_id)

    def test_查别人的任务id返回404(self):
        self.登录()
        self.assertEqual(self.client.get("/api/直答处理/状态", params={"task": "猜的id"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
