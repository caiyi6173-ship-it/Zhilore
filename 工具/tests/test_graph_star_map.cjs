// node --test 工具/tests/test_graph_star_map.cjs
// 静夜星图的确定性布局、画面命中、动效生命周期与大图回归；不访问网络或真实账号。
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');
const test = require('node:test');
const root = path.join(__dirname, '../..');
const source = readFileSync(path.join(root, '网页/graph.js'), 'utf8');
const css = readFileSync(path.join(root, '网页/style.css'), 'utf8');

function collectionGraph(count = 18, groups = 3, long = false) {
  const 节点 = [], 边 = [];
  for (let g = 0; g < groups; g++) {
    const name = long ? '关于设计、科学与人工智能的长期思考：一份很长的中文收藏夹名称 · ' + g : '收藏夹 · ' + g;
    const id = 'tag:' + name;
    节点.push({ id, 标题: name, 类型: '标签', 数量: 0 });
    for (let i = g; i < count; i += groups) {
      const n = { id: 'notes/' + i + '.md', 标题: long ? '如何从信息收集走向真正的理解——关于知识、写作与长期学习的一个很长的中文标题（第' + i + '篇）' : '知识的另一种连接方式 · ' + i, 类型: '文章', 收藏夹: name };
      节点.push(n); 边.push({ 源: n.id, 目标: id, 类型: '标签' });
      节点.find(n => n.id === id).数量++;
    }
  }
  return { 节点, 边 };
}

function harness({ width = 1050, height = 680, reduced = false, fine = true, coarse = false } = {}) {
  const elements = new Map(), frames = new Map(), docEvents = new Map(), media = new Map();
  const opened = [], requests = [], intersections = [], resizes = [], textPaints = [];
  let clock = 0, frameId = 0, paints = 0, measurements = 0;
  const add = (map, name, fn) => { if (!map.has(name)) map.set(name, []); map.get(name).push(fn); };
  const drawing = {
    font: '', setTransform() {}, clearRect() { paints++; textPaints.length = 0; }, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, fill() {}, arc() {}, strokeText() {}, fillText(text, x, y) { textPaints.push({ text, x, y }); },
    measureText(text) {
      measurements++;
      const size = Number(this.font.match(/([\d.]+)px/)?.[1] || 12);
      return { width: [...text].reduce((sum, c) => sum + (c.charCodeAt(0) > 255 ? size : size * .56), 0) };
    },
  };
  const element = id => {
    if (!elements.has(id)) {
      const events = new Map(), attrs = new Map(), captures = new Set(), classes = new Set();
      const el = {
        id, clientWidth: width, clientHeight: height, width: 0, height: 0,
        offsetWidth: id === 'graph-tooltip' ? Math.min(270, width - 24) : width,
        offsetHeight: id === 'graph-tooltip' ? 60 : height,
        hidden: ['graph-tooltip', 'graph-node-list'].includes(id), value: '', dataset: {}, textContent: '', innerHTML: '',
        style: { setProperty(name, value) { this[name] = value; } },
        classList: { add: n => classes.add(n), remove: n => classes.delete(n), contains: n => classes.has(n) },
        setAttribute: (n, v) => attrs.set(n, String(v)), getAttribute: n => attrs.get(n),
        addEventListener: (n, fn) => add(events, n, fn),
        dispatch(n, event = {}) { for (const fn of events.get(n) || []) fn(event); },
        getBoundingClientRect: () => ({ left: 0, top: 0, width: el.clientWidth, height: el.clientHeight }),
        getContext: () => drawing, focus() {}, select() {}, querySelector: () => null,
        setPointerCapture: n => captures.add(n), hasPointerCapture: n => captures.has(n), releasePointerCapture: n => captures.delete(n),
      };
      elements.set(id, el);
    }
    return elements.get(id);
  };
  const matchMedia = query => {
    if (!media.has(query)) {
      const listeners = [];
      media.set(query, {
        matches: query.includes('reduced-motion') ? reduced : query.includes('coarse') ? coarse : fine,
        addEventListener: (_, fn) => listeners.push(fn),
        change(value) { this.matches = value; listeners.forEach(fn => fn({ matches: value })); },
      });
    }
    return media.get(query);
  };
  const state = { 图谱: null, 图数据: null, 图请求: null, 当前标签: null, 当前: null };
  const context = vm.createContext({
    console, AbortController, Map, Set, performance, 状态: state, $: element,
    window: { devicePixelRatio: 1, matchMedia, addEventListener() {} },
    document: { hidden: false, documentElement: element('html'), addEventListener: (n, fn) => add(docEvents, n, fn) },
    getComputedStyle: () => ({ getPropertyValue: () => '#aabbcc' }),
    requestAnimationFrame: fn => { frames.set(++frameId, fn); return frameId; }, cancelAnimationFrame: id => frames.delete(id),
    ResizeObserver: class { constructor(fn) { resizes.push(fn); } observe() {} },
    IntersectionObserver: class { constructor(fn) { intersections.push(fn); } observe() {} },
    转义: text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    打开图谱节点: n => { if (n) opened.push(n.id); },
    知识库请求: (...args) => { requests.push(args); return Promise.resolve({ ok: true, json: async () => collectionGraph() }); },
    载入索引: async () => true, 应用地址路由: async () => {},
  });
  vm.runInContext(source, context, { filename: 'graph.js' });
  const api = vm.runInContext('({ 构建图谱, 图谱布局步进, 图谱布局未完成, 推进图谱布局, 适应图谱, 调整图谱尺寸, 绘制图谱, 请求图谱绘制, 停止图谱动画, 世界到屏幕, 屏幕到世界, 命中图谱节点, 按位置缩放, 缓动位置缩放, 缩放图谱, 图谱胶囊尺寸, 图谱条目排版, 图谱内容区域, 图谱显示条目标题, 图谱矩形相交, 显示图谱提示, 选中图谱节点, 渲染图谱节点列表, 切换图谱节点列表, 载入图谱, 绑定图谱事件 })', context);
  api.绑定图谱事件();
  const frame = (delta = 1000 / 60) => {
    clock += delta;
    const pending = [...frames]; frames.clear();
    for (const [, fn] of pending) fn(clock);
  };
  const flush = (limit = 120) => {
    let count = 0;
    while (frames.size && count < limit) { frame(); count++; }
    assert.equal(frames.size, 0, '没有交互后必须停止 RAF');
    return count;
  };
  const seed = (data = collectionGraph(), old = null) => {
    if (state.图谱) api.停止图谱动画();
    const graph = api.构建图谱(width, height, data, old);
    state.图谱 = graph; state.图数据 = data; api.调整图谱尺寸();
    return graph;
  };
  const visibility = hidden => { context.document.hidden = hidden; for (const fn of docEvents.get('visibilitychange') || []) fn(); };
  const intersect = visible => intersections.forEach(fn => fn([{ isIntersecting: visible }]));
  const pointer = (p, extra = {}) => ({ pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, clientX: p.x, clientY: p.y, ...extra });
  const wheel = (p, deltaY, deltaMode = 0) => element('graph-canvas').dispatch('wheel', { clientX: p.x, clientY: p.y, deltaY, deltaMode, preventDefault() {} });
  return { api, state, element, frames, frame, flush, seed, visibility, intersect, pointer, wheel, opened, requests, matchMedia, context, stats: () => ({ paints, measurements, textPaints }) };
}
function near(a, b, epsilon = 1e-7) { assert.ok(Math.abs(a - b) < epsilon, a + ' ≠ ' + b); }
function positions(graph) { return new Map(graph.节点.map(n => [n.id, [n.x, n.y]])); }
function assertPositions(graph, before) { for (const [id, [x, y]] of before) { near(graph.节点表.get(id).x, x); near(graph.节点表.get(id).y, y); } }

// 数据契约与稳定布局

test('只为有真实收藏夹归属和真实边的节点建立群组，不改变 ID/类型，不虚构语义线', () => {
  const h = harness(), data = collectionGraph(12, 3), g = h.seed(data);
  assert.equal(g.群组.length, 3); assert.equal(g.边.length, data.边.length);
  for (const n of g.节点) assert.equal(n.类型, data.节点.find(old => old.id === n.id).类型);
  for (const group of g.群组) { assert.equal(group.锚点.条目数, group.成员.length); assert.equal(group.锚点.群组锚点, true); }
  const bad = { 节点: data.节点, 边: [] };
  assert.equal(h.api.构建图谱(1050, 680, bad).群组.length, 0);
  assert.equal(h.api.构建图谱(1050, 680, { 节点: data.节点.map(({ 收藏夹, ...n }) => n), 边: data.边 }).群组.length, 0);
});

test('重复节点、重复/反向边、空边、自连线与悬空边均安全过滤', () => {
  const h = harness(), data = collectionGraph(1, 1), edge = data.边[0];
  const g = h.seed({ 节点: [...data.节点, data.节点[0], null, { id: 1 }], 边: [...data.边, edge, { 源: edge.目标, 目标: edge.源, 类型: edge.类型 }, null, { 源: edge.源, 目标: 'missing' }, { 源: edge.源, 目标: edge.源 }] });
  h.flush(); assert.equal(g.节点.length, 2); assert.equal(g.边.length, 1); assert.equal(g.群组[0].锚点.条目数, 1);
});

test('收藏夹和条目的输入顺序变化不会随机洗牌；群组之间留有清晰间距', () => {
  const h = harness(), data = collectionGraph(70, 7);
  const a = h.api.构建图谱(1050, 680, data);
  const b = h.api.构建图谱(1050, 680, { 节点: [...data.节点].reverse(), 边: [...data.边].reverse() });
  assertPositions(b, positions(a));
  for (let i = 0; i < a.群组.length; i++) for (let j = i + 1; j < a.群组.length; j++) {
    const x = a.群组[i], y = a.群组[j];
    assert.ok(Math.abs(x.锚点.x - y.锚点.x) >= x.半宽 + y.半宽 + 150 || Math.abs(x.锚点.y - y.锚点.y) >= x.半高 + y.半高 + 150);
  }
  const group = a.群组[0], radii = new Set(group.成员.map(n => Math.round(Math.hypot(n.x - group.锚点.x, n.y - group.锚点.y))));
  assert.ok(radii.size >= 7, '条目不应整齐落在同一个圆环上');
});

test('相同数据刷新保留坐标、手动镜头、选中和键盘节点；新增条目不推动旧节点', () => {
  const h = harness(), data = collectionGraph(18, 3), old = h.seed(data); h.flush();
  old.缩放 = .73; old.偏移x = -118; old.偏移y = 259; old.自动适应 = false;
  old.选中 = 'notes/2.md'; old.键盘节点 = 'notes/3.md';
  const before = positions(old), refreshed = h.seed(data, old); h.flush();
  assertPositions(refreshed, before); near(refreshed.缩放, .73); near(refreshed.偏移x, -118); near(refreshed.偏移y, 259);
  assert.equal(refreshed.选中, old.选中); assert.equal(refreshed.键盘节点, old.键盘节点);
  const extra = { id: 'notes/new.md', 标题: '新增条目', 类型: '文章', 收藏夹: data.节点[0].标题 };
  const next = h.seed({ 节点: [...data.节点, extra], 边: [...data.边, { 源: extra.id, 目标: data.节点[0].id, 类型: '标签' }] }, refreshed); h.flush();
  assertPositions(next, before); near(next.缩放, .73); near(next.偏移x, -118); near(next.偏移y, 259);
  assert.equal(next.选中, 'notes/2.md'); assert.equal(next.温度, 0);
});

test('刷新已删除节点时只清理失效选择，保留其余节点位置', () => {
  const h = harness(), data = collectionGraph(6, 1), old = h.seed(data); h.flush();
  old.选中 = 'notes/2.md'; old.键盘节点 = old.选中;
  const next = h.seed({ 节点: data.节点.filter(n => n.id !== old.选中), 边: data.边 }, old); h.flush();
  assert.equal(next.选中, null); assert.equal(next.键盘节点, null); assert.equal(next.边.length, 5);
  for (const n of next.节点) { near(n.x, old.节点表.get(n.id).x); near(n.y, old.节点表.get(n.id).y); }
});

test('旧式文章/分节/概念图保持原类型和原布局分支，更新不扰动旧坐标', () => {
  const h = harness(), data = { 节点: [{ id: 'a', 类型: '文章' }, { id: 's', 类型: '分节' }, { id: 'c', 类型: '概念' }, { id: 'tag:t', 类型: '标签' }], 边: [{ 源: 'a', 目标: 's', 类型: '文章线' }, { 源: 's', 目标: 'tag:t', 类型: '标签' }, { 源: 'c', 目标: 'tag:t', 类型: '概念页' }] };
  const old = h.seed(data); h.flush(); const before = positions(old);
  assert.equal(old.群组.length, 0); assert.deepEqual(Array.from(old.节点, n => n.类型), ['文章', '分节', '概念', '标签']);
  const next = h.seed({ 节点: [...data.节点, { id: 's2', 类型: '分节' }], 边: [...data.边, { 源: 'a', 目标: 's2', 类型: '文章线' }] }, old); h.flush();
  assertPositions(next, before);
});

// 真实尺寸、标签层级、适配与命中

for (const width of [1050, 338, 268]) for (const groups of [1, 3]) {
  test(width + 'px 图谱 / ' + groups + ' 个收藏夹：长中文胶囊和可见标签不会被适配裁切', () => {
    const h = harness({ width, height: 580 }), g = h.seed(collectionGraph(15, groups, true)); h.flush();
    for (const p of g.画面.可见) {
      assert.ok(p.框.x >= 0 && p.框.x + p.框.w <= width && p.框.y >= 0 && p.框.y + p.框.h <= 580, p.n.id);
      if (p.标题框) assert.ok(p.标题框.x >= 0 && p.标题框.x + p.标题框.w <= width);
      if (p.胶囊) assert.equal(h.api.命中图谱节点(g, { x: p.框.x + 7, y: p.y }), p.n, '胶囊侧边不是圆点，仍然必须可点');
    }
  });
}

test('桌面密集远景的收藏夹胶囊就近二维避让，名称可见且不改变真实节点坐标', () => {
  const h = harness({ width: 1190, height: 456 }), g = h.seed(collectionGraph(490, 10, true)); h.flush();
  const before = positions(g), capsules = [...g.画面.坐标.values()].filter(p => p.n.群组锚点);
  assert.equal(capsules.length, 10);
  assert.ok(capsules.every(p => p.胶囊), '利用横向留白，而不是过早隐藏收藏夹名称');
  assert.ok(capsules.some(p => Math.abs(p.n.胶囊偏移.x) > 30), '密集远景允许横向避让');
  for (const p of capsules) {
    assert.equal(h.api.命中图谱节点(g, p).id, p.n.id);
    assert.ok(Math.hypot(p.n.胶囊偏移.x, p.n.胶囊偏移.y) <= p.胶囊.w + 25, '标题避让不超过约一个胶囊宽度');
  }
  const rendered = new Map(capsules.map(p => [p.n.id, [p.x, p.y]]));
  h.api.请求图谱绘制(); h.flush(); assertPositions(g, before);
  for (const p of capsules) { const now = g.画面.坐标.get(p.n.id); near(now.x, rendered.get(p.n.id)[0]); near(now.y, rendered.get(p.n.id)[1]); }
});

test('空图、独立单节点与单收藏夹都可适配，所有视图数值有限', () => {
  for (const data of [{ 节点: [], 边: [] }, { 节点: [{ id: 'a', 标题: '独立节点', 类型: '文章' }], 边: [] }, collectionGraph(1, 1)]) {
    const h = harness({ width: 268, height: 500 }), g = h.seed(data); h.flush(); h.api.适应图谱(g, false);
    assert.ok([g.缩放, g.偏移x, g.偏移y].every(Number.isFinite));
  }
});

test('55% 以下只优先保留收藏夹；悬停、键盘、选中标题不受层级隐藏影响', () => {
  const h = harness(), g = h.seed(collectionGraph(9, 1)); h.flush();
  g.缩放 = .4; g.偏移x = 480; g.偏移y = 300; h.api.绘制图谱(g);
  assert.equal(g.画面.可见.filter(p => p.标题框).length, 0);
  g.悬停 = 'notes/0.md'; g.键盘节点 = 'notes/1.md'; g.选中 = 'notes/2.md'; h.api.绘制图谱(g);
  for (const id of [g.悬停, g.键盘节点, g.选中]) assert.ok(g.画面.坐标.get(id).标题框, id);
  g.悬停 = g.键盘节点 = g.选中 = null; g.缩放 = 1.3; h.api.绘制图谱(g);
  assert.ok(g.画面.可见.filter(p => p.标题框).length >= 5);
});

test('可见标题的文字区域可以单击；普通标签避让其他标题和节点胶囊', () => {
  const h = harness(), g = h.seed(collectionGraph(14, 1)); h.flush();
  const labeled = g.画面.可见.filter(p => p.标题框);
  assert.ok(labeled.length > 4);
  for (const p of labeled) {
    const b = p.标题框, hit = h.api.命中图谱节点(g, { x: b.x + b.w - 1, y: b.y + b.h / 2 });
    assert.equal(hit?.id, p.n.id);
    for (const q of labeled) if (p !== q) assert.equal(h.api.图谱矩形相交(b, q.标题框), false);
    for (const q of g.画面.可见) if (q.胶囊) assert.equal(h.api.图谱矩形相交(b, q.框), false);
  }
  const b = labeled[0].标题框, p = { x: b.x + b.w / 2, y: b.y + b.h / 2 }, canvas = h.element('graph-canvas');
  canvas.dispatch('pointerdown', h.pointer(p)); canvas.dispatch('pointerup', h.pointer(p));
  assert.deepEqual(h.opened, [labeled[0].n.id]);
});

test('窄分栏中选中条目优先寻找胶囊之间的空隙，绘制在胶囊之后且标题可点', () => {
  const h = harness({ width: 338, height: 340 }), g = h.seed(collectionGraph(2, 2, true)); h.flush();
  g.缩放 = 1; g.偏移x = g.偏移y = 0; g.自动适应 = false;
  g.群组[0].锚点.x = 170; g.群组[0].锚点.y = 115;
  g.群组[1].锚点.x = 170; g.群组[1].锚点.y = 200;
  for (const group of g.群组) { group.锚点.胶囊偏移 = null; group.成员[0].x = 200; group.成员[0].y = 161; }
  g.选中 = g.群组[0].成员[0].id; h.api.绘制图谱(g);
  const p = g.画面.坐标.get(g.选中), b = p.标题框;
  assert.ok(b);
  for (const q of g.画面.可见) if (q.胶囊) assert.equal(h.api.图谱矩形相交(b, q.框), false);
  assert.equal(h.stats().textPaints.at(-1).text, p.标题文本, '重要标题不能被后绘制的胶囊盖住');
  assert.equal(h.api.命中图谱节点(g, { x: b.x + b.w - 1, y: b.y + b.h / 2 }), p.n);
});

test('极窄且拥挤时重要标题仍在最上层，覆盖区域的命中与可见内容一致', () => {
  const h = harness({ width: 150, height: 180 }), g = h.seed(collectionGraph(3, 3, true)); h.flush();
  g.缩放 = 1; g.偏移x = g.偏移y = 0; g.自动适应 = false;
  for (const [i, group] of g.群组.entries()) {
    group.锚点.x = 75; group.锚点.y = 30 + i * 40; group.锚点.胶囊偏移 = null;
    group.成员[0].x = -300; group.成员[0].y = -300;
  }
  const n = g.群组[0].成员[0]; n.x = 75; n.y = 65; g.选中 = n.id; h.api.绘制图谱(g);
  const p = g.画面.坐标.get(n.id), b = p.标题框;
  const cover = g.画面.可见.find(q => q.胶囊 && h.api.图谱矩形相交(b, q.框));
  assert.ok(cover, '该压力样例必须覆盖无法完全避让的情况');
  const x = (Math.max(b.x, cover.框.x) + Math.min(b.x + b.w, cover.框.x + cover.框.w)) / 2;
  const y = (Math.max(b.y, cover.框.y) + Math.min(b.y + b.h, cover.框.y + cover.框.h)) / 2;
  assert.equal(h.api.命中图谱节点(g, { x, y }), n);
  assert.equal(h.stats().textPaints.at(-1).text, p.标题文本);
});

test('悬停提示只用已有标题/类型/关联数，窄屏内不挤入控制区，无网络请求', () => {
  const h = harness({ width: 268, height: 510 }), g = h.seed(collectionGraph(4, 1, true)); h.flush();
  const n = g.节点.find(n => n.所属群组), tip = h.element('graph-tooltip');
  h.api.显示图谱提示(n, { x: 260, y: 500 });
  assert.ok(tip.getAttribute('aria-label').includes(n.标题)); assert.match(tip.innerHTML, /文章 · 1 个关联/);
  assert.ok(parseFloat(tip.style.left) >= 0 && parseFloat(tip.style.left) + tip.offsetWidth <= 268);
  assert.ok(parseFloat(tip.style.top) + tip.offsetHeight <= h.api.图谱内容区域(g).y + h.api.图谱内容区域(g).h + 16);
  assert.equal(h.requests.length, 0);
});

test('矮分栏中的长提示受安全内容高度约束，不盖住底部控制区', () => {
  const h = harness({ width: 268, height: 180 }), g = h.seed(collectionGraph(1, 1, true)); h.flush();
  const tip = h.element('graph-tooltip'); tip.offsetHeight = 220;
  h.api.显示图谱提示(g.节点[1], { x: 240, y: 150 });
  const height = parseFloat(tip.style.maxHeight), bottom = h.api.图谱内容区域(g).y + h.api.图谱内容区域(g).h + 16;
  assert.ok(height < tip.offsetHeight); assert.ok(parseFloat(tip.style.top) + height <= bottom);
});

test('极长中文提示在矮分栏保留类型与关联数，视觉截断不丢失可访问全文', () => {
  const h = harness({ width: 250, height: 158 }), g = h.seed(collectionGraph(1, 1)); h.flush();
  const n = g.节点[1], tip = h.element('graph-tooltip');
  n.标题 = '思考、学习与设计的边界'.repeat(15) + '<img src=x onerror=alert(1)>';
  h.api.显示图谱提示(n, { x: 230, y: 110 });
  assert.ok(tip.getAttribute('aria-label').includes(n.标题));
  assert.match(tip.innerHTML, /class="graph-tooltip-title"/);
  assert.match(tip.innerHTML, /class="graph-tooltip-meta">文章 · 1 个关联/);
  assert.doesNotMatch(tip.innerHTML, /<img/); assert.match(tip.innerHTML, /&lt;img/);
  assert.equal(tip.style['--star-tooltip-lines'], '2');
  assert.match(css, /\.graph-panel \.graph-tooltip-meta\s*\{[^}]*flex-shrink:\s*0/);
  assert.equal(h.requests.length, 0);
});

test('键盘提示跟随本帧避让后的胶囊，而非未避让的世界坐标', () => {
  const h = harness({ width: 338, height: 350 }), g = h.seed(collectionGraph(2, 2)); h.flush();
  g.缩放 = 1; g.偏移x = g.偏移y = 0; g.自动适应 = false;
  for (const group of g.群组) { group.锚点.x = 170; group.锚点.y = 100; group.锚点.胶囊偏移 = null; }
  h.api.绘制图谱(g);
  const n = g.画面.可见.find(p => p.胶囊 && Math.abs(p.y - 100) > 1).n;
  g.选中 = g.节点[g.节点.indexOf(n) - 1].id;
  h.element('graph-canvas').dispatch('keydown', { key: 'ArrowRight', preventDefault() {} }); h.flush();
  assert.equal(g.键盘节点, n.id);
  const p = g.画面.坐标.get(n.id), tip = h.element('graph-tooltip'), actual = [tip.style.left, tip.style.top];
  assert.notEqual(p.y, h.api.世界到屏幕(g, n).y);
  h.api.显示图谱提示(n, p); assert.deepEqual([tip.style.left, tip.style.top], actual);
});

// 有限过渡与输入语义

test('滚轮短缓动每一帧都以鼠标为锚点；实际变换与画面命中同步', () => {
  const h = harness(), g = h.seed(collectionGraph(8, 1)); h.flush();
  const p = h.api.世界到屏幕(g, g.节点.find(n => n.所属群组)), world = h.api.屏幕到世界(g, p), before = g.缩放;
  h.wheel(p, -90); assert.ok(g.镜头); near(g.缩放, before);
  for (let i = 0; i < 12; i++) {
    h.frame(); const current = h.api.世界到屏幕(g, world); near(current.x, p.x); near(current.y, p.y);
    assert.equal(g.画面.缩放, g.缩放); assert.ok(h.api.命中图谱节点(g, p));
  }
  h.flush(); near(g.缩放, before * Math.exp(.225)); assert.equal(g.镜头, null);
});

test('连续滚轮不丢失目标倍率，像素/行/页模式均受缩放上下限约束', () => {
  const h = harness(), g = h.seed(); h.flush();
  const p = { x: 411, y: 230 }, before = g.缩放;
  for (let i = 0; i < 5; i++) h.wheel(p, -30);
  near(g.镜头.到.缩放, Math.min(4, before * Math.exp(.375))); h.flush();
  for (let i = 0; i < 40; i++) h.wheel(p, -100, 1);
  h.flush(); assert.equal(g.缩放, 4);
  for (let i = 0; i < 50; i++) h.wheel(p, 1, 2);
  h.flush(); near(g.缩放, .05);
});

test('适配采用约 280ms 镜头过渡，过渡中的单击仍打开当前画面下的同一个节点', () => {
  const h = harness(), g = h.seed(collectionGraph(10, 2)); h.flush();
  g.缩放 = .45; g.偏移x = 570; g.偏移y = 280; g.自动适应 = false; h.api.绘制图谱(g);
  h.api.适应图谱(); assert.equal(g.镜头.时长, 280); h.frame(); h.frame(140);
  const current = g.画面.可见.find(p => p.胶囊 && p.x > 80 && p.x < 970 && p.y > 40 && p.y < 550);
  assert.ok(current);
  const canvas = h.element('graph-canvas'), transform = [g.缩放, g.偏移x, g.偏移y];
  canvas.dispatch('pointerdown', h.pointer(current)); assert.equal(g.镜头, null);
  canvas.dispatch('pointerup', h.pointer(current)); h.flush();
  assert.deepEqual(h.opened, [current.n.id]); assert.deepEqual([g.缩放, g.偏移x, g.偏移y], transform);
  h.api.适应图谱(); h.frame(); h.frame(280); assert.equal(g.镜头, null); assert.equal(g.自动适应, true); h.flush();
});

test('空白拖动为一对一平移，打断镜头；节点拖动、取消、丢捕获均无惯性和误打开', () => {
  for (const ending of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    const h = harness(), g = h.seed(collectionGraph(6, 1)); h.flush();
    const canvas = h.element('graph-canvas'), p = { x: 15, y: 15 }, original = [g.偏移x, g.偏移y];
    h.api.缩放图谱(1.2); canvas.dispatch('pointerdown', h.pointer(p)); assert.equal(g.镜头, null);
    canvas.dispatch('pointermove', h.pointer({ x: 58, y: 39 })); near(g.偏移x, original[0] + 43); near(g.偏移y, original[1] + 24);
    canvas.dispatch(ending, h.pointer({ x: 58, y: 39 })); h.flush();
    assert.deepEqual(h.opened, []); assert.equal(g.手势, null); assert.equal(canvas.hasPointerCapture(1), false);
    const n = g.节点.find(n => n.所属群组), point = h.api.世界到屏幕(g, n), start = [n.x, n.y];
    canvas.dispatch('pointerdown', h.pointer(point));
    canvas.dispatch('pointermove', h.pointer({ x: point.x + 40, y: point.y + 30 }));
    canvas.dispatch(ending, h.pointer({ x: point.x + 40, y: point.y + 30 })); h.flush();
    near(n.x, start[0] + 40 / g.缩放); near(n.y, start[1] + 30 / g.缩放); assert.equal(g.温度, 0); assert.deepEqual(h.opened, []);
  }
});

test('拖出又拖回仍不算点击；副指针与右键不接管手势', () => {
  const h = harness(), g = h.seed(); h.flush();
  const canvas = h.element('graph-canvas'), p = g.画面.可见[0];
  canvas.dispatch('pointerdown', h.pointer(p, { button: 2 })); assert.equal(g.手势, null);
  canvas.dispatch('pointerdown', h.pointer(p, { isPrimary: false })); assert.equal(g.手势, null);
  canvas.dispatch('pointerdown', h.pointer(p));
  canvas.dispatch('pointermove', h.pointer({ x: p.x + 20, y: p.y }, { pointerId: 2 })); assert.equal(g.手势.距离, 0);
  canvas.dispatch('pointermove', h.pointer({ x: p.x + 30, y: p.y })); canvas.dispatch('pointermove', h.pointer(p)); canvas.dispatch('pointerup', h.pointer(p));
  assert.deepEqual(h.opened, []); h.flush();
});

test('键盘保留所有节点的原顺序、Enter 打开、Home 适配与加减缩放入口', () => {
  const h = harness({ width: 268 }), g = h.seed(collectionGraph(9, 3)); h.flush();
  const canvas = h.element('graph-canvas'), visited = [];
  for (let i = 0; i < g.节点.length; i++) {
    canvas.dispatch('keydown', { key: 'ArrowRight', preventDefault() {} }); h.flush();
    visited.push(g.键盘节点); const p = g.画面.坐标.get(g.键盘节点);
    assert.ok(p.胶囊 || p.标题框); assert.ok(p.x >= 0 && p.x <= 268);
  }
  assert.deepEqual(visited, Array.from(g.节点, n => n.id));
  canvas.dispatch('keydown', { key: 'Enter', preventDefault() {} }); assert.equal(h.opened[0], g.节点.at(-1).id);
  canvas.dispatch('keydown', { key: 'Home', preventDefault() {} }); h.flush(); assert.equal(g.自动适应, true);
  const before = g.缩放; canvas.dispatch('keydown', { key: '+', preventDefault() {} }); h.flush(); near(g.缩放, before * 1.2);
});

test('节点列表保留原类型、完整标题、过滤和单击打开入口，HTML 内容被转义', () => {
  const h = harness(), data = collectionGraph(4, 1); data.节点[1].标题 = '<img src=x>中文标题'; const g = h.seed(data); h.flush();
  h.api.切换图谱节点列表(true); h.element('graph-node-filter').value = '中文标题'; h.api.渲染图谱节点列表();
  const results = h.element('graph-node-results'); assert.match(results.innerHTML, /&lt;img/); assert.match(results.innerHTML, /文章/);
  assert.doesNotMatch(results.innerHTML, /<img/); assert.match(h.element('graph-node-count').textContent, /1 \/ 5/);
  results.dispatch('click', { target: { closest: () => ({ dataset: { nodeId: g.节点[1].id } }) } });
  assert.equal(h.opened[0], g.节点[1].id); assert.equal(h.element('graph-node-list').hidden, true);
});

// 氛围独立、隐藏/触屏/减少动态效果

test('悬停以 180ms 展开关联光环；背景跟随不移动节点、不预读正文、不持续重画 Canvas', () => {
  const h = harness(), g = h.seed(collectionGraph(10, 2)); h.flush();
  const p = g.画面.可见.find(p => p.n.所属群组), before = positions(g), canvas = h.element('graph-canvas');
  canvas.dispatch('pointermove', h.pointer(p)); h.frame(); h.frame(90);
  assert.ok(g.焦点权重.get(p.n.id) > 0 && g.焦点权重.get(p.n.id) < 1); h.frame(90); h.flush();
  assert.equal(g.焦点权重.get(p.n.id), 1); assertPositions(g, before); assert.equal(h.requests.length, 0);
  const paints = h.stats().paints;
  canvas.dispatch('pointermove', h.pointer({ x: p.x + 1, y: p.y + 1 })); h.flush(); assert.equal(h.stats().paints, paints);
  assert.equal(h.element('graph-stage').dataset.ambient, 'on'); assert.notEqual(h.element('graph-stage').style['--star-pointer-x'], '0px');
  canvas.dispatch('pointerleave'); h.flush(); assert.equal(g.焦点权重.size, 0);
});

test('页面隐藏、图谱出视口和 display:none 尺寸变化均停止 Canvas 及背景跟随', () => {
  for (const mode of ['hidden', 'intersection', 'size']) {
    const h = harness(), g = h.seed(); h.flush();
    h.api.缩放图谱(1.3); h.element('graph-canvas').dispatch('pointermove', h.pointer({ x: 34, y: 65 }));
    assert.ok(h.frames.size > 0);
    if (mode === 'hidden') h.visibility(true);
    if (mode === 'intersection') h.intersect(false);
    if (mode === 'size') { h.element('graph-stage').clientWidth = 0; h.api.调整图谱尺寸(); }
    assert.equal(h.frames.size, 0); assert.equal(g.镜头, null); assert.equal(h.element('graph-stage').dataset.ambient, 'off');
    const before = h.stats().paints; h.api.请求图谱绘制(); assert.equal(h.frames.size, 0); assert.equal(h.stats().paints, before);
    if (mode === 'hidden') h.visibility(false);
    if (mode === 'intersection') h.intersect(true);
    if (mode === 'size') { h.element('graph-stage').clientWidth = 1050; h.api.调整图谱尺寸(); }
    h.flush(); assert.equal(h.element('graph-stage').dataset.ambient, 'on');
  }
});

test('手势中隐藏页面或图谱会释放捕获，返回后不延续旧拖动、不误开内容', () => {
  for (const mode of ['hidden', 'intersection', 'size']) {
    const h = harness(), g = h.seed(); h.flush();
    const canvas = h.element('graph-canvas'), p = g.画面.可见.find(p => p.n.所属群组);
    canvas.dispatch('pointerdown', h.pointer(p));
    canvas.dispatch('pointermove', h.pointer({ x: p.x + 28, y: p.y + 20 })); h.frame();
    const position = [p.n.x, p.n.y]; assert.ok(canvas.hasPointerCapture(1));
    if (mode === 'hidden') h.visibility(true);
    if (mode === 'intersection') h.intersect(false);
    if (mode === 'size') { h.element('graph-stage').clientHeight = 0; h.api.调整图谱尺寸(); }
    assert.equal(g.手势, null); assert.equal(canvas.hasPointerCapture(1), false); assert.equal(h.frames.size, 0);
    if (mode === 'hidden') h.visibility(false);
    if (mode === 'intersection') h.intersect(true);
    if (mode === 'size') { h.element('graph-stage').clientHeight = 680; h.api.调整图谱尺寸(); }
    canvas.dispatch('pointermove', h.pointer({ x: p.x + 88, y: p.y + 90 })); canvas.dispatch('pointerup', h.pointer(p)); h.flush();
    assert.deepEqual([p.n.x, p.n.y], position); assert.deepEqual(h.opened, []);
  }
});

test('减少动态设置及其运行中切换可停止过渡，保留立即缩放、点击、键盘等功能', () => {
  const h = harness(), g = h.seed(); h.flush(); h.api.缩放图谱(1.3); h.frame();
  h.matchMedia('(prefers-reduced-motion: reduce)').change(true); h.flush();
  assert.equal(g.镜头, null); assert.equal(g.焦点动画, null); assert.equal(h.element('graph-stage').dataset.ambient, 'off');
  const before = g.缩放; h.api.缩放图谱(1.1); near(g.缩放, before * 1.1); assert.equal(g.镜头, null); h.flush();
  const p = g.画面.可见[0]; h.element('graph-canvas').dispatch('pointermove', h.pointer(p)); h.flush(); assert.equal(g.焦点权重.get(p.n.id), 1);
  h.matchMedia('(prefers-reduced-motion: reduce)').change(false); h.flush(); assert.equal(h.element('graph-stage').dataset.ambient, 'on');
});

test('触屏/粗指针不启用持续氛围或鼠标跟随，触摸单击仍直接打开', () => {
  for (const coarse of [false, true]) {
    const h = harness({ coarse, fine: !coarse }), g = h.seed(); h.flush();
    const p = g.画面.可见[0], canvas = h.element('graph-canvas');
    canvas.dispatch('pointerdown', h.pointer(p, { pointerType: 'touch' }));
    assert.equal(h.element('graph-stage').dataset.ambient, 'off');
    canvas.dispatch('pointerup', h.pointer(p, { pointerType: 'touch' })); h.flush(); assert.equal(h.opened[0], p.n.id);
    canvas.dispatch('pointermove', h.pointer({ x: 22, y: 22 }, { pointerType: 'touch' })); h.flush();
    assert.equal(h.element('graph-stage').style['--star-pointer-x'], '0px');
  }
});

test('CSS 氛围与 Canvas 分离、无装饰散点，主题/窄屏/轻阅读淡入均保持局部作用域', () => {
  const starCss = css.slice(css.indexOf('/* 静夜星图'));
  assert.match(starCss, /:root\[data-theme="dark"\] \.graph-panel/);
  assert.match(starCss, /\.graph-stage::before, \.graph-stage::after/);
  assert.match(starCss, /pointer-events: none/); assert.match(starCss, /animation-play-state: paused/);
  assert.match(starCss, /12s/); assert.match(starCss, /16s/); assert.match(starCss, /prefers-reduced-motion: reduce/);
  assert.match(starCss, /@container knowledge-graph \(max-width: 480px\)/);
  assert.match(starCss, /\.has-reader \.graph-panel \.graph-node-list \{ max-height: calc\(100% - 16px\); top: 8px; \}/);
  assert.match(starCss, /\.has-reader \.graph-panel \.graph-find-input \{ padding: 6px 10px; flex-shrink: 0; \}/);
  assert.match(starCss, /body\.personal-workspace:has\(#graph-panel\) \.account-bar nav/);
  assert.match(starCss, /quiet-reader-in 180ms/); assert.doesNotMatch(starCss, /radial-gradient\(var\(--border\)/);
  assert.doesNotMatch(source, /dataset\.theme\s*=|localStorage\.setItem|Three\.|WebGL/);
});

// 大图：节点数包含收藏夹锚点。以真实逻辑配合可控时钟验证有限工作量。
for (const count of [500, 1000]) {
  test(count + ' 节点：布局分帧、视口裁剪、文字测量复用，连续拖放缩放后停止重画', t => {
    const h = harness(), data = collectionGraph(count - 5, 5), start = performance.now(), g = h.seed(data);
    assert.equal(g.节点.length, count); assert.equal(h.api.图谱布局未完成(g), true);
    assert.equal(h.stats().paints, 0, '初次大型布局不能用半完成的位置误导点击');
    const frames = h.flush(), initialMs = performance.now() - start;
    assert.ok(frames > 1 && frames < 50); assert.equal(g.节点.every(n => n.布局就绪), true);
    const measured = h.stats().measurements; h.api.绘制图谱(g); h.api.绘制图谱(g); assert.equal(h.stats().measurements, measured);
    const before = positions(g), frameCosts = [];
    g.自动适应 = false;
    for (let i = 0; i < 24; i++) {
      const begin = performance.now();
      h.wheel({ x: 530, y: 320 }, i % 4 === 0 ? 32 : -48); h.frame();
      frameCosts.push(performance.now() - begin);
    }
    h.flush(); assertPositions(g, before); assert.equal(g.镜头, null); assert.equal(g.动画, null);
    g.缩放 = 4; g.偏移x = 525; g.偏移y = 340; h.api.绘制图谱(g);
    assert.ok(g.画面.可见.length < count / 2, '放大后只为视口中的节点绘制标签与命中几何');
    frameCosts.sort((a, b) => a - b);
    t.diagnostic(JSON.stringify({ nodes: count, layoutFrames: frames, initialMs: +initialMs.toFixed(1), logicFrameMedianMs: +frameCosts[12].toFixed(2), logicFrameP95Ms: +frameCosts[22].toFixed(2), visibleAt4x: g.画面.可见.length }));
  });
}
