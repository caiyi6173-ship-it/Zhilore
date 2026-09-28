# -*- coding: utf-8 -*-
"""直答处理链路的浏览器预览夹具。仅本机回环，不联网、不消耗任何真实直答额度。

    D:\\python\\python.exe -X utf8 工具\\tests\\serve_zhida_preview.py
    浏览器打开 http://127.0.0.1:8102/__fixture/登录          → 登录后落到账号页
               http://127.0.0.1:8102/__fixture/登录?落到=/workspace/  → 直接看卡片列表

`/__fixture/登录` 只负责种一个假会话 cookie，之后账号页、工作区、接口全部走真实
代码与真实门禁。工作区数据落在本进程的临时目录，不会写进本机 `数据/用户工作区.sqlite3`。
"""
from __future__ import annotations

import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from zhihu_oauth.collection_data import ContentPage, creation_item
from zhihu_oauth.provider import TokenGrant
from zhihu_oauth.store import SessionStore
from zhihu_oauth.workspace import WorkspaceRepository
import 应用 as 应用模块
from test_oauth import Clock, FakeProvider, TEST_SETTINGS, collection

ORIGIN = "http://127.0.0.1:8102"
设置 = replace(TEST_SETTINGS, redirect_uri=ORIGIN + "/auth/zhihu/callback")


# 创作内容与收藏一样只有标题+摘要，但没有 FavTime、也没有 Author（作者就是本人）。
创作样例 = [
    ("answer", "为什么大模型会「一本正经地胡说八道」？"),
    ("article", "我用三个月搭了一套个人知识库"),
    ("zvideo", "三分钟讲清 RAG 的两种做法"),
    ("pin", "今天把收藏夹清理了一遍"),
    ("question", "普通人该怎么开始用 AI？"),
]


def 创作网址(类型, 号):
    """URL 必须能被 content_link 反解出同一个 id，否则服务端会判协议错误。"""
    if 类型 == "answer":
        return f"https://www.zhihu.com/question/1/answer/{号}"
    if 类型 == "article":
        return f"https://zhuanlan.zhihu.com/p/{号}"
    if 类型 == "zvideo":
        return f"https://www.zhihu.com/zvideo/{号}"
    if 类型 == "pin":
        return f"https://www.zhihu.com/pin/{号}"
    return f"https://www.zhihu.com/question/{号}"


class 预览Provider(FakeProvider):
    """一个账号、两个公开收藏夹（42 + 28 条），全部内存构造。

    条目刻意做成"像真的"：有像样的标题、作者、赞同数与收录时间，
    这样预览卡片视图时看到的效果和真实收录接近。数字全是编的。
    """

    标题样例 = [
        "codex 有哪些奇技淫巧？",
        "35 岁了，想学习 AI，搞懂 AI，用好 AI 还来得及吗？",
        "本地跑大模型，显存不够时你会先砍哪一项？",
        "用 AI 写周报，怎么避免一眼假？",
        "RAG 到底该先做向量库还是先做评测？",
        "小团队要不要自建推理服务？",
    ]
    作者样例 = ["啵啵", ":-) Tool", "陈默", "林一", "阿远", "苏和"]

    def __init__(self, clock):
        super().__init__(clock)
        self.pages = {}
        self.items["mock-token-a"] = [collection(101, "我的收藏"), collection(202, "AI 工具")]
        现在 = int(time.time())
        for 序号, (identifier, 条数) in enumerate(((101, 42), (202, 28))):
            self.pages[("mock-token-a", str(identifier), "0")] = ContentPage(
                [{"id": f"answer:{identifier}{i}", "type": "answer",
                  "url": f"https://www.zhihu.com/question/1/answer/{identifier}{i}",
                  "title": self.标题样例[(i + 序号 * 3) % len(self.标题样例)],
                  "summary": (f"这是第 {i + 1} 条内容的公开摘要（模拟），只用于演示卡片视图。"
                              "真实摘要由知乎开放接口给出，最多到摘要，不给全文。"),
                  "author": self.作者样例[i % len(self.作者样例)],
                  "created_at": 现在 - (i + 1) * 86400,
                  "collected_at": 现在 - 序号 * 3600 - i * 600,
                  "like_count": str(200 + i * 37), "comment_count": str(4 + i * 3),
                  "favorite_count": str(30 + i * 11)}
                 for i in range(条数)], "0", None, str(条数))

    async def collection_contents(self, token, identifier, offset):
        return self.pages[(token, str(identifier), str(offset))]

    async def creations(self, token, content_type="all", offset="0"):
        """假创作数据：按上游"原始格式"构造（与真实响应一样无 FavTime / Author），
        再过一遍 creation_item —— 让归一化路径也被预览覆盖，而不是绕过它。
        （2026-09-15 线上翻车教训：绕过归一化的夹具会掩盖协议错误。）"""
        现在 = int(time.time())
        条目 = []
        for 序号 in range(18):
            类型, 标题 = 创作样例[序号 % len(创作样例)]
            条目.append(creation_item({
                "ContentType": 类型,
                "Url": 创作网址(类型, str(2000 + 序号)),
                "Title": 标题 if 序号 < len(创作样例) else f"{标题}（{序号 // len(创作样例) + 1}）",
                "Summary": ("这是自己创作内容的公开摘要（模拟），只用于演示创作视图；"
                            "真实摘要由知乎开放接口给出，最多到摘要，不给全文。"),
                "CreatedAt": 现在 - (序号 + 1) * 86400,
                "LikeCount": 300 + 序号 * 47, "CommentCount": 9 + 序号 * 2,
                "FavoriteCount": 20 + 序号 * 5,
            }))
        起始 = int(str(offset) or "0")
        页 = 条目[起始:起始 + 20]
        return ContentPage(页, str(起始),
                           str(起始 + 20) if 起始 + 20 < len(条目) else None, str(len(条目)))


class 预览直答客户端:
    """确定性假客户端：不联网，但会真实走完分批、进度、写入与跳转。"""

    可用 = True

    class 配置:
        model = "zhida-thinking-1p5（模拟）"

    def __init__(self, access_secret=None):
        self.自备 = bool(access_secret)

    # 每次 直答工厂() 都会新建实例，所以计数必须放在类上，否则额度永远回到 42。
    累计消耗 = 0

    def 剩余额度(self):
        # 预览：从 42 起按批递减，用来演示"处理完额度会变"。真实额度来自官方额度接口。
        return max(0, 42 - type(self).累计消耗)

    def 凭证有效(self):
        return True  # 预览里任何凭证都算有效（不联网验证）

    def 归并摘要(self, 收藏夹, 条目):
        type(self).累计消耗 += 1
        time.sleep(1.0)  # 让浏览器能看见进度在动
        blocks = []
        for start in range(0, len(条目), 8):
            group = 条目[start:start + 8]
            if not group:
                break
            blocks.append({
                "title": f"{收藏夹} · 主题 {len(blocks) + 1}",
                "markdown": ("## 这一组在讲什么\n\n" + "、".join(i["title"] for i in group[:3]) +
                             " 等条目指向同一主题。\n\n## 要点\n\n- 归并后的第一条要点\n- 归并后的第二条要点"),
                "summary": f"由 {len(group)} 条公开摘要归并而成。",
                "tags": [收藏夹, "模拟标签"], "key_points": ["模拟要点一", "模拟要点二"],
                "sources": [{"title": i["title"], "url": i["url"]} for i in group],
                "model": "zhida-thinking-1p5（模拟）",
            })
        return {"blocks": blocks, "错误": ""}


时钟 = Clock()
会话库 = SessionStore(时钟)
provider = 预览Provider(时钟)
临时目录 = tempfile.TemporaryDirectory(prefix="zhida-preview-")
workspace = WorkspaceRepository(Path(临时目录.name) / "ws.sqlite3")
知识库 = FastAPI()


@知识库.get("/api/索引")
async def 索引():
    return {"文章": [], "入口": [], "收藏夹": [], "标签": [],
            "统计": {"文章数": 0, "笔记数": 0, "收藏夹数": 0, "标签数": 0}}


应用 = 应用模块.建一体化应用(设置, 会话库, SimpleNamespace(app=知识库),
                          provider=provider, 直答工厂=预览直答客户端,
                          工作区仓库=workspace)

夹具登录路径 = "/__fixture/登录"


def 造会话():
    """给夹具账号开一个真会话，返回 (cookie 名, handle)。"""
    handle, _session = 会话库.create(TokenGrant("mock-token-a", 时钟.now + 86400),
                                    provider.users["mock-token-a"])
    return 设置.session_cookie, handle


def 站内路径(值: str, 缺省: str = "/account") -> str:
    """只接受站内绝对路径，挡掉 `//evil.com` 这类协议相对跳转。"""
    if 值.startswith("/") and not 值.startswith("//"):
        return 值
    return 缺省


def 跳账号页(cookie名: str, handle: str, 落到: str = "/account"):
    响应 = RedirectResponse(站内路径(落到), status_code=303)
    响应.set_cookie(cookie名, handle, path="/", httponly=True, samesite="lax")
    return 响应


class 夹具入口:
    """把夹具登录路径挡在门禁之前 —— 它必须在公开路径之外自己处理。

    只有这一条路径走旁路；它之后的账号页、工作区、接口全部走真实代码与真实门禁，
    所以预览里看到的仍然是生产同构的行为。
    """

    def __init__(self, 应用):
        self.应用 = 应用

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == 夹具登录路径:
            查询 = parse_qs(scope.get("query_string", b"").decode("utf-8", "replace"))
            await 跳账号页(*造会话(), 落到=(查询.get("落到") or ["/account"])[0])(scope, receive, send)
            return
        await self.应用(scope, receive, send)


if __name__ == "__main__":
    cookie名, handle = 造会话()
    print("PREVIEW FIXTURES ONLY — 127.0.0.1:8102；不联网、不消耗真实直答额度", flush=True)
    print(f"浏览器打开 http://127.0.0.1:8102{夹具登录路径}", flush=True)
    print(f"SESSION_COOKIE={cookie名}", flush=True)
    print(f"SESSION_HANDLE={handle}", flush=True)
    uvicorn.run(夹具入口(应用), host="127.0.0.1", port=8102, workers=1,
                access_log=False, log_level="warning")
