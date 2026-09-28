"""Reuse only the knowledge UI, never the author's vault or local-only API."""
from pathlib import Path

from .config import ACCESS_SECRET_URL

UI_ROOT = Path(__file__).resolve().parents[2] / "网页"
ASSET_NAMES = (
    "app.js", "graph.js", "style.css", "oauth/workspace.js", "oauth/workspace.css",
    "oauth/import-collections.js", "oauth/collections.js",
    "lib/marked.min.js", "lib/purify.min.js", "lib/highlight.min.js",
    "lib/hljs-light.css", "lib/hljs-dark.css", "lib/katex.min.js",
    "lib/katex.min.css", "lib/auto-render.min.js",
)


def workspace_assets() -> dict[str, Path]:
    assets = {name: UI_ROOT / name for name in ASSET_NAMES}
    for path in (UI_ROOT / "lib" / "fonts").iterdir():
        if path.is_file() and path.suffix in {".woff", ".woff2", ".ttf"}:
            assets["lib/fonts/" + path.name] = path
    return assets


# 「知乎收藏」（/workspace/）顶部的直答处理横幅。请求必须走 workspace.js 的受控封装，
# 所以脚本要在它之后加载。
直答横幅 = '''<style>
#zh-import{max-width:1180px;margin:16px auto 0;padding:18px 20px;border:1px solid var(--text-faint);
border-radius:14px;background:var(--bg-sub);display:flex;gap:22px;align-items:flex-start;
justify-content:space-between;flex-wrap:wrap}
#zh-import h2{margin:0 0 6px;font-size:17px;line-height:1.45}
#zh-import p{margin:0;font-size:12px;color:var(--text-sub);line-height:1.85}
#zh-import .zh-import-main{flex:1 1 420px;min-width:0}
#zh-import .zh-import-side{display:flex;flex-direction:column;gap:9px;align-items:flex-end;min-width:220px}
#zh-import button{border:0;border-radius:10px;padding:14px 26px;font-size:14px;font-weight:600;
background:var(--text);color:var(--bg);cursor:pointer;white-space:nowrap;font-family:inherit}
#zh-import button:disabled{opacity:.5;cursor:default}
#zh-import-track{margin-top:12px;height:6px;border-radius:999px;background:var(--text-faint);overflow:hidden}
#zh-import-bar-fill{height:100%;width:0;background:var(--text);transition:width .4s ease}
#zh-import[data-state="done"] #zh-import-bar-fill{background:#3f7d4a}
#zh-import[data-state="failed"] #zh-import-bar-fill{background:#a4442c}
#zh-import-status{margin-top:9px;min-height:1.7em}
#zh-import-issues{margin-top:7px;color:#8a5a00}
#zh-import-quota{margin:0;font-size:12px;color:var(--text-sub);text-align:right;line-height:1.6}
#zh-import-secret{font-size:12px;color:var(--text-sub);text-decoration:underline;
text-underline-offset:4px;margin-top:2px}
#zh-import-secret:hover{color:var(--text)}
#zh-import-settings{font-size:12px;color:var(--text-sub);text-decoration:underline;
text-underline-offset:4px;margin-top:8px}
#zh-import-settings:hover{color:var(--text)}
@media (max-width:720px){#zh-import{margin:12px 12px 0;padding:16px}
#zh-import .zh-import-side{align-items:stretch;width:100%}#zh-import button{width:100%}
#zh-import-quota{text-align:left}#zh-import-secret{text-align:center}}
</style>
<section id="zh-import" aria-label="用知乎直答处理收藏夹">
  <div class="zh-import-main">
    <h2>用知乎直答把收藏夹整理成知识块</h2>
    <p>读取你账号下的公开收藏夹摘要，按主题归并成知识块，写进这个个人知识库。
       一次调用处理一批摘要，不会逐条消耗额度；开始后会先算出预计调用次数，超过上限就直接停下并告诉你。
       直答额度默认走应用凭证（所有访问者共用同一个池子），右侧实时显示今日剩余次数；
       想消耗自己的额度，点右侧「用我自己的额度 · 设置」填自己的 Access Secret。</p>
    <div id="zh-import-track"><div id="zh-import-bar-fill"></div></div>
    <p id="zh-import-status">还没开始。点右侧按钮即可处理当前账号的公开收藏夹。</p>
    <p id="zh-import-issues" hidden></p>
  </div>
  <div class="zh-import-side">
    <p id="zh-import-quota" aria-live="polite">今日直答额度：读取中…</p>
    <button id="zh-import-start" type="button">用直答生成知识地图（收藏 + 创作地图）</button>
    <a id="zh-import-secret" href="''' + ACCESS_SECRET_URL + '''" target="_blank"
       rel="noopener noreferrer">获取开放平台 Access Secret ↗</a>
    <a id="zh-import-settings" href="/settings">用我自己的额度 · 设置</a>
  </div>
</section>
'''


# 「知乎收藏」（/workspace/）下半部分：把知识库布局整块换成"收藏夹 + 内容卡片"。
# 卡片只展示收录时的快照字段（标题/作者/摘要/互动数字），看原文一律跳回知乎；
# 互动数字是只读的——开放平台只有 GET，没有点赞/评论这类写接口。
收藏夹视图 = '''<div class="collections" id="collections">
  <aside class="collections-side" aria-label="数据来源与分组">
    <nav id="collections-source" class="collections-source" aria-label="数据来源">
      <button class="collections-source-item active" type="button" data-来源="收藏">我的收藏</button>
      <button class="collections-source-item" type="button" data-来源="创作">我的创作</button>
    </nav>
    <div class="collections-side-head"><span id="collections-side-label">收藏夹</span><span id="collections-count" class="collections-count">—</span></div>
    <nav id="collections-list" class="collections-list" aria-label="收藏夹列表"></nav>
    <p id="collections-side-state" class="collections-side-state">正在读取已收录内容…</p>
  </aside>
  <section class="collections-main" aria-labelledby="collections-title" aria-busy="false">
    <header class="collections-head">
      <div>
        <p class="collections-label" id="collections-label">PUBLIC COLLECTIONS / 公开收藏</p>
        <h1 id="collections-title">正在读取收藏夹…</h1>
        <p id="collections-meta" class="collections-meta"></p>
      </div>
      <button id="collections-theme" class="collections-theme" type="button"
              title="切换明暗主题" aria-label="切换明暗主题"><svg viewBox="0 0 24 24" width="16" height="16"
        fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"
        aria-hidden="true"><path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"/></svg></button>
    </header>
    <p id="collections-note" class="collections-note" hidden></p>
    <div id="collections-cards" class="collections-cards"></div>
    <div class="collections-actions">
      <button id="collections-more" class="collections-more" type="button" hidden>加载更多</button>
      <button id="collections-sync" class="collections-more" type="button" hidden>同步我的创作</button>
    </div>
    <p id="collections-feedback" class="collections-feedback" role="status" aria-live="polite" hidden></p>
    <section id="collections-empty" class="collections-empty" hidden>
      <h2 id="collections-empty-title">还没有收录到内容</h2>
      <p id="collections-empty-copy">在上面的横幅点「用直答生成知识地图」，或到账号页收录最近一页摘要。
         收录后这里会出现标题与摘要卡片，看原文直接跳回知乎。</p>
      <a class="collections-cta" id="collections-empty-cta" href="/account">去账号页收录摘要 →</a>
    </section>
  </section>
</div>
'''


def workspace_html() -> str:
    """「知乎收藏」（/workspace/）：直答处理横幅 + 收录内容的卡片列表。

    与「个人知识库」分工：这里是**原始收藏**（标题/摘要/作者/原文链接，看原文跳知乎），
    归并出来的知识块与图谱留在 `/`。所以这一页不加载知识库那套前端
    （`graph.js` / `app.js`），改成整块换掉布局。
    """
    return _个人视图(带横幅=True)


def personal_html() -> str:
    """「个人知识库」（/）：同一套账号隔离视图，但只用于查看结果，不放处理横幅。"""
    page = _个人视图(带横幅=False)
    page = page.replace('知乎收藏 <span>/ 个人知识库</span>', '个人知识库 <span>/ 账号隔离</span>')
    # 处理入口在「知乎收藏」，这里给一条回去的路，形成 收藏夹 → 处理 → 结果 的闭环。
    page = page.replace('<a class="workspace-import" href="/account">管理账号 / 收录公开摘要</a>',
                        '<a class="workspace-import" href="/workspace/">去知乎收藏处理收藏夹</a>'
                        '<a class="workspace-import" href="/account">管理账号 / 收录公开摘要</a>')
    return page


def _换成收藏夹视图(page: str) -> str:
    """把知识库布局整块换成「收藏夹 + 内容卡片」，并摘掉知识库那套前端。

    顺带修掉桌面端"不能下划、下半部分被压扁"：原布局是固定视口 + 内部网格 +
    `overflow:hidden`，图谱靠 flex 撑满剩余高度，顶部一加横幅就被压得很小，
    而放开滚动的规则只在窄屏媒体查询里。卡片列表是文档流，天然可以下划。
    """
    起点 = page.index('<div class="layout">')
    终点 = page.index('<footer class="statusbar">')
    page = page[:起点] + 收藏夹视图 + page[终点:]
    # 图谱与知识库阅读面板只属于「个人知识库」，这里不加载它们的脚本。
    for 标签 in ('<script src="/workspace/assets/graph.js"></script>\n',
                 '<script src="/workspace/assets/app.js"></script>\n'):
        page = page.replace(标签, '')
    page = page.replace('正在读取本地知识库…', '正在读取已收录内容…')
    page = page.replace('<span id="status-right">本地知识库</span>',
                        '<span id="status-right">账号隔离 · 非全文</span>')
    return page


def _个人视图(带横幅: bool) -> str:
    page = (UI_ROOT / "index.html").read_text(encoding="utf-8")
    for name in ASSET_NAMES:
        page = page.replace('"/' + name + '"', '"/workspace/assets/' + name + '"')
    page = page.replace("</head>", '<link rel="stylesheet" href="/workspace/assets/oauth/workspace.css">\n</head>')
    页面根 = '<body class="sidebar-open personal-workspace workspace-locked'
    if 带横幅:
        # 卡片页要能正常下划：它不再是看板式布局，而是普通文档流。
        页面根 += ' collections-page'
    页面根 += '">'
    page = page.replace('<body class="sidebar-open">', 页面根)
    if 带横幅:
        # 直答处理横幅 + 收藏夹卡片视图只放在「知乎收藏」页顶部。
        page = page.replace(页面根, 页面根 + 直答横幅)
        page = _换成收藏夹视图(page)
    bar = '''<header class="account-bar" aria-label="个人工作区账户">
      <a class="workspace-brand" href="/">知乎收藏 <span>/ 个人知识库</span></a>
      <p id="workspace-account" aria-live="polite">正在核验当前账号…</p>
      <nav aria-label="账户与导入"><a class="workspace-import" href="/account">管理账号 / 收录公开摘要</a>
      <button id="workspace-logout" type="button" disabled>退出</button></nav>
    </header>'''
    范围说明 = ('摘要与知识块都来自收录时的快照 · 非全文 · 看原文请点「阅读原文」回知乎'
                if 带横幅 else '摘要快照与直答知识块 · 非全文 · 图谱连线表示所属收藏夹')
    bar += (f'\n    <p class="workspace-scope">{范围说明}</p>'
            '\n    <section id="workspace-gate" role="status"><h1>正在核验账号</h1><p>确认身份后才读取你的知识库。</p><a href="/account">返回账号页</a></section>')
    布局锚点 = '<div class="collections"' if 带横幅 else '<div class="layout">'
    page = page.replace(布局锚点, bar + '\n' + 布局锚点, 1)
    脚本 = ('<script src="/assets/login-redirect.js"></script>\n'
            '<script src="/workspace/assets/oauth/workspace.js"></script>\n')
    if 带横幅:
        # 横幅与卡片脚本都依赖 workspace.js 的受控封装，必须排在它之后。
        脚本 += ('<script src="/workspace/assets/oauth/import-collections.js"></script>\n'
                 '<script src="/workspace/assets/oauth/collections.js"></script>\n')
        page = page.replace('</body>', 脚本 + '</body>', 1)
    else:
        page = page.replace('<script src="/workspace/assets/graph.js">', 脚本 + '<script src="/workspace/assets/graph.js">')
    page = page.replace('知乎知识库 <span class="vault-local">本地</span>', '我的摘要库 <span class="vault-local">账号隔离</span>')
    page = page.replace('重新扫描笔记库', '刷新我的摘要库')
    page = page.replace('正在读取本地知识库…', '正在读取个人摘要库…').replace('>本地知识库<', '>知识条目<')
    page = page.replace('搜索标题、正文、标签、作者…', '搜索已收录标题、摘要、收藏夹…')
    page = page.replace('连接文章、分节与概念…', '连接知识条目与所属收藏夹…')
    page = page.replace('<span><i class="legend-section"></i>分节</span>', '')
    page = page.replace('<span><i class="legend-concept"></i>概念</span>', '')
    # 工作区里既可能是公开摘要快照，也可能是直答归并出的知识块，图例统一叫「知识条目」。
    page = page.replace('<i class="legend-article"></i>文章', '<i class="legend-article"></i>知识条目')
    page = page.replace('<i class="legend-tag"></i>标签', '<i class="legend-tag"></i>收藏夹')
    return page
