// Synthetic browser state only. No real accounts, credentials or network.
'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const redirectSource = fs.readFileSync(path.join(__dirname, '../../网页/oauth/login-redirect.js'), 'utf8');
const sessionSource = fs.readFileSync(path.join(__dirname, '../../网页/oauth/integrated-session.js'), 'utf8');
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'return_to_cases.json'), 'utf8'));
const flush = () => new Promise(resolve => setImmediate(resolve));
const identity = (id = 'fixture-A', expires = Date.now() + 60000) => ({
  authenticated: true, user: {id, name: '模拟昵称', provider: 'zhihu'},
  csrf_token: 'csrf-' + id, expires_at: new Date(expires).toISOString(),
});

function redirects(location = {pathname: '/', search: '', hash: ''}) {
  const context = vm.createContext({window: {}, location, URLSearchParams});
  vm.runInContext(redirectSource, context, {filename: 'login-redirect.js'});
  return context.window.LoginRedirect;
}

for (const fixture of cases) {
  test('前后端共享 returnTo 样本：' + fixture.name, () => {
    assert.equal(redirects().safeReturnPath(fixture.input), fixture.expected);
  });
}

test('共享回跳工具不可覆写，且生成的登录地址保留编码而非提前解码', () => {
  const helper = redirects();
  assert.ok(Object.isFrozen(helper));
  const target = '/workspace/?q=a%26b#note-2';
  const url = new URL(helper.loginUrl(target, '/', 'token_expired'), 'https://fixture.invalid');
  assert.equal(url.pathname, '/login');
  assert.equal(url.searchParams.get('returnTo'), target);
  assert.equal(url.searchParams.get('result'), 'token_expired');
  assert.equal(helper.loginUrl('//outside.invalid', 'https://outside.invalid'), '/login?returnTo=%2Fworkspace%2F');
});

test('当前页面目标包含查询串与片段，嵌套 returnTo 目标回落默认值', () => {
  assert.equal(redirects({pathname: '/workspace/', search: '?q=a%26b', hash: '#note'}).currentTarget(), '/workspace/?q=a%26b#note');
  assert.equal(redirects({pathname: '/', search: '?returnTo=//outside.invalid', hash: ''}).currentTarget('/'), '/');
});

function harness({pathname = '/', search = '', hash = '', chipPresent = true} = {}) {
  const requests = [], navigation = [], timers = new Map(), documentEvents = new Map(), windowEvents = new Map();
  const elements = new Map();
  let sequence = 0;
  for (const id of ['zh-account', 'zh-account-name', 'zh-account-out']) {
    elements.set(id, {hidden: true, disabled: false, textContent: '', listeners: new Map(),
      set innerHTML(_value) { throw new Error('Account names must be plain text'); },
      addEventListener(name, handler) { this.listeners.set(name, handler); },
    });
  }
  if (!chipPresent) elements.delete('zh-account');
  const document = {visibilityState: 'visible', getElementById: id => elements.get(id),
    addEventListener: (name, handler) => documentEvents.set(name, handler)};
  const window = {addEventListener: (name, handler) => windowEvents.set(name, handler)};
  const context = vm.createContext({window, document, URLSearchParams, Date,
    location: {pathname, search, hash, replace: url => navigation.push(url)},
    setTimeout(fn, delay) { timers.set(++sequence, {fn, delay}); return sequence; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject, answered: false})),
  });
  vm.runInContext(redirectSource, context, {filename: 'login-redirect.js'});
  vm.runInContext(sessionSource, context, {filename: 'integrated-session.js'});
  const take = url => {const request = requests.find(r => r.url === url && !r.answered); assert.ok(request, 'Missing request: ' + url); request.answered = true; return request;};
  const reply = (request, data, status = 200) => request.resolve({ok: status >= 200 && status < 300, status, json: async () => data});
  return {requests, navigation, timers, document, elements, take, reply,
    hidden() { document.visibilityState = 'hidden'; documentEvents.get('visibilitychange')(); },
    visible() { document.visibilityState = 'visible'; documentEvents.get('visibilitychange')(); },
    pageshow: () => windowEvents.get('pageshow')({persisted: true}),
    pagehide: () => windowEvents.get('pagehide')(),
    expire() { const timer = [...timers.values()][0]; assert.ok(timer, 'Must schedule expiry'); timer.fn(); },
    logout: () => elements.get('zh-account-out').listeners.get('click')(),
  };
}

async function boot(h, me = identity()) {
  h.reply(h.take('/api/me'), me); await flush();
}
function assertRedirect(h, target, result = null) {
  assert.equal(h.navigation.length, 1);
  const url = new URL(h.navigation[0], 'https://fixture.invalid');
  assert.equal(url.pathname, '/login');
  assert.equal(url.searchParams.get('returnTo'), target);
  assert.equal(url.searchParams.get('result'), result);
}

test('一体化账号条缺席时不发请求', () => {
  const h = harness({chipPresent: false});
  assert.deepEqual(h.requests, []);
});

test('一体化身份核验不缓存，显示文本昵称并设置到期计时', async () => {
  const h = harness(), me = identity(); me.user.name = '<img src=x onerror=alert(1)>';
  const request = h.take('/api/me');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.cache, 'no-store');
  h.reply(request, me); await flush();
  assert.equal(h.elements.get('zh-account-name').textContent, '知乎 · ' + me.user.name);
  assert.equal(h.elements.get('zh-account').hidden, false);
  assert.equal(h.elements.get('zh-account-out').disabled, false);
  assert.equal(h.timers.size, 1);
  assert.deepEqual(h.navigation, []);
});

for (const code of ['login_required', 'session_expired', 'token_expired', 'authorization_failed', 'unknown-error']) {
  test('一体化身份失效只用固定错误码回登录 ' + code, async () => {
    const h = harness({search: '?view=graph', hash: '#selected'});
    h.reply(h.take('/api/me'), {error: {code}}, 401); await flush();
    assertRedirect(h, '/?view=graph#selected', code === 'unknown-error' ? 'session_expired' : code);
    assert.equal(h.elements.get('zh-account').hidden, true);
  });
}

test('一体化 Token 到期回跳保留原地址，后续验证响应不能再次跳转', async () => {
  const h = harness({search: '?q=a%26b', hash: '#note'}); await boot(h);
  h.pageshow(); const late = h.take('/api/me');
  h.expire(); h.reply(late, identity('B')); await flush();
  assertRedirect(h, '/?q=a%26b#note', 'token_expired');
  assert.equal(h.elements.get('zh-account').hidden, true);
});

test('一体化已经过期的身份不能短暂显示为已登录', async () => {
  const h = harness(); await boot(h, identity('A', Date.now() - 1000));
  assertRedirect(h, '/', 'token_expired');
  assert.equal(h.timers.size, 0);
});

for (const scenario of ['network', 'malformed', 'server-error']) {
  test('一体化核验' + scenario + '不误认为登录失效、不制造重定向循环', async () => {
    const h = harness(), request = h.take('/api/me');
    if (scenario === 'network') request.reject(new Error('offline'));
    else h.reply(request, {authenticated: true}, scenario === 'server-error' ? 503 : 200);
    await flush();
    assert.deepEqual(h.navigation, []);
    assert.equal(h.elements.get('zh-account-out').disabled, true);
    assert.match(h.elements.get('zh-account-name').textContent, /无法核验/);
  });
}

test('一体化退出使用当前 CSRF，重复点击只发一次并保留 returnTo', async () => {
  const h = harness({pathname: '/index.html', search: '?view=graph', hash: '#note'}); await boot(h);
  const pending = h.logout(), request = h.take('/auth/logout');
  await h.logout();
  assert.equal(h.requests.filter(r => r.url === '/auth/logout').length, 1);
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-fixture-A');
  h.reply(request, {logged_out: true}); await pending;
  assertRedirect(h, '/index.html?view=graph#note');
  assert.equal(h.timers.size, 0);
});

test('一体化退出时服务端会话已失效也正确返回登录页', async () => {
  const h = harness(); await boot(h);
  const pending = h.logout(); h.reply(h.take('/auth/logout'), {}, 401); await pending;
  assertRedirect(h, '/', 'session_expired');
});

for (const scenario of ['network', 'csrf']) {
  test('一体化退出' + scenario + '失败不能声称成功，允许重试', async () => {
    const h = harness(); await boot(h);
    const pending = h.logout(), request = h.take('/auth/logout');
    if (scenario === 'network') request.reject(new Error('offline'));
    else h.reply(request, {error: {code: 'csrf_rejected'}}, 403);
    await pending;
    assert.deepEqual(h.navigation, []);
    assert.match(h.elements.get('zh-account-name').textContent, /退出未完成/);
    assert.equal(h.elements.get('zh-account-out').disabled, false);
    const retry = h.logout(); h.reply(h.take('/auth/logout'), {logged_out: true}); await retry;
    assertRedirect(h, '/');
  });
}

test('一体化隐藏页面使旧身份响应失效，返回后核验最新身份', async () => {
  const h = harness(), old = h.take('/api/me');
  h.hidden(); h.reply(old, identity('old')); await flush();
  assert.equal(h.elements.get('zh-account').hidden, true);
  assert.equal(h.timers.size, 0);
  h.visible(); const fresh = identity('new'); fresh.user.name = '新模拟账号';
  h.reply(h.take('/api/me'), fresh); await flush();
  assert.equal(h.elements.get('zh-account-name').textContent, '知乎 · 新模拟账号');
  assert.equal(h.elements.get('zh-account').hidden, false);
});

test('一体化 BFCache 返回时复核，退出成功后迟到响应不得恢复账号条', async () => {
  const h = harness(); await boot(h);
  h.pagehide(); assert.equal(h.elements.get('zh-account').hidden, true);
  h.pageshow(); const old = h.take('/api/me');
  const pending = h.logout(); h.reply(h.take('/auth/logout'), {logged_out: true}); await pending;
  h.reply(old, identity('late')); await flush();
  assertRedirect(h, '/');
  assert.equal(h.elements.get('zh-account').hidden, true);
});
