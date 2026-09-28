"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const authFailures = new Set(["login_required", "session_expired", "token_expired", "authorization_failed", "workspace_session_changed"]);
  const endpoints = new Set(["索引", "图谱", "笔记", "搜索", "收藏夹", "创作"]);
  // 工作区里唯一允许的非只读操作：启动直答处理、查进度、查额度。
  // 它们的路由挂在 /api 下而不是 /api/workspace 下，所以要单独记路径与方法。
  const 特殊端点 = new Map([
    ["直答处理", {path: "/api/直答处理", method: "POST"}],
    ["直答处理/状态", {path: "/api/直答处理/状态", method: "GET"}],
    ["直答额度", {path: "/api/直答额度", method: "GET"}],
    // 「知识库图谱」右键删除：真删当前账号自己的记录（一条，或一个收藏夹的全部）。
    ["记录", {path: "/api/workspace/记录", method: "DELETE"}],
    // 「我的创作」：同步一页创作（知乎那一步走服务端，浏览器不直连知乎）。
    ["import-creations", {path: "/api/workspace/import-creations", method: "POST"}],
  ]);
  const preferences = new Map();
  let session = null;
  let revision = 0;
  let pending = new AbortController();
  let expiryTimer = null;
  let suspended = false;
  let closed = false;
  let verifying = false;
  let cleanup = () => {};

  function abortError() { return new DOMException("Workspace request cancelled", "AbortError"); }

  function lock(title, copy) {
    document.body.classList.add("workspace-locked");
    $("workspace-gate").querySelector("h1").textContent = title;
    $("workspace-gate").querySelector("p").textContent = copy;
    $("workspace-account").textContent = "账号信息已隐藏";
    $("workspace-logout").disabled = true;
    revision += 1;
    pending.abort(); pending = new AbortController();
    cleanup();
    preferences.clear();
  }

  function leave(code = "session_expired") {
    if (closed) return;
    closed = true;
    clearTimeout(expiryTimer);
    lock("需要重新核验账号", "已清空此页面的摘要和缓存，正在返回账号页。");
    session = null;
    const result = authFailures.has(code) ? code : "";
    location.replace(window.LoginRedirect.loginUrl(window.LoginRedirect.currentTarget(), "/workspace/", result));
  }

  async function identity() {
    const response = await fetch("/api/me", {credentials: "same-origin", cache: "no-store", signal: pending.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.code || "session_expired");
    if (!data.authenticated || !data.user?.id || typeof data.user.name !== "string" ||
        !data.csrf_token || !Number.isFinite(Date.parse(data.expires_at))) throw new Error("session_expired");
    return data;
  }

  async function initialize() {
    const ticket = revision;
    try {
      const data = await identity();
      if (ticket !== revision || closed) return false;
      if (Date.parse(data.expires_at) <= Date.now()) { leave("token_expired"); return false; }
      session = data;
      const stateNote = data.state_mode === "cookie_bound" ? " · state 未回传（Cookie 绑定）· 仅适合临时联调" : "";
      $("workspace-account").textContent = data.user.name + " · 知乎 ID " + data.user.id + stateNote;
      $("workspace-logout").disabled = false;
      $("workspace-logout").addEventListener("click", logout);
      expiryTimer = setTimeout(() => leave("token_expired"), Math.min(Date.parse(data.expires_at) - Date.now(), 2147483647));
      document.body.classList.remove("workspace-locked");
      return true;
    } catch (error) {
      if (ticket !== revision || error.name === "AbortError") return false;
      if (authFailures.has(error.message)) leave(error.message);
      else lock("暂时无法核验账号", "没有读取任何知识库数据。请返回账号页重试。");
      return false;
    }
  }

  async function request(url, options = {}) {
    if (!session || suspended || closed) throw abortError();
    const [path] = url.split("?");
    const name = path.startsWith("/api/") ? path.slice(5) : "";
    const method = options.method || "GET";
    const 特殊 = 特殊端点.get(name);
    const allowed = 特殊 ? method === 特殊.method : (method === "GET" && endpoints.has(name));
    if (!allowed) {
      throw new Error("Unsupported workspace operation");
    }
    const query = url.slice(path.length);
    const ticket = revision;
    const signal = options.signal ? AbortSignal.any([pending.signal, options.signal]) : pending.signal;
    const headers = {"X-Workspace-Session": session.csrf_token};
    if (method !== "GET") {
      // 写操作（POST / DELETE）都要带 CSRF 与 JSON Content-Type：后者是服务端同源校验的硬要求，
      // DELETE 少了它会得到 403 而不是 401。
      headers["X-CSRF-Token"] = session.csrf_token;
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(特殊 ? 特殊.path + query : "/api/workspace/" + name + query, {
      ...options, method, credentials: "same-origin", cache: "no-store", signal, headers,
    });
    const data = await response.json();
    if (ticket !== revision || signal.aborted || closed) throw abortError();
    if (response.status === 401 || (!response.ok && authFailures.has(data.error?.code))) {
      leave(authFailures.has(data.error?.code) ? data.error.code : "session_expired"); throw abortError();
    }
    // Recheck even if another request or expiry invalidates the page before the caller reads JSON.
    return {ok: response.ok, status: response.status, json: async () => {
      if (ticket !== revision || closed) throw abortError();
      return data;
    }};
  }

  function suspend() {
    if (closed) return;
    suspended = true;
    lock("页面已锁定", "返回页面后会重新核验账号，不沿用另一个账号的数据。");
  }

  async function recheck() {
    if (closed || verifying) return;
    verifying = true;
    const ticket = revision;
    try {
      const data = await identity();
      if (ticket !== revision || closed) return;
      if (!session || data.user.id !== session.user.id || data.csrf_token !== session.csrf_token) {
        leave("workspace_session_changed"); return;
      }
      if (Date.parse(data.expires_at) <= Date.now()) { leave("token_expired"); return; }
      if (suspended) location.reload();
    } catch (error) {
      if (ticket !== revision || error.name === "AbortError") return;
      if (authFailures.has(error.message)) leave(error.message);
      else lock("暂时无法核验账号", "为避免显示旧账号数据，工作区保持锁定。请返回账号页重试。");
    } finally { verifying = false; }
  }

  async function logout() {
    if (!session || closed) return;
    const csrf = session.csrf_token;
    suspend();
    try {
      const response = await fetch("/auth/logout", {method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: "{}"});
      if (!response.ok) throw new Error("Logout failed");
      leave("");
    } catch {
      lock("退出尚未完成", "页面数据已清空，但尚未确认服务端退出成功。请返回账号页检查连接状态。");
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") suspend();
    else recheck();
  });
  window.addEventListener("pagehide", suspend);
  window.addEventListener("pageshow", event => { if (event.persisted) { suspend(); recheck(); } });
  window.WorkspaceAccess = Object.freeze({
    initialize, request, setCleanup(fn) { cleanup = fn; },
    preferences: {getItem: key => preferences.get(key) ?? null, setItem: (key, value) => preferences.set(key, String(value))},
  });
})();
