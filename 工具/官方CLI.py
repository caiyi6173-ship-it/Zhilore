# -*- coding: utf-8 -*-
"""知乎开放平台官方 CLI 适配层。

这个模块只负责：
- 定位并调用本机官方 zhihu-cli；
- 将 CLI 的 PascalCase JSON 转为抓取器内部统一的中文/蛇形字段；
- 处理收藏夹列表和收藏夹条目的 Offset 分页。

Access Secret 不由本模块读取、传递或写入项目；CLI 从系统凭证存储读取它。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


class 官方CLI错误(RuntimeError):
    """官方 CLI 不可用、鉴权失败或返回协议错误。"""



def _值(obj: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in obj and obj[name] is not None:
            return obj[name]
    return default


class 官方CLI客户端:
    """对 zhihu-cli 的最小、可测试封装。"""

    def __init__(self, 可执行文件: str | None = None, 超时秒: int = 120):
        self.可执行文件 = 可执行文件 or self._定位可执行文件()
        self.超时秒 = 超时秒

    @staticmethod
    def _定位可执行文件() -> str:
        candidates = []
        env_path = os.environ.get("ZHIHU_CLI_PATH", "").strip()
        if env_path:
            candidates.append(env_path)
        path_hit = shutil.which("zhihu-cli")
        if path_hit:
            candidates.append(path_hit)
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates.append(os.path.join(
                local_app_data, "ZhihuCLI", "current", "zhihu-cli.exe"))
        candidates.append(os.path.expanduser(
            r"~\AppData\Local\ZhihuCLI\current\zhihu-cli.exe"))

        seen = set()
        for path in candidates:
            absolute = os.path.abspath(path)
            if absolute in seen:
                continue
            seen.add(absolute)
            if os.path.isfile(absolute):
                return absolute
        raise 官方CLI错误(
            "找不到官方 zhihu-cli。请先安装知乎官方 CLI，或设置环境变量 ZHIHU_CLI_PATH。"
        )

    def 调用(self, args: list[str]) -> dict[str, Any]:
        command = [self.可执行文件, *args, "--pretty"]
        try:
            result = subprocess.run(
                command,
                cwd=os.path.dirname(self.可执行文件),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.超时秒,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise 官方CLI错误(f"官方 CLI 请求超时（>{self.超时秒} 秒）") from exc
        except OSError as exc:
            raise 官方CLI错误(f"启动官方 CLI 失败：{exc}") from exc

        stdout = (result.stdout or "").strip()
        try:
            payload = json.loads(stdout) if stdout else None
        except json.JSONDecodeError as exc:
            raise 官方CLI错误(
                f"官方 CLI 返回的不是合法 JSON（退出码 {result.returncode}）"
            ) from exc

        if not isinstance(payload, dict):
            raise 官方CLI错误("官方 CLI 返回格式不是 JSON 对象")
        if result.returncode != 0 or payload.get("ok") is False:
            error = payload.get("error") or {}
            code = error.get("code") or "CLI_ERROR"
            message = error.get("message") or "官方 CLI 请求失败"
            raise 官方CLI错误(f"{code}: {message}")
        if payload.get("Code") not in (None, 0):
            raise 官方CLI错误(
                f"知乎开放平台返回业务错误 {payload.get('Code')}：{payload.get('Message', '')}"
            )
        return payload

    @staticmethod
    def _数据(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("Data")
        return data if isinstance(data, dict) else {}

    def 列出收藏夹(self, 上限: int = 50) -> list[dict[str, Any]]:
        payload = self.调用(["me", "favorites", "lists", "--limit", str(max(1, min(50, 上限)))])
        items = self._数据(payload).get("Items") or []
        结果 = []
        for item in items:
            if not isinstance(item, dict):
                continue
            token = _值(item, "UrlToken", "url_token", default=0)
            address = _值(item, "Url", "url", default="") or ""
            title = _值(item, "Title", "title", default="") or ""
            try:
                token = int(token or 0)
            except (TypeError, ValueError):
                token = 0
            结果.append({
                "名称": title,
                "地址": address or (f"https://www.zhihu.com/collection/{token}" if token else ""),
                "数量": _值(item, "ItemCount", "item_count", default=0) or 0,
                "ID": token,
                "URLToken": token,
                "描述": _值(item, "Description", "description", default="") or "",
                "公开": bool(_值(item, "IsPublic", "is_public", default=False)),
                "来源": "官方CLI",
            })
        return 结果

    def 收藏夹条目(self, url_token: int | str, 每页: int = 50) -> list[dict[str, Any]]:
        try:
            token = int(url_token)
        except (TypeError, ValueError) as exc:
            raise 官方CLI错误(f"收藏夹 URL Token 不是整数：{url_token}") from exc
        if token <= 0:
            raise 官方CLI错误(f"收藏夹 URL Token 必须为正整数：{token}")

        结果: list[dict[str, Any]] = []
        offset = 0
        seen_offsets: set[int] = set()
        while True:
            if offset in seen_offsets:
                raise 官方CLI错误("收藏夹分页返回了重复 Offset，已停止以避免死循环")
            seen_offsets.add(offset)
            payload = self.调用([
                "me", "favorites", "items",
                "--url-token", str(token),
                "--offset", str(offset),
                "--limit", str(max(1, min(50, 每页))),
            ])
            data = self._数据(payload)
            items = data.get("Items") or []
            for item in items:
                normalized = self._规范化条目(item)
                if normalized:
                    结果.append(normalized)

            paging = data.get("Paging") or {}
            if bool(_值(paging, "IsEnd", "is_end", default=True)):
                break
            next_offset = _值(paging, "NextOffset", "next_offset", default=None)
            try:
                next_offset = int(next_offset)
            except (TypeError, ValueError) as exc:
                raise 官方CLI错误(f"收藏夹分页 NextOffset 无法解析：{next_offset}") from exc
            if next_offset < 0:
                raise 官方CLI错误(f"收藏夹分页 NextOffset 非法：{next_offset}")
            offset = next_offset
        return 结果

    @staticmethod
    def _规范化条目(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        author = _值(item, "Author", "author", default={}) or {}
        if not isinstance(author, dict):
            author = {}
        content_type = str(_值(item, "ContentType", "content_type", default="") or "").lower()
        url = str(_值(item, "Url", "url", default="") or "")
        title = str(_值(item, "Title", "title", default="") or "").strip()
        summary = str(_值(item, "Summary", "summary", default="") or "").strip()
        if not url and not title and not summary:
            return None
        return {
            "官方CLI": True,
            "type": content_type or "unknown",
            "url": url,
            "title": title or "知乎收藏内容",
            "summary": summary,
            "created_at": _值(item, "CreatedAt", "created_at", default=0) or 0,
            "fav_time": _值(item, "FavTime", "fav_time", default=0) or 0,
            "like_count": _值(item, "LikeCount", "like_count", default=0) or 0,
            "comment_count": _值(item, "CommentCount", "comment_count", default=0) or 0,
            "favorite_count": _值(item, "FavoriteCount", "favorite_count", default=0) or 0,
            "author": {
                "name": str(_值(author, "Name", "name", default="") or ""),
                "url_token": str(_值(author, "UrlToken", "url_token", default="") or ""),
                "url": str(_值(author, "Url", "url", default="") or ""),
            },
        }
