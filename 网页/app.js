/* ==========================================================================
   网页版 Obsidian · 前端逻辑
   ========================================================================== */

/* ─────────────────────────── 状态 ─────────────────────────── */

const 知识库存储 = window.WorkspaceAccess?.preferences || localStorage;
const 知识库请求 = (url, options) => window.WorkspaceAccess ? window.WorkspaceAccess.request(url, options) : fetch(url, options);

const 状态 = {
  索引: null,
  当前: null,
  当前标签: null,
  路由: null,
  标题到笔记: new Map(),
  路径到笔记: new Map(),
  正文缓存: new Map(),
  笔记请求序号: 0,
  折叠的收藏夹: new Set(JSON.parse(知识库存储.getItem('折叠') || '[]')),
  折叠的文章: new Set(JSON.parse(知识库存储.getItem('折叠文章') || '[]')),
  路径到文章: new Map(),
  搜索词: '',
  搜索结果: [],
  搜索选中: 0,
  搜索请求: null,
  图数据: null,
  图请求: null,
  图谱: null,
};

const $ = (id) => document.getElementById(id);
const 转义 = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ─────────────────────────── 启动 ─────────────────────────── */

document.addEventListener('DOMContentLoaded', 启动);

async function 启动() {
  if (window.WorkspaceAccess) {
    window.WorkspaceAccess.setCleanup(清空个人工作区);
    if (!(await window.WorkspaceAccess.initialize())) return;
  }
  配置Marked();
  绑定事件();
  初始化主题();
  window.addEventListener('hashchange', 应用地址路由);
  if (!(await 载入索引())) return;
  await Promise.all([载入图谱(), 应用地址路由()]);
}

function 清空个人工作区() {
  状态.笔记请求序号 += 1;
  取消搜索请求();
  状态.图请求?.控制器.abort();
  停止图谱动画();
  状态.索引 = null; 状态.当前 = null; 状态.当前标签 = null; 状态.路由 = null;
  状态.图数据 = null; 状态.图谱 = null; 状态.图请求 = null;
  状态.搜索结果 = []; 状态.搜索词 = ''; 状态.搜索请求 = null;
  for (const map of [状态.标题到笔记, 状态.路径到笔记, 状态.正文缓存, 状态.路径到文章,
    状态.折叠的收藏夹, 状态.折叠的文章]) map.clear();
  for (const id of ['tree', 'note-head', 'note-body', 'note-foot', 'tag-head', 'tag-notes',
    'search-results', 'tag-cloud', 'toc', 'related', 'backlinks', 'sec-meta', 'graph-node-results']) $(id).replaceChildren();
  for (const id of ['tab-note-label', 'graph-tooltip', 'graph-count', 'graph-announcement', 'status-left', 'brand-sub']) $(id).textContent = '';
  $('search-input').value = ''; $('graph-node-filter').value = '';
  const canvas = $('graph-canvas');
  canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
  document.title = '个人知识库 · 账号待核验';
}

function 读取位置() {
  try { return decodeURIComponent((location.hash || '').replace(/^#/, '')); }
  catch { return ''; }
}

function 写入位置(位置) {
  if (读取位置() !== 位置) location.hash = 位置 ? encodeURIComponent(位置) : '';
}

async function 应用地址路由() {
  const 位置 = 读取位置();
  if (位置 === 状态.路由) return;
  if (!位置 || 位置 === 'graph') return 打开图谱(false);
  if (位置.startsWith('tag:')) return 打开标签(位置.slice(4), false);
  if (状态.路径到笔记.has(位置)) return 打开笔记(位置, false);
  提示('这个链接对应的笔记不在当前知识库中');
  return 打开图谱(false);
}

/* ─────────────────────────── Markdown ─────────────────────────── */

const 高亮扩展 = {
  name: 'mark',
  level: 'inline',
  start(src) { return src.indexOf('=='); },
  tokenizer(src) {
    const m = /^==(?=[^\s=])([\s\S]*?[^\s=])==/.exec(src);
    if (m) {
      const token = { type: 'mark', raw: m[0], text: m[1], tokens: [] };
      token.tokens = this.lexer.inlineTokens(m[1]);
      return token;
    }
  },
  renderer(token) { return `<mark>${this.parser.parseInline(token.tokens)}</mark>`; },
};

function 配置Marked() {
  marked.setOptions({ gfm: true, breaks: false, mangle: false });
  marked.use({ extensions: [高亮扩展] });
}

function 拆frontmatter(文本) {
  if (!文本.startsWith('---')) return [{}, 文本];
  const m = 文本.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!m) return [{}, 文本];
  const fm = {};
  for (const 行 of m[1].split(/\r?\n/)) {
    const i = 行.indexOf(':');
    if (i <= 0) continue;
    const k = 行.slice(0, i).trim();
    let v = 行.slice(i + 1).trim();
    if (/^\[.*\]$/.test(v)) {
      v = v.slice(1, -1).split(',').map((x) => x.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
    } else {
      v = v.replace(/^['"]|['"]$/g, '');
    }
    fm[k] = v;
  }
  return [fm, 文本.slice(m[0].length)];
}

/**
 * 预处理：Markdown 的链接地址默认不能含空格。
 * 笔记里是 assets/某篇 标题/001.png 这种带空格的中文路径，
 * 这里统一用尖括号包起来，marked 才能正确解析成图片。
 * 代码块与行内代码原样跳过，避免误伤。
 */
function 预处理图片路径(文本) {
  const 段 = 文本.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return 段.map((s, i) => {
    if (i % 2 === 1) return s;   // 代码段，不动
    return s.replace(/(!?\[[^\]]*\]\()([^)<>\n]*\s[^)<>\n]*)(\))/g, (m, 前, 路径, 后) => {
      const p = 路径.trim();
      if (/^[a-z]+:\/\//i.test(p) || /^</.test(路径)) return m;   // 网络地址 / 已包好
      return 前 + '<' + p + '>' + 后;
    });
  }).join('');
}

function 渲染Markdown(文本) {
  const html = marked.parse(预处理图片路径(文本));
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target', 'data-'], ALLOW_DATA_ATTR: true });
}

/* ─────────────────────────── 行内语法：双链 / 标签 ─────────────────────────── */

const 双链正则 = /\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]/g;
const 标签正则 = /(^|[\s(（【「>])(#[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_+\-.]{0,29})/g;

function 处理行内语法(root) {
  const 跳过 = new Set(['CODE', 'PRE', 'A', 'SCRIPT', 'STYLE', 'KBD', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6']);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      let p = n.parentElement;
      while (p && p !== root) {
        if (跳过.has(p.tagName)) return NodeFilter.FILTER_REJECT;
        p = p.parentElement;
      }
      双链正则.lastIndex = 0;
      标签正则.lastIndex = 0;
      return 双链正则.test(n.nodeValue) || 标签正则.test(n.nodeValue)
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });

  const 待处理 = [];
  while (walker.nextNode()) 待处理.push(walker.currentNode);

  for (const 节点 of 待处理) {
    const 原文 = 节点.nodeValue;
    const 片段 = document.createDocumentFragment();
    let 位置 = 0;
    双链正则.lastIndex = 0;
    标签正则.lastIndex = 0;

    // 用一个组合正则逐段切分
    const 组合 = /\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]|(^|[\s(（【「>])(#[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_+\-.]{0,29})/g;
    let m;
    while ((m = 组合.exec(原文))) {
      const 起点 = m.index + (m[3] ? m[3].length : 0);
      if (起点 > 位置) 片段.appendChild(document.createTextNode(原文.slice(位置, 起点)));

      if (m[1] !== undefined) {
        // 双链
        const 目标 = m[1].trim();
        const 显示 = (m[2] || m[1]).trim();
        const a = document.createElement('a');
        a.className = 'wikilink' + (状态.标题到笔记.has(目标) ? '' : ' missing');
        a.textContent = 显示;
        a.dataset.target = 目标;
        a.href = '#';
        片段.appendChild(a);
      } else if (m[4]) {
        // 标签
        const 名 = m[4].slice(1);
        const a = document.createElement('a');
        a.className = 'taglink';
        a.textContent = '#' + 名;
        a.dataset.tag = 名;
        a.href = '#';
        片段.appendChild(a);
      }
      位置 = 组合.lastIndex;
    }
    if (位置 < 原文.length) 片段.appendChild(document.createTextNode(原文.slice(位置)));
    if (位置 > 0) 节点.parentNode.replaceChild(片段, 节点);
  }
}

/* ─────────────────────────── 其他后处理 ─────────────────────────── */

function 修正图片(root, 目录) {
  root.querySelectorAll('img').forEach((img) => {
    let src = img.getAttribute('src') || '';
    if (!src) return;
    if (/^(https?:|data:)/i.test(src)) { img.loading = 'lazy'; return; }

    // DOMPurify 已经帮非 ASCII 字符编码过一次了，这里先解码回来，
    // 否则会变成 %25E6... 这种双重编码，图片直接 404。
    try { src = decodeURIComponent(src); } catch (e) { /* 含非法转义就原样用 */ }

    if (src.startsWith('/')) {
      img.src = src;
    } else {
      const 全 = (目录 ? 目录.replace(/\/+$/, '') + '/' : '') + src.replace(/^\/+/, '');
      img.src = '/笔记库/' + 全.split('/').filter(Boolean).map(encodeURIComponent).join('/');
    }
    img.loading = 'lazy';
    img.title = '点击查看原图';
    img.addEventListener('click', () => window.open(img.src, '_blank'));
  });
}

function 加标题锚点(root) {
  root.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach((h, i) => { h.id = 'h-' + i; });
}

function 处理Callout(root) {
  root.querySelectorAll('blockquote').forEach((bq) => {
    // 收集文本节点，找到第一个「非空白」的（blockquote 与 p 之间常有换行符）
    const 文本节点们 = [];
    const walker = document.createTreeWalker(bq, NodeFilter.SHOW_TEXT);
    let n, 首节点 = null, 首文本 = '';
    while ((n = walker.nextNode())) {
      文本节点们.push(n);
      if (!首节点 && n.nodeValue && n.nodeValue.trim()) { 首节点 = n; 首文本 = n.nodeValue; }
    }
    if (!首节点) return;

    const m = 首文本.match(/^(\s*)\[!(\w+)\][+-]?\s*([^\n]*)/);
    if (!m) return;

    bq.classList.add('callout');
    // 只摘掉 [!info] 那段标记，保留后面的正文
    首节点.nodeValue = 首文本.slice(m[0].length).replace(/^\s+/, '');

    const 标题 = document.createElement('strong');
    标题.textContent = (m[3] || m[2]).trim();
    const 换行 = document.createElement('br');
    首节点.parentNode.insertBefore(标题, 首节点);
    首节点.parentNode.insertBefore(换行, 首节点);
  });
}

function 渲染公式(root) {
  if (typeof renderMathInElement !== 'function') return;
  try {
    renderMathInElement(root, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '\\[', right: '\\]', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
      ],
      ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
      throwOnError: false,
    });
  } catch (e) { /* 忽略公式错误 */ }
}

function 高亮代码(root) {
  root.querySelectorAll('pre code').forEach((块) => {
    try { hljs.highlightElement(块); } catch (e) { /* 忽略 */ }
  });
}

/* ─────────────────────────── 索引与文件树 ─────────────────────────── */

async function 载入索引() {
  let 索引;
  try {
    const r = await 知识库请求('/api/索引');
    if (!r.ok) throw new Error('索引请求失败');
    索引 = await r.json();
    if (!索引 || !Array.isArray(索引.文章)) throw new Error('索引格式不正确');
  } catch (e) {
    if (!状态.索引) 显示空库();
    提示('索引加载失败，请确认本地服务仍在运行');
    return false;
  }
  状态.笔记请求序号 += 1;
  状态.索引 = 索引;
  const 笔记 = 展平索引(索引);
  状态.标题到笔记.clear();
  状态.路径到笔记.clear();
  状态.路径到文章.clear();
  for (const n of 笔记) {
    状态.标题到笔记.set(n.标题, n);
    状态.路径到笔记.set(n.路径, n);
  }
  for (const a of 索引.文章) {
    状态.路径到文章.set(a.路径, a.路径);
    for (const n of a.分节 || []) 状态.路径到文章.set(n.路径, a.路径);
  }
  状态.正文缓存.clear();
  渲染文件树();
  渲染标签云();
  更新状态栏();
  return true;
}

// 账号视图里一条记录可能是公开摘要快照、直答归并的知识块，或自己的创作；本地库则只有 MOC。
function 记录类型(文章) {
  if (文章.内容模式 === 'public_summary') return '公开摘要';
  if (文章.内容模式 === 'zhida_block') return '知识块';
  if (文章.内容模式 === 'creation') return '创作';
  return 'MOC';
}

function 展平索引(索引) {
  const 结果 = [];
  for (const a of 索引.文章 || []) {
    结果.push({
      ...a,
      标题: a.MOC标题,
      路径: a.路径,
      文章: a.标题,
      类型: 记录类型(a),
      层级: '文章',
      分节数: (a.分节 || []).length,
    });
    for (const s of a.分节 || []) {
      结果.push({
        ...s,
        收藏夹: a.收藏夹,
        作者: a.作者,
        原链接: a.原链接,
        文章: a.标题,
        类型: '分节',
        层级: '分节',
      });
    }
  }
  结果.push(...(索引.入口 || []));
  return 结果;
}

function 显示空库() {
  $('tree').innerHTML = '<div class="link-empty" style="padding:14px">暂时无法读取文件列表</div>';
  $('brand-sub').textContent = '连接失败';
  $('status-left').textContent = '无法连接本地知识库';
  显示图谱消息('知识库加载失败', '请确认本地服务仍在运行，然后重新加载。', true);
}

function 渲染文件树() {
  const 树 = $('tree');
  树.innerHTML = '';
  const 片段 = document.createDocumentFragment();

  const 分组 = new Map();
  for (const a of 状态.索引.文章 || []) {
    if (!分组.has(a.收藏夹)) 分组.set(a.收藏夹, []);
    分组.get(a.收藏夹).push(a);
  }

  const 顺序 = (状态.索引.收藏夹 || []).map((c) => c.名称);
  const 夹名们 = [...分组.keys()].sort((a, b) => {
    const ia = 顺序.indexOf(a), ib = 顺序.indexOf(b);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
  });

  for (const 夹名 of 夹名们) {
    const 条目 = 分组.get(夹名).slice().sort((a, b) => a.标题.localeCompare(b.标题, 'zh-CN'));

    const 折叠 = 状态.折叠的收藏夹.has(夹名);
    const 文件夹 = document.createElement('div');
    文件夹.className = 'tree-folder' + (折叠 ? ' collapsed' : '');
    文件夹.innerHTML = `
      <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
      <span class="label">${转义(夹名)}</span>
      <span class="count">${条目.length}</span>`;
    文件夹.setAttribute('role', 'button');
    文件夹.tabIndex = 0;
    文件夹.setAttribute('aria-expanded', String(!折叠));
    文件夹.dataset.toggle = 'collection';
    文件夹.dataset.key = 夹名;

    const 列表 = document.createElement('div');
    列表.className = 'tree-items';

    for (const a of 条目) {
      const 折叠文章 = 状态.折叠的文章.has(a.路径);
      const 文章夹 = document.createElement('div');
      文章夹.className = 'tree-article' + (折叠文章 ? ' collapsed' : '');
      文章夹.innerHTML = `
        <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        <span class="label">${转义(a.标题)}</span>
        <span class="count">${(a.分节 || []).length}</span>`;
      文章夹.title = `${a.标题}\n${a.作者 || ''}`;
      文章夹.setAttribute('role', 'button');
      文章夹.tabIndex = 0;
      文章夹.setAttribute('aria-expanded', String(!折叠文章));
      文章夹.dataset.toggle = 'article';
      文章夹.dataset.key = a.路径;

      const 分节列表 = document.createElement('div');
      分节列表.className = 'tree-sections';
      const 项们 = [{ 标题: 记录类型(a), 路径: a.路径, 类型: 记录类型(a) }, ...(a.分节 || [])];
      for (const n of 项们) {
        const 项 = document.createElement('button');
        项.type = 'button';
        项.title = n.标题;
        项.className = 'tree-item type-' + (n.类型 || '分节');
        项.dataset.路径 = n.路径;
        项.innerHTML = `<span class="dot"></span><span class="label">${转义(n.类型 === 'MOC' ? 'MOC · 文章概览' : n.标题.split(' · ').pop())}</span>`;
        分节列表.appendChild(项);
      }
      列表.appendChild(文章夹);
      列表.appendChild(分节列表);
    }

    片段.appendChild(文件夹);
    片段.appendChild(列表);
  }
  if ((状态.索引.入口 || []).length) {
    const 标题 = document.createElement('div');
    标题.className = 'sidebar-head';
    标题.textContent = '概念与导航';
    片段.appendChild(标题);
    for (const n of 状态.索引.入口) {
      const 项 = document.createElement('button');
      项.type = 'button';
      项.className = 'tree-item tree-entry';
      项.dataset.路径 = n.路径;
      项.title = n.标题;
      项.innerHTML = `<span class="dot"></span><span class="label">${转义(n.标题)}</span>`;
      片段.appendChild(项);
    }
  }
  树.appendChild(片段);
  if (!树.children.length) 树.innerHTML = window.WorkspaceAccess
    ? '<p class="link-empty">还没有收录摘要。请从顶部进入“收录公开摘要”，选择收藏夹后收录一页。</p>'
    : '<p class="link-empty">还没有笔记，导入后重新扫描即可。</p>';
}

function 绑定树事件() {
  $('tree').addEventListener('keydown', (e) => {
    if (['Enter', ' '].includes(e.key) && e.target.matches('[data-toggle]')) { e.preventDefault(); e.target.click(); }
  });
  $('tree').addEventListener('click', (e) => {
    const item = e.target.closest('.tree-item');
    if (item) {
      e.stopPropagation();
      打开笔记(item.dataset.路径);
      return;
    }
    const toggle = e.target.closest('[data-toggle]');
    if (!toggle) return;
    e.stopPropagation();
    const kind = toggle.dataset.toggle;
    const key = toggle.dataset.key;
    const set = kind === 'collection' ? 状态.折叠的收藏夹 : 状态.折叠的文章;
    const storage = kind === 'collection' ? '折叠' : '折叠文章';
    if (set.has(key)) {
      set.delete(key);
      toggle.classList.remove('collapsed');
    } else {
      set.add(key);
      toggle.classList.add('collapsed');
    }
    toggle.setAttribute('aria-expanded', String(!set.has(key)));
    知识库存储.setItem(storage, JSON.stringify([...set]));
  });
}

function 高亮树项(路径) {
  document.querySelector('.tree-item.active')?.classList.remove('active');
  const target = document.querySelector(`.tree-item[data-路径="${CSS.escape(路径)}"]`);
  if (!target) return;

  const 夹 = 状态.路径到笔记.get(路径)?.收藏夹;
  const 夹节点 = document.querySelector(`.tree-folder[data-key="${CSS.escape(夹)}"]`);
  if (夹 && 夹节点) {
    状态.折叠的收藏夹.delete(夹);
    夹节点.classList.remove('collapsed');
    夹节点.setAttribute('aria-expanded', 'true');
  }

  const 文章 = 状态.路径到文章.get(路径);
  const 文章节点 = document.querySelector(`.tree-article[data-key="${CSS.escape(文章)}"]`);
  if (文章 && 文章节点) {
    状态.折叠的文章.delete(文章);
    文章节点.classList.remove('collapsed');
    文章节点.setAttribute('aria-expanded', 'true');
  }

  知识库存储.setItem('折叠', JSON.stringify([...状态.折叠的收藏夹]));
  知识库存储.setItem('折叠文章', JSON.stringify([...状态.折叠的文章]));
  target.classList.add('active');
  target.scrollIntoView({ block: 'nearest' });
}

function 更新状态栏() {
  const 统 = 状态.索引?.统计 || {};
  if (window.WorkspaceAccess) {
    // 个人视图里既可能是公开摘要快照，也可能是直答归并出的知识块，统一按「知识条目」计数。
    $('status-left').textContent = `${统.文章数 || 0} 条知识条目 · ${统.收藏夹数 || 0} 个收藏夹`;
    $('status-right').textContent = '账号隔离 · 非全文';
    $('sidebar-count').textContent = `${统.文章数 || 0} 条`;
    $('brand-sub').textContent = `${统.文章数 || 0} 条知识条目`;
    return;
  }
  $('status-left').textContent =
    `${统.文章数 || 0} 篇文章 · ${统.笔记数 || 0} 个分节 · ${统.收藏夹数 || 0} 个收藏夹 · ${统.标签数 || 0} 个标签`;
  $('status-right').textContent = '本地知识库 · 只读浏览';
  $('sidebar-count').textContent = `${统.文章数 || 0} 篇`;
  $('brand-sub').textContent = `${统.文章数 || 0} 篇 · ${统.笔记数 || 0} 节`;
}

/* ─────────────────────────── 工作区导航 ─────────────────────────── */

async function 打开图谱(更新地址 = true) {
  状态.笔记请求序号 += 1;
  状态.当前 = null;
  状态.当前标签 = null;
  状态.路由 = '';
  if (更新地址) 写入位置('');
  for (const id of ['reader', 'note', 'tag-view', 'reader-message', 'resizer-2', 'tab-note']) $(id).hidden = true;
  $('reader').setAttribute('aria-busy', 'false');
  $('workspace-panes').classList.remove('has-reader', 'reader-expanded');
  $('btn-reader-expand').setAttribute('aria-pressed', 'false');
  $('btn-reader-expand').title = '展开阅读';
  $('btn-reader-expand').setAttribute('aria-label', '展开阅读');
  $('tab-graph').classList.add('active');
  $('btn-home').classList.add('active');
  高亮树项('');
  隐藏右栏();
  清理大纲监听();
  选中图谱节点(null);
  切换图谱节点列表(false);
  $('progress').style.width = '0%';
  document.title = '关系图谱 · 知乎知识库';
  return 载入图谱();
}

function 显示阅读面板(标题, 类型) {
  $('reader').hidden = false;
  $('resizer-2').hidden = false;
  $('tab-note').hidden = false;
  $('workspace-panes').classList.add('has-reader');
  $('tab-note').classList.add('active');
  $('tab-graph').classList.remove('active');
  $('btn-home').classList.remove('active');
  $('tab-note-label').textContent = 标题;
  $('tab-note-title').title = 标题;
  $('reader-kind').textContent = 类型;
  $('btn-reader-details').disabled = true;
  $('content').scrollTop = 0;
  $('progress').style.width = '0%';
  document.title = 标题 + ' · 知乎知识库';
  清理大纲监听();
  隐藏右栏();
  if (window.innerWidth <= 760) 设置文件树开关(false);
  调整图谱尺寸();
}

function 隐藏右栏() {
  $('rail').hidden = true;
  $('btn-reader-details').setAttribute('aria-expanded', 'false');
  ['sec-meta', 'sec-toc', 'sec-related', 'sec-backlink'].forEach(id => $(id).style.display = 'none');
}

function 图谱节点入口(节点) {
  if (!节点) return null;
  if (节点.类型 === '标签' || 节点.id.startsWith('tag:')) {
    return { 类型: '标签', 标签: 节点.标题 || 节点.id.slice(4) };
  }
  return 状态.路径到笔记.has(节点.id) ? { 类型: '笔记', 路径: 节点.id } : null;
}

function 打开图谱节点(节点) {
  const 入口 = 图谱节点入口(节点);
  if (!入口) { 提示('节点对应的笔记暂不可用，请重新扫描知识库'); return; }
  切换图谱节点列表(false);
  if (入口.类型 === '标签') return 打开标签(入口.标签);
  return 打开笔记(入口.路径);
}

function 获取标签内容(标签) {
  const 所有 = [...状态.路径到笔记.values()];
  const 匹配 = n => (n.标签 || []).includes(标签);
  return {
    分节: 所有.filter(n => n.类型 === '分节' && 匹配(n)),
    概念: 所有.filter(n => n.类型 === '概念' && 匹配(n)),
  };
}

function 打开标签(标签, 更新地址 = true) {
  状态.笔记请求序号 += 1;
  状态.当前 = null;
  状态.当前标签 = 标签;
  状态.路由 = 'tag:' + 标签;
  if (更新地址) 写入位置(状态.路由);
  显示阅读面板('#' + 标签, '标签 · 关联分节');
  $('reader').setAttribute('aria-busy', 'false');
  $('note').hidden = true;
  $('reader-message').hidden = true;
  $('tag-view').hidden = false;
  高亮树项('');
  选中图谱节点('tag:' + 标签);
  渲染标签内容(标签);
  $('graph-announcement').textContent = '已打开标签 ' + 标签;
}

function 渲染标签内容(标签) {
  const { 分节, 概念 } = 获取标签内容(标签);
  const 分组 = new Map();
  for (const n of 分节) {
    const 文章 = 状态.路径到文章.get(n.路径) || n.文章 || n.路径;
    if (!分组.has(文章)) 分组.set(文章, []);
    分组.get(文章).push(n);
  }
  $('tag-head').innerHTML = `
    <span class="tag-eyebrow">标签索引</span>
    <h1>#${转义(标签)}</h1>
    <p class="tag-summary">${分节.length} 个分节，来自 ${分组.size} 篇文章。<br>沿着共同标签，继续探索相关笔记。</p>
    ${概念.map(n => `<a class="tag-concept" data-note="${转义(n.路径)}" href="#${encodeURIComponent(n.路径)}">打开概念笔记 · ${转义(n.标题)} ↗</a>`).join('')}`;
  $('tag-notes').innerHTML = [...分组.values()].map(条目 => `
    <section class="tag-group">
      <h2 class="tag-group-heading">${转义(条目[0].收藏夹 || '')} / ${转义(条目[0].文章 || '相关笔记')}</h2>
      ${条目.map(n => `<a class="tag-note" data-note="${转义(n.路径)}" href="#${encodeURIComponent(n.路径)}"><strong>${转义(n.标题.split(' · ').pop())} ↗</strong>${n.摘要 ? `<p>${转义(n.摘要)}</p>` : ''}</a>`).join('')}
    </section>`).join('') || '<p class="link-empty">这个标签暂时没有关联分节。导入笔记后可重新扫描知识库。</p>';
}

/* ─────────────────────────── 打开笔记 ─────────────────────────── */

async function 打开笔记(路径, 更新地址 = true) {
  const 笔记 = 状态.路径到笔记.get(路径);
  if (!笔记) { 提示('找不到这篇笔记'); return; }
  const 请求序号 = ++状态.笔记请求序号;
  状态.当前 = 笔记;
  状态.当前标签 = null;
  状态.路由 = 路径;
  if (更新地址) 写入位置(路径);
  显示阅读面板(笔记.标题, (笔记.类型 || '笔记') + ' · 阅读');
  $('reader').setAttribute('aria-busy', 'true');
  $('note').hidden = true;
  $('tag-view').hidden = true;
  $('reader-message').hidden = false;
  $('reader-message').innerHTML = '<p>正在读取笔记…</p>';
  高亮树项(路径);
  选中图谱节点(路径);

  let 原文 = 状态.正文缓存.get(路径);
  if (原文 === undefined) {
    try {
      const r = await 知识库请求('/api/笔记?路径=' + encodeURIComponent(路径));
      if (!r.ok) throw new Error('笔记请求失败');
      const j = await r.json();
      if (请求序号 !== 状态.笔记请求序号) return;
      原文 = j.内容 || '';
    } catch (e) {
      if (请求序号 === 状态.笔记请求序号) {
        $('reader-message').innerHTML = `<h2>笔记加载失败</h2><p>请确认本地服务和笔记文件仍然可用。</p><button class="text-btn" data-retry-note="${转义(路径)}">重新加载这篇笔记</button>`;
        $('reader').setAttribute('aria-busy', 'false');
        提示('笔记加载失败，请重试');
      }
      return;
    }
    状态.正文缓存.set(路径, 原文);
  }
  const [, 正文] = 拆frontmatter(原文);
  渲染笔记(正文, 笔记);
  $('reader-message').hidden = true;
  $('note').hidden = false;
  $('reader').setAttribute('aria-busy', 'false');
  $('btn-reader-details').disabled = false;
  $('graph-announcement').textContent = '已打开 ' + 笔记.标题;
}

function 渲染笔记(正文, 笔记) {
  // ── 头部
  const 元 = [
    笔记.类型 ? `<span class="chip kind-${转义(笔记.类型)}">${转义(笔记.类型)}</span>` : '',
    笔记.作者 ? `<span class="chip">${转义(笔记.作者)}</span>` : '',
    笔记.收藏时间 ? `<span class="chip">收藏于 ${转义(笔记.收藏时间)}</span>` : '',
    笔记.赞同数 ? `<span class="chip">赞同 ${Number(笔记.赞同数).toLocaleString()}</span>` : '',
    笔记.字数 ? `<span class="chip">${Number(笔记.字数).toLocaleString()} 字</span>` : '',
    笔记.原链接 ? `<a href="${转义(笔记.原链接)}" target="_blank" rel="noopener">知乎原文 ↗</a>` : '',
  ].filter(Boolean).join('');

  $('note-head').innerHTML = `
    <div class="note-breadcrumb">
      <a href="#" data-folder="${转义(笔记.收藏夹)}">${转义(笔记.收藏夹)}</a>
      <span class="sep">/</span>
      <span>${转义(笔记.文章 || 笔记.标题)}</span>
      ${笔记.层级 === '分节' ? `<span class="sep">/</span><span>${转义(笔记.标题.split(' · ').pop())}</span>` : ''}
    </div>
    <h1 class="note-title">${转义(笔记.标题)}</h1>
    <div class="note-meta">${元}</div>`;

  $('note-head').querySelector('[data-folder]')?.addEventListener('click', (e) => {
    e.preventDefault();
    const c = e.target.dataset.folder;
    状态.折叠的收藏夹.delete(c);
    知识库存储.setItem('折叠', JSON.stringify([...状态.折叠的收藏夹]));
    渲染文件树();
    document.querySelector(`.tree-folder .label`)?.scrollIntoView({ block: 'nearest' });
  });

  // ── 正文
  const 体 = $('note-body');
  体.innerHTML = 渲染Markdown(正文);

  // 正文开头若重复了一级标题，去掉（顶部已有大标题）
  const 首个H1 = 体.querySelector('h1');
  if (首个H1 && 首个H1.textContent.trim() === 笔记.标题.trim()) 首个H1.remove();

  加标题锚点(体);
  处理Callout(体);
  处理行内语法(体);
  修正图片(体, 笔记.路径.includes('/') ? 笔记.路径.slice(0, 笔记.路径.lastIndexOf('/')) : '');
  高亮代码(体);
  渲染公式(体);

  // ── 底部标签
  if (笔记.标签 && 笔记.标签.length) {
    $('note-foot').innerHTML = 笔记.标签.map((t) =>
      `<a class="taglink" data-tag="${转义(t)}" href="#">#${转义(t)}</a>`).join('');
  } else {
    $('note-foot').innerHTML = '';
  }

  生成右栏(笔记);
}

/* ─────────────────────────── 右栏 ─────────────────────────── */

function 生成右栏(笔记) {
  $('sec-meta').style.display = '';
  // 元信息
  const 行 = [
    ['收藏夹', 笔记.收藏夹],
    ['作者', 笔记.作者 || '—'],
    ['类型', 笔记.类型 || '—'],
    ['发布时间', 笔记.发布时间 || '—'],
    ['收藏时间', 笔记.收藏时间 || '—'],
    ['字数', (笔记.字数 || 0).toLocaleString() + ' 字'],
    ['赞同', Number(笔记.赞同数 || 0).toLocaleString()],
    ['评论', Number(笔记.评论数 || 0).toLocaleString()],
  ];
  $('sec-meta').innerHTML = `
    <div class="rail-title">信息</div>
    <div class="meta-card">
      ${行.map(([k, v]) => `<div class="meta-row"><span class="k">${k}</span><span class="v">${转义(v)}</span></div>`).join('')}
      ${笔记.原链接 ? `<div class="meta-row"><span class="k">原文</span><span class="v"><a href="${转义(笔记.原链接)}" target="_blank" rel="noopener">打开知乎 ↗</a></span></div>` : ''}
    </div>`;

  // 大纲
  const 容器 = $('toc');
  const 标题们 = [...document.querySelectorAll('#note-body h1,#note-body h2,#note-body h3,#note-body h4')];
  if (!标题们.length) {
    $('sec-toc').style.display = 'none';
  } else {
    $('sec-toc').style.display = '';
    容器.innerHTML = 标题们.map((h) =>
      `<a class="toc-item lv-${h.tagName[1]}" data-id="${h.id}">${转义(h.textContent)}</a>`).join('');
    容器.querySelectorAll('.toc-item').forEach((a) => {
      a.onclick = () => {
        const 目标 = document.getElementById(a.dataset.id);
        if (目标) $('content').scrollTo({ top: 目标.offsetTop - 16, behavior: 'smooth' });
      };
    });
    监听大纲滚动(标题们);
  }

  // 相关笔记
  const 相关 = (笔记.相关 || []).map((t) => 状态.标题到笔记.get(t)).filter(Boolean);
  $('sec-related').style.display = 相关.length ? '' : 'none';
  $('related').innerHTML = 相关.map((n) =>
    `<div class="link-item" data-路径="${转义(n.路径)}">${转义(n.标题)}<span class="sub">${转义(n.作者 || '')} · ${转义(n.收藏夹)}</span></div>`).join('');
  $('related').querySelectorAll('.link-item').forEach((el) => {
    el.onclick = () => 打开笔记(el.dataset.路径);
  });

  // 反向链接：谁的「相关」里有我
  const 反链 = [...状态.标题到笔记.values()].filter((n) =>
    n.路径 !== 笔记.路径 && (n.相关 || []).includes(笔记.标题));
  $('sec-backlink').style.display = 反链.length ? '' : 'none';
  $('backlinks').innerHTML = 反链.map((n) =>
    `<div class="link-item" data-路径="${转义(n.路径)}">${转义(n.标题)}<span class="sub">${转义(n.收藏夹)}</span></div>`).join('');
  $('backlinks').querySelectorAll('.link-item').forEach((el) => {
    el.onclick = () => 打开笔记(el.dataset.路径);
  });

  $('note-head').querySelector('.note-meta a[target]')?.addEventListener('click', (e) => e.stopPropagation());
}

let 大纲滚动回调 = null;
function 清理大纲监听() {
  if (大纲滚动回调) $('content').removeEventListener('scroll', 大纲滚动回调);
  大纲滚动回调 = null;
}

function 监听大纲滚动(标题们) {
  清理大纲监听();
  const 内容区 = $('content');
  大纲滚动回调 = () => {
    let 当前 = 标题们[0];
    for (const h of 标题们) {
      if (h.getBoundingClientRect().top - 内容区.getBoundingClientRect().top <= 70) 当前 = h;
      else break;
    }
    document.querySelectorAll('.toc-item').forEach(a => a.classList.toggle('active', a.dataset.id === 当前.id));
  };
  内容区.addEventListener('scroll', 大纲滚动回调, { passive: true });
  大纲滚动回调();
}

/* ─────────────────────────── 搜索 ─────────────────────────── */

let 搜索定时器 = null;

function 打开搜索(预填) {
  $('ov-search').classList.remove('hidden');
  const 框 = $('search-input');
  框.value = 预填 || '';
  框.focus();
  框.select();
  执行搜索(框.value);
}

function 取消搜索请求() {
  clearTimeout(搜索定时器);
  状态.搜索请求?.abort();
  状态.搜索请求 = null;
}

function 关闭搜索() {
  取消搜索请求();
  $('ov-search').classList.add('hidden');
}

async function 执行搜索(词) {
  取消搜索请求();
  状态.搜索词 = 词;
  状态.搜索结果 = [];
  状态.搜索选中 = 0;
  $('search-results').innerHTML = '';
  if (!词.trim()) return;
  const 请求 = new AbortController();
  状态.搜索请求 = 请求;
  try {
    const r = await 知识库请求('/api/搜索?q=' + encodeURIComponent(词), {
      signal: 请求.signal,
    });
    if (!r.ok) throw new Error('搜索请求失败');
    const j = await r.json();
    if (请求 !== 状态.搜索请求 || 请求.signal.aborted || 词 !== 状态.搜索词) return;
    状态.搜索结果 = j.结果 || [];
    状态.搜索选中 = 0;
    渲染搜索结果(词);
  } catch (e) {
    if (e.name === 'AbortError' || 请求 !== 状态.搜索请求 || 词 !== 状态.搜索词) return;
    $('search-results').innerHTML = '<div class="link-empty" style="padding:20px">搜索失败</div>';
  }
}

function 渲染搜索结果(词) {
  const 容器 = $('search-results');
  if (!状态.搜索结果.length) {
    容器.innerHTML = '<div class="link-empty" style="padding:26px;text-align:center">没有找到相关笔记</div>';
    return;
  }
  const 粉 = (s) => 转义(s).replace(new RegExp(词.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), (m) => `<em>${m}</em>`);
  容器.innerHTML = 状态.搜索结果.map((r, i) => `
    <div class="result${i === 状态.搜索选中 ? ' active' : ''}" data-路径="${转义(r.路径)}" data-i="${i}">
      <div class="r-title">${粉(r.标题)}${r.类型 ? `<span class="badge">${转义(r.类型)}</span>` : ''}</div>
      <div class="r-snippet">${粉(r.片段 || '')}</div>
      <div class="r-meta">
        <span>${转义(r.收藏夹)}</span>
        ${r.文章 ? `<span>${转义(r.文章)}</span>` : ''}
        ${r.作者 ? `<span>${转义(r.作者)}</span>` : ''}
        ${(r.标签 || []).slice(0, 4).map((t) => `<span>#${转义(t)}</span>`).join('')}
      </div>
    </div>`).join('');

  容器.querySelectorAll('.result').forEach((el) => {
    el.onclick = () => { 关闭搜索(); 打开笔记(el.dataset.路径); };
  });
}

function 按标签筛选(标签) {
  关闭();
  打开标签(标签);
}

/* ─────────────────────────── 标签云 ─────────────────────────── */

function 渲染标签云() {
  const 标签们 = 状态.索引.标签 || [];
  const 最大 = Math.max(1, ...标签们.map(t => t.数量 || 0));
  $('tag-cloud').innerHTML = 标签们.map(t => {
    const 号 = 12 + Math.round(((t.数量 || 0) / 最大) * 6);
    return `<a class="tag-pill taglink" data-tag="${转义(t.名称)}" href="#${encodeURIComponent('tag:' + t.名称)}" style="font-size:${号}px">#${转义(t.名称)}<b>${t.数量 || 0}</b></a>`;
  }).join('');
}

/* ─────────────────────────── 主题 ─────────────────────────── */

function 初始化主题() {
  应用主题(知识库存储.getItem('主题') || 'light');
}

function 应用主题(t) {
  document.documentElement.dataset.theme = t;
  $('hljs-light').disabled = t !== 'light';
  $('hljs-dark').disabled = t !== 'dark';
  知识库存储.setItem('主题', t);
  请求图谱绘制();
}

/* ─────────────────────────── 事件 ─────────────────────────── */

function 绑定事件() {
  绑定树事件();
  $('btn-theme').onclick = () =>
    应用主题(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');

  $('search-trigger').onclick = () => 打开搜索('');
  $('btn-graph').onclick = () => 打开图谱();
  $('btn-home').onclick = () => 打开图谱();
  $('tab-graph').onclick = () => 打开图谱();
  $('btn-close-note').onclick = () => 打开图谱();
  $('btn-reader-close').onclick = () => 打开图谱();
  $('btn-quick-search').onclick = () => 打开搜索('');
  $('btn-reader-expand').onclick = 切换展开阅读;
  $('tab-note-title').onclick = () => $('reader').focus({ preventScroll: true });
  $('btn-reader-details').onclick = () => {
    const 打开 = $('rail').hidden;
    $('rail').hidden = !打开;
    $('btn-reader-details').setAttribute('aria-expanded', String(打开));
  };
  $('btn-sidebar').onclick = () => 设置文件树开关(!document.body.classList.contains('sidebar-open'));
  if (window.innerWidth <= 760) 设置文件树开关(false);
  $('btn-collapse-tree').onclick = () => {
    const 路径们 = (状态.索引?.文章 || []).map(a => a.路径);
    const 折叠 = 路径们.some(p => !状态.折叠的文章.has(p));
    状态.折叠的文章 = new Set(折叠 ? 路径们 : []);
    知识库存储.setItem('折叠文章', JSON.stringify([...状态.折叠的文章]));
    渲染文件树();
  };
  绑定图谱事件();
  $('btn-tags').onclick = () => $('ov-tags').classList.remove('hidden');

  $('btn-rescan').onclick = async (e) => {
    const 按钮 = e.currentTarget;
    if (按钮.disabled) return;
    if (window.WorkspaceAccess) {
      按钮.disabled = true;
      try {
        if (await 载入索引()) {
          状态.路由 = null;
          await Promise.all([载入图谱(true), 应用地址路由()]);
          提示('已刷新当前账号的摘要库');
        }
      } finally { 按钮.disabled = false; }
      return;
    }
    按钮.disabled = true;
    按钮.classList.add('spin');
    提示('正在重新扫描笔记库…');
    try {
      const r = await 知识库请求('/api/重扫', { method: 'POST' });
      if (!r.ok) throw new Error('扫描请求失败');
      const j = await r.json();
      if (j.成功) {
        if (await 载入索引()) {
          状态.路由 = null;
          await Promise.all([载入图谱(true), 应用地址路由()]);
          提示(`已更新：${j.统计?.笔记数 || 0} 个分节`);
        }
      } else 提示(j.detail || '扫描失败，看下命令行窗口的提示');
    } catch (err) { 提示('扫描请求失败'); }
    finally { 按钮.classList.remove('spin'); 按钮.disabled = false; }
  };



  // 遮罩关闭
  document.querySelectorAll('.overlay').forEach((ov) => {
    ov.addEventListener('mousedown', (e) => {
      if (e.target === ov) 关闭();
    });
    ov.querySelectorAll('[data-close]').forEach((b) => (b.onclick = 关闭));
  });

  // 搜索输入
  $('search-input').addEventListener('input', (e) => {
    取消搜索请求();
    const v = e.target.value;
    状态.搜索词 = v;
    状态.搜索结果 = [];
    $('search-results').innerHTML = '';
    if (!v.trim()) 执行搜索(v);
    else 搜索定时器 = setTimeout(() => 执行搜索(v), 180);
  });

  // 键盘
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      打开搜索('');
      return;
    }
    if (e.key === 'Escape') {
      if (!$('ov-search').classList.contains('hidden') || !$('ov-tags').classList.contains('hidden')) 关闭();
      else if (!$('graph-node-list').hidden) { 切换图谱节点列表(false); $('btn-graph-list').focus(); }
      else if (!$('rail').hidden) { $('rail').hidden = true; $('btn-reader-details').setAttribute('aria-expanded', 'false'); }
      else if (!$('reader').hidden) { 打开图谱(); $('graph-canvas').focus(); }
      return;
    }

    // 搜索结果导航
    if (!$('ov-search').classList.contains('hidden')) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        状态.搜索选中 = Math.max(0, Math.min(状态.搜索结果.length - 1,
          状态.搜索选中 + (e.key === 'ArrowDown' ? 1 : -1)));
        渲染搜索结果(状态.搜索词);
        document.querySelector('.result.active')?.scrollIntoView({ block: 'nearest' });
      }
      if (e.key === 'Enter' && 状态.搜索结果[状态.搜索选中]) {
        关闭搜索();
        打开笔记(状态.搜索结果[状态.搜索选中].路径);
      }
    }
  });

  // 双链 / 标签点击（事件委托）
  document.addEventListener('click', (e) => {
    const 链 = e.target.closest('.wikilink');
    if (链) {
      e.preventDefault();
      const 目标 = 链.dataset.target;
      const 笔记 = 状态.标题到笔记.get(目标);
      if (笔记) 打开笔记(笔记.路径);
      else 提示(`笔记库里还没有「${目标}」`);
      return;
    }
    const 笔记链接 = e.target.closest('[data-note], [data-retry-note]');
    if (笔记链接) {
      e.preventDefault();
      打开笔记(笔记链接.dataset.note || 笔记链接.dataset.retryNote);
      return;
    }
    const 标 = e.target.closest('.taglink');
    if (标) {
      e.preventDefault();
      按标签筛选(标.dataset.tag);
    }
  });

  // 阅读进度
  $('content').addEventListener('scroll', () => {
    const c = $('content');
    const 总 = c.scrollHeight - c.clientHeight;
    $('progress').style.width = 总 > 0 ? (c.scrollTop / 总) * 100 + '%' : '0%';
  }, { passive: true });

  // 左栏宽度拖拽
  支持拖拽调宽($('resizer-1'), $('sidebar'), '--w-left', 200, 420, false);
  支持拖拽调宽($('resizer-2'), $('reader'), '--w-reader', 340, 1000, true);

}

function 设置文件树开关(打开) {
  document.body.classList.toggle('sidebar-open', 打开);
  $('btn-sidebar').setAttribute('aria-expanded', String(打开));
  调整图谱尺寸();
}

function 切换展开阅读() {
  const 展开 = $('workspace-panes').classList.toggle('reader-expanded');
  $('btn-reader-expand').setAttribute('aria-pressed', String(展开));
  $('btn-reader-expand').title = 展开 ? '恢复图谱分栏' : '展开阅读';
  $('btn-reader-expand').setAttribute('aria-label', 展开 ? '恢复图谱分栏' : '展开阅读');
  if (展开) 停止图谱动画();
  else 调整图谱尺寸();
}

function 支持拖拽调宽(条, 面板, 变量, 最小, 最大, 反向) {
  let 手势 = null;
  const 改宽 = (宽) => {
    const 上限 = 变量 === '--w-reader' ? Math.min(最大, $('workspace-panes').clientWidth * .7) : 最大;
    宽 = Math.max(Math.min(最小, 上限), Math.min(上限, 宽));
    document.documentElement.style.setProperty(变量, 宽 + 'px');
    条.setAttribute('aria-valuenow', String(Math.round(宽)));
    调整图谱尺寸();
  };
  条.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    手势 = { id: e.pointerId, x: e.clientX, 宽: 面板.getBoundingClientRect().width };
    条.setPointerCapture(e.pointerId);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  条.addEventListener('pointermove', e => {
    if (手势?.id === e.pointerId) 改宽(手势.宽 + (e.clientX - 手势.x) * (反向 ? -1 : 1));
  });
  const 结束 = e => {
    if (!手势 || 手势.id !== e.pointerId) return;
    手势 = null;
    if (条.hasPointerCapture(e.pointerId)) 条.releasePointerCapture(e.pointerId);
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  };
  条.addEventListener('pointerup', 结束);
  条.addEventListener('pointercancel', 结束);
  条.addEventListener('lostpointercapture', 结束);
  条.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();
    改宽(面板.getBoundingClientRect().width + (e.key === 'ArrowRight' ? 16 : -16) * (反向 ? -1 : 1));
  });
}

function 关闭() {
  关闭搜索();
  document.querySelectorAll('.overlay').forEach((ov) => ov.classList.add('hidden'));
}

let 提示定时器 = null;
function 提示(文字) {
  const el = $('toast');
  el.textContent = 文字;
  el.classList.remove('hidden');
  clearTimeout(提示定时器);
  提示定时器 = setTimeout(() => el.classList.add('hidden'), 2600);
}
