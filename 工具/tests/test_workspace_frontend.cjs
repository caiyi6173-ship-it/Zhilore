// Synthetic transport only; no real accounts, cookies or network.
'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../网页/oauth/workspace.js'), 'utf8');
const importSource = fs.readFileSync(path.join(__dirname, '../../网页/oauth/import-collections.js'), 'utf8');
const redirectSource = fs.readFileSync(path.join(__dirname, '../../网页/oauth/login-redirect.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const identity = (id = 'A', csrf = 'csrf-' + id, expires = Date.now() + 60000) => ({
  authenticated: true, user: {id, name: '相同昵称', provider: 'zhihu'}, csrf_token: csrf,
  expires_at: new Date(expires).toISOString(),
});

function harness({pathname = '/workspace/', search = '', hash = '', 横幅 = false} = {}) {
  const requests = [], navigation = [], documentEvents = new Map(), windowEvents = new Map(), timers = new Map(), 间隔 = new Map();
  const classes = new Set(['workspace-locked']), elements = new Map();
  let sequence = 0, cleanups = 0;
  for (const id of ['workspace-gate', 'workspace-account', 'workspace-logout', 'h1', 'p']) {
    elements.set(id, {textContent: '', disabled: true, listeners: new Map(),
      addEventListener(name, fn) { this.listeners.set(name, fn); },
      querySelector(selector) { return elements.get(selector); },
    });
  }
  if (横幅) {
    // 「知乎收藏」顶部的直答处理横幅。加载时机由调用方决定：会话就绪后再跑脚本，
    // 否则脚本第一次取状态只会拿到 AbortError 并进入重试等待。
    for (const id of ['zh-import', 'zh-import-start', 'zh-import-status', 'zh-import-bar-fill',
                      'zh-import-issues', 'zh-import-quota', 'zh-import-secret']) {
      elements.set(id, {textContent: '', disabled: false, hidden: false, style: {}, dataset: {}, listeners: new Map(),
        addEventListener(name, fn) { this.listeners.set(name, fn); },
        querySelector(selector) { return elements.get(selector); },
      });
    }
  }
  const document = {visibilityState: 'visible', body: {dataset: {}, classList: {
    add: name => classes.add(name), remove: name => classes.delete(name),
  }}, getElementById: id => elements.get(id), addEventListener: (name, fn) => documentEvents.set(name, fn)};
  const window = {addEventListener: (name, fn) => windowEvents.set(name, fn)};
  const context = vm.createContext({window, document, Date, AbortController, AbortSignal, DOMException, URLSearchParams,
    localStorage: {getItem() { throw new Error('No persistent account preferences'); }},
    setTimeout(fn) { timers.set(++sequence, fn); return sequence; }, clearTimeout: id => timers.delete(id),
    setInterval(fn) { 间隔.set(++sequence, fn); return sequence; }, clearInterval: id => 间隔.delete(id),
    location: {pathname, search, hash, replace: url => navigation.push(url), assign: url => navigation.push(url),
      reload: () => navigation.push('RELOAD')},
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject, answered: false})),
  });
  vm.runInContext(redirectSource, context, {filename: 'login-redirect.js'});
  vm.runInContext(source, context, {filename: 'workspace.js'});
  const api = window.WorkspaceAccess;
  api.setCleanup(() => { cleanups += 1; });
  const take = url => {const r = requests.find(r => r.url === url && !r.answered); assert.ok(r, 'Missing request: ' + url); r.answered = true; return r;};
  const reply = (r, data, status = 200) => r.resolve({ok: status >= 200 && status < 300, status, json: async () => data});
  return {api, requests, navigation, timers, 间隔, classes, elements, document, take, reply,
    cleanups: () => cleanups,
    加载横幅: () => vm.runInContext(importSource, context, {filename: 'import-collections.js'}),
    跑定时器() { const 待跑 = [...timers.values()]; timers.clear(); 待跑.forEach(fn => fn()); },
    轮询一次: () => [...间隔.values()].forEach(fn => fn()),
    hidden() { document.visibilityState = 'hidden'; documentEvents.get('visibilitychange')(); },
    visible() { document.visibilityState = 'visible'; documentEvents.get('visibilitychange')(); },
    pagehide: () => windowEvents.get('pagehide')(),
    pageshow: () => windowEvents.get('pageshow')({persisted: true}),
    logout: () => elements.get('workspace-logout').listeners.get('click')(),
  };
}

async function boot(h, me = identity()) {
  const pending = h.api.initialize();
  h.reply(h.take('/api/me'), me);
  assert.equal(await pending, true);
}

// All account information must be verified before requesting the personal index.
test('未核验身份时工作区保持锁定且不能请求知识库', async () => {
  const h = harness();
  await assert.rejects(h.api.request('/api/索引'), {name: 'AbortError'});
  assert.ok(h.classes.has('workspace-locked'));
  assert.equal(h.requests.length, 0);
});

test('核验后显示稳定 ID，同源请求带会话标记且不走共享 API', async () => {
  const h = harness(); await boot(h);
  assert.equal(h.classes.has('workspace-locked'), false);
  assert.match(h.elements.get('workspace-account').textContent, /知乎 ID A/);
  // 「收藏夹」是「知乎收藏」页卡片列表用的只读端点，同样必须走受控封装。
  for (const name of ['索引', '图谱', '笔记?路径=summaries%2Fmock.md', '搜索?q=mock', '收藏夹']) {
    const pending = h.api.request('/api/' + name);
    const request = h.take('/api/workspace/' + name);
    assert.equal(request.options.headers['X-Workspace-Session'], 'csrf-A');
    assert.equal(request.options.credentials, 'same-origin');
    assert.equal(request.options.cache, 'no-store');
    h.reply(request, {synthetic: true});
    assert.equal((await (await pending).json()).synthetic, true);
  }
});

test('不允许共享重扫、写请求或任意外部地址', async () => {
  const h = harness(); await boot(h);
  for (const [url, options] of [['/api/重扫', {method: 'POST'}], ['/api/索引', {method: 'POST'}],
                               ['/api/收藏夹', {method: 'POST'}],
                               ['https://other.invalid/api/索引', {}], ['/api/../笔记库', {}]]) {
    await assert.rejects(h.api.request(url, options), /Unsupported workspace operation/);
  }
  assert.equal(h.requests.length, 1);
});

test('折叠等偏好只驻留当前页面内存，锁屏时清空', async () => {
  const h = harness(); await boot(h);
  h.api.preferences.setItem('折叠', 'A-only');
  assert.equal(h.api.preferences.getItem('折叠'), 'A-only');
  h.hidden();
  assert.equal(h.api.preferences.getItem('折叠'), null);
  assert.ok(h.cleanups() > 0);
});

test('隐藏页面即锁定清屏，中止旧请求且晚到响应不能重新显示', async () => {
  const h = harness(); await boot(h);
  const pending = h.api.request('/api/笔记?路径=mock');
  const rejected = assert.rejects(pending, {name: 'AbortError'});
  const request = h.take('/api/workspace/笔记?路径=mock');
  h.hidden();
  assert.equal(request.options.signal.aborted, true);
  h.reply(request, {内容: 'A-only'});
  await rejected;
  assert.ok(h.classes.has('workspace-locked'));
  assert.doesNotMatch(h.elements.get('workspace-account').textContent, /知乎 ID A/);
});

test('响应已到但 JSON 尚未被业务代码读取时，切号锁定仍使其失效', async () => {
  const h = harness(); await boot(h);
  const pending = h.api.request('/api/索引');
  h.reply(h.take('/api/workspace/索引'), {文章: ['A-only']});
  const response = await pending;
  h.hidden();
  await assert.rejects(response.json(), {name: 'AbortError'});
});

test('返回页面须核验相同 ID 和同一轮会话，通过后重新加载而非恢复缓存', async () => {
  const h = harness(); await boot(h); h.hidden(); h.visible();
  h.reply(h.take('/api/me'), identity()); await flush();
  assert.deepEqual(h.navigation, ['RELOAD']);
  assert.ok(h.classes.has('workspace-locked'));
});

for (const me of [identity('B'), identity('A', 'rotated-csrf')]) {
  test('账号或会话轮换后返回账号页，不显示新身份配旧数据 ' + me.csrf_token, async () => {
    const h = harness(); await boot(h); h.hidden(); h.visible();
    h.reply(h.take('/api/me'), me); await flush();
    assert.deepEqual(h.navigation, ['/login?returnTo=%2Fworkspace%2F&result=workspace_session_changed']);
    assert.ok(h.classes.has('workspace-locked'));
  });
}

test('浏览器 BFCache 恢复也先清屏、核验再读取', async () => {
  const h = harness(); await boot(h); h.pagehide(); h.pageshow();
  h.reply(h.take('/api/me'), identity('B')); await flush();
  assert.deepEqual(h.navigation, ['/login?returnTo=%2Fworkspace%2F&result=workspace_session_changed']);
  assert.ok(h.cleanups() >= 2);
});

test('Token 到期立即清空并要求重新授权', async () => {
  const h = harness(); await boot(h);
  [...h.timers.values()][0]();
  assert.deepEqual(h.navigation, ['/login?returnTo=%2Fworkspace%2F&result=token_expired']);
  assert.ok(h.classes.has('workspace-locked'));
  await assert.rejects(h.api.request('/api/索引'), {name: 'AbortError'});
});

for (const [status, code] of [[401, 'token_expired'], [401, 'unknown-auth-error'], [409, 'workspace_session_changed']]) {
  test('接口授权失效触发整体清屏而非仅显示读取失败 ' + code, async () => {
    const h = harness(); await boot(h);
    const pending = h.api.request('/api/图谱');
    const rejected = assert.rejects(pending, {name: 'AbortError'});
    h.reply(h.take('/api/workspace/图谱'), {error: {code}}, status);
    await rejected;
    assert.ok(h.classes.has('workspace-locked'));
    assert.equal(h.navigation.length, 1);
  });
}

test('存储暂不可用返回可重试错误，不误认为成功或切换用户', async () => {
  const h = harness(); await boot(h);
  const pending = h.api.request('/api/索引');
  h.reply(h.take('/api/workspace/索引'), {error: {code: 'workspace_unavailable'}}, 503);
  assert.equal((await pending).ok, false);
  assert.deepEqual(h.navigation, []);
});

test('退出使用当前 CSRF，确认成功后返回账号页', async () => {
  const h = harness(); await boot(h);
  const pending = h.logout(), request = h.take('/auth/logout');
  assert.ok(h.classes.has('workspace-locked'));
  assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-A');
  assert.equal(request.options.method, 'POST');
  h.reply(request, {logged_out: true}); await pending;
  assert.deepEqual(h.navigation, ['/login?returnTo=%2Fworkspace%2F']);
});

test('退出失败保持锁定，不声称已退出、不恢复缓存', async () => {
  const h = harness(); await boot(h);
  const pending = h.logout();
  h.reply(h.take('/auth/logout'), {error: {code: 'csrf_rejected'}}, 403); await pending;
  assert.deepEqual(h.navigation, []);
  assert.ok(h.classes.has('workspace-locked'));
  assert.equal(h.elements.get('h1').textContent, '退出尚未完成');
});

test('身份核验中被隐藏的页面不能被晚到响应解锁', async () => {
  const h = harness(), pending = h.api.initialize(), request = h.take('/api/me');
  h.hidden(); h.reply(request, identity());
  assert.equal(await pending, false);
  assert.ok(h.classes.has('workspace-locked'));
});

test('已过期身份不能启动个人数据加载', async () => {
  const h = harness(), pending = h.api.initialize();
  h.reply(h.take('/api/me'), identity('A', 'csrf-A', Date.now() - 5000));
  assert.equal(await pending, false);
  assert.deepEqual(h.navigation, ['/login?returnTo=%2Fworkspace%2F&result=token_expired']);
});


test('手机侧栏定位在个人画布内，不覆盖账号与退出入口', () => {
  const css = fs.readFileSync(path.join(__dirname, '../../网页/oauth/workspace.css'), 'utf8');
  assert.match(css, /\.personal-workspace \.layout\s*\{\s*position:\s*relative;/);
  assert.ok(css.includes('grid-column: 1 / -1')); 
  assert.match(css, /@media\s*\(max-width:\s*760px\)\s*\{[\s\S]*?\.personal-workspace \.sidebar\s*\{\s*bottom:\s*0;/);
});

for (const action of ['expire', 'logout', '401']) {
  test('工作区' + action + '返回登录页时完整保留查询串与片段', async () => {
    const h = harness({search: '?q=a%26b&view=graph', hash: '#note-2'});
    await boot(h);
    let expectedResult;
    if (action === 'expire') {
      [...h.timers.values()][0](); expectedResult = 'token_expired';
    } else if (action === 'logout') {
      const pending = h.logout(); h.reply(h.take('/auth/logout'), {logged_out: true}); await pending;
    } else {
      const pending = h.api.request('/api/索引');
      const rejected = assert.rejects(pending, {name: 'AbortError'});
      h.reply(h.take('/api/workspace/索引'), {error: {code: 'token_expired'}}, 401);
      await rejected; expectedResult = 'token_expired';
    }
    assert.equal(h.navigation.length, 1);
    const url = new URL(h.navigation[0], 'https://fixture.invalid');
    assert.equal(url.pathname, '/login');
    assert.equal(url.searchParams.get('returnTo'), '/workspace/?q=a%26b&view=graph#note-2');
    assert.equal(url.searchParams.get('result'), expectedResult || null);
  });
}

// 「个人知识库」与「知乎收藏」共用这套脚本，会话失效时各自回到自己那一页。
test('个人知识库页会话失效时回个人知识库而不是知乎收藏', async () => {
  const h = harness({pathname: '/'});
  await boot(h);
  const pending = h.api.request('/api/索引');
  const rejected = assert.rejects(pending, {name: 'AbortError'});
  h.reply(h.take('/api/workspace/索引'), {error: {code: 'token_expired'}}, 401);
  await rejected;
  assert.equal(h.navigation.length, 1);
  assert.equal(new URL(h.navigation[0], 'https://fixture.invalid').searchParams.get('returnTo'), '/');
});

// 横幅加载后有两路请求并行：接回进度（状态）与额度轮询。按 URL 应答，避免顺序耦合。
async function 应答横幅(h, {状态 = {task: null}, 额度剩余 = 12} = {}) {
  for (let i = 0; i < 10; i += 1) {
    const 未答 = h.requests.filter(r => !r.answered);
    if (!未答.length) break;
    for (const r of 未答) {
      r.answered = true;
      if (r.url === '/api/直答额度') h.reply(r, {可用: true, 剩余: 额度剩余, 消耗方: '应用凭证（开发者账号）'});
      else h.reply(r, 状态);
    }
    await flush();
  }
}

test('直答处理跑完跳进个人知识库，加载时读到的历史结果不跳', async () => {
  const h = harness({横幅: true});
  await boot(h);
  h.timers.clear();  // 清掉会话到期定时器，只观察横幅自己的延时跳转
  h.加载横幅();
  await 应答横幅(h, {额度剩余: 12});
  assert.equal(h.elements.get('zh-import').dataset.state, undefined);
  assert.match(h.elements.get('zh-import-quota').textContent, /剩余 12 次/);
  assert.match(h.elements.get('zh-import-quota').textContent, /应用凭证/, '额度属于应用凭证，界面要说清楚');

  const 点击 = h.elements.get('zh-import-start').listeners.get('click')();
  h.reply(h.take('/api/直答处理'), {task: {id: 't1', 阶段: '读取收藏夹', 条目总数: 10, 条目完成: 0,
    预计调用: 2, 已用调用: 0, 完成: false, 失败: ''}}, 202);
  await 点击;
  assert.equal(h.elements.get('zh-import-start').textContent, '生成中…');

  h.轮询一次();
  h.reply(h.take('/api/直答处理/状态?task=t1'), {task: {id: 't1', 消息: '完成：3 个知识块',
    条目总数: 10, 条目完成: 10, 预计调用: 2, 已用调用: 2, 完成: true, 失败: ''}});
  await flush(); await flush();
  assert.equal(h.elements.get('zh-import').dataset.state, 'done');
  assert.equal(h.elements.get('zh-import-start').textContent, '用直答生成知识地图（收藏 + 创作地图）', '跑完要把按钮文案还原');
  assert.equal(h.elements.get('zh-import-bar-fill').style.width, '100%');
  assert.match(h.elements.get('zh-import-status').textContent, /正在进入个人知识库/);
  assert.deepEqual(h.navigation, []);
  // 处理完立刻重查额度，让用户看到消耗。
  h.reply(h.take('/api/直答额度'), {可用: true, 剩余: 10, 消耗方: '应用凭证（开发者账号）'});
  await flush();
  assert.match(h.elements.get('zh-import-quota').textContent, /剩余 10 次/);
  h.跑定时器();
  assert.deepEqual(h.navigation, ['/']);

  // 重新打开页面时读到的是上一次的历史结果：只展示，不再把人弹走。
  const 再来 = harness({横幅: true});
  await boot(再来);
  再来.timers.clear();
  再来.加载横幅();
  await 应答横幅(再来, {状态: {task: {id: 't0', 条目总数: 10, 条目完成: 10, 完成: true, 失败: ''}},
    额度剩余: 0});
  assert.equal(再来.elements.get('zh-import').dataset.state, 'done');
  assert.equal(再来.elements.get('zh-import-status').textContent, '上次处理已完成');
  assert.match(再来.elements.get('zh-import-quota').textContent, /剩余 0 次/);
  再来.跑定时器();
  assert.deepEqual(再来.navigation, []);
});

test('额度读不到时横幅照样可用', async () => {
  const h = harness({横幅: true});
  await boot(h);
  h.timers.clear();
  h.加载横幅();
  for (let i = 0; i < 10; i += 1) {
    const 未答 = h.requests.filter(r => !r.answered);
    if (!未答.length) break;
    for (const r of 未答) {
      r.answered = true;
      if (r.url === '/api/直答额度') h.reply(r, {error: {code: 'internal_error'}}, 500);
      else h.reply(r, {task: null});
    }
    await flush();
  }
  assert.match(h.elements.get('zh-import-quota').textContent, /暂时读不到/);
  assert.equal(h.elements.get('zh-import-start').disabled, false, '额度读不到不能挡住处理按钮');
});
