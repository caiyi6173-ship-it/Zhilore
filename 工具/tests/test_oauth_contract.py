# -*- coding: utf-8 -*-
"""Official documented OAuth shapes; synthetic HTTP, no real credentials."""
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _环境隔离 import 仅环境
from zhihu_oauth.config import Settings
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.provider import ZhihuProvider

OFFICIAL = Settings(app_id="synthetic-app", app_key="synthetic-key",
                    redirect_uri="https://demo.example/auth/zhihu/callback")


class OfficialConfigurationTests(unittest.TestCase):
    def test_default_contract_matches_documented_user_endpoint(self):
        # 只摘掉 ZHIHU_*，不用 patch.dict(clear=True)：见 _环境隔离.py 的说明。
        with 仅环境():
            settings = Settings.from_env()
        self.assertEqual(settings.profile_url, "https://openapi.zhihu.com/user")
        self.assertEqual(settings.profile_auth, "oauth_bearer")
        self.assertEqual(settings.profile_id_path, "/uid")
        self.assertEqual(settings.profile_name_path, "/fullname")

    def test_explicit_invalid_or_empty_override_is_not_silently_defaulted(self):
        with 仅环境({"ZHIHU_OAUTH_PROFILE_URL": ""}):
            settings = Settings.from_env()
        self.assertEqual(settings.profile_url, "")
        self.assertIn("ZHIHU_OAUTH_PROFILE_URL", settings.issues()[0])

    def test_access_secret_is_separate_from_oauth_readiness(self):
        self.assertTrue(OFFICIAL.ready)
        self.assertFalse(OFFICIAL.collections_ready)
        public = OFFICIAL.public()
        self.assertEqual(public["collections"]["missing"], ["ZHIHU_ACCESS_SECRET"])
        self.assertEqual(public["workspace_uri"], "https://demo.example/workspace/")
        self.assertTrue(public["https_required_for_submission"])
        self.assertFalse(replace(OFFICIAL, profile_auth="platform_headers").ready)


class OfficialProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_bearer_and_root_uid_fullname_without_phone_or_email(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://openapi.zhihu.com/user")
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-user-token")
            self.assertNotIn("X-OAuth-Token", request.headers)
            return httpx.Response(200, json={"uid": "stable-official-shape", "fullname": "模拟昵称",
                                           "phone_no": "DO-NOT-EXPOSE-PHONE", "email": "DO-NOT-EXPOSE-EMAIL",
                                           "avatar_path": "https://example.invalid/avatar"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            identity = await ZhihuProvider(OFFICIAL, client).identify("synthetic-user-token")
        self.assertEqual(identity.public(), {"id": "stable-official-shape", "name": "模拟昵称", "provider": "zhihu"})

    async def test_exchange_uses_form_and_documented_root_token_shape(self):
        def handler(request):
            self.assertEqual(str(request.url), "https://openapi.zhihu.com/access_token")
            self.assertEqual(request.headers["content-type"], "application/x-www-form-urlencoded")
            fields = parse_qs(request.content.decode())
            self.assertEqual(fields, {"app_id": ["synthetic-app"], "app_key": ["synthetic-key"],
                                     "grant_type": ["authorization_code"], "code": ["synthetic-code"],
                                     "redirect_uri": [OFFICIAL.redirect_uri]})
            return httpx.Response(200, json={"access_token": "synthetic-token", "token_type": "Bearer", "expires_in": 3600})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            grant = await ZhihuProvider(OFFICIAL, client, lambda: 10000).exchange_code("synthetic-code")
        self.assertEqual(grant.expires_at, 13595)
        self.assertNotIn("synthetic-token", repr(grant))

    async def test_http_200_business_errors_are_not_treated_as_success(self):
        for code, error in ((401, "authorization_failed"), (403, "permission_denied"), (404, "identity_unavailable")):
            with self.subTest(code=code):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, json={"code": code, "message": "untrusted-error-secret"}))) as client:
                    with self.assertRaises(OAuthError) as caught:
                        await ZhihuProvider(OFFICIAL, client).identify("synthetic-user-token")
                self.assertEqual(caught.exception.code, error)
                self.assertNotIn("untrusted-error-secret", str(caught.exception.public()))

    async def test_missing_collection_secret_does_not_fall_back_to_developer_account(self):
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
            provider = ZhihuProvider(OFFICIAL, client)
            for action in (lambda: provider.collections("synthetic-token"),
                           lambda: provider.collection_contents("synthetic-token", "101", "0")):
                with self.assertRaises(OAuthError) as caught:
                    await action()
                self.assertEqual(caught.exception.code, "collections_configuration_required")
        self.assertEqual(calls, [])

    async def test_identity_requires_stable_uid_not_nickname_or_phone(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, json={"fullname": "模拟昵称", "phone_no": "not-a-uid"}))) as client:
            with self.assertRaises(OAuthError) as caught:
                await ZhihuProvider(OFFICIAL, client).identify("synthetic-token")
        self.assertEqual(caught.exception.code, "identity_unavailable")


if __name__ == "__main__":
    unittest.main()
