"""OAuth, public collections and identity-isolated workspaces in one origin."""
from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
from html import escape
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .collection_data import (
    COLLECTIONS_LIMIT, CONTENT_PAGE_SIZE, CONTENT_SCOPE_NOTE, PUBLIC_SCOPE_NOTE, USER_CONTENTS_TYPES,
    collection_request,
)
from .config import ACCESS_SECRET_URL, CALLBACK_PATH, STATE_FALLBACK_COOKIE_BOUND, Settings, origin_of
from .errors import AUTH_FAILURES, MESSAGES, OAuthError
from .provider import Provider, ZhihuProvider
from .safe_redirect import login_url, request_return_path, requested_return_path, safe_return_path
from .store import FLOW_TTL, SESSION_TTL, SessionStore
from .workspace import (WORKSPACE_LIMIT, WorkspaceRepository, workspace_collections, workspace_creations,
                        workspace_index, workspace_graph, workspace_note, workspace_search)
from .workspace_ui import workspace_assets, workspace_html
from .直答处理 import 任务登记处, 运行任务, 查今日剩余额度
from .直答凭证 import 直答凭证库

WEB_ROOT = Path(__file__).resolve().parents[2] / "网页" / "oauth"
# The account page is the only page that must stay reachable *while signed in*:
# /login deliberately bounces an authenticated visitor to its destination, so it
# can never host the collection list or the "import this page" action.
ACCOUNT_PATH = "/account"
SECURITY_HEADERS = {
    "Cache-Control": "no-store, private", "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
}


def create_app(settings: Settings | None = None, provider: Provider | None = None,
               store: SessionStore | None = None, workspace: WorkspaceRepository | None = None,
               default_return_to: str = "/workspace/", 直答工厂=None) -> FastAPI:
    """Build the authorisation app.

    `default_return_to` is where a login lands when the browser asked for no
    specific destination. The integrated entry point overrides it with "/account"
    so that logging in lands on the account page.

    `直答工厂` is an optional zero-argument factory returning a client that exposes
    `可用` and `归并摘要`. It is injected by the integrated entry point so this
    package keeps no hard dependency on the knowledge-side 直答 module.
    """
    default_return_to = safe_return_path(default_return_to, "/workspace/")
    settings = settings or Settings.from_env()
    store = store or SessionStore()
    workspace = workspace or WorkspaceRepository()
    client = None
    if provider is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            headers={"Accept": "application/json"},
        )
        provider = ZhihuProvider(settings, client, store.clock)

    async def sweep():
        while True:
            await asyncio.sleep(30)
            store.prune()

    @asynccontextmanager
    async def lifespan(_app):
        janitor = asyncio.create_task(sweep())
        try:
            yield
        finally:
            janitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await janitor
            store.clear()
            if client is not None:
                await client.aclose()

    app = FastAPI(title="知乎收藏 · 个人知识库", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    assets = {name: (WEB_ROOT / name).read_text(encoding="utf-8")
              for name in ("index.html", "oauth.css", "oauth.js", "login-redirect.js",
                           "settings.html", "settings.js")}
    assets["index.html"] = assets["index.html"].replace(
        'data-default-return-to="/workspace/"', 'data-default-return-to="' + escape(default_return_to, quote=True) + '"')
    # Same markup, but a logged-out visitor here lands back on the account page
    # rather than on whatever the deployment uses as its main workspace.
    account_html = assets["index.html"].replace(
        'data-default-return-to="' + escape(default_return_to, quote=True) + '"',
        'data-default-return-to="' + ACCOUNT_PATH + '"')

    personal_assets = workspace_assets()
    personal_html = workspace_html()
    直答任务登记处 = 任务登记处()
    凭证库 = 直答凭证库()

    def set_cookie(response, name: str, value: str, ttl: int):
        response.set_cookie(name, value, max_age=ttl, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax")

    def clear_cookie(response, name: str):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax")

    def clear_session(response, request):
        store.revoke(request.cookies.get(settings.session_cookie))
        clear_cookie(response, settings.session_cookie)

    def require_same_origin(request):
        # POST + exact Origin also protects pre-login requests; no cross-origin CORS.
        if request.headers.get("origin") != settings.origin:
            raise OAuthError("origin_rejected", 403)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise OAuthError("csrf_rejected", 403)

    def request_session(request):
        return store.get(request.cookies.get(settings.session_cookie))

    def require_csrf(request, session):
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied.isascii() or not secrets.compare_digest(session.csrf, supplied):
            raise OAuthError("csrf_rejected", 403)

    def workspace_session(request):
        session = request_session(request)
        supplied = request.headers.get("x-workspace-session", "")
        if not supplied.isascii() or not secrets.compare_digest(session.csrf, supplied):
            # A stale tab must neither read another account nor revoke its new cookie.
            raise OAuthError("workspace_session_changed", 409)
        return session

    @app.middleware("http")
    async def secure_boundary(request: Request, call_next):
        try:
            if origin_of(str(request.url)) != settings.origin:
                raise OAuthError("origin_rejected", 400)
            if len(request.scope.get("query_string", b"")) > 8192:
                raise OAuthError("invalid_callback", 400)
            response = await call_next(request)
        except OAuthError as exc:
            response = JSONResponse(exc.public(), status_code=exc.status)
        except Exception:
            # Do not log raw exception/request objects from an auth flow.
            response = JSONResponse(OAuthError("internal_error", 500).public(), status_code=500)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path in {"/workspace", "/workspace/"} and response.status_code == 200:
            response.headers["Content-Security-Policy"] = SECURITY_HEADERS["Content-Security-Policy"].replace("style-src 'self'", "style-src 'self' 'unsafe-inline'; font-src 'self'")
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(OAuthError)
    async def safe_error(request: Request, exc: OAuthError):
        response = JSONResponse(exc.public(), status_code=exc.status)
        if exc.code in AUTH_FAILURES:
            # A late API response may belong to a previous browser login. Revoke only
            # its server session; never overwrite a newer cookie via Set-Cookie.
            store.revoke(request.cookies.get(settings.session_cookie))
        if exc.code in {"rate_limited", "quota_exceeded", "server_busy"}:
            response.headers["Retry-After"] = "60"
        return response

    @app.get("/")
    async def page(request: Request):
        # Old /?returnTo links remain valid, but the login URL stays a login URL.
        if "returnTo" in request.query_params:
            return RedirectResponse(login_url(requested_return_path(request.query_params, default_return_to), default_return_to), status_code=303)
        return HTMLResponse(assets["index.html"])

    @app.get("/login")
    @app.get("/login/")
    async def login_page(request: Request):
        target = requested_return_path(request.query_params, default_return_to)
        try:
            request_session(request)
        except OAuthError:
            return HTMLResponse(assets["index.html"])
        return RedirectResponse(target, status_code=303)

    @app.get(ACCOUNT_PATH)
    @app.get(ACCOUNT_PATH + "/")
    async def account_page(request: Request):
        # Stay reachable after login: this is where the current account's public
        # collections are listed and one page of summaries is imported.
        try:
            request_session(request)
        except OAuthError as exc:
            response = RedirectResponse(
                login_url(request_return_path(request, ACCOUNT_PATH), ACCOUNT_PATH, exc.code), status_code=303)
            clear_session(response, request)
            return response
        return HTMLResponse(account_html)

    @app.get("/settings")
    @app.get("/settings/")
    async def settings_page(request: Request):
        """直答凭证设置：填自己的 Access Secret，用自己���额度。"""
        try:
            request_session(request)
        except OAuthError as exc:
            response = RedirectResponse(login_url("/settings", ACCOUNT_PATH, exc.code), status_code=303)
            clear_session(response, request)
            return response
        return HTMLResponse(assets["settings.html"])

    @app.get("/assets/oauth.css")
    async def stylesheet():
        return Response(assets["oauth.css"], media_type="text/css")

    @app.get("/assets/oauth.js")
    async def javascript():
        return Response(assets["oauth.js"], media_type="text/javascript")

    @app.get("/assets/login-redirect.js")
    async def redirect_javascript():
        return Response(assets["login-redirect.js"], media_type="text/javascript")

    @app.get("/assets/settings.js")
    async def settings_javascript():
        return Response(assets["settings.js"], media_type="text/javascript")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get("/api/status")
    async def status():
        return {
            "mode": "personal_knowledge_workspace", "configuration": settings.public(),
            "messages": MESSAGES,
            "limitations": {"collections_limit": COLLECTIONS_LIMIT, "pagination_supported": False,
                            "content_page_size": CONTENT_PAGE_SIZE, "content_pagination_supported": True,
                            "public_data_only_guaranteed": True, "persistent_sessions": False, "full_text_supported": False,
                             "workspace_user_limit": WORKSPACE_LIMIT, "workspace_storage": "per_user_summary_snapshots"},
        }

    @app.post("/auth/zhihu/start")
    async def start(request: Request):
        require_same_origin(request)
        if not settings.ready:
            raise OAuthError("configuration_required", 503)
        # All browser input is untrusted. Validate the JSON destination and bind
        # it to state; the callback cannot replace it through a query parameter.
        return_to = default_return_to
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024:
                raise OAuthError("invalid_start_request", 400)
        if body:
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise OAuthError("invalid_start_request", 400) from None
            if not isinstance(payload, dict) or not set(payload) <= {"return_to"}:
                raise OAuthError("invalid_start_request", 400)
            candidate = payload.get("return_to")
            if candidate is not None and not isinstance(candidate, str):
                raise OAuthError("invalid_start_request", 400)
            return_to = safe_return_path(candidate, default_return_to)
        flow, state = store.begin(request.cookies.get(settings.flow_cookie), return_to)
        response = JSONResponse({"authorization_url": provider.authorize_url(state)})
        clear_session(response, request)
        set_cookie(response, settings.flow_cookie, flow, FLOW_TTL)
        return response

    @app.get(CALLBACK_PATH)
    async def callback(request: Request):
        result = "connected"
        response = None
        claimed = None
        try:
            if not settings.ready:
                raise OAuthError("configuration_required", 503)
            query = request.query_params
            for key in ("state", "authorization_code", "code", "error"):
                if len(query.getlist(key)) > 1:
                    raise OAuthError("invalid_callback")
            state = query.get("state")
            if state and (len(state) > 128 or not state.isascii()):
                raise OAuthError("state_invalid")
            flow_handle = request.cookies.get(settings.flow_cookie)
            # Documented platform behaviour: the callback carries only
            # `authorization_code`. Strict rejection stays the default; the
            # cookie-bound fallback must be enabled explicitly by the operator.
            claimed = store.consume(
                flow_handle, state,
                allow_stateless=settings.state_fallback == STATE_FALLBACK_COOKIE_BOUND,
            )
            if "error" in query:
                if "authorization_code" in query or "code" in query:
                    raise OAuthError("invalid_callback")
                name = "authorization_cancelled" if query.get("error") == "access_denied" else "authorization_failed"
                raise OAuthError(name)
            if "authorization_code" in query and "code" in query:
                raise OAuthError("invalid_callback")
            code = query.get("authorization_code", query.get("code", ""))
            if not code:
                raise OAuthError("missing_code")
            if len(code) > 4096 or not code.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in code):
                raise OAuthError("invalid_callback")
            grant = await provider.exchange_code(code)
            if grant.expires_at <= store.clock():
                raise OAuthError("token_expired", 401)
            user = await provider.identify(grant.access_token)
            handle, _session = store.complete(flow_handle, claimed, grant, user)
            # The destination was sanitised at /start and lives server-side, so
            # the callback query string cannot rewrite where this lands.
            response = RedirectResponse(safe_return_path(claimed.return_to, default_return_to), status_code=303)
            clear_session(response, request)
            set_cookie(response, settings.session_cookie, handle, SESSION_TTL)
        except OAuthError as exc:
            result = exc.code
        except Exception:
            result = "internal_error"
        finally:
            store.cancel(request.cookies.get(settings.flow_cookie))
        if response is None:
            # Only a validated state can supply a retry destination.
            target = claimed.return_to if claimed is not None else default_return_to
            response = RedirectResponse(login_url(target, default_return_to, result), status_code=303)
            # A failed callback cannot leave this browser reading an older account.
            clear_session(response, request)
        clear_cookie(response, settings.flow_cookie)
        return response

    @app.get("/api/me")
    async def me(request: Request):
        session = request_session(request)
        return {
            "authenticated": True, "user": session.user.public(),
            "expires_at": datetime.fromtimestamp(session.expires_at, timezone.utc).isoformat(),
            "csrf_token": session.csrf,
            # Honest labelling required by the hackathon OAuth acceptance rules:
            # a cookie-bound login without platform `state` is debugging-only.
            "state_mode": "cookie_bound" if session.stateless else "state",
        }

    async def accessible_collections(request, session):
        items = await provider.collections(session.access_token)
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        return [item for item in items if item.get("is_public") is True]

    @app.get("/api/collections")
    async def collections(request: Request):
        session = request_session(request)
        items = await accessible_collections(request, session)
        return {"user": session.user.public(), "items": items, "limit": COLLECTIONS_LIMIT,
                "has_more": None, "scope": "public", "scope_note": PUBLIC_SCOPE_NOTE}

    async def authorized_content_page(request, session, identifier, offset):
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        identifier, offset = collection_request(identifier, offset)
        # No client-supplied account, URL, or cached membership is trusted. Recheck each page.
        items = await accessible_collections(request, session)
        selected = next((item for item in items if item["id"] == identifier), None)
        if selected is None:
            raise OAuthError("collection_unavailable", 404)
        page = await provider.collection_contents(session.access_token, identifier, offset)
        # Logout, account switch, or expiry during either upstream request suppresses data.
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        return selected, page

    async def authorized_creation_page(request, session, content_type, offset):
        """创作没有"收藏夹"这层归属，但会话的二次校验一步都不能省。"""
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        page = await provider.creations(session.access_token, content_type, offset)
        # Logout, account switch, or expiry during the upstream request suppresses data.
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        return page

    @app.get("/api/collections/{identifier}/contents")
    async def collection_contents(identifier: str, request: Request):
        session = request_session(request)
        if len(request.query_params.getlist("offset")) > 1:
            raise OAuthError("invalid_collection_request", 400)
        selected, page = await authorized_content_page(request, session, identifier, request.query_params.get("offset", "0"))
        return {"user": session.user.public(), "collection": selected, **page.public(),
                "scope": "public", "scope_note": CONTENT_SCOPE_NOTE}

    @app.get("/workspace")
    @app.get("/workspace/")
    async def personal_workspace(request: Request):
        try:
            request_session(request)
        except OAuthError as exc:
            response = RedirectResponse(login_url(request_return_path(request, default_return_to), default_return_to, exc.code), status_code=303)
            clear_session(response, request)
            return response
        return HTMLResponse(personal_html)

    @app.get("/workspace/assets/{name:path}")
    async def personal_asset(name: str):
        path = personal_assets.get(name)
        if path is None:
            return Response(status_code=404)
        return Response(path.read_bytes(), media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def personal_records(request):
        session = workspace_session(request)
        records = workspace.records(session.user.subject)
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        return records

    @app.get("/api/workspace/索引")
    async def personal_index(request: Request):
        return workspace_index(personal_records(request))

    @app.get("/api/workspace/收藏夹")
    async def personal_collections(request: Request):
        """「知乎收藏」页的卡片列表数据：按收藏夹分组的内容快照（纯读，不联网）。"""
        return workspace_collections(personal_records(request))

    @app.get("/api/workspace/创作")
    async def personal_creations(request: Request):
        """「我的创作」卡片数据：按内容类型分组。

        刻意不并入收藏的图谱 / 索引——收藏是"别人写的、我收的"，创作是"我写的"，
        织进同一张关系网会把两种语义混在一起。
        """
        session = workspace_session(request)
        records = workspace.creations(session.user.subject)
        if request_session(request) is not session:
            raise OAuthError("session_expired", 401)
        return workspace_creations(records)

    @app.get("/api/workspace/图谱")
    async def personal_graph(request: Request):
        return workspace_graph(personal_records(request))

    @app.get("/api/workspace/笔记")
    async def personal_note(request: Request):
        records = personal_records(request)
        paths = request.query_params.getlist("路径")
        if len(paths) != 1 or len(paths[0]) > 128:
            raise OAuthError("note_unavailable", 404)
        return workspace_note(records, paths[0])

    @app.get("/api/workspace/搜索")
    async def personal_search(request: Request):
        return workspace_search(personal_records(request), request.query_params.get("q", "")[:200])

    @app.post("/api/workspace/import")
    async def import_summary_page(request: Request):
        require_same_origin(request)
        session = request_session(request)
        require_csrf(request, session)
        # Bound the streaming body before parsing; do not accept arbitrary content, URL or uid.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024:
                raise OAuthError("invalid_import_request", 400)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise OAuthError("invalid_import_request", 400) from None
        if not isinstance(data, dict) or set(data) != {"collection_id", "offset"}:
            raise OAuthError("invalid_import_request", 400)
        selected, page = await authorized_content_page(request, session, data["collection_id"], data["offset"])
        result = workspace.import_page(session.user.subject, selected, page.items)
        return {"user": session.user.public(), "imported": result, "offset": page.offset,
                "workspace_url": "/workspace/", "source": "public_summary"}

    @app.post("/api/workspace/import-creations")
    async def import_creation_page(request: Request):
        """同步一页自己的创作 —— 与收藏并列的数据源，不是收藏夹的一种。"""
        require_same_origin(request)
        session = request_session(request)
        if session is None:
            raise OAuthError("login_required", 401)
        require_csrf(request, session)
        data = await 读取受限body(request, 512, "invalid_creation_request")
        if not isinstance(data, dict) or set(data) - {"offset", "content_type"}:
            raise OAuthError("invalid_creation_request", 400)
        类型 = str(data.get("content_type") or "all")
        if 类型 not in USER_CONTENTS_TYPES:
            raise OAuthError("invalid_creation_request", 400)
        page = await authorized_creation_page(request, session, 类型, str(data.get("offset") or "0"))
        result = workspace.import_creations(session.user.subject, page.items)
        return {"user": session.user.public(), "imported": result, "offset": page.offset,
                "workspace_url": "/workspace/", "source": "creation"}

    async def 读取受限body(request: Request, 上限: int = 1024, 错误码: str = "invalid_credential_request") -> dict:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 上限:
                raise OAuthError(错误码, 400)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise OAuthError(错误码, 400) from None
        return data if isinstance(data, dict) else {}

    @app.delete("/api/workspace/记录")
    async def personal_delete_record(request: Request):
        """删掉一条已收录记录，或一个收藏夹下的全部记录（「知识库图谱」右键删除用）。

        只删当前账号自己的行，且是**真删**——重新收录摘要或重跑直答处理会再次出现
        （源在知乎）。参数只认 `{路径}` 或 `{收藏夹}` 之一：两个都不给或都给一律拒绝，
        否则空选择器会把整个工作区删空。
        """
        require_same_origin(request)
        session = request_session(request)
        if session is None:
            raise OAuthError("login_required", 401)
        require_csrf(request, session)
        data = await 读取受限body(request, 512, "invalid_delete_request")
        字段 = next(iter(data), "")
        值 = data.get(字段, "")
        if len(data) != 1 or 字段 not in {"路径", "收藏夹"}:
            raise OAuthError("invalid_delete_request", 400)
        if not isinstance(值, str) or not 值.strip() or len(值) > 200:
            raise OAuthError("invalid_delete_request", 400)
        result = workspace.delete(session.user.subject,
                                  **({"path": 值.strip()} if 字段 == "路径" else {"collection_id": 值.strip()}))
        if not result["deleted"]:
            raise OAuthError("record_not_found", 404)
        return result

    def 取直答客户端(session=None):
        """按会话取直答客户端：用户自备凭证优先，回落到应用默认凭证。"""
        if 直答工厂 is None:
            raise OAuthError("zhida_unavailable", 503)
        自备 = 凭证库.取(session.user.subject) if session else ""
        client = 直答工厂(access_secret=自备) if 自备 else 直答工厂()
        if not getattr(client, "可用", False):
            raise OAuthError("zhida_unavailable", 503)
        return client
        return client

    @app.get("/api/直答额度")
    async def 直答额度(request: Request):
        # 额度挂在 Access Secret 上：用户自备凭证就显示他自己的，否则是应用凭证（所有访问者共用）。
        # 这里只回数字与来源，不回凭证本身。查询走官方额度接口，不消耗业务额度。
        session = request_session(request)
        client = 取直答客户端(session)
        剩余 = 查今日剩余额度(client)
        自备 = 凭证库.已设置(session.user.subject)
        return {"可用": True, "剩余": 剩余,
                "消耗方": "你自己的 Access Secret" if 自备 else "应用凭证（开发者账号）",
                "凭证": "自用凭证" if 自备 else "应用默认",
                "模型": getattr(getattr(client, "配置", None), "model", ""),
                "AccessSecret地址": ACCESS_SECRET_URL,
                "设置地址": "/settings"}

    @app.post("/api/直答凭证")
    async def 保存直答凭证(request: Request):
        """保存用户自备的 Access Secret。只进进程内存，先验证再存。"""
        require_same_origin(request)
        session = request_session(request)
        require_csrf(request, session)
        data = await 读取受限body(request)
        if set(data) != {"access_secret"}:
            raise OAuthError("invalid_credential_request", 400)
        凭证 = data["access_secret"]
        if not isinstance(凭证, str) or not 直答凭证库.合规(凭证.strip()):
            raise OAuthError("invalid_credential_request", 400)
        client = 直答工厂(access_secret=凭证.strip()) if 直答工厂 else None
        if client is None or not getattr(client, "可用", False):
            raise OAuthError("zhida_unavailable", 503)
        判定 = getattr(client, "凭证有效", None)
        有效 = 判定() if callable(判定) else None
        if 有效 is False:
            raise OAuthError("invalid_access_secret", 400)
        if 有效 is None:
            # 无法判定（网络/平台异常）时不保存，也不误报"无效"。
            raise OAuthError("credential_check_unavailable", 503)
        凭证库.存(session.user.subject, 凭证.strip())
        return {"已设置": True, "凭证": "自用凭证", "剩余": 查今日剩余额度(client),
                "消耗方": "你自己的 Access Secret"}

    @app.delete("/api/直答凭证")
    async def 清除直答凭证(request: Request):
        """删掉自备凭证，回到应用默认额度。"""
        require_same_origin(request)
        session = request_session(request)
        require_csrf(request, session)
        凭证库.清(session.user.subject)
        return {"已设置": False, "凭证": "应用默认"}

    @app.post("/api/直答处理")
    async def 启动直答处理(request: Request):
        # 只有已登录、同源、带当前 CSRF 的请求能启动；任务绑在会话的稳定用户 ID 上，
        # 别人既不能替你启动，也不能靠猜 id 查你的进度。
        require_same_origin(request)
        session = request_session(request)
        require_csrf(request, session)
        client = 取直答客户端(session)
        task = 直答任务登记处.开始(session.user.subject)
        asyncio.create_task(运行任务(task, provider, workspace, client, session.access_token))
        return {"task": task.public()}

    @app.get("/api/直答处理/状态")
    async def 直答处理进度(request: Request):
        session = request_session(request)
        # 不传 task 时返回进行中的那个，页面刷新后可以接着显示进度。
        task = 直答任务登记处.取或当前(session.user.subject, request.query_params.get("task", ""))
        return {"task": task.public()}

    @app.post("/auth/logout")
    async def logout(request: Request):
        require_same_origin(request)
        try:
            session = request_session(request)
        except OAuthError as exc:
            if exc.code not in AUTH_FAILURES:
                raise
        else:
            require_csrf(request, session)
        store.cancel(request.cookies.get(settings.flow_cookie))
        response = JSONResponse({"logged_out": True, "remote_authorization_revoked": False})
        clear_session(response, request)
        clear_cookie(response, settings.flow_cookie)
        return response

    return app
