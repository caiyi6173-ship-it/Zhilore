# -*- coding: utf-8 -*-
"""Public collection tests. All accounts, tokens and responses are synthetic."""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zhihu_oauth.app import create_app
from zhihu_oauth.collection_data import (
    MAX_INT64, CONTENT_PAGE_SIZE, collection_request, public_collections,
    public_content_page,
)
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.provider import COLLECTION_CONTENTS_URL, ZhihuProvider
from zhihu_oauth.store import SessionStore
from test_oauth import Clock, TEST_SETTINGS, FakeProvider, collection


def raw_collection(identifier=101, public=True):
    return {"UrlToken": identifier, "Title": "公开测试夹", "Description": "模拟数据",
            "IsPublic": public, "Url": "javascript:untrusted()"}


def raw_item(identifier=1, kind="answer", **fields):
    paths = {"answer": f"https://www.zhihu.com/answer/{identifier}",
             "article": f"https://zhuanlan.zhihu.com/p/{identifier}",
             "question": f"https://www.zhihu.com/question/{identifier}",
             "pin": f"https://www.zhihu.com/pin/{identifier}",
             "zvideo": f"https://www.zhihu.com/zvideo/{identifier}"}
    return {"ContentType": kind, "Url": paths[kind], "Title": "模拟标题",
            "Summary": "模拟摘要，不是真实知乎内容", "CreatedAt": 1745486539,
            "FavTime": 1746000000, "LikeCount": 128, "CommentCount": 12,
            "FavoriteCount": 20, "Author": {"Name": "模拟作者", "UrlToken": "unused"},
            "Favlists": [{"Title": "不可传回的关联夹名称"}], **fields}


def payload(items=None, *, end=True, next_offset="0", total=1):
    return {"Code": 0, "Data": {"Items": items if items is not None else [raw_item()],
            "Paging": {"IsEnd": end, "NextOffset": next_offset, "Totals": total}}}


class PublicDataTests(unittest.TestCase):
    def protocol_error(self, fn, *args):
        with self.assertRaises(OAuthError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, "upstream_protocol_error")

    def test_only_explicitly_public_collections_are_copied(self):
        private = {"IsPublic": False, "Title": "private-title-MUST-NOT-LEAK"}
        result = public_collections({"Code": 0, "Data": {"Items": [private, raw_collection()]}})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://www.zhihu.com/collection/101")
        self.assertTrue(result[0]["is_public"])
        self.assertNotIn("private-title", json.dumps(result))

    def test_unknown_visibility_fails_closed(self):
        for value in (None, "true", "false", 1, 0, [], {}):
            with self.subTest(value=value):
                self.protocol_error(public_collections, {"Code": 0, "Data": {"Items": [raw_collection(public=value)]}})
        item = raw_collection()
        del item["IsPublic"]
        self.protocol_error(public_collections, {"Code": 0, "Data": {"Items": [item]}})

    def test_collection_envelopes_limits_and_duplicate_ids(self):
        for value in (None, [], {}, {"Code": False}, {"Code": "0"},
                      {"Code": 0, "Data": {"Items": "not-list"}},
                      {"Code": 0, "Data": {"Items": [None]}},
                      {"Code": 0, "Data": {"Items": [raw_collection()] * 51}},
                      {"Code": 0, "Data": {"Items": [raw_collection(), raw_collection()]}}):
            with self.subTest(value=str(value)[:100]):
                self.protocol_error(public_collections, value)

    def test_int64_collection_id_is_not_rounded(self):
        result = public_collections({"Code": 0, "Data": {"Items": [raw_collection(MAX_INT64)]}})
        self.assertEqual(result[0]["id"], str(MAX_INT64))
        self.assertEqual(result[0]["url"], f"https://www.zhihu.com/collection/{MAX_INT64}")

    def test_request_rejects_noncanonical_ids_but_preserves_cursor(self):
        self.assertEqual(collection_request(str(MAX_INT64), "00020"), (str(MAX_INT64), "00020"))
        for identifier, offset in (("0", "0"), ("01", "0"), ("-1", "0"), ("../1", "0"),
                                   (str(MAX_INT64 + 1), "0"), ("1", "1e2"), ("1", " 20"),
                                   ("1", "-1"), ("1", str(MAX_INT64 + 1)), (1, "0"), ("1", 0),
                                   (True, "0"), ("1", False), ("1", "")):
            with self.subTest(identifier=identifier, offset=offset):
                with self.assertRaises(OAuthError) as caught:
                    collection_request(identifier, offset)
                self.assertEqual((caught.exception.code, caught.exception.status), ("invalid_collection_request", 400))

    def test_five_content_types_and_tracking_removal(self):
        for kind in ("answer", "article", "question", "pin", "zvideo"):
            item = raw_item(123, kind)
            item["Url"] += "?source=test#part"
            with self.subTest(kind=kind):
                result = public_content_page(payload([item]), "0").items[0]
                self.assertEqual(result["id"], kind + ":123")
                self.assertNotIn("?", result["url"])
                self.assertNotIn("#", result["url"])
                self.assertEqual(result["author"], "模拟作者")

    def test_unrelated_favlists_and_author_profile_are_not_relayed(self):
        item = raw_item(Author={"Name": "模拟作者", "Url": "https://untrusted.invalid", "Headline": "not-needed"})
        result = public_content_page(payload([item]), "0").items[0]
        self.assertEqual(set(result), {"id", "type", "url", "title", "summary", "author",
                                      "created_at", "collected_at", "like_count", "comment_count", "favorite_count"})
        self.assertNotIn("关联夹", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("not-needed", json.dumps(result))

    def test_optional_author_empty_summary_and_bounded_text(self):
        item = raw_item(Title="题" * 301, Summary="文" * 4001, Author={"Name": "名" * 201})
        result = public_content_page(payload([item]), "0").items[0]
        self.assertEqual([len(result[key]) for key in ("title", "summary", "author")], [300, 4000, 200])
        for author in (None, "missing"):
            item = raw_item(Title="", Summary="", Author=author)
            if author == "missing":
                del item["Author"]
            self.assertIsNone(public_content_page(payload([item]), "0").items[0]["author"])

    def test_content_urls_cannot_escape_zhihu_content_paths(self):
        urls = ["http://www.zhihu.com/answer/1", "javascript:alert(1)", "//www.zhihu.com/answer/1",
                "https://evil.test/answer/1", "https://www.zhihu.com.evil.test/answer/1",
                "https://user:password@www.zhihu.com/answer/1", "https://www.zhihu.com:444/answer/1",
                "https://www.zhihu.com:bad/answer/1", "https://www.zhihu.com/answer/1/../2",
                "https://www.zhihu.com/answer/%31", "https://www.zhihu.com/collection/1",
                "https://www.zhihu.com/answer/1\n", "https://www.zhihu.com/answer/1?x=中文",
                "https://www.zhihu.com\\@evil.test/answer/1", "https://www.zhihu.com/answer/0"]
        for url in urls:
            with self.subTest(url=url):
                self.protocol_error(public_content_page, payload([raw_item(Url=url)]), "0")
        result = public_content_page(payload([raw_item(Url="https://www.zhihu.com:443/question/2/answer/1")]), "0")
        self.assertEqual(result.items[0]["id"], "answer:1")

    def test_content_counts_and_totals_keep_int64_precision(self):
        result = public_content_page(payload([raw_item(LikeCount=MAX_INT64, CommentCount=str(MAX_INT64))], total=MAX_INT64), "0")
        self.assertEqual(result.items[0]["like_count"], str(MAX_INT64))
        self.assertEqual(result.items[0]["comment_count"], str(MAX_INT64))
        self.assertEqual(result.public()["paging"]["total"], str(MAX_INT64))

    def test_invalid_content_fields_fail_the_whole_page(self):
        fields = {"ContentType": ["unknown", None, []], "Title": [None, 1], "Summary": [None, []],
                  "Author": ["name", {"Name": None}], "CreatedAt": [True, -1, 253402300800],
                  "FavTime": [1.5, "yesterday"], "LikeCount": [True, -1, 1.1, str(MAX_INT64 + 1)]}
        for field, values in fields.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.protocol_error(public_content_page, payload([raw_item(), raw_item(**{field: value})]), "0")
        item = raw_item()
        del item["Summary"]
        self.protocol_error(public_content_page, payload([item]), "0")

    def test_cursor_is_preserved_not_calculated_from_limit(self):
        page = public_content_page(payload(end=False, next_offset="00027", total=100), "0")
        self.assertEqual(page.public()["paging"], {"offset": "0", "limit": 20, "has_more": True,
                                                 "next_offset": "00027", "total": "100"})
        self.assertEqual(public_content_page(payload(end=False, next_offset=str(MAX_INT64)), "9007199254740993").next_offset, str(MAX_INT64))

    def test_empty_nonfinal_page_and_final_page(self):
        page = public_content_page(payload([], end=False, next_offset="20", total=50), "0")
        self.assertEqual(page.items, [])
        self.assertEqual(page.next_offset, "20")
        final = public_content_page(payload([], end=True, next_offset="unused", total=0), "20")
        self.assertIsNone(final.next_offset)
        self.assertFalse(final.public()["paging"]["has_more"])

    def test_duplicate_content_urls_are_deduplicated_by_type_and_id(self):
        first = raw_item(Title="first")
        alternate = raw_item(Title="second", Url="https://www.zhihu.com/question/2/answer/1")
        page = public_content_page(payload([first, alternate, raw_item(1, "article")]), "0")
        self.assertEqual([item["id"] for item in page.items], ["answer:1", "article:1"])
        self.assertEqual(page.items[0]["title"], "first")

    def test_invalid_paging_is_rejected_without_infinite_loop(self):
        for next_offset in (None, 20, True, "", "20", "00020", "19", "-1", "20.5", "20e2", str(MAX_INT64 + 1)):
            with self.subTest(next_offset=next_offset):
                self.protocol_error(public_content_page, payload(end=False, next_offset=next_offset), "20")
        for field, value in (("IsEnd", 1), ("IsEnd", "false"), ("Totals", -1), ("Totals", True)):
            data = payload()
            data["Data"]["Paging"][field] = value
            self.protocol_error(public_content_page, data, "0")
        data = payload()
        del data["Data"]["Paging"]
        self.protocol_error(public_content_page, data, "0")
        self.protocol_error(public_content_page, payload([raw_item()] * (CONTENT_PAGE_SIZE + 1)), "0")


class ContentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_token_and_unrounded_cursor_are_forwarded_in_fixed_headers(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = ZhihuProvider(TEST_SETTINGS, client, Clock())
            result = await provider.collection_contents("mock-token-a", str(MAX_INT64), "09007199254740993")
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(str(request.url).split("?")[0], COLLECTION_CONTENTS_URL)
        self.assertEqual(dict(request.url.params), {"FavlistUrlToken": str(MAX_INT64), "Offset": "09007199254740993", "Limit": "20"})
        self.assertEqual(request.headers["X-OAuth-Token"], "mock-token-a")
        self.assertEqual(request.headers["Authorization"], "Bearer " + TEST_SETTINGS.access_secret)
        self.assertEqual(request.headers["X-Request-Timestamp"], "1800000000")
        self.assertNotIn("mock-token", str(request.url))
        self.assertEqual(result.offset, "09007199254740993")

    async def test_no_token_or_invalid_request_never_reaches_platform(self):
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
            provider = ZhihuProvider(TEST_SETTINGS, client)
            for token in (None, "", "token\r\nInjected: yes", []):
                with self.subTest(token=token), self.assertRaises(OAuthError):
                    await provider.collection_contents(token, "101", "0")
            with self.assertRaises(OAuthError):
                await provider.collection_contents("mock-token-a", "101", "-1")
        self.assertEqual(calls, [])

    async def test_http_and_business_errors_are_safe_and_do_not_follow_redirects(self):
        cases = [(401, {}, "authorization_failed"), (403, {}, "permission_denied"),
                 (404, {}, "collection_unavailable"), (429, {}, "rate_limited"),
                 (503, {}, "upstream_unavailable"), (302, {}, "upstream_protocol_error"),
                 (200, {"Code": 20001}, "authorization_failed"), (200, {"Code": 30001}, "rate_limited"),
                 (200, {"Code": 30002}, "quota_exceeded"), (200, {"Code": 90001}, "upstream_unavailable"),
                 (200, {"Code": 10001}, "upstream_protocol_error"),
                 (200, {"error": "insufficient_scope"}, "permission_denied")]
        for status, body, expected in cases:
            with self.subTest(status=status, expected=expected):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, json={**body, "raw_secret": "not-for-browser"}, headers={"Location": "https://evil.test"})
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
                    provider = ZhihuProvider(TEST_SETTINGS, client)
                    with self.assertRaises(OAuthError) as caught:
                        await provider.collection_contents("mock-token-a", "101", "0")
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("not-for-browser", json.dumps(caught.exception.public()))

    async def test_network_error_is_sanitized(self):
        def handler(request):
            raise httpx.ReadTimeout("secret-response", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(OAuthError) as caught:
                await ZhihuProvider(TEST_SETTINGS, client).collection_contents("mock-token-a", "101", "0")
        self.assertEqual(caught.exception.code, "upstream_unavailable")
        self.assertNotIn("secret-response", str(caught.exception))


class PublicFakeProvider(FakeProvider):
    def __init__(self, clock):
        super().__init__(clock)
        self.content_reads = []
        self.content_errors = {}
        self.on_content = None
        self.pages = {
            ("mock-token-a", "101", "0"): payload([raw_item(11, Title="A 摘要测试")], end=False, next_offset="00027", total=2),
            ("mock-token-a", "101", "00027"): payload([raw_item(12, Title="A 第二页")], total=2),
            ("mock-token-b", "202", "0"): payload([raw_item(21, Title="B 摘要测试")]),
        }

    async def collection_contents(self, token, identifier, offset):
        self.content_reads.append((token, identifier, offset))
        if self.on_content:
            self.on_content()
        if token in self.content_errors:
            raise self.content_errors[token]
        return public_content_page(copy.deepcopy(self.pages[(token, identifier, offset)]), offset)


class PublicCollectionsAppTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(self.clock)
        self.provider = PublicFakeProvider(self.clock)
        self.app = create_app(TEST_SETTINGS, self.provider, self.store)
        self.a = TestClient(self.app, base_url=TEST_SETTINGS.origin)
        self.b = TestClient(self.app, base_url=TEST_SETTINGS.origin)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def login(self, client, account="a"):
        start = client.post("/auth/zhihu/start", json={}, headers={"Origin": TEST_SETTINGS.origin})
        self.assertEqual(start.status_code, 200)
        state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
        response = client.get("/auth/zhihu/callback", params={"state": state, "authorization_code": account}, follow_redirects=False)
        self.assertEqual(response.headers["location"], "/workspace/")
        return client.get("/api/me").json()

    def assert_error(self, response, code, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertEqual(set(response.json()), {"error"})
        self.assertIn("no-store", response.headers["cache-control"])
        for secret in ("mock-token-a", "mock-token-b", TEST_SETTINGS.app_key, TEST_SETTINGS.access_secret):
            self.assertNotIn(secret, response.text)

    def test_unauthenticated_contents_never_reads_platform(self):
        self.assert_error(self.a.get("/api/collections/101/contents"), "login_required", 401)
        self.assertEqual(self.provider.reads, [])
        self.assertEqual(self.provider.content_reads, [])

    def test_each_account_reads_only_its_accessible_public_collection(self):
        self.login(self.a)
        self.login(self.b, "b")
        for client, identifier, subject, title in ((self.a, "101", "stable-a", "A 摘要测试"), (self.b, "202", "stable-b", "B 摘要测试")):
            result = client.get(f"/api/collections/{identifier}/contents")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["user"]["id"], subject)
            self.assertEqual(result.json()["items"][0]["title"], title)
            self.assertEqual(result.json()["scope"], "public")
        self.assert_error(self.a.get("/api/collections/202/contents"), "collection_unavailable", 404)
        self.assert_error(self.b.get("/api/collections/101/contents"), "collection_unavailable", 404)
        self.assertEqual(self.provider.content_reads, [("mock-token-a", "101", "0"), ("mock-token-b", "202", "0")])

    def test_shared_public_collection_is_valid_for_both_accounts(self):
        self.provider.items["mock-token-b"].append(collection(101, "共享公开夹"))
        self.provider.pages[("mock-token-b", "101", "0")] = payload()
        self.login(self.a)
        self.login(self.b, "b")
        self.assertEqual(self.a.get("/api/collections/101/contents").status_code, 200)
        self.assertEqual(self.b.get("/api/collections/101/contents").status_code, 200)
        self.assertEqual([call[0] for call in self.provider.content_reads], ["mock-token-a", "mock-token-b"])

    def test_private_or_unknown_collection_never_reads_contents(self):
        self.login(self.a)
        self.provider.items["mock-token-a"].append({**collection(303, "private-MUST-NOT-LEAK"), "is_public": False})
        listing = self.a.get("/api/collections")
        self.assertNotIn("private-MUST-NOT-LEAK", listing.text)
        for identifier in ("303", "999"):
            self.assert_error(self.a.get(f"/api/collections/{identifier}/contents"), "collection_unavailable", 404)
        self.assertEqual(self.provider.content_reads, [])

    def test_invalid_or_duplicate_offset_stops_before_upstream(self):
        self.login(self.a)
        for suffix in ("101/contents?offset=1&offset=2", "101/contents?offset=-1", "01/contents", "0/contents", f"{MAX_INT64 + 1}/contents", "101/contents?offset=1e2"):
            self.assert_error(self.a.get("/api/collections/" + suffix), "invalid_collection_request", 400)
        self.assertEqual(self.provider.reads, [])
        self.assertEqual(self.provider.content_reads, [])

    def test_client_cannot_override_user_token_or_upstream_url(self):
        self.login(self.a)
        response = self.a.get("/api/collections/101/contents", params={"user": "stable-b", "token": "mock-token-b", "url": "https://evil.test"},
                              headers={"X-OAuth-Token": "mock-token-b"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], "stable-a")
        self.assertEqual(self.provider.content_reads, [("mock-token-a", "101", "0")])

    def test_each_page_rechecks_membership_and_preserves_cursor(self):
        self.login(self.a)
        first = self.a.get("/api/collections/101/contents").json()
        cursor = first["paging"]["next_offset"]
        self.assertEqual(cursor, "00027")
        second = self.a.get("/api/collections/101/contents", params={"offset": cursor}).json()
        self.assertFalse(second["paging"]["has_more"])
        self.assertEqual(self.provider.reads, ["mock-token-a", "mock-token-a"])
        self.assertEqual(self.provider.content_reads[-1], ("mock-token-a", "101", "00027"))
        self.provider.items["mock-token-a"][0]["is_public"] = False
        self.assert_error(self.a.get("/api/collections/101/contents?offset=00027"), "collection_unavailable", 404)
        self.assertEqual(len(self.provider.content_reads), 2)

    def test_token_expired_before_request_never_reaches_platform(self):
        self.login(self.a)
        self.clock.now += 601
        self.assert_error(self.a.get("/api/collections/101/contents"), "token_expired", 401)
        self.assertEqual(self.provider.reads, [])

    def test_expiry_during_membership_suppresses_content_call(self):
        self.login(self.a)
        self.provider.on_read = lambda: setattr(self.clock, "now", self.clock.now + 601)
        self.assert_error(self.a.get("/api/collections/101/contents"), "token_expired", 401)
        self.assertEqual(self.provider.content_reads, [])

    def test_expiry_during_content_suppresses_response(self):
        self.login(self.a)
        self.provider.on_content = lambda: setattr(self.clock, "now", self.clock.now + 601)
        self.assert_error(self.a.get("/api/collections/101/contents"), "token_expired", 401)

    def test_revocation_during_membership_or_content_suppresses_response(self):
        for phase in ("on_read", "on_content"):
            with self.subTest(phase=phase):
                self.login(self.a)
                handle = self.a.cookies.get(TEST_SETTINGS.session_cookie)
                setattr(self.provider, phase, lambda: self.store.revoke(handle))
                self.assert_error(self.a.get("/api/collections/101/contents"), "session_expired", 401)
                setattr(self.provider, phase, None)

    def test_content_errors_do_not_revoke_other_users_or_leak_raw_data(self):
        self.login(self.b, "b")
        cases = [("permission_denied", 403), ("collection_unavailable", 404), ("rate_limited", 429),
                 ("quota_exceeded", 429), ("upstream_unavailable", 502), ("upstream_protocol_error", 502),
                 ("authorization_failed", 401)]
        for code, status in cases:
            with self.subTest(code=code):
                self.login(self.a)
                self.provider.content_errors["mock-token-a"] = OAuthError(code, status)
                response = self.a.get("/api/collections/101/contents")
                self.assert_error(response, code, status)
                self.assertEqual(self.b.get("/api/collections/202/contents").status_code, 200)
                self.assertEqual(self.a.get("/api/me").status_code, 401 if status == 401 else 200)
                if status == 429:
                    self.assertEqual(response.headers["retry-after"], "60")

    def test_status_distinguishes_list_and_content_pagination(self):
        limits = self.a.get("/api/status").json()["limitations"]
        self.assertFalse(limits["pagination_supported"])
        self.assertTrue(limits["content_pagination_supported"])
        self.assertEqual(limits["content_page_size"], 20)
        self.assertFalse(limits["full_text_supported"])


class ContentConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_logout_while_content_is_inflight_cannot_return_data(self):
        clock, started, release = Clock(), asyncio.Event(), asyncio.Event()
        class WaitingProvider(PublicFakeProvider):
            async def collection_contents(self, token, identifier, offset):
                started.set()
                await release.wait()
                return await super().collection_contents(token, identifier, offset)
        provider = WaitingProvider(clock)
        store = SessionStore(clock)
        app = create_app(TEST_SETTINGS, provider, store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=TEST_SETTINGS.origin) as client:
            start = await client.post("/auth/zhihu/start", json={}, headers={"Origin": TEST_SETTINGS.origin})
            state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
            await client.get("/auth/zhihu/callback", params={"state": state, "authorization_code": "a"})
            identity = (await client.get("/api/me")).json()
            pending = asyncio.create_task(client.get("/api/collections/101/contents"))
            await asyncio.wait_for(started.wait(), timeout=2)
            try:
                logged_out = await client.post("/auth/logout", json={}, headers={"Origin": TEST_SETTINGS.origin, "X-CSRF-Token": identity["csrf_token"]})
                self.assertEqual(logged_out.status_code, 200)
            finally:
                release.set()
            result = await asyncio.wait_for(pending, timeout=2)
            self.assertEqual(result.status_code, 401)
            self.assertEqual(result.json()["error"]["code"], "session_expired")
            self.assertNotIn("items", result.json())


if __name__ == "__main__":
    unittest.main()
