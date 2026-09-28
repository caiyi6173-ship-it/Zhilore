"use strict";
/* 「知乎收藏」页下半部分：收录内容的卡片列表。
 *
 * 只读展示收录时的快照（标题 / 作者 / 摘要 / 互动数字），不做任何采集：
 * 数据来自 /api/workspace/收藏夹（本机 sqlite，不联网、不耗额度）。
 * 互动数字是只读的——开放平台只有 GET，点赞/评论只能在知乎做，所以一律
 * 用「阅读原文」跳回原文页，绝不把它们做成看着能点的按钮。
 */
(() => {
  const $ = id => document.getElementById(id);
  const root = $("collections");
  // 这个页面不加载知识库那套前端（app.js / graph.js），所以会话核验要在这里发起。
  if (!root || !window.WorkspaceAccess) return;

  const 每页 = 20;
  const {initialize, request, preferences} = window.WorkspaceAccess;
  /* 两个来源共用同一套卡片渲染，只是分组维度不同：
     收藏按「收藏夹」分组，创作按「内容类型」分组（回答 / 文章 / 视频 / 想法 / 提问）。 */
  const 来源表 = {
    收藏: {
      读: "/api/收藏夹", 侧栏: "收藏夹", 标签: "PUBLIC COLLECTIONS / 公开收藏",
      空标题: "还没有收录到内容",
      空说明: "在上面的横幅点「用直答处理我的收藏夹」，或到账号页收录最近一页摘要。收录后这里会出现标题与摘要卡片，看原文直接跳回知乎。",
      空动作: {文本: "去账号页收录摘要 →", 地址: "/account"},
      统计: 统计 => `${统计.收藏夹数 || 0} 个收藏夹 · 已收录 ${统计.内容数 || 0} 条摘要 · ${统计.知识块数 || 0} 个知识块`,
    },
    创作: {
      读: "/api/创作", 侧栏: "内容类型", 标签: "MY CREATIONS / 我的创作",
      空标题: "还没有同步到创作",
      空说明: "同步会读取你在知乎公开范围内的创作（回答 / 文章 / 视频 / 想法 / 提问）的标题与摘要，不是全文；私密内容不在开放范围。",
      空动作: {文本: "同步我的创作", 动作: "同步"},
      统计: 统计 => `${统计.收藏夹数 || 0} 种内容类型 · 已同步 ${统计.内容数 || 0} 条创作`,
    },
  };
  let 来源 = "收藏";
  let 数据 = null;
  let 当前 = null;
  let 显示条数 = 每页;

  /* ── 主题：知识库的初始化在 app.js 里，本页不加载它，所以自己来 ── */
  function 应用主题(值) {
    const 主题 = 值 === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = 主题;
    preferences.setItem("主题", 主题);
  }
  应用主题(preferences.getItem("主题") || "light");
  $("collections-theme")?.addEventListener("click", () => {
    应用主题(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  });

  function 建(tag, 类名, 文本) {
    const 元素 = document.createElement(tag);
    if (类名) 元素.className = 类名;
    if (文本 !== undefined && 文本 !== null) 元素.textContent = 文本;
    return 元素;
  }

  function 时间文本(秒) {
    if (!秒) return "";
    const 日 = new Date(秒 * 1000);
    const 补零 = n => String(n).padStart(2, "0");
    return `${日.getFullYear()}-${补零(日.getMonth() + 1)}-${补零(日.getDate())}`;
  }

  function 提示(文本, 出错 = false) {
    const 行 = $("collections-feedback");
    行.textContent = 文本;
    行.hidden = !文本;
    行.dataset.kind = 出错 ? "error" : "ok";
  }

  async function call(url, 选项) {
    // 注意：按 workspace.js 的约定传 `/api/<名>`，它负责改写成 `/api/workspace/<名>`。
    const response = await request(url, 选项);
    const data = await response.json();
    if (!response.ok) {
      const 错 = new Error(data.error?.message || data.error?.code || "请求失败");
      错.code = data.error?.code || "http_" + response.status;
      throw 错;
    }
    return data;
  }

  function 画收藏夹列表() {
    const 列表 = $("collections-list");
    列表.replaceChildren();
    数据.收藏夹.forEach(夹 => {
      const 当前项 = 当前 && 夹.id === 当前.id;
      const 按钮 = 建("button", "collections-item" + (当前项 ? " active" : ""));
      按钮.type = "button";
      按钮.setAttribute("aria-current", 当前项 ? "true" : "false");
      按钮.append(建("span", "collections-item-name", 夹.名称),
                  建("span", "collections-item-count", String(夹.内容数 + 夹.知识块数)));
      按钮.addEventListener("click", () => {
        当前 = 夹;
        显示条数 = 每页;
        画收藏夹列表();
        画内容();
      });
      列表.append(按钮);
    });
  }

  function 画卡片(条目) {
    const 卡 = 建("article", "card");
    卡.append(建("h2", "card-title", 条目.标题));

    const 副信息 = [];
    if (条目.作者) 副信息.push(条目.作者);
    if (条目.收录时间) 副信息.push("收录于 " + 时间文本(条目.收录时间));
    if (条目.模式 === "知识块") 副信息.push(条目.来源数 ? `由 ${条目.来源数} 条摘要归并` : "直答归并");
    副信息.push(条目.模式);
    卡.append(建("p", "card-byline", 副信息.join(" · ")));

    if (条目.摘要) 卡.append(建("p", "card-excerpt", 条目.摘要));
    if (条目.标签 && 条目.标签.length) {
      const 标签行 = 建("div", "card-tags");
      条目.标签.forEach(标签 => 标签行.append(建("span", "card-tag", 标签)));
      卡.append(标签行);
    }

    const 底部 = 建("footer", "card-foot");
    if (条目.原文链接) {
      const 链接 = 建("a", "card-origin", "阅读原文");
      链接.href = 条目.原文链接;
      链接.target = "_blank";
      链接.rel = "noopener noreferrer";
      链接.title = "到知乎看原文与最新状态";
      底部.append(链接);
    } else {
      底部.append(建("span", "card-origin missing", "原文链接缺失"));
    }
    const 数字 = [];
    if (条目.赞同数) 数字.push("赞同 " + 条目.赞同数);
    if (条目.评论数) 数字.push("评论 " + 条目.评论数);
    if (条目.收藏数) 数字.push("收藏 " + 条目.收藏数);
    if (数字.length) {
      const 统计 = 建("span", "card-stats", 数字.join(" · ") + " · 收录时");
      统计.title = "知乎开放接口没有写操作，赞同/评论只能到知乎去点";
      底部.append(统计);
    }
    卡.append(底部);
    return 卡;
  }

  function 画内容() {
    if (!数据) return;
    const 配置 = 来源表[来源];
    const 是创作 = 来源 === "创作";
    const 夹 = 当前;
    $("collections-title").textContent = 夹 ? 夹.名称 : (是创作 ? "选一种内容类型" : "选择一个收藏夹");
    const 元信息 = [];
    if (夹) {
      元信息.push((是创作 ? "类型 " : "收藏夹 ") + 夹.id);
      元信息.push(`已${是创作 ? "同步" : "收录"} ${夹.内容数} 条${是创作 ? "创作" : "摘要"}`);
      if (夹.知识块数) 元信息.push(`归并出 ${夹.知识块数} 个知识块`);
    }
    $("collections-meta").textContent = 元信息.join(" · ");
    $("collections-label").textContent = 配置.标签;
    $("collections-side-label").textContent = 配置.侧栏;

    const 全部 = 夹 ? 夹.内容 : [];
    const 显示 = 全部.slice(0, 显示条数);
    $("collections-cards").replaceChildren(...显示.map(画卡片));
    const 更多 = $("collections-more");
    更多.hidden = 全部.length <= 显示.length;
    更多.textContent = `加载更多（还有 ${Math.max(0, 全部.length - 显示.length)} 条）`;
    // 创作按 Offset 分页，一页 20 条，所以这里允许继续同步下一页。
    const 同步 = $("collections-sync");
    同步.hidden = !是创作;
    同步.textContent = 数据.统计?.内容数 ? "再同步一页" : "同步我的创作";
    $("collections-note").textContent = 数据.说明 || "";
    $("collections-note").hidden = false;
    $("collections-side-state").hidden = true;

    $("status-left").textContent = 配置.统计(数据.统计 || {});
    $("status-right").textContent = "账号隔离 · 非全文";
  }

  function 画空态() {
    const 配置 = 来源表[来源];
    const 是创作 = 来源 === "创作";
    $("collections-empty").hidden = false;
    $("collections-cards").replaceChildren();
    $("collections-note").hidden = true;
    $("collections-more").hidden = true;
    $("collections-sync").hidden = !是创作;
    $("collections-sync").textContent = "同步我的创作";
    $("collections-label").textContent = 配置.标签;
    $("collections-side-label").textContent = 配置.侧栏;
    $("collections-title").textContent = 配置.空标题;
    $("collections-meta").textContent = "";
    $("collections-empty-title").textContent = 配置.空标题;
    $("collections-empty-copy").textContent = 配置.空说明;
    const 行动 = $("collections-empty-cta");
    行动.textContent = 配置.空动作.文本;
    if (配置.空动作.地址) {
      行动.href = 配置.空动作.地址;
      行动.removeAttribute("onclick");
    } else {
      行动.removeAttribute("href");
      行动.addEventListener("click", 同步创作);
    }
    $("collections-side-state").hidden = true;
    $("status-left").textContent = 是创作 ? "0 种内容类型 · 已同步 0 条创作" : "0 个收藏夹 · 已收录 0 条摘要";
  }

  $("collections-more").addEventListener("click", () => {
    显示条数 += 每页;
    画内容();
  });

  /* ── 来源切换：我的收藏 ↔ 我的创作 ── */
  $("collections-source").addEventListener("click", e => {
    const 按钮 = e.target.closest?.("[data-来源]");
    if (!按钮 || 按钮.dataset.来源 === 来源) return;
    来源 = 按钮.dataset.来源;
    for (const 项 of $("collections-source").children) {
      项.classList.toggle("active", 项.dataset.来源 === 来源);
    }
    载入当前来源();
  });

  /* ── 同步创作：知乎那一步在服务端完成，浏览器不直连知乎 ── */
  let 正在同步 = false;
  async function 同步创作() {
    if (正在同步) return;
    正在同步 = true;
    const 按钮 = $("collections-sync");
    按钮.disabled = true;
    按钮.textContent = "正在同步…";
    try {
      // 创作接口按 Offset 分页，所以下一页从"已同步条数"接着取。
      const 偏移 = String(数据?.统计?.内容数 || 0);
      const 结果 = await call("/api/import-creations",
                              {method: "POST", body: JSON.stringify({offset: 偏移})});
      const 导入 = 结果.imported || {};
      提示(`已同步 ${导入.added || 0} 条，更新 ${导入.updated || 0} 条`, false);
      await 载入当前来源();
    } catch (错误) {
      if (错误.name !== "AbortError") 提示("同步失败：" + 错误.message, true);
    } finally {
      正在同步 = false;
      按钮.disabled = false;
      按钮.textContent = 数据?.统计?.内容数 ? "再同步一页" : "同步我的创作";
    }
  }
  $("collections-sync").addEventListener("click", 同步创作);

  async function 载入当前来源() {
    数据 = null;
    当前 = null;
    显示条数 = 每页;
    $("collections-empty").hidden = true;
    try {
      数据 = await call(来源表[来源].读);
      当前 = 数据.收藏夹[0] || null;
      $("collections-count").textContent = String(数据.统计?.收藏夹数 ?? 数据.收藏夹.length);
      画收藏夹列表();
      if (!当前) { 画空态(); return; }
      画内容();
    } catch (错误) {
      if (错误.name === "AbortError") return;
      $("collections-side-state").hidden = true;
      提示(`暂时读不到${来源 === "创作" ? "创作" : "已收录内容"}：` + 错误.message, true);
    }
  }

  (async () => {
    let 就绪 = false;
    try {
      就绪 = await initialize();
    } catch {
      就绪 = false;
    }
    if (!就绪) return;  // 门禁已接管页面（锁定或跳登录），不读任何数据。
    await 载入当前来源();
  })();
})();
