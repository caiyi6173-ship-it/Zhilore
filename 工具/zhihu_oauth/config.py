"""Configuration comes only from server environment; nothing loads a .env file."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

CALLBACK_PATH = "/auth/zhihu/callback"
LOCAL_REDIRECT_URI = "http://127.0.0.1:8100" + CALLBACK_PATH
PROFILE_HOSTS = frozenset({"openapi.zhihu.com", "developer.zhihu.com"})
PROFILE_AUTH_MODES = frozenset({"oauth_bearer", "platform_headers"})
# Official OAuth user_info contract, verified 2026-09-13.
PROFILE_URL = "https://openapi.zhihu.com/user"
# 官方文档写明的 Access Secret 查看/获取入口（个人中心），2026-09-14 实测 200。
ACCESS_SECRET_URL = "https://developer.zhihu.com/profile"
# Documented platform behaviour (quickstart step 2 + zhihu2077 live test): the
# callback returns only `authorization_code`, never `state`. STRICT keeps
# rejecting such callbacks; COOKIE_BOUND is the explicit hackathon fallback
# that relates the login via the single-use flow cookie instead.
STATE_FALLBACK_COOKIE_BOUND = "cookie_bound"
STATE_FALLBACK_MODES = frozenset({"", STATE_FALLBACK_COOKIE_BOUND})


def origin_of(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    suffix = "" if port is None or (parsed.scheme, port) in {("http", 80), ("https", 443)} else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def valid_redirect_uri(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            value and len(value) <= 2048 and value.isascii()
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and url.hostname and url.username is None and url.password is None
            and url.path == CALLBACK_PATH and not url.query and not url.fragment
            and "?" not in value and "#" not in value and "\\" not in value
            and (url.port is None or 1 <= url.port <= 65535)
            and (url.scheme == "https" or (
                url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
            ))
        )
    except ValueError:
        return False


def valid_profile_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            value and len(value) <= 2048 and value.isascii()
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and url.scheme == "https" and url.hostname in PROFILE_HOSTS
            and url.username is None and url.password is None
            and url.port in {None, 443} and url.path.startswith("/")
            and not url.query and not url.fragment
            and "?" not in value and "#" not in value and "\\" not in value
        )
    except ValueError:
        return False


def valid_field_path(value: str) -> bool:
    # Explicit object-field paths, not executable JSONPath and not array offsets.
    return bool(len(value) <= 256 and re.fullmatch(r"(?:/[A-Za-z_][A-Za-z0-9_]*){1,10}", value))


@dataclass(frozen=True)
class Settings:
    app_id: str = field(default="", repr=False)
    app_key: str = field(default="", repr=False)
    access_secret: str = field(default="", repr=False)
    redirect_uri: str = ""
    profile_url: str = PROFILE_URL
    profile_auth: str = "oauth_bearer"
    profile_id_path: str = "/uid"
    profile_name_path: str = "/fullname"
    state_fallback: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(**{
            field_name: os.environ.get(env_name, getattr(defaults, field_name))
            for field_name, env_name in ENV_FIELDS.items()
        })

    def _field_issues(self, fields) -> tuple[list[str], list[dict[str, str]]]:
        missing, invalid = [], []
        for field_name in fields:
            env_name = ENV_FIELDS[field_name]
            value = getattr(self, field_name)
            if not value:
                if field_name not in {"profile_name_path", "state_fallback"}:
                    missing.append(env_name)
                continue
            valid = True
            if field_name in {"app_id", "app_key", "access_secret"}:
                valid = len(value) <= 16384 and value.isascii() and all(32 < ord(c) < 127 for c in value)
            elif field_name == "redirect_uri":
                valid = valid_redirect_uri(value)
            elif field_name == "profile_url":
                valid = valid_profile_url(value)
            elif field_name == "profile_auth":
                valid = value in PROFILE_AUTH_MODES
            elif field_name == "state_fallback":
                valid = value in STATE_FALLBACK_MODES
            elif field_name.endswith("_path"):
                valid = valid_field_path(value)
            if not valid:
                invalid.append({"field": env_name, "message": FIELD_RULES[field_name]})
        return missing, invalid

    def issues(self) -> tuple[list[str], list[dict[str, str]]]:
        fields = [name for name in ENV_FIELDS if name != "access_secret"]
        if self.profile_auth == "platform_headers":
            fields.append("access_secret")
        return self._field_issues(fields)

    def collection_issues(self) -> tuple[list[str], list[dict[str, str]]]:
        return self._field_issues(("access_secret",))

    @property
    def collections_ready(self) -> bool:
        missing, invalid = self.collection_issues()
        return not missing and not invalid

    @property
    def ready(self) -> bool:
        missing, invalid = self.issues()
        return not missing and not invalid

    @property
    def origin(self) -> str:
        return origin_of(self.redirect_uri if valid_redirect_uri(self.redirect_uri) else LOCAL_REDIRECT_URI)

    @property
    def secure_cookies(self) -> bool:
        return self.origin.startswith("https://")

    @property
    def session_cookie(self) -> str:
        return "__Host-zh_session" if self.secure_cookies else "zh_session_local"

    @property
    def flow_cookie(self) -> str:
        return "__Host-zh_flow" if self.secure_cookies else "zh_flow_local"

    def public(self) -> dict:
        missing, invalid = self.issues()
        collection_missing, collection_invalid = self.collection_issues()
        return {
            "ready": self.ready, "missing": missing, "invalid": invalid,
            "callback_uri": self.redirect_uri if valid_redirect_uri(self.redirect_uri) else LOCAL_REDIRECT_URI,
            "profile_contract_configured": bool(self.profile_url and self.profile_auth and self.profile_id_path),
            "state_fallback_enabled": self.state_fallback == STATE_FALLBACK_COOKIE_BOUND,
            "collections": {"ready": self.collections_ready, "missing": collection_missing, "invalid": collection_invalid},
            "workspace_uri": self.origin + "/workspace/",
            "https_required_for_submission": True,
        }


ENV_FIELDS = {
    "app_id": "ZHIHU_OAUTH_APP_ID",
    "app_key": "ZHIHU_OAUTH_APP_KEY",
    "access_secret": "ZHIHU_ACCESS_SECRET",
    "redirect_uri": "ZHIHU_OAUTH_REDIRECT_URI",
    "profile_url": "ZHIHU_OAUTH_PROFILE_URL",
    "profile_auth": "ZHIHU_OAUTH_PROFILE_AUTH",
    "profile_id_path": "ZHIHU_OAUTH_PROFILE_ID_PATH",
    "profile_name_path": "ZHIHU_OAUTH_PROFILE_NAME_PATH",
    "state_fallback": "ZHIHU_OAUTH_STATE_FALLBACK",
}
FIELD_RULES = {
    "app_id": "仅接受无空白、无控制字符的应用标识。",
    "app_key": "凭证不得包含空白或控制字符。",
    "access_secret": "凭证不得包含空白或控制字符。",
    "redirect_uri": "回调必须使用 HTTPS（本机环回地址可用 HTTP），路径固定 /auth/zhihu/callback，无查询串、片段或尾斜杠。",
    "profile_url": "须为平台确认的 HTTPS 用户信息接口，仅允许 openapi.zhihu.com 或 developer.zhihu.com，无查询串或凭证。",
    "profile_auth": "官方 /user 默认使用 oauth_bearer；仅其他已确认接口使用 platform_headers。",
    "profile_id_path": "官方 /user 的稳定 ID 路径为 /uid；自定义接口须填写已确认的对象字段路径。",
    "profile_name_path": "官方 /user 的昵称路径为 /fullname；显式置空时显示“知乎用户”。",
    "state_fallback": "留空保持严格 state 校验；cookie_bound 表示接受知乎不回传 state 的回调，改用一次性 flow Cookie 关联登录，仅适合临时联调。",
}
