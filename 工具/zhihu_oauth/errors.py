"""Safe public errors: never include upstream bodies, codes, or credentials."""

MESSAGES = {
    "collections_configuration_required": "登录能力已独立配置，但收藏数据接口尚缺有效的 Access Secret。请由维护者在服务端配置；这不是你的知乎登录失败。",
    "workspace_session_changed": "账号或会话已切换，请重新进入知识库工作区。",
    "invalid_import_request": "摘要收录参数不合法，请重新打开公开收藏夹后再试。",
    "invalid_delete_request": "删除参数不合法，请重新打开知识库图谱后再试。",
    "invalid_creation_request": "创作同步参数不合法，请重新打开知识库后再试。",
    "creation_unavailable": "暂时读不到你的创作内容，请稍后再试。",
    "record_not_found": "这条内容已经不在你的工作区里了（可能刚被删过），图谱已刷新。",
    "invalid_credential_request": "凭证格式不合法：请粘贴开放平台的 Access Secret（8-256 位字母、数字与 . _ ~ + / = -）。",
    "invalid_access_secret": "这个 Access Secret 被知乎拒绝了（无效或没有直答权限）。请到开放平台个人中心核对后重试。",
    "credential_check_unavailable": "暂时无法验证凭证（网络或开放平台异常）。请稍后重试；不影响继续用应用默认额度。",
    "workspace_limit_reached": "此演示工作区最多保存 2000 条摘要，已停止新增；已有内容不受影响。",
    "workspace_unavailable": "工作区存储暂时不可用，请稍后重试。",
    "note_unavailable": "这条摘要不在当前用户的工作区中。",
    "configuration_required": "应用配置尚未完成，请由项目维护者在服务端补齐配置。",
    "login_required": "请先使用你自己的知乎账号授权。",
    "session_expired": "会话已失效，可能已退出、服务已重启或授权已过期，请重新授权。",
    "token_expired": "授权 Token 已过期，已停止读取，请重新授权。",
    "authorization_failed": "知乎拒绝鉴权，已停止访问。请重新授权；维护者还需核对应用凭证和权限。",
    "invalid_collection_request": "收藏夹 ID 或分页参数不合法，请重新读取公开列表后再试。",
    "collection_unavailable": "该收藏夹不在当前账户可读取的公开列表中，或已不可访问。请重新读取列表；私密收藏夹及列表上限之外的收藏夹不在此入口的读取范围。",
    "permission_denied": "知乎拒绝此接口的访问权限；登录成功不代表已获准读取收藏夹。",
    "rate_limited": "请求频率受到知乎限制，请稍后手动重试。",
    "quota_exceeded": "知乎接口配额已用尽，请由维护者核对配额。",
    "upstream_unavailable": "暂时无法连接知乎，请稍后重试。",
    "upstream_protocol_error": "知乎响应与已配置的接口协议不匹配，已停止访问，请由维护者核对文档。",
    "identity_unavailable": "未取得已确认字段中的稳定用户 ID，未建立登录会话。",
    "code_exchange_failed": "授权码交换失败或已被使用，请重新发起授权。",
    "state_missing": "知乎回调没有返回 state，无法安全关联本次登录。请联系平台确认，不能跳过校验。",
    "state_invalid": "授权回调与本次浏览器登录不匹配，已拒绝，请重新授权。",
    "flow_expired": "本次授权已超时，请重新发起授权。",
    "invalid_callback": "授权回调参数缺失、重复或冲突，已拒绝处理。",
    "invalid_start_request": "发起授权的请求不合法，请刷新登录页后重试。",
    "authorization_cancelled": "你已取消授权，没有建立新的登录会话。",
    "missing_code": "回调未带回授权码，可能取消了授权；未交换 Token。",
    "origin_rejected": "请求来源或访问地址不匹配，请从登记的应用地址打开。",
    "csrf_rejected": "操作校验失败，请刷新页面后重试。",
    "server_busy": "验证服务暂时繁忙，请稍后重试。",
    "internal_error": "验证服务暂时无法处理请求，请联系维护者。",
    "zhida_unavailable": "服务端尚未配置知乎直答凭证（ZHIHU_ACCESS_SECRET），无法调用直答。这不影响登录与公开摘要收录。",
    "task_already_running": "这个账号已经有一个直答处理任务在进行，请等它结束后再开始新的任务。",
    "task_not_found": "这个处理任务的进度已经查不到了（服务可能刚重启过）。可以重新开始一次处理。",
}


class OAuthError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code = code if code in MESSAGES else "internal_error"
        self.status = status
        super().__init__(self.code)

    def public(self) -> dict:
        return {"error": {"code": self.code, "message": MESSAGES[self.code]}}


AUTH_FAILURES = frozenset({
    "login_required", "session_expired", "token_expired", "authorization_failed",
})
