# -*- coding: utf-8 -*-
"""一体化入口：知乎 OAuth 登录门禁 + 个人知识库 / 知乎收藏。

一个进程、一个 origin、一个端口：

    GET /            未登录 → 知乎授权页；已登录 → 「个人知识库」（账号隔离的处理结果）
    GET /account     账号页：收藏夹列表 + 收录公开摘要
    GET /workspace/  「知乎收藏」：同一个人知识库视图，顶部多一条直答处理横幅
    GET /auth/zhihu/callback   与登记值完全一致的授权回调
    其余 /api/*、/lib/*、/笔记库/*、静态资源：未登录一律拒绝

为什么必须同端口：`zhihu_oauth` 的 `require_same_origin` 会拿请求的 Origin
与 `settings.origin`（由 redirect_uri 推导）比对。两个端口就是两个 origin，
登录无法完成。所以这里把授权与会话直接挂进知识库应用，而不是让两个网页互跳。

复用 zhihu_oauth 的会话、/login 和 returnTo 回跳；本文件负责单租户入口的组装与门禁。

数据边界：「个人知识库」与「知乎收藏」读的都是**当前账号**在
`数据/用户工作区.sqlite3` 里的摘要快照与直答知识块（按 stable user id 分账），
不复用 `笔记库/` 那份本地文件库；文件库只在独立的 `工具/服务.py` 里提供。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

工具目录 = Path(__file__).resolve().parent
项目根 = 工具目录.parent
网页目录 = 项目根 / "网页"

if str(工具目录) not in sys.path:
    sys.path.insert(0, str(工具目录))

from zhihu_oauth.app import SECURITY_HEADERS, create_app as 建OAuth应用  # noqa: E402
from zhihu_oauth.config import CALLBACK_PATH, Settings  # noqa: E402
from zhihu_oauth.errors import OAuthError  # noqa: E402
from zhihu_oauth.store import SessionStore  # noqa: E402
from zhihu_oauth.safe_redirect import login_url, request_return_path, requested_return_path  # noqa: E402
from zhihu_oauth.workspace_ui import personal_html  # noqa: E402
from 知乎直答 import 知乎直答客户端  # noqa: E402

# 未登录也放行的路径。授权流程本身必须在门禁之外，否则首次登录无从开始。
公开路径 = frozenset({
    "/",
    "/login",
    "/login/",
    "/favicon.ico",
    "/assets/login-redirect.js",
    "/assets/oauth.css",
    "/assets/oauth.js",
    "/api/status",          # 配置状态：登录页要用它决定按钮是否可用
    "/api/me",              # 由 OAuth 处理器自己返回 login_required
    "/auth/zhihu/start",
    "/auth/zhihu/callback",
    "/auth/logout",
})

# 登录页需要严格 CSP；工作区复用其自带前端，但要求同源且不允许被嵌套。
登录页安全头 = dict(SECURITY_HEADERS)
工作区安全头 = {
    "Cache-Control": "no-store, private",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

def 载入知识库模块():
    """以独立模块名载入 服务.py，拿到它的 FastAPI 应用与索引装载函数。"""
    spec = importlib.util.spec_from_file_location("知识库服务", 工具目录 / "服务.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("无法载入 工具/服务.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["知识库服务"] = module
    spec.loader.exec_module(module)
    return module


def 回调路径(设置: Settings) -> str:
    """回调实际挂在登记地址的路径上；未配置时用项目默认路径。"""
    if 设置.redirect_uri:
        path = urlsplit(设置.redirect_uri).path
        if path:
            return path
    return CALLBACK_PATH


def 建一体化应用(设置: Settings, 会话库: SessionStore, 知识库, provider=None, 直答工厂=None,
                 工作区仓库=None) -> FastAPI:
    """组装「知识库首页 + OAuth 授权 + 账号工作区」。

    `工作区仓库` 只在本地预览/验收夹具里传：夹具必须把工作区数据写进它自己的临时目录，
    否则会落到 `数据/用户工作区.sqlite3`（本机开发库），把假数据混进真数据里。
    """
    # 登录后默认回到账号页（收藏夹列表 + 处理入口），由用户自己决定下一步去哪。
    # 被门禁拦下的页面仍然通过 returnTo 回到原处，不受这里影响。
    授权应用 = 建OAuth应用(settings=设置, store=会话库, provider=provider,
                       default_return_to="/account", 直答工厂=直答工厂, workspace=工作区仓库)
    应用 = FastAPI(title="知乎知识库 · 一体化入口", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=授权应用.router.lifespan_context)
    应用.exception_handlers.update(授权应用.exception_handlers)

    # 「个人知识库」（/）与「知乎收藏」（/workspace/）共用同一套账号隔离视图：
    # 前者只查看处理结果，后者的顶部多一条直答处理横幅。
    个人知识库页 = personal_html()
    回调路径值 = 回调路径(设置)
    门禁路径 = 公开路径 | {回调路径值}

    def 加头(response, 额外: dict | None = None):
        response.headers.update(工作区安全头 if 额外 is None else 额外)
        return response

    def 去登录(request, result=None):
        # Fallback 只在 returnTo 缺失或非法时生效：一律回到账号页，
        # 与 default_return_to="/account" 保持一致。
        target = (requested_return_path(request.query_params, "/account") if "returnTo" in request.query_params
                  else request_return_path(request, "/account"))
        # 裸访问知识库首页（无 query/片段）时不要回首页：授权完先让用户看到账号页，
        # 在那里绑定账号、选收藏夹，再自己决定进知识库还是工作区。
        # 带参数的链接仍然"回原处"，不破坏既有的 returnTo 契约。
        if "?" not in target and target.rstrip("/") in {"", "/index.html"}:
            target = "/account"
        return 加头(RedirectResponse(login_url(target, "/account", result), status_code=303), 登录页安全头)

    @应用.get("/")
    @应用.get("/index.html")
    async def 首页(request: Request):
        if "returnTo" in request.query_params:
            return 去登录(request)
        try:
            会话库.get(request.cookies.get(设置.session_cookie))
        except OAuthError as exc:
            return 去登录(request, exc.code)
        return HTMLResponse(个人知识库页, headers=工作区安全头)

    @应用.middleware("http")
    async def 登录门禁(request: Request, call_next):
        path = request.url.path
        if path not in 门禁路径:
            try:
                会话库.get(request.cookies.get(设置.session_cookie))
            except OAuthError as exc:
                # Pages keep path + query; fragments are recovered by login-redirect.js.
                是页面 = request.method == "GET" and (not os.path.splitext(path)[1] or path.endswith(".html"))
                if 是页面 and not path.startswith(("/api/", "/auth/", "/笔记库/", "/assets/", "/lib/", "/oauth/")):
                    return 去登录(request, exc.code)
                return 加头(JSONResponse(exc.public(), status_code=exc.status))
        response = await call_next(request)
        是授权响应 = path.startswith(("/api/", "/auth/", "/assets/")) or path in {
            "/login", "/login/", "/account", "/account/", 回调路径值}
        加头(response, 登录页安全头 if 是授权响应 else 工作区安全头)
        if 设置.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    # 授权路由：跳过它自带的首页，其余原样挂到本应用（回调路径按登记值）。
    已挂回调 = False
    for 路由 in 授权应用.routes:
        路径 = getattr(路由, "path", None)
        if 路径 == "/":
            continue
        if 路径 == CALLBACK_PATH:
            已挂回调 = True
            if 路径 != 回调路径值:
                continue
        应用.routes.append(路由)

    # 知识库路由：跳过它的两个挂载点，末尾按顺序重建，保证 / 门禁优先命中。
    for 路由 in 知识库.app.routes:
        if 路由.__class__.__name__ == "Mount":
            continue
        应用.routes.append(路由)

    应用.mount("/lib", StaticFiles(directory=str(网页目录 / "lib")), name="lib")
    应用.mount("/", StaticFiles(directory=str(网页目录), html=True), name="网页")

    # 登记的回调地址不是项目默认路径时，把同一个处理器再挂一次。
    if 回调路径值 != CALLBACK_PATH and 已挂回调:
        原处理器 = next(路由.endpoint for 路由 in 授权应用.routes if getattr(路由, "path", None) == CALLBACK_PATH)
        应用.add_api_route(回调路径值, 原处理器, methods=["GET"], name="zhihu_callback_registered")
    elif not 已挂回调:
        raise RuntimeError("授权应用未提供回调处理器")

    应用.state.会话库 = 会话库
    应用.state.设置 = 设置
    return 应用


def 主函数() -> None:
    parser = argparse.ArgumentParser(description="知乎授权登录 + 知识库工作区（一体化）")
    parser.add_argument("--端口", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--不开浏览器", action="store_true")
    args = parser.parse_args()

    # 本机默认回调跟随实际端口；两个端口就是两个 origin，授权无法跨端口完成。
    if not os.environ.get("ZHIHU_OAUTH_REDIRECT_URI", "").strip():
        环回 = "localhost" if args.host in {"localhost", "::1"} else "127.0.0.1"
        os.environ["ZHIHU_OAUTH_REDIRECT_URI"] = f"http://{环回}:{args.端口}{CALLBACK_PATH}"

    设置 = Settings.from_env()
    会话库 = SessionStore()
    知识库 = 载入知识库模块()
    知识库.载入索引()
    应用 = 建一体化应用(设置, 会话库, 知识库, 直答工厂=知乎直答客户端)

    地址 = f"http://{args.host}:{args.端口}"
    print(f"  一体化入口：{地址}", flush=True)
    print(f"  登录回调：{设置.redirect_uri or '未配置（使用默认 ' + CALLBACK_PATH + '）'}", flush=True)
    直答 = 知乎直答客户端()
    if 直答.可用:
        print(f"  直答引擎：{直答.配置.model}（个人知识库里可一键处理收藏夹）", flush=True)
    else:
        print("  直答引擎：未配置（缺少 ZHIHU_ACCESS_SECRET）；不影响登录与摘要收录", flush=True)
    if 设置.issues()[0] or 设置.issues()[1]:
        缺失, 非法 = 设置.issues()
        print(f"  配置未就绪：缺少 {len(缺失)} 项，非法 {len(非法)} 项；登录按钮将保持禁用。", flush=True)

    if not args.不开浏览器:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(地址)).start()

    import uvicorn
    uvicorn.run(应用, host=args.host, port=args.端口, workers=1, access_log=False, log_level="warning")


if __name__ == "__main__":
    主函数()
