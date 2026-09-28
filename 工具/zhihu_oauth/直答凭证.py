# -*- coding: utf-8 -*-
"""登录用户自备的直答凭证（Access Secret）。

为什么只放内存：

1. 这是**别人的凭证**。一旦写进 sqlite、.env 或日志，就成了我们替用户保管密钥，
   泄露面从"浏览器里的一次输入"扩大到整个服务器磁盘与备份。
2. 直答的额度挂在 Access Secret 上，所以"用谁的凭证"就等于"烧谁的额度"。
   保存它只是为了让后续调用带上它，不需要持久化。
3. 进程重启即失效，用户重新填一次即可——代价很小，安全性高很多。

键用登录用户的稳定 ID（subject），不是会话句柄：同一账号换个标签页不必重填，
但跨账号绝不可能互相取到。
"""
from __future__ import annotations

import re
import threading

# 官方 Access Secret 的形态未公开，按 Bearer 凭证的通用字符集从严限制。
凭证格式 = re.compile(r"[A-Za-z0-9._~+/=-]{8,256}")


class 直答凭证库:
    """进程内存里的 subject → Access Secret 映射。"""

    def __init__(self, 上限: int = 512):
        self.上限 = max(1, 上限)
        self._表: dict[str, str] = {}
        self._锁 = threading.Lock()

    @staticmethod
    def 合规(值: str) -> bool:
        return isinstance(值, str) and bool(凭证格式.fullmatch(值))

    def 取(self, subject: str) -> str:
        if not subject:
            return ""
        with self._锁:
            return self._表.get(subject, "")

    def 已设置(self, subject: str) -> bool:
        return bool(self.取(subject))

    def 存(self, subject: str, 凭证: str) -> None:
        if not subject or not self.合规(凭证):
            raise ValueError("凭证不合规")
        with self._锁:
            if len(self._表) >= self.上限 and subject not in self._表:
                # 满了就丢最早写入的一条，绝不因此报错或落盘。
                self._表.pop(next(iter(self._表)), None)
            self._表[subject] = 凭证

    def 清(self, subject: str) -> None:
        with self._锁:
            self._表.pop(subject, None)
