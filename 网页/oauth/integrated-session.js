"use strict";
(() => {
  const chip = document.getElementById("zh-account");
  if (!chip) return;
  const name = document.getElementById("zh-account-name");
  const logoutButton = document.getElementById("zh-account-out");
  const authFailures = new Set(["login_required", "session_expired", "token_expired", "authorization_failed"]);
  let session = null;
  let revision = 0;
  let timer = null;
  let leaving = false;
  let loggingOut = false;

  function leave(result = "") {
    if (leaving) return;
    leaving = true;
    revision += 1;
    clearTimeout(timer);
    chip.hidden = true;
    location.replace(window.LoginRedirect.loginUrl(window.LoginRedirect.currentTarget("/"), "/", result));
  }

  function unverified(message) {
    name.textContent = message;
    logoutButton.disabled = true;
    chip.hidden = false;
  }

  async function verify() {
    if (leaving || loggingOut) return;
    const ticket = ++revision;
    try {
      const response = await fetch("/api/me", {credentials: "same-origin", cache: "no-store"});
      const data = await response.json();
      if (ticket !== revision || leaving) return;
      if (response.status === 401 || authFailures.has(data.error?.code)) {
        leave(authFailures.has(data.error?.code) ? data.error.code : "session_expired"); return;
      }
      if (!response.ok || !data.authenticated || !data.user?.id || !data.csrf_token || !Number.isFinite(Date.parse(data.expires_at))) {
        unverified("暂时无法核验账号，请刷新页面"); return;
      }
      if (Date.parse(data.expires_at) <= Date.now()) { leave("token_expired"); return; }
      session = data;
      clearTimeout(timer);
      timer = setTimeout(() => leave("token_expired"), Math.min(Date.parse(data.expires_at) - Date.now(), 2147483647));
      name.textContent = "知乎 · " + (data.user.name || "已授权");
      logoutButton.disabled = false;
      chip.hidden = false;
    } catch {
      if (ticket === revision && !leaving) unverified("暂时无法核验账号，请刷新页面");
    }
  }

  logoutButton.addEventListener("click", async () => {
    if (!session || leaving || loggingOut) return;
    loggingOut = true;
    revision += 1;
    logoutButton.disabled = true;
    try {
      const response = await fetch("/auth/logout", {method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf_token}, body: "{}"});
      if (response.ok) leave();
      else if (response.status === 401) leave("session_expired");
      else { name.textContent = "退出未完成，请重试"; logoutButton.disabled = false; }
    } catch { name.textContent = "退出未完成，请重试"; logoutButton.disabled = false; }
    finally { loggingOut = false; }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { revision += 1; chip.hidden = true; }
    else verify();
  });
  window.addEventListener("pagehide", () => { revision += 1; chip.hidden = true; });
  window.addEventListener("pageshow", event => { if (event.persisted) verify(); });
  verify();
})();
