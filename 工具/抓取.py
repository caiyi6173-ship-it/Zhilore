# -*- coding: utf-8 -*-
"""
知乎收藏夹 → Markdown 抓取器
=============================

思路：
    默认通过知乎官方 zhihu-cli 和开放平台 Access Secret 读取本人公开范围内
    的收藏夹与收藏内容。网页 Cookie 抓取保留为兼容模式，用于官方接口暂未
    返回的数据；两种来源最终都适配为同一种笔记保存结构。

用法：
    python 抓取.py --列表             通过官方 CLI 列出收藏夹
    python 抓取.py                   抓取 配置.json 里配置的全部收藏夹
    python 抓取.py --收藏夹 "AI工具"  只抓指定收藏夹
    python 抓取.py --网页 --登录      网页兼容模式：扫码登录一次
    python 抓取.py --网页 --列表      网页兼容模式：列出收藏夹

产出：
    笔记库/<收藏夹名>/<标题>.md
    笔记库/<收藏夹名>/assets/<文章名>/001.jpg ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------- 路径常量

工具目录 = os.path.dirname(os.path.abspath(__file__))
项目根 = os.path.dirname(工具目录)
笔记库 = os.path.join(项目根, "笔记库")
配置路径 = os.path.join(工具目录, "配置.json")
浏览器数据目录 = os.path.join(工具目录, "_浏览器数据")
抓取缓存目录 = os.path.join(工具目录, "_抓取缓存")
原始收件箱 = os.path.join(笔记库, ".raw", "inbox")

# ---------------------------------------------------------------- 小工具


def 读配置() -> dict:
    if not os.path.isfile(配置路径):
        return {"收藏夹": [], "选项": {}}
    with open(配置路径, "r", encoding="utf-8") as f:
        return json.load(f)


def 默认选项(cfg: dict) -> dict:
    base = {
        "抓取方式": "官方CLI",
        "抓取正文": True,
        "下载图片": True,
        "增量更新": True,
        "每篇最多图片": 40,
        "请求间隔秒": 1.0,
        "无头模式": True,
    }
    base.update(cfg.get("选项", {}) or {})
    return base


def 净化文件名(name: str, 最大长度: int = 80) -> str:
    """去掉 Windows 不允许的字符，压缩空白，限制长度。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > 最大长度:
        name = name[:最大长度].rstrip()
    return name or "未命名"


def 收藏夹ID(地址: str) -> str:
    """从 https://www.zhihu.com/collection/123456 里取出 123456"""
    m = re.search(r"collection/(\d+)", 地址 or "")
    return m.group(1) if m else ""


def 收藏夹Token(配置: dict) -> str:
    """兼容官方 CLI 的 URLToken、旧配置 ID 和收藏夹 URL。"""
    token = 配置.get("URLToken") or 配置.get("UrlToken") or 配置.get("ID")
    return str(token or 收藏夹ID(配置.get("地址", "")))


def 图片目录名(标题: str) -> str:
    """图片存放目录名。

    Markdown 的链接地址不能含空格，否则 marked 之类的解析器不会把它当图片。
    所以这里把空格换成短横线，中文和全角标点保留（它们是安全的）。
    """
    return 净化文件名(标题).replace(" ", "-")


def 构造原文链接(类型: str, 内容: dict) -> str:
    """由接口返回的内容对象拼出知乎原文地址。"""
    内容ID = 内容.get("id")
    if 类型 == "answer":
        问题ID = (内容.get("question") or {}).get("id")
        return f"https://www.zhihu.com/question/{问题ID}/answer/{内容ID}"
    return f"https://zhuanlan.zhihu.com/p/{内容ID}"


def 时间戳转日期(ts) -> str:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def 日志(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 抓取器

# 在页面里发请求：让知乎自己的拦截器负责签名
JS取JSON = r"""
async (args) => {
  const [url, referer] = args;
  try {
    const resp = await fetch(url, {
      credentials: 'include',
      headers: {
        'x-requested-with': 'fetch',
        'accept': 'application/json, text/plain, */*'
      }
    });
    if (!resp.ok) return {__error: resp.status};
    const text = await resp.text();
    try { return JSON.parse(text); }
    catch (e) { return {__error: 'not-json', __text: text.slice(0, 300)}; }
  } catch (e) {
    return {__error: 'network', __text: String(e).slice(0, 300)};
  }
}
"""

JS检测登录 = r"""
() => {
  const badge = document.querySelector('.AppHeader-profile, .Avatar--user, [aria-label="个人中心"]');
  const hasLoginBtn = !!document.querySelector('.SignFlow, .Button--blue[href*="signin"], .SignFlowButton');
  return { logged_in: !!badge && !hasLoginBtn };
}
"""


class 知乎抓取器:
    def __init__(self, 有头: bool = False, 选项: dict | None = None):
        self.有头 = 有头
        self.选项 = 选项 or {}
        self.playwright = None
        self.context = None
        self.page = None
        self.图片总数 = 0

    # ---------------- 浏览器生命周期 ----------------

    def 打开(self):
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            日志("未检测到 patchright，回退到 playwright")
            from playwright.sync_api import sync_playwright  # type: ignore

        os.makedirs(浏览器数据目录, exist_ok=True)
        self.playwright = sync_playwright().start()
        # 用持久化用户目录，登录一次长期有效
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=浏览器数据目录,
            headless=not self.有头,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context.set_default_timeout(30000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def 关闭(self):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def __enter__(self):
        self.打开()
        return self

    def __exit__(self, *exc):
        self.关闭()

    # ---------------- 登录 ----------------

    def 登录(self):
        self.有头 = True
        if not self.page:
            self.打开()
        日志("正在打开知乎，请在浏览器里完成登录（扫码或账号密码）…")
        self.page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded")
        # 最多等 5 分钟
        for _ in range(300):
            time.sleep(1)
            try:
                if self.已登录():
                    日志("检测到登录成功，登录态已保存到 工具/_浏览器数据/")
                    return True
            except Exception:
                pass
        日志("等待超时。如果浏览器里其实已经登录成功，直接重新运行抓取即可。")
        return False

    def 已登录(self) -> bool:
        try:
            r = self.page.evaluate(JS检测登录)
            if r.get("logged_in"):
                return True
        except Exception:
            pass
        # 兜底：看接口能不能拿到自己的信息
        data = self.取JSON("https://www.zhihu.com/api/v4/me?include=name,url_token")
        return isinstance(data, dict) and "name" in data

    # ---------------- 请求封装 ----------------

    def 取JSON(self, url: str, referer: str = "https://www.zhihu.com/") -> dict | list | None:
        if not self.page:
            return None
        try:
            r = self.page.evaluate(JS取JSON, [url, referer])
        except Exception as e:
            日志(f"请求异常：{e}")
            return None
        if isinstance(r, dict) and "__error" in r:
            code = r["__error"]
            if code in (401, 403):
                日志(f"被拒绝（{code}），登录态可能已过期，请重新 --登录")
            else:
                日志(f"请求失败：{code} {r.get('__text','')[:120]}")
            return None
        return r

    def 确保在知乎(self):
        cur = self.page.url or ""
        if "zhihu.com" not in cur:
            self.page.goto("https://www.zhihu.com/", wait_until="domcontentloaded")
            time.sleep(1.5)

    # ---------------- 收藏夹 ----------------

    def 列出收藏夹(self) -> list[dict]:
        """列出当前账号的全部收藏夹（含自己创建的）。"""
        self.确保在知乎()
        me = self.取JSON("https://www.zhihu.com/api/v4/me?include=name,url_token")
        if not isinstance(me, dict) or "url_token" not in me:
            日志("拿不到账号信息，可能未登录。")
            return []
        日志(f"当前账号：{me.get('name')}")
        结果, offset = [], 0
        while True:
            url = (f"https://www.zhihu.com/api/v4/people/{me['url_token']}/collections"
                   f"?limit=20&offset={offset}")
            data = self.取JSON(url)
            if not isinstance(data, dict) or "data" not in data:
                break
            for it in data["data"]:
                # 只保留自己创建的（收藏别人的也行，视需要）
                结果.append({
                    "名称": it.get("title", ""),
                    "地址": f"https://www.zhihu.com/collection/{it.get('id')}",
                    "数量": it.get("item_count", 0),
                    "ID": it.get("id"),
                })
            paging = data.get("paging", {})
            if paging.get("is_end"):
                break
            offset += 20
            time.sleep(0.4)
        return 结果

    def 抓收藏夹条目(self, cid: str) -> list[dict]:
        """翻页取收藏夹里的全部条目（每条含一篇回答/文章的基本信息）。"""
        条目, offset = [], 0
        while True:
            url = (f"https://www.zhihu.com/api/v4/collections/{cid}/items"
                   f"?offset={offset}&limit=20")
            data = self.取JSON(url, referer=f"https://www.zhihu.com/collection/{cid}")
            if not isinstance(data, dict) or "data" not in data:
                break
            条目.extend(data["data"])
            paging = data.get("paging", {})
            if paging.get("is_end"):
                break
            offset += 20
            time.sleep(float(self.选项.get("请求间隔秒", 1.0)) * 0.4)
        return 条目

    # ---------------- 正文 ----------------

    def 取正文(self, 类型: str, 内容ID: str) -> dict | None:
        """单独取一篇回答/文章的完整内容。"""
        if 类型 == "answer":
            url = (f"https://www.zhihu.com/api/v4/answers/{内容ID}"
                   f"?include=content,excerpt,created_time,updated_time,voteup_count,"
                   f"comment_count,author.name,author.url_token,question.title,question.id")
        else:
            url = (f"https://www.zhihu.com/api/v4/articles/{内容ID}"
                   f"?include=content,excerpt,created,updated,voteup_count,"
                   f"comment_count,author.name,author.url_token,title")
        data = self.取JSON(url)
        return data if isinstance(data, dict) and "content" in data else None

    # ---------------- 图片 ----------------

    def 下载图片(self, urls: list[str], 目标目录: str) -> dict[str, str]:
        """下载图片到本地，返回 {原地址: 相对路径}"""
        映射: dict[str, str] = {}
        if not urls:
            return 映射
        os.makedirs(目标目录, exist_ok=True)
        cookies = ({c["name"]: c["value"] for c in self.context.cookies()}
                   if self.context else {})
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            "Referer": "https://www.zhihu.com/",
        }
        try:
            import requests
        except ImportError:
            日志("requests 不可用，跳过图片下载")
            return 映射

        for i, u in enumerate(urls, 1):
            if u in 映射:
                continue
            ext = ".jpg"
            m = re.search(r"\.(jpg|jpeg|png|gif|webp|bmp)", u, re.I)
            if m:
                ext = "." + m.group(1).lower()
            文件名 = f"{i:03d}{ext}"
            路径 = os.path.join(目标目录, 文件名)
            if os.path.isfile(路径) and os.path.getsize(路径) > 0:
                映射[u] = 文件名
                continue
            try:
                r = requests.get(u, headers=headers, cookies=cookies, timeout=25)
                if r.status_code == 200 and r.content:
                    with open(路径, "wb") as f:
                        f.write(r.content)
                    映射[u] = 文件名
                    self.图片总数 += 1
            except Exception:
                pass
            time.sleep(0.15)
        return 映射

    def 收集已有链接(self, 收藏夹名: str) -> set[str]:
        """扫一遍本地已抓过的文章链接，供增量模式跳过。"""
        目录们 = [
            os.path.join(原始收件箱, 净化文件名(收藏夹名)),
            os.path.join(笔记库, ".raw", "sources", 净化文件名(收藏夹名)),
            os.path.join(笔记库, 净化文件名(收藏夹名)),
        ]
        链接: set[str] = set()
        for 目录 in 目录们:
            if not os.path.isdir(目录):
                continue
            for f in os.listdir(目录):
                if not f.endswith(".md"):
                    continue
                try:
                    with open(os.path.join(目录, f), "r", encoding="utf-8") as fh:
                        head = fh.read(2000)
                    m = re.search(r"^原链接:\s*(\S+)", head, re.M)
                    if m:
                        链接.add(m.group(1).strip())
                except Exception:
                    pass
        return 链接

    # ---------------- 保存一篇笔记 ----------------

    def 保存笔记(self, 收藏夹名: str, 条目: dict, 正文数据: dict):
        图片列表: list[str] = []
        if "_markdown_content" in 正文数据:
            md正文 = 正文数据.get("_markdown_content") or ""
        else:
            from html2md import html_to_markdown  # 同目录模块
            content = 正文数据.get("content") or ""
            md正文 = html_to_markdown(content, 图片列表)

        原始类型 = str(正文数据.get("type") or "article").lower()
        类型表 = {
            "answer": "回答", "article": "文章", "zvideo": "视频",
            "pin": "想法", "question": "问题",
        }
        类型 = 类型表.get(原始类型, 原始类型 or "内容")
        if 原始类型 == "answer":
            标题 = (正文数据.get("question") or {}).get("title") or "知乎回答"
        else:
            标题 = 正文数据.get("title") or "知乎内容"
        原链接 = 正文数据.get("_original_url") or 构造原文链接(原始类型, 正文数据)

        作者 = (正文数据.get("author") or {}).get("name", "")
        发布时间 = 时间戳转日期(正文数据.get("created_time") or 正文数据.get("created"))
        更新时间 = 时间戳转日期(正文数据.get("updated_time") or 正文数据.get("updated"))
        收藏时间 = 时间戳转日期(条目.get("created_time"))

        # New captures are raw evidence. 知识重构.py turns them into article folders.
        收藏夹目录 = os.path.join(原始收件箱, 净化文件名(收藏夹名))
        os.makedirs(收藏夹目录, exist_ok=True)

        安全标题 = 净化文件名(标题)
        笔记路径 = os.path.join(收藏夹目录, f"{安全标题}.md")
        # 重名时加后缀
        n = 2
        while os.path.isfile(笔记路径):
            已有 = False
            try:
                with open(笔记路径, "r", encoding="utf-8") as f:
                    已有 = f"原链接: {原链接}" in f.read()
            except Exception:
                pass
            if 已有:
                break          # 同一篇，直接覆盖更新
            笔记路径 = os.path.join(收藏夹目录, f"{安全标题} ({n}).md")
            n += 1

        # 下载图片并替换链接
        if self.选项.get("下载图片", True) and 图片列表:
            限量 = int(self.选项.get("每篇最多图片", 40))
            urls = 图片列表[:限量]
            图片夹名 = 图片目录名(标题)
            图片目录 = os.path.join(收藏夹目录, "assets", 图片夹名)
            映射 = self.下载图片(urls, 图片目录)
            for 原, 新 in 映射.items():
                md正文 = md正文.replace(原, f"assets/{图片夹名}/{新}")

        字数 = len(re.sub(r"\s", "", md正文))
        阅读分钟 = max(1, round(字数 / 400))

        front = [
            "---",
            f"标题: {标题}",
            f"类型: {类型}",
            f"作者: {作者}",
            f"收藏夹: {收藏夹名}",
            f"原链接: {原链接}",
            f"发布时间: {发布时间}",
            f"更新时间: {更新时间}",
            f"收藏时间: {收藏时间}",
            f"赞同数: {正文数据.get('voteup_count', 0)}",
            f"评论数: {正文数据.get('comment_count', 0)}",
            f"收藏数: {正文数据.get('favorite_count', 0)}",
            f"正文来源: {正文数据.get('_content_source', '知乎网页正文')}",
            f"字数: {字数}",
            f"阅读时长: {阅读分钟} 分钟",
            f"抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "标签: []",
            "---",
            "",
            f"# {标题}",
            "",
            f"> [!info] 来源",
            f"> 作者：{作者} ｜ 类型：{类型} ｜ 收藏于 {收藏时间 or '未知'}\n"
            f"> 原文：<{原链接}>",
            "",
            md正文,
            "",
        ]
        with open(笔记路径, "w", encoding="utf-8") as f:
            f.write("\n".join(front))
        return 笔记路径

    def 抓取官方收藏夹(self, 收藏夹配置: dict, cli) -> int:
        """通过官方 CLI 读取收藏夹，并适配到现有 保存笔记 输入结构。"""
        名称 = 收藏夹配置.get("名称") or 收藏夹配置.get("地址", "")
        token = 收藏夹Token(收藏夹配置)
        if not token or not token.isdigit() or int(token) <= 0:
            日志(f"跳过「{名称}」：缺少有效的 URLToken/ID/收藏夹地址")
            return 0

        日志(f"通过官方 CLI 抓取收藏夹「{名称}」（URLToken {token}）")
        条目列表 = cli.收藏夹条目(token)
        if not 条目列表:
            日志(f"「{名称}」没有开放平台可见的公开收藏内容")
            return 0
        日志(f"共 {len(条目列表)} 条开放平台可见内容")

        增量 = bool(self.选项.get("增量更新", True))
        已有链接 = self.收集已有链接(名称) if 增量 else set()
        成功, 跳过 = 0, 0
        for i, item in enumerate(条目列表, 1):
            原链接 = item.get("url", "")
            if 增量 and 原链接 and 原链接 in 已有链接:
                跳过 += 1
                continue
            标题 = item.get("title") or "知乎收藏内容"
            日志(f"  [{i}/{len(条目列表)}] {标题[:40]}")
            原始类型 = item.get("type") or "unknown"
            正文 = {
                "type": 原始类型,
                "title": 标题,
                "question": {"title": 标题} if 原始类型 == "answer" else {},
                "author": item.get("author") or {},
                "created_time": item.get("created_at", 0),
                "updated_time": item.get("created_at", 0),
                "voteup_count": item.get("like_count", 0),
                "comment_count": item.get("comment_count", 0),
                "favorite_count": item.get("favorite_count", 0),
                "_original_url": 原链接,
                "_markdown_content": item.get("summary") or "_开放平台未返回摘要。_",
                "_content_source": "知乎开放平台摘要",
            }
            条目 = {"created_time": item.get("fav_time", 0)}
            try:
                self.保存笔记(名称, 条目, 正文)
                成功 += 1
            except Exception as exc:
                日志(f"    保存失败：{exc}")
            time.sleep(float(self.选项.get("请求间隔秒", 1.0)))

        日志(f"「{名称}」跳过 {跳过} 篇已存在内容，新增 {成功} 篇")
        return 成功

    # ---------------- 主流程 ----------------

    def 抓取收藏夹(self, 收藏夹配置: dict) -> int:
        名称 = 收藏夹配置.get("名称") or 收藏夹配置.get("地址", "")
        cid = 收藏夹配置.get("ID") or 收藏夹ID(收藏夹配置.get("地址", ""))
        if not cid:
            日志(f"跳过「{名称}」：地址里找不到收藏夹 ID")
            return 0

        self.确保在知乎()
        日志(f"开始抓取收藏夹「{名称}」（ID {cid}）")
        条目列表 = self.抓收藏夹条目(cid)
        if not 条目列表:
            日志(f"「{名称}」没抓到条目，可能收藏夹为空或权限不足")
            return 0
        日志(f"共 {len(条目列表)} 条")

        增量 = bool(self.选项.get("增量更新", True))
        已有链接 = self.收集已有链接(名称) if 增量 else set()
        if 已有链接:
            日志(f"本地已有 {len(已有链接)} 篇，增量模式将跳过它们")

        成功, 跳过 = 0, 0
        for i, 条目 in enumerate(条目列表, 1):
            内容 = 条目.get("content") or {}
            类型 = 内容.get("type", "answer")
            内容ID = str(内容.get("id") or "")
            标题 = (内容.get("question") or {}).get("title") or 内容.get("title") or 内容ID
            if not 内容ID:
                continue

            if 增量 and 构造原文链接(类型, 内容) in 已有链接:
                跳过 += 1
                continue

            日志(f"  [{i}/{len(条目列表)}] {标题[:40]}")
            正文 = None
            if self.选项.get("抓取正文", True):
                正文 = self.取正文(类型, 内容ID)
            if 正文 is None:
                # 退而求其次：用列表接口里带的 content
                if 内容.get("content"):
                    正文 = dict(内容)
                    正文["type"] = 类型
                else:
                    日志("    取正文失败，跳过")
                    continue
            try:
                self.保存笔记(名称, 条目, 正文)
                成功 += 1
            except Exception as e:
                日志(f"    保存失败：{e}")
            time.sleep(float(self.选项.get("请求间隔秒", 1.0)))

        if 跳过:
            日志(f"「{名称}」跳过 {跳过} 篇已存在的，新增 {成功} 篇")
        return 成功


# ---------------------------------------------------------------- 命令行


def 主函数():
    parser = argparse.ArgumentParser(
        description="知乎收藏夹 → Markdown（供 Obsidian 风格网页浏览）")
    parser.add_argument("--登录", action="store_true", help="打开浏览器登录知乎，保存登录态")
    parser.add_argument("--列表", action="store_true", help="列出账号下的全部收藏夹")
    parser.add_argument("--收藏夹", type=str, default="", help="只抓指定名称的收藏夹")
    parser.add_argument("--有头", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--网页", action="store_true", help="使用旧网页 Cookie 抓取模式")
    parser.add_argument("--官方", action="store_true", help="强制使用官方 CLI（默认）")
    parser.add_argument("--忽略增量", action="store_true", help="不跳过已抓过的文章")
    args = parser.parse_args()

    cfg = 读配置()
    选项 = 默认选项(cfg)
    if args.忽略增量:
        选项["增量更新"] = False

    os.makedirs(笔记库, exist_ok=True)
    os.makedirs(抓取缓存目录, exist_ok=True)

    抓取方式 = str(选项.get("抓取方式", "官方CLI"))
    使用网页 = args.网页 or args.登录 or (抓取方式 == "网页" and not args.官方)

    if not 使用网页:
        from 官方CLI import 官方CLI客户端, 官方CLI错误
        try:
            cli = 官方CLI客户端()
            抓取器 = 知乎抓取器(选项=选项)
            if args.列表:
                夹子 = cli.列出收藏夹()
                print("\n开放平台可见的收藏夹：\n")
                for c in 夹子:
                    print(f"  {c['数量']:>5} 篇  {c['名称']}")
                    print(f"          {c['地址']}  (URLToken: {c['URLToken']})")
                if not 夹子:
                    日志("开放平台没有返回收藏夹；只会读取本人公开范围内的数据。")
                return

            待抓 = cfg.get("收藏夹", [])
            if args.收藏夹:
                待抓 = [c for c in 待抓 if args.收藏夹 in c.get("名称", "")]
            if not 待抓:
                日志("配置里没有要抓的收藏夹。")
                日志("先运行 python 抓取.py --列表，再把 URLToken/地址填入 工具/配置.json")
                return
            总成功 = sum(抓取器.抓取官方收藏夹(c, cli) for c in 待抓)
            日志(f"完成，共写入 {总成功} 篇开放平台笔记。")
            日志("注意：当前收藏 API 只返回摘要，不返回完整正文。")
            日志("下一步：python 知识重构.py && python 处理.py")
            return
        except 官方CLI错误 as exc:
            日志(f"官方 CLI 模式失败：{exc}")
            sys.exit(3)

    with 知乎抓取器(有头=args.有头 or args.登录, 选项=选项) as 抓取器:
        if args.登录:
            抓取器.登录()
            return

        if not 抓取器.已登录():
            日志("没有检测到有效登录态。请先运行：  python 抓取.py --网页 --登录")
            sys.exit(3)

        if args.列表:
            夹子 = 抓取器.列出收藏夹()
            if not 夹子:
                日志("没能列出收藏夹。")
                return
            print("\n你账号下的收藏夹：\n")
            for c in 夹子:
                print(f"  {c['数量']:>5} 篇  {c['名称']}")
                print(f"          {c['地址']}")
            print("\n把想要的填进 工具/配置.json 的「收藏夹」里即可。\n")
            return

        待抓 = cfg.get("收藏夹", [])
        if args.收藏夹:
            待抓 = [c for c in 待抓 if args.收藏夹 in c.get("名称", "")]
        if not 待抓:
            日志("配置里没有要抓的收藏夹。")
            日志("先运行  python 抓取.py --网页 --列表  看看有哪些，再填进 工具/配置.json")
            return

        总成功 = 0
        for c in 待抓:
            总成功 += 抓取器.抓取收藏夹(c)

        日志(f"完成，共写入 {总成功} 篇笔记，下载图片 {抓取器.图片总数} 张。")
        日志(f"笔记目录：{笔记库}")
        日志("下一步：python 知识重构.py && python 处理.py")


if __name__ == "__main__":
    主函数()




