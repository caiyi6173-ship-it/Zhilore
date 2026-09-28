"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const 输入 = $("access-secret");
  const 保存 = $("save-credential");
  const 删除 = $("clear-credential");
  const 提示 = $("notice");
  const 徽标 = $("credential-badge");
  const 归属 = $("quota-owner");
  const 额度行 = $("quota-line");
  const 备注 = $("credential-note");
  let 令牌 = "";

  function 显示(文本, 出错 = false) {
    提示.textContent = 文本;
    提示.hidden = false;
    提示.dataset.kind = 出错 ? "error" : "ok";
  }

  async function 请求(url, 选项 = {}) {
    const 头 = Object.assign({"X-CSRF-Token": 令牌, "Content-Type": "application/json"}, 选项.headers || {});
    const r = await fetch(url, Object.assign({}, 选项, {headers: 头, credentials: "same-origin", cache: "no-store"}));
    const 数据 = await r.json().catch(() => ({}));
    if (r.status === 401) {
      location.assign("/login?returnTo=" + encodeURIComponent("/settings"));
      throw new Error("login_required");
    }
    if (!r.ok) {
      const 错 = new Error(数据.error?.message || 数据.error?.code || "请求失败");
      错.code = 数据.error?.code || "";
      throw 错;
    }
    return 数据;
  }

  function 渲染额度(数据) {
    归属.textContent = 数据.消耗方 || "未知";
    额度行.textContent = typeof 数据.剩余 === "number" ? `今日剩余 ${数据.剩余} 次` : "暂时读不到剩余次数";
    徽标.textContent = 数据.凭证 === "自用凭证" ? "自用凭证" : "应用默认";
    徽标.className = "badge" + (数据.凭证 === "自用凭证" ? " connected" : "");
    删除.hidden = 数据.凭证 !== "自用凭证";
    备注.textContent = 数据.凭证 === "自用凭证"
      ? "已保存你的凭证：只驻留服务器内存，不回显、不落盘；服务重启后需要重新填写。"
      : "当前使用应用凭证，所有访问者共用同一个额度池子。";
    备注.hidden = false;
  }

  async function 刷新() {
    try {
      渲染额度(await 请求("/api/直答额度"));
    } catch (错误) {
      显示(错误.message, true);
    }
  }

  保存.addEventListener("click", async () => {
    const 值 = (输入.value || "").trim();
    if (!值) {
      显示("请先粘贴 Access Secret。", true);
      return;
    }
    保存.disabled = true;
    保存.textContent = "验证中…";
    try {
      // 凭证只在请求体里出现一次，成功后立刻从页面清空，不留痕迹。
      const 数据 = await 请求("/api/直答凭证", {method: "POST", body: JSON.stringify({access_secret: 值})});
      输入.value = "";
      渲染额度({...数据, 剩余: 数据.剩余, 凭证: "自用凭证", 消耗方: "你自己的 Access Secret"});
      显示("已验证并保存：后续处理将消耗你自己的直答额度。");
    } catch (错误) {
      显示(错误.message, true);
    } finally {
      保存.disabled = false;
      保存.textContent = "验证并保存";
      输入.value = "";
    }
  });

  删除.addEventListener("click", async () => {
    删除.disabled = true;
    try {
      const 数据 = await 请求("/api/直答凭证", {method: "DELETE"});
      渲染额度({...数据, 剩余: null, 凭证: "应用默认", 消耗方: "应用凭证（开发者账号）"});
      await 刷新();
      显示("已删除你的凭证，后续处理回到应用默认额度。");
    } catch (错误) {
      显示(错误.message, true);
    } finally {
      删除.disabled = false;
    }
  });

  (async () => {
    try {
      const 我 = await fetch("/api/me", {credentials: "same-origin", cache: "no-store"}).then(r => r.json());
      if (!我.authenticated) {
        location.assign("/login?returnTo=" + encodeURIComponent("/settings"));
        return;
      }
      令牌 = 我.csrf_token || "";
      await 刷新();
    } catch (错误) {
      显示("无法读取当前账号，请重新登录。", true);
    }
  })();
})();
