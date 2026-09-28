# -*- coding: utf-8 -*-
"""用知乎直答把公开收藏摘要归并成知识块。

额度很小（邀测期实测只有个位数，之后按账号发放），所以：

* **一次请求处理一批**，绝不逐条调用；
* 启动前先预估调用次数，超过上限**直接拒绝**，而不是跑到一半额度耗尽；
* 每个收藏夹的所有批次先攒齐再一次性替换写入，重跑不会累积重复。

任务状态只放在进程内存里（uvicorn 单 worker），配合前端轮询进度。
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, field

from .errors import OAuthError

BATCH_SIZE = 25
MAX_CALLS_DEFAULT = 90
MAX_ITEMS_PER_COLLECTION = 250
批次间隔秒数 = 1.0
任务保留上限 = 8
阶段列表 = ("准备中", "读取收藏夹列表", "读取公开摘要", "归并知识块", "完成", "失败")


def 预计调用次数(条目总数: int, 每批: int = BATCH_SIZE) -> int:
    """Requests needed for N summaries. Zero items still costs nothing."""
    if 条目总数 <= 0 or 每批 <= 0:
        return 0
    return math.ceil(条目总数 / 每批)


def 查今日剩余额度(client) -> int | None:
    """今日直答剩余额度；查询走官方额度接口，不消耗业务额度。

    客户端不支持（测试替身）或查询失败时返回 None —— 绝不让额度查询挡住处理。
    """
    查询 = getattr(client, "剩余额度", None)
    if not callable(查询):
        return None
    try:
        return 查询()
    except Exception:  # noqa: BLE001
        return None


@dataclass
class 直答任务:
    id: str
    subject: str
    阶段: str = "准备中"
    消息: str = ""
    收藏夹总数: int = 0
    收藏夹完成: int = 0
    条目总数: int = 0
    条目完成: int = 0
    知识块数: int = 0
    已用调用: int = 0
    预计调用: int = 0
    今日剩余额度: int | None = None
    问题: list[str] = field(default_factory=list)
    完成: bool = False
    失败: str = ""
    结束时间: float = 0.0

    def public(self) -> dict:
        return {
            "id": self.id, "阶段": self.阶段, "消息": self.消息,
            "收藏夹总数": self.收藏夹总数, "收藏夹完成": self.收藏夹完成,
            "条目总数": self.条目总数, "条目完成": self.条目完成,
            "知识块数": self.知识块数, "已用调用": self.已用调用, "预计调用": self.预计调用,
            "今日剩余额度": self.今日剩余额度,
            "问题": self.问题[-6:], "完成": self.完成, "失败": self.失败,
            "进度": round(self.条目完成 / self.条目总数, 4) if self.条目总数 else 0.0,
        }


class 任务登记处:
    """In-process registry. No `await` in `开始`, so the check-and-set is atomic."""

    def __init__(self, 保留上限: int = 任务保留上限):
        self.保留上限 = 保留上限
        self.任务: dict[str, 直答任务] = {}
        self.进行中: dict[str, str] = {}

    def 开始(self, subject: str) -> 直答任务:
        self._清进行中(subject)
        if subject in self.进行中:
            raise OAuthError("task_already_running", 409)
        task = 直答任务(id=uuid.uuid4().hex, subject=subject)
        self.任务[task.id] = task
        self.进行中[subject] = task.id
        self._清理()
        return task

    def _清进行中(self, subject: str) -> None:
        """任务跑完不会回调这里，所以"进行中"要靠读取时惰性清掉。"""
        handle = self.进行中.get(subject)
        task = self.任务.get(handle) if handle else None
        if task is not None and task.完成:
            self.进行中.pop(subject, None)

    def 取(self, subject: str, task_id: str) -> 直答任务:
        task = self.任务.get(str(task_id or ""))
        # 只能查自己的任务，不能靠猜 id 读别人的进度。
        if task is None or task.subject != subject:
            raise OAuthError("task_not_found", 404)
        return task

    def 取或当前(self, subject: str, task_id: str = "") -> 直答任务:
        """不传 id 时返回进行中的任务，其次返回最近结束的那个。

        页面刷新后前端手上没有 task id，靠这个把进度接回来。
        """
        if task_id:
            return self.取(subject, task_id)
        self._清进行中(subject)
        进行中 = self.进行中.get(subject)
        if 进行中:
            return self.任务[进行中]
        我的 = [t for t in self.任务.values() if t.subject == subject]
        if not 我的:
            raise OAuthError("task_not_found", 404)
        return max(我的, key=lambda t: t.结束时间)

    def 结束(self, task: 直答任务) -> None:
        task.完成 = True
        task.结束时间 = task.结束时间 or time.time()
        if self.进行中.get(task.subject) == task.id:
            self.进行中.pop(task.subject, None)

    def _清理(self) -> None:
        if len(self.任务) <= self.保留上限:
            return
        已结束 = sorted((t for t in self.任务.values() if t.完成), key=lambda t: t.结束时间)
        for 旧 in 已结束[:max(0, len(self.任务) - self.保留上限)]:
            self.任务.pop(旧.id, None)


async def 拉取条目(provider, token: str, collection: dict, 上限: int = MAX_ITEMS_PER_COLLECTION) -> list[dict]:
    """Walk every page of one public collection. Never trusts a client cursor."""
    items: list[dict] = []
    offset = "0"
    while len(items) < 上限:
        page = await provider.collection_contents(token, collection["id"], offset)
        items.extend(page.items)
        if page.next_offset is None:
            break
        offset = page.next_offset
    return items[:上限]


async def 运行任务(task: 直答任务, provider, workspace, client, token: str,
                    每批: int = BATCH_SIZE, 调用上限: int = MAX_CALLS_DEFAULT,
                    间隔: float = 批次间隔秒数) -> None:
    """Pull, merge in batches, store. Records every failure on the task instead of raising."""
    try:
        task.阶段 = "读取收藏夹列表"
        collections = await provider.collections(token)
        task.收藏夹总数 = len(collections)
        # 没有公开收藏夹不再直接失败：创作可以独立归并。

        task.阶段 = "读取公开摘要"
        待处理: list[tuple[dict, list[dict]]] = []
        总数 = 0
        for index, collection in enumerate(collections, 1):
            task.消息 = f"读取「{collection['title']}」（{index}/{len(collections)}）"
            items = await 拉取条目(provider, token, collection)
            if items:
                待处理.append((collection, items))
                总数 += len(items)
        task.条目总数 = 总数
        # 「我的创作」：与收藏并列的输入。归并语义是「创作地图」——按主题归并
        # 你自己写过的内容，给创作方向做参考，不是替你学习收藏。
        task.消息 = "读取我的创作"
        创作: list[dict] = []
        偏移 = "0"
        while len(创作) < MAX_ITEMS_PER_COLLECTION:
            page = await provider.creations(token, "all", 偏移)
            创作.extend(page.items)
            if page.next_offset is None:
                break
            偏移 = page.next_offset
        创作 = 创作[:MAX_ITEMS_PER_COLLECTION]
        if 创作:
            待处理.append(({"id": "creations", "title": "我的创作"}, 创作))
            总数 += len(创作)
        # 增量：上次归并时已经用过的条目不再送直答（省额度），只处理新进的。
        for index, (collection, items) in enumerate(待处理):
            已有 = workspace.已归并条目(task.subject, collection["id"])
            # 老数据兜底：指纹是 2026-09-15 才加的，升级前归并过的收藏夹只有块、没有
            # 指纹行。用块里记的来源网址反查一遍，把"其实已经归并过"的条目认出来——
            # 否则点一次处理就把老文章全送一遍，那正是用户报的"又被处理一次"。
            来源 = workspace.块的来源网址(task.subject, collection["id"])
            if 来源:
                已有 |= {item["id"] for item in items if item.get("url") in 来源}
            待处理[index] = (collection, [item for item in items if item["id"] not in 已有])
        跳过数 = sum(1 for _, items in 待处理 if not items)
        待处理 = [(夹, 条) for 夹, 条 in 待处理 if 条]
        # 每个分组分别向上取整再求和：42+28 条是 2+2=4 批，合并算成 ceil(70/25)=3 会低估。
        task.预计调用 = sum(预计调用次数(len(items), 每批) for _, items in 待处理)
        if task.预计调用 == 0:
            task.阶段 = "失败"
            task.失败 = ("没有需要归并的新内容" + (f"（{跳过数} 个分组都已处理过）" if 跳过数 else "") +
                         "；等收藏夹或创作有更新后再来。")
            return
        if task.预计调用 > 调用上限:
            task.阶段 = "失败"
            task.失败 = (f"预计需要 {task.预计调用} 次直答调用，超过上限 {调用上限}；"
                         f"请先减少收藏夹内容，或调高上限后再试")
            return

        # 额度预检：直答当日额度可能只有个位数（实测 2026-09-14 为 2 次），
        # 先查清再动手——不够就零调用停下，而不是跑到一半才发现。
        task.今日剩余额度 = 查今日剩余额度(client)
        if task.今日剩余额度 is not None and task.今日剩余额度 < task.预计调用:
            task.阶段 = "失败"
            task.失败 = (f"今日直答额度剩余 {task.今日剩余额度} 次，本次预计需要 {task.预计调用} 次；"
                         f"已停止，没有发起任何调用。额度按自然日重置；"
                         f"也可以先只留内容较少的收藏夹再试。")
            return

        task.阶段 = "归并知识块"
        for collection, items in 待处理:
            全部块: list[dict] = []
            成功批次: list[list[dict]] = []
            批数 = 预计调用次数(len(items), 每批)
            for 批号, start in enumerate(range(0, len(items), 每批), 1):
                batch = items[start:start + 每批]
                task.消息 = f"「{collection['title']}」第 {批号}/{批数} 批（{len(batch)} 条摘要）"
                result = await asyncio.to_thread(client.归并摘要, collection["title"], batch)
                task.已用调用 += 1
                if result["错误"]:
                    task.问题.append(f"{collection['title']} 第 {批号} 批:{result['错误']}")
                else:
                    全部块.extend(result["blocks"])
                    成功批次.append(batch)
                if 批号 < 批数 and 间隔:
                    await asyncio.sleep(间隔)
            # 增量归并：只送新增条目，块以追加方式入库，老块保留不重算（省额度）。
            if 全部块:
                入库 = workspace.append_blocks(task.subject, collection, 全部块)
                task.知识块数 += 入库["blocks"]
            # 指纹只累加「这一批真的成功了」的条目：失败的批次下次还会重试，
            # 不会被永久跳过（那等于静默丢内容）。
            for batch in 成功批次:
                workspace.记录归并条目(task.subject, collection["id"], [item["id"] for item in batch])
            task.条目完成 += len(items)
            task.收藏夹完成 += 1

        task.阶段 = "完成"
        if task.问题:
            task.消息 = f"完成：{task.知识块数} 个知识块，{len(task.问题)} 个批次未成功"
        else:
            task.消息 = f"完成：{task.知识块数} 个知识块，消耗 {task.已用调用} 次直答调用"
    except OAuthError as exc:
        task.阶段 = "失败"
        task.失败 = exc.public().get("message", "处理失败")
        task.问题.append(task.失败)
    except Exception as exc:  # noqa: BLE001 - 任何异常都不能让任务悬在"进行中"
        task.阶段 = "失败"
        task.失败 = f"处理中断：{type(exc).__name__}"
        task.问题.append(task.失败)
    finally:
        # 无论成功、失败还是中途退出，都必须落一个终态，否则这个账号再也启动不了新任务。
        task.完成 = True
        task.结束时间 = task.结束时间 or time.time()
