// Synthetic data only; no real accounts, cookies or network.
// Run: node --test 工具/tests/test_collections_frontend.cjs
'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../网页/oauth/collections.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

const 条目 = (编号, 覆盖 = {}) => ({
  模式: '公开摘要', 标题: `标题${编号}`, 作者: `作者${编号}`, 摘要: `摘要${编号} 的正文片段。`,
  原文链接: `https://www.zhihu.com/question/1/answer/${编号}`,
  赞同数: '2189', 评论数: '36', 收藏数: '12', 收录时间: 1757750400, 标签: [], 来源数: 0, ...覆盖,
});
const 载荷 = (收藏夹, 说明 = '快照不是全文。') => ({
  收藏夹, 说明,
  统计: {收藏夹数: 收藏夹.length,
         内容数: 收藏夹.reduce((和, 夹) => 和 + 夹.内容数, 0),
         知识块数: 收藏夹.reduce((和, 夹) => 和 + 夹.知识块数, 0)},
});

function harness(数据, {就绪 = true} = {}) {
  const elements = new Map(), 请求 = [], 偏好 = new Map();
  const element = (tag = 'div') => {
    const 自身 = {
      tag, children: [], text: '', className: '', hidden: false, disabled: false, dataset: {}, listeners: new Map(),
      get textContent() { return 自身.text + 自身.children.map(子 => 子.textContent ?? '').join(''); },
      set textContent(值) { 自身.text = String(值 ?? ''); 自身.children = []; },
      set innerHTML(_值) { throw new Error('卡片视图必须用 textContent，不许拼 HTML'); },
      append(...节点) { 自身.children.push(...节点); },
      replaceChildren(...节点) { 自身.text = ''; 自身.children = []; 自身.children.push(...节点); },
      addEventListener(名, 函数) { 自身.listeners.set(名, 函数); },
      setAttribute(名, 值) { 自身[名] = 值; },
      removeAttribute(名) { delete 自身[名]; },
      closest(选择器) {
        const 键 = 选择器.replace(/[[\]]/g, '').replace(/^data-/, '');
        return 自身.dataset && 自身.dataset[键] !== undefined ? 自身 : null;
      },
      classList: {
        add: 名 => { 自身.className = (自身.className + ' ' + 名).trim(); },
        remove: 名 => { 自身.className = 自身.className.split(' ').filter(x => x && x !== 名).join(' '); },
        toggle: (名, 开) => (开 ? 自身.classList.add(名) : 自身.classList.remove(名)),
        contains: 名 => (自身.className || '').split(' ').includes(名),
      },
      async click() { return 自身.listeners.get('click')?.(); },
    };
    return 自身;
  };
  for (const id of ['collections', 'collections-list', 'collections-count', 'collections-side-state',
                    'collections-title', 'collections-meta', 'collections-note', 'collections-cards',
                    'collections-more', 'collections-feedback', 'collections-empty', 'collections-theme',
                    'status-left', 'status-right',
                    // 来源切换与创作同步用到的新节点
                    'collections-source', 'collections-sync', 'collections-label', 'collections-side-label',
                    'collections-empty-title', 'collections-empty-copy', 'collections-empty-cta']) {
    const 元素 = element();
    // 与 workspace_ui.py 的初始状态一致：这几个在标记里就带 hidden。
    元素.hidden = ['collections-note', 'collections-more', 'collections-feedback', 'collections-empty',
                   'collections-sync'].includes(id);
    elements.set(id, 元素);
  }
  // 来源切换里有两个按钮，data-来源 决定读哪个端点（与 workspace_ui.py 的标记一致）。
  for (const 名 of ['收藏', '创作']) {
    const 按钮 = element('button');
    按钮.dataset = {来源: 名};
    按钮.className = 名 === '收藏' ? 'collections-source-item active' : 'collections-source-item';
    elements.get('collections-source').append(按钮);
  }
  const context = vm.createContext({
    document: {
      documentElement: {dataset: {}},
      getElementById: id => elements.get(id) ?? null,
      createElement: element,
    },
    window: {WorkspaceAccess: {
      initialize: async () => 就绪,
      request: url => { 请求.push(url); return Promise.resolve({ok: true, status: 200, json: async () => 数据}); },
      preferences: {getItem: 名 => 偏好.get(名) ?? null, setItem: (名, 值) => 偏好.set(名, String(值))},
      setCleanup: () => {},
    }},
  });
  vm.runInContext(source, context, {filename: 'collections.js'});
  const 取 = id => elements.get(id);
  const 找 = (根, 类名) => 全部(根, 类名)[0] ?? null;
  function 全部(节点, 类名, 结果 = []) {
    if ((节点.className || '').split(' ').includes(类名)) 结果.push(节点);
    for (const 子 of 节点.children || []) 全部(子, 类名, 结果);
    return 结果;
  }
  return {elements, 请求, 取, 找, 全部, 偏好};
}

test('渲染收藏夹列表与内容卡片，只把原文链接做成可点的', async () => {
  const h = harness(载荷([{id: '1006141608', 名称: '我的收藏', 内容数: 2, 知识块数: 0,
    最近收录: 1757750400, 内容: [条目(11), 条目(12)]}]));
  await flush(); await flush();
  assert.deepEqual(h.请求, ['/api/收藏夹'], '只读一个端点，不联网、不重复请求');
  assert.equal(h.取('collections-list').children.length, 1);
  assert.equal(h.找(h.取('collections-list'), 'collections-item').textContent.includes('我的收藏'), true);
  assert.equal(h.取('collections-title').textContent, '我的收藏');
  assert.match(h.取('collections-meta').textContent, /1006141608/);
  assert.match(h.取('status-left').textContent, /1 个收藏夹 · 已收录 2 条摘要/);

  const 卡片 = h.全部(h.取('collections-cards'), 'card');
  assert.equal(卡片.length, 2);
  assert.equal(h.找(卡片[0], 'card-title').textContent, '标题11');
  assert.match(h.找(卡片[0], 'card-byline').textContent, /作者11/);
  assert.match(h.找(卡片[0], 'card-byline').textContent, /收录于 2025-09-13/);
  assert.match(h.找(卡片[0], 'card-excerpt').textContent, /摘要11/);

  const 链接 = h.找(卡片[0], 'card-origin');
  assert.equal(链接.href, 'https://www.zhihu.com/question/1/answer/11');
  assert.equal(链接.target, '_blank');
  assert.equal(链接.rel, 'noopener noreferrer');
  // 互动数字只能看：整页除了「阅读原文」之外不该再有可点元素。
  assert.match(h.找(卡片[0], 'card-stats').textContent, /赞同 2189 · 评论 36 · 收藏 12/);
  const 可点 = 卡片.flatMap(卡 => h.全部(卡, 'card-origin')).length;
  assert.equal(可点, 2, '每张卡片只有一个可点入口');
  assert.equal(h.取('collections-more').hidden, true, '不足一页不显示加载更多');
  assert.equal(h.取('collections-empty').hidden, true);
  assert.equal(h.取('collections-note').textContent, '快照不是全文。');
});

test('知识块卡片标出来源数，没有互动数字就不显示统计', async () => {
  const h = harness(载荷([{id: '101', 名称: '我的收藏', 内容数: 0, 知识块数: 1, 最近收录: 0,
    内容: [条目(0, {模式: '知识块', 标题: '归并出来的主题', 作者: '', 赞同数: '', 评论数: '', 收藏数: '',
                     收录时间: 0, 来源数: 8, 标签: ['我的收藏']})]}]));
  await flush(); await flush();
  const 卡片 = h.全部(h.取('collections-cards'), 'card')[0];
  assert.match(h.找(卡片, 'card-byline').textContent, /由 8 条摘要归并/);
  assert.equal(h.找(卡片, 'card-stats'), null, '没有数字就不渲染统计行');
  assert.equal(h.全部(卡片, 'card-tag')[0].textContent, '我的收藏');
});

test('超过一页时先给 20 张，点「加载更多」再补上', async () => {
  const 内容 = Array.from({length: 25}, (_, i) => 条目(i + 1));
  const h = harness(载荷([{id: '1', 名称: '我的收藏', 内容数: 25, 知识块数: 0, 最近收录: 1, 内容}]));
  await flush(); await flush();
  assert.equal(h.全部(h.取('collections-cards'), 'card').length, 20);
  assert.equal(h.取('collections-more').hidden, false);
  await h.取('collections-more').click();
  assert.equal(h.全部(h.取('collections-cards'), 'card').length, 25);
  assert.equal(h.取('collections-more').hidden, true);
});

test('空库显示空态而不是空白页', async () => {
  const h = harness(载荷([]));
  await flush(); await flush();
  assert.equal(h.取('collections-empty').hidden, false);
  assert.equal(h.取('collections-cards').children.length, 0);
  assert.equal(h.取('collections-note').hidden, true);
  assert.match(h.取('status-left').textContent, /0 个收藏夹/);
});

test('标题与摘要一律当纯文本，绝不拼 HTML', async () => {
  const 脏 = '<img src=x onerror=alert(1)><script>alert(2)</script>';
  const h = harness(载荷([{id: '1', 名称: 脏, 内容数: 1, 知识块数: 0, 最近收录: 1,
    内容: [条目(1, {标题: 脏, 摘要: 脏})]}]));
  await flush(); await flush();
  const 卡片 = h.全部(h.取('collections-cards'), 'card')[0];
  assert.equal(h.找(卡片, 'card-title').textContent, 脏, '原样保留文本，交给 textContent 转义（innerHTML 会直接抛错）');
  assert.equal(h.找(卡片, 'card-excerpt').textContent, 脏);
});

test('账号没核验通过就不读数据', async () => {
  const h = harness(载荷([]), {就绪: false});
  await flush(); await flush();
  assert.deepEqual(h.请求, [], '门禁接管时一个请求都不发');
});
