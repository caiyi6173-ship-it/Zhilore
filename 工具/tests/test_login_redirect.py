"""returnTo contracts; synthetic identities only, for both supported entry points."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from zhihu_oauth.app import create_app
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.safe_redirect import safe_return_path
from zhihu_oauth.store import FLOW_TTL, SessionStore
from zhihu_oauth.workspace import WorkspaceRepository
from test_oauth import Clock, FakeProvider, TEST_SETTINGS

spec = importlib.util.spec_from_file_location("integrated_return_to", TOOLS / "应用.py")
integrated = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrated)
CASES = json.loads((Path(__file__).parent / "return_to_cases.json").read_text(encoding="utf-8"))


class SafeReturnPathTests(unittest.TestCase):
    def test_shared_browser_server_corpus(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertEqual(safe_return_path(case["input"], "/fallback"), case["expected"] or "/fallback")

    def test_untrusted_default_cannot_escape_factory(self):
        settings, store = TEST_SETTINGS, SessionStore(Clock())
        app = create_app(settings, FakeProvider(store.clock), store, default_return_to="//evil.example")
        with TestClient(app, base_url=settings.origin) as client:
            response = client.get("/login")
            self.assertIn('data-default-return-to="/workspace/"', response.text)


class RedirectFlowContract:
    integrated_mode = False
    default = "/workspace/"

    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(self.clock)
        self.provider = FakeProvider(self.clock)
        self.directory = tempfile.TemporaryDirectory(prefix="return-to-tests-")
        self.addCleanup(self.directory.cleanup)
        if self.integrated_mode:
            knowledge = SimpleNamespace(app=FastAPI())
            @knowledge.app.get("/api/索引")
            async def index():
                return {"文章": [], "入口": []}
            # 必须把仓库落在临时目录：一体化入口默认用 数据/用户工作区.sqlite3，
            # 测试与预览夹具都不能把假数据写进本机开发库。
            self.workspace = WorkspaceRepository(Path(self.directory.name) / "integrated.sqlite3")
            self.app = integrated.建一体化应用(TEST_SETTINGS, self.store, knowledge, self.provider,
                                          工作区仓库=self.workspace)
        else:
            self.app = create_app(TEST_SETTINGS, self.provider, self.store,
                                  WorkspaceRepository(Path(self.directory.name) / "fixture.sqlite3"))
        self.a, self.b = self.client(), self.client()

    def client(self):
        client = TestClient(self.app, base_url=TEST_SETTINGS.origin)
        self.addCleanup(client.close)
        return client

    def start(self, target=None, client=None, query=""):
        response = (client or self.a).post("/auth/zhihu/start" + query,
            json={} if target is None else {"return_to": target}, headers={"Origin": TEST_SETTINGS.origin})
        self.assertEqual(response.status_code, 200, response.text)
        return parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]

    def callback(self, state, client=None, **query):
        return (client or self.a).get("/auth/zhihu/callback", params={"state": state, **query}, follow_redirects=False)

    def login(self, target=None, client=None, account="a"):
        return self.callback(self.start(target, client), client, authorization_code=account)

    def assert_login(self, response, target, result=None):
        self.assertEqual(response.status_code, 303)
        url = urlsplit(response.headers["location"])
        self.assertEqual(url.path, "/login")
        self.assertFalse(url.netloc or url.scheme or url.fragment)
        values = parse_qs(url.query)
        self.assertEqual(values["returnTo"], [target])
        if result:
            self.assertEqual(values["result"], [result])

    def test_login_page_and_shared_asset_are_public(self):
        for url in ("/login", "/login/", "/assets/login-redirect.js"):
            response = self.a.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertNotIn(TEST_SETTINGS.app_key, response.text)
        self.assertIn('data-default-return-to="' + self.default + '"', self.a.get("/login").text)

    def test_account_page_offers_official_collection_visibility_link(self):
        # 私密夹读不到是开放接口的契约（favlist_contents 只返回公开内容）。
        # 页面只能给出知乎官方入口，不做任何绕过读取。
        page = self.a.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn('href="https://www.zhihu.com/collections/mine"', page.text)
        self.assertIn('rel="noopener noreferrer"', page.text)
        self.assertIn("私密收藏夹不在开放范围", page.text)

    def test_account_page_stays_reachable_after_login(self):
        # /login bounces an authenticated visitor to its destination, so the
        # collection list and the "import this page" action need their own route.
        self.assert_login(self.a.get("/account", follow_redirects=False), "/account", "login_required")
        self.assert_login(self.a.get("/account/", follow_redirects=False), "/account/", "login_required")
        self.login()
        for url in ("/account", "/account/"):
            page = self.a.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn('data-default-return-to="/account"', page.text)
            self.assertIn('id="collections"', page.text)
            self.assertIn('id="import-page"', page.text)
            self.assertNotIn(TEST_SETTINGS.app_key, page.text)
            # The account page still hands off to the per-account summary workspace.
            self.assertIn('href="/workspace/"', page.text)
            # 入口页不放「用自己的 Access Secret」设置入口：它属于「知乎收藏」页的横幅
            # （见 test_workspace.py 的横幅断言），这里守住"没有多出来一个入口"。
            self.assertNotIn('href="/settings"', page.text)
            # 服务端配置面板与双账号验收说明都从入口页撤掉，只保留账号与收藏两件事。
            self.assertNotIn('id="configuration"', page.text)
            self.assertNotIn('verification-note', page.text)
            self.assertNotIn('privacy-copy', page.text)
            # 但页面最底下保留了作者的开发感想（用户要求的手写体手记）——那是给读者看的，
            # 和上面三块"维护者自查用"的东西不是一回事，别一起清掉。
            self.assertIn('class="reflection"', page.text)
            self.assertIn("人外有人，我也会一直在路上", page.text)
            self.assertIn("界面 UI 的流程美观程度", page.text)
            self.assertIn("「想法记录」「想法开发」", page.text)

    def test_guard_keeps_path_and_query_while_api_stays_401(self):
        target = "/workspace/?view=graph&q=%E6%94%B6%E8%97%8F&sort=new"
        self.assert_login(self.a.get(target, follow_redirects=False), target, "login_required")
        self.assertEqual(self.a.get("/api/workspace/索引").status_code, 401)

    def test_legacy_root_return_to_link_goes_to_login_not_a_fake_target_page(self):
        response = self.a.get("/", params={"returnTo": "/workspace/?q=rag#graph"}, follow_redirects=False)
        self.assert_login(response, "/workspace/?q=rag#graph")

    def test_success_keeps_query_and_fragment_and_refresh_is_authenticated(self):
        target = "/workspace/?view=graph#tag=fixture"
        response = self.login(target)
        self.assertEqual(response.headers["location"], target)
        self.assertEqual(self.a.get(target).status_code, 200)
        self.assertEqual(self.a.get("/api/me").json()["user"]["id"], "stable-a")

    def test_default_and_root_destinations_are_not_lost(self):
        self.assertEqual(self.login().headers["location"], self.default)
        self.assertEqual(self.login("/").headers["location"], "/")
        self.assertEqual(self.login("/?view=all#graph").headers["location"], "/?view=all#graph")

    def test_already_logged_in_login_page_returns_without_another_oauth_request(self):
        self.login()
        for target in ("/", "/workspace/?q=rag#graph"):
            response = self.a.get("/login", params={"returnTo": target}, follow_redirects=False)
            self.assertEqual(response.headers["location"], target)
        self.assertEqual(len(self.provider.exchanged), 1)

    def test_unsafe_targets_use_default_in_callback_and_authenticated_login(self):
        for case in CASES:
            if case["expected"] is not None or not isinstance(case["input"], str) or "\ud800" in case["input"]:
                continue
            with self.subTest(case=case["name"]):
                self.assertEqual(self.login(case["input"]).headers["location"], self.default)
                response = self.a.get("/login", params={"returnTo": case["input"]}, follow_redirects=False)
                self.assertEqual(response.headers["location"], self.default)

    def test_duplicate_return_to_is_not_ambiguous(self):
        self.login()
        response = self.a.get("/login?returnTo=%2Fworkspace%2F&returnTo=%2F", follow_redirects=False)
        self.assertEqual(response.headers["location"], self.default)

    def test_callback_and_start_query_cannot_overwrite_state_bound_target(self):
        state = self.start("/workspace/?q=original#graph", query="?returnTo=%2Fignored")
        response = self.callback(state, authorization_code="a", returnTo="//evil.example", return_to="/ignored")
        self.assertEqual(response.headers["location"], "/workspace/?q=original#graph")

    def test_cancel_keeps_destination_and_retry_uses_a_fresh_flow(self):
        target = "/workspace/?q=retry#graph"
        old_state = self.start(target)
        response = self.callback(old_state, error="access_denied", returnTo="//evil.example")
        self.assert_login(response, target, "authorization_cancelled")
        self.assertEqual(self.a.get("/api/me").status_code, 401)
        self.assertEqual(self.provider.exchanged, [])
        new_state = self.start(parse_qs(urlsplit(response.headers["location"]).query)["returnTo"][0])
        self.assertNotEqual(old_state, new_state)
        self.assertEqual(self.callback(new_state, authorization_code="a").headers["location"], target)

    def test_failed_exchange_or_profile_keeps_destination_without_granting_login(self):
        target = "/workspace/#tag=retry"
        for attribute, code in (("exchange_error", "code_exchange_failed"), ("exchange_error", "token_expired"),
                                ("identity_error", "permission_denied")):
            with self.subTest(code=code):
                setattr(self.provider, attribute, OAuthError(code, 401))
                response = self.login(target)
                self.assert_login(response, target, code)
                self.assertEqual(self.a.get("/api/me").status_code, 401)
                setattr(self.provider, attribute, None)

    def test_invalid_missing_expired_and_replayed_state_do_not_trust_callback_target(self):
        state = self.start("/workspace/?q=private")
        response = self.callback("wrong-state", authorization_code="a", returnTo="/ignored")
        self.assert_login(response, self.default, "state_invalid")
        self.assertEqual(self.provider.exchanged, [])
        self.start("/workspace/?q=private")
        self.assert_login(self.callback("", authorization_code="a"), self.default, "state_missing")
        state = self.start("/workspace/?q=private")
        self.clock.now += FLOW_TTL + 1
        self.assert_login(self.callback(state, authorization_code="a"), self.default, "flow_expired")
        state = self.start("/workspace/?q=private")
        self.callback(state, authorization_code="a")
        self.assert_login(self.callback(state, authorization_code="a"), self.default, "state_invalid")

    def test_two_browsers_keep_independent_destinations_and_identities(self):
        a_state = self.start("/workspace/?q=A", self.a)
        b_state = self.start("/workspace/?q=B", self.b)
        self.assertEqual(self.callback(b_state, self.b, authorization_code="b").headers["location"], "/workspace/?q=B")
        self.assertEqual(self.callback(a_state, self.a, authorization_code="a").headers["location"], "/workspace/?q=A")
        self.assertEqual(self.a.get("/api/me").json()["user"]["id"], "stable-a")
        self.assertEqual(self.b.get("/api/me").json()["user"]["id"], "stable-b")

    def test_expired_session_returns_to_requested_page_after_reauthorization(self):
        self.login()
        self.clock.now += 601
        target = "/workspace/?view=graph"
        response = self.a.get(target, follow_redirects=False)
        self.assert_login(response, target, "token_expired")
        self.assertEqual(self.login(target).headers["location"], target)

    def test_collection_permissions_do_not_break_login_or_its_destination(self):
        self.provider.read_error = OAuthError("permission_denied", 403)
        self.assertEqual(self.login("/workspace/?q=allowed").headers["location"], "/workspace/?q=allowed")
        self.assertEqual(self.a.get("/api/collections").status_code, 403)
        self.assertEqual(self.a.get("/api/me").status_code, 200)
        self.assertEqual(self.a.get("/workspace/").status_code, 200)

    def test_lifespan_clears_sessions_for_this_entry_point(self):
        with TestClient(self.app, base_url=TEST_SETTINGS.origin) as client:
            self.login(client=client)
            handle = client.cookies.get(TEST_SETTINGS.session_cookie)
            self.store.get(handle)
        with self.assertRaises(OAuthError):
            self.store.get(handle)


class PersonalRedirectTests(RedirectFlowContract, unittest.TestCase):
    pass


class IntegratedRedirectTests(RedirectFlowContract, unittest.TestCase):
    integrated_mode = True
    # 一体化入口登录后默认回账号页，由用户自己决定去知识库还是工作区。
    default = "/account"

    def test_一体化入口把工作区仓库透传给授权应用(self):
        """回归：漏传仓库会让预览夹具/测试把假知识块写进本机 数据/用户工作区.sqlite3。"""
        捕获 = {}
        真正的建OAuth应用 = integrated.建OAuth应用

        def 记录参数(**kwargs):
            捕获.update(kwargs)
            return 真正的建OAuth应用(**kwargs)

        仓库 = WorkspaceRepository(Path(self.directory.name) / "captured.sqlite3")
        with patch.object(integrated, "建OAuth应用", 记录参数):
            integrated.建一体化应用(TEST_SETTINGS, self.store,
                                 SimpleNamespace(app=FastAPI()), self.provider, 工作区仓库=仓库)
        self.assertIs(捕获.get("workspace"), 仓库, "一体化入口必须把注入的仓库继续往下传")

        # 不传时保持原行为：授权应用自己建默认仓库（生产路径不变）。
        捕获.clear()
        with patch.object(integrated, "建OAuth应用", 记录参数):
            integrated.建一体化应用(TEST_SETTINGS, self.store,
                                 SimpleNamespace(app=FastAPI()), self.provider)
        self.assertIsNone(捕获.get("workspace"))

    def test_root_guard_preserves_query_and_static_pages_redirect_without_exposing_assets(self):
        self.assert_login(self.a.get("/?view=graph", follow_redirects=False), "/?view=graph", "login_required")
        self.assert_login(self.a.get("/index.html?q=rag", follow_redirects=False), "/index.html?q=rag", "login_required")
        for url in ("/app.js", "/笔记库/wiki/fixture.md", "/oauth/integrated-session.js"):
            self.assertEqual(self.a.get(url, follow_redirects=False).status_code, 401)
        self.assertEqual(self.login().headers["location"], self.default)
        首页 = self.a.get("/").text
        # 「个人知识库」与「知乎收藏」现在是同一套账号隔离视图：
        # 核验账号的闸门、顶部账号栏都要在。
        self.assertIn('id="workspace-gate"', 首页)
        self.assertIn('id="workspace-account"', 首页)
        self.assertIn('个人知识库 <span>/ 账号隔离</span>', 首页)
        self.assertIn('href="/account"', 首页)
        # 处理入口在「知乎收藏」，个人知识库里给一条回去的路，形成闭环。
        self.assertIn('href="/workspace/"', 首页)
        # 直答处理横幅只属于「知乎收藏」(/workspace/)，不在个人知识库里重复出现。
        self.assertNotIn('id="zh-import"', 首页)
        # 分工：`/` 是知识库（图谱布局 + 知识库前端），`/workspace/` 是原始收藏的卡片页。
        self.assertIn('class="layout"', 首页)
        self.assertIn("graph.js", 首页)
        self.assertNotIn('id="collections"', 首页)
        收藏页 = self.a.get("/workspace/").text
        self.assertIn('id="zh-import"', 收藏页)
        self.assertIn('知乎收藏 <span>/ 个人知识库</span>', 收藏页)
        self.assertIn('id="collections"', 收藏页)
        self.assertIn("collections-page", 收藏页)
        self.assertNotIn('class="layout"', 收藏页)
        self.assertNotIn("graph.js", 收藏页)

    def test_未登录打开知识库首页时登录后落在账号页(self):
        """用户的诉求：授权完先看到账号页（绑定账号、选收藏夹），而不是直接进知识库。"""
        self.assert_login(self.a.get("/", follow_redirects=False), "/account", "login_required")
        self.assert_login(self.a.get("/index.html", follow_redirects=False), "/account", "login_required")
        # 带参数的链接仍然回原处，returnTo 契约不变。
        self.assert_login(self.a.get("/?view=graph", follow_redirects=False), "/?view=graph", "login_required")


if __name__ == "__main__":
    unittest.main()
