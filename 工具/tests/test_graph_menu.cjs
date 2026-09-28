// node --test 工具/tests/test_graph_menu.cjs
//
// 「知识库图谱」右键删除节点：菜单内容、二次确认、请求形状、失败兜底。
// 只桩 DOM 与网络，不访问真实账号、不联网、不碰任何数据库。
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.join(__dirname, '../..');
const source = readFileSync(path.join(root, '网页/graph.js'), 'utf8');

// ── 刚够菜单用的最小 DOM ────────────────────────────────────────────────────
function 匹配(节点, 选择器) {
  return 选择器.startsWith('.') ? 节点.类名.split(/\s+/).includes(选择器.slice(1)) : 节点.标签名 === 选择器;
}

function 造元素(标签 = 'div') {
  const 事件 = new Map();
  const 节点 = {
    标签名: 标签, 类名: '', 文字: '', id: '', hidden: false, disabled: false,
    子节点: [], 父节点: null, 属性: new Map(), style: {}, value: '', innerHTML: '',
    clientWidth: 900, clientHeight: 640, width: 0, height: 0,
    classList: { add() {}, remove() {}, contains: () => false },
    get className() { return 节点.类名; }, set className(v) { 节点.类名 = String(v); },
    get textContent() { return 节点.文字; }, set textContent(v) { 节点.文字 = String(v); },
    append(...项) { for (const x of 项) { x.父节点 = 节点; 节点.子节点.push(x); } },
    replaceChildren(...项) { 节点.子节点 = []; 节点.append(...项); },
    remove() { const p = 节点.父节点; if (p) p.子节点 = p.子节点.filter(x => x !== 节点); 节点.父节点 = null; },
    contains(x) { for (let c = x; c; c = c.父节点) if (c === 节点) return true; return false; },
    addEventListener(n, fn) { if (!事件.has(n)) 事件.set(n, []); 事件.get(n).push(fn); },
    dispatch(n, e = {}) { for (const fn of 事件.get(n) || []) fn(e); },
    setAttribute(n, v) { 节点.属性.set(n, String(v)); },
    getAttribute(n) { return 节点.属性.get(n); },
    focus() {}, setPointerCapture() {}, hasPointerCapture: () => false, releasePointerCapture() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 268, height: 132 }),
    getContext: () => 假画布上下文,
    querySelector(选择器) { return 收集(节点, 选择器, [])[0] || null; },
    querySelectorAll(选择器) { return 收集(节点, 选择器, []); },
  };
  return 节点;
}

function 收集(节点, 选择器, 结果) {
  for (const 子 of 节点.子节点) {
    if (匹配(子, 选择器)) 结果.push(子);
    收集(子, 选择器, 结果);
  }
  return 结果;
}

const 假画布上下文 = new Proxy({}, {
  get: (_, 键) => (键 === 'canvas' ? {} : () => ({ width: 0, height: 0 })),
  set: () => true,
});

function 文本(节点) { return [节点.文字, ...节点.子节点.map(文本)].join(' '); }

function 按文字找按钮(盒, 文字) {
  return 收集(盒, 'button', []).find(b => b.文字 === 文字) || null;
}

function 造环境({ 有账号 = true, 删除结果 = { ok: true, 数据: { deleted: 2 } } } = {}) {
  const 请求 = [], 提示列表 = [], 索引调用 = [], 图谱跳转 = [];
  const 元素表 = new Map(), 文档事件 = new Map();
  const $ = id => {
    if (!元素表.has(id)) 元素表.set(id, 造元素('div'));
    return 元素表.get(id);
  };
  const body = 造元素('body');
  const 知识库请求 = (url, 选项 = {}) => {
    请求.push({ url, ...选项 });
    if (url.includes('记录')) {
      return Promise.resolve({ ok: 删除结果.ok, status: 删除结果.ok ? 200 : 400,
                               json: async () => 删除结果.数据 });
    }
    return Promise.resolve({ ok: true, json: async () => ({ 节点: [], 边: [] }) });
  };
  const context = vm.createContext({
    console, Map, Set, Promise, Number, String, Array, Object, Math, JSON, Date, Error, DOMException, AbortController,
    状态: { 图谱: null, 图数据: null, 图请求: null, 当前标签: null, 当前: null },
    $, innerWidth: 1200, innerHeight: 800,
    window: { devicePixelRatio: 1, WorkspaceAccess: 有账号 ? {} : undefined,
              matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {} },
    document: { body, hidden: false, documentElement: 造元素('html'),
                createElement: 造元素,
                addEventListener: (n, fn) => { if (!文档事件.has(n)) 文档事件.set(n, new Set()); 文档事件.get(n).add(fn); },
                removeEventListener: (n, fn) => { 文档事件.get(n)?.delete(fn); } },
    getComputedStyle: () => ({ getPropertyValue: () => '#aabbcc' }),
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    ResizeObserver: class { observe() {} }, IntersectionObserver: class { observe() {} },
    知识库请求, 转义: t => String(t), 打开图谱节点() {},
    提示: t => 提示列表.push(t), 载入索引: async () => { 索引调用.push(1); return true; },
    打开图谱: () => { 图谱跳转.push(1); },
  });
  vm.runInContext(source, context, { filename: 'graph.js' });
  const api = vm.runInContext('({ 打开图谱菜单, 关闭图谱菜单 })', context);
  const 菜单 = () => body.子节点.find(x => x.id === 'graph-menu') || null;
  const 空闲 = () => new Promise(r => setTimeout(r, 0));
  return { api, body, $, 请求, 提示列表, 索引调用, 图谱跳转, 文档事件, 菜单, 空闲, context };
}

const 收藏夹节点 = { id: 'tag:AI 工具 · 202', 标题: 'AI 工具 · 202', 类型: '标签', 数量: 28, 收藏夹id: '202' };
const 条目节点 = { id: 'summaries/abc.md', 标题: 'codex 有哪些奇技淫巧？', 类型: '文章', 收藏夹: 'AI 工具 · 202', 收藏夹id: '202' };

test('右键收藏夹：先说明收录条数，确认后才发请求', async () => {
  const h = 造环境();
  h.api.打开图谱菜单(收藏夹节点, 40, 40);
  const 盒 = h.菜单();
  assert.ok(盒, '右键节点必须弹出菜单');
  assert.match(文本(盒), /AI 工具 · 202/);
  assert.match(文本(盒), /已收录 28 条/);
  assert.ok(按文字找按钮(盒, '删除节点'), '首屏就是一个「删除节点」按钮');

  按文字找按钮(盒, '删除节点').dispatch('click');
  assert.match(文本(盒), /整夹删除？/, '整夹删除要单独确认，不能一步删掉');
  assert.match(文本(盒), /全部 28 条/, '必须写清会连带删掉多少条');
  assert.equal(h.请求.length, 0, '确认之前一个请求都不许发');

  const 确认 = 按文字找按钮(盒, '确认删除');
  assert.ok(确认);
  确认.dispatch('click');
  await h.空闲();

  const 删除请求 = h.请求.filter(r => r.url.includes('记录'));
  assert.equal(删除请求.length, 1);
  assert.equal(删除请求[0].method, 'DELETE');
  assert.deepEqual(JSON.parse(删除请求[0].body), { 收藏夹: '202' }, '整夹删除传收藏夹 id，不传节点 id');
  assert.deepEqual(h.提示列表, ['已删除 2 条记录']);
  assert.equal(h.索引调用.length, 1, '删完必须重建索引与图谱');
  assert.equal(h.菜单(), null, '删完菜单自己收起来');
});

test('右键条目：请求体是记录路径', async () => {
  const h = 造环境({ 删除结果: { ok: true, 数据: { deleted: 1 } } });
  h.api.打开图谱菜单(条目节点, 10, 10);
  const 盒 = h.菜单();
  assert.match(文本(盒), /codex 有哪些奇技淫巧？/);
  按文字找按钮(盒, '删除节点').dispatch('click');
  assert.match(文本(盒), /确认删除？/);
  assert.doesNotMatch(文本(盒), /整夹删除/, '单条删除不该吓唬人说整夹没了');
  按文字找按钮(盒, '确认删除').dispatch('click');
  await h.空闲();
  assert.deepEqual(JSON.parse(h.请求.find(r => r.url.includes('记录')).body), { 路径: 'summaries/abc.md' });
  assert.deepEqual(h.提示列表, ['已删除 1 条记录']);
});

test('取消与点外面：只关菜单，不发请求', async () => {
  const h = 造环境();
  h.api.打开图谱菜单(收藏夹节点, 40, 40);
  const 盒 = h.菜单();
  按文字找按钮(盒, '删除节点').dispatch('click');
  按文字找按钮(盒, '取消').dispatch('click');
  assert.equal(h.菜单(), null);
  assert.equal(h.请求.length, 0);

  h.api.打开图谱菜单(收藏夹节点, 40, 40);
  按文字找按钮(h.菜单(), '删除节点').dispatch('click');
  const 外面 = 造元素('div');
  for (const fn of h.文档事件.get('pointerdown') || []) fn({ target: 外面 });
  assert.equal(h.菜单(), null, '点菜单外必须关掉');
  assert.equal(h.请求.length, 0);

  h.api.打开图谱菜单(收藏夹节点, 40, 40);
  for (const fn of h.文档事件.get('keydown') || []) fn({ key: 'Escape', stopPropagation() {} });
  assert.equal(h.菜单(), null, 'Esc 必须关掉');
});

test('删除失败：菜单留下、按钮可用、错误写在菜单里', async () => {
  const h = 造环境({ 删除结果: { ok: false, 数据: { error: { code: 'record_not_found', message: '这条内容已经不在你的工作区里了。' } } } });
  h.api.打开图谱菜单(条目节点, 10, 10);
  按文字找按钮(h.菜单(), '删除节点').dispatch('click');
  const 确认 = 按文字找按钮(h.菜单(), '确认删除');
  确认.dispatch('click');
  await h.空闲();
  assert.equal(确认.disabled, false, '失败后要让人能重试');
  assert.equal(确认.文字, '确认删除');
  assert.match(文本(h.菜单()), /已经不在你的工作区里/, '把服务端的说明原样给用户看');
  assert.deepEqual(h.提示列表, [], '失败不该报成功');
  assert.equal(h.索引调用.length, 0, '失败不该刷新成空库');
});

test('收藏夹缺 id 时宁可报错，也不发一个删不掉的请求', async () => {
  const h = 造环境();
  h.api.打开图谱菜单({ id: 'tag:手改过的节点', 标题: '手改过的节点', 类型: '标签', 数量: 3 }, 10, 10);
  按文字找按钮(h.菜单(), '删除节点').dispatch('click');
  按文字找按钮(h.菜单(), '确认删除').dispatch('click');
  await h.空闲();
  assert.equal(h.请求.filter(r => r.url.includes('记录')).length, 0);
  assert.match(文本(h.菜单()), /缺少标识/);
});

test('删掉的正是当前打开的那条时，回图谱而不是留着旧内容', async () => {
  const h = 造环境();
  // 模拟"阅读面板正开着这条记录"：直接在 vm 的共享状态里设好。
  vm.runInContext('状态.当前 = { 路径: "summaries/abc.md", 收藏夹: "AI 工具 · 202" };', h.context);
  h.api.打开图谱菜单(条目节点, 10, 10);
  按文字找按钮(h.菜单(), '删除节点').dispatch('click');
  按文字找按钮(h.菜单(), '确认删除').dispatch('click');
  await h.空闲();
  assert.deepEqual(h.图谱跳转, [1], '被删的笔记还开着，必须回图谱');
});
