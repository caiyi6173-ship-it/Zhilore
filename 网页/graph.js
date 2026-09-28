/* 静夜星图：收藏夹层次、Canvas 绘制与输入。导航和请求契约仍由 app.js 提供。 */

let 图谱尺寸观察器 = null;
let 图谱可见观察器 = null;
let 图谱在视口 = true;
let 图谱动态偏好 = null;
let 图谱触屏输入 = false;
let 图谱氛围帧 = null;
let 图谱雾光点 = null;
const 图谱字体 = '"DengXian", "PingFang SC", "Microsoft YaHei", sans-serif';

function 图谱哈希(文字) {
  let hash = 2166136261;
  for (const 字 of 文字) hash = Math.imul(hash ^ 字.charCodeAt(0), 16777619);
  return (hash >>> 0) / 4294967295;
}

function 构建图谱(宽, 高, 数据 = { 节点: [], 边: [] }, 旧图 = null) {
  const 节点表 = new Map(), 旧节点 = 旧图?.节点表 || new Map();
  for (const n of 数据.节点 || []) {
    if (!n || typeof n.id !== 'string' || 节点表.has(n.id)) continue;
    const 角度 = 图谱哈希(n.id) * Math.PI * 2;
    const 距离 = 130 + 图谱哈希(n.id + ':radius') * 230, 旧 = 旧节点.get(n.id);
    节点表.set(n.id, {
      ...n, 标题: String(n.标题 || n.id),
      x: 旧?.x ?? Math.cos(角度) * 距离, y: 旧?.y ?? Math.sin(角度) * 距离,
      vx: 0, vy: 0, 位置保留: !!旧, 布局就绪: true, 胶囊偏移: 旧?.胶囊偏移 || null,
      r: n.类型 === '文章' ? 8 : n.类型 === '概念' ? 6.5 : n.类型 === '标签' ? 4 + Math.min(3, Math.sqrt(Math.max(0, Number(n.数量) || 1))) : 4.8,
    });
  }
  const 节点 = [...节点表.values()], 邻接 = new Map(节点.map(n => [n.id, new Set()]));
  const 边 = [], 已有边 = new Set();
  for (const e of 数据.边 || []) {
    if (!e) continue;
    const a = 节点表.get(e.源), b = 节点表.get(e.目标);
    if (!a || !b || a === b) continue;
    const key = JSON.stringify([...[a.id, b.id].sort(), e.类型 || '']);
    if (已有边.has(key)) continue;
    已有边.add(key); 边.push({ a, b, 类型: e.类型 });
    邻接.get(a.id).add(b.id); 邻接.get(b.id).add(a.id);
  }
  const 群组表 = new Map();
  for (const n of 节点) {
    // 只有「收藏夹字段 + 对应标签节点 + 真实归属边」同时存在才升级为群组。
    if (typeof n.收藏夹 !== 'string' || !n.收藏夹) continue;
    const id = 'tag:' + n.收藏夹, 锚点 = 节点表.get(id);
    if (!锚点 || 锚点.类型 !== '标签' || !邻接.get(n.id).has(id)) continue;
    if (!群组表.has(id)) 群组表.set(id, { id, 锚点, 成员: [] });
    群组表.get(id).成员.push(n); n.所属群组 = id;
  }
  const 图 = {
    节点, 节点表, 边, 邻接, 群组表, 群组: [...群组表.values()], ctx: null, 动画: null,
    宽: 旧图?.宽 || 宽, 高: 旧图?.高 || 高,
    缩放: 旧图?.缩放 || 1, 偏移x: 旧图?.偏移x ?? 0, 偏移y: 旧图?.偏移y ?? 0,
    自动适应: 旧图?.自动适应 ?? true,
    选中: 节点表.has(旧图?.选中) ? 旧图.选中 : null,
    悬停: null, 键盘节点: 节点表.has(旧图?.键盘节点) ? 旧图.键盘节点 : null, 手势: null,
    温度: 旧图 ? 0 : .15, 镜头: null, 焦点权重: new Map(), 焦点目标: null, 焦点动画: null,
    文字缓存: new Map(), 排版缓存: new Map(), 布局队列: [], 布局游标: 0, 布局剩余: 0,
    命中网格: 创建图谱网格(96), 避让网格: 创建图谱网格(96), 布局网格: new Map(),
    已绘制: false, 画面: null, 时钟: 0,
  };
  if (图.群组.length) {
    准备收藏夹布局(图, 旧图);
    图.温度 = 0;
    if (节点.length <= 400) 推进图谱布局(图, Infinity);
  } else if (!旧图) {
    // 没有归属元数据的旧式本地图谱仍使用原来的文章/分节/概念布局。
    const 文章 = 节点.filter(n => n.类型 === '文章');
    文章.forEach((n, i) => {
      const 角度 = i / Math.max(1, 文章.length) * Math.PI * 2 - Math.PI / 2;
      n.x = Math.cos(角度) * 230; n.y = Math.sin(角度) * 195;
    });
    for (const e of 边.filter(e => e.类型 === '文章线')) {
      const 角度 = 图谱哈希(e.b.id) * Math.PI * 2;
      e.b.x = e.a.x + Math.cos(角度) * 100; e.b.y = e.a.y + Math.sin(角度) * 100;
    }
    for (const n of 节点.filter(n => n.类型 === '标签')) {
      const 关联 = [...邻接.get(n.id)].map(id => 节点表.get(id)).filter(n => n.类型 === '分节');
      if (!关联.length) continue;
      n.x = 关联.reduce((sum, a) => sum + a.x, 0) / 关联.length + (图谱哈希(n.id) - .5) * 110;
      n.y = 关联.reduce((sum, a) => sum + a.y, 0) / 关联.length + (图谱哈希(n.id + 'y') - .5) * 110;
    }
    if (节点.length > 400) 图.布局剩余 = 40;
    else for (let i = 0; i < 180; i++) 图谱布局步进(图, .85 * (1 - i / 180) + .15);
  }
  return 图;
}

function 创建图谱网格(大小) { return { 大小, 格子: new Map() }; }
function 清空图谱网格(网格) { for (const 格 of 网格.格子.values()) 格.length = 0; }
function 图谱矩形相交(a, b, 间距 = 0) {
  return a.x < b.x + b.w + 间距 && a.x + a.w + 间距 > b.x && a.y < b.y + b.h + 间距 && a.y + a.h + 间距 > b.y;
}
function 图谱网格加入(网格, 框, 值) {
  const d = 网格.大小;
  for (let x = Math.floor(框.x / d); x <= Math.floor((框.x + 框.w) / d); x++) {
    for (let y = Math.floor(框.y / d); y <= Math.floor((框.y + 框.h) / d); y++) {
      const key = x + ',' + y;
      if (!网格.格子.has(key)) 网格.格子.set(key, []);
      网格.格子.get(key).push({ 框, 值 });
    }
  }
}
function 图谱网格碰撞(网格, 框, 忽略 = null, 间距 = 0) {
  const d = 网格.大小;
  for (let x = Math.floor((框.x - 间距) / d); x <= Math.floor((框.x + 框.w + 间距) / d); x++) {
    for (let y = Math.floor((框.y - 间距) / d); y <= Math.floor((框.y + 框.h + 间距) / d); y++) {
      for (const 项 of 网格.格子.get(x + ',' + y) || []) {
        if (项.值 !== 忽略 && 图谱矩形相交(框, 项.框, 间距)) return true;
      }
    }
  }
  return false;
}

function 准备收藏夹布局(图, 旧图) {
  const 群组网格 = 创建图谱网格(400);
  const 排序 = 图.群组.slice().sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  for (const 组 of 排序) {
    const 旧组 = 旧图?.群组表?.get(组.id), 数量 = 组.成员.length;
    组.半宽 = Math.max(195, 100 + Math.sqrt(数量) * 43, 旧组?.半宽 || 0);
    组.半高 = Math.max(137, 64 + Math.sqrt(数量) * 32, 旧组?.半高 || 0);
    组.占位 = 创建图谱网格(60);
    组.锚点.群组锚点 = true; 组.锚点.条目数 = 数量;
    if (组.锚点.位置保留) {
      图谱网格加入(群组网格, { x: 组.锚点.x - 组.半宽 - 75, y: 组.锚点.y - 组.半高 - 75, w: 2 * 组.半宽 + 150, h: 2 * 组.半高 + 150 }, 组.id);
    }
    let 下一个序号 = 0;
    for (const n of 组.成员) {
      const 旧 = 旧图?.节点表.get(n.id);
      if (旧?.所属群组 === 组.id && Number.isFinite(旧.群内序号)) { n.群内序号 = 旧.群内序号; 下一个序号 = Math.max(下一个序号, n.群内序号 + 1); }
      if (n.位置保留) 图谱网格加入(组.占位, { x: n.x - 18, y: n.y - 18, w: 36, h: 36 }, n.id);
    }
    组.下一个序号 = 下一个序号;
  }
  let 候选 = 0;
  for (const 组 of 排序) {
    if (!组.锚点.位置保留) {
      图.布局队列.push(() => {
        // 非规则螺旋装箱；已有群组永远不参与重排，新增群组只寻找空地。
        let 框;
        do {
          const i = 候选++, 角 = i * 2.399963229728653;
          const r = Math.sqrt(i) * 265;
          组.锚点.x = Math.cos(角) * r * 1.15; 组.锚点.y = Math.sin(角) * r * .86;
          框 = { x: 组.锚点.x - 组.半宽 - 75, y: 组.锚点.y - 组.半高 - 75, w: 2 * 组.半宽 + 150, h: 2 * 组.半高 + 150 };
        } while (图谱网格碰撞(群组网格, 框));
        图谱网格加入(群组网格, 框, 组.id);
      });
    }
    for (const n of 组.成员.slice().sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0)) {
      if (n.群内序号 == null) n.群内序号 = 组.下一个序号++;
      if (n.位置保留) continue;
      n.布局就绪 = false;
      图.布局队列.push(() => {
        let 框, 次数 = 0, dx, dy;
        do {
          const 序 = n.群内序号 + 次数 * .73;
          const 角 = 序 * 2.399963229728653 + 图谱哈希(组.id) * 6.28 + (图谱哈希(n.id) - .5) * .32;
          const 距离 = Math.sqrt(.18 + .82 * ((序 * .754877666 + .31) % 1)) * (1 + Math.max(0, 次数 - 14) * .035);
          dx = Math.cos(角) * 组.半宽 * 距离; dy = Math.sin(角) * 组.半高 * 距离;
          n.x = 组.锚点.x + dx; n.y = 组.锚点.y + dy;
          框 = { x: n.x - 18, y: n.y - 18, w: 36, h: 36 }; 次数++;
        } while ((Math.abs(dx) < 115 && Math.abs(dy) < 48 || 图谱网格碰撞(组.占位, 框)) && 次数 < 96);
        n.布局就绪 = true; 图谱网格加入(组.占位, 框, n.id);
      });
    }
  }
}

function 图谱布局未完成(图) { return 图.布局游标 < 图.布局队列.length || 图.布局剩余 > 0; }
function 推进图谱布局(图, 预算 = 96) {
  let 数量 = 0;
  while (图.布局游标 < 图.布局队列.length && 数量++ < 预算) 图.布局队列[图.布局游标++]();
  if (图.布局游标 === 图.布局队列.length) { 图.布局队列.length = 0; 图.布局游标 = 0; }
  for (let i = 0; i < 2 && 图.布局剩余 > 0; i++) {
    图谱布局步进(图, .85 * 图.布局剩余 / 40 + .15); 图.布局剩余--;
  }
}

function 图谱布局步进(图, 温度) {
  if (图.群组.length) return;
  const 网格宽 = 155, 网格 = 图.布局网格;
  网格.clear();
  图.节点.forEach((n, i) => {
    n.index = i;
    const key = Math.floor(n.x / 网格宽) + ',' + Math.floor(n.y / 网格宽);
    if (!网格.has(key)) 网格.set(key, []);
    网格.get(key).push(n);
  });
  for (const a of 图.节点) {
    const x = Math.floor(a.x / 网格宽), y = Math.floor(a.y / 网格宽);
    for (let ix = x - 1; ix <= x + 1; ix++) for (let iy = y - 1; iy <= y + 1; iy++) {
      for (const b of 网格.get(ix + ',' + iy) || []) {
        if (b.index <= a.index) continue;
        let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
        if (d2 > 网格宽 * 网格宽) continue;
        if (d2 < .01) { dx = .1; dy = .1; d2 = .02; }
        const 距离 = Math.sqrt(d2), 力 = Math.min(8, 3300 / d2) * 温度;
        const fx = dx / 距离 * 力, fy = dy / 距离 * 力;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
  }
  for (const e of 图.边) {
    const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, 距离 = Math.max(1, Math.hypot(dx, dy));
    const 长度 = e.类型 === '文章线' ? 96 : e.类型 === '标签' ? 120 : e.类型 === '概念页' ? 75 : 230;
    const 力 = (距离 - 长度) * .006 * 温度, fx = dx / 距离 * 力, fy = dy / 距离 * 力;
    e.a.vx += fx; e.a.vy += fy; e.b.vx -= fx; e.b.vy -= fy;
  }
  for (const n of 图.节点) {
    if (n === 图.手势?.节点 || n.位置保留) { n.vx = n.vy = 0; continue; }
    n.vx = (n.vx - n.x * .0007 * 温度) * .76; n.vy = (n.vy - n.y * .0007 * 温度) * .76;
    n.x += n.vx; n.y += n.vy;
  }
}

function 世界到屏幕(图, 点) { return { x: 点.x * 图.缩放 + 图.偏移x, y: 点.y * 图.缩放 + 图.偏移y }; }
function 屏幕到世界(图, 点) { return { x: (点.x - 图.偏移x) / 图.缩放, y: (点.y - 图.偏移y) / 图.缩放 }; }
function 节点屏幕半径(图, 节点, 缩放 = 图.缩放) {
  return 节点.所属群组 ? Math.max(2.4, Math.min(6, 4.1 * Math.sqrt(缩放))) : Math.max(3, 节点.r * Math.sqrt(缩放));
}
function 图谱重要节点(图, n) { return n.id === 图.悬停 || n.id === 图.键盘节点 || n.id === 图.选中 || n.id === 图.右键节点; }
function 图谱内容区域(图) {
  const 边距 = 图.宽 < 480 ? 18 : 32, 矮 = 图.高 < 280;
  return { x: 边距, y: 矮 ? 14 : 28, w: Math.max(40, 图.宽 - 边距 * 2), h: Math.max(40, 图.高 - (矮 ? 68 : 图.宽 < 480 ? 142 : 112)) };
}
function 测量图谱文字(图, 文本, 字体) {
  const key = 字体 + '\0' + 文本;
  if (!图.文字缓存.has(key)) {
    if (图.ctx) 图.ctx.font = 字体;
    图.文字缓存.set(key, 图.ctx ? 图.ctx.measureText(文本).width : [...文本].length * 7.5);
  }
  return 图.文字缓存.get(key);
}
function 截短图谱文字(图, 文本, 字体, 最大宽) {
  const key = 字体 + ':' + 最大宽 + ':' + 文本;
  if (图.排版缓存.has(key)) return 图.排版缓存.get(key);
  let 结果 = 文本;
  if (测量图谱文字(图, 文本, 字体) > 最大宽) {
    const 字符 = [...文本]; let 低 = 0, 高 = 字符.length;
    while (低 < 高) {
      const 中 = Math.ceil((低 + 高) / 2);
      if (测量图谱文字(图, 字符.slice(0, 中).join('') + '…', 字体) <= 最大宽) 低 = 中;
      else 高 = 中 - 1;
    }
    结果 = 字符.slice(0, 低).join('') + '…';
  }
  const 排版 = { 文本: 结果, 宽: 测量图谱文字(图, 结果, 字体), 字体 };
  // 连续缩放使用离散字号，缓存随数据换代释放，不随动画帧无限增长。
  图.排版缓存.set(key, 排版); return 排版;
}
function 图谱胶囊尺寸(图, n) {
  const 字体 = '500 13px ' + 图谱字体, 数字字体 = '500 11px "Consolas", monospace';
  const 数字 = String(n.条目数), 数宽 = Math.max(12, 测量图谱文字(图, 数字, 数字字体));
  const 最大宽 = Math.min(264, Math.max(110, 图.宽 - 52), 图.宽 < 480 ? 182 : 264);
  const 标题 = 截短图谱文字(图, n.标题, 字体, 最大宽 - 数宽 - 53);
  return { ...标题, 数字, 数宽, 数字字体, w: 标题.宽 + 数宽 + 53, h: 32 };
}
function 图谱短标题(n) {
  const 标题 = n.类型 === '分节' ? n.标题.split(' · ').pop() : n.标题;
  return (n.类型 === '标签' && !n.群组锚点 ? '#' : '') + 标题;
}
function 图谱条目排版(图, n, 缩放 = 图.缩放) {
  const 字号 = 缩放 > 1.25 ? 13 : 12;
  const 字体 = '400 ' + 字号 + 'px ' + 图谱字体;
  const 排版 = 截短图谱文字(图, 图谱短标题(n), 字体, Math.min(图.宽 - 44, 图.宽 < 480 ? 184 : 246));
  return { ...排版, h: 字号 + 5 };
}
function 图谱显示条目标题(图, n, 缩放 = 图.缩放) {
  if (图谱重要节点(图, n)) return true;
  if (n.群组锚点) return false;
  if (n.所属群组) return 缩放 >= .55 && 图谱哈希(n.id + ':label') <= Math.min(1, (缩放 - .55) / .32);
  return 缩放 >= .27 || n.类型 !== '分节';
}

function 命中图谱节点(图, 点) {
  const 帧 = 图.画面;
  if (帧 && 帧.缩放 === 图.缩放 && 帧.偏移x === 图.偏移x && 帧.偏移y === 图.偏移y) {
    const key = Math.floor(点.x / 96) + ',' + Math.floor(点.y / 96);
    let 命中 = null, 优先 = -1, 最近 = Infinity;
    for (const { 框, 值 } of 图.命中网格.格子.get(key) || []) {
      if (点.x < 框.x || 点.x > 框.x + 框.w || 点.y < 框.y || 点.y > 框.y + 框.h) continue;
      const 距离 = Math.hypot(点.x - 值.x, 点.y - 值.y);
      if (值.优先 === 1 && 距离 > 值.命中半径) continue;
      if (值.优先 > 优先 || 值.优先 === 优先 && 距离 < 最近) { 命中 = 值.n; 优先 = 值.优先; 最近 = 距离; }
    }
    return 命中;
  }
  let 命中 = null, 最近 = Infinity;
  for (const n of 图.节点) {
    if (!n.布局就绪) continue;
    const p = 世界到屏幕(图, n), 距离 = Math.hypot(p.x - 点.x, p.y - 点.y);
    const 胶囊 = n.群组锚点 ? 图谱胶囊尺寸(图, n) : null;
    const 范围内 = 胶囊 ? Math.abs(p.x - 点.x) <= 胶囊.w / 2 && Math.abs(p.y - 点.y) <= 胶囊.h / 2 : 距离 <= Math.max(10, 节点屏幕半径(图, n) + 5);
    if (范围内 && 距离 < 最近) { 命中 = n; 最近 = 距离; }
  }
  return 命中;
}

function 按位置缩放(图, 倍率, 点) {
  图.镜头 = null;
  const 原点 = 屏幕到世界(图, 点);
  图.缩放 = Math.max(.05, Math.min(4, 图.缩放 * 倍率));
  图.偏移x = 点.x - 原点.x * 图.缩放; 图.偏移y = 点.y - 原点.y * 图.缩放;
  图.自动适应 = false;
}
function 读取图谱动态偏好() {
  if (!图谱动态偏好 && typeof window.matchMedia === 'function') 图谱动态偏好 = {
    减少: window.matchMedia('(prefers-reduced-motion: reduce)'),
    精细: window.matchMedia('(hover: hover) and (pointer: fine)'),
    触屏: window.matchMedia('(any-pointer: coarse)'),
  };
  return 图谱动态偏好;
}
function 图谱减少动态() { return 读取图谱动态偏好()?.减少.matches ?? true; }
function 设置图谱镜头(图, 目标, 时长, 锚点 = null) {
  if (图谱减少动态() || !图.ctx || !图.已绘制) {
    Object.assign(图, 目标); 图.镜头 = null;
  } else 图.镜头 = { 从: { 缩放: 图.缩放, 偏移x: 图.偏移x, 偏移y: 图.偏移y }, 到: 目标, 时长, 开始: null, 锚点 };
  请求图谱绘制();
}
function 缓动位置缩放(图, 倍率, 点) {
  const 缩放 = Math.max(.05, Math.min(4, (图.镜头?.锚点 ? 图.镜头.到.缩放 : 图.缩放) * 倍率));
  const 世界 = 屏幕到世界(图, 点);
  图.自动适应 = false;
  设置图谱镜头(图, { 缩放, 偏移x: 点.x - 世界.x * 缩放, 偏移y: 点.y - 世界.y * 缩放 }, 150, { 点, 世界 });
}
function 图谱适配边界(图, 缩放) {
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  for (const n of 图.节点) {
    if (!n.布局就绪) continue;
    let w, 上, 下;
    if (n.群组锚点) { const 胶囊 = 图谱胶囊尺寸(图, n); w = 胶囊.w / 2 + 5; 上 = 下 = 胶囊.h / 2 + 5; }
    else {
      const r = 节点屏幕半径(图, n, 缩放); w = 上 = 下 = r + 5;
      if (图谱显示条目标题(图, n, 缩放)) { const 排版 = 图谱条目排版(图, n, 缩放); w = Math.max(w, 排版.宽 / 2 + 4); 下 = r + 9 + 排版.h; }
    }
    x1 = Math.min(x1, n.x * 缩放 - w); x2 = Math.max(x2, n.x * 缩放 + w);
    y1 = Math.min(y1, n.y * 缩放 - 上); y2 = Math.max(y2, n.y * 缩放 + 下);
  }
  return { x1, y1, x2, y2 };
}
function 适应图谱(图 = 状态.图谱, 重绘 = true) {
  if (!图 || !图.节点.length || !图.宽 || !图.高) return;
  图.自动适应 = true;
  if (图谱布局未完成(图)) { if (重绘) 请求图谱绘制(); return; }
  const 区域 = 图谱内容区域(图); let 低 = .05, 高 = 1.5;
  for (let i = 0; i < 16; i++) {
    const 中 = (低 + 高) / 2, b = 图谱适配边界(图, 中);
    if (b.x2 - b.x1 <= 区域.w && b.y2 - b.y1 <= 区域.h) 低 = 中;
    else 高 = 中;
  }
  const b = 图谱适配边界(图, 低);
  const 目标 = { 缩放: 低, 偏移x: 区域.x + 区域.w / 2 - (b.x1 + b.x2) / 2, 偏移y: 区域.y + 区域.h / 2 - (b.y1 + b.y2) / 2 };
  if (重绘) { 隐藏图谱提示(); 设置图谱镜头(图, 目标, 280); }
  else { 图.镜头 = null; Object.assign(图, 目标); }
}

function 图谱可绘制() {
  const 舞台 = $('graph-stage');
  return !document.hidden && 图谱在视口 && 舞台.clientWidth > 0 && 舞台.clientHeight > 0;
}
function 调整图谱尺寸() {
  const 图 = 状态.图谱, 舞台 = $('graph-stage'), 画布 = $('graph-canvas');
  同步图谱氛围();
  if (!图 || !舞台.clientWidth || !舞台.clientHeight) { 中止图谱手势(); 停止图谱动画(); return; }
  const 中心 = 屏幕到世界(图, { x: 图.宽 / 2, y: 图.高 / 2 });
  const 首次上下文 = !图.ctx;
  图.镜头 = null; 图.宽 = 舞台.clientWidth; 图.高 = 舞台.clientHeight;
  图.像素比 = Math.min(3, window.devicePixelRatio || 1);
  const 像素宽 = Math.round(图.宽 * 图.像素比), 像素高 = Math.round(图.高 * 图.像素比);
  if (画布.width !== 像素宽 || 画布.height !== 像素高) { 画布.width = 像素宽; 画布.height = 像素高; }
  图.ctx = 画布.getContext('2d');
  if (首次上下文) { 图.文字缓存.clear(); 图.排版缓存.clear(); }
  if (图.自动适应) 适应图谱(图, false);
  else { 图.偏移x = 图.宽 / 2 - 中心.x * 图.缩放; 图.偏移y = 图.高 / 2 - 中心.y * 图.缩放; }
  隐藏图谱提示(); 请求图谱绘制();
}
function 停止图谱动画(图 = 状态.图谱) {
  if (图?.动画 != null) cancelAnimationFrame(图.动画);
  if (图) { 图.动画 = null; 图.镜头 = null; 图.焦点动画 = null; }
}
function 中止图谱手势(图 = 状态.图谱) {
  const 手势 = 图?.手势;
  if (!手势) return;
  图.手势 = null; 图.温度 = 0;
  if (手势.节点 && 手势.距离 > 5) 手势.节点.位置保留 = true;
  const 画布 = $('graph-canvas');
  if (画布.hasPointerCapture(手势.pointerId)) 画布.releasePointerCapture(手势.pointerId);
  画布.style.cursor = 'grab'; 隐藏图谱提示();
}
function 推进图谱镜头(图, 时间) {
  const 镜头 = 图.镜头;
  if (!镜头) return;
  镜头.开始 ??= 时间;
  const t = 图谱减少动态() ? 1 : Math.min(1, (时间 - 镜头.开始) / 镜头.时长), k = 1 - (1 - t) ** 3;
  for (const key of ['缩放', '偏移x', '偏移y']) 图[key] = 镜头.从[key] + (镜头.到[key] - 镜头.从[key]) * k;
  if (镜头.锚点) {
    图.偏移x = 镜头.锚点.点.x - 镜头.锚点.世界.x * 图.缩放;
    图.偏移y = 镜头.锚点.点.y - 镜头.锚点.世界.y * 图.缩放;
  }
  if (t === 1) 图.镜头 = null;
}
function 推进图谱焦点(图, 时间) {
  const 目标 = 图.悬停 || 图.键盘节点 || 图.选中 || null;
  if (目标 !== 图.焦点目标 || !图.焦点动画 && (目标 ? 图.焦点权重.get(目标) !== 1 : 图.焦点权重.size)) {
    图.焦点目标 = 目标;
    图.焦点动画 = { 从: new Map(图.焦点权重), 开始: 时间 };
  }
  const 动画 = 图.焦点动画;
  if (!动画) return;
  const t = 图谱减少动态() ? 1 : Math.min(1, (时间 - 动画.开始) / 180), k = 1 - (1 - t) ** 3;
  const ids = new Set([...动画.从.keys(), ...(目标 ? [目标] : [])]);
  图.焦点权重.clear();
  for (const id of ids) {
    const 起点 = 动画.从.get(id) || 0, 值 = 起点 + ((id === 目标 ? 1 : 0) - 起点) * k;
    if (值 > .001) 图.焦点权重.set(id, 值);
  }
  if (t === 1) 图.焦点动画 = null;
}
function 请求图谱绘制() {
  const 图 = 状态.图谱;
  if (!图 || !图.ctx || 图.动画 != null || !图谱可绘制()) return;
  图.动画 = requestAnimationFrame(时间戳 => {
    图.动画 = null;
    if (状态.图谱 !== 图 || !图谱可绘制()) return;
    const 时间 = Number.isFinite(时间戳) ? 时间戳 : 图.时钟 + 1000 / 60; 图.时钟 = 时间;
    if (图谱布局未完成(图)) {
      推进图谱布局(图);
      if (图谱布局未完成(图)) { 请求图谱绘制(); return; }
      if (图.自动适应) 适应图谱(图, false);
    }
    if (图.温度 > .01 && !图.手势) {
      图谱布局步进(图, 图.温度); 图.温度 *= .88;
      if (图.自动适应 && !图.镜头) 适应图谱(图, false);
    }
    推进图谱镜头(图, 时间); 推进图谱焦点(图, 时间); 绘制图谱(图);
    if (图.镜头 || 图.焦点动画 || 图.温度 > .01 && !图.手势) 请求图谱绘制();
  });
}

function 图谱圆角路径(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y);
  ctx.arc(x + w - r, y + r, r, -Math.PI / 2, 0); ctx.lineTo(x + w, y + h - r);
  ctx.arc(x + w - r, y + h - r, r, 0, Math.PI / 2); ctx.lineTo(x + r, y + h);
  ctx.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI); ctx.lineTo(x, y + r);
  ctx.arc(x + r, y + r, r, Math.PI, Math.PI * 1.5);
}
function 图谱颜色() {
  const 样式 = getComputedStyle($('graph-stage')), 根 = getComputedStyle(document.documentElement);
  const 色 = (名, 备选) => 样式.getPropertyValue(名).trim() || 根.getPropertyValue(备选 || 名).trim();
  return {
    背景: 色('--star-bg', '--bg'), 文字: 色('--star-text', '--text-sub'), 强调: 色('--star-accent', '--accent'),
    胶囊: 色('--star-capsule', '--bg'), 描边: 色('--star-border', '--border'), 数字: 色('--star-muted', '--text-faint'),
    文章: 色('--star-point', '--graph-article'), 分节: 色('--graph-section'), 标签: 色('--graph-tag'), 概念: 色('--graph-concept'), 线: 色('--star-edge', '--graph-edge'),
  };
}
function 图谱节点亮度(图, n) {
  let 总 = 0, 相关 = 0;
  for (const [id, 权重] of 图.焦点权重) { 总 += 权重; if (id === n.id || 图.邻接.get(id)?.has(n.id)) 相关 += 权重; }
  return n.id === 图.选中 ? 1 : 1 - Math.max(0, 总 - 相关) * .62;
}
function 图谱连线端点(p, q) {
  if (!p.胶囊) return p;
  const dx = q.x - p.x, dy = q.y - p.y;
  const t = 1 / Math.max(1, Math.abs(dx) / (p.胶囊.w / 2 + 2), Math.abs(dy) / (p.胶囊.h / 2 + 2));
  return { x: p.x + dx * t, y: p.y + dy * t };
}
function 绘制图谱连线(图, 坐标, 颜色) {
  const ctx = 图.ctx, 批次 = new Map();
  for (const e of 图.边) {
    const a = 坐标.get(e.a.id), b = 坐标.get(e.b.id);
    if (!a || !b || Math.max(a.x, b.x) < 0 || Math.min(a.x, b.x) > 图.宽 || Math.max(a.y, b.y) < 0 || Math.min(a.y, b.y) > 图.高) continue;
    const 权重 = Math.max(图.焦点权重.get(e.a.id) || 0, 图.焦点权重.get(e.b.id) || 0);
    if (!批次.has(权重)) 批次.set(权重, []);
    批次.get(权重).push([图谱连线端点(a, b), 图谱连线端点(b, a)]);
  }
  const 聚焦 = [...图.焦点权重.values()].reduce((a, b) => a + b, 0);
  for (const [权重, 线们] of 批次) {
    ctx.globalAlpha = 权重 ? .28 + .52 * 权重 : (图.群组.length ? .65 : .85) * (1 - 聚焦 * .62);
    ctx.strokeStyle = 权重 ? 颜色.强调 : 颜色.线; ctx.lineWidth = 权重 ? .8 + 权重 * .3 : .65;
    ctx.beginPath();
    for (const [a, b] of 线们) { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); }
    ctx.stroke();
  }
}
function 绘制图谱胶囊(图, p, 颜色) {
  const ctx = 图.ctx, n = p.n, c = p.胶囊, x = p.框.x, y = p.框.y;
  const 权重 = Math.max(图.焦点权重.get(n.id) || 0, n.id === 图.选中 ? .55 : 0);
  const 亮度 = 图谱节点亮度(图, n);
  if (权重) {
    ctx.globalAlpha = 权重 * .32; ctx.strokeStyle = 颜色.强调; ctx.lineWidth = 1;
    const 扩 = 3 + 权重 * 3; 图谱圆角路径(ctx, x - 扩, y - 扩, c.w + 扩 * 2, c.h + 扩 * 2, 16 + 扩); ctx.stroke();
  }
  ctx.globalAlpha = 亮度; ctx.fillStyle = 颜色.胶囊; ctx.strokeStyle = 权重 ? 颜色.强调 : 颜色.描边; ctx.lineWidth = 1;
  图谱圆角路径(ctx, x, y, c.w, c.h, 16); ctx.fill(); ctx.stroke();
  // 胶囊内的短划是锚点标记，不在画布上添加伪知识节点。
  ctx.strokeStyle = 颜色.强调; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x + 13, p.y - 3); ctx.lineTo(x + 13, p.y + 3); ctx.stroke();
  ctx.globalAlpha = Math.max(.85, 亮度);
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = c.字体; ctx.fillStyle = 颜色.文字; ctx.fillText(c.文本, x + 24, p.y + .5);
  const 分隔 = x + c.w - c.数宽 - 20;
  ctx.strokeStyle = 颜色.描边; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(分隔, p.y - 5); ctx.lineTo(分隔, p.y + 5); ctx.stroke();
  ctx.font = c.数字字体; ctx.fillStyle = 颜色.数字; ctx.fillText(c.数字, 分隔 + 9, p.y + .5);
}
function 绘制图谱光点(图, p, 颜色) {
  const ctx = 图.ctx, n = p.n, 亮度 = 图谱节点亮度(图, n);
  const 权重 = Math.max(图.焦点权重.get(n.id) || 0, n.id === 图.选中 ? .6 : 0);
  const 色 = 权重 ? 颜色.强调 : 颜色[n.类型] || 颜色.分节;
  if (权重 || 图.缩放 >= .55 && n.所属群组) {
    ctx.globalAlpha = 亮度 * (.07 + 权重 * .09); ctx.fillStyle = 色;
    ctx.beginPath(); ctx.arc(p.x, p.y, p.r + 3 + 权重 * 4, 0, Math.PI * 2); ctx.fill();
  }
  if (权重) {
    ctx.globalAlpha = 权重 * .65; ctx.strokeStyle = 颜色.强调; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(p.x, p.y, p.r + 4 + 权重 * 4, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.globalAlpha = 亮度; ctx.fillStyle = 色; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
  if (n.类型 === '标签' && !权重) { ctx.fillStyle = 颜色.背景; ctx.fill(); ctx.strokeStyle = 颜色.标签; ctx.lineWidth = 1.5; ctx.stroke(); }
  else ctx.fill();
}
function 绘制图谱标题(图, 可见, 颜色) {
  const ctx = 图.ctx, 区域 = 图谱内容区域(图), 绘制队列 = [];
  const 优先级 = n => n.id === 图.悬停 ? 100 : n.id === 图.键盘节点 ? 95 : n.id === 图.选中 ? 90 : n.类型 === '文章' ? 60 : n.类型 === '概念' ? 50 : n.类型 === '标签' ? 30 : 10;
  const 标题们 = 可见.filter(p => !p.胶囊 && 图谱显示条目标题(图, p.n)).sort((a, b) => 优先级(b.n) - 优先级(a.n) || a.n.id.localeCompare(b.n.id));
  for (const p of 标题们) {
    const n = p.n, 重要 = 图谱重要节点(图, n), 排版 = 图谱条目排版(图, n);
    const w = 排版.宽, h = 排版.h, 空隙 = p.r + 8;
    const 候选 = [
      { x: p.x - w / 2, y: p.y + 空隙 }, { x: p.x - w / 2, y: p.y - 空隙 - h },
      { x: p.x + 空隙, y: p.y - h / 2 }, { x: p.x - 空隙 - w, y: p.y - h / 2 },
    ];
    const 钳制 = b => ({ x: Math.max(8, Math.min(b.x, 图.宽 - w - 8)), y: Math.max(6, Math.min(b.y, 区域.y + 区域.h + 12 - h)) });
    if (重要) {
      // 分栏/远景中，先沿附近空隙寻找位置；不能直接把选中标题放到胶囊背后。
      候选.push(
        { x: p.x - 空隙 - w, y: p.y + 空隙 }, { x: p.x + 空隙, y: p.y + 空隙 },
        { x: p.x - 空隙 - w, y: p.y - 空隙 - h }, { x: p.x + 空隙, y: p.y - 空隙 - h },
      );
      候选.push(...候选.map(钳制));
      for (let i = 1; i <= 8; i++) {
        候选.push(钳制({ x: p.x - w / 2, y: p.y + 空隙 + i * (h + 8) }));
        候选.push(钳制({ x: p.x - w / 2, y: p.y - 空隙 - h - i * (h + 8) }));
      }
    }
    const 范围内 = b => b.x >= 8 && b.x + w <= 图.宽 - 8 && b.y >= 6 && b.y + h <= 区域.y + 区域.h + 12;
    let 位置 = 候选.find(b => 范围内(b) && !图谱网格碰撞(图.避让网格, { ...b, w, h }, n.id, 4));
    if (!位置 && 重要) 位置 = 钳制(候选[0]);
    if (!位置) continue;
    const 框 = { ...位置, w, h };
    图谱网格加入(图.避让网格, 框, n.id);
    图谱网格加入(图.命中网格, { x: 框.x - 4, y: 框.y - 3, w: w + 8, h: h + 6 }, { ...p, 优先: 重要 ? 优先级(n) : 0 });
    p.标题框 = 框; p.标题文本 = 排版.文本;
    绘制队列.push({ n, 重要, 排版, 框 });
  }
  // 先为重要标题占位、最后绘制它；实在拥挤时，视觉和点击层级保持一致。
  for (const { n, 重要, 排版, 框 } of 绘制队列.reverse()) {
    ctx.globalAlpha = Math.max(.85, 图谱节点亮度(图, n));
    if (重要) { ctx.fillStyle = 颜色.背景; 图谱圆角路径(ctx, 框.x - 4, 框.y - 2, 框.w + 8, 框.h + 4, 4); ctx.fill(); }
    ctx.font = 排版.字体; ctx.textBaseline = 'top'; ctx.textAlign = 'left';
    ctx.lineWidth = 3; ctx.strokeStyle = 颜色.背景; ctx.lineJoin = 'round'; ctx.strokeText(排版.文本, 框.x, 框.y + 1);
    ctx.fillStyle = 重要 ? 颜色.强调 : 颜色.文字; ctx.fillText(排版.文本, 框.x, 框.y + 1);
  }
}
function 图谱胶囊候选(图, 宽) {
  const key = 'capsule-offsets:' + 宽;
  if (!图.排版缓存.has(key)) {
    const 候选 = [];
    for (let x = -4; x <= 4; x++) for (let y = -8; y <= 8; y++) {
      if (x || y) 候选.push({ x: x * (宽 / 2 + 12), y: y * 42 });
    }
    候选.sort((a, b) => a.x * a.x + a.y * a.y - b.x * b.x - b.y * b.y || a.y - b.y || a.x - b.x);
    图.排版缓存.set(key, 候选);
  }
  return 图.排版缓存.get(key);
}

function 排列图谱胶囊(图, 坐标) {
  const 占位 = 创建图谱网格(96), 区域 = 图谱内容区域(图);
  const 胶囊们 = [...坐标.values()].filter(p => p.胶囊 && p.框.x + p.框.w >= 0 && p.框.x <= 图.宽 && p.框.y + p.框.h >= 0 && p.框.y <= 图.高);
  胶囊们.sort((a, b) => Number(图谱重要节点(图, b.n) || b.n === 图.手势?.节点) - Number(图谱重要节点(图, a.n) || a.n === 图.手势?.节点) || (a.n.id < b.n.id ? -1 : 1));
  for (const p of 胶囊们) {
    const 原点 = { x: p.x, y: p.y }, c = p.胶囊, 旧偏移 = p.n.胶囊偏移;
    // 胶囊也是文字标签：远景时在屏幕空间避让，绝不重排世界中的节点。
    // 保留已解出的微小偏移，使缩放/刷新不会逐帧在候选位置之间跳动。
    const 偏移们 = 旧偏移 ? [旧偏移, { x: 0, y: 0 }] : [{ x: 0, y: 0 }];
    // 二维就近避让利用两侧留白，避免把大图的名称挤到远处或过早隐藏。
    偏移们.push(...图谱胶囊候选(图, c.w));
    let 位置 = null;
    if (p.n === 图.手势?.节点) {
      const 偏移 = 图.手势.胶囊偏移 || { x: 0, y: 0 };
      位置 = { x: 原点.x + 偏移.x - c.w / 2, y: 原点.y + 偏移.y - c.h / 2, w: c.w, h: c.h };
    } else for (const 偏移 of 偏移们) {
      const 框 = {
        x: Math.max(8, Math.min(原点.x + 偏移.x - c.w / 2, 图.宽 - c.w - 8)),
        y: Math.max(8, Math.min(原点.y + 偏移.y - c.h / 2, 区域.y + 区域.h - c.h)), w: c.w, h: c.h,
      };
      if (!图谱网格碰撞(占位, 框, null, 7)) { 位置 = 框; break; }
    }
    if (!位置) {
      // 极密远景让低优先级标题让位；真实锚点仍以原标签形状可达，焦点优先。
      p.胶囊 = null; p.r = 4;
      p.框 = { x: p.x - 4, y: p.y - 4, w: 8, h: 8 }; continue;
    }
    p.框 = 位置; p.x = 位置.x + c.w / 2; p.y = 位置.y + c.h / 2;
    p.n.胶囊偏移 = { x: p.x - 原点.x, y: p.y - 原点.y };
    图谱网格加入(占位, 位置, p.n.id);
  }
}

function 绘制图谱(图) {
  const ctx = 图.ctx, 颜色 = 图谱颜色();
  ctx.setTransform(图.像素比 || 1, 0, 0, 图.像素比 || 1, 0, 0); ctx.clearRect(0, 0, 图.宽, 图.高);
  清空图谱网格(图.命中网格); 清空图谱网格(图.避让网格);
  const 坐标 = new Map(), 可见 = [];
  for (const n of 图.节点) {
    if (!n.布局就绪) continue;
    const p = { ...世界到屏幕(图, n), n, r: 节点屏幕半径(图, n) };
    p.胶囊 = n.群组锚点 ? 图谱胶囊尺寸(图, n) : null;
    p.框 = p.胶囊 ? { x: p.x - p.胶囊.w / 2, y: p.y - 16, w: p.胶囊.w, h: 32 } : { x: p.x - p.r, y: p.y - p.r, w: p.r * 2, h: p.r * 2 };
    坐标.set(n.id, p);
  }
  排列图谱胶囊(图, 坐标);
  for (const p of 坐标.values()) {
    const n = p.n;
    const 余量 = !p.胶囊 && 图谱显示条目标题(图, n) ? Math.max(24, 图谱条目排版(图, n).宽 / 2 + 16) : 24;
    if (p.框.x + p.框.w < -余量 || p.框.x > 图.宽 + 余量 || p.框.y + p.框.h < -余量 || p.框.y > 图.高 + 余量) continue;
    可见.push(p); 图谱网格加入(图.避让网格, p.框, n.id);
    const 命中半径 = Math.max(10, p.r + 5);
    图谱网格加入(图.命中网格, p.胶囊 ? p.框 : { x: p.x - 命中半径, y: p.y - 命中半径, w: 命中半径 * 2, h: 命中半径 * 2 }, { ...p, 命中半径, 优先: p.胶囊 ? 2 : 1 });
  }
  绘制图谱连线(图, 坐标, 颜色);
  for (const p of 可见) if (!p.胶囊) 绘制图谱光点(图, p, 颜色);
  for (const p of 可见) if (p.胶囊) 绘制图谱胶囊(图, p, 颜色);
  绘制图谱标题(图, 可见, 颜色);
  ctx.globalAlpha = 1;
  图.画面 = { 缩放: 图.缩放, 偏移x: 图.偏移x, 偏移y: 图.偏移y, 坐标, 可见 }; 图.已绘制 = true;
  $('graph-zoom').textContent = Math.round(图.缩放 * 100) + '%';
  const 键盘点 = 坐标.get(图.键盘节点);
  if (键盘点 && !图.手势) 显示图谱提示(键盘点.n, 键盘点);
}

function 同步图谱氛围() {
  const 舞台 = $('graph-stage');
  const 偏好 = 读取图谱动态偏好(), 精细指针 = !!偏好?.精细.matches;
  const 触屏 = 图谱触屏输入 || 偏好?.触屏.matches;
  const 开启 = 图谱可绘制() && 精细指针 && !触屏 && !图谱减少动态() && !!状态.图谱?.节点.length;
  舞台.dataset.ambient = 开启 ? 'on' : 'off';
  if (!开启) {
    if (图谱氛围帧 != null) cancelAnimationFrame(图谱氛围帧);
    图谱氛围帧 = null; 图谱雾光点 = null;
    舞台.style.setProperty('--star-pointer-x', '0px'); 舞台.style.setProperty('--star-pointer-y', '0px');
  }
  return 开启;
}
function 跟随图谱雾光(点) {
  const 舞台 = $('graph-stage');
  if (舞台.dataset.ambient !== 'on') return;
  图谱雾光点 = 点;
  if (图谱氛围帧 != null) return;
  图谱氛围帧 = requestAnimationFrame(() => {
    图谱氛围帧 = null;
    if (舞台.dataset.ambient !== 'on') return;
    const p = 图谱雾光点;
    舞台.style.setProperty('--star-pointer-x', (p ? (p.x / 舞台.clientWidth - .5) * 14 : 0).toFixed(2) + 'px');
    舞台.style.setProperty('--star-pointer-y', (p ? (p.y / 舞台.clientHeight - .5) * 10 : 0).toFixed(2) + 'px');
  });
}

function 显示图谱消息(标题, 详情 = '', 可重试 = false) {
  $('graph-message').hidden = false;
  $('graph-message-title').textContent = 标题;
  $('graph-message-detail').textContent = 详情;
  $('btn-graph-retry').hidden = !可重试;
}

async function 载入图谱(强制 = false) {
  if (强制) {
    状态.图请求?.控制器.abort();
    状态.图请求 = null;
    状态.图数据 = null;
  }
  if (状态.图数据 && 状态.图谱) { 调整图谱尺寸(); return true; }
  if (状态.图请求) return 状态.图请求.完成;
  const 请求 = { 控制器: new AbortController(), 完成: null };
  状态.图请求 = 请求;
  显示图谱消息('正在载入图谱', window.WorkspaceAccess ? '连接公开摘要与所属收藏夹…' : '连接文章、分节与概念…');
  请求.完成 = (async () => {
    try {
      const r = await 知识库请求('/api/图谱', { signal: 请求.控制器.signal });
      if (!r.ok) throw new Error('图谱请求失败');
      const 数据 = await r.json();
      if (状态.图请求 !== 请求 || 请求.控制器.signal.aborted) return false;
      if (!Array.isArray(数据.节点) || !Array.isArray(数据.边)) throw new Error('图谱格式不正确');
      停止图谱动画();
      状态.图谱 = 构建图谱($('graph-stage').clientWidth, $('graph-stage').clientHeight, 数据, 状态.图谱);
      状态.图数据 = 数据;
      const 路由节点 = 状态.当前标签 ? 'tag:' + 状态.当前标签 : 状态.当前?.路径;
      if (路由节点) 状态.图谱.选中 = 状态.图谱.节点表.has(路由节点) ? 路由节点 : null;
      $('graph-count').textContent = `${状态.图谱.节点.length} 个节点 · ${状态.图谱.边.length} 条连线`;
      if (状态.图谱.节点.length) $('graph-message').hidden = true;
      else 显示图谱消息('图谱还是空的', window.WorkspaceAccess
        ? '请从顶部“收录公开摘要”进入收藏管理，选择收藏夹并收录最近一页摘要。这里不会展示其他账号的笔记。'
        : '导入笔记后，点击左下角的重新扫描按钮生成关系图谱。');
      渲染图谱节点列表();
      调整图谱尺寸();
      return true;
    } catch (e) {
      if (状态.图请求 !== 请求 || e.name === 'AbortError') return false;
      显示图谱消息('图谱加载失败', '请确认本地服务仍在运行，然后重新加载。', true);
      return false;
    } finally {
      if (状态.图请求 === 请求) 状态.图请求 = null;
    }
  })();
  return 请求.完成;
}

function 隐藏图谱提示() {
  $('graph-tooltip').hidden = true;
  if (状态.图谱) 状态.图谱.悬停 = null;
}

function 显示图谱提示(n, 点) {
  const 提示框 = $('graph-tooltip'), 图 = 状态.图谱;
  if (!n || !图 || 图.手势) { 隐藏图谱提示(); return; }
  // 只使用图数据的标题、类型和度数；悬停不会预读正文或请求接口。
  const 标题 = (n.类型 === '标签' && !n.群组锚点 ? '#' : '') + n.标题;
  const 说明 = n.类型 + ' · ' + (图.邻接.get(n.id)?.size || 0) + ' 个关联 · 单击打开';
  const 文本 = 标题 + '\n' + 说明;
  if (提示框.getAttribute('aria-label') !== 文本) {
    提示框.setAttribute('aria-label', 文本);
    提示框.innerHTML = '<span class="graph-tooltip-title">' + 转义(标题) + '</span>\n<span class="graph-tooltip-meta">' + 转义(说明) + '</span>';
  }
  const 区域 = 图谱内容区域(图), 底 = Math.min(图.高 - 8, 区域.y + 区域.h + 16);
  const 最大高 = Math.max(40, 底 - 16);
  提示框.style.maxHeight = 最大高 + 'px';
  // 保留元信息一整行；超长标题仅视觉截断，完整内容留在无障碍名称和节点列表中。
  提示框.style.setProperty('--star-tooltip-lines', String(Math.max(1, Math.min(5, Math.floor((最大高 - 47) / 20)))));
  提示框.hidden = false;
  const w = 提示框.offsetWidth || Math.min(270, 图.宽 - 24), h = Math.min(提示框.offsetHeight || 60, 最大高);
  const 右 = 点.x + 18, 左 = 点.x - w - 18, 上 = 点.y - h - 18, 下 = 点.y + 18;
  const x = 右 + w <= 图.宽 - 12 ? 右 : 左 >= 12 ? 左 : Math.max(12, Math.min(右, 图.宽 - w - 12));
  const 候选y = 上 >= 10 ? 上 : 下 + h <= 底 ? 下 : 上;
  const y = Math.max(8, Math.min(候选y, 底 - h));
  提示框.style.left = x + 'px'; 提示框.style.top = y + 'px';
}

function 选中图谱节点(id) {
  const 图 = 状态.图谱;
  if (!图) return;
  图.选中 = 图.节点表.has(id) ? id : null;
  图.键盘节点 = null;
  隐藏图谱提示();
  请求图谱绘制();
}

function 渲染图谱节点列表() {
  const 全部 = 状态.图谱?.节点 || [];
  const 词 = $('graph-node-filter').value.trim().toLocaleLowerCase();
  const 结果 = 全部.filter(n => (n.标题 + ' ' + n.类型).toLocaleLowerCase().includes(词));
  $('graph-node-count').textContent = `${结果.length} / ${全部.length} 个节点 · 点击打开`;
  $('graph-node-results').innerHTML = 结果.length ? 结果.map(n =>
    `<button class="node-result" data-node-id="${转义(n.id)}"><span class="node-result-type">${转义(n.类型)}</span><span class="node-result-title">${n.类型 === '标签' ? '#' : ''}${转义(n.标题)}</span></button>`
  ).join('') : '<p class="link-empty">没有匹配的节点</p>';
}

function 切换图谱节点列表(打开) {
  $('graph-node-list').hidden = !打开;
  $('btn-graph-list').setAttribute('aria-expanded', String(打开));
  if (打开) { 渲染图谱节点列表(); $('graph-node-filter').focus(); }
}

// ── 右键菜单：删除节点 ──────────────────────────────────────────────────────
// 只在账号隔离模式（存在 WorkspaceAccess）提供：本地文件库没有删除接口，右键不弹菜单。
// 服务端是**真删**且只作用于当前账号，所以这里必须二次确认，并把会连带删掉多少条写清楚。
let 图谱菜单盒 = null;

function 菜单元素(标签, 类名, 文字) {
  const el = document.createElement(标签);
  if (类名) el.className = 类名;
  if (文字 !== undefined) el.textContent = 文字;
  return el;
}

function 关闭图谱菜单(还原 = true) {
  if (图谱菜单盒) {
    图谱菜单盒.remove();
    document.removeEventListener('pointerdown', 图谱菜单盒.点外面, true);
    document.removeEventListener('keydown', 图谱菜单盒.按Esc, true);
    图谱菜单盒 = null;
  }
  const 图 = 状态.图谱;
  if (还原 && 图?.右键节点) { 图.右键节点 = null; 请求图谱绘制(); }
}

async function 删除后刷新(被删路径, 被删收藏夹) {
  const 当前 = 状态.当前;
  const 被牵连 = !!当前 && ((被删路径 && 当前.路径 === 被删路径) ||
    (被删收藏夹 && String(当前.收藏夹 || '').endsWith(' · ' + 被删收藏夹)));
  状态.图数据 = null;
  await 载入索引();
  // 被删的那条正开着 → 回图谱，否则阅读面板还挂着已经不存在的内容。
  if (被牵连) { 打开图谱(false); return; }
  await 载入图谱(true);
}

function 打开图谱菜单(节点, 屏幕x, 屏幕y) {
  关闭图谱菜单(false);
  const 是收藏夹 = 节点.类型 === '标签' || String(节点.id).startsWith('tag:');
  const 条数 = Math.max(1, Number(节点.数量) || 1);
  const 标题文字 = (是收藏夹 ? '#' : '') + (节点.标题 || 节点.id);
  const 盒 = 菜单元素('div', 'graph-menu');
  盒.id = 'graph-menu';
  盒.setAttribute('role', 'dialog');
  盒.setAttribute('aria-label', '节点操作');
  const 按钮 = (文字, 危险) => {
    const b = 菜单元素('button', 'graph-menu-action' + (危险 ? ' graph-menu-danger' : ''), 文字);
    b.type = 'button';
    return b;
  };
  const 按钮行 = (...项) => { const 行 = 菜单元素('div', 'graph-menu-row'); 行.append(...项); return 行; };

  function 弹首屏() {
    const 删 = 按钮('删除节点', true);
    删.addEventListener('click', 弹确认);
    盒.replaceChildren(
      菜单元素('p', 'graph-menu-title', 标题文字),
      菜单元素('p', 'graph-menu-meta', 是收藏夹 ? `收藏夹 · 已收录 ${条数} 条` : (节点.收藏夹 || '条目')),
      删,
    );
  }

  function 弹确认() {
    const 确认按钮 = 按钮('确认删除', true);
    确认按钮.addEventListener('click', () => 提交(确认按钮));
    const 取消按钮 = 按钮('取消', false);
    取消按钮.addEventListener('click', () => 关闭图谱菜单());
    盒.replaceChildren(
      菜单元素('p', 'graph-menu-title', 是收藏夹 ? '整夹删除？' : '确认删除？'),
      菜单元素('p', 'graph-menu-meta', 标题文字),
      菜单元素('p', 'graph-menu-note', 是收藏夹
        ? `将清空这个收藏夹已收录的全部 ${条数} 条内容：图谱、收藏夹卡片列表与检索里都会消失。`
        : '图谱、收藏夹卡片列表与检索里都不再出现。'),
      菜单元素('p', 'graph-menu-note', '真删、不可恢复；重新收录摘要或重跑直答处理，同样的内容会再次出现。'),
      按钮行(取消按钮, 确认按钮),
    );
    确认按钮.focus({ preventScroll: true });
  }

  async function 提交(确认按钮) {
    盒.querySelectorAll('button').forEach(b => { b.disabled = true; });
    确认按钮.textContent = '正在删除…';
    const 参数 = 是收藏夹 ? {收藏夹: 节点.收藏夹id || ''} : {路径: 节点.id};
    try {
      if (是收藏夹 && !参数.收藏夹) throw new Error('这个收藏夹缺少标识，无法删除');
      const r = await 知识库请求('/api/记录', {method: 'DELETE', body: JSON.stringify(参数)});
      const 数据 = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(数据?.error?.message || '删除失败，请重试');
      关闭图谱菜单();
      提示(`已删除 ${Number(数据?.deleted) || 0} 条记录`);
      await 删除后刷新(是收藏夹 ? '' : 节点.id, 是收藏夹 ? 参数.收藏夹 : '');
    } catch (错误) {
      确认按钮.disabled = false; 确认按钮.textContent = '确认删除';
      盒.append(菜单元素('p', 'graph-menu-error', String(错误?.message || 错误)));
    }
  }

  弹首屏();
  盒.点外面 = e => { if (!盒.contains(e.target)) 关闭图谱菜单(); };
  盒.按Esc = e => { if (e.key === 'Escape') { e.stopPropagation(); 关闭图谱菜单(); } };
  document.addEventListener('pointerdown', 盒.点外面, true);
  document.addEventListener('keydown', 盒.按Esc, true);
  document.body.append(盒);
  const r = 盒.getBoundingClientRect();
  盒.style.left = Math.max(8, Math.min(屏幕x, innerWidth - r.width - 8)) + 'px';
  盒.style.top = Math.max(8, Math.min(屏幕y, innerHeight - r.height - 8)) + 'px';
  图谱菜单盒 = 盒;
  盒.querySelector('button')?.focus({ preventScroll: true });
}

function 图谱手势是否点击(手势, 结束点) {
  return !!手势?.节点 && Math.max(手势.距离, Math.hypot(结束点.x - 手势.起点.x, 结束点.y - 手势.起点.y)) <= 5;
}

function 绑定图谱事件() {
  const 画布 = $('graph-canvas');
  const 取点 = e => {
    const r = 画布.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  画布.addEventListener('pointerdown', e => {
    const 图 = 状态.图谱;
    if (!图 || 图谱布局未完成(图) || 图.手势 || e.button !== 0 || e.isPrimary === false) return;
    if (e.pointerType === 'touch') { 图谱触屏输入 = true; 同步图谱氛围(); }
    // 取消的是尚未绘出的镜头帧；命中始终使用当前已经显示的变换。
    停止图谱动画();
    const p = 取点(e), 世界 = 屏幕到世界(图, p), 节点 = 命中图谱节点(图, p);
    图.手势 = {
      pointerId: e.pointerId, 起点: p, 距离: 0, 节点,
      原偏移x: 图.偏移x, 原偏移y: 图.偏移y,
      节点偏移x: 节点 ? 世界.x - 节点.x : 0, 节点偏移y: 节点 ? 世界.y - 节点.y : 0,
      胶囊偏移: 节点?.胶囊偏移 ? { ...节点.胶囊偏移 } : null,
    };
    图.键盘节点 = null; 隐藏图谱提示();
    画布.setPointerCapture(e.pointerId); 画布.focus({ preventScroll: true });
  });
  画布.addEventListener('pointermove', e => {
    const 图 = 状态.图谱;
    if (!图 || 图谱布局未完成(图)) return;
    const p = 取点(e), 手势 = 图.手势;
    if (e.pointerType !== 'touch') 跟随图谱雾光(p);
    if (手势) {
      if (手势.pointerId !== e.pointerId) return;
      const dx = p.x - 手势.起点.x, dy = p.y - 手势.起点.y;
      手势.距离 = Math.max(手势.距离, Math.hypot(dx, dy));
      if (手势.距离 <= 5) return;
      图.自动适应 = false; 画布.style.cursor = 'grabbing';
      if (手势.节点) {
        const 世界 = 屏幕到世界(图, p);
        手势.节点.x = 世界.x - 手势.节点偏移x; 手势.节点.y = 世界.y - 手势.节点偏移y;
        手势.节点.vx = 手势.节点.vy = 0;
      } else {
        图.偏移x = 手势.原偏移x + dx; 图.偏移y = 手势.原偏移y + dy;
      }
      请求图谱绘制();
    } else {
      const n = 命中图谱节点(图, p), id = n?.id || null;
      const 需要重绘 = id !== 图.悬停 || !!图.键盘节点;
      图.悬停 = id; 图.键盘节点 = null;
      画布.style.cursor = n ? 'pointer' : 'grab';
      显示图谱提示(n, p);
      if (需要重绘) 请求图谱绘制();
    }
  });
  const 结束手势 = (e, 取消 = false) => {
    const 图 = 状态.图谱, 手势 = 图?.手势;
    if (!手势 || 手势.pointerId !== e.pointerId) return;
    const 单击 = !取消 && 图谱手势是否点击(手势, 取点(e));
    图.手势 = null;
    if (画布.hasPointerCapture(e.pointerId)) 画布.releasePointerCapture(e.pointerId);
    画布.style.cursor = 'grab';
    // 拖放不加入惯性或自动回弹，真实节点保留用户放置的位置。
    if (手势.距离 > 5 && 手势.节点) 手势.节点.位置保留 = true;
    图.温度 = 0;
    if (单击) 打开图谱节点(手势.节点);
    请求图谱绘制();
  };
  画布.addEventListener('pointerup', e => 结束手势(e));
  画布.addEventListener('pointercancel', e => 结束手势(e, true));
  画布.addEventListener('lostpointercapture', e => 结束手势(e, true));
  画布.addEventListener('pointerleave', () => {
    if (状态.图谱?.手势) return;
    const 曾悬停 = !!状态.图谱?.悬停;
    隐藏图谱提示(); 画布.style.cursor = 'grab'; 跟随图谱雾光(null);
    if (曾悬停) 请求图谱绘制();
  });
  // 右键节点：原生菜单在画布上没有可用项，换成自绘菜单（本地文件库模式不提供删除，直接放行）。
  画布.addEventListener('contextmenu', e => {
    const 图 = 状态.图谱;
    if (!图 || !window.WorkspaceAccess) return;
    const n = 命中图谱节点(图, 取点(e));
    if (!n) return;
    e.preventDefault();
    图.右键节点 = n.id;
    请求图谱绘制();
    打开图谱菜单(n, e.clientX, e.clientY);
  });
  画布.addEventListener('wheel', e => {
    e.preventDefault();
    const 图 = 状态.图谱;
    if (!图 || 图.手势 || 图谱布局未完成(图)) return;
    const 单位 = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 图.高 : 1;
    const delta = Math.max(-120, Math.min(120, e.deltaY * 单位));
    缓动位置缩放(图, Math.exp(-delta * .0025), 取点(e));
    隐藏图谱提示();
  }, { passive: false });
  画布.addEventListener('keydown', e => {
    const 图 = 状态.图谱;
    if (!图?.节点.length || 图谱布局未完成(图)) return;
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
      e.preventDefault(); 图.镜头 = null;
      const 当前 = 图.节点.findIndex(n => n.id === (图.键盘节点 || 图.选中));
      const 方向 = ['ArrowLeft', 'ArrowUp'].includes(e.key) ? -1 : 1;
      const index = 当前 < 0 ? 0 : (当前 + 方向 + 图.节点.length) % 图.节点.length;
      const n = 图.节点[index]; 图.键盘节点 = n.id; 图.悬停 = null;
      const 点 = 世界到屏幕(图, n), 区域 = 图谱内容区域(图);
      const 半宽 = n.群组锚点 ? 图谱胶囊尺寸(图, n).w / 2 + 8 : 图谱条目排版(图, n).宽 / 2 + 8;
      if (点.x - 半宽 < 区域.x || 点.x + 半宽 > 区域.x + 区域.w || 点.y < 区域.y + 20 || 点.y > 区域.y + 区域.h - 40) {
        图.偏移x = 区域.x + 区域.w / 2 - n.x * 图.缩放; 图.偏移y = 区域.y + 区域.h / 2 - n.y * 图.缩放;
        图.自动适应 = false;
      }
      $('graph-announcement').textContent = n.标题 + '，' + n.类型 + '，按 Enter 打开';
      $('graph-tooltip').hidden = true; 请求图谱绘制();
    } else if (e.key === 'Enter') {
      e.preventDefault(); 打开图谱节点(图.节点表.get(图.键盘节点 || 图.选中) || 图.节点[0]);
    } else if (e.key === 'Home' || e.key === '0') { e.preventDefault(); 适应图谱(); }
    else if (['+', '=', '-'].includes(e.key)) { e.preventDefault(); 缩放图谱(e.key === '-' ? 1 / 1.2 : 1.2); }
  });
  画布.addEventListener('blur', () => {
    if (状态.图谱) 状态.图谱.键盘节点 = null;
    隐藏图谱提示(); 请求图谱绘制();
  });
  $('btn-graph-fit').onclick = () => 适应图谱();
  $('btn-zoom-in').onclick = () => 缩放图谱(1.2);
  $('btn-zoom-out').onclick = () => 缩放图谱(1 / 1.2);
  $('btn-graph-list').onclick = () => 切换图谱节点列表($('graph-node-list').hidden);
  $('btn-close-node-list').onclick = () => { 切换图谱节点列表(false); $('btn-graph-list').focus(); };
  $('graph-node-filter').addEventListener('input', 渲染图谱节点列表);
  $('graph-node-results').addEventListener('click', e => {
    const 目标 = e.target.closest('[data-node-id]');
    const n = 状态.图谱?.节点表.get(目标?.dataset.nodeId);
    if (n) { 切换图谱节点列表(false); 打开图谱节点(n); }
  });
  $('btn-graph-retry').onclick = async () => {
    if (!状态.索引 && !(await 载入索引())) return;
    await 载入图谱(true); await 应用地址路由();
  };
  if (typeof ResizeObserver !== 'undefined') {
    图谱尺寸观察器 = new ResizeObserver(调整图谱尺寸); 图谱尺寸观察器.observe($('graph-stage'));
  }
  if (typeof IntersectionObserver !== 'undefined') {
    图谱可见观察器 = new IntersectionObserver(entries => {
      图谱在视口 = entries.some(entry => entry.isIntersecting);
      if (!图谱在视口) { 中止图谱手势(); 停止图谱动画(); }
      else { 调整图谱尺寸(); 请求图谱绘制(); }
      同步图谱氛围();
    });
    图谱可见观察器.observe($('graph-stage'));
  }
  const 偏好改变 = () => {
    if (图谱减少动态()) 停止图谱动画();
    同步图谱氛围(); 请求图谱绘制();
  };
  for (const 媒体 of Object.values(读取图谱动态偏好() || {})) {
    if (媒体.addEventListener) 媒体.addEventListener('change', 偏好改变);
    else if (媒体.addListener) 媒体.addListener(偏好改变);
  }
  window.addEventListener('resize', 调整图谱尺寸);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { 中止图谱手势(); 停止图谱动画(); }
    else { 调整图谱尺寸(); 请求图谱绘制(); }
    同步图谱氛围();
  });
  同步图谱氛围();
}

function 缩放图谱(倍率) {
  const 图 = 状态.图谱;
  if (!图 || 图谱布局未完成(图)) return;
  缓动位置缩放(图, 倍率, { x: 图.宽 / 2, y: 图.高 / 2 }); 隐藏图谱提示();
}
