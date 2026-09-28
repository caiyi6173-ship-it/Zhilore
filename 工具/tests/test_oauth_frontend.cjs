// Synthetic API responses only. No real Zhihu accounts, credentials, or network.
// Run: node --test 工具/tests/test_oauth_frontend.cjs
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const source = readFileSync(path.join(root, '网页/oauth/oauth.js'), 'utf8');
const redirectSource = readFileSync(path.join(root, '网页/oauth/login-redirect.js'), 'utf8');
const html = readFileSync(path.join(root, '网页/oauth/index.html'), 'utf8');
const oauthCss = readFileSync(path.join(root, '网页/oauth/oauth.css'), 'utf8');
const messages = Object.fromEntries([
  'login_required', 'session_expired', 'token_expired', 'authorization_failed',
  'permission_denied', 'rate_limited', 'quota_exceeded', 'upstream_unavailable',
  'upstream_protocol_error', 'authorization_cancelled', 'state_missing',
  'csrf_rejected', 'configuration_required', 'collections_configuration_required', 'collection_unavailable', 'invalid_collection_request', 'server_busy',
  'workspace_limit_reached', 'workspace_unavailable',
].map(code => [code, 'safe-message:' + code]));
const account = (id = 'fixture-A', expires = Date.now() + 60000) => ({
  authenticated: true, user: {id, name: '相同昵称', provider: 'zhihu'},
  expires_at: new Date(expires).toISOString(), csrf_token: 'fixture-csrf-' + id,
});
const collections = (id = 'fixture-A', itemId = '101') => ({
  user: {id}, items: [{id: itemId, title: '模拟收藏 ' + id, description: '仅用于测试', is_public: true}],
});
const status = (ready = true) => ({messages, configuration: {
  ready, callback_uri: 'http://127.0.0.1:8100/auth/zhihu/callback',
  missing: ready ? [] : ['ZHIHU_OAUTH_APP_ID'], invalid: [],
  collections: {ready: true, missing: [], invalid: []},
}});
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(search = '', {pathname = '/', hash = '', defaultReturnTo = '/workspace/'} = {}) {
  const requests = [], elements = new Map(), timers = new Map(), navigation = [], history = [];
  const documentEvents = new Map(), windowEvents = new Map();
  let timerId = 0;
  const element = (tag = 'div') => {
    const el = {
      tag, children: [], text: '', hidden: false, disabled: false, dataset: {}, events: new Map(), attributes: {},
      get textContent() { return this.text + this.children.map(child => child.textContent || '').join(''); },
      set textContent(value) { this.text = String(value ?? ''); this.children = []; },
      set innerHTML(_value) { throw new Error('OAuth UI must not insert raw HTML'); },
      append(...children) { for (const child of children) this.children.push(...(child.tag === 'fragment' ? child.children : [child])); },
      replaceChildren(...children) { this.text = ''; this.children = []; this.append(...children); },
      addEventListener(name, fn) { this.events.set(name, fn); },
      setAttribute(name, value) { this.attributes[name] = value; },
      focus() { document.activeElement = this; },
      async click() { if (!this.disabled && !this.hidden) await this.events.get('click')?.(); },
    };
    return el;
  };
  for (const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    const el = element(); el.hidden = /\bhidden\b/.test(match[0]); el.disabled = /\bdisabled\b/.test(match[0]);
    elements.set(match[1], el);
  }
  const document = {
    visibilityState: 'visible', body: {dataset: {defaultReturnTo}},
    getElementById(id) { assert.ok(elements.has(id), 'DOM id must exist: ' + id); return elements.get(id); },
    createElement: element, createDocumentFragment: () => element('fragment'),
    addEventListener: (event, fn) => documentEvents.set(event, fn),
  };
  const loginPage = ['/login', '/login/'].includes(pathname) || new URLSearchParams(search).has('returnTo');
  const location = {pathname, search, hash, assign: url => navigation.push(url), replace: url => navigation.push(url)};
  const context = vm.createContext({
    document, URL, URLSearchParams, AbortController, location,
    history: {replaceState: (...args) => {
      history.push(args);
      const url = new URL(args[2], 'https://fixture.invalid');
      Object.assign(location, {pathname: url.pathname, search: url.search, hash: url.hash});
    }},
    window: {addEventListener: (event, fn) => windowEvents.set(event, fn)},
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    // An aborted transport may still finish. Keep promises alive to test the epoch guard.
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject, answered: false})),
  });
  vm.runInContext(redirectSource, context, {filename: 'login-redirect.js'});
  vm.runInContext(source, context, {filename: 'oauth.js'});
  const get = id => elements.get(id);
  const take = url => {
    const request = requests.find(item => item.url === url && !item.answered);
    assert.ok(request, 'Expected request: ' + url); request.answered = true; return request;
  };
  const reply = (request, body, code = 200) => request.resolve({ok: code >= 200 && code < 300, json: async () => body});
  return {requests, get, take, reply, document, documentEvents, windowEvents, timers, navigation, history, location, loginPage};
}

async function boot(h, identity = null, ready = true, list = collections(identity?.user.id)) {
  h.reply(h.take('/api/status'), status(ready)); await flush();
  h.reply(h.take('/api/me'), identity || {error: {code: 'login_required'}}, identity ? 200 : 401); await flush();
  if (identity && !h.loginPage) { h.reply(h.take('/api/collections'), list); await flush(); }
}

async function refresh(h, identity, list = collections(identity.user.id)) {
  const pending = h.get('refresh-session').click();
  await boot(h, identity, true, list); await pending;
}

test('未配置时禁止发起授权，不请求共享 Demo 或收藏夹', async () => {
  const h = harness(); await boot(h, null, false);
  assert.equal(h.get('login').disabled, true);
  assert.equal(h.get('collection-list').hidden, true);
  // 配置面板撤掉后，未配置这件事仍要有可见反馈：徽标写"待配置"。
  assert.equal(h.get('connection-badge').textContent, '待配置');
  assert.deepEqual(h.requests.map(r => r.url), ['/api/status', '/api/me']);
});

test('入口页只留账号与收藏：服务端配置、双账号验收、设置入口都不再出现', () => {
  // 这三块要么是维护者自查用的（配置面板），要么属于「知乎收藏」页（设置入口）。
  // 配置状态本身仍要生效，但只作为内部状态，不落到入口页 DOM 上。
  assert.doesNotMatch(html, /id="configuration"/);
  assert.doesNotMatch(html, /class="verification-note"/);
  assert.doesNotMatch(html, /class="privacy-copy"/);
  assert.doesNotMatch(html, /href="\/settings"/);
  assert.doesNotMatch(source, /config-fields|callback-uri|config-badge/);
});

test('入口页底部保留作者手记：手写体 + 蓝色文字', () => {
  // 这一段是用户明确要求加的（作者开发感想），位置固定在页面最底下，纯展示。
  assert.match(html, /class="reflection"/);
  assert.match(html, /人外有人，我也会一直在路上/);
  assert.match(html, /「想法记录」「想法开发」/);
  // 「手写」是这条需求的硬指标：字体栈必须落在楷体/手写体系，
  // 不能只写 var(--sans)（那样会渲染成普通无衬线，等于没做）。
  assert.match(oauthCss, /\.reflection-body\s*\{[^}]*KaiTi/);
  assert.match(oauthCss, /\.reflection\s*\{[^}]*border-radius/);
  // 文字颜色是蓝色（用户明确要求）：用站点自己的品牌蓝变量，别再回到暖褐色。
  assert.match(oauthCss, /\.reflection-body\s*\{[^}]*color:\s*var\(--blue\)/);
  assert.doesNotMatch(oauthCss, /\.reflection-body\s*\{[^}]*#3d3527/);
});

test('配置完成但未登录时只开放授权入口', async () => {
  const h = harness(); await boot(h);
  assert.equal(h.get('login').disabled, false);
  assert.equal(h.get('logout').hidden, true);
  assert.equal(h.get('refresh-collections').disabled, true);
});

test('returnTo：合法站内路径保留在地址栏，并随授权请求一起发出', async () => {
  const h = harness('?returnTo=%2Fworkspace%2F');
  await boot(h);
  assert.deepEqual(h.history[0], [null, '', '/login?returnTo=%2Fworkspace%2F']);
  assert.equal(h.location.pathname, '/login');
  const pending = h.get('login').click();
  await flush();
  const start = h.take('/auth/zhihu/start');
  assert.deepEqual(JSON.parse(start.options.body), {return_to: '/workspace/'});
  h.reply(start, {authorization_url: 'https://openapi.zhihu.com/authorize?state=fixture'}); await pending;
});

test('returnTo：带查询串的中文路径同样只按站内路径处理', async () => {
  const h = harness('?returnTo=' + encodeURIComponent('/搜索?q=rag'));
  await boot(h);
  assert.deepEqual(h.history[0], [null, '', '/login?' + new URLSearchParams({returnTo: '/搜索?q=rag'})]);
  const pending = h.get('login').click();
  await flush();
  const start = h.take('/auth/zhihu/start');
  assert.deepEqual(JSON.parse(start.options.body), {return_to: '/搜索?q=rag'});
  h.reply(start, {authorization_url: 'https://openapi.zhihu.com/authorize?state=fixture'}); await pending;
});

test('returnTo：站外、协议相对、上跳、反斜杠、控制字符、超长一律丢弃', async () => {
  const bad = ['//evil.example/x', 'https://evil.example/x', 'javascript:alert(1)',
               '/a/../../etc', '/a\\b', '/' + 'x'.repeat(600), 'evi\nl'];
  for (const raw of bad) {
    const h = harness('?returnTo=' + encodeURIComponent(raw));
    await boot(h);
    assert.deepEqual(h.history[0], [null, '', '/login?returnTo=%2Fworkspace%2F'], 'must fall back for ' + JSON.stringify(raw));
    const pending = h.get('login').click();
    await flush();
    const start = h.take('/auth/zhihu/start');
    assert.deepEqual(JSON.parse(start.options.body), {return_to: '/workspace/'}, 'must not forward ' + JSON.stringify(raw));
    h.reply(start, {authorization_url: 'https://openapi.zhihu.com/authorize?state=fixture'}); await pending;
  }
});

test('returnTo：没有该参数时行为与过去一致', async () => {
  const h = harness('?result=connected');
  await boot(h);
  assert.deepEqual(h.history[0], [null, '', '/']);
  const pending = h.get('login').click();
  await flush();
  const start = h.take('/auth/zhihu/start');
  assert.deepEqual(JSON.parse(start.options.body), {});
  h.reply(start, {authorization_url: 'https://openapi.zhihu.com/authorize?state=fixture'}); await pending;
});

test('两个独立 UI 会话使用稳定 ID，而不是昵称区分账号', async () => {
  const a = harness(), b = harness();
  await boot(a, account('fixture-A'), true, collections('fixture-A', '101'));
  await boot(b, account('fixture-B'), true, collections('fixture-B', '202'));
  assert.equal(a.get('account-name').textContent, b.get('account-name').textContent);
  assert.match(a.get('account-id').textContent, /fixture-A/);
  assert.match(b.get('account-id').textContent, /fixture-B/);
  assert.match(a.get('collection-list').textContent, /101/);
  assert.doesNotMatch(a.get('collection-list').textContent, /fixture-B/);
  assert.doesNotMatch(b.get('collection-list').textContent, /fixture-A/);
});

test('服务端返回另一个用户的收藏夹时立即清空身份和列表', async () => {
  const h = harness(); await boot(h, account(), true, collections('fixture-B'));
  assert.equal(h.get('account-name').textContent, '尚未连接');
  assert.equal(h.get('collection-list').hidden, true);
  assert.match(h.get('notice').textContent, /session_expired/);
});

test('收藏夹标题和描述作为文本，链接只由合法 ID 构造', async () => {
  const h = harness(), list = collections();
  list.items[0].title = '<img src=x onerror=alert(1)>';
  list.items[0].description = '<script>bad()</script>';
  list.items[0].url = 'javascript:alert(1)';
  await boot(h, account(), true, list);
  assert.match(h.get('collection-list').textContent, /<img src=x/);
  const link = h.get('collection-list').children[0].children[1].children[0].children[0];
  assert.equal(link.href, 'https://www.zhihu.com/collection/101');
  assert.equal(link.rel, 'noopener noreferrer');
});

test('非法收藏夹 ID 不生成链接，不显示部分列表', async () => {
  const h = harness(); await boot(h, account(), true, collections('fixture-A', '../private'));
  assert.equal(h.get('collection-list').hidden, true);
  assert.match(h.get('notice').textContent, /upstream_protocol_error/);
});

test('空列表说明接口未返回数据，而不是断言用户没有收藏夹', async () => {
  const h = harness(); await boot(h, account(), true, {user: {id: 'fixture-A'}, items: []});
  assert.equal(h.get('collection-count').textContent, '0');
  assert.match(h.get('collection-state-copy').textContent, /不等于/);
});

for (const code of ['permission_denied', 'rate_limited', 'quota_exceeded', 'upstream_unavailable']) {
  test('接口错误 ' + code + ' 清空旧列表但保留身份，不自动重试或切换账号', async () => {
    const h = harness(); await boot(h, account());
    const pending = h.get('refresh-collections').click();
    h.reply(h.take('/api/collections'), {error: {code}}, code === 'permission_denied' ? 403 : 429);
    await flush(); await pending;
    assert.match(h.get('account-id').textContent, /fixture-A/);
    assert.equal(h.get('collection-list').hidden, true);
    assert.equal(h.get('notice').textContent, messages[code]);
    assert.equal(h.requests.filter(r => r.url === '/api/collections').length, 2);
    assert.equal(h.get('refresh-collections').disabled, false);
  });
}

for (const code of ['token_expired', 'authorization_failed']) {
  test(code + ' 清空账号数据并要求重新授权', async () => {
    const h = harness(); await boot(h, account());
    h.get('refresh-collections').click();
    h.reply(h.take('/api/collections'), {error: {code}}, 401); await flush();
    assert.equal(h.get('account-name').textContent, '尚未连接');
    assert.equal(h.get('collection-list').hidden, true);
    assert.equal(h.get('login').disabled, false);
    assert.equal(h.get('notice').textContent, messages[code]);
  });
}

for (const code of ['authorization_cancelled', 'state_missing']) {
  test('安全回调结果 ' + code + ' 可见，且查询串立即清除', async () => {
    const h = harness('?result=' + code); await boot(h);
    assert.equal(h.get('notice').textContent, messages[code]);
    assert.equal(h.history[0][2], '/');
    assert.equal(h.get('account-name').textContent, '尚未连接');
  });
}

test('未知回调结果不能插入任意文本', async () => {
  const h = harness('?result=%3Cscript%3Ebad%3C/script%3E'); await boot(h);
  assert.doesNotMatch(h.get('notice').textContent, /script/);
  assert.equal(h.history[0][2], '/');
});

test('授权从同源 POST 发起，仅导航到固定知乎授权端点', async () => {
  const h = harness(); await boot(h);
  const pending = h.get('login').click(), request = h.take('/auth/zhihu/start');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  h.reply(request, {authorization_url: 'https://openapi.zhihu.com/authorize?state=fixture'}); await pending;
  assert.equal(h.navigation.length, 1);
});

test('拒绝跳转到非知乎授权地址', async () => {
  const h = harness(); await boot(h);
  const pending = h.get('login').click();
  h.reply(h.take('/auth/zhihu/start'), {authorization_url: 'https://example.invalid/authorize'}); await pending;
  assert.equal(h.navigation.length, 0);
  assert.match(h.get('notice').textContent, /upstream_protocol_error/);
});

test('慢速收藏请求期间可以退出，晚到响应不能重新显示旧账号', async () => {
  const h = harness(); await boot(h, account());
  h.get('refresh-collections').click(); const stale = h.take('/api/collections');
  assert.equal(h.get('logout').disabled, false);
  const pending = h.get('logout').click(), logout = h.take('/auth/logout');
  assert.equal(stale.options.signal.aborted, true);
  assert.equal(logout.options.headers['X-CSRF-Token'], 'fixture-csrf-fixture-A');
  assert.equal(h.get('collection-list').hidden, true);
  h.reply(logout, {logged_out: true, remote_authorization_revoked: false}); await pending;
  h.reply(stale, collections()); await flush();
  assert.equal(h.get('account-name').textContent, '尚未连接');
  assert.equal(h.get('collection-list').hidden, true);
  assert.match(h.get('notice').textContent, /不等于在知乎端撤销/);
});

test('退出失败不显示成功，也不恢复旧收藏数据', async () => {
  const h = harness(); await boot(h, account());
  const pending = h.get('logout').click(); h.reply(h.take('/auth/logout'), {error: {code: 'csrf_rejected'}}, 403); await pending;
  assert.equal(h.get('collection-list').hidden, true);
  assert.equal(h.get('notice').textContent, messages.csrf_rejected);
});

test('切换到后台立即清屏，恢复时必须重新核验会话', async () => {
  const h = harness(); await boot(h, account());
  h.get('refresh-collections').click(); const stale = h.take('/api/collections');
  h.document.visibilityState = 'hidden'; h.documentEvents.get('visibilitychange')();
  assert.equal(h.get('collection-list').hidden, true);
  assert.equal(stale.options.signal.aborted, true);
  h.reply(stale, collections()); await flush();
  h.document.visibilityState = 'visible'; h.documentEvents.get('visibilitychange')();
  await boot(h, account('fixture-B'), true, collections('fixture-B', '202'));
  assert.match(h.get('account-id').textContent, /fixture-B/);
  assert.doesNotMatch(h.get('collection-list').textContent, /fixture-A/);
});

test('旧身份请求晚到不能覆盖较新的身份', async () => {
  const h = harness(); h.reply(h.take('/api/status'), status()); await flush();
  const stale = h.take('/api/me');
  h.windowEvents.get('pageshow')({persisted: true});
  await boot(h, account('fixture-B'), true, collections('fixture-B', '202'));
  h.reply(stale, account('fixture-A')); await flush();
  assert.equal(stale.options.signal.aborted, true);
  assert.match(h.get('account-id').textContent, /fixture-B/);
  assert.equal(h.requests.filter(r => r.url === '/api/collections').length, 1);
});

test('有效期定时器先清屏再向服务端核验，不延用已过期数据', async () => {
  const h = harness(); await boot(h, account());
  assert.equal(h.timers.size, 1);
  [...h.timers.values()][0]();
  assert.equal(h.get('collection-list').hidden, true);
  h.reply(h.take('/api/status'), status()); await flush();
  h.reply(h.take('/api/me'), {error: {code: 'token_expired'}}, 401); await flush();
  assert.equal(h.get('account-name').textContent, '尚未连接');
  assert.equal(h.timers.size, 0);
  assert.equal(h.get('notice').textContent, messages.token_expired);
});

test('刷新到不同账号只显示新账号收藏，CSRF 随身份更新', async () => {
  const h = harness(); await boot(h, account());
  await refresh(h, account('fixture-B'), collections('fixture-B', '202'));
  assert.match(h.get('collection-list').textContent, /fixture-B/);
  assert.doesNotMatch(h.get('collection-list').textContent, /fixture-A/);
  const pending = h.get('logout').click(), request = h.take('/auth/logout');
  assert.equal(request.options.headers['X-CSRF-Token'], 'fixture-csrf-fixture-B');
  h.reply(request, {logged_out: true}); await pending;
});


// Public-content reader: no production mock login entry point is added.
const readButton = (h, index = 0) => h.get('collection-list').children[index].children[2].children[1];
const contentItem = (id = '1', fields = {}) => ({
  id: 'answer:' + id, type: 'answer', url: 'https://www.zhihu.com/answer/' + id,
  title: '模拟内容 ' + id, summary: '仅用于测试的摘要 ' + id, author: '模拟作者',
  created_at: 1745486539, collected_at: 1746000000,
  like_count: '128', comment_count: '12', favorite_count: '20', ...fields,
});
const contentPage = ({user = 'fixture-A', collection = '101', items = [contentItem()], offset = '0', next = null, total = '1'} = {}) => ({
  user: {id: user}, collection: {id: collection, is_public: true}, items,
  paging: {offset, limit: 20, has_more: next !== null, next_offset: next, total}, scope: 'public',
});
const contentUrl = (collection = '101', offset = '0') => '/api/collections/' + collection + '/contents?offset=' + offset;
const contentRequests = h => h.requests.filter(request => request.url.includes('/contents?'));
async function openContents(h, data = contentPage(), index = 0) {
  const pending = readButton(h, index).click();
  h.reply(h.take(contentUrl(data.collection.id)), data);
  await pending;
}

function twoCollections() {
  const list = collections();
  list.items.push({id: '303', title: '另一个模拟夹', description: '', is_public: true});
  return list;
}

test('只呈现明确公开的收藏夹，私密名称不进入 DOM', async () => {
  const h = harness(), list = collections();
  list.items.push({is_public: false, title: 'PRIVATE-MUST-NOT-LEAK'});
  await boot(h, account(), true, list);
  assert.equal(h.get('collection-count').textContent, '1');
  assert.doesNotMatch(h.get('collection-list').textContent, /PRIVATE/);
  assert.equal(contentRequests(h).length, 0);
});

test('可见性未知时不显示任何部分列表', async () => {
  const h = harness(), list = collections();
  delete list.items[0].is_public;
  await boot(h, account(), true, list);
  assert.equal(h.get('collection-list').hidden, true);
  assert.match(h.get('notice').textContent, /upstream_protocol_error/);
});

test('50 个收藏夹提示返回上限，超过 50 则拒绝异常响应', async () => {
  const list = collections();
  list.items = Array.from({length: 50}, (_, i) => ({...list.items[0], id: String(i + 1)}));
  const h = harness(); await boot(h, account(), true, list);
  assert.equal(h.get('collection-count').textContent, '50');
  assert.match(h.get('scope-note').textContent, /达到 50 个返回上限/);
  const bad = harness(); list.items.push({...list.items[0], id: '51'});
  await boot(bad, account(), true, list);
  assert.equal(bad.get('collection-list').hidden, true);
});

test('点击后按需请求摘要，显示作者、计数和原文外链', async () => {
  const h = harness(); await boot(h, account());
  assert.equal(contentRequests(h).length, 0);
  await openContents(h);
  assert.equal(h.get('collection-reader').hidden, false);
  assert.equal(h.get('content-list').children.length, 1);
  assert.match(h.get('content-list').textContent, /模拟作者/);
  assert.match(h.get('content-list').textContent, /赞同 128/);
  assert.equal(h.get('load-more').hidden, true);
  assert.equal(h.document.activeElement, h.get('reader-title'));
  assert.equal(readButton(h).attributes['aria-pressed'], 'true');
  const request = contentRequests(h)[0];
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.cache, 'no-store');
  const link = h.get('content-list').children[0].children[1].children[0];
  assert.equal(link.href, 'https://www.zhihu.com/answer/1');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.equal(link.target, '_blank');
});

test('两套 UI 的摘要绑定各自稳定 ID，不因为同名而互换', async () => {
  const a = harness(), b = harness();
  await boot(a, account()); await boot(b, account('fixture-B'), true, collections('fixture-B', '202'));
  await openContents(a, contentPage({items: [contentItem('11')]}));
  await openContents(b, contentPage({user: 'fixture-B', collection: '202', items: [contentItem('22')]}));
  assert.match(a.get('content-list').textContent, /模拟内容 11/);
  assert.doesNotMatch(a.get('content-list').textContent, /模拟内容 22/);
  assert.match(b.get('content-list').textContent, /模拟内容 22/);
});

test('分页保留超过 JS 安全整数的游标和总数，跨页按类型与 ID 去重', async () => {
  const h = harness(); await boot(h, account());
  const next = '09007199254740993', total = '9223372036854775807';
  await openContents(h, contentPage({items: [contentItem('1', {like_count: total})], next, total}));
  assert.equal(contentRequests(h).length, 1);
  assert.match(h.get('content-list').textContent, /9,223,372,036,854,775,807/);
  const pending = h.get('load-more').click();
  assert.equal(h.get('load-more').disabled, true);
  await h.get('load-more').click();
  assert.equal(contentRequests(h).length, 2);
  h.reply(h.take(contentUrl('101', next)), contentPage({offset: next, total, items: [contentItem('1', {title: '重复不替换'}), contentItem('2')]}));
  await pending;
  assert.equal(h.get('content-list').children.length, 2);
  assert.doesNotMatch(h.get('content-list').textContent, /重复不替换/);
  assert.match(h.get('reader-progress').textContent, /已展示 2 条.*已到接口最后一页/);
  assert.equal(h.get('load-more').hidden, true);
});

test('空但未结束的页不自动循环请求，可手动继续读取', async () => {
  const h = harness(); await boot(h, account());
  await openContents(h, contentPage({items: [], next: '27', total: '3'}));
  assert.match(h.get('reader-state-title').textContent, /本页没有可见条目/);
  assert.equal(h.get('load-more').hidden, false);
  assert.equal(contentRequests(h).length, 1);
  const pending = h.get('load-more').click();
  h.reply(h.take(contentUrl('101', '27')), contentPage({offset: '27', items: [], total: '3'}));
  await pending;
  assert.match(h.get('reader-state-copy').textContent, /不等于/);
  assert.equal(h.get('load-more').hidden, true);
});

for (const code of ['rate_limited', 'quota_exceeded', 'upstream_unavailable', 'server_busy']) {
  test('摘要分页 ' + code + ' 保留已加载条目，重试同一游标而不跳页', async () => {
    const h = harness(); await boot(h, account());
    await openContents(h, contentPage({next: '00027', total: '2'}));
    const pending = h.get('load-more').click();
    h.reply(h.take(contentUrl('101', '00027')), {error: {code, message: 'RAW-SECRET'}}, 429); await pending;
    assert.equal(h.get('content-list').children.length, 1);
    assert.equal(h.get('reader-feedback').textContent, messages[code]);
    assert.equal(h.get('retry-content').hidden, false);
    assert.equal(h.get('load-more').hidden, true);
    assert.equal(contentRequests(h).length, 2);
    const retry = h.get('retry-content').click();
    h.reply(h.take(contentUrl('101', '00027')), contentPage({offset: '00027', items: [contentItem('2')], total: '2'})); await retry;
    assert.equal(h.get('content-list').children.length, 2);
    assert.equal(h.get('reader-feedback').hidden, true);
  });
}

for (const code of ['permission_denied', 'collection_unavailable', 'upstream_protocol_error']) {
  test('摘要分页 ' + code + ' 清掉旧摘要，再次尝试从第一页开始而不跳页', async () => {
    const h = harness(); await boot(h, account());
    await openContents(h, contentPage({next: '27', total: '2'}));
    const pending = h.get('load-more').click();
    h.reply(h.take(contentUrl('101', '27')), {error: {code}}, 403); await pending;
    assert.equal(h.get('content-list').children.length, 0);
    assert.equal(h.get('content-list').hidden, true);
    assert.match(h.get('account-id').textContent, /fixture-A/);
    assert.equal(h.get('reader-feedback').textContent, messages[code]);
    const retry = h.get('retry-content').click();
    h.reply(h.take(contentUrl('101', '0')), contentPage()); await retry;
    assert.equal(h.get('content-list').children.length, 1);
  });
}

test('网络异常不渲染原始错误内容，也不丢失已读取的摘要', async () => {
  const h = harness(); await boot(h, account());
  await openContents(h, contentPage({next: '27'}));
  const pending = h.get('load-more').click();
  h.take(contentUrl('101', '27')).reject(new Error('secret transport details')); await pending;
  assert.equal(h.get('reader-feedback').textContent, messages.upstream_unavailable);
  assert.equal(h.get('content-list').children.length, 1);
});

for (const code of ['token_expired', 'authorization_failed', 'session_expired']) {
  test('读取摘要时 ' + code + ' 清除身份、列表和阅读面板', async () => {
    const h = harness(); await boot(h, account());
    const pending = readButton(h).click();
    h.reply(h.take(contentUrl()), {error: {code}}, 401); await pending;
    assert.equal(h.get('account-name').textContent, '尚未连接');
    assert.equal(h.get('collection-list').hidden, true);
    assert.equal(h.get('collection-reader').hidden, true);
    assert.equal(h.get('content-list').textContent, '');
    assert.equal(h.get('notice').textContent, messages[code]);
  });
}

test('内容响应属于其他账号时清除全部数据，而不是当成空夹子', async () => {
  const h = harness(); await boot(h, account());
  await openContents(h, contentPage({user: 'fixture-B'}));
  assert.equal(h.get('collection-reader').hidden, true);
  assert.equal(h.get('collection-list').hidden, true);
  assert.equal(h.get('notice').textContent, messages.session_expired);
});

test('异常分页、收藏夹、条目字段被整体拒绝，不显示部分响应', async () => {
  const changes = [
    page => { page.collection.id = '202'; },
    page => { page.collection.is_public = false; },
    page => { page.paging.offset = '20'; },
    page => { page.paging.limit = 21; },
    page => { page.paging.total = 9007199254740992; },
    page => { page.paging.total = '9223372036854775808'; },
    page => { page.paging.has_more = 'true'; },
    page => { page.paging.has_more = true; page.paging.next_offset = '0'; },
    page => { page.paging.has_more = true; page.paging.next_offset = 20; },
    page => { page.paging.has_more = true; page.paging.next_offset = '9223372036854775808'; },
    page => { page.paging.next_offset = '20'; },
    page => { page.items.push({...contentItem('2'), like_count: 9007199254740992}); },
    page => { page.items.push({...contentItem('2'), collected_at: -1}); },
    page => { page.items = Array.from({length: 21}, (_, i) => contentItem(String(i + 1))); },
  ];
  for (const change of changes) {
    const h = harness(); await boot(h, account()); const page = contentPage(); change(page);
    const pending = readButton(h).click();
    h.reply(h.take(contentUrl()), page); await pending;
    assert.equal(h.get('content-list').children.length, 0);
    assert.equal(h.get('content-list').hidden, true);
    assert.equal(h.get('reader-feedback').hidden, false);
  }
});

test('标题、摘要和作者中的 HTML 只作为文本，移除链接追踪参数', async () => {
  const h = harness(); await boot(h, account());
  await openContents(h, contentPage({items: [contentItem('1', {title: '<img src=x onerror=alert(1)>',
    summary: '<script>bad()</script>', author: '<b>作者</b>', url: 'https://www.zhihu.com/answer/1?track=yes#part'})]}));
  assert.match(h.get('content-list').textContent, /<script>bad\(\)<\/script>/);
  const link = h.get('content-list').children[0].children[1].children[0];
  assert.equal(link.href, 'https://www.zhihu.com/answer/1');
});

test('不允许把内容原文链接指向外部站点、脚本、凭证 URL 或其他路径', async () => {
  for (const url of ['https://evil.test/answer/1', 'javascript:alert(1)', 'http://www.zhihu.com/answer/1',
    'https://user:pass@www.zhihu.com/answer/1', 'https://www.zhihu.com:444/answer/1',
    'https://www.zhihu.com/collection/1', 'https://www.zhihu.com/answer/1\\evil',
    'https://www.zhihu.com/answer/1\n']) {
    const h = harness(); await boot(h, account());
    await openContents(h, contentPage({items: [contentItem('1', {url})]}));
    assert.equal(h.get('content-list').children.length, 0, url);
    assert.equal(h.get('reader-feedback').textContent, messages.upstream_protocol_error);
  }
});

test('关闭阅读面板会中止请求、清掉内容并将焦点归还按钮', async () => {
  const h = harness(); await boot(h, account()); const trigger = readButton(h);
  const pending = trigger.click(), stale = h.take(contentUrl());
  await h.get('close-reader').click();
  assert.equal(stale.options.signal.aborted, true);
  assert.equal(h.document.activeElement, trigger);
  assert.equal(trigger.attributes['aria-pressed'], 'false');
  h.reply(stale, contentPage()); await pending;
  assert.equal(h.get('collection-reader').hidden, true);
  assert.equal(h.get('content-list').textContent, '');
});

test('切换收藏夹后，前一个慢响应不能覆盖新选择', async () => {
  const h = harness(); await boot(h, account(), true, twoCollections());
  const first = readButton(h).click(), stale = h.take(contentUrl());
  await openContents(h, contentPage({collection: '303', items: [contentItem('33')]}), 1);
  assert.equal(stale.options.signal.aborted, true);
  h.reply(stale, contentPage()); await first;
  assert.match(h.get('content-list').textContent, /模拟内容 33/);
  assert.doesNotMatch(h.get('content-list').textContent, /模拟内容 1/);
  assert.equal(readButton(h).attributes['aria-pressed'], 'false');
  assert.equal(readButton(h, 1).attributes['aria-pressed'], 'true');
});

test('慢速摘要期间可立即退出，旧响应即使成功也不能恢复页面', async () => {
  const h = harness(); await boot(h, account());
  const pending = readButton(h).click(), stale = h.take(contentUrl());
  assert.equal(h.get('logout').disabled, false);
  const logout = h.get('logout').click();
  assert.equal(stale.options.signal.aborted, true);
  assert.equal(h.get('collection-reader').hidden, true);
  h.reply(h.take('/auth/logout'), {logged_out: true}); await logout;
  h.reply(stale, contentPage()); await pending;
  assert.equal(h.get('account-name').textContent, '尚未连接');
  assert.equal(h.get('content-list').textContent, '');
});

test('后台清屏后切到另一个账号，旧摘要响应和错误都不能污染新账号', async () => {
  for (const fails of [false, true]) {
    const h = harness(); await boot(h, account());
    const pending = readButton(h).click(), stale = h.take(contentUrl());
    h.document.visibilityState = 'hidden'; h.documentEvents.get('visibilitychange')();
    assert.equal(h.get('content-list').textContent, '');
    h.document.visibilityState = 'visible'; h.documentEvents.get('visibilitychange')();
    await boot(h, account('fixture-B'), true, collections('fixture-B', '202'));
    await openContents(h, contentPage({user: 'fixture-B', collection: '202', items: [contentItem('22')]}));
    h.reply(stale, fails ? {error: {code: 'authorization_failed'}} : contentPage(), fails ? 401 : 200); await pending;
    assert.match(h.get('account-id').textContent, /fixture-B/);
    assert.match(h.get('content-list').textContent, /模拟内容 22/);
  }
});

test('会话到期计时器立即清除摘要，再核验授权而不延用缓存', async () => {
  const h = harness(); await boot(h, account()); await openContents(h);
  [...h.timers.values()][0]();
  assert.equal(h.get('collection-reader').hidden, true);
  assert.equal(h.get('content-list').textContent, '');
  h.reply(h.take('/api/status'), status()); await flush();
  h.reply(h.take('/api/me'), {error: {code: 'token_expired'}}, 401); await flush();
  assert.equal(h.get('notice').textContent, messages.token_expired);
});

test('重新读列表或内容时先清旧数据，内容刷新总从第一页开始', async () => {
  const h = harness(); await boot(h, account());
  await openContents(h, contentPage({next: '27'}));
  const reset = h.get('refresh-content').click();
  assert.equal(h.get('content-list').textContent, '');
  h.reply(h.take(contentUrl()), contentPage({items: [contentItem('9')]})); await reset;
  assert.match(h.get('content-list').textContent, /模拟内容 9/);
  await h.get('refresh-collections').click();
  assert.equal(h.get('collection-reader').hidden, true);
  assert.equal(h.get('content-list').textContent, '');
  h.reply(h.take('/api/collections'), collections()); await flush();
  assert.equal(h.get('collection-reader').hidden, true);
});


const importResult = (id = 'fixture-A', offset = '0') => ({user: {id}, source: 'public_summary', offset,
  imported: {added: 1, updated: 0, total: 1}, workspace_url: '/workspace/'});

test('登录与收藏配置独立：缺 Access Secret 仍显示个人工作区入口', async () => {
  const h = harness(), config = status();
  config.configuration.collections = {ready: false, missing: ['ZHIHU_ACCESS_SECRET'], invalid: []};
  h.reply(h.take('/api/status'), config); await flush();
  h.reply(h.take('/api/me'), account()); await flush();
  assert.equal(h.get('enter-workspace').hidden, false);
  assert.equal(h.get('refresh-collections').disabled, true);
  assert.match(h.get('collection-state-title').textContent, /待配置/);
  assert.equal(h.requests.some(r => r.url === '/api/collections'), false);
});

test('摘要页收录只提交收藏夹 ID 和原样游标，携带当前 CSRF', async () => {
  const h = harness(); await boot(h, account()); await openContents(h);
  assert.equal(h.get('import-page').hidden, false);
  const pending = h.get('import-page').click(), request = h.take('/api/workspace/import');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['X-CSRF-Token'], 'fixture-csrf-fixture-A');
  assert.deepEqual(JSON.parse(request.options.body), {collection_id: '101', offset: '0'});
  assert.equal(h.get('import-page').disabled, true);
  await h.get('import-page').click();
  assert.equal(h.requests.filter(r => r.url === '/api/workspace/import').length, 1);
  h.reply(request, importResult()); await pending;
  assert.match(h.get('import-feedback').textContent, /新增 1 条/);
  assert.match(h.get('import-feedback').textContent, /工作区共有 1 条摘要/);
  assert.equal(h.get('import-page').disabled, false);
});

test('收录最近一页而非全量累计列表，保留前导零游标', async () => {
  const h = harness(); await boot(h, account()); await openContents(h, contentPage({next: '00027', total: '2'}));
  const page = h.get('load-more').click();
  h.reply(h.take(contentUrl('101', '00027')), contentPage({offset: '00027', total: '2', items: [contentItem('2')]})); await page;
  const pending = h.get('import-page').click(), request = h.take('/api/workspace/import');
  assert.equal(JSON.parse(request.options.body).offset, '00027');
  assert.match(h.get('import-page').textContent, /正在收录/);
  h.reply(request, importResult('fixture-A', '00027')); await pending;
  assert.match(h.get('import-page').textContent, /1 条/);
});

test('空页不提供虚假的摘要收录入口', async () => {
  const h = harness(); await boot(h, account()); await openContents(h, contentPage({items: [], total: '0'}));
  assert.equal(h.get('import-page').hidden, true);
});

for (const code of ['permission_denied', 'collection_unavailable']) {
  test('收录时 ' + code + ' 清空旧摘要而非报告收录成功', async () => {
    const h = harness(); await boot(h, account()); await openContents(h);
    const pending = h.get('import-page').click();
    h.reply(h.take('/api/workspace/import'), {error: {code}}, 403); await pending;
    assert.equal(h.get('import-page').hidden, true);
    assert.equal(h.get('content-list').children.length, 0);
    assert.equal(h.get('reader-feedback').textContent, messages[code]);
    assert.equal(h.get('enter-workspace').hidden, false);
  });
}

test('收录遇到限额错误不自动重试且保留已显示的公开摘要', async () => {
  const h = harness(); await boot(h, account()); await openContents(h);
  const pending = h.get('import-page').click();
  h.reply(h.take('/api/workspace/import'), {error: {code: 'workspace_limit_reached'}}, 409); await pending;
  assert.equal(h.get('import-feedback').textContent, messages.workspace_limit_reached);
  assert.equal(h.get('content-list').children.length, 1);
  assert.equal(h.requests.filter(r => r.url === '/api/workspace/import').length, 1);
});

test('关闭阅读面板后晚到的收录结果不能重新出现', async () => {
  const h = harness(); await boot(h, account()); await openContents(h);
  const pending = h.get('import-page').click(), request = h.take('/api/workspace/import');
  await h.get('close-reader').click();
  assert.equal(request.options.signal.aborted, true);
  h.reply(request, importResult()); await pending;
  assert.equal(h.get('import-feedback').hidden, true);
  assert.equal(h.get('import-feedback').textContent, '');
});

test('收录响应属于其他账号时清空全部账号数据', async () => {
  const h = harness(); await boot(h, account()); await openContents(h);
  const pending = h.get('import-page').click();
  h.reply(h.take('/api/workspace/import'), importResult('fixture-B')); await pending;
  assert.equal(h.get('enter-workspace').hidden, true);
  assert.equal(h.get('collection-list').hidden, true);
  assert.match(h.get('notice').textContent, /session_expired/);
});

test('收录期间可立即退出，旧收录响应不恢复身份或成功提示', async () => {
  const h = harness(); await boot(h, account()); await openContents(h);
  const pending = h.get('import-page').click(), request = h.take('/api/workspace/import');
  const logout = h.get('logout').click();
  h.reply(h.take('/auth/logout'), {logged_out: true}); await logout;
  h.reply(request, importResult()); await pending;
  assert.equal(h.get('account-name').textContent, '尚未连接');
  assert.equal(h.get('import-feedback').textContent, '');
});

async function assertStartTarget(h, target) {
  const pending = h.get('login').click();
  await flush();
  const request = h.take('/auth/zhihu/start');
  assert.deepEqual(JSON.parse(request.options.body), {return_to: target});
  h.reply(request, {authorization_url: 'https://openapi.zhihu.com/authorize?state=fixture'});
  await pending;
  assert.equal(h.navigation.at(-1), 'https://openapi.zhihu.com/authorize?state=fixture');
}

test('returnTo：登录页刷新仍保留查询串和片段，不提前冒充工作区 URL', async () => {
  const target = '/workspace/?q=a%26b&view=graph#note-2';
  const first = harness('?' + new URLSearchParams({returnTo: target}), {pathname: '/login'});
  await boot(first);
  assert.equal(first.location.pathname, '/login');
  assert.equal(new URLSearchParams(first.location.search).get('returnTo'), target);
  assert.deepEqual(first.navigation, []);
  const reloaded = harness(first.location.search, first.location);
  await boot(reloaded);
  await assertStartTarget(reloaded, target);
});

for (const [target, hash, expected] of [
  ['/workspace/?q=rag', '#note-2', '/workspace/?q=rag#note-2'],
  ['/workspace/#saved', '#inherited', '/workspace/#saved'],
  ['/', '#graph', '/#graph'],
]) {
  test('returnTo：接回服务器不可见的片段，且显式目标片段优先 ' + expected, async () => {
    const h = harness('?' + new URLSearchParams({returnTo: target}), {pathname: '/login', hash});
    await boot(h);
    assert.equal(h.location.hash, '');
    assert.equal(new URLSearchParams(h.location.search).get('returnTo'), expected);
    await assertStartTarget(h, expected);
  });
}

for (const defaultReturnTo of ['/workspace/', '/']) {
  test('returnTo：登录页无参数时使用对应入口默认目标 ' + defaultReturnTo, async () => {
    const h = harness('', {pathname: '/login/', defaultReturnTo});
    await boot(h);
    assert.equal(h.location.pathname, '/login');
    assert.equal(new URLSearchParams(h.location.search).get('returnTo'), defaultReturnTo);
    await assertStartTarget(h, defaultReturnTo);
  });
}

test('returnTo：根路径是有效目标，不被个人工作区默认值替换', async () => {
  const h = harness('?returnTo=%2F', {pathname: '/login'});
  await boot(h);
  await assertStartTarget(h, '/');
});

test('returnTo：重复参数回落默认值，不选择攻击者提供的其中一个', async () => {
  const h = harness('?returnTo=%2Fworkspace%2F&returnTo=%2Fother', {pathname: '/login', defaultReturnTo: '/'});
  await boot(h);
  assert.equal(new URLSearchParams(h.location.search).getAll('returnTo').length, 1);
  await assertStartTarget(h, '/');
});

test('returnTo：非法目标和非法页面默认值不能触发站外跳转', async () => {
  const h = harness('?returnTo=' + encodeURIComponent('//outside.invalid'), {
    pathname: '/login', defaultReturnTo: 'https://outside.invalid',
  });
  await boot(h);
  await assertStartTarget(h, '/workspace/');
});

test('returnTo：已登录用户访问登录页只做身份核验后回跳，不先读取收藏', async () => {
  const target = '/workspace/?view=graph#node-1';
  const h = harness('?' + new URLSearchParams({returnTo: target}), {pathname: '/login'});
  await boot(h, account());
  assert.deepEqual(h.navigation, [target]);
  assert.deepEqual(h.requests.map(request => request.url), ['/api/status', '/api/me']);
});

test('returnTo：普通账号管理首页即使已登录仍留在本页展示收藏', async () => {
  const h = harness(); await boot(h, account());
  assert.deepEqual(h.navigation, []);
  assert.deepEqual(h.history, []);
  assert.equal(h.get('collection-list').hidden, false);
});

for (const result of ['authorization_cancelled', 'state_missing', 'token_expired', 'permission_denied']) {
  test('returnTo：失败原因显示为固定提示，清理 URL 后重试仍使用原目标 ' + result, async () => {
    const target = '/workspace/?q=retry#target';
    const h = harness('?' + new URLSearchParams({returnTo: target, result, code: 'must-not-retain'}), {pathname: '/login'});
    await boot(h);
    assert.equal(h.get('notice').textContent, messages[result]);
    assert.deepEqual([...new URLSearchParams(h.location.search).keys()], ['returnTo']);
    await assertStartTarget(h, target);
  });
}

test('returnTo：回到账号管理首页仍保留普通查询编码和收藏区锚点', async () => {
  const query = '?view=collections&q=a%26b&space=a%20b';
  const h = harness(query, {hash: '#collections'}); await boot(h, account());
  assert.equal(h.location.pathname + h.location.search + h.location.hash, '/' + query + '#collections');
  assert.deepEqual(h.history, []);
  assert.deepEqual(h.navigation, []);
  assert.equal(h.get('collection-list').hidden, false);
});

test('账号管理页只移除授权临时参数，不抹掉回跳位置或重编码普通查询', async () => {
  const query = '?result=authorization_cancelled&%63ode=fixture-code&authorization_code=fixture&state=fixture'
    + '&error=fixture&error_description=fixture&error_uri=fixture&access_token=fixture&token_type=fixture&expires_in=1'
    + '&view=collections&q=a%26b&space=a%20b';
  const h = harness(query, {hash: '#collections'}); await boot(h);
  assert.equal(h.location.pathname + h.location.search + h.location.hash,
    '/?view=collections&q=a%26b&space=a%20b#collections');
  assert.equal(h.get('notice').textContent, messages.authorization_cancelled);
  assert.deepEqual(h.navigation, []);
});
