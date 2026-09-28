# -*- coding: utf-8 -*-
"""测试用的环境隔离：只摘掉本项目自己的变量，**绝不清空整个环境**。

为什么不能用 `patch.dict(os.environ, {}, clear=True)`：
它会清空整个环境、退出时再逐项写回。宿主机经父进程传进来的变量若超过
**32767 字符**（本机实测存在约 48 万字符的变量，IDE / MCP 配置里常见），
Windows 上 `os.environ[名字] = 值` 会直接抛
`ValueError: the environment variable is longer than 32767 characters`。

后果很有误导性：**整个进程里第一个退出的 clear 用例莫名报错**，后面的用例
反而正常（还原在中途就断了），看起来像"某个测试坏了"，其实与代码无关。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

前缀 = "ZHIHU_"


@contextmanager
def 仅环境(变量: dict | None = None):
    """临时把 `ZHIHU_*` 换成给定值（默认全部移除），退出时精确还原。

    只动本项目的变量，值都很小，因此不会有 Windows 的 32767 字符限制问题。
    """
    摘掉 = {名字: os.environ.pop(名字) for 名字 in [k for k in os.environ if k.startswith(前缀)]}
    if 变量:
        os.environ.update({名字: str(值) for 名字, 值 in 变量.items()})
    try:
        yield
    finally:
        for 名字 in [k for k in os.environ if k.startswith(前缀)]:
            os.environ.pop(名字, None)
        os.environ.update(摘掉)
