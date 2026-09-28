# -*- coding: utf-8 -*-
"""Synthetic identities + temporary SQLite only; never reads the author's vault."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zhihu_oauth.app import create_app
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.store import SessionStore
from zhihu_oauth.workspace import (WorkspaceRepository, workspace_creations, workspace_graph,
                                   workspace_index)
from test_oauth import Clock, TEST_SETTINGS
from test_public_collections import PublicFakeProvider, payload, raw_item


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repository = WorkspaceRepository(Path(self.tmp.name) / "personal.sqlite3")
        self.clock = Clock()
        self.store = SessionStore(self.clock)
        self.provider = PublicFakeProvider(self.clock)
        self.app = create_app(TEST_SETTINGS, self.provider, self.store, self.repository)
        self.a, self.b = self.client(), self.client()

    def client(self):
        client = TestClient(self.app, base_url=TEST_SETTINGS.origin)
        self.addCleanup(client.close)
        return client

    def login(self, client, account="a"):
        start = client.post("/auth/zhihu/start", json={}, headers={"Origin": TEST_SETTINGS.origin})
        self.assertEqual(start.status_code, 200, start.text)
        state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
        result = client.get("/auth/zhihu/callback", params={"state": state, "authorization_code": account}, follow_redirects=False)
        self.assertEqual(result.headers["location"], "/workspace/")
        return client.get("/api/me").json()

    def headers(self, client):
        me = client.get("/api/me").json()
        return {"Origin": TEST_SETTINGS.origin, "X-CSRF-Token": me["csrf_token"]}

    def read(self, client, name="索引", **kwargs):
        marker = client.get("/api/me").json()["csrf_token"]
        return client.get("/api/workspace/" + name, headers={"X-Workspace-Session": marker}, **kwargs)

    def collect(self, client, identifier="101", offset="0"):
        return client.post("/api/workspace/import", json={"collection_id": identifier, "offset": offset}, headers=self.headers(client))

    def assert_error(self, response, code, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn("no-store", response.headers["cache-control"])
        for secret in ("mock-token-a", "mock-token-b", TEST_SETTINGS.app_key, TEST_SETTINGS.access_secret):
            self.assertNotIn(secret, response.text)

    def test_unauthenticated_html_redirects_and_api_never_creates_database(self):
        for url in ("/workspace", "/workspace/"):
            response = self.a.get(url, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(urlsplit(response.headers["location"]).path, "/login")
            self.assertEqual(parse_qs(urlsplit(response.headers["location"]).query), {"returnTo": [url], "result": ["login_required"]})
        for name in ("索引", "图谱", "搜索", "笔记"):
            self.assert_error(self.a.get("/api/workspace/" + name), "login_required", 401)
        self.assert_error(self.a.post("/api/workspace/import", json={}, headers={"Origin": TEST_SETTINGS.origin}), "login_required", 401)
        self.assertFalse(self.repository.path.exists())
        self.assertEqual(self.provider.content_reads, [])

    def test_empty_new_user_never_receives_author_notes(self):
        self.login(self.a)
        index = self.read(self.a).json()
        self.assertEqual(index["文章"], [])
        self.assertEqual(index["入口"], [])
        self.assertEqual(index["来源模式"], "public_summary")
        self.assertEqual(self.read(self.a, "图谱").json(), {"节点": [], "边": []})

    def test_two_same_name_users_have_separate_index_graph_search_and_notes(self):
        self.login(self.a)
        self.login(self.b, "b")
        self.assertEqual(self.collect(self.a).json()["imported"], {"added": 1, "updated": 0, "total": 1})
        self.assertEqual(self.collect(self.b, "202").json()["user"]["id"], "stable-b")
        for client, wanted, forbidden in ((self.a, "A 摘要测试", "B 摘要测试"), (self.b, "B 摘要测试", "A 摘要测试")):
            index = self.read(client).json()
            self.assertEqual(len(index["文章"]), 1)
            self.assertEqual(index["文章"][0]["标题"], wanted)
            self.assertEqual(index["文章"][0]["分节"], [])
            graph = self.read(client, "图谱").json()
            self.assertEqual(len(graph["节点"]), 2)
            self.assertEqual(len(graph["边"]), 1)
            self.assertEqual(graph["边"][0]["类型"], "标签")
            self.assertEqual(self.read(client, "搜索", params={"q": wanted}).json()["总数"], 1)
            self.assertEqual(self.read(client, "搜索", params={"q": forbidden}).json()["总数"], 0)
        path_a = self.read(self.a).json()["文章"][0]["路径"]
        self.assert_error(self.read(self.b, "笔记", params={"路径": path_a}), "note_unavailable", 404)
        self.assertEqual(self.read(self.a, "笔记", params={"路径": path_a}).status_code, 200)

    def test_repeated_pages_upsert_and_preserve_provider_cursor(self):
        self.login(self.a)
        self.collect(self.a)
        result = self.collect(self.a).json()
        self.assertEqual(result["imported"], {"added": 0, "updated": 1, "total": 1})
        self.assertEqual(result["source"], "public_summary")
        second = self.collect(self.a, offset="00027")
        self.assertEqual(second.json()["offset"], "00027")
        self.assertEqual(second.json()["imported"]["total"], 2)
        self.assertEqual(self.provider.content_reads[-1], ("mock-token-a", "101", "00027"))
        self.assertEqual(len(self.provider.reads), 3)

    def test_import_rechecks_membership_even_if_page_was_previously_read(self):
        self.login(self.a)
        self.assertEqual(self.a.get("/api/collections/101/contents").status_code, 200)
        self.provider.items["mock-token-a"][0]["is_public"] = False
        self.assert_error(self.collect(self.a), "collection_unavailable", 404)
        self.assertEqual(len(self.provider.content_reads), 1)
        self.assertEqual(self.repository.records("stable-a"), [])

    def test_import_other_users_collection_never_fetches_it(self):
        self.login(self.a)
        self.assert_error(self.collect(self.a, "202"), "collection_unavailable", 404)
        self.assertEqual(self.provider.content_reads, [])

    def test_import_rejects_client_identity_content_or_url(self):
        self.login(self.a)
        headers = self.headers(self.a)
        for key, value in (("uid", "stable-b"), ("url", "https://evil.invalid"), ("items", []), ("token", "mock-token-b")):
            body = {"collection_id": "101", "offset": "0", key: value}
            self.assert_error(self.a.post("/api/workspace/import", json=body, headers=headers), "invalid_import_request", 400)
        for body in ([], None, {"collection_id": "101"}):
            self.assert_error(self.a.post("/api/workspace/import", json=body, headers={**headers, "Content-Type": "application/json"}), "invalid_import_request", 400)
        self.assertEqual(self.provider.reads, [])

    def test_import_requires_exact_origin_json_and_current_csrf(self):
        me = self.login(self.a)
        for headers in ({}, {"Origin": "https://other.invalid"}, {"Origin": TEST_SETTINGS.origin},
                        {"Origin": TEST_SETTINGS.origin, "X-CSRF-Token": "incorrect"}):
            response = self.a.post("/api/workspace/import", json={"collection_id": "101", "offset": "0"}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.provider.reads, [])
        old_csrf = me["csrf_token"]
        self.login(self.a, "b")
        response = self.a.post("/api/workspace/import", json={"collection_id": "202", "offset": "0"},
                               headers={"Origin": TEST_SETTINGS.origin, "X-CSRF-Token": old_csrf})
        self.assert_error(response, "csrf_rejected", 403)
        self.assertEqual(self.a.get("/api/me").json()["user"]["id"], "stable-b")

    def test_import_request_size_and_pagination_validation(self):
        self.login(self.a)
        headers = {**self.headers(self.a), "Content-Type": "application/json"}
        for body in ("{" + "x" * 1100, "not-json"):
            self.assert_error(self.a.post("/api/workspace/import", content=body, headers=headers), "invalid_import_request", 400)
        for identifier, offset in (("../101", "0"), ("101", "-1"), (True, "0"), ("101", [])):
            self.assert_error(self.collect(self.a, identifier, offset), "invalid_collection_request", 400)
        self.assertEqual(self.provider.reads, [])

    def test_stale_or_missing_workspace_marker_never_reads_new_account(self):
        old = self.login(self.a)["csrf_token"]
        self.login(self.a, "b")
        for marker in ("", old):
            response = self.a.get("/api/workspace/索引", headers={"X-Workspace-Session": marker})
            self.assert_error(response, "workspace_session_changed", 409)
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(self.a.get("/api/me").json()["user"]["id"], "stable-b")
        self.assertFalse(self.repository.path.exists())

    def test_query_and_headers_cannot_select_another_uid(self):
        self.login(self.a)
        self.login(self.b, "b")
        self.collect(self.a)
        self.collect(self.b, "202")
        response = self.read(self.a, params={"uid": "stable-b", "user_id": "stable-b", "token": "mock-token-b"})
        self.assertEqual(response.json()["文章"][0]["标题"], "A 摘要测试")

    def test_database_persists_summary_but_not_access_token(self):
        self.login(self.a)
        self.collect(self.a)
        self.a.post("/auth/logout", json={}, headers=self.headers(self.a))
        self.assertEqual(self.a.get("/api/me").status_code, 401)
        self.store.clear()
        reopened = WorkspaceRepository(self.repository.path)
        self.assertEqual(len(reopened.records("stable-a")), 1)
        self.login(self.a)
        self.assertEqual(self.read(self.a).json()["统计"]["文章数"], 1)
        raw = self.repository.path.read_bytes()
        for secret in (b"mock-token-a", b"mock-token-b", TEST_SETTINGS.app_key.encode(), TEST_SETTINGS.access_secret.encode()):
            self.assertNotIn(secret, raw)

    def test_expired_token_cannot_read_saved_summary(self):
        self.login(self.a)
        self.collect(self.a)
        marker = self.a.get("/api/me").json()["csrf_token"]
        self.clock.now += 601
        response = self.a.get("/api/workspace/索引", headers={"X-Workspace-Session": marker})
        self.assert_error(response, "token_expired", 401)
        self.assertNotIn("set-cookie", response.headers)

    def test_expiry_during_either_upstream_phase_never_commits_data(self):
        for phase in ("on_read", "on_content"):
            with self.subTest(phase=phase):
                self.login(self.a)
                setattr(self.provider, phase, lambda: setattr(self.clock, "now", self.clock.now + 601))
                self.assert_error(self.collect(self.a), "token_expired", 401)
                self.assertEqual(self.repository.records("stable-a"), [])
                setattr(self.provider, phase, None)

    def test_revocation_during_import_never_commits_or_deletes_new_cookie(self):
        self.login(self.a)
        handle = self.a.cookies.get(TEST_SETTINGS.session_cookie)
        self.provider.on_content = lambda: self.store.revoke(handle)
        response = self.collect(self.a)
        self.assert_error(response, "session_expired", 401)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.repository.records("stable-a"), [])

    def test_permission_and_rate_errors_are_not_empty_import_success(self):
        for code, status in (("permission_denied", 403), ("rate_limited", 429), ("quota_exceeded", 429),
                             ("authorization_failed", 401), ("upstream_unavailable", 502)):
            with self.subTest(code=code):
                self.login(self.a)
                self.provider.content_errors["mock-token-a"] = OAuthError(code, status)
                self.assert_error(self.collect(self.a), code, status)
                self.assertEqual(self.repository.records("stable-a"), [])
                self.provider.content_errors.clear()

    def test_snapshot_escapes_untrusted_summary_and_does_not_fetch_urls(self):
        self.login(self.a)
        self.provider.pages[("mock-token-a", "101", "0")] = payload([
            raw_item(11, Summary='<script>alert(1)</script>\n<img src="https://evil.invalid"> **not markdown**')])
        self.collect(self.a)
        path = self.read(self.a).json()["文章"][0]["路径"]
        text = self.read(self.a, "笔记", params={"路径": path}).json()["内容"]
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("不是全文", text)
        self.assertIn("未经过大模型拆分", text)

    def test_note_parameter_cannot_traverse_files_or_have_multiple_paths(self):
        self.login(self.a)
        for params in ({"路径": "../../README.md"}, {"路径": "D:/项目ai/知乎/笔记库/index.md"},
                       [("路径", "a"), ("路径", "b")], {}):
            self.assert_error(self.read(self.a, "笔记", params=params), "note_unavailable", 404)

    def test_workspace_static_allowlist_and_shared_api_remain_closed(self):
        self.login(self.a)
        page = self.a.get("/workspace/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("workspace-locked", page.text)
        self.assertIn("/workspace/assets/oauth/workspace.js", page.text)
        # The workspace can reach the account page even when "/" is the shared vault.
        self.assertIn('class="workspace-import" href="/account"', page.text)
        self.assertIn('<a href="/account">返回账号页</a>', page.text)
        self.assertNotIn("127.0.0.1:8099", page.text)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        for path in ("app.js", "graph.js", "oauth/workspace.js", "lib/marked.min.js"):
            self.assertEqual(self.a.get("/workspace/assets/" + path).status_code, 200)
        for path in ("index.html", "索引.json", "../README.md", "%2e%2e/README.md", "../../笔记库/README.md"):
            self.assertEqual(self.a.get("/workspace/assets/" + path).status_code, 404)
        for url in ("/api/索引", "/api/图谱", "/笔记库/README.md", "/数据/用户工作区.sqlite3"):
            self.assertEqual(self.a.get(url).status_code, 404)
        self.assertEqual(self.a.post("/api/重扫", json={}).status_code, 404)

    def test_工作区顶部提供直答处理入口(self):
        self.login(self.a)
        page = self.a.get("/workspace/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="zh-import"', page.text)
        self.assertIn('id="zh-import-start"', page.text)
        self.assertIn("用直答生成知识地图（收藏 + 创作地图）", page.text)
        # 额度实时显示（开发者的池子）+ 一键去官方页面获取 Access Secret。
        self.assertIn('id="zh-import-quota"', page.text)
        self.assertIn('id="zh-import-secret"', page.text)
        self.assertIn('href="https://developer.zhihu.com/profile"', page.text)
        self.assertIn('rel="noopener noreferrer"', page.text)
        # 用自己的 Access Secret 烧自己的额度：横幅右侧要有设置页入口，
        # 并且正文要说清"默认走应用凭证"，不能让用户以为额度一定是自己的。
        self.assertIn('id="zh-import-settings"', page.text)
        self.assertIn('href="/settings"', page.text)
        self.assertIn("直答额度默认走应用凭证", page.text)
        # 脚本必须走 workspace.js 的受控封装，所以要在它之后加载。
        self.assertIn("/workspace/assets/oauth/import-collections.js", page.text)
        self.assertLess(page.text.index("oauth/workspace.js"), page.text.index("oauth/import-collections.js"))
        self.assertEqual(self.a.get("/workspace/assets/oauth/import-collections.js").status_code, 200)
        # 个人知识库只查看结果，不放处理入口。
        个人 = self.a.get("/")
        self.assertNotIn('id="zh-import"', 个人.text)
        self.assertNotIn('id="zh-import-secret"', 个人.text)
        self.assertNotIn('id="zh-import-settings"', 个人.text)

    def test_知乎收藏页换成收藏夹卡片视图且不再加载知识库前端(self):
        self.login(self.a)
        page = self.a.get("/workspace/")
        # 卡片视图：收藏夹列表 + 内容卡片 + 空态 + 主题按钮。
        for 标记 in ('id="collections"', 'id="collections-list"', 'id="collections-cards"',
                     'id="collections-empty"', 'id="collections-theme"'):
            self.assertIn(标记, page.text)
        # 账号栏与直答横幅都还在，账号核验的锚点不能少。
        for 标记 in ('id="workspace-account"', 'id="workspace-logout"', 'id="workspace-gate"', 'id="zh-import"'):
            self.assertIn(标记, page.text)
        self.assertIn("collections-page", page.text)
        # 知识库那套（侧栏 / 图谱 / 阅读面板）只属于「个人知识库」，这一页不该再有。
        self.assertNotIn('class="layout"', page.text)
        self.assertNotIn("graph.js", page.text)
        self.assertNotIn("app.js", page.text)
        # 卡片脚本同样要走 workspace.js 的受控封装。
        self.assertIn("/workspace/assets/oauth/collections.js", page.text)
        self.assertLess(page.text.index("oauth/workspace.js"), page.text.index("oauth/collections.js"))
        self.assertEqual(self.a.get("/workspace/assets/oauth/collections.js").status_code, 200)

    def test_收藏夹卡片接口按收藏夹分组并只给只读字段(self):
        self.login(self.a)
        self.assertEqual(self.read(self.a, "收藏夹").json()["收藏夹"], [], "空库返回结构完整的空列表")
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"}, [self.卡片条目(11)])
        self.repository.import_page("stable-a", {"id": "202", "title": "AI 工具"}, [self.卡片条目(21)])
        数据 = self.read(self.a, "收藏夹").json()
        self.assertEqual(数据["统计"], {"收藏夹数": 2, "内容数": 2, "知识块数": 0})
        夹 = {item["id"]: item for item in 数据["收藏夹"]}
        self.assertEqual(sorted(夹), ["101", "202"])
        条目 = 夹["101"]["内容"][0]
        self.assertEqual(set(条目), {"模式", "标题", "作者", "摘要", "原文链接", "赞同数", "评论数",
                                     "收藏数", "收录时间", "标签", "来源数"})
        self.assertEqual(条目["模式"], "公开摘要")
        self.assertEqual(条目["标题"], "标题11")
        self.assertEqual(条目["作者"], "作者11")
        self.assertEqual(条目["赞同数"], "7")
        # 看原文一律跳回知乎，所以必须给可用的原文链接。
        self.assertTrue(条目["原文链接"].startswith("https://www.zhihu.com/"))
        # 纯读：响应里不能出现任何凭证。
        for 秘密 in ("mock-token-a", TEST_SETTINGS.app_key, TEST_SETTINGS.access_secret):
            self.assertNotIn(秘密, self.read(self.a, "收藏夹").text)

    def test_收藏夹卡片接口剥掉危险链接并要求工作区会话(self):
        self.login(self.b, "b")
        # 写入路径本来就拒绝非 http(s) 链接，所以这里直接种一条脏快照，
        # 专门盯读取侧的兜底：卡片不能把 javascript: 之类的链接渲染出去。
        脏 = dict(self.卡片条目(21), url="javascript:alert(1)", title="<img src=x onerror=alert(1)>")
        with self.repository.connection() as db:
            db.execute("INSERT INTO summaries VALUES (?, ?, ?, ?, ?)",
                       ("stable-b", "summaries/poisoned.md", "202", "AI 工具",
                        json.dumps(脏, ensure_ascii=False)))
        数据 = self.read(self.b, "收藏夹").json()
        条目 = 数据["收藏夹"][0]["内容"][0]
        self.assertEqual(条目["原文链接"], "")
        self.assertEqual(条目["标题"], "<img src=x onerror=alert(1)>", "原样交给前端转义，不在服务端拼 HTML")
        # 没有工作区会话头 → 拒绝；连登录都没有 → 401。
        self.assert_error(self.b.get("/api/workspace/收藏夹"), "workspace_session_changed", 409)
        self.assertEqual(self.client().get("/api/workspace/收藏夹").status_code, 401)

    def test_卡片视图也列知识块并标出来源数(self):
        self.login(self.a)
        self.repository.replace_blocks("stable-a", {"id": "101", "title": "我的收藏"}, [{
            "title": "归并出来的主题", "markdown": "## 要点\n\n正文", "summary": "由 8 条摘要归并而成。",
            "tags": ["我的收藏"], "key_points": [], "model": "zhida-thinking-1p5",
            "sources": [{"title": "标题11", "url": "https://www.zhihu.com/question/1/answer/11"},
                        {"title": "坏链接", "url": "javascript:alert(1)"}],
        }])
        数据 = self.read(self.a, "收藏夹").json()
        self.assertEqual(数据["统计"]["知识块数"], 1)
        条目 = 数据["收藏夹"][0]["内容"][0]
        self.assertEqual(条目["模式"], "知识块")
        self.assertEqual(条目["来源数"], 2)
        self.assertEqual(条目["原文链接"], "https://www.zhihu.com/question/1/answer/11", "取第一个安全来源")
        self.assertEqual(条目["赞同数"], "", "知识块没有互动数字，不能编一个出来")

    def 删除(self, client=None, **参数):
        client = client or self.a
        return client.request("DELETE", "/api/workspace/记录", json=参数, headers=self.headers(client))

    def test_删除单条记录后图谱与列表同步(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"},
                                    [self.卡片条目(11), self.卡片条目(12)])
        路径 = next(r["path"] for r in self.repository.records("stable-a") if r["item"]["title"] == "标题11")
        self.assertEqual(len(self.read(self.a, "图谱").json()["节点"]), 3, "2 条记录 + 1 个收藏夹标签")
        响应 = self.删除(路径=路径)
        self.assertEqual(响应.status_code, 200, 响应.text)
        self.assertEqual((响应.json()["摘要"], 响应.json()["知识块"], 响应.json()["deleted"]), (1, 0, 1))
        self.assertEqual([r["item"]["title"] for r in self.repository.records("stable-a")], ["标题12"])
        self.assertEqual(len(self.read(self.a, "图谱").json()["节点"]), 2, "删完节点与连线一起消失")
        # 再删同一条：明确回 404，而不是装作成功。
        self.assert_error(self.删除(路径=路径), "record_not_found", 404)

    def test_删除收藏夹连知识块一起清空且不动别的夹(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"},
                                    [self.卡片条目(11), self.卡片条目(12)])
        self.repository.import_page("stable-a", {"id": "202", "title": "AI 工具"}, [self.卡片条目(21)])
        self.repository.replace_blocks("stable-a", {"id": "101", "title": "我的收藏"}, [{
            "title": "归并块", "markdown": "## 要点\n\n正文", "summary": "由 8 条归并。",
            "tags": ["我的收藏"], "key_points": [], "model": "zhida-thinking-1p5", "sources": []}])
        响应 = self.删除(收藏夹="101")
        self.assertEqual(响应.status_code, 200, 响应.text)
        self.assertEqual((响应.json()["摘要"], 响应.json()["知识块"], 响应.json()["deleted"]), (2, 1, 3))
        self.assertEqual({r["collection_id"] for r in self.repository.records("stable-a")}, {"202"})
        图谱 = self.read(self.a, "图谱").json()
        self.assertEqual([n["标题"] for n in 图谱["节点"] if n["类型"] == "标签"], ["AI 工具 · 202"])

    def test_图谱节点带收藏夹id方便前端整夹删除(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "202", "title": "AI 工具"}, [self.卡片条目(21)])
        图谱 = self.read(self.a, "图谱").json()
        标签 = next(n for n in 图谱["节点"] if n["类型"] == "标签")
        self.assertEqual(标签["收藏夹id"], "202", "标题里虽有 id，但前端不该去反解展示字符串")
        文章 = next(n for n in 图谱["节点"] if n["类型"] == "文章")
        self.assertEqual((文章["收藏夹id"], 文章["路径"]), ("202", 文章["id"]))

    def test_删除接口的鉴权与参数校验(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"}, [self.卡片条目(11)])
        路径 = self.repository.records("stable-a")[0]["path"]
        未登录 = self.client().request("DELETE", "/api/workspace/记录", json={"路径": 路径})
        self.assertGreaterEqual(未登录.status_code, 400, "未登录绝不能删")
        # 同源 → CSRF 两道门；Content-Type 必须是 JSON（少了它是 403 而不是 401）。
        self.assert_error(self.a.request("DELETE", "/api/workspace/记录", json={"路径": 路径}), "origin_rejected", 403)
        self.assert_error(self.a.request("DELETE", "/api/workspace/记录", json={"路径": 路径},
                                        headers={"Origin": TEST_SETTINGS.origin}), "csrf_rejected", 403)
        self.assert_error(self.a.request("DELETE", "/api/workspace/记录", json={"路径": 路径},
                                        headers={"Origin": "https://evil.invalid"}), "origin_rejected", 403)
        坏参数 = ({}, {"路径": ""}, {"路径": "   "}, {"路径": "x" * 201}, {"路径": 123},
                  {"uid": "stable-b"}, {"路径": "summaries/a.md", "收藏夹": "101"})
        for 参数 in 坏参数:
            self.assert_error(self.a.request("DELETE", "/api/workspace/记录", json=参数, headers=self.headers(self.a)),
                              "invalid_delete_request", 400)
        self.assertEqual(len(self.repository.records("stable-a")), 1, "校验失败时一条都不能删")

    def test_删除不能越过账号边界(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"}, [self.卡片条目(11)])
        self.repository.import_page("stable-b", {"id": "202", "title": "AI 工具"}, [self.卡片条目(21)])
        # 拿别人的路径 / 收藏夹 id 来删：回 404 而不是 403，不泄露"这条到底存不存在"。
        self.assert_error(self.删除(路径=self.repository.records("stable-b")[0]["path"]), "record_not_found", 404)
        self.assert_error(self.删除(收藏夹="202"), "record_not_found", 404)
        self.assertEqual(len(self.repository.records("stable-b")), 1, "别人的记录一条都不能动")
        self.assertEqual(len(self.repository.records("stable-a")), 1)

    def test_仓库层删除必须恰好给一个选择器(self):
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"}, [self.卡片条目(11)])
        for 参数 in ({}, {"path": "p", "collection_id": "101"}):
            with self.assertRaises(OAuthError) as 捕获:
                self.repository.delete("stable-a", **参数)
            self.assertEqual(捕获.exception.status, 400, "空选择器会把整库删空，必须拒绝")
        self.assertEqual(len(self.repository.records("stable-a")), 1)

    def 创作条目(self, 编号: int, 类型: str = "answer") -> dict:
        """归一化后的创作条目：id 必须写成 content_link 反解的形式（类型:号）。"""
        号 = str(3000 + 编号)
        网址 = {"answer": f"https://www.zhihu.com/question/1/answer/{号}",
                "article": f"https://zhuanlan.zhihu.com/p/{号}",
                "zvideo": f"https://www.zhihu.com/zvideo/{号}",
                "pin": f"https://www.zhihu.com/pin/{号}",
                "question": f"https://www.zhihu.com/question/{号}"}[类型]
        return {"id": f"{类型}:{号}", "type": 类型, "url": 网址, "title": f"创作{编号}",
                "summary": f"摘要{编号}。", "author": None, "created_at": 1750000000 + 编号,
                "collected_at": 1750000000 + 编号, "like_count": "12", "comment_count": "3",
                "favorite_count": "1"}

    def test_增量归并追加不覆盖且创作知识块显示名(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"}, [self.卡片条目(11)])
        夹 = {"id": "101", "title": "我的收藏"}
        第一批 = [{"title": "主题一", "markdown": "正文", "summary": "摘要", "tags": [],
                   "key_points": [], "model": "m", "sources": []}]
        self.repository.append_blocks("stable-a", 夹, 第一批)
        self.repository.记录归并条目("stable-a", "101", ["answer:11"])
        self.assertEqual(self.repository.已归并条目("stable-a", "101"), {"answer:11"})
        # 老内容不重算：新增一块时老块保留，路径序号接着往后排。
        第二批 = [{"title": "主题二", "markdown": "正文二", "summary": "摘要二", "tags": [],
                   "key_points": [], "model": "m", "sources": []}]
        self.repository.append_blocks("stable-a", 夹, 第二批)
        块 = [r for r in self.repository.records("stable-a") if r["kind"] == "block"]
        self.assertEqual(len(块), 2, "追加不覆盖老块")
        self.assertEqual(sorted(r["path"] for r in 块), ["blocks/101-000.md", "blocks/101-001.md"])
        # 创作的知识块（虚拟分组 creations）显示为「我的创作」，不带内部 id 后缀。
        self.repository.replace_blocks("stable-a", {"id": "creations", "title": "我的创作"},
                                       [{"title": "创作地图", "markdown": "正文", "summary": "s",
                                         "tags": [], "key_points": [], "model": "m", "sources": []}])
        视图 = workspace_index(self.repository.records("stable-a"))
        self.assertEqual(next(a for a in 视图["文章"] if a["标题"] == "创作地图")["收藏夹"],
                         "我的创作", "creations 的知识块不带 ·creations 后缀")
        # 整组删除「我的创作」：原始创作与知识块一起清，指纹也清。
        self.repository.import_creations("stable-a", [self.创作条目(1)])
        结果 = self.repository.delete("stable-a", collection_id="creations")
        self.assertGreaterEqual(结果["知识块"], 1)
        self.assertEqual(len(self.repository.creations("stable-a")), 0)
        self.assertEqual(self.repository.已归并条目("stable-a", "creations"), set())

    def test_归并指纹是并集_快照变小也不会重跑(self):
        """一次拉取拿到的条目可能比上次少（列表上限、上游波动、用户在知乎取消收藏），
        所以指纹必须**累加**；覆盖写会让掉出快照的条目下次又被当成新内容送一遍，
        用户看到的就是"处理过的文章又被处理一次"。"""
        self.repository.记录归并条目("stable-a", "101", ["answer:1", "answer:2"])
        self.repository.记录归并条目("stable-a", "101", ["answer:3"])
        self.assertEqual(self.repository.已归并条目("stable-a", "101"),
                         {"answer:1", "answer:2", "answer:3"})
        # 空列表是脏输入，不能把已有指纹清空。
        self.repository.记录归并条目("stable-a", "101", [])
        self.assertEqual(len(self.repository.已归并条目("stable-a", "101")), 3)

    def test_一个来源的多个主题块都要留下(self):
        """线上实测：一条回答正常能拆出多个主题块，这些块**都引用同一个来源**。
        所以入库绝不能按来源去重——那会把正当的块当成重复删掉。"""
        夹 = {"id": "101", "title": "我的收藏"}
        网址 = "https://www.zhihu.com/answer/457426770"

        def 块(标题):
            return {"title": 标题, "markdown": "正文", "summary": "", "tags": [], "key_points": [],
                    "model": "m", "sources": [{"title": "来源", "url": 网址}]}

        self.assertEqual(self.repository.append_blocks("stable-a", 夹, [块("气质"), 块("外在"), 块("内在")]),
                         {"blocks": 3})
        块列表 = [r for r in self.repository.records("stable-a") if r["kind"] == "block"]
        self.assertEqual(len(块列表), 3, "同源不等于重复，三块都要在")
        # 反查接口用来给老数据补指纹：它只回答"这个夹引用过哪些来源网址"。
        self.assertEqual(self.repository.块的来源网址("stable-a", "101"), {网址})
        self.assertEqual(self.repository.块的来源网址("stable-a", "202"), set())

    def test_右删之后追加不覆盖已有块(self):
        """序号取"现有路径最大下标 + 1"，不能用 COUNT(*)——右键删掉**靠前**的节点之后
        COUNT 会变小，接着写就会撞上仍然存在的路径，把别人的块覆盖掉。"""
        夹 = {"id": "101", "title": "我的收藏"}

        def 块(标题):
            return {"title": 标题, "markdown": "正文", "summary": "", "tags": [],
                    "key_points": [], "model": "m", "sources": []}

        self.repository.append_blocks("stable-a", 夹, [块("一"), 块("二"), 块("三")])
        self.assertEqual(self.repository.delete("stable-a", path="blocks/101-000.md")["知识块"], 1)
        self.repository.append_blocks("stable-a", 夹, [块("四")])
        块列表 = [r for r in self.repository.records("stable-a") if r["kind"] == "block"]
        self.assertEqual(sorted(r["path"] for r in 块列表),
                         ["blocks/101-001.md", "blocks/101-002.md", "blocks/101-003.md"])
        self.assertEqual({r["item"]["title"] for r in 块列表}, {"二", "三", "四"},
                         "老块一个都不能被新块覆盖")

    def test_创作归一化容忍真实响应缺FavTime与Author(self):
        from zhihu_oauth.collection_data import creation_item
        # 2026-09-15 线上实测的真实字段集合：无 FavTime、无 Author，数字是 int 不是 str。
        原始 = {"ContentType": "answer", "Url": "https://www.zhihu.com/question/1/answer/3000",
                "CreatedAt": 1750000000, "LikeCount": 12, "CommentCount": 3, "FavoriteCount": 1,
                "Title": "创作标题", "Summary": "摘要"}
        条目 = creation_item(原始)
        self.assertEqual(条目["id"], "answer:3000")
        self.assertEqual((条目["created_at"], 条目["collected_at"]), (1750000000, 1750000000))
        self.assertEqual((条目["author"], 条目["type"]), (None, "answer"))
        self.assertNotIn("FavTime", 原始)

    def test_创作落库幂等按类型分组且不并进收藏(self):
        self.login(self.a)
        self.repository.import_page("stable-a", {"id": "101", "title": "我的收藏"}, [self.卡片条目(11)])
        条目 = [self.创作条目(i, 类型) for i, 类型 in enumerate(("answer", "article", "zvideo"))]
        self.assertEqual(self.repository.import_creations("stable-a", 条目)["added"], 3)
        self.assertEqual(self.repository.import_creations("stable-a", 条目)["added"], 0, "再同步一次不能重复新增")
        # 创作并入 records()（用户要求）：图谱 / 索引 / 检索都能看到，归到「我的创作」标签。
        self.assertEqual(len(self.repository.records("stable-a")), 4, "1 条收藏 + 3 条创作")
        视图 = workspace_creations(self.repository.creations("stable-a"))
        self.assertEqual(视图["统计"], {"收藏夹数": 3, "内容数": 3, "知识块数": 0})
        self.assertEqual([夹["名称"] for 夹 in 视图["收藏夹"]], ["回答", "文章", "视频"])
        首条 = 视图["收藏夹"][0]["内容"][0]
        self.assertEqual((首条["模式"], 首条["作者"]), ("创作", ""), "创作没有作者，也不该编一个出来")
        self.assertEqual(len(self.repository.creations("stable-b")), 0, "别人的创作一条都不能读到")
        图谱 = workspace_graph(self.repository.records("stable-a"))
        self.assertEqual(sorted(n["标题"] for n in 图谱["节点"] if n["类型"] == "标签"),
                         ["我的创作", "我的收藏 · 101"], "创作在图谱里单独成一个星簇")
        创作标签 = next(n for n in 图谱["节点"] if n["标题"] == "我的创作")
        self.assertEqual(创作标签["收藏夹id"], "creations", "整组删除要走这个固定 id")
        索引 = workspace_index(self.repository.records("stable-a"))
        self.assertIn("creation", {a["内容模式"] for a in 索引["文章"]})

    def test_创作接口要求工作区会话且参数必须合法(self):
        self.login(self.a)
        self.assert_error(self.a.get("/api/workspace/创作"), "workspace_session_changed", 409)
        self.assertEqual(self.client().get("/api/workspace/创作").status_code, 401)
        self.assert_error(self.a.post("/api/workspace/import-creations", json={"offset": "0"}), "origin_rejected", 403)
        self.assert_error(self.a.post("/api/workspace/import-creations", json={"offset": "0"},
                                     headers={"Origin": TEST_SETTINGS.origin}), "csrf_rejected", 403)
        for 坏参数 in ({"offset": "0", "content_type": "瞎写的"}, {"offset": "0", "别的": 1}):
            self.assert_error(self.a.post("/api/workspace/import-creations", json=坏参数, headers=self.headers(self.a)),
                              "invalid_creation_request", 400)
        self.assertEqual(len(self.repository.creations("stable-a")), 0, "校验失败时一条都不能同步")

    def 卡片条目(self, 编号: int, **覆盖) -> dict:
        """归一化后的快照条目（id / 原文链接必须自洽，否则 import_page 会拒绝）。"""
        条目 = {"id": f"answer:{编号}", "type": "answer",
                "url": f"https://www.zhihu.com/question/1/answer/{编号}",
                "title": f"标题{编号}", "summary": f"摘要{编号}。" * 4, "author": f"作者{编号}",
                "created_at": 1, "collected_at": 2, "like_count": "7", "comment_count": "3",
                "favorite_count": "1"}
        条目.update(覆盖)
        return 条目

    def test_per_user_limit_is_atomic_and_does_not_limit_other_users(self):
        self.login(self.a)
        self.login(self.b, "b")
        with patch("zhihu_oauth.workspace.WORKSPACE_LIMIT", 1):
            self.assertEqual(self.collect(self.a).status_code, 200)
            self.assertEqual(self.collect(self.a).status_code, 200)
            self.assert_error(self.collect(self.a, offset="00027"), "workspace_limit_reached", 409)
            self.assertEqual(self.collect(self.b, "202").status_code, 200)
        self.assertEqual(len(self.repository.records("stable-a")), 1)
        self.assertEqual(len(self.repository.records("stable-b")), 1)

    def test_database_error_is_sanitized(self):
        self.login(self.a)
        self.repository.path = Path(self.tmp.name)  # Opening a directory as a DB must fail safely.
        self.assert_error(self.read(self.a), "workspace_unavailable", 503)


if __name__ == "__main__":
    unittest.main()
