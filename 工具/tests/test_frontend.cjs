// Run with: node --test 工具/tests/test_frontend.cjs
const assert = require('node:assert/strict');
const { mkdirSync, mkdtempSync, readFileSync, writeFileSync } = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const source = readFileSync(path.join(root, '网页/app.js'), 'utf8');
const graphSource = readFileSync(path.join(root, '网页/graph.js'), 'utf8');

// 自包含夹具：仓库里的示例笔记库（演示数据）已按要求删除，测试不再读它。
// 夹具在临时目录生成真实文件，好让「图谱节点入口指向的文件确实存在」这条断言仍有意义。
const 夹具目录 = mkdtempSync(path.join(os.tmpdir(), 'zhihu-kb-fixture-'));
function 写夹具笔记(相对路径) {
  const 绝对路径 = path.join(夹具目录, 相对路径);
  mkdirSync(path.dirname(绝对路径), { recursive: true });
  writeFileSync(绝对路径, '# 夹具笔记\n');
  return 相对路径;
}
const 夹具文章路径 = 写夹具笔记('wiki/articles/夹具夹/夹具文章/00 MOC.md');
const 夹具分节路径 = [1, 2, 3, 4, 5, 6].map(i => 写夹具笔记(`wiki/articles/夹具夹/夹具文章/0${i} 夹具分节${i}.md`));
const 夹具另一篇路径 = 写夹具笔记('wiki/articles/夹具夹/另一篇/01 另一分节.md');
const 夹具概念路径 = 写夹具笔记('wiki/concepts/标签：RAG.md');
const 夹具导航路径 = 写夹具笔记('wiki/collections/夹具夹 MOC.md');

const liveIndex = {
  生成时间: '2026-09-14T00:00:00',
  统计: { 文章数: 2, 笔记数: 9, 收藏夹数: 1, 标签数: 2, 总字数: 120 },
  收藏夹: [{ 名称: '夹具夹', 数量: 2, 字数: 120 }],
  标签: [{ 名称: 'RAG', 数量: 6 }, { 名称: '其它', 数量: 1 }],
  文章: [
    {
      标题: '夹具文章', MOC标题: '夹具文章', 路径: 夹具文章路径, 收藏夹: '夹具夹', 作者: '夹具作者',
      类型: '回答', 原链接: 'https://www.zhihu.com/question/1/answer/2', 字数: 60, 标签: ['RAG'],
      相关: [], 摘要: '夹具摘要',
      分节: 夹具分节路径.map((路径, 序号) => ({
        标题: `夹具文章 · 夹具分节${序号 + 1}`, 路径, 标签: ['RAG'], 摘要: '夹具摘要', 序号: 序号 + 1, 字数: 10, 相关: [],
      })),
    },
    {
      标题: '另一篇', MOC标题: '另一篇', 路径: 夹具另一篇路径, 收藏夹: '夹具夹', 作者: '夹具作者',
      类型: '文章', 原链接: 'https://zhuanlan.zhihu.com/p/2', 字数: 20, 标签: ['其它'], 相关: [], 摘要: '夹具摘要',
      分节: [{ 标题: '另一篇 · 另一分节', 路径: 夹具另一篇路径, 标签: ['其它'], 摘要: '夹具摘要', 序号: 1, 字数: 10, 相关: [] }],
    },
  ],
  入口: [
    { 标题: '夹具夹 MOC', 路径: 夹具导航路径, 文章: '', 收藏夹: '导航', 类型: '导航', 作者: '', 原链接: '',
      发布时间: '', 收藏时间: '', 赞同数: 0, 评论数: 0, 字数: 10, 阅读时长: '', 标签: ['RAG'], 相关: [], 摘要: '', 层级: '导航' },
    { 标题: '标签：RAG', 路径: 夹具概念路径, 文章: '', 收藏夹: '概念', 类型: '概念', 作者: '', 原链接: '',
      发布时间: '', 收藏时间: '', 赞同数: 0, 评论数: 0, 字数: 0, 阅读时长: '', 标签: ['RAG'], 相关: [], 摘要: '', 层级: '概念' },
  ],
};
const liveGraph = {
  节点: [
    { id: 夹具文章路径, 标题: '夹具文章', 类型: '文章', 字数: 60 },
    ...夹具分节路径.map((路径, i) => ({ id: 路径, 标题: `夹具分节${i + 1}`, 类型: '分节', 字数: 10 })),
    { id: 夹具另一篇路径, 标题: '另一篇 · 另一分节', 类型: '分节', 字数: 10 },
    { id: 'tag:RAG', 标题: 'RAG', 类型: '标签', 数量: 6 },
    { id: 'tag:其它', 标题: '其它', 类型: '标签', 数量: 1 },
    { id: 夹具概念路径, 标题: '标签：RAG', 类型: '概念', 字数: 0 },
  ],
  边: [
    { 源: 夹具文章路径, 目标: 'tag:RAG', 类型: '标签' },
    { 源: 夹具另一篇路径, 目标: 'tag:其它', 类型: '标签' },
    { 源: 夹具概念路径, 目标: 'tag:RAG', 类型: '概念页' },
  ],
};
const sampleGraph = {
  节点: [
    { id: 'article', 标题: '文章', 类型: '文章' },
    { id: 'section', 标题: '文章 · 分节', 类型: '分节' },
    { id: 'tag:RAG', 标题: 'RAG', 类型: '标签', 数量: 1 },
  ],
  边: [{ 源: 'article', 目标: 'section', 类型: '文章线' }, { 源: 'section', 目标: 'tag:RAG', 类型: '标签' }],
};

function harness() {
  const elements = new Map(), requests = [], rendered = [], messages = [];
  const listeners = new Map(), frames = new Map();
  let frameId = 0, context;
  const on = (map, name, fn) => {
    if (!map.has(name)) map.set(name, []);
    map.get(name).push(fn);
  };
  const drawing = {
    setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, fill() {}, arc() {}, strokeText() {}, fillText() {},
    measureText: text => ({ width: text.length * 7 }),
  };
  const getElement = (id) => {
    if (!elements.has(id)) {
      const classes = new Set(), attributes = new Map(), events = new Map(), captures = new Set();
      const style = { setProperty(name, value) { this[name] = value; } };
      const el = {
        id, innerHTML: '', textContent: '', style, value: '', hidden: false, disabled: false,
        dataset: {}, events, clientWidth: 1000, clientHeight: 650,
        width: 0, height: 0, scrollTop: 0, scrollHeight: 650,
        classList: {
          add: (...names) => names.forEach(n => classes.add(n)),
          remove: (...names) => names.forEach(n => classes.delete(n)),
          contains: name => classes.has(name),
          toggle(name, force) {
            const add = force === undefined ? !classes.has(name) : !!force;
            if (add) classes.add(name); else classes.delete(name);
            return add;
          },
        },
        setAttribute: (name, value) => attributes.set(name, String(value)),
        getAttribute: name => attributes.get(name) ?? null,
        addEventListener: (name, fn) => on(events, name, fn),
        removeEventListener: (name, fn) => events.set(name, (events.get(name) || []).filter(f => f !== fn)),
        dispatch(name, event = {}) { for (const fn of events.get(name) || []) fn(event); },
        setPointerCapture: id => captures.add(id),
        hasPointerCapture: id => captures.has(id),
        releasePointerCapture: id => captures.delete(id),
        querySelectorAll: () => [],
        getBoundingClientRect: () => ({ left: 12, top: 24, x: 12, y: 24, width: el.clientWidth, height: el.clientHeight }),
        getContext: () => drawing,
        focus() { if (context) context.document.activeElement = el; },
        select() {}, scrollIntoView() {},
      };
      elements.set(id, el);
    }
    return elements.get(id);
  };
  for (const id of ['reader', 'note', 'tag-view', 'reader-message', 'resizer-2', 'tab-note', 'rail', 'graph-node-list', 'graph-tooltip']) getElement(id).hidden = true;
  for (const id of ['ov-search', 'ov-tags']) getElement(id).classList.add('hidden');
  getElement('html').dataset.theme = 'light';
  getElement('body').classList.add('sidebar-open');
  context = vm.createContext({
    AbortController, console, setTimeout, clearTimeout, rendered, messages,
    location: { hash: '' },
    window: { innerWidth: 1440, devicePixelRatio: 1, addEventListener() {} },
    localStorage: { getItem: () => null, setItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => '#888888' }),
    requestAnimationFrame: fn => { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame: id => frames.delete(id),
    document: {
      hidden: false, title: '', body: getElement('body'), documentElement: getElement('html'),
      addEventListener: (name, fn) => on(listeners, name, fn), getElementById: getElement,
      querySelector: () => null,
      querySelectorAll: selector => selector === '.overlay' ? [getElement('ov-search'), getElement('ov-tags')] : [],
    },
    fetch: (url, options = {}) => new Promise((resolve, reject) => {
      requests.push({ url, signal: options.signal, options, resolve, reject });
    }),
  });
  vm.runInContext(graphSource, context, { filename: 'graph.js' });
  vm.runInContext(source + '\nglobalThis.app = { 状态, 执行搜索, 关闭搜索, 关闭, 打开笔记, 载入索引, 绑定事件, 渲染标签云, 打开图谱, 打开标签, 图谱节点入口, 打开图谱节点, 获取标签内容, 读取位置, 应用地址路由, 构建图谱, 世界到屏幕, 屏幕到世界, 按位置缩放, 命中图谱节点, 图谱手势是否点击, 适应图谱, 调整图谱尺寸, 载入图谱, 请求图谱绘制, 停止图谱动画, 绑定图谱事件, 切换展开阅读 };', context, { filename: 'app.js' });
  vm.runInContext(
    '高亮树项 = () => {}; 渲染笔记 = (body, note) => rendered.push({ body, path: note.路径 });' +
    '提示 = message => messages.push(message); 渲染文件树 = () => {}; 更新状态栏 = () => {};', context);
  const reply = (index, json, ok = true) => requests[index].resolve({ ok, json: async () => json });
  const seedGraph = (data = sampleGraph) => {
    const graph = context.app.构建图谱(1000, 650, data);
    context.app.状态.图谱 = graph;
    context.app.状态.图数据 = data;
    context.app.调整图谱尺寸();
    return graph;
  };
  const flushFrames = () => {
    let count = 0;
    while (frames.size && count < 200) {
      const [id, fn] = frames.entries().next().value;
      frames.delete(id); fn(); count++;
    }
    assert.equal(frames.size, 0, '图谱动画必须在有限帧内停止');
    return count;
  };
  return { app: context.app, context, requests, rendered, messages, element: getElement, reply, listeners, frames, seedGraph, flushFrames };
}

async function loadIndex(h, index = liveIndex) {
  const pending = h.app.载入索引();
  h.reply(h.requests.length - 1, index);
  await pending;
}

function addNote(h, name) {
  h.app.状态.路径到笔记.set(name, { 路径: name, 标题: name, 类型: '分节' });
}

function near(a, b) { assert.ok(Math.abs(a - b) < 1e-7, a + ' != ' + b); }

function result(title) {
  return { 结果: [{ 标题: title, 路径: 'wiki/' + title + '.md', 标签: [] }] };
}

test('旧搜索结果晚到时不能覆盖新搜索', async () => {
  const h = harness();
  const old = h.app.执行搜索('old');
  const recent = h.app.执行搜索('new');
  h.reply(1, result('new'));
  await recent;
  h.reply(0, result('old'));
  await old;
  assert.equal(h.app.状态.搜索结果[0].标题, 'new');
  assert.equal(h.requests[0].signal.aborted, true);
});

test('相同搜索词重新请求也按请求身份区分先后', async () => {
  const h = harness();
  const old = h.app.执行搜索('RAG');
  const recent = h.app.执行搜索('RAG');
  h.reply(1, result('new'));
  await recent;
  h.reply(0, result('old'));
  await old;
  assert.equal(h.app.状态.搜索结果[0].标题, 'new');
});

test('过期搜索的失败不能覆盖成功结果', async () => {
  const h = harness();
  const old = h.app.执行搜索('old');
  const recent = h.app.执行搜索('new');
  h.reply(1, result('new'));
  await recent;
  h.requests[0].reject(new Error('delayed failure'));
  await old;
  assert.match(h.element('search-results').innerHTML, /new/);
  assert.doesNotMatch(h.element('search-results').innerHTML, /搜索失败/);
});

test('清空搜索立即取消未完成请求并清除结果', async () => {
  const h = harness();
  const old = h.app.执行搜索('RAG');
  await h.app.执行搜索('');
  assert.equal(h.requests[0].signal.aborted, true);
  h.reply(0, result('old'));
  await old;
  assert.equal(h.app.状态.搜索结果.length, 0);
  assert.equal(h.element('search-results').innerHTML, '');
});

test('关闭遮罩取消搜索且晚到结果不能再写回', async () => {
  const h = harness();
  const old = h.app.执行搜索('RAG');
  h.app.关闭();
  assert.equal(h.requests[0].signal.aborted, true);
  h.reply(0, result('old'));
  await old;
  assert.equal(h.app.状态.搜索结果.length, 0);
});

test('紧凑索引的路径到文章映射保存路径而非对象', async () => {
  const h = harness();
  const load = h.app.载入索引();
  h.reply(0, { 文章: [{ 路径: 'wiki/moc.md', MOC标题: 'MOC', 分节: [
    { 路径: 'wiki/section.md', 标题: 'Section' },
  ] }] });
  await load;
  assert.equal(h.app.状态.路径到文章.get('wiki/section.md'), 'wiki/moc.md');
});

test('连续打开笔记时只渲染最后一次选择', async () => {
  const h = harness();
  for (const name of ['a', 'b']) h.app.状态.路径到笔记.set(name, { 路径: name, 标题: name });
  const old = h.app.打开笔记('a');
  const recent = h.app.打开笔记('b');
  h.reply(1, { 内容: 'B' });
  await recent;
  h.reply(0, { 内容: 'A' });
  await old;
  assert.equal(h.rendered.length, 1);
  assert.equal(h.rendered[0].body, 'B');
  assert.equal(h.rendered[0].path, 'b');
  assert.equal(h.app.状态.当前.路径, 'b');
});

test('笔记HTTP失败不缓存空正文且可以重试', async () => {
  const h = harness();
  h.app.状态.路径到笔记.set('a', { 路径: 'a', 标题: 'a' });
  const first = h.app.打开笔记('a');
  h.reply(0, { detail: 'not found' }, false);
  await first;
  assert.equal(h.app.状态.正文缓存.has('a'), false);
  const retry = h.app.打开笔记('a');
  h.reply(1, { 内容: 'restored' });
  await retry;
  assert.equal(h.rendered[0].body, 'restored');
});



test('标签重复渲染后一次点击只导航一次，不触发全文搜索', async () => {
  const h = harness();
  await loadIndex(h);
  h.app.绑定事件();
  h.app.渲染标签云(); h.app.渲染标签云();
  const previous = h.app.状态.笔记请求序号;
  const tag = { dataset: { tag: 'RAG' } };
  const event = { preventDefault() {}, target: { closest: selector => selector === '.taglink' ? tag : null } };
  for (const fn of h.listeners.get('click') || []) fn(event);
  assert.equal(h.app.状态.笔记请求序号, previous + 1);
  assert.equal(h.app.状态.当前标签, 'RAG');
  assert.equal(h.element('tag-view').hidden, false);
  assert.equal(h.requests.filter(r => r.url.startsWith('/api/搜索')).length, 0);
});

test('夹具图谱全部节点均有有效入口，笔记目标也实际存在', async () => {
  const h = harness();
  await loadIndex(h);
  const types = new Set();
  for (const node of liveGraph.节点) {
    const entry = h.app.图谱节点入口(node);
    assert.ok(entry, '无入口: ' + node.id);
    types.add(node.类型);
    if (entry.类型 === '笔记') {
      assert.doesNotThrow(() => readFileSync(path.join(夹具目录, entry.路径)));
    } else {
      assert.ok(h.app.获取标签内容(entry.标签).分节.length > 0, '空标签: ' + entry.标签);
    }
  }
  assert.deepEqual([...types].sort(), ['分节', '文章', '标签', '概念'].sort());
});

test('标签按完整名称匹配分节，RAG 包含 6 个分节和概念入口', async () => {
  const h = harness();
  await loadIndex(h);
  const rag = h.app.获取标签内容('RAG');
  const expected = liveIndex.文章.flatMap(a => a.分节).filter(n => n.标签.includes('RAG'));
  assert.equal(rag.分节.length, expected.length);
  assert.equal(rag.概念.length, 1);
  assert.equal(h.app.获取标签内容('RA').分节.length, 0);
  h.app.打开标签('RAG');
  assert.match(h.element('tag-head').innerHTML, /打开概念笔记/);
  assert.equal((h.element('tag-notes').innerHTML.match(/class="tag-note"/g) || []).length, expected.length);
});

test('没有独立概念页的标签也能打开关联分节', async () => {
  const h = harness();
  await loadIndex(h);
  const tag = liveGraph.节点.find(n => n.类型 === '标签' && h.app.获取标签内容(n.标题).概念.length === 0);
  await h.app.打开图谱节点(tag);
  assert.equal(h.app.状态.当前标签, tag.标题);
  assert.equal(h.element('reader').hidden, false);
  assert.match(h.element('tag-notes').innerHTML, /data-note=/);
  assert.doesNotMatch(h.element('tag-head').innerHTML, /打开概念笔记/);
});

test('标签标题中的 HTML 作为文本转义，不插入可执行标记', () => {
  const h = harness();
  h.app.打开标签('<img src=x onerror=alert(1)>');
  assert.match(h.element('tag-head').innerHTML, /&lt;img/);
  assert.doesNotMatch(h.element('tag-head').innerHTML, /<img/);
});

test('打开标签后，晚到的笔记正文不能覆盖标签面板', async () => {
  const h = harness();
  addNote(h, 'a');
  const pending = h.app.打开笔记('a');
  h.app.打开标签('RAG');
  h.reply(0, { 内容: 'late note' }); await pending;
  assert.equal(h.rendered.length, 0);
  assert.equal(h.app.状态.正文缓存.has('a'), false);
  assert.equal(h.element('note').hidden, true);
  assert.equal(h.element('tag-view').hidden, false);
});

test('关闭阅读返回图谱后，晚到的笔记响应不会重新打开面板', async () => {
  const h = harness();
  h.seedGraph(); addNote(h, 'a');
  const pending = h.app.打开笔记('a');
  await h.app.打开图谱();
  h.reply(0, { 内容: 'late note' }); await pending;
  assert.equal(h.rendered.length, 0);
  assert.equal(h.element('reader').hidden, true);
  assert.equal(h.app.状态.当前, null);
});

test('无 hash 的默认页面只加载主图，不预读笔记正文', async () => {
  const h = harness();
  const pending = h.app.应用地址路由();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/图谱');
  h.reply(0, sampleGraph); await pending;
  assert.equal(h.element('reader').hidden, true);
  assert.equal(h.context.document.title, '关系图谱 · 知乎知识库');
  assert.equal(h.app.状态.图谱.节点.length, 3);
});

test('笔记深链接可打开，后续 hash 路由可前进到标签、返回图谱', async () => {
  const h = harness();
  h.seedGraph(); addNote(h, 'wiki/中文 笔记.md');
  h.context.location.hash = '#' + encodeURIComponent('wiki/中文 笔记.md');
  const pending = h.app.应用地址路由();
  h.reply(0, { 内容: 'linked note' }); await pending;
  assert.equal(h.rendered[0].path, 'wiki/中文 笔记.md');
  h.context.location.hash = '#tag%3ARAG';
  await h.app.应用地址路由();
  assert.equal(h.app.状态.当前标签, 'RAG');
  h.context.location.hash = '';
  await h.app.应用地址路由();
  assert.equal(h.element('reader').hidden, true);
  assert.equal(h.app.状态.路由, '');
});

test('无效或损坏的 hash 安全退回图谱', async () => {
  const h = harness(); h.seedGraph();
  for (const hash of ['#missing.md', '#%E0%A4%A']) {
    h.app.状态.路由 = null; h.context.location.hash = hash;
    await h.app.应用地址路由();
    assert.equal(h.element('reader').hidden, true);
    assert.equal(h.app.状态.路由, '');
  }
});

test('索引刷新失败保留已有映射与正文缓存', async () => {
  const h = harness();
  await loadIndex(h);
  const index = h.app.状态.索引, count = h.app.状态.路径到笔记.size;
  h.app.状态.正文缓存.set('cached', 'body');
  const pending = h.app.载入索引();
  h.reply(1, { detail: 'offline' }, false);
  assert.equal(await pending, false);
  assert.equal(h.app.状态.索引, index);
  assert.equal(h.app.状态.路径到笔记.size, count);
  assert.equal(h.app.状态.正文缓存.get('cached'), 'body');
});

test('并发图谱加载合并为一个请求，完成后复用数据', async () => {
  const h = harness();
  const first = h.app.载入图谱(), second = h.app.载入图谱();
  assert.equal(h.requests.length, 1);
  h.reply(0, sampleGraph);
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  await h.app.载入图谱();
  assert.equal(h.requests.length, 1);
});

test('图谱 HTTP 失败不缓存，重试后能恢复', async () => {
  const h = harness();
  const first = h.app.载入图谱();
  h.reply(0, {}, false); assert.equal(await first, false);
  assert.equal(h.app.状态.图数据, null);
  assert.equal(h.element('btn-graph-retry').hidden, false);
  const retry = h.app.载入图谱();
  h.reply(1, sampleGraph); assert.equal(await retry, true);
  assert.equal(h.element('graph-message').hidden, true);
});

test('图谱数据格式错误会显示错误状态并允许重试', async () => {
  const h = harness();
  const first = h.app.载入图谱();
  h.reply(0, { 节点: {} }); assert.equal(await first, false);
  assert.match(h.element('graph-message-title').textContent, /加载失败/);
  assert.equal(h.app.状态.图请求, null);
});

test('强制刷新取消旧图请求，旧响应不能覆盖新图', async () => {
  const h = harness();
  const old = h.app.载入图谱(), fresh = h.app.载入图谱(true);
  assert.equal(h.requests[0].signal.aborted, true);
  h.reply(1, sampleGraph); assert.equal(await fresh, true);
  const current = h.app.状态.图谱;
  h.reply(0, { 节点: [], 边: [] }); assert.equal(await old, false);
  assert.equal(h.app.状态.图谱, current);
  assert.equal(h.app.状态.图谱.节点.length, 3);
});

test('旧图请求延迟失败也不会覆盖新请求的成功界面', async () => {
  const h = harness();
  const old = h.app.载入图谱(), fresh = h.app.载入图谱(true);
  h.reply(1, sampleGraph); await fresh;
  h.requests[0].reject(new Error('late network error')); await old;
  assert.equal(h.element('graph-message').hidden, true);
});

test('空图显示导入提示，视图数值保持有限', async () => {
  const h = harness();
  const pending = h.app.载入图谱();
  h.reply(0, { 节点: [], 边: [] }); assert.equal(await pending, true);
  assert.match(h.element('graph-message-title').textContent, /空/);
  assert.equal(Number.isFinite(h.app.状态.图谱.缩放), true);
  assert.equal(h.element('btn-graph-retry').hidden, true);
});

test('布局使用稳定种子，并过滤重复节点、悬空边和自连线', () => {
  const h = harness();
  const data = { 节点: [...sampleGraph.节点, sampleGraph.节点[0], null], 边: [...sampleGraph.边, { 源: 'missing', 目标: 'section' }, { 源: 'article', 目标: 'article' }] };
  const a = h.app.构建图谱(1000, 650, data), b = h.app.构建图谱(1000, 650, data);
  assert.equal(a.节点.length, 3); assert.equal(a.边.length, 2);
  for (let i = 0; i < a.节点.length; i++) {
    near(a.节点[i].x, b.节点[i].x); near(a.节点[i].y, b.节点[i].y);
    assert.ok(Number.isFinite(a.节点[i].x));
  }
});

test('重载同一图保留坐标、缩放、中心和选择，不随机重排', () => {
  const h = harness(), old = h.seedGraph();
  old.缩放 = .73; old.偏移x = 88; old.偏移y = 99; old.自动适应 = false; old.选中 = 'section';
  const current = h.app.构建图谱(1000, 650, sampleGraph, old);
  assert.equal(current.缩放, .73); assert.equal(current.偏移x, 88); assert.equal(current.偏移y, 99);
  assert.equal(current.选中, 'section'); assert.equal(current.温度, 0);
  for (const n of current.节点) { near(n.x, old.节点表.get(n.id).x); near(n.y, old.节点表.get(n.id).y); }
});

test('世界坐标与 CSS 屏幕坐标可往返，鼠标缩放保持锚点', () => {
  const h = harness(), graph = h.seedGraph();
  graph.缩放 = .6; graph.偏移x = 135; graph.偏移y = -42;
  const world = { x: 157.3, y: -88.5 };
  const screen = h.app.世界到屏幕(graph, world), back = h.app.屏幕到世界(graph, screen);
  near(back.x, world.x); near(back.y, world.y);
  h.app.按位置缩放(graph, 1.8, screen);
  const zoomed = h.app.世界到屏幕(graph, world);
  near(zoomed.x, screen.x); near(zoomed.y, screen.y);
  h.app.按位置缩放(graph, 1e9, screen); assert.equal(graph.缩放, 4);
  h.app.按位置缩放(graph, 1e-9, screen); assert.equal(graph.缩放, .05);
});

test('高 DPI 画布按 CSS 像素命中，附近重叠命中最近的节点', () => {
  const h = harness(), graph = h.seedGraph();
  h.context.window.devicePixelRatio = 2;
  h.app.调整图谱尺寸();
  assert.equal(h.element('graph-canvas').width, 2000);
  assert.equal(h.element('graph-canvas').height, 1300);
  const node = graph.节点[0], point = h.app.世界到屏幕(graph, node);
  assert.equal(h.app.命中图谱节点(graph, point), node);
  const neighbor = graph.节点[1];
  neighbor.x = node.x + 3 / graph.缩放; neighbor.y = node.y;
  assert.equal(h.app.命中图谱节点(graph, { x: point.x + 2.9, y: point.y }), neighbor);
  assert.equal(h.app.命中图谱节点(graph, { x: -10000, y: -10000 }), null);
});

test('轻微抖动仍算点击，拖出再拖回起点也不能误打开', () => {
  const h = harness();
  const gesture = { 节点: { id: 'a' }, 起点: { x: 10, y: 20 }, 距离: 3 };
  assert.equal(h.app.图谱手势是否点击(gesture, { x: 13, y: 24 }), true);
  assert.equal(h.app.图谱手势是否点击(gesture, { x: 16, y: 20 }), false);
  gesture.距离 = 30;
  assert.equal(h.app.图谱手势是否点击(gesture, { x: 10, y: 20 }), false);
  assert.equal(h.app.图谱手势是否点击({ ...gesture, 节点: null }, { x: 10, y: 20 }), false);
});

test('尺寸变化不重建布局，并保留手动视图的世界中心', () => {
  const h = harness(), graph = h.seedGraph();
  graph.自动适应 = false; graph.缩放 = .7; graph.偏移x = 314; graph.偏移y = 188;
  const coordinates = graph.节点.map(n => ({ x: n.x, y: n.y }));
  const center = h.app.屏幕到世界(graph, { x: graph.宽 / 2, y: graph.高 / 2 });
  h.element('graph-stage').clientWidth = 570; h.element('graph-stage').clientHeight = 500;
  h.app.调整图谱尺寸();
  assert.equal(h.app.状态.图谱, graph); assert.equal(graph.缩放, .7);
  const after = h.app.屏幕到世界(graph, { x: graph.宽 / 2, y: graph.高 / 2 });
  near(center.x, after.x); near(center.y, after.y);
  graph.节点.forEach((n, i) => { near(n.x, coordinates[i].x); near(n.y, coordinates[i].y); });
});

test('适应全部节点将当前图谱置于画布范围内', () => {
  const h = harness(), graph = h.seedGraph(liveGraph);
  h.app.适应图谱(graph, false);
  for (const n of graph.节点) {
    const p = h.app.世界到屏幕(graph, n);
    assert.ok(p.x >= 0 && p.x <= graph.宽 && p.y >= 0 && p.y <= graph.高, n.id);
  }
});

test('图谱只挂一个动画帧并自动冷却停止，静态界面不永久空跑', () => {
  const h = harness(), graph = h.seedGraph();
  for (let i = 0; i < 10; i++) h.app.请求图谱绘制();
  assert.equal(h.frames.size, 1);
  assert.ok(h.flushFrames() < 100);
  assert.equal(graph.动画, null); assert.ok(graph.温度 <= .01);
});

test('关闭搜索遮罩不会取消主图的绘制，隐藏页面才停止动画', () => {
  const h = harness(), graph = h.seedGraph();
  h.app.绑定图谱事件(); h.app.关闭();
  assert.equal(h.frames.size, 1); assert.ok(graph.动画 !== null);
  h.context.document.hidden = true;
  for (const fn of h.listeners.get('visibilitychange')) fn();
  assert.equal(h.frames.size, 0); assert.equal(graph.动画, null);
});

test('Canvas 鼠标点击标签节点会打开关联视图', () => {
  const h = harness(), graph = h.seedGraph(); h.flushFrames(); h.app.绑定图谱事件();
  const canvas = h.element('graph-canvas'), node = graph.节点表.get('tag:RAG');
  const p = h.app.世界到屏幕(graph, node);
  const event = { pointerId: 1, button: 0, clientX: p.x + 12, clientY: p.y + 24 };
  canvas.dispatch('pointerdown', event); canvas.dispatch('pointerup', event);
  assert.equal(h.app.状态.当前标签, 'RAG');
  assert.equal(canvas.hasPointerCapture(1), false);
});

test('Canvas 拖拽、取消以及丢失捕获不会误开笔记', () => {
  for (const mode of ['drag', 'pointercancel', 'lostpointercapture']) {
    const h = harness(), graph = h.seedGraph(); h.flushFrames(); h.app.绑定图谱事件();
    const canvas = h.element('graph-canvas'), p = h.app.世界到屏幕(graph, graph.节点表.get('tag:RAG'));
    const event = { pointerId: 2, button: 0, clientX: p.x + 12, clientY: p.y + 24 };
    canvas.dispatch('pointerdown', event);
    if (mode === 'drag') {
      canvas.dispatch('pointermove', { ...event, clientX: event.clientX + 50 });
      canvas.dispatch('pointermove', event); canvas.dispatch('pointerup', event);
    } else canvas.dispatch(mode, event);
    assert.equal(h.app.状态.当前标签, null, mode);
    assert.equal(graph.手势, null); assert.equal(h.element('reader').hidden, true);
  }
});

test('Canvas 方向键可选择任一节点并通过 Enter 打开', () => {
  const h = harness(), graph = h.seedGraph(); h.app.绑定图谱事件();
  const canvas = h.element('graph-canvas');
  for (let i = 0; i < graph.节点.length; i++) canvas.dispatch('keydown', { key: 'ArrowRight', preventDefault() {} });
  assert.equal(graph.键盘节点, 'tag:RAG');
  canvas.dispatch('keydown', { key: 'Enter', preventDefault() {} });
  assert.equal(h.app.状态.当前标签, 'RAG');
});

test('主界面 DOM 覆盖全部脚本入口，图谱不再置于弹窗中', () => {
  const html = readFileSync(path.join(root, '网页/index.html'), 'utf8');
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
  const refs = [...(source + graphSource).matchAll(/\$\('([^']+)'\)/g)].map(m => m[1]);
  assert.ok(refs.length > 50);
  for (const id of refs) assert.ok(ids.has(id), '缺失 DOM: ' + id);
  assert.equal(ids.has('welcome'), false); assert.equal(ids.has('ov-graph'), false);
  assert.ok(html.indexOf('src="/graph.js"') < html.indexOf('src="/app.js"'));
});
