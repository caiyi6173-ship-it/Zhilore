# -*- coding: utf-8 -*-
"""
知乎 HTML → Markdown 转换器
============================

把知乎回答/文章的 content HTML 转成干净的 Markdown，供 Obsidian 使用。

专门处理的知乎坑：
  1. 公式图片   https://www.zhihu.com/equation?tex=E%3Dmc%5E2  →  $E=mc^2$
  2. 懒加载图片 <img data-original> / <noscript><img src></noscript>
  3. 外链跳转   https://link.zhihu.com/?target=http%3A%2F%2F...  →  真实地址
  4. 代码块     <pre lang="python"><code>...</code></pre>
  5. 富文本     <b> <em> <u> <del> <mark> <sup> <sub>
  6. 折叠块     <details>/<summary>
  7. 视频/卡片  导出为普通链接，避免塞入无法播放的 iframe

对外只暴露一个函数：html_to_markdown(html, images=None) -> str
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------- 基础工具

# 这些属性的优先级：知乎不同版本把真实图片地址放在不同属性里
IMG_SRC_ATTRS = ("data-original", "data-actualsrc", "data-src", "data-original-src", "src")

# 需要前后留空行的块级元素
BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "table", "figure", "figcaption",
    "hr", "dl", "dt", "dd", "details", "summary",
}

# 直接丢弃的标签（脚本、样式、广告位等）
DROP_TAGS = {"script", "style", "noscript", "iframe", "svg", "button", "form", "input"}


def clean_url(url: str) -> str:
    """还原知乎的跳转链接，补全协议头。"""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    # 知乎外链会包一层 link.zhihu.com/?target=<真实地址>
    if "link.zhihu.com" in url and "target=" in url:
        try:
            qs = parse_qs(urlparse(url).query)
            real = qs.get("target", [""])[0]
            if real:
                url = unquote(real)
        except Exception:
            pass
    return url


def equation_tex(src: str) -> str | None:
    """从知乎公式图片地址里还原 LaTeX 源码。"""
    if not src or "equation" not in src:
        return None
    try:
        qs = parse_qs(urlparse(src).query)
        tex = qs.get("tex", [""])[0]
        return unquote(tex) if tex else None
    except Exception:
        return None


def pick_img_src(tag: Tag) -> str:
    """按优先级挑出真实图片地址，跳过 base64 占位图。"""
    for attr in IMG_SRC_ATTRS:
        v = tag.get(attr)
        if isinstance(v, list):
            v = v[0] if v else None
        if v and isinstance(v, str) and not v.startswith("data:"):
            return clean_url(v)
    return ""


def squash(text: str) -> str:
    """压缩连续空白，保留单个空格。"""
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def escape_md(text: str) -> str:
    """转义 Markdown 里有歧义的字符（只处理正文文本，不碰行内格式）。"""
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


# ---------------------------------------------------------------- 转换器


class ZhihuHtmlToMarkdown:
    def __init__(self, collect_images: bool = True):
        self.collect_images = collect_images
        self.images: list[str] = []   # 按出现顺序收集的图片地址

    # ---------------- 入口 ----------------

    def convert(self, html: str) -> str:
        if not html or not html.strip():
            return ""
        soup = BeautifulSoup(html, "lxml")
        body = soup.body or soup
        md = self._render_children(body)
        return self._tidy(md)

    # ---------------- 渲染核心 ----------------

    def _render_children(self, node: Tag) -> str:
        parts = []
        for child in node.children:
            parts.append(self._render(child))
        return "".join(parts)

    def _render(self, node) -> str:
        # 纯文本
        if isinstance(node, NavigableString):
            if node.parent and node.parent.name in DROP_TAGS:
                return ""
            return squash(str(node))

        if not isinstance(node, Tag):
            return ""

        name = node.name.lower()
        if name in DROP_TAGS:
            return ""

        # 数学公式（新版知乎用 span[data-tex]）
        tex = node.get("data-tex") if name == "span" else None
        if tex:
            return f" ${tex.strip()}$ "

        handler = getattr(self, f"_h_{name}", None)
        if handler:
            return handler(node)

        # 未特殊处理的标签：继续往里钻
        inner = self._render_children(node)
        if name in BLOCK_TAGS:
            return f"\n\n{inner.strip()}\n\n"
        return inner

    # ---------------- 块级元素 ----------------

    def _h_p(self, node: Tag) -> str:
        inner = self._render_children(node).strip()
        if not inner:
            return ""
        # 段落里只有一张公式图、且没有其它文字 → 提升为独立公式块
        children = list(node.children)
        tags = [c for c in children if isinstance(c, Tag)]
        text = "".join(str(c) for c in children if isinstance(c, NavigableString)).strip()
        if len(tags) == 1 and tags[0].name == "img" and not text:
            tex = equation_tex(pick_img_src(tags[0]))
            if tex:
                return f"\n\n$$\n{tex}\n$$\n\n"
        return f"\n\n{inner}\n\n"

    def _heading(self, node: Tag, level_hint: int | None = None) -> str:
        level = level_hint or int(node.name[1])
        level = max(1, min(6, level))
        inner = self._render_children(node).strip()
        if not inner:
            return ""
        # 知乎正文标题通常从 h2 开始，整体下压一级更符合笔记层级
        return f"\n\n{'#' * level} {inner}\n\n"

    def _h_h1(self, node): return self._heading(node)
    def _h_h2(self, node): return self._heading(node)
    def _h_h3(self, node): return self._heading(node)
    def _h_h4(self, node): return self._heading(node)
    def _h_h5(self, node): return self._heading(node)
    def _h_h6(self, node): return self._heading(node)

    def _h_blockquote(self, node: Tag) -> str:
        inner = self._render_children(node).strip()
        if not inner:
            return ""
        quoted = "\n".join(
            ("> " + line) if line.strip() else ">"
            for line in inner.splitlines()
        )
        return f"\n\n{quoted}\n\n"

    def _h_ul(self, node: Tag) -> str:
        return self._list(node, ordered=False)

    def _h_ol(self, node: Tag) -> str:
        return self._list(node, ordered=True)

    def _list(self, node: Tag, ordered: bool, depth: int = 0) -> str:
        lines: list[str] = []
        idx = 0
        for li in node.find_all("li", recursive=False):
            idx += 1
            # 先把嵌套列表摘出来，避免和内文混在一起
            nested: list[Tag] = []
            for sub in li.find_all(["ul", "ol"], recursive=False):
                sub.extract()
                nested.append(sub)

            text = self._render_children(li).strip().replace("\n\n", " ").strip()
            bullet = f"{idx}." if ordered else "-"
            lines.append(f"{'  ' * depth}{bullet} {text}".rstrip())

            for sub in nested:
                sub_md = self._list(sub, ordered=(sub.name == "ol"), depth=depth + 1)
                if sub_md.strip():
                    lines.append(sub_md.strip("\n"))
        return "\n\n" + "\n".join(lines) + "\n\n"

    def _h_pre(self, node: Tag) -> str:
        code_tag = node.find("code")
        raw = (code_tag or node).get_text()
        raw = raw.replace("\r\n", "\n").strip("\n")

        lang = ""
        for cand in (code_tag, node):
            if cand is None:
                continue
            for attr in ("data-language", "lang", "language"):
                v = cand.get(attr)
                if v:
                    lang = str(v).strip().lower()
                    break
            if lang:
                break
        if not lang:
            cls = " ".join((code_tag or node).get("class", []))
            m = re.search(r"language-([\w+#-]+)", cls)
            if m:
                lang = m.group(1).lower()
        lang = re.sub(r"[^\w+#-]", "", lang)

        # 代码里可能出现反引号，用足够的围栏长度包住
        fence = "```"
        while fence in raw:
            fence += "`"
        return f"\n\n{fence}{lang}\n{raw}\n{fence}\n\n"

    def _h_code(self, node: Tag) -> str:
        # 行内代码
        if node.find_parent("pre"):
            return self._render_children(node)
        raw = node.get_text()
        if not raw.strip():
            return ""
        ticks = "`"
        while ticks in raw:
            ticks += "`"
        pad = " " if raw.startswith("`") or raw.endswith("`") else ""
        return f"{ticks}{pad}{raw}{pad}{ticks}"

    def _h_hr(self, node: Tag) -> str:
        return "\n\n---\n\n"

    def _h_br(self, node: Tag) -> str:
        return "\n"

    def _h_figure(self, node: Tag) -> str:
        img = node.find("img")
        cap = node.find("figcaption")
        out = []
        if img is not None:
            out.append(self._img(img))
        if cap is not None:
            text = self._render_children(cap).strip()
            if text:
                out.append(f"\n*{text}*\n")
        return "\n\n" + "".join(out).strip() + "\n\n"

    def _h_table(self, node: Tag) -> str:
        rows: list[list[str]] = []
        for tr in node.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            rows.append([self._render_children(c).strip().replace("\n", " ") for c in cells])
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head = rows[0]
        body = rows[1:]
        lines = ["| " + " | ".join(head) + " |", "|" + "|".join([" --- "] * width) + "|"]
        for r in body:
            lines.append("| " + " | ".join(r) + " |")
        return "\n\n" + "\n".join(lines) + "\n\n"

    def _h_details(self, node: Tag) -> str:
        summary = node.find("summary")
        title = self._render_children(summary).strip() if summary else "展开"
        if summary:
            summary.extract()
        inner = self._render_children(node).strip()
        return f"\n\n> **{title}**\n>\n> " + inner.replace("\n", "\n> ") + "\n\n"

    def _h_video(self, node: Tag) -> str:
        src = pick_img_src(node) or node.get("src") or ""
        return f"\n\n[视频]({clean_url(src)})\n\n" if src else ""

    # ---------------- 行内元素 ----------------

    def _img(self, node: Tag) -> str:
        src = pick_img_src(node)
        if not src:
            return ""
        # 公式图片 → LaTeX
        tex = equation_tex(src)
        if tex:
            return f" ${tex}$ "
        alt = (node.get("alt") or "").strip()
        if self.collect_images:
            self.images.append(src)
        return f"\n\n![{alt}]({src})\n\n"

    def _h_img(self, node: Tag) -> str:
        return self._img(node)

    def _h_a(self, node: Tag) -> str:
        href = clean_url(node.get("href") or "")
        text = self._render_children(node).strip()
        if not href:
            return text
        # 锚点、空链接
        if href.startswith("#") or href.startswith("javascript:"):
            return text
        if not text:
            return href
        # 图片链接：直接给地址
        if text == href:
            return f"<{href}>"
        return f"[{text}]({href})"

    def _h_b(self, node): return f"**{self._render_children(node).strip()}**"
    def _h_strong(self, node): return self._h_b(node)
    def _h_i(self, node): return f"*{self._render_children(node).strip()}*"
    def _h_em(self, node): return self._h_i(node)
    def _h_u(self, node): return f"<u>{self._render_children(node).strip()}</u>"
    def _h_del(self, node): return f"~~{self._render_children(node).strip()}~~"
    def _h_s(self, node): return self._h_del(node)
    def _h_mark(self, node): return f"=={self._render_children(node).strip()}=="
    def _h_sup(self, node): return f"<sup>{self._render_children(node).strip()}</sup>"
    def _h_sub(self, node): return f"<sub>{self._render_children(node).strip()}</sub>"

    # ---------------- 收尾整理 ----------------

    @staticmethod
    def _tidy(md: str) -> str:
        md = md.replace("\u200b", "").replace("\ufeff", "")
        md = md.replace("\xa0", " ")
        # 三个以上换行压成两个
        md = re.sub(r"\n{3,}", "\n\n", md)
        # 行尾空格
        md = re.sub(r"[ \t]+\n", "\n", md)
        # ![alt](url) 前后多余空行压缩
        md = re.sub(r"\n{2,}(!\[)", r"\n\n\1", md)
        return md.strip()


def html_to_markdown(html: str, images: list[str] | None = None) -> str:
    """转换入口。

    参数
    ----
    html    : 知乎 content 字段的 HTML
    images  : 可选，传入一个 list，函数会把遇到的图片地址按顺序追加进去
    """
    conv = ZhihuHtmlToMarkdown()
    md = conv.convert(html)
    if images is not None:
        images.extend(conv.images)
    return md


if __name__ == "__main__":
    demo = """
    <p>这是<b>加粗</b>和<em>斜体</em>，以及<a href="https://link.zhihu.com/?target=https%3A%2F%2Fexample.com">外链</a>。</p>
    <p><img class="eeimg" src="https://www.zhihu.com/equation?tex=E%3Dmc%5E2" alt="E=mc^2"></p>
    <ul><li>第一项<ul><li>嵌套项</li></ul></li><li>第二项</li></ul>
    <pre lang="python"><code>print("hello")</code></pre>
    <blockquote><p>引用文字</p></blockquote>
    """
    print(html_to_markdown(demo))
