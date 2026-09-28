# -*- coding: utf-8 -*-
"""Loopback-only returnTo acceptance fixture. NEVER DEPLOY.

Uses the real start/state/callback/session routes with fictional identities.
The separate fixture buttons replace ONLY the external Zhihu authorization step.
No environment credentials, author vault, real user database or network provider.

python -X utf8 工具/tests/serve_return_to_preview.py --mode personal --port 8112
python -X utf8 工具/tests/serve_return_to_preview.py --mode integrated --port 8113
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zhihu_oauth.app import create_app
from zhihu_oauth.config import Settings
from zhihu_oauth.errors import OAuthError
from zhihu_oauth.store import SessionStore
from zhihu_oauth.workspace import WorkspaceRepository, workspace_graph, workspace_index
from test_oauth import FakeProvider, TEST_SETTINGS
from test_login_redirect import integrated


class FixtureSettings(Settings):
    # Cookies are NOT port-scoped. Distinct names avoid touching 8099/8100 sessions.
    @property
    def session_cookie(self):
        return f"returnto_fixture_{urlsplit(self.redirect_uri).port}_session"

    @property
    def flow_cookie(self):
        return f"returnto_fixture_{urlsplit(self.redirect_uri).port}_flow"


class FixtureClock:
    offset = 0

    def __call__(self):
        return time.time() + self.offset


SCRIPT = r"""
'use strict';
(() => {
  const panel = document.getElementById('return-to-fixture');
  const status = document.getElementById('fixture-status');
  const target = () => location.pathname.startsWith('/login')
    ? new URLSearchParams(location.search).get('returnTo')
    : location.pathname + location.search + location.hash;
  const display = document.getElementById('fixture-location');
  display.textContent = location.pathname + location.search + location.hash;
  window.addEventListener('hashchange', () => { display.textContent = location.pathname + location.search + location.hash; });
  panel.addEventListener('click', async event => {
    const button = event.target.closest('button[data-scenario]');
    if (!button) return;
    button.disabled = true;
    try {
      const scenario = button.dataset.scenario;
      const response = await fetch('/__fixture/' + (scenario === 'expire' ? 'expire' : 'start'), {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario, return_to: target()}),
      });
      if (!response.ok) throw new Error('fixture failure');
      const data = await response.json();
      if (scenario === 'expire') location.reload();
      else location.assign(data.callback_url);
    } catch { status.textContent = '模拟步骤失败，请检查夹具服务。'; button.disabled = false; }
  });
  // Never send even a synthetic authorization to the external provider.
  document.getElementById('login')?.addEventListener('click', event => {
    event.stopImmediatePropagation(); event.preventDefault();
    status.textContent = '真实授权按钮在隔离夹具中停用；请使用上方模拟按钮。';
  }, true);
})();
"""
STYLE = """
#return-to-fixture{position:relative;z-index:100;max-height:130px;overflow:auto;padding:8px 16px;
background:#fff2c8;color:#644810;border-bottom:2px dashed #bb923a;font:12px/1.6 sans-serif;flex-shrink:0}
#return-to-fixture strong{font-size:14px}#return-to-fixture button,#return-to-fixture a{margin:4px 8px 4px 0}
#fixture-location{display:block;overflow-wrap:anywhere;font:11px/1.5 monospace}
"""


def make_preview(mode: str, port: int):
    origin = f"http://127.0.0.1:{port}"
    settings = FixtureSettings(**{**asdict(TEST_SETTINGS), "redirect_uri": origin + "/auth/zhihu/callback"})
    clock = FixtureClock()
    store = SessionStore(clock)
    provider = FakeProvider(clock, settings)
    temporary = tempfile.TemporaryDirectory(prefix="return-to-preview-")
    temporary_root = Path(temporary.name).resolve()
    workspace = WorkspaceRepository(temporary_root / "mock-workspace.sqlite3")
    if mode == "personal":
        inner = create_app(settings, provider, store, workspace)
        default_target = "/workspace/"
    else:
        knowledge = SimpleNamespace(app=FastAPI())

        @knowledge.app.get("/api/索引")
        async def empty_index():
            return workspace_index([])

        @knowledge.app.get("/api/图谱")
        async def empty_graph():
            return workspace_graph([])

        with patch.object(integrated, "建OAuth应用", lambda **kwargs: create_app(workspace=workspace, **kwargs)):
            inner = integrated.建一体化应用(settings, store, knowledge, provider)
        default_target = "/"

    @asynccontextmanager
    async def lifespan(_app):
        try:
            async with inner.router.lifespan_context(inner):
                yield
        finally:
            # Verify the final recursive-cleanup target, not a string-built shell path.
            resolved = Path(temporary.name).resolve()
            if resolved != temporary_root or not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise RuntimeError("Unsafe fixture cleanup target")
            temporary.cleanup()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.stop = lambda: None
    banner = f"""<aside id="return-to-fixture"><strong>仅模拟回跳验收 · {mode} · 非真实知乎授权</strong>
<button type="button" data-scenario="a">模拟 A 授权成功</button>
<button type="button" data-scenario="b">模拟 B 授权成功</button>
<button type="button" data-scenario="cancel">模拟取消授权</button>
<button type="button" data-scenario="permission">模拟收藏接口 403</button>
<button type="button" data-scenario="expire">模拟 Token 过期</button>
<a href="{default_target}?view=graph#tag%3AreturnTo">打开工作区深链接</a>
<a href="/login?returnTo=%2F%2Foutside.invalid">测试外站目标拦截</a>
<span id="fixture-status"></span><code id="fixture-location"></code></aside>"""

    @app.middleware("http")
    async def loopback_fixture(request: Request, call_next):
        if request.client.host not in {"127.0.0.1", "::1"} or request.headers.get("host") != urlsplit(origin).netloc:
            return Response(status_code=403)
        if request.method == "POST" and request.headers.get("origin") != origin:
            return Response(status_code=403)
        response = await call_next(request)
        if response.status_code == 200 and response.headers.get("content-type", "").startswith("text/html"):
            body = (b"".join([chunk async for chunk in response.body_iterator])).decode("utf-8")
            body = body.replace("</head>", '<link rel="stylesheet" href="/__fixture/style.css"></head>')
            body = re.sub(r"<body(?:\s[^>]*)?>", lambda match: match[0] + banner, body, count=1)
            body = body.replace("</body>", '<script src="/__fixture/script.js" defer></script></body>')
            headers = {key: value for key, value in response.headers.items() if key != "content-length"}
            return Response(body, media_type="text/html", headers=headers)
        return response

    @app.get("/__fixture/script.js")
    async def script():
        return Response(SCRIPT, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/__fixture/style.css")
    async def style():
        return Response(STYLE, media_type="text/css")

    @app.post("/__fixture/start")
    async def start(request: Request):
        data = await request.json()
        scenario = data.get("scenario")
        if scenario not in {"a", "b", "cancel", "permission"}:
            return Response(status_code=400)
        provider.read_error = OAuthError("permission_denied", 403) if scenario == "permission" else None
        cookies = {name: request.cookies[name] for name in (settings.session_cookie, settings.flow_cookie) if name in request.cookies}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=inner), base_url=origin, cookies=cookies) as client:
            started = await client.post("/auth/zhihu/start", json={"return_to": data.get("return_to")}, headers={"Origin": origin})
        if started.status_code != 200:
            return Response(status_code=500)
        state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
        query = {"state": state, **({"error": "access_denied"} if scenario == "cancel" else {"authorization_code": "b" if scenario == "b" else "a"})}
        response = JSONResponse({"callback_url": "/auth/zhihu/callback?" + urlencode(query)})
        for cookie in started.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", cookie)
        return response

    @app.post("/__fixture/expire")
    async def expire():
        clock.offset += 601
        return {"synthetic_expiration": True}

    @app.post("/__fixture/stop")
    async def stop():
        app.state.stop()
        return {"stopping": True}

    app.mount("/", inner)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["personal", "integrated"], default="personal")
    parser.add_argument("--port", type=int, default=8112)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged loopback test port")
    app = make_preview(args.mode, args.port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1, access_log=False, log_level="warning"))
    app.state.stop = lambda: setattr(server, "should_exit", True)
    print(f"MOCK ONLY: {args.mode} at http://127.0.0.1:{args.port}/login; no real OAuth", flush=True)
    server.run()
