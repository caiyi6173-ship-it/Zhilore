"""Validate public collection data at the platform boundary; never relay raw records."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import OAuthError

COLLECTIONS_LIMIT = 50
CONTENT_PAGE_SIZE = 20
MAX_INT64 = 2**63 - 1
MAX_TIMESTAMP = 253402300799  # Last whole second supported by the UI's calendar.
PUBLIC_SCOPE_NOTE = "仅读取当前授权用户的公开收藏夹，私密收藏夹不在开放范围——开放接口只提供公开内容，完成授权也不会改变这一点；要收录私密夹请先在知乎改为公开。列表最多返回 50 个，接口未提供分页，不保证已列出全部。"
CONTENT_SCOPE_NOTE = "这里是公开内容的标题与摘要，不是全文；阅读原文请前往知乎。每次翻页会重新核验该收藏夹仍在当前用户的公开列表中。"
# 「我的创作」接口（/api/v1/user/contents）的取值：ContentType 必填，SortField 可选。
USER_CONTENTS_TYPES = ("all", "answer", "article", "zvideo", "pin", "question")
USER_CONTENTS_SORTS = ("ts", "like_count")
CREATION_SCOPE_NOTE = "这里是你自己创作的标题与摘要，不是全文；阅读原文请前往知乎。接口只返回公开范围内的创作，私密内容不在开放范围。"

CONTENT_PATHS = {
    "answer": ("www.zhihu.com", r"/(?:question/[1-9][0-9]{0,19}/)?answer/(?P<id>[1-9][0-9]{0,19})"),
    "article": ("zhuanlan.zhihu.com", r"/p/(?P<id>[1-9][0-9]{0,19})"),
    "question": ("www.zhihu.com", r"/question/(?P<id>[1-9][0-9]{0,19})"),
    "pin": ("www.zhihu.com", r"/pin/(?P<id>[1-9][0-9]{0,19})"),
    "zvideo": ("www.zhihu.com", r"/zvideo/(?P<id>[1-9][0-9]{0,19})"),
}


def int64(value, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"[0-9]{1,20}", str(value)):
        raise OAuthError("upstream_protocol_error", 502)
    number = int(value)
    if not minimum <= number <= MAX_INT64:
        raise OAuthError("upstream_protocol_error", 502)
    return number


def collection_request(identifier: str, offset: str) -> tuple[str, str]:
    try:
        if not isinstance(identifier, str) or not isinstance(offset, str):
            raise OAuthError("upstream_protocol_error", 502)
        number = int64(identifier, 1)
        int64(offset)
        if identifier != str(number):
            raise OAuthError("upstream_protocol_error", 502)
    except OAuthError:
        raise OAuthError("invalid_collection_request", 400) from None
    # NextOffset is validated as Int64, but its string representation is preserved.
    return identifier, offset


def text(value, length: int) -> str:
    if not isinstance(value, str):
        raise OAuthError("upstream_protocol_error", 502)
    return value[:length]


def data_items(payload: dict, limit: int) -> tuple[dict, list]:
    if not isinstance(payload, dict) or type(payload.get("Code")) is not int or payload["Code"] != 0:
        raise OAuthError("upstream_protocol_error", 502)
    data = payload.get("Data")
    items = data.get("Items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) > limit or any(not isinstance(item, dict) for item in items):
        raise OAuthError("upstream_protocol_error", 502)
    return data, items


def public_collections(payload: dict) -> list[dict]:
    _data, items = data_items(payload, COLLECTIONS_LIMIT)
    result, seen = [], set()
    for item in items:
        public = item.get("IsPublic")
        if not isinstance(public, bool):
            raise OAuthError("upstream_protocol_error", 502)
        if not public:
            continue  # Do not even copy the title/description of non-public records.
        identifier = str(int64(item.get("UrlToken"), 1))
        if identifier in seen:
            raise OAuthError("upstream_protocol_error", 502)
        seen.add(identifier)
        result.append({
            "id": identifier, "title": text(item.get("Title"), 300),
            "description": text(item.get("Description"), 4000), "is_public": True,
            "url": "https://www.zhihu.com/collection/" + identifier,
        })
    return result


def content_link(kind: str, value) -> tuple[str, str]:
    if kind not in CONTENT_PATHS or not isinstance(value, str) or len(value) > 2048:
        raise OAuthError("upstream_protocol_error", 502)
    if not value.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise OAuthError("upstream_protocol_error", 502)
    try:
        url = urlsplit(value)
        host, pattern = CONTENT_PATHS[kind]
        match = re.fullmatch(pattern, url.path)
        valid = url.scheme == "https" and url.hostname == host and url.port in {None, 443}
        if not valid or url.username is not None or url.password is not None or not match:
            raise ValueError()
    except ValueError:
        raise OAuthError("upstream_protocol_error", 502) from None
    # Drop tracking queries/fragments, credentials, and all non-content paths.
    return kind + ":" + match["id"], "https://" + host + url.path


def timestamp(value) -> int:
    number = int64(value)
    if number > MAX_TIMESTAMP:
        raise OAuthError("upstream_protocol_error", 502)
    return number


def content_item(item: dict) -> dict:
    kind = item.get("ContentType")
    if not isinstance(kind, str):
        raise OAuthError("upstream_protocol_error", 502)
    identifier, url = content_link(kind, item.get("Url"))
    author = item.get("Author")
    if author is not None and not isinstance(author, dict):
        raise OAuthError("upstream_protocol_error", 502)
    return {
        "id": identifier, "type": kind, "url": url,
        "title": text(item.get("Title"), 300), "summary": text(item.get("Summary"), 4000),
        "author": text(author.get("Name"), 200) if author is not None else None,
        "created_at": timestamp(item.get("CreatedAt")), "collected_at": timestamp(item.get("FavTime")),
        "like_count": str(int64(item.get("LikeCount"))),
        "comment_count": str(int64(item.get("CommentCount"))),
        "favorite_count": str(int64(item.get("FavoriteCount"))),
        # Favlists may contain unrelated names; author profiles are unnecessary here.
    }


@dataclass(frozen=True)
class ContentPage:
    items: list[dict]
    offset: str
    next_offset: str | None
    total: str

    def public(self) -> dict:
        return {"items": self.items, "paging": {
            "offset": self.offset, "limit": CONTENT_PAGE_SIZE,
            "has_more": self.next_offset is not None, "next_offset": self.next_offset,
            "total": self.total,
        }}


def public_content_page(payload: dict, offset: str, 归一化=content_item) -> ContentPage:
    current_offset = int64(offset)
    data, items = data_items(payload, CONTENT_PAGE_SIZE)
    paging = data.get("Paging")
    if not isinstance(paging, dict) or not isinstance(paging.get("IsEnd"), bool):
        raise OAuthError("upstream_protocol_error", 502)
    total = str(int64(paging.get("Totals")))
    next_offset = None
    if not paging["IsEnd"]:
        next_offset = paging.get("NextOffset")
        if not isinstance(next_offset, str) or int64(next_offset) <= current_offset:
            raise OAuthError("upstream_protocol_error", 502)
    result, seen = [], set()
    for item in items:
        normalized = 归一化(item)
        if normalized["id"] not in seen:
            seen.add(normalized["id"])
            result.append(normalized)
    return ContentPage(result, offset, next_offset, total)


def creation_item(item: dict) -> dict:
    """用户自己的创作：响应里没有 FavTime（它没被"收藏"过），也没有 Author 字段。

    不能直接复用 `content_item`——它在缺 FavTime 时会当协议错误抛出（2026-09-15
    线上实测）。字段集合以真实响应为准：ContentType / Url / CreatedAt /
    LikeCount / CommentCount / FavoriteCount / Title / Summary。
    """
    kind = item.get("ContentType")
    if not isinstance(kind, str):
        raise OAuthError("upstream_protocol_error", 502)
    identifier, url = content_link(kind, item.get("Url"))
    return {
        "id": identifier, "type": kind, "url": url,
        "title": text(item.get("Title"), 300), "summary": text(item.get("Summary"), 4000),
        "author": None,  # 作者就是本人，响应里不返回该字段。
        "created_at": timestamp(item.get("CreatedAt")),
        "collected_at": timestamp(item.get("CreatedAt")),  # 创作没有收藏时间，用创建时间排序。
        "like_count": str(int64(item.get("LikeCount"))),
        "comment_count": str(int64(item.get("CommentCount"))),
        "favorite_count": str(int64(item.get("FavoriteCount"))),
    }


def public_creation_page(payload: dict, offset: str) -> ContentPage:
    """创作内容与收藏内容共用 Offset / Paging 协议，只有条目的归一化不同。"""
    return public_content_page(payload, offset, creation_item)


def creation_request(content_type: str, offset: str) -> tuple[str, str]:
    """ContentType 是创作接口的必填项，取值固定；Offset 按 Int64 校验后原样回传。"""
    try:
        if not isinstance(content_type, str) or content_type not in USER_CONTENTS_TYPES:
            raise OAuthError("upstream_protocol_error", 502)
        if not isinstance(offset, str):
            raise OAuthError("upstream_protocol_error", 502)
        number = int64(offset)
    except OAuthError:
        raise OAuthError("invalid_creation_request", 400) from None
    return content_type, str(number)
