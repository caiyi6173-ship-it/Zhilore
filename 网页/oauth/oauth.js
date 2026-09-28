"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const authFailures = new Set(["login_required", "session_expired", "token_expired", "authorization_failed"]);
  let messages = {};
  let configured = false;
  let collectionsConfigured = false;
  let current = null;
  let epoch = 0;
  let controller = new AbortController();
  let expiryTimer = null;
  let busy = false;
  let reader = null;
  const collectionButtons = new Map();
  const publicScopeNote = "仅读取当前授权用户的公开收藏夹，私密收藏夹不在开放范围——这是开放接口的契约，完成授权也不会改变。要收录私密夹，请先在知乎把它改为公开（见「在知乎管理收藏夹可见性」），再点「重新读取」。列表最多返回 50 个，接口未提供分页，不保证已列出全部。";
  const retryableReads = new Set(["rate_limited", "quota_exceeded", "upstream_unavailable", "server_busy"]);
  const contentTypes = new Map([
    ["answer", ["回答", "www.zhihu.com", /^\/(?:question\/[1-9][0-9]{0,19}\/)?answer\/([1-9][0-9]{0,19})$/]],
    ["article", ["文章", "zhuanlan.zhihu.com", /^\/p\/([1-9][0-9]{0,19})$/]],
    ["question", ["问题", "www.zhihu.com", /^\/question\/([1-9][0-9]{0,19})$/]],
    ["pin", ["想法", "www.zhihu.com", /^\/pin\/([1-9][0-9]{0,19})$/]],
    ["zvideo", ["视频", "www.zhihu.com", /^\/zvideo\/([1-9][0-9]{0,19})$/]],
  ]);

  const {safeReturnPath, loginUrl} = window.LoginRedirect;
  const loginQuery = new URLSearchParams(location.search);
  const callbackResult = loginQuery.get("result");
  const defaultReturnTo = safeReturnPath(document.body.dataset.defaultReturnTo) || "/workspace/";
  const loginPage = ["/login", "/login/"].includes(location.pathname) || loginQuery.has("returnTo");
  const requestedTargets = loginQuery.getAll("returnTo");
  let returnTo = requestedTargets.length === 1 ? safeReturnPath(requestedTargets[0]) : null;
  if (loginPage) {
    returnTo ||= defaultReturnTo;
    // HTTP redirects inherit a fragment the server cannot see. Move it into
    // returnTo before clearing the login URL, unless the target already has one.
    if (location.hash && !returnTo.includes("#")) returnTo = safeReturnPath(returnTo + location.hash) || returnTo;
    history.replaceState(null, "", loginUrl(returnTo, defaultReturnTo));
  } else if (location.search) {
    // The account page can itself be a returnTo destination. Remove only OAuth
    // metadata; keep its ordinary query bytes and section fragment intact.
    const callbackFields = new Set(["result", "authorization_code", "code", "state", "error",
      "error_description", "error_uri", "access_token", "token_type", "expires_in"]);
    const query = location.search.slice(1).split("&")
      .filter(part => !callbackFields.has(new URLSearchParams(part).keys().next().value)).join("&");
    const cleanSearch = query ? "?" + query : "";
    if (cleanSearch !== location.search) history.replaceState(null, "", location.pathname + cleanSearch + location.hash);
  }

  function invalidateRequests() {
    epoch += 1;
    clearReader();
    controller.abort();
    controller = new AbortController();
    clearTimeout(expiryTimer);
    expiryTimer = null;
    return epoch;
  }

  function notice(message, tone = "warning") {
    $("notice").textContent = message || "";
    $("notice").dataset.tone = tone;
    $("notice").hidden = !message;
  }

  function publicMessage(code) {
    return messages[code] || "连接未完成，请刷新状态后重试。";
  }

  function updateButtons() {
    $("login").disabled = busy || !configured;
    $("login").hidden = Boolean(current);
    $("logout").hidden = !current;
    $("enter-workspace").hidden = !current;
    // Reading collections must never prevent an immediate logout.
    $("logout").disabled = !current;
    $("refresh-session").disabled = busy;
    $("refresh-collections").disabled = busy || !current || !collectionsConfigured;
  }

  function collectionState(title, copy) {
    collectionButtons.clear();
    $("collection-list").replaceChildren();
    $("collection-list").hidden = true;
    $("collection-state").hidden = false;
    $("collection-state-title").textContent = title;
    $("collection-state-copy").textContent = copy;
    $("collection-count").textContent = "—";
  }

  function resetAccount() {
    current = null;
    clearReader();
    $("scope-note").textContent = publicScopeNote;
    $("account-name").textContent = "尚未连接";
    $("account-id").textContent = "登录后显示稳定用户 ID，不用昵称区分账号。";
    $("expires").textContent = "";
    $("expires").hidden = true;
    $("connection-badge").textContent = configured ? "待授权" : "待配置";
    $("connection-badge").className = "badge warning";
    collectionState("先连接，再看收藏", "这里不会预填演示数据，也不会展示开发者账号的收藏夹。");
    $("collections").setAttribute("aria-busy", "false");
    updateButtons();
  }

  async function request(url, options = {}) {
    let response;
    try {
      response = await fetch(url, { credentials: "same-origin", cache: "no-store", signal: controller.signal, ...options });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new Error("upstream_unavailable");
    }
    let data;
    try { data = await response.json(); } catch { throw new Error("upstream_protocol_error"); }
    if (!response.ok) throw new Error(data.error?.code || "upstream_unavailable");
    return data;
  }

  function applyConfiguration(config) {
    // 服务端配置面板已从入口页移除（只在 /api/status 里可查），这里只保留两份状态：
    // configured 决定「用知乎账号授权」是否可点，collectionsConfigured 决定收藏接口相关按钮。
    configured = config.ready === true;
    collectionsConfigured = config.collections?.ready === true;
  }

  function renderAccount(data) {
    if (!data.authenticated || !data.user?.id || !data.csrf_token || !Number.isFinite(Date.parse(data.expires_at))) {
      throw new Error("upstream_protocol_error");
    }
    current = data;
    $("account-name").textContent = data.user.name;
    $("account-id").textContent = "知乎 ID · " + data.user.id;
    $("expires").textContent = "会话有效至 " + new Date(data.expires_at).toLocaleString("zh-CN", {hour12: false});
    $("expires").hidden = false;
    $("connection-badge").textContent = "已连接";
    $("connection-badge").className = "badge connected";
    clearTimeout(expiryTimer);
    expiryTimer = setTimeout(() => refreshSession(), Math.max(0, Math.min(Date.parse(data.expires_at) - Date.now() + 500, 2147483647)));
    updateButtons();
  }

  function decimal(value) {
    return typeof value === "string" && /^[0-9]{1,20}$/.test(value) && BigInt(value) <= 9223372036854775807n;
  }

  function publicCollectionItems(data) {
    if (!current || data.user?.id !== current.user.id) throw new Error("session_expired");
    if (!Array.isArray(data.items) || data.items.length > 50) throw new Error("upstream_protocol_error");
    const items = [], seen = new Set();
    for (const item of data.items) {
      if (!item || typeof item.is_public !== "boolean") throw new Error("upstream_protocol_error");
      if (!item.is_public) continue;
      if (!decimal(item.id) || item.id.startsWith("0") || seen.has(item.id) ||
          typeof item.title !== "string" || typeof item.description !== "string") throw new Error("upstream_protocol_error");
      seen.add(item.id); items.push(item);
    }
    return items;
  }

  function renderCollections(data) {
    const items = publicCollectionItems(data);
    $("scope-note").textContent = publicScopeNote + (items.length === 50 ? " 本次已达到 50 个返回上限，可能还有未列出的公开收藏夹。" : "");
    if (!items.length) {
      collectionState("接口未返回公开收藏夹", "这不等于你的账号没有收藏夹。请检查是否有公开收藏夹，以及应用是否获准读取公开内容。");
      $("collection-count").textContent = "0";
      return;
    }
    const fragment = document.createDocumentFragment();
    collectionButtons.clear();
    items.forEach((item, index) => {
      const row = document.createElement("li"); row.className = "collection-item";
      const number = document.createElement("span"); number.className = "collection-number"; number.textContent = String(index + 1).padStart(2, "0");
      const content = document.createElement("div");
      const title = document.createElement("h3");
      const link = document.createElement("a");
      link.href = "https://www.zhihu.com/collection/" + item.id;
      link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = item.title || "未命名收藏夹"; title.append(link);
      const description = document.createElement("p"); description.className = "collection-description"; description.textContent = item.description || "暂无描述";
      const meta = document.createElement("p"); meta.className = "collection-meta"; meta.textContent = "COLLECTION / " + item.id;
      content.append(title, description, meta);
      const actions = document.createElement("div"); actions.className = "collection-actions";
      const visibility = document.createElement("span"); visibility.className = "visibility"; visibility.textContent = "公开";
      const read = document.createElement("button"); read.type = "button"; read.className = "button small secondary read-collection";
      read.textContent = "查看内容"; read.setAttribute("aria-controls", "collection-reader"); read.setAttribute("aria-pressed", "false");
      read.setAttribute("aria-label", "查看收藏夹内容：" + (item.title || "未命名收藏夹"));
      read.addEventListener("click", () => openCollection(item, read));
      collectionButtons.set(item.id, read);
      actions.append(visibility, read); row.append(number, content, actions); fragment.append(row);
    });
    $("collection-list").replaceChildren(fragment);
    $("collection-list").hidden = false;
    $("collection-state").hidden = true;
    $("collection-count").textContent = String(items.length);
  }

  function clearReader() {
    reader?.controller.abort();
    reader = null;
    for (const button of collectionButtons.values()) button.setAttribute("aria-pressed", "false");
    $("collection-reader").hidden = true;
    $("collection-reader").setAttribute("aria-busy", "false");
    $("reader-title").textContent = "";
    $("reader-description").textContent = "";
    $("reader-progress").textContent = "";
    $("reader-feedback").textContent = ""; $("reader-feedback").hidden = true;
    $("reader-state-title").textContent = ""; $("reader-state-copy").textContent = "";
    $("content-list").replaceChildren(); $("content-list").hidden = true;
    $("load-more").hidden = true; $("retry-content").hidden = true;
    $("import-page").hidden = true;
    $("import-feedback").textContent = ""; $("import-feedback").hidden = true;
  }

  function readerState(title, copy) {
    $("reader-state").hidden = false;
    $("reader-state-title").textContent = title;
    $("reader-state-copy").textContent = copy;
  }

  function updateReaderButtons(view) {
    if (reader !== view) return;
    $("refresh-content").disabled = view.busy;
    $("import-page").hidden = view.lastOffset === null || !view.lastCount;
    $("import-page").disabled = view.busy;
    $("import-page").textContent = view.importing ? "正在收录…" : "收录最近一页摘要（" + view.lastCount + " 条）";
    $("load-more").hidden = !view.loaded || view.nextOffset === null || view.failedOffset !== null;
    $("load-more").disabled = view.busy;
    $("retry-content").hidden = view.failedOffset === null;
    $("retry-content").disabled = view.busy;
    $("retry-content").textContent = view.loaded ? "重试本页" : "重新读取第一页";
  }

  function readerCurrent(view, ticket) {
    return reader === view && ticket === epoch && current !== null;
  }

  async function openCollection(collection, trigger) {
    if (!current || busy) return;
    clearReader();
    const view = {collection, trigger, items: new Map(), nextOffset: null, total: "0", loaded: false,
      busy: false, failedOffset: null, lastOffset: null, lastCount: 0, importing: false, controller: new AbortController()};
    reader = view;
    $("collection-reader").hidden = false;
    $("reader-title").textContent = collection.title || "未命名收藏夹";
    $("reader-description").textContent = collection.description || "暂无描述";
    trigger?.setAttribute("aria-pressed", "true");
    $("reader-title").focus();
    await loadContentPage(view, "0");
  }

  function validateContentItem(item) {
    if (!item || !contentTypes.has(item.type) || typeof item.url !== "string" || item.url.length > 2048 ||
        !/^[\x21-\x7e]+$/.test(item.url) || item.url.includes("\\")) throw new Error("upstream_protocol_error");
    const [, host, pattern] = contentTypes.get(item.type);
    let url;
    try { url = new URL(item.url); } catch { throw new Error("upstream_protocol_error"); }
    const match = url.pathname.match(pattern);
    if (url.protocol !== "https:" || url.hostname !== host || url.port || url.username || url.password ||
        !match || item.id !== item.type + ":" + match[1]) throw new Error("upstream_protocol_error");
    if (typeof item.title !== "string" || typeof item.summary !== "string" ||
        (item.author !== null && typeof item.author !== "string") ||
        ![item.created_at, item.collected_at].every(time => Number.isSafeInteger(time) && time >= 0 && time <= 253402300799) ||
        ![item.like_count, item.comment_count, item.favorite_count].every(decimal)) throw new Error("upstream_protocol_error");
    return {...item, url: "https://" + host + url.pathname};
  }

  function validateContentPage(data, view, offset) {
    if (data.user?.id !== current.user.id) throw new Error("session_expired");
    if (data.collection?.is_public !== true) throw new Error("collection_unavailable");
    const paging = data.paging;
    if (data.collection.id !== view.collection.id || !Array.isArray(data.items) || data.items.length > 20 || !paging ||
        paging.offset !== offset || paging.limit !== 20 || typeof paging.has_more !== "boolean" || !decimal(paging.total)) {
      throw new Error("upstream_protocol_error");
    }
    if (paging.has_more ? !decimal(paging.next_offset) || BigInt(paging.next_offset) <= BigInt(offset) : paging.next_offset !== null) {
      throw new Error("upstream_protocol_error");
    }
    return {items: data.items.map(validateContentItem), paging};
  }

  function renderContentItems(view) {
    const fragment = document.createDocumentFragment();
    for (const item of view.items.values()) {
      const row = document.createElement("li"); row.className = "content-item";
      const type = document.createElement("span"); type.className = "content-type"; type.textContent = contentTypes.get(item.type)[0];
      const heading = document.createElement("h4");
      const link = document.createElement("a"); link.href = item.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = item.title || "未命名" + type.textContent;
      link.setAttribute("aria-label", link.textContent + "（在知乎阅读原文，新标签页）"); heading.append(link);
      const meta = document.createElement("p"); meta.className = "content-meta";
      meta.textContent = (item.author || "作者未返回") + " · 收藏于 " + new Date(item.collected_at * 1000).toLocaleDateString("zh-CN");
      const summary = document.createElement("p"); summary.className = "content-summary"; summary.textContent = item.summary || "接口未返回摘要，请到知乎阅读原文。";
      const stats = document.createElement("p"); stats.className = "content-stats";
      stats.textContent = "赞同 " + BigInt(item.like_count).toLocaleString("zh-CN") + " · 评论 " + BigInt(item.comment_count).toLocaleString("zh-CN") + " · 收藏 " + BigInt(item.favorite_count).toLocaleString("zh-CN");
      row.append(type, heading, meta, summary, stats); fragment.append(row);
    }
    $("content-list").replaceChildren(fragment);
    $("content-list").hidden = view.items.size === 0;
    $("reader-state").hidden = view.items.size > 0;
    if (view.loaded) {
      $("reader-progress").textContent = "已展示 " + view.items.size + " 条 · 接口报告总数 " + BigInt(view.total).toLocaleString("zh-CN") + " 条" +
        (view.nextOffset === null ? " · 已到接口最后一页" : " · 可继续加载");
    } else $("reader-progress").textContent = "";
    if (!view.items.size) readerState(view.nextOffset === null ? "接口未返回公开内容" : "本页没有可见条目",
      view.nextOffset === null ? "收藏夹可能为空，或其中内容不在接口的公开范围。这不等于你在知乎网站上没有收藏。" : "平台仍返回了下一页位置，你可以手动继续加载。不会自动连续请求。");
  }

  function contentReadError(error, view, offset, ticket) {
    if (!readerCurrent(view, ticket) || error.name === "AbortError") return;
    if (authFailures.has(error.message)) { handleError(error, ticket); return; }
    const retryable = retryableReads.has(error.message);
    if (!retryable) {
      view.items.clear(); view.loaded = false; view.nextOffset = null; view.total = "0";
      view.lastOffset = null; view.lastCount = 0;
      $("import-feedback").hidden = true; $("import-feedback").textContent = "";
    }
    // Once earlier items are discarded, retry from page one instead of silently skipping them.
    view.failedOffset = retryable ? offset : "0";
    renderContentItems(view);
    $("reader-feedback").textContent = publicMessage(error.message);
    $("reader-feedback").hidden = false;
    if (!view.items.size) readerState("暂时无法读取公开内容", retryable
      ? "没有将接口错误当作空收藏夹。可稍后重试本页，或重新读取公开收藏夹列表。"
      : "已清除旧摘要。可从第一页重新尝试，或重新读取公开收藏夹列表核对访问范围。");
  }

  async function loadContentPage(view, offset) {
    if (reader !== view || !current || view.busy || offset === null) return;
    const ticket = epoch;
    view.busy = true; updateReaderButtons(view);
    $("collection-reader").setAttribute("aria-busy", "true");
    $("reader-feedback").textContent = ""; $("reader-feedback").hidden = true;
    if (!view.items.size) readerState("正在读取公开内容", "由服务端核验当前账户与公开收藏夹，再读取本页内容。可以随时关闭、切换收藏夹或退出。");
    try {
      const data = await request("/api/collections/" + view.collection.id + "/contents?offset=" + encodeURIComponent(offset), {signal: view.controller.signal});
      if (!readerCurrent(view, ticket)) return;
      const page = validateContentPage(data, view, offset);
      for (const item of page.items) if (!view.items.has(item.id)) view.items.set(item.id, item);
      view.nextOffset = page.paging.next_offset; view.total = page.paging.total;
      view.loaded = true; view.failedOffset = null;
      view.lastOffset = offset; view.lastCount = page.items.length;
      $("import-feedback").hidden = true; $("import-feedback").textContent = "";
      renderContentItems(view);
    } catch (error) { contentReadError(error, view, offset, ticket); }
    finally {
      if (readerCurrent(view, ticket)) {
        view.busy = false; updateReaderButtons(view);
        $("collection-reader").setAttribute("aria-busy", "false");
      }
    }
  }

  async function importPage() {
    const view = reader;
    if (!view || !current || view.busy || view.lastOffset === null || !view.lastCount) return;
    const ticket = epoch, offset = view.lastOffset;
    view.busy = true; view.importing = true; updateReaderButtons(view);
    $("import-feedback").hidden = true; $("import-feedback").textContent = "";
    try {
      const data = await request("/api/workspace/import", {
        method: "POST", signal: view.controller.signal,
        headers: {"Content-Type": "application/json", "X-CSRF-Token": current.csrf_token},
        body: JSON.stringify({collection_id: view.collection.id, offset}),
      });
      if (!readerCurrent(view, ticket)) return;
      if (data.user?.id !== current.user.id) throw new Error("session_expired");
      if (data.source !== "public_summary" || data.offset !== offset || !data.imported ||
          ![data.imported.added, data.imported.updated, data.imported.total].every(n => Number.isSafeInteger(n) && n >= 0)) {
        throw new Error("upstream_protocol_error");
      }
      $("import-feedback").textContent = "已收录本页：新增 " + data.imported.added + " 条，更新 " + data.imported.updated +
        " 条。你的工作区共有 " + data.imported.total + " 条摘要。点击“进入我的知识库工作区”查看。";
      $("import-feedback").hidden = false;
    } catch (error) {
      if (!readerCurrent(view, ticket) || error.name === "AbortError") return;
      if (authFailures.has(error.message)) handleError(error, ticket);
      else if (["permission_denied", "collection_unavailable", "collections_configuration_required", "upstream_protocol_error"].includes(error.message)) {
        contentReadError(error, view, offset, ticket);
      } else {
        $("import-feedback").textContent = publicMessage(error.message);
        $("import-feedback").hidden = false;
      }
    } finally {
      if (readerCurrent(view, ticket)) { view.busy = false; view.importing = false; updateReaderButtons(view); }
    }
  }

  function handleError(error, ticket, showLoginRequired = true) {
    if (ticket !== epoch || error.name === "AbortError") return;
    const code = error.message;
    if (authFailures.has(code)) {
      invalidateRequests();
      busy = false; resetAccount();
    }
    if (code !== "login_required" || showLoginRequired) notice(publicMessage(code), code === "login_required" ? "warning" : "error");
  }

  async function loadCollections(ticket = epoch) {
    if (!current) return;
    if (!collectionsConfigured) {
      clearReader();
      collectionState("已连接账号，收藏接口待配置", publicMessage("collections_configuration_required"));
      return;
    }
    clearReader();
    busy = true; updateButtons();
    $("collections").setAttribute("aria-busy", "true");
    collectionState("正在读取公开收藏夹", "请求携带当前用户的授权 Token，仅由服务端直接调用知乎接口。");
    try {
      const data = await request("/api/collections");
      if (ticket === epoch) renderCollections(data);
    } catch (error) {
      if (ticket === epoch && error.name !== "AbortError") {
        collectionState("暂时无法读取", publicMessage(error.message));
        handleError(error, ticket);
      }
    } finally {
      if (ticket === epoch) {
        busy = false; updateButtons();
        $("collections").setAttribute("aria-busy", "false");
      }
    }
  }

  async function refreshSession(initial = false) {
    const ticket = invalidateRequests();
    busy = true; resetAccount();
    try {
      const status = await request("/api/status");
      if (ticket !== epoch) return;
      messages = status.messages || {};
      applyConfiguration(status.configuration);
      resetAccount();
      if (initial && callbackResult) {
        if (callbackResult === "connected") notice("身份识别已完成，正在读取当前账户的公开收藏夹。", "success");
        else if (Object.prototype.hasOwnProperty.call(messages, callbackResult)) notice(publicMessage(callbackResult), "warning");
      } else if (!initial) notice("");
      const identity = await request("/api/me");
      if (ticket !== epoch) return;
      renderAccount(identity);
      if (loginPage) { location.replace(returnTo || defaultReturnTo); return; }
      await loadCollections(ticket);
    } catch (error) {
      handleError(error, ticket, !initial);
    } finally {
      if (ticket === epoch) { busy = false; updateButtons(); }
    }
  }

  $("login").addEventListener("click", async () => {
    const ticket = invalidateRequests();
    busy = true; updateButtons(); notice("");
    try {
      const data = await request("/auth/zhihu/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(returnTo ? {return_to: returnTo} : {}),
      });
      if (ticket !== epoch) return;
      const url = new URL(data.authorization_url);
      if (url.origin !== "https://openapi.zhihu.com" || url.pathname !== "/authorize") throw new Error("upstream_protocol_error");
      location.assign(url.href);
    } catch (error) {
      handleError(error, ticket);
      if (ticket === epoch) { busy = false; updateButtons(); }
    }
  });

  $("logout").addEventListener("click", async () => {
    const csrf = current?.csrf_token;
    const ticket = invalidateRequests();
    busy = true; resetAccount();
    try {
      await request("/auth/logout", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf || ""}, body: "{}"});
      if (ticket === epoch) notice("已退出，服务端会话已清除。这不等于在知乎端撤销应用授权。", "success");
    } catch (error) { handleError(error, ticket); }
    finally { if (ticket === epoch) { busy = false; updateButtons(); } }
  });

  $("close-reader").addEventListener("click", () => {
    const trigger = reader?.trigger; clearReader(); trigger?.focus();
  });
  $("import-page").addEventListener("click", importPage);
  $("load-more").addEventListener("click", () => reader && loadContentPage(reader, reader.nextOffset));
  $("retry-content").addEventListener("click", () => reader && loadContentPage(reader, reader.failedOffset));
  $("refresh-content").addEventListener("click", () => reader && openCollection(reader.collection, reader.trigger));

  $("refresh-session").addEventListener("click", () => refreshSession());
  $("refresh-collections").addEventListener("click", () => { notice(""); loadCollections(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") { invalidateRequests(); busy = false; resetAccount(); }
    else refreshSession();
  });
  window.addEventListener("pageshow", event => { if (event.persisted) refreshSession(); });
  refreshSession(true);
})();


/* Decorative pointer response only. No account, button, navigation, or API behavior. */
(() => {
  const scene = document.getElementById("hero-field");
  if (!scene?.style || typeof window.matchMedia !== "function" ||
      typeof window.requestAnimationFrame !== "function") return;
  const surface = scene.closest(".intro");
  if (!surface) return;

  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const properties = ["--pointer-x", "--pointer-y", "--pointer-active", "--drift-x", "--drift-y"];
  let frame = 0;
  let clientX = 0;
  let clientY = 0;

  function enabled() {
    return finePointer.matches && !reducedMotion.matches && !document.hidden;
  }

  function render() {
    frame = 0;
    if (!enabled()) return;
    const bounds = scene.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const x = Math.max(0, Math.min(1, (clientX - bounds.left) / bounds.width));
    const y = Math.max(0, Math.min(1, (clientY - bounds.top) / bounds.height));
    scene.style.setProperty("--pointer-x", (x * 100).toFixed(2) + "%");
    scene.style.setProperty("--pointer-y", (y * 100).toFixed(2) + "%");
    scene.style.setProperty("--pointer-active", "1");
    scene.style.setProperty("--drift-x", ((x - .5) * 18).toFixed(2) + "px");
    scene.style.setProperty("--drift-y", ((y - .5) * 12).toFixed(2) + "px");
  }

  function reset() {
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
    for (const property of properties) scene.style.removeProperty(property);
  }

  surface.addEventListener("pointermove", event => {
    if (!enabled() || event.pointerType === "touch") return;
    clientX = event.clientX;
    clientY = event.clientY;
    if (!frame) frame = window.requestAnimationFrame(render);
  }, {passive: true});
  surface.addEventListener("pointerleave", reset, {passive: true});
  surface.addEventListener("pointercancel", reset, {passive: true});
  finePointer.addEventListener("change", reset);
  reducedMotion.addEventListener("change", reset);
  document.addEventListener("visibilitychange", reset);
  window.addEventListener("pagehide", reset);
})();
