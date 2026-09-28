"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const board = $("zh-import");
  // 这个横幅只在个人知识库里出现，请求必须走工作区的受控封装（带会话与 CSRF）。
  if (!board || !window.WorkspaceAccess) return;

  const POLL_MS = 2000;
  const 额度刷新毫秒 = 30000;
  const startButton = $("zh-import-start");
  const statusLine = $("zh-import-status");
  const barFill = $("zh-import-bar-fill");
  const issueList = $("zh-import-issues");
  const quotaLine = $("zh-import-quota");
  const {request} = window.WorkspaceAccess;
  let taskId = "";
  let timer = null;
  let quotaTimer = null;
  let busy = false;

  async function 刷新额度() {
    try {
      const data = await call("/api/直答额度");
      if (typeof data.剩余 === "number") {
        quotaLine.textContent = `今日直答额度：剩余 ${data.剩余} 次 · ${data.消耗方}`;
      } else {
        quotaLine.textContent = "今日直答额度：暂时读不到（不影响处理）";
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      quotaLine.textContent = "今日直答额度：暂时读不到（不影响处理）";
    }
  }

  function setBusy(value) {
    busy = value;
    startButton.disabled = value;
    startButton.textContent = value ? "生成中…" : "用直答生成知识地图（收藏 + 创作地图）";
  }

  async function call(url, options) {
    const response = await request(url, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.error?.message || data.error?.code || "请求失败");
      error.code = data.error?.code || "http_" + response.status;
      throw error;
    }
    return data;
  }

  function paint(task, 可以刷新 = false) {
    if (!task) return;
    const total = task.条目总数 || 0;
    const done = task.条目完成 || 0;
    const percent = total ? Math.min(100, Math.round((done / total) * 100)) : (task.完成 ? 100 : 0);
    barFill.style.width = percent + "%";
    // 额度按自然日重置且可能只有个位数，所以把「这次要花几次」和「今日还剩几次」一起显示。
    const 明细 = [];
    if (task.预计调用) 明细.push(`直答调用 ${task.已用调用}/${task.预计调用} 次`);
    if (typeof task.今日剩余额度 === "number") 明细.push(`今日额度剩余 ${task.今日剩余额度} 次`);
    let text = task.消息 || task.阶段 || "";
    if (明细.length) text += `（${明细.join(" · ")}）`;
    statusLine.textContent = text;
    if (task.问题 && task.问题.length) {
      issueList.textContent = task.问题.join("；");
      issueList.hidden = false;
    } else {
      issueList.hidden = true;
    }
    if (!task.完成) return;
    clearInterval(timer);
    timer = null;
    setBusy(false);
    刷新额度();  // 处理完立刻反映消耗
    if (task.失败) {
      board.dataset.state = "failed";
      statusLine.textContent = task.失败;
      return;
    }
    board.dataset.state = "done";
    // 只有本页面刚刚跑完才跳转；加载时读到的历史结果只展示，否则回到本页会被再次弹走。
    if (!可以刷新) {
      statusLine.textContent = task.消息 || "上次处理已完成";
      return;
    }
    // 处理结果写进当前账号的知识库，看完进度直接进「个人知识库」看图谱。
    statusLine.textContent = (task.消息 || "处理完成") + " · 正在进入个人知识库…";
    setTimeout(() => location.assign("/"), 2000);
  }

  function poll() {
    clearInterval(timer);
    timer = setInterval(async () => {
      try {
        const query = taskId ? "?task=" + encodeURIComponent(taskId) : "";
        paint((await call("/api/直答处理/状态" + query)).task, true);
      } catch (error) {
        if (error.name === "AbortError") return;
        clearInterval(timer);
        timer = null;
        setBusy(false);
        board.dataset.state = "failed";
        statusLine.textContent = error.message;
      }
    }, POLL_MS);
  }

  startButton.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    issueList.hidden = true;
    board.dataset.state = "running";
    statusLine.textContent = "正在读取公开收藏夹…";
    try {
      const data = await call("/api/直答处理", {method: "POST", body: "{}"});
      taskId = data.task?.id || "";
      paint(data.task);
      poll();
    } catch (error) {
      if (error.name === "AbortError") return;
      setBusy(false);
      board.dataset.state = "failed";
      statusLine.textContent = error.message;
    }
  });

  // 工作区封装要等 app.js 调完 initialize 才有 session；在这之前调用只会拿到 AbortError。
  async function 等就绪() {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        return await call("/api/直答处理/状态");
      } catch (error) {
        if (error.name !== "AbortError") throw error;
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }
    throw new Error("工作区还没就绪，请刷新页面重试");
  }

  (async () => {
    try {
      // 页面刷新后把进行中的任务接回来；没有任务不是错误。
      paint((await 等就绪()).task);
    } catch (error) {
      if (error.code === "task_not_found") return;
      statusLine.textContent = "暂时无法检查处理状态：" + error.message;
    }
  })();

  // 额度独立轮询：额度是开发者的池子，用户随时要知道还剩多少。
  (async () => {
    try {
      await 等就绪();  // 复用同一条就绪判定，确认 workspace.js 已核验完账号
    } catch {
      // 就绪失败也照样试一次额度，拿不到就显示"暂时读不到"，不挡住处理按钮。
    }
    await 刷新额度();
    quotaTimer = setInterval(刷新额度, 额度刷新毫秒);
  })();
})();
