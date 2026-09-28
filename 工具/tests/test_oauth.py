# -*- coding: utf-8 -*-
"""Synthetic-provider tests only. These are NOT real Zhihu account authorizations."""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zhihu_oauth.app import create_app
from zhihu_oauth.config import Settings, valid_redirect_uri, valid_profile_url
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.provider import (
    AUTHORIZE_URL, TOKEN_URL, COLLECTIONS_URL, Identity, TokenGrant, ZhihuProvider,
)
from zhihu_oauth.store import SessionStore, FLOW_TTL, SESSION_TTL


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


# Deliberately fictional profile contract; no undocumented endpoint is assumed.
TEST_SETTINGS = Settings(
    app_id="fixture-app-id", app_key="fixture-app-key-NOT-REAL",
    access_secret="fixture-platform-secret-NOT-REAL",
    redirect_uri="https://demo.example.test/auth/zhihu/callback",
    profile_url="https://openapi.zhihu.com/test-only-profile-contract",
    profile_auth="oauth_bearer", profile_id_path="/data/id", profile_name_path="/data/name",
)


def collection(identifier, title):
    return {"id": str(identifier), "title": title, "description": "测试数据，不是真实用户数据",
            "is_public": True, "url": "https://www.zhihu.com/collection/" + str(identifier)}


class FakeProvider:
    def __init__(self, clock, settings=TEST_SETTINGS):
        self.clock, self.settings = clock, settings
        self.exchanged, self.identified, self.reads = [], [], []
        self.users = {"mock-token-a": Identity("stable-a", "同名测试用户"),
                      "mock-token-b": Identity("stable-b", "同名测试用户")}
        self.items = {"mock-token-a": [collection(101, "A 专属测试夹")],
                      "mock-token-b": [collection(202, "B 专属测试夹")]}
        self.exchange_error = None
        self.identity_error = None
        self.read_error = None
        self.on_identify = None
        self.on_read = None

    def authorize_url(self, state):
        return AUTHORIZE_URL + "?" + urlencode({"redirect_uri": self.settings.redirect_uri,
            "app_id": self.settings.app_id, "response_type": "code", "state": state})

    async def exchange_code(self, code):
        self.exchanged.append(code)
        if self.exchange_error:
            raise self.exchange_error
        if code not in ("a", "b"):
            raise OAuthError("code_exchange_failed", 401)
        return TokenGrant("mock-token-" + code, self.clock() + 600)

    async def identify(self, token):
        self.identified.append(token)
        if self.on_identify:
            self.on_identify()
        if self.identity_error:
            raise self.identity_error
        return self.users[token]

    async def collections(self, token):
        self.reads.append(token)
        if self.on_read:
            self.on_read()
        if self.read_error:
            raise self.read_error
        return self.items[token]


class OAuthAppTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.settings = TEST_SETTINGS
        self.store = SessionStore(self.clock)
        self.provider = FakeProvider(self.clock)
        self.app = create_app(self.settings, self.provider, self.store)
        self.a = self.client()
        self.b = self.client()

    def client(self, app=None, base_url=None):
        client = TestClient(app or self.app, base_url=base_url or self.settings.origin)
        self.addCleanup(client.close)
        return client

    def post(self, client, url, **kwargs):
        headers = {"Origin": self.settings.origin, **kwargs.pop("headers", {})}
        return client.post(url, json={}, headers=headers, **kwargs)

    def start(self, client):
        response = self.post(client, "/auth/zhihu/start")
        self.assertEqual(response.status_code, 200, response.text)
        state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
        return state, response

    def callback(self, client, state=None, **params):
        if state is not None:
            params["state"] = state
        return client.get("/auth/zhihu/callback", params=params, follow_redirects=False)

    def login(self, client, account="a"):
        state, _ = self.start(client)
        response = self.callback(client, state, authorization_code=account)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/workspace/")
        return response

    def outcome(self, response):
        if response.headers["location"] == "/workspace/":
            return "connected"
        return parse_qs(urlsplit(response.headers["location"]).query)["result"][0]

    def test_login_primary_callback_and_stable_identity(self):
        self.login(self.a)
        me = self.a.get("/api/me").json()
        self.assertEqual(me["user"], {"id": "stable-a", "name": "同名测试用户", "provider": "zhihu"})
        self.assertTrue(me["csrf_token"])
        self.assertEqual(self.provider.exchanged, ["a"])
        self.assertEqual(self.provider.identified, ["mock-token-a"])
        self.assertNotIn("access_token", me)

    def test_compatible_code_parameter(self):
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, state, code="a")), "connected")

    def test_two_accounts_isolated_even_with_identical_names(self):
        self.login(self.a, "a")
        self.login(self.b, "b")
        a, b = self.a.get("/api/collections").json(), self.b.get("/api/collections").json()
        self.assertEqual([item["id"] for item in a["items"]], ["101"])
        self.assertEqual([item["id"] for item in b["items"]], ["202"])
        self.assertNotEqual(a["user"]["id"], b["user"]["id"])
        self.assertEqual(a["user"]["name"], b["user"]["name"])
        self.assertNotEqual(self.a.cookies.get(self.settings.session_cookie), self.b.cookies.get(self.settings.session_cookie))
        self.assertEqual(self.provider.reads, ["mock-token-a", "mock-token-b"])

    def test_client_cannot_select_another_account_or_supply_a_token(self):
        self.login(self.a, "a")
        response = self.a.get("/api/collections?user_id=stable-b&token=mock-token-b",
                              headers={"X-OAuth-Token": "mock-token-b", "Authorization": "Bearer mock-token-b"})
        self.assertEqual(response.json()["user"]["id"], "stable-a")
        self.assertEqual(self.provider.reads, ["mock-token-a"])

    def test_unauthenticated_request_never_reads_developer_collections(self):
        response = self.a.get("/api/collections")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "login_required")
        self.assertEqual(self.provider.reads, [])

    def test_missing_configuration_disables_authorization(self):
        app = create_app(Settings(), self.provider, self.store)
        client = self.client(app, "http://127.0.0.1:8100")
        response = client.post("/auth/zhihu/start", json={}, headers={"Origin": "http://127.0.0.1:8100"})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(client.get("/api/status").json()["configuration"]["ready"])
        self.assertEqual(self.provider.exchanged, [])

    def test_configuration_does_not_expose_secret_values(self):
        status = self.a.get("/api/status")
        self.assertTrue(status.json()["configuration"]["ready"])
        for secret in (self.settings.app_key, self.settings.access_secret):
            self.assertNotIn(secret, status.text)
            self.assertNotIn(secret, repr(self.settings))
        self.assertEqual(status.json()["configuration"]["callback_uri"], self.settings.redirect_uri)

    def test_state_missing_is_rejected_before_exchange(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, authorization_code="a")), "state_missing")
        self.assertEqual(self.provider.exchanged, [])
        self.assertEqual(self.a.get("/api/me").status_code, 401)

    def test_state_mismatch_is_rejected(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, "wrong", authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, [])

    def test_unicode_state_is_rejected_safely(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, "非ASCII", authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, [])

    def test_state_from_another_browser_cannot_be_used(self):
        state_a, _ = self.start(self.a)
        self.start(self.b)
        self.assertEqual(self.outcome(self.callback(self.b, state_a, authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, [])

    def test_callback_without_browser_cookie_is_rejected(self):
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.b, state, authorization_code="a")), "state_invalid")

    def test_callback_is_single_use(self):
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, state, authorization_code="a")), "connected")
        self.assertEqual(self.outcome(self.callback(self.a, state, authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, ["a"])

    def test_new_flow_invalidates_previous_flow(self):
        old, _ = self.start(self.a)
        new, _ = self.start(self.a)
        self.assertNotEqual(old, new)
        self.assertEqual(self.outcome(self.callback(self.a, old, authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, [])

    def test_authorization_timeout(self):
        state, _ = self.start(self.a)
        self.clock.now += FLOW_TTL + 1
        self.assertEqual(self.outcome(self.callback(self.a, state, authorization_code="a")), "flow_expired")
        self.assertEqual(self.provider.exchanged, [])

    def test_duplicate_and_conflicting_callback_parameters(self):
        for fields in ([('code', 'a'), ('code', 'b')], [('authorization_code', 'a'), ('code', 'a')],
                       [('error', 'access_denied'), ('code', 'a')], [('state', 'another'), ('code', 'a')]):
            with self.subTest(fields=fields):
                state, _ = self.start(self.a)
                response = self.a.get("/auth/zhihu/callback", params=[('state', state), *fields], follow_redirects=False)
                self.assertEqual(self.outcome(response), "invalid_callback")
        self.assertEqual(self.provider.exchanged, [])

    def test_standard_cancellation_without_any_token_exchange(self):
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, state, error="access_denied")), "authorization_cancelled")
        self.assertEqual(self.provider.exchanged, [])
        self.assertEqual(self.provider.reads, [])
        self.assertEqual(self.a.get("/api/me").status_code, 401)

    def test_cancellation_still_requires_state(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, error="access_denied")), "state_missing")

    def test_missing_code_does_not_guess_a_cancel_protocol(self):
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, state)), "missing_code")
        self.assertEqual(self.provider.exchanged, [])

    def test_upstream_error_text_is_not_reflected(self):
        state, _ = self.start(self.a)
        response = self.callback(self.a, state, error="SENSITIVE-UPSTREAM-FIXTURE", error_description="SECRET-DESCRIPTION-FIXTURE")
        self.assertEqual(self.outcome(response), "authorization_failed")
        self.assertNotIn("FIXTURE", response.text + response.headers["location"])

    def test_code_exchange_failure_does_not_create_session(self):
        self.provider.exchange_error = OAuthError("code_exchange_failed", 401)
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, state, code="a")), "code_exchange_failed")
        self.assertEqual(self.provider.identified, [])
        self.assertEqual(self.a.get("/api/collections").status_code, 401)

    def test_identity_failure_blocks_login_and_collections(self):
        self.provider.identity_error = OAuthError("identity_unavailable", 502)
        state, _ = self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, state, code="a")), "identity_unavailable")
        self.assertEqual(self.a.get("/api/collections").status_code, 401)
        self.assertEqual(self.provider.reads, [])

    def test_token_expiry_stops_before_upstream_request(self):
        self.login(self.a)
        self.clock.now += 601
        response = self.a.get("/api/collections")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "token_expired")
        self.assertEqual(self.provider.reads, [])
        # A stale API response must not clear a newer login cookie. The old handle is revoked.
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.a.get("/api/me").status_code, 401)

    def test_upstream_auth_failure_revokes_only_this_session(self):
        self.login(self.a, "a")
        self.login(self.b, "b")
        self.provider.read_error = OAuthError("authorization_failed", 401)
        self.assertEqual(self.a.get("/api/collections").status_code, 401)
        self.provider.read_error = None
        self.assertEqual(self.a.get("/api/collections").status_code, 401)
        self.assertEqual(self.b.get("/api/collections").json()["user"]["id"], "stable-b")
        self.assertEqual(self.provider.reads, ["mock-token-a", "mock-token-b"])

    def test_permission_denied_is_not_empty_success_or_another_account(self):
        self.login(self.a)
        self.provider.read_error = OAuthError("permission_denied", 403)
        response = self.a.get("/api/collections")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")
        self.assertNotIn("items", response.json())
        self.assertEqual(self.a.get("/api/me").status_code, 200)
        self.assertEqual(self.provider.reads, ["mock-token-a"])

    def test_quota_rate_and_network_errors_remain_distinguishable(self):
        self.login(self.a)
        for name, status in (("quota_exceeded", 429), ("rate_limited", 429), ("upstream_unavailable", 502)):
            with self.subTest(name=name):
                self.provider.read_error = OAuthError(name, status)
                response = self.a.get("/api/collections")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], name)
                self.assertEqual(self.a.get("/api/me").status_code, 200)

    def test_empty_collection_list_is_distinct_from_error(self):
        self.login(self.a)
        self.provider.items["mock-token-a"] = []
        data = self.a.get("/api/collections").json()
        self.assertEqual(data["items"], [])
        self.assertEqual(data["limit"], 50)
        self.assertIsNone(data["has_more"])
        self.assertIn("私密收藏夹不在开放范围", data["scope_note"])
        self.assertEqual(data["scope"], "public")

    def test_logout_clears_server_state_and_other_account_remains(self):
        self.login(self.a, "a")
        self.login(self.b, "b")
        old_cookie = self.a.cookies.get(self.settings.session_cookie)
        csrf = self.a.get("/api/me").json()["csrf_token"]
        response = self.post(self.a, "/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["remote_authorization_revoked"])
        self.assertEqual(self.a.get("/api/me").status_code, 401)
        with self.assertRaises(OAuthError):
            self.store.get(old_cookie)
        self.assertEqual(self.b.get("/api/me").json()["user"]["id"], "stable-b")

    def test_logout_requires_origin_and_csrf(self):
        self.login(self.a)
        for headers in ({}, {"X-CSRF-Token": "wrong"}, {"Origin": "https://evil.example", "X-CSRF-Token": "wrong"}):
            response = self.post(self.a, "/auth/logout", headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.a.get("/api/me").status_code, 200)

    def test_start_requires_post_json_and_exact_origin(self):
        self.assertEqual(self.a.get("/auth/zhihu/start").status_code, 405)
        self.assertEqual(self.a.post("/auth/zhihu/start", json={}).status_code, 403)
        self.assertEqual(self.a.post("/auth/zhihu/start", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.a.post("/auth/zhihu/start", content="x", headers={"Origin": self.settings.origin}).status_code, 403)
        self.assertEqual(self.provider.exchanged, [])

    def test_host_validation_and_no_forwarded_header_bypass(self):
        response = self.a.get("/api/status", headers={"Host": "evil.example", "X-Forwarded-Host": "demo.example.test"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_cookies_are_opaque_and_restricted(self):
        state, start_response = self.start(self.a)
        flow = start_response.headers["set-cookie"]
        self.assertIn("__Host-zh_flow=", flow)
        self.assertIn("HttpOnly", flow)
        self.assertIn("Secure", flow)
        self.assertIn("SameSite=lax", flow)
        self.assertIn("Path=/", flow)
        self.assertNotIn("Domain=", flow)
        self.assertNotIn(state, flow)
        response = self.callback(self.a, state, code="a")
        session_cookie = self.a.cookies.get(self.settings.session_cookie)
        self.assertGreaterEqual(len(session_cookie), 40)
        self.assertNotIn("mock-token", session_cookie)
        self.assertNotIn("mock-token", response.text + str(response.headers))

    def test_all_responses_disable_caching_and_referrers(self):
        for path in ("/", "/api/status", "/api/me", "/api/collections", "/missing", "/assets/oauth.js"):
            with self.subTest(path=path):
                response = self.a.get(path)
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
                self.assertIn("strict-transport-security", response.headers)

    def test_public_urls_never_contain_credentials_and_next_is_ignored(self):
        response = self.post(self.a, "/auth/zhihu/start?next=https://evil.example")
        authorization_url = response.json()["authorization_url"]
        params = parse_qs(urlsplit(authorization_url).query)
        self.assertEqual(set(params), {"app_id", "redirect_uri", "response_type", "state"})
        self.assertEqual(params["redirect_uri"], [self.settings.redirect_uri])
        self.assertNotIn("evil.example", authorization_url)
        for secret in (self.settings.app_key, self.settings.access_secret, "mock-token-a"):
            self.assertNotIn(secret, authorization_url)

    def test_no_shared_vault_or_mock_login_routes(self):
        for path in ("/api/索引", "/api/笔记", "/笔记库/wiki/test.md", "/auth/mock", "/docs", "/openapi.json"):
            self.assertEqual(self.a.get(path).status_code, 404)

    def test_restart_clears_sessions(self):
        self.login(self.a)
        self.store.clear()
        response = self.a.get("/api/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "session_expired")

    def test_lifespan_shutdown_clears_tokens(self):
        with TestClient(self.app, base_url=self.settings.origin) as client:
            self.login(client)
            handle = client.cookies.get(self.settings.session_cookie)
            self.assertEqual(self.store.get(handle).user.subject, "stable-a")
        with self.assertRaises(OAuthError):
            self.store.get(handle)

    def test_logout_during_callback_cannot_restore_session(self):
        state, _ = self.start(self.a)
        flow = self.a.cookies.get(self.settings.flow_cookie)
        self.provider.on_identify = lambda: self.store.cancel(flow)
        response = self.callback(self.a, state, code="a")
        self.assertEqual(self.outcome(response), "state_invalid")
        self.assertEqual(self.a.get("/api/me").status_code, 401)

    def test_expiry_during_identity_does_not_create_a_session(self):
        state, _ = self.start(self.a)
        self.provider.on_identify = lambda: setattr(self.clock, "now", self.clock.now + 601)
        response = self.callback(self.a, state, code="a")
        self.assertIn(self.outcome(response), {"flow_expired", "token_expired"})
        self.assertEqual(self.a.get("/api/me").status_code, 401)

    def test_logout_during_collection_request_suppresses_old_data(self):
        self.login(self.a)
        handle = self.a.cookies.get(self.settings.session_cookie)
        self.provider.on_read = lambda: self.store.revoke(handle)
        response = self.a.get("/api/collections")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("A 专属测试夹", response.text)

    def test_expiry_during_collection_request_suppresses_old_data(self):
        self.login(self.a)
        self.provider.on_read = lambda: setattr(self.clock, "now", self.clock.now + 601)
        response = self.a.get("/api/collections")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("A 专属测试夹", response.text)

    def test_second_account_switch_rotates_and_revokes_previous_session(self):
        self.login(self.a, "a")
        old = self.a.cookies.get(self.settings.session_cookie)
        self.login(self.a, "b")
        self.assertNotEqual(old, self.a.cookies.get(self.settings.session_cookie))
        self.assertEqual(self.a.get("/api/me").json()["user"]["id"], "stable-b")
        with self.assertRaises(OAuthError):
            self.store.get(old)


class ConfigurationAndStoreTests(unittest.TestCase):
    def test_profile_contract_is_explicit_not_guessed(self):
        for field in ("profile_url", "profile_auth", "profile_id_path"):
            self.assertFalse(replace(TEST_SETTINGS, **{field: ""}).ready)
        self.assertTrue(replace(TEST_SETTINGS, profile_name_path="").ready)

    def test_redirect_uri_restrictions(self):
        for url in ("https://app.example/auth/zhihu/callback", "http://127.0.0.1:8100/auth/zhihu/callback", "http://localhost:8100/auth/zhihu/callback"):
            self.assertTrue(valid_redirect_uri(url), url)
        for url in ("http://app.example/auth/zhihu/callback", "https://u:p@app.example/auth/zhihu/callback", "https://app.example/auth/zhihu/callback/", "https://app.example/auth/zhihu/callback?x=y", "https://app.example/auth/zhihu/callback#x", "javascript:evil", "https://app.example:bad/auth/zhihu/callback"):
            self.assertFalse(valid_redirect_uri(url), url)

    def test_profile_url_rejects_external_hosts_and_token_queries(self):
        for url in ("http://openapi.zhihu.com/user", "https://evil.example/user", "https://openapi.zhihu.com.evil.example/user", "https://u:p@openapi.zhihu.com/user", "https://openapi.zhihu.com/user?access_token=secret", "https://127.0.0.1/user"):
            self.assertFalse(valid_profile_url(url), url)
        self.assertTrue(valid_profile_url(TEST_SETTINGS.profile_url))

    def test_invalid_config_values_are_not_echoed(self):
        settings = replace(TEST_SETTINGS, app_key="SECRET\nFIXTURE", profile_url="https://evil.example/SECRET-FIXTURE")
        self.assertFalse(settings.ready)
        public = json.dumps(settings.public())
        self.assertNotIn("SECRET", public)
        self.assertNotIn("evil.example", public)

    def test_local_http_cookie_exception_is_not_used_for_remote_http(self):
        local = replace(TEST_SETTINGS, redirect_uri="http://127.0.0.1:8100/auth/zhihu/callback")
        self.assertFalse(local.secure_cookies)
        self.assertFalse(local.session_cookie.startswith("__Host-"))
        self.assertFalse(replace(local, redirect_uri="http://demo.example/auth/zhihu/callback").ready)

    def test_bounded_pending_store_and_cleanup(self):
        clock = Clock()
        store = SessionStore(clock, capacity=1)
        store.begin()
        with self.assertRaisesRegex(OAuthError, "server_busy"):
            store.begin()
        clock.now += FLOW_TTL + 1
        store.prune()
        store.begin()

    def test_bounded_sessions_and_max_lifetime(self):
        clock = Clock()
        store = SessionStore(clock, capacity=1)
        handle, session = store.create(TokenGrant("mock", clock() + 86400), Identity("a", "a"))
        self.assertEqual(session.expires_at, clock() + SESSION_TTL)
        self.assertNotIn("mock", repr(session))
        with self.assertRaisesRegex(OAuthError, "server_busy"):
            store.create(TokenGrant("mock2", clock() + 86400), Identity("b", "b"))
        clock.now += SESSION_TTL + 1
        with self.assertRaisesRegex(OAuthError, "token_expired"):
            store.get(handle)
        store.create(TokenGrant("mock2", clock() + 100), Identity("b", "b"))

    def test_claimed_state_cannot_be_consumed_twice(self):
        store = SessionStore(Clock())
        handle, state = store.begin()
        store.consume(handle, state)
        with self.assertRaisesRegex(OAuthError, "state_invalid"):
            store.consume(handle, state)


class StateFallbackTests(unittest.TestCase):
    """ZHIHU_OAUTH_STATE_FALLBACK=cookie_bound: documented no-state callbacks.

    The quickstart (step 2) and the zhihu2077 live test both show the platform
    callback carrying only `authorization_code`. Strict rejection stays the
    default; these tests cover the explicit operator opt-in only.
    """

    def setUp(self):
        self.clock = Clock()
        self.settings = replace(TEST_SETTINGS, state_fallback="cookie_bound")
        self.store = SessionStore(self.clock)
        self.provider = FakeProvider(self.clock, self.settings)
        self.app = create_app(self.settings, self.provider, self.store)
        self.a = self.client()
        self.b = self.client()

    def client(self):
        client = TestClient(self.app, base_url=self.settings.origin)
        self.addCleanup(client.close)
        return client

    def post(self, client, url):
        return client.post(url, json={}, headers={"Origin": self.settings.origin})

    def start(self, client):
        response = self.post(client, "/auth/zhihu/start")
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def callback(self, client, state=None, **params):
        if state is not None:
            params["state"] = state
        return client.get("/auth/zhihu/callback", params=params, follow_redirects=False)

    def outcome(self, response):
        if response.headers["location"] == "/workspace/":
            return "connected"
        return parse_qs(urlsplit(response.headers["location"]).query)["result"][0]

    def test_configuration_reports_fallback_without_secrets(self):
        self.assertTrue(self.settings.public()["state_fallback_enabled"])
        self.assertTrue(self.a.get("/api/status").json()["configuration"]["state_fallback_enabled"])
        self.assertFalse(replace(TEST_SETTINGS, state_fallback="").public()["state_fallback_enabled"])

    def test_invalid_fallback_values_are_rejected(self):
        self.assertFalse(replace(TEST_SETTINGS, state_fallback="on").ready)
        self.assertFalse(replace(TEST_SETTINGS, state_fallback="cookie_bound ").ready)

    def test_stateless_callback_connects_and_is_labelled(self):
        self.start(self.a)
        response = self.callback(self.a, authorization_code="a")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/workspace/")
        me = self.a.get("/api/me").json()
        self.assertEqual(me["state_mode"], "cookie_bound")
        self.assertEqual(self.provider.exchanged, ["a"])

    def test_stateful_callback_still_reports_state_mode(self):
        response = self.post(self.a, "/auth/zhihu/start")
        state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
        self.assertEqual(self.outcome(self.callback(self.a, state, authorization_code="a")), "connected")
        self.assertEqual(self.a.get("/api/me").json()["state_mode"], "state")

    def test_supplied_state_is_still_verified_when_fallback_enabled(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, "wrong", authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, [])

    def test_stateless_callback_is_single_use(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, authorization_code="a")), "connected")
        self.assertEqual(self.outcome(self.callback(self.a, authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, ["a"])

    def test_stateless_callback_without_flow_cookie_is_rejected(self):
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.b, authorization_code="a")), "state_invalid")
        self.assertEqual(self.provider.exchanged, [])

    def test_stateless_callback_still_expires_with_flow(self):
        self.start(self.a)
        self.clock.now += FLOW_TTL + 1
        self.assertEqual(self.outcome(self.callback(self.a, authorization_code="a")), "flow_expired")
        self.assertEqual(self.provider.exchanged, [])

    def test_error_callback_without_state_still_exchanges_nothing(self):
        # The cookie-bound fallback only trusts the flow cookie for a real
        # authorization code; an error callback still cancels and never trades a token.
        self.start(self.a)
        self.assertEqual(self.outcome(self.callback(self.a, error="access_denied")), "authorization_cancelled")
        self.assertEqual(self.provider.exchanged, [])
        self.assertEqual(self.a.get("/api/me").status_code, 401)


class ZhihuProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.requests = []
        self.response = httpx.Response(200, json={})
        self.exception = None

        async def handler(request):
            self.requests.append(request)
            if self.exception:
                raise self.exception
            return self.response

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        self.addAsyncCleanup(self.client.aclose)
        self.provider = ZhihuProvider(TEST_SETTINGS, self.client, self.clock)

    async def test_token_exchange_uses_documented_form_and_accepts_20000(self):
        self.response = httpx.Response(200, json={"code": 20000, "access_token": "mock-token", "expires_in": 3600, "token_type": "Bearer"})
        grant = await self.provider.exchange_code("mock-code")
        request = self.requests[0]
        form = parse_qs(request.content.decode())
        self.assertEqual(str(request.url), TOKEN_URL)
        self.assertEqual(request.method, "POST")
        self.assertIn("application/x-www-form-urlencoded", request.headers["content-type"])
        self.assertEqual(form, {"app_id": [TEST_SETTINGS.app_id], "app_key": [TEST_SETTINGS.app_key],
                              "redirect_uri": [TEST_SETTINGS.redirect_uri], "grant_type": ["authorization_code"], "code": ["mock-code"]})
        self.assertEqual(grant.expires_at, self.clock() + 3595)
        self.assertNotIn("mock-token", repr(grant))
        self.assertNotIn("mock-code", str(request.url))

    async def test_token_expiry_must_be_a_valid_positive_integer(self):
        for value in (None, 0, -1, True, "abc", 1.5, 4000000000):
            with self.subTest(value=value):
                self.response = httpx.Response(200, json={"access_token": "mock-token", "expires_in": value})
                with self.assertRaisesRegex(OAuthError, "upstream_protocol_error"):
                    await self.provider.exchange_code("mock-code")

    async def test_missing_token_or_wrong_token_type_is_rejected(self):
        for payload, expected in (({"expires_in": 3600}, "code_exchange_failed"),
                                  ({"access_token": "mock-token", "expires_in": 3600, "token_type": "other"}, "upstream_protocol_error")):
            self.response = httpx.Response(200, json=payload)
            with self.assertRaisesRegex(OAuthError, expected):
                await self.provider.exchange_code("mock-code")

    async def test_identity_uses_explicit_bearer_contract_and_stable_id(self):
        self.response = httpx.Response(200, json={"code": 20000, "data": {"id": "stable-a", "name": "同名用户"}})
        user = await self.provider.identify("mock-user-token")
        self.assertEqual(user, Identity("stable-a", "同名用户"))
        request = self.requests[0]
        self.assertEqual(str(request.url), TEST_SETTINGS.profile_url)
        self.assertEqual(request.headers["authorization"], "Bearer mock-user-token")
        self.assertNotIn("x-oauth-token", request.headers)
        self.assertNotIn(TEST_SETTINGS.app_key, str(request.headers))
        self.assertEqual(request.url.query, b"")

    async def test_identity_can_use_explicit_platform_header_contract(self):
        provider = ZhihuProvider(replace(TEST_SETTINGS, profile_auth="platform_headers"), self.client, self.clock)
        self.response = httpx.Response(200, json={"data": {"id": 123, "name": "测试用户"}})
        self.assertEqual((await provider.identify("mock-user-token")).subject, "123")
        self.assertEqual(self.requests[0].headers["authorization"], "Bearer " + TEST_SETTINGS.access_secret)
        self.assertEqual(self.requests[0].headers["x-oauth-token"], "mock-user-token")

    async def test_identity_missing_stable_id_never_uses_nickname_or_token(self):
        for value in (None, {}, [], True, "", " ", "bad\nvalue"):
            self.response = httpx.Response(200, json={"data": {"id": value, "name": "same-nickname"}})
            with self.assertRaisesRegex(OAuthError, "identity_unavailable"):
                await self.provider.identify("mock-user-token")
        self.response = httpx.Response(200, json={"name": "same-nickname", "access_token": "mock-user-token"})
        with self.assertRaisesRegex(OAuthError, "identity_unavailable"):
            await self.provider.identify("mock-user-token")

    async def test_unconfigured_display_name_is_optional(self):
        provider = ZhihuProvider(replace(TEST_SETTINGS, profile_name_path=""), self.client, self.clock)
        self.response = httpx.Response(200, json={"data": {"id": "stable-a"}})
        self.assertEqual((await provider.identify("mock-token")).name, "知乎用户")

    async def test_collections_send_both_platform_and_current_user_headers(self):
        self.response = httpx.Response(200, json={"Code": 0, "Data": {"Items": [
            {"UrlToken": 101, "Title": "A", "Description": "描述", "IsPublic": True, "Url": "javascript:alert(1)"}
        ]}})
        items = await self.provider.collections("mock-user-a")
        request = self.requests[0]
        self.assertEqual(str(request.url), COLLECTIONS_URL + "?Limit=50")
        self.assertEqual(request.headers["Authorization"], "Bearer " + TEST_SETTINGS.access_secret)
        self.assertEqual(request.headers["X-OAuth-Token"], "mock-user-a")
        self.assertEqual(request.headers["X-Request-Timestamp"], str(int(self.clock())))
        self.assertNotIn(TEST_SETTINGS.app_key, str(request.headers))
        self.assertEqual(items[0]["url"], "https://www.zhihu.com/collection/101")

    async def test_missing_user_token_cannot_fall_back_to_access_secret(self):
        for token in ("", None, "bad\r\nheader"):
            with self.assertRaisesRegex(OAuthError, "authorization_failed"):
                await self.provider.collections(token)
        self.assertEqual(self.requests, [])

    async def test_http_failures_are_sanitized_and_differentiated(self):
        for status, code in ((401, "authorization_failed"), (403, "permission_denied"), (429, "rate_limited"), (503, "upstream_unavailable")):
            self.response = httpx.Response(status, text="SENSITIVE-UPSTREAM-FIXTURE")
            with self.assertRaises(OAuthError) as caught:
                await self.provider.collections("mock-token")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("SENSITIVE", json.dumps(caught.exception.public()))

    async def test_business_failures_are_sanitized_and_differentiated(self):
        for value, code in ((20001, "authorization_failed"), (30001, "rate_limited"), (30002, "quota_exceeded"), (90001, "upstream_unavailable"), (10001, "upstream_protocol_error")):
            self.response = httpx.Response(200, json={"Code": value, "Message": "SENSITIVE-FIXTURE"})
            with self.assertRaises(OAuthError) as caught:
                await self.provider.collections("mock-token")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("SENSITIVE", str(caught.exception))

    async def test_invalid_grant_and_insufficient_scope(self):
        for value, code in (("invalid_grant", "code_exchange_failed"), ("invalid_token", "authorization_failed"), ("insufficient_scope", "permission_denied")):
            self.response = httpx.Response(400, json={"error": value, "error_description": "SENSITIVE-FIXTURE"})
            with self.assertRaisesRegex(OAuthError, code):
                await self.provider.exchange_code("mock-code")

    async def test_redirect_is_never_followed_with_credentials(self):
        self.response = httpx.Response(302, headers={"Location": "https://evil.example/steal"})
        with self.assertRaisesRegex(OAuthError, "upstream_protocol_error"):
            await self.provider.collections("mock-token")
        self.assertEqual(len(self.requests), 1)

    async def test_timeouts_are_safe_and_not_retried(self):
        self.exception = httpx.ReadTimeout("SENSITIVE-TOKEN-FIXTURE")
        with self.assertRaisesRegex(OAuthError, "upstream_unavailable"):
            await self.provider.collections("mock-token")
        self.assertEqual(len(self.requests), 1)

    async def test_non_json_and_oversize_responses_fail_closed(self):
        for response in (httpx.Response(200, text="<html>Login</html>"), httpx.Response(200, json=[])):
            self.response = response
            with self.assertRaisesRegex(OAuthError, "upstream_protocol_error"):
                await self.provider.collections("mock-token")
        self.response = httpx.Response(200, text="x" * 200)
        with patch("zhihu_oauth.provider.MAX_RESPONSE_BYTES", 100):
            with self.assertRaisesRegex(OAuthError, "upstream_protocol_error"):
                await self.provider.collections("mock-token")

    async def test_malformed_collection_data_is_not_an_empty_success(self):
        item = {"UrlToken": 1, "Title": "A", "Description": "", "IsPublic": True}
        for payload in ({"Code": 0}, {"Code": 0, "Data": {"Items": {}}},
                        {"Code": False, "Data": {"Items": []}},
                        {"Code": 0, "Data": {"Items": [dict(item, UrlToken="bad")]}},
                        {"Code": 0, "Data": {"Items": [dict(item, IsPublic="true")]}},
                        {"Code": 0, "Data": {"Items": [item, item]}},
                        {"Code": 0, "Data": {"Items": [item] * 51}}):
            self.response = httpx.Response(200, json=payload)
            with self.assertRaisesRegex(OAuthError, "upstream_protocol_error"):
                await self.provider.collections("mock-token")


class EndToEndMockHttpTests(unittest.TestCase):
    def test_two_complete_flows_through_http_provider_adapter(self):
        """Real app + real adapter, synthetic network only; never calls Zhihu."""
        clock = Clock()
        requests = []

        async def handler(request):
            requests.append(request)
            if str(request.url) == TOKEN_URL:
                code = parse_qs(request.content.decode())["code"][0]
                return httpx.Response(200, json={"code": 20000, "access_token": "mock-" + code, "expires_in": 3600})
            if str(request.url) == TEST_SETTINGS.profile_url:
                account = request.headers["authorization"].removeprefix("Bearer mock-")
                return httpx.Response(200, json={"data": {"id": "stable-" + account, "name": "same-name"}})
            if str(request.url).startswith(COLLECTIONS_URL):
                token = request.headers["x-oauth-token"]
                number = 101 if token == "mock-a" else 202
                return httpx.Response(200, json={"Code": 0, "Data": {"Items": [
                    {"UrlToken": number, "Title": token + " collection", "Description": "", "IsPublic": True}
                ]}})
            raise AssertionError("Unexpected synthetic endpoint")

        transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addCleanup(lambda: asyncio.run(transport_client.aclose()))
        provider = ZhihuProvider(TEST_SETTINGS, transport_client, clock)
        app = create_app(TEST_SETTINGS, provider, SessionStore(clock))
        clients = [TestClient(app, base_url=TEST_SETTINGS.origin), TestClient(app, base_url=TEST_SETTINGS.origin)]
        for client in clients:
            self.addCleanup(client.close)
        for account, client, expected_id in zip(("a", "b"), clients, ("101", "202")):
            started = client.post("/auth/zhihu/start", json={}, headers={"Origin": TEST_SETTINGS.origin})
            state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
            callback = client.get("/auth/zhihu/callback", params={"state": state, "authorization_code": account}, follow_redirects=False)
            self.assertEqual(callback.headers["location"], "/workspace/")
            self.assertEqual(client.get("/api/me").json()["user"]["id"], "stable-" + account)
            self.assertEqual(client.get("/api/collections").json()["items"][0]["id"], expected_id)
        self.assertEqual(len(requests), 6)
        self.assertEqual(clients[0].get("/api/me").json()["user"]["id"], "stable-a")


if __name__ == "__main__":
    unittest.main()
