"""Zhihu HTTP protocol adapter. No CLI and no developer-account fallback."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlencode

import httpx

from .collection_data import (
    COLLECTIONS_LIMIT, CONTENT_PAGE_SIZE, ContentPage, collection_request, creation_request,
    public_collections, public_content_page, public_creation_page,
)
from .config import Settings
from .errors import OAuthError

AUTHORIZE_URL = "https://openapi.zhihu.com/authorize"
TOKEN_URL = "https://openapi.zhihu.com/access_token"
COLLECTIONS_URL = "https://developer.zhihu.com/api/v1/user/favlists"
COLLECTION_CONTENTS_URL = "https://developer.zhihu.com/api/v1/user/favlist_contents"
USER_CONTENTS_URL = "https://developer.zhihu.com/api/v1/user/contents"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class Identity:
    subject: str
    name: str

    def public(self) -> dict:
        return {"id": self.subject, "name": self.name, "provider": "zhihu"}


class Provider(Protocol):
    def authorize_url(self, state: str) -> str: ...
    async def exchange_code(self, code: str) -> TokenGrant: ...
    async def identify(self, token: str) -> Identity: ...
    async def collections(self, token: str) -> list[dict]: ...
    async def collection_contents(self, token: str, identifier: str, offset: str) -> ContentPage: ...


def require_token(token: str) -> None:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", token):
        raise OAuthError("authorization_failed", 401)


def field_at(payload: dict, path: str):
    value = payload
    for component in path.lstrip("/").split("/"):
        if not isinstance(value, dict) or component not in value:
            raise OAuthError("identity_unavailable", 502)
        value = value[component]
    return value


def stable_subject(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise OAuthError("identity_unavailable", 502)
    subject = str(value)
    if not subject or subject.strip() != subject or len(subject) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in subject):
        raise OAuthError("identity_unavailable", 502)
    return subject


def positive_integer(value, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[0-9]{1,20}", str(value)):
        raise OAuthError("upstream_protocol_error", 502)
    result = int(value)
    if not 0 < result <= maximum:
        raise OAuthError("upstream_protocol_error", 502)
    return result


class ZhihuProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.client = client
        self.clock = clock

    def authorize_url(self, state: str) -> str:
        return AUTHORIZE_URL + "?" + urlencode({
            "redirect_uri": self.settings.redirect_uri,
            "app_id": self.settings.app_id,
            "response_type": "code",
            "state": state,
        })

    def user_headers(self, token: str) -> dict[str, str]:
        require_token(token)  # Missing user token is a hard stop, even with Access Secret.
        if not self.settings.collections_ready:
            raise OAuthError("collections_configuration_required", 503)
        return {
            "Authorization": "Bearer " + self.settings.access_secret,
            "X-OAuth-Token": token,
            "X-Request-Timestamp": str(int(self.clock())),
        }

    async def _json(self, method: str, url: str, **kwargs) -> dict:
        try:
            async with self.client.stream(method, url, follow_redirects=False, **kwargs) as response:
                status = response.status_code
                if status == 401:
                    raise OAuthError("authorization_failed", 401)
                if status == 403:
                    raise OAuthError("permission_denied", 403)
                if status == 404 and url == COLLECTION_CONTENTS_URL:
                    raise OAuthError("collection_unavailable", 404)
                if status == 429:
                    raise OAuthError("rate_limited", 429)
                if status >= 500:
                    raise OAuthError("upstream_unavailable", 502)
                if 300 <= status < 400:
                    # Never forward tokens or app secrets to a redirect target.
                    raise OAuthError("upstream_protocol_error", 502)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise OAuthError("upstream_protocol_error", 502)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise OAuthError("upstream_protocol_error", 502)
                self._business_error(payload, url)
                if not 200 <= status < 300:
                    code = "code_exchange_failed" if url == TOKEN_URL else "upstream_protocol_error"
                    raise OAuthError(code, 502)
                return payload
        except (httpx.HTTPError, OSError):
            raise OAuthError("upstream_unavailable", 502) from None
        except (ValueError, UnicodeError, RecursionError):
            raise OAuthError("upstream_protocol_error", 502) from None

    def _business_error(self, payload: dict, url: str) -> None:
        error = payload.get("error")
        if error:
            if error in ("invalid_token", "expired_token"):
                raise OAuthError("authorization_failed", 401)
            if error == "invalid_grant":
                raise OAuthError("code_exchange_failed", 401)
            if error in ("insufficient_scope", "access_denied"):
                raise OAuthError("permission_denied", 403)
            raise OAuthError("upstream_protocol_error", 502)
        code = payload.get("Code", payload.get("code"))
        if code is None:
            return  # Token/profile envelopes do not always contain a business code.
        if isinstance(code, bool):
            raise OAuthError("upstream_protocol_error", 502)
        if str(code) in {"0", "20000"}:
            return
        if str(code) == "404" and url == self.settings.profile_url:
            raise OAuthError("identity_unavailable", 502)
        known = {
            "401": ("authorization_failed", 401),
            "403": ("permission_denied", 403),
            "20001": ("authorization_failed", 401),
            "30001": ("rate_limited", 429),
            "30002": ("quota_exceeded", 429),
            "90001": ("upstream_unavailable", 502),
        }
        name, status = known.get(str(code), ("upstream_protocol_error", 502))
        raise OAuthError(name, status)

    async def exchange_code(self, code: str) -> TokenGrant:
        requested_at = self.clock()
        payload = await self._json("POST", TOKEN_URL, data={
            "app_id": self.settings.app_id,
            "app_key": self.settings.app_key,
            "grant_type": "authorization_code",
            "redirect_uri": self.settings.redirect_uri,
            "code": code,
        })
        token = payload.get("access_token")
        if not token:
            raise OAuthError("code_exchange_failed", 502)
        require_token(token)
        kind = payload.get("token_type", "Bearer")
        if not isinstance(kind, str) or kind.lower() != "bearer":
            raise OAuthError("upstream_protocol_error", 502)
        seconds = positive_integer(payload.get("expires_in"), 366 * 86400)
        # Account for request time and a small safety margin; no guessed refresh flow.
        return TokenGrant(token, requested_at + seconds - 5)

    async def identify(self, token: str) -> Identity:
        require_token(token)
        if not self.settings.profile_url or not self.settings.profile_id_path:
            raise OAuthError("configuration_required", 503)
        if self.settings.profile_auth == "platform_headers":
            headers = self.user_headers(token)
        elif self.settings.profile_auth == "oauth_bearer":
            headers = {"Authorization": "Bearer " + token}
        else:
            raise OAuthError("configuration_required", 503)
        payload = await self._json("GET", self.settings.profile_url, headers=headers)
        subject = stable_subject(field_at(payload, self.settings.profile_id_path))
        name = "知乎用户"
        if self.settings.profile_name_path:
            name = field_at(payload, self.settings.profile_name_path)
            if not isinstance(name, str) or not name.strip():
                raise OAuthError("identity_unavailable", 502)
            name = name[:200]
        return Identity(subject, name)

    async def collections(self, token: str) -> list[dict]:
        payload = await self._json("GET", COLLECTIONS_URL, params={"Limit": COLLECTIONS_LIMIT}, headers=self.user_headers(token))
        return public_collections(payload)

    async def collection_contents(self, token: str, identifier: str, offset: str) -> ContentPage:
        headers = self.user_headers(token)
        identifier, offset = collection_request(identifier, offset)
        payload = await self._json("GET", COLLECTION_CONTENTS_URL, headers=headers, params={
            "FavlistUrlToken": identifier, "Offset": offset, "Limit": CONTENT_PAGE_SIZE,
        })
        return public_content_page(payload, offset)

    async def creations(self, token: str, content_type: str, offset: str) -> ContentPage:
        """用户自己的公开创作 —— 与收藏并列的数据源，不是收藏夹的一种。

        ContentType 是接口必填项、取值固定，所以在这里校验而不是让调用方拼；
        SortField 用接口默认值（按时间倒序），少一个参数就少一处协议猜测。
        """
        headers = self.user_headers(token)
        content_type, offset = creation_request(content_type, offset)
        payload = await self._json("GET", USER_CONTENTS_URL, headers=headers, params={
            "ContentType": content_type, "Offset": offset, "Limit": CONTENT_PAGE_SIZE,
        })
        return public_creation_page(payload, offset)
