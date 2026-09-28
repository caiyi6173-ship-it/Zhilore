# -*- coding: utf-8 -*-
"""知乎直答（开发者开放平台）拆分引擎。

与 `LLM处理.LLM客户端` 同构（`可用` / `分析文章`），所以 `知识重构.py` 可以直接换引擎：

    from 知乎直答 import 知乎直答客户端

直答接口的硬约束（官方文档 + 2026-09-13 实测）：

1. 只保证 `model` / `messages` / `stream` 三个字段 —— **没有 `response_format`**，
   结构化输出只能靠提示词约束 + 容错解析（实测会把 JSON 包在 ```json 围栏里）。
2. 鉴权是 Access Secret 的 Bearer + `X-Request-Timestamp`（秒级）。
   **时间戳必须每次请求重新取**，重试时沿用旧时间戳会鉴权失败。
3. 会返回 429 限流 —— 必须退避重试，不能当成失败直接降级。

Access Secret 只从环境变量（或显式传入的 env 文件）读取，绝不写入产物。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from LLM处理 import PROMPT_VERSION, 本地拆分, 敏感信息脱敏, 规范化LLM结果

DEFAULT_BASE_URL = "https://developer.zhihu.com/v1"
DEFAULT_MODEL = "zhida-thinking-1p5"
可选模型 = ("zhida-fast-1p5", "zhida-thinking-1p5", "zhida-agent")
# 实测 zhida-agent 会返回 429，档位语义也偏"检索问答"，默认不用它。
MAX_CONTENT_CHARS = 24000
MAX_BLOCKS_PER_BATCH = 8
MAX_BLOCK_MARKDOWN = 20000
请求超时上限 = 600
# 额度查询是独立端点（public API 前缀不同），且不需要消耗业务额度。
直答APIID = "zhida_openai"
额度缓存秒数 = 30

系统提示 = (
    "这是一次数据转换任务，不是问答：请把用户消息里 JSON 的 content 字段拆成可独立阅读、"
    "可互相关联的 Obsidian 分节，并把结果写成 JSON。不要回答、不要解释、不要寒暄、不要反问。"
    "只输出一个 JSON 对象，不要代码围栏，不要任何解释文字。"
    "schema: {summary:string,article_tags:string[],sections:[{title:string,"
    "markdown:string,tags:string[],key_points:string[]}]}。"
    "要求 3-8 个 sections；每个 section 2-5 个具体标签；markdown 不重复文章一级标题；中文输出。"
    "只使用原文已有的事实、数字、人名和结论，绝不补充原文没有的信息，也不要引用外部资料。"
)

归并系统提示 = (
    "这是一次数据转换任务，不是问答：用户消息里 JSON 的 items 是同一个收藏夹里的若干条内容摘要"
    "（只有标题和摘要，不是全文，每条带一个从 1 开始的序号）。"
    "请把它们按主题归并成 3-6 个可独立阅读的知识块——不是逐条复述，"
    "而是把讲同一件事的条目合并进同一个知识块，讲不同主题的分开。"
    "不要回答、不要解释、不要寒暄、不要反问；直接输出 JSON。"
    "只输出一个 JSON 对象，不要代码围栏，不要任何解释文字。"
    "schema: {blocks:[{title:string,markdown:string,tags:string[],"
    "key_points:string[],sources:number[]}]}。"
    "sources 填该知识块用到的条目序号，必须是数字数组（例如 [1,2,5]），"
    "不要写成字符串、不要写「第 N 条」；每个知识块至少引用一个序号，且不要重复引用同一序号。"
    "markdown 用二级标题和短段落或列表组织，长度 200-800 字。"
    "只使用摘要里已有的信息，绝不补充外部资料，也不要编造数字、人名或结论。中文输出。"
)

# 用户消息里必须带指令：直答是检索问答产品，只给数据、不给任务时它会当成提问来回答
# （2026-09-14 实测：回了一大段"您的问题中没有包含具体的提问内容"）。指令里刻意不出现花括号，
# 保证「用户消息里唯一的 JSON 就是数据」这条性质可被测试和排查复用。
任务说明模板 = (
    "这是一个数据转换任务，不是问答。{动作}。不要回答、不要解释、不要寒暄、不要反问，"
    "也不要在 JSON 前后写任何文字或代码围栏。\n\n"
    "待处理数据（JSON）：\n{数据}\n\n"
    "{结尾要求}"
)
拆分动作 = "请把下面 JSON 里 content 字段的文章按主题拆成分节，并把结果写成 JSON"
拆分结尾 = "现在直接输出那个 JSON 对象（summary、article_tags、sections 字段；sections 每项含 title、markdown、tags、key_points）。"
拆分纠错 = ("注意：你上一次的输出不是合法 JSON，无法解析。请只输出 JSON 对象本身，"
            "第一个字符必须是左花括号，不要任何解释文字，也不要代码围栏。")
归并动作 = "请把下面 JSON 里 items 数组的公开摘要按主题归并成知识块，并把结果写成 JSON"
归并结尾 = ("现在直接输出那个 JSON 对象（blocks 数组，每项含 title、markdown、tags、key_points、sources 字段；"
            "sources 是数字数组）。")
归并纠错 = ("注意：你上一次的输出不是合法 JSON，无法解析。请只输出 JSON 对象本身，"
            "第一个字符必须是左花括号，不要任何解释文字，也不要代码围栏。")


def 组用户消息(动作: str, 数据: dict, 结尾要求: str) -> str:
    return 任务说明模板.format(动作=动作, 数据=json.dumps(数据, ensure_ascii=False), 结尾要求=结尾要求)


def 规范来源序号(值: Any, 上限: int) -> list[int]:
    """把模型给的 sources 归一成合法条目序号。

    实测模型会写成 [1,2] / ["1","2"] / "1,2,3" / "第 1 条、第 2 条" / "3-6" 等各种形态；
    按"只认 int"处理会把它们全丢掉，知识块就失去了知乎原文链接（实测 4 个块全部 0 条来源）。
    """
    原始: list[int] = []

    def 收(单个: Any) -> None:
        if isinstance(单个, bool):
            return
        if isinstance(单个, int):
            原始.append(单个)
            return
        if not isinstance(单个, str):
            return
        for 片段 in re.split(r"[^\d\-—~]+", 单个):
            if not 片段:
                continue
            起止 = re.split(r"[-—~]", 片段)
            if len(起止) == 2 and 起止[0].isdigit() and 起止[1].isdigit():
                起, 止 = int(起止[0]), int(起止[1])
                if 起 <= 止:
                    原始.extend(range(起, min(止, 起 + 99) + 1))
                continue
            if 片段.isdigit():
                原始.append(int(片段))

    for 单个 in (值 if isinstance(值, (list, tuple)) else [值]):
        收(单个)
    结果: list[int] = []
    for 序号 in 原始:
        if 1 <= 序号 <= 上限 and 序号 not in 结果:
            结果.append(序号)
    return 结果


def 载入env文件(path: str) -> None:
    """Load ZHIHU_* settings without overriding variables already set."""
    if not path or not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key.startswith("ZHIHU_") and key not in os.environ:
                os.environ[key] = value


def 提取JSON(text: Any) -> Any:
    """Pull one JSON value out of a model reply.

    The model wraps its answer in a ```json fence even when told not to, and may
    prepend a sentence. Try the fenced payload, then the raw text, then the
    outermost braces. Never raise: callers fall back to local splitting.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.S)
    if fence:
        candidate = fence.group(1).strip()
    candidates = [candidate]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start >= 0 and end > start:
        candidates.append(candidate[start:end + 1])
    for attempt in candidates:
        try:
            return json.loads(attempt)
        except (ValueError, UnicodeError):
            continue
    return None


def _退避秒数(headers, attempt: int) -> float:
    """Honour Retry-After when the platform sends it, otherwise back off."""
    try:
        raw = headers.get("Retry-After") if headers else None
    except Exception:
        raw = None
    if isinstance(raw, str) and raw.strip().isdigit():
        return min(30.0, float(raw.strip()))
    return min(8.0, 1.0 * (2 ** (attempt - 1)))


def 额度URL(base_url: str) -> str:
    """由对话端点推导额度端点：https://developer.zhihu.com/v1 → .../api/v1/quota。"""
    片段 = urllib.parse.urlsplit(base_url)
    return f"{片段.scheme}://{片段.netloc}/api/v1/quota"


@dataclass
class 直答配置:
    enabled: bool
    access_secret: str
    base_url: str
    model: str
    timeout: int
    attempts: int


class 知乎直答客户端:
    """Minimal chat-completions client for developer.zhihu.com."""

    def __init__(self, env_file: str | None = None, access_secret: str | None = None):
        if env_file:
            载入env文件(env_file)
        # 显式传入的凭证优先：用于"用户自备 Access Secret，烧自己的额度"。
        # 环境变量里的应用凭证仍是默认。凭证本身绝不写入产物、日志或响应。
        自备 = (access_secret or "").strip()
        model = os.environ.get("ZHIHU_ZHIDA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.配置 = 直答配置(
            enabled=os.environ.get("ZHIHU_ZHIDA_ENABLED", "true").lower() != "false",
            access_secret=自备 or os.environ.get("ZHIHU_ACCESS_SECRET", "").strip(),
            base_url=os.environ.get("ZHIHU_ZHIDA_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL,
            model=model if model in 可选模型 else DEFAULT_MODEL,
            timeout=min(请求超时上限, max(5, int(os.environ.get("ZHIHU_ZHIDA_TIMEOUT_MS", "180000")) // 1000)),
            attempts=max(1, min(4, int(os.environ.get("ZHIHU_ZHIDA_MAX_ATTEMPTS", "3")))),
        )
        self.引擎 = "直答"
        self.自备凭证 = bool(自备)
        self._额度缓存: tuple[float, tuple[str, int | None]] | None = None

    @property
    def 可用(self) -> bool:
        return self.配置.enabled and bool(self.配置.access_secret)

    def 剩余额度(self) -> int | None:
        """今日直答剩余调用次数；查询本身不消耗额度。

        拿不到就返回 None——额度查不到绝不能挡住主流程。
        """
        return self.额度状态()[1]

    def 凭证有效(self) -> bool | None:
        """校验 Access Secret：True 有效 / False 无效（401/403）/ None 未知（网络等原因）。"""
        return {"有效": True, "无效": False}.get(self.额度状态()[0])

    def 额度状态(self) -> tuple[str, int | None]:
        """返回 ("有效"|"无效"|"未知", 剩余次数)。结果缓存 30 秒。"""
        现在 = time.monotonic()
        if self._额度缓存 and 现在 - self._额度缓存[0] < 额度缓存秒数:
            return self._额度缓存[1]
        结果 = self._查额度() if self.可用 else ("未知", None)
        self._额度缓存 = (现在, 结果)
        return 结果

    def _查额度(self) -> tuple[str, int | None]:
        request = urllib.request.Request(
            f"{额度URL(self.配置.base_url)}?APIIDs={直答APIID}",
            headers={
                "Authorization": f"Bearer {self.配置.access_secret}",
                "X-Request-Timestamp": str(int(time.time())),
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=min(30, self.配置.timeout)) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 401/403 说明这张凭证不可用；其它状态码（含 5xx）无法判定，按"未知"处理。
            return ("无效", None) if exc.code in (401, 403) else ("未知", None)
        except Exception:  # noqa: BLE001 - 额度查询失败不影响主流程
            return "未知", None
        if not isinstance(payload, dict):
            return "未知", None
        for item in payload.get("Data") or []:
            if isinstance(item, dict) and str(item.get("APIID", "")).lower() == 直答APIID:
                try:
                    return "有效", int(item.get("RemainingQuota"))
                except (TypeError, ValueError):
                    return "未知", None
        return "未知", None

    def _请求并解析(self, system: str, user: str, 纠错: str = "") -> tuple[Any, str]:
        """One chat completion with bounded retries. Never raises; returns (parsed, error).

        Only model / messages / stream are sent — every other field is unsupported.
        The timestamp is signed per attempt, because reusing a stale one fails auth.

        输出不是合法 JSON 时，下一次重试会把 `纠错` 追加到用户消息末尾（对模型的权重最高），
        而不是原样重发——原样重发实测仍会得到非 JSON 的闲聊回复。
        """
        url = f"{self.配置.base_url}/chat/completions"
        last_error = "未知错误"
        当前user = user

        for attempt in range(1, self.配置.attempts + 1):
            body = json.dumps({
                "model": self.配置.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": 当前user}],
                "stream": False,
            }, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url, data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.配置.access_secret}",
                    "X-Request-Timestamp": str(int(time.time())),
                },
                method="POST",
            )
            retryable = False
            try:
                with urllib.request.urlopen(request, timeout=self.配置.timeout) as response:
                    parsed, reason = self._解析响应(response.read())
                if parsed is None:
                    last_error = reason
                    retryable = True  # A malformed answer may be transient.
                    if 纠错 and attempt < self.配置.attempts:
                        当前user = user + "\n\n" + 纠错
                        print(f"[直答] 输出不是合法 JSON（第 {attempt}/{self.配置.attempts} 次），"
                              "追加格式要求后重试", flush=True)
                else:
                    return parsed, ""
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code == 429:
                    # 429 有两种成因，必须分开：真实限流（该退避）与当日额度用尽（重试纯属白等）。
                    # 实测 2026-09-14：额度用尽后退避到 65 秒仍然全是 429。
                    剩余 = self.剩余额度()
                    if 剩余 == 0:
                        last_error = "HTTP 429（今日直答额度已用尽，额度按自然日重置）"
                        retryable = False
                    else:
                        retryable = True
                        if attempt < self.配置.attempts:
                            delay = _退避秒数(exc.headers, attempt)
                            额度说明 = "额度剩余未知" if 剩余 is None else f"额度剩余 {剩余} 次"
                            print(f"[直答] 限流（第 {attempt}/{self.配置.attempts} 次，{额度说明}），"
                                  f"等待 {delay:.0f} 秒后重试", flush=True)
                            time.sleep(delay)
                elif exc.code in (500, 502, 503, 504):
                    retryable = True
                    if attempt < self.配置.attempts:
                        time.sleep(_退避秒数(exc.headers, attempt))
                elif exc.code == 401:
                    last_error = "HTTP 401（Access Secret 无效或已过期）"
                elif exc.code == 403:
                    last_error = "HTTP 403（无直答权限）"
                elif exc.code == 400:
                    last_error = "HTTP 400（请求被拒，可能是模型档位或内容过长）"
                else:
                    retryable = exc.code >= 500
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"网络错误 {type(exc).__name__}"
                retryable = True
                if attempt < self.配置.attempts:
                    time.sleep(_退避秒数(None, attempt))

            if not retryable or attempt >= self.配置.attempts:
                break
        return None, last_error

    def _解析响应(self, raw: bytes) -> tuple[dict | None, str]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            return None, "响应不是 JSON"
        if not isinstance(payload, dict):
            return None, "响应顶层不是对象"
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return None, "响应缺少 choices"
        choice = choices[0]
        if choice.get("finish_reason") == "error":
            return None, "流式错误"
        message = choice.get("message")
        if not isinstance(message, dict):
            return None, "响应缺少 message"
        # `reasoning_content` is the thinking model's scratchpad; only `content` is used.
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None, "响应缺少 content"
        parsed = 提取JSON(content)
        if parsed is None:
            return None, "输出不是合法 JSON"
        return parsed, ""

    def 分析文章(self, 标题: str, 正文: str, 已有标签: list[str] | None = None) -> dict[str, Any]:
        if not self.可用:
            return 本地拆分(标题, 正文, 已有标签)

        user = 组用户消息(拆分动作, {
            "title": 标题,
            "existing_tags": 已有标签 or [],
            "content": 敏感信息脱敏(正文)[:MAX_CONTENT_CHARS],
        }, 拆分结尾)
        parsed, last_error = self._请求并解析(系统提示, user, 拆分纠错)
        if parsed is not None:
            try:
                result = 规范化LLM结果(parsed, 标题, 正文, 已有标签)
                result["模型"] = self.配置.model
                result["提示词版本"] = PROMPT_VERSION
                result["引擎"] = self.引擎
                return result
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                # A structurally invalid answer (e.g. section count) will not improve
                # by repeating the same request, so it goes straight to the fallback.
                last_error = f"规范化失败 {type(exc).__name__}: {exc}"

        print(f"[直答] 拆分失败，使用本地规则降级：{last_error}", flush=True)
        if "429" in last_error:
            # 2026-09-14 用官方额度接口（GET /api/v1/quota，不耗额度）查清的实情：
            # 直答（APIID=zhida_openai）当日常见只有 2 次，用完就是持续 429，
            # 加长退避没有意义。查额度可以立刻分清"限流"和"额度用尽"。
            print("[直答] 提示：429 已区分「限流」与「额度用尽」。当前额度（GET "
                  f"{额度URL(self.配置.base_url)}?APIIDs={直答APIID}）："
                  f"剩余 {self.剩余额度()} 次；额度按自然日重置。"
                  "要提升额度可邮件 openplatform@zhihu.com 说明场景与预估调用量。", flush=True)
        fallback = 本地拆分(标题, 正文, 已有标签)
        fallback["模型"] = self.配置.model
        fallback["引擎"] = self.引擎
        fallback["降级原因"] = last_error
        return fallback

    def 归并摘要(self, 收藏夹: str, 条目: list[dict]) -> dict[str, Any]:
        """Merge one batch of public summaries into themed knowledge blocks.

        One request per batch is the whole point: the quota is small, so a request
        must never be spent per item. Returns {"blocks": [...], "错误": str}.
        """
        if not self.可用:
            return {"blocks": [], "错误": "直答不可用（缺少 ZHIHU_ACCESS_SECRET）"}
        if not 条目:
            return {"blocks": [], "错误": "本批没有条目"}

        载荷 = [{"index": index, "title": str(item.get("title") or "")[:300],
                 "summary": 敏感信息脱敏(str(item.get("summary") or ""))[:1000]}
                for index, item in enumerate(条目, 1)]
        user = 组用户消息(归并动作, {"collection": 收藏夹, "count": len(载荷), "items": 载荷}, 归并结尾)

        parsed, last_error = self._请求并解析(归并系统提示, user, 归并纠错)
        if parsed is None:
            return {"blocks": [], "错误": last_error}
        raw_blocks = parsed.get("blocks") if isinstance(parsed, dict) else None
        if not isinstance(raw_blocks, list) or not raw_blocks:
            return {"blocks": [], "错误": "输出缺少 blocks 数组"}

        blocks = []
        for raw in raw_blocks[:MAX_BLOCKS_PER_BATCH]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()[:120]
            markdown = str(raw.get("markdown") or "").strip()[:MAX_BLOCK_MARKDOWN]
            if not title or not markdown:
                continue
            来源 = []
            for index in 规范来源序号(raw.get("sources"), len(条目)):
                item = 条目[index - 1]
                来源.append({"title": str(item.get("title") or "")[:200],
                             "url": str(item.get("url") or "")[:2048]})
            blocks.append({
                "title": title, "markdown": markdown,
                "summary": str(raw.get("summary") or "").strip()[:600] or markdown[:180],
                "tags": [str(t).strip()[:40] for t in (raw.get("tags") or []) if str(t).strip()][:8],
                "key_points": [str(p).strip()[:200] for p in (raw.get("key_points") or []) if str(p).strip()][:6],
                "sources": 来源[:20], "model": self.配置.model,
            })
        if not blocks:
            return {"blocks": [], "错误": "输出的知识块都缺少标题或正文"}
        return {"blocks": blocks, "错误": ""}

