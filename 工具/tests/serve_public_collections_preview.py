# -*- coding: utf-8 -*-
"""Loopback-only UI fixtures. Never deploy; no real OAuth, credentials, or network.

Run directly, then open http://127.0.0.1:8101/__fixture/a .
Production assets and API routes are reused with an entirely synthetic provider.
"""
from __future__ import annotations

import atexit
import re
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zhihu_oauth.app import WEB_ROOT, create_app
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.provider import TokenGrant
from zhihu_oauth.store import SessionStore
from zhihu_oauth.workspace import WorkspaceRepository
from zhihu_oauth.workspace_ui import workspace_html
from test_oauth import TEST_SETTINGS, collection
from test_public_collections import PublicFakeProvider, payload, raw_item

ORIGIN = "http://127.0.0.1:8101"
SCENARIOS = {"a": "模拟账号 A", "b": "模拟账号 B", "empty": "空公开列表", "expired": "Token 失效", "long": "50 夹与长文本"}
settings = replace(TEST_SETTINGS, redirect_uri=ORIGIN + "/auth/zhihu/callback")
store = SessionStore(time.time)


class PreviewProvider(PublicFakeProvider):
    def __init__(self):
        super().__init__(time.time)
        self.scenario = "未选择"
        self.retry_seen = False

    def configure(self, name):
        self.scenario = SCENARIOS[name]
        self.retry_seen = False
        self.content_errors.clear()
        self.items = {"mock-token-a": [collection(101, "阅读与思考 · 模拟数据"),
                      collection(303, "空收藏夹 · 模拟数据"), collection(404, "接口权限受限 · 模拟数据"),
                      collection(505, "分页失败后重试 · 模拟数据"),
                      {**collection(999, "私密标题不得显示"), "is_public": False}],
                      "mock-token-b": [collection(202, "B 账号公开收藏 · 模拟数据")]}
        self.pages = {
            ("mock-token-a", "101", "0"): payload([
                raw_item(11, "article", Title="把阅读变成思考：怎样整理自己的收藏？（模拟）", Summary="这是浏览器验收使用的虚构摘要，不来自真实知乎账号。\n这里只展示公开接口返回的标题和摘要；阅读完整内容需要前往知乎。"),
                raw_item(12, Title="为什么收藏很多，真正读完的却不多？（模拟）", Summary="从一个小主题开始，按需读取而不是一次加载全部内容。下面的“加载更多”使用接口返回的游标。")], end=False, next_offset="00027", total=3),
            ("mock-token-a", "101", "00027"): payload([raw_item(12), raw_item(13, "zvideo", Title="建立自己的知识索引（第二页模拟）", Summary="第二页带有一个重复条目，页面只追加尚未展示的内容。")], total=3),
            ("mock-token-a", "303", "0"): payload([], total=0),
            ("mock-token-a", "505", "0"): payload([raw_item(51, Title="已读取的第一页（模拟）")], end=False, next_offset="27", total=2),
            ("mock-token-a", "505", "27"): payload([raw_item(52, Title="重试后读取的第二页（模拟）")], total=2),
            ("mock-token-b", "202", "0"): payload([raw_item(21, Title="B 账号独立摘要（模拟）", Summary="这条内容只出现在测试账号 B 的响应中。")]),
        }
        if name == "empty":
            self.items["mock-token-a"] = []
        if name == "expired":
            self.content_errors["mock-token-a"] = OAuthError("authorization_failed", 401)
        if name == "long":
            first = collection(9223372036854775807, "长标题压力测试（模拟）" + "阅读索引与内容整理" * 32)
            first["description"] = "CollectionDescriptionWithoutSpaces_" * 80
            self.items["mock-token-a"] = [first] + [collection(i + 1, f"第 {i + 2:02d} 个公开夹（模拟）") for i in range(49)]
            for item in self.items["mock-token-a"]:
                self.pages[("mock-token-a", item["id"], "0")] = payload([
                    raw_item(81, Title="<img src=x onerror=alert(1)> 仅显示文字 · " + "LongTitleWithoutSpaces" * 10,
                             Summary=("这是一段较长的模拟摘要，用于验证窄屏、自动换行与滚动阅读。\n" * 100),
                             Author={"Name": "模拟作者" * 25}, LikeCount=9223372036854775807)])

    async def collection_contents(self, token, identifier, offset):
        if identifier == "404":
            raise OAuthError("permission_denied", 403)
        if identifier == "505" and offset == "27" and not self.retry_seen:
            self.retry_seen = True
            raise OAuthError("rate_limited", 429)
        return await super().collection_contents(token, identifier, offset)


provider = PreviewProvider()
preview_directory = tempfile.TemporaryDirectory(prefix="zhihu-workspace-preview-")
preview_root = Path(preview_directory.name).resolve()
if not preview_root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
    raise RuntimeError("Preview directory must remain under the system temporary directory")
atexit.register(preview_directory.cleanup)
workspace = WorkspaceRepository(preview_root / "workspace.sqlite3")


def preview_workspace_html():
    return workspace_html().replace(
        "公开摘要快照 · 非全文",
        "仅模拟验收 · 非真实知乎授权 · 公开摘要快照 · 非全文",
    )


with patch("zhihu_oauth.app.workspace_html", preview_workspace_html):
    app = create_app(settings, provider, store, workspace)


async def preview_page():
    source = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    links = " · ".join(f'<a href="/__fixture/{name}">{label}</a>' for name, label in SCENARIOS.items())
    banner = '<aside class="preview-banner"><strong>仅模拟验收 · 非真实知乎授权</strong><br>'
    banner += f'当前：{provider.scenario}。不连接知乎，不读取任何真实账号。<nav aria-label="模拟场景">{links}</nav></aside>'
    source = source.replace("</head>", '<link rel="stylesheet" href="/__fixture.css"></head>')
    return HTMLResponse(re.sub(r"<body(?:\s[^>]*)?>", lambda match: match[0] + banner, source, count=1))


async def disabled_real_login():
    return JSONResponse(OAuthError("configuration_required", 503).public(), status_code=503)


app.router.routes.insert(0, APIRoute("/", preview_page, methods=["GET"]))
app.router.routes.insert(0, APIRoute("/auth/zhihu/start", disabled_real_login, methods=["POST"]))


@app.get("/__fixture.css")
async def preview_style():
    return Response(".preview-banner{max-width:1240px;margin:16px auto 0;padding:12px 18px;border:1px dashed #a3792c;background:#fff3cf;color:#745219;font:12px/1.8 'Microsoft YaHei',sans-serif}.preview-banner nav{display:flex;gap:8px;flex-wrap:wrap}.preview-banner a{color:inherit}@media(max-width:700px){.preview-banner{margin:12px 16px 0}}", media_type="text/css")


@app.get("/__fixture/{name}")
async def fixture(name: str):
    if name not in SCENARIOS:
        return Response(status_code=404)
    provider.configure(name)
    store.clear()
    token = "mock-token-b" if name == "b" else "mock-token-a"
    handle, _session = store.create(TokenGrant(token, time.time() + 600), provider.users[token])
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(settings.session_cookie, handle, httponly=True, samesite="lax", max_age=600, path="/")
    return response


if __name__ == "__main__":
    print("TEST FIXTURES ONLY — 127.0.0.1:8101; no real OAuth; do not deploy", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8101, workers=1, access_log=False, log_level="warning")
