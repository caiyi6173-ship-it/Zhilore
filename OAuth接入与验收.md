# 知乎 OAuth、个人工作区接入与验收

更新：2026-09-13。推荐入口为 **8100 的同站账号页 + 个人工作区**，不是原 8099 共享文件知识库的登录包装。

## 1. 先明确当前完成程度

| 用户目标 | 已实现 / 已验证 | 仍待真实平台确认 |
| --- | --- | --- |
| OAuth 登录、回调、用户识别 | 授权码流程、一次性 state 与浏览器绑定、服务端换 Token；按官方 /user 的 uid 识别 | 应用凭证、回调登记、实际 state 与回调行为 |
| 两个不同知乎账号分别授权 | 独立测试客户端、同昵称不同 UID 隔离通过；浏览器模拟 A / B 串行切换通过 | **真实授权 0 / 2**，需两位账号本人确认授权 |
| 各自公开收藏夹与摘要 | 当前会话 Token、仅公开列表、20 条内容分页、服务端重新核验 | 应用真实权限、真实列表与分页返回 |
| 绑定后进入个人知识库 | 同站回调成功 303 到 /workspace/；按 UID 的摘要库、图谱、搜索与阅读 | 真实 UID 下端到端收录 |
| 取消、过期、权限限制 | 模拟取消、缺失 / 错误 state、过期、403、限流、切号迟到响应等通过 | 平台真实取消形式、有效期和权限错误 |

**代码和模拟通过不等于真实 OAuth 已接通，也不等于已经可以公开上线。** 本轮 162 个 Python、126 个 Node 测试全部通过，无跳过；详见 [最新验收报告](D:/项目ai/知乎/outputs/账号与工作区整合验收-2026-09-13.md)。

### 对旧说明的更正

旧记录曾把“尚未找到文档”写成“平台不回传 state、没有可用身份接口”。这个推论不成立。本轮已读到官方 OAuth Skill 中的 state 传入 / 回传 / 校验示例，以及官方 GET /user 的用户身份契约。**不能再以文档缺失为由阻塞实现，也不能因此关闭 state 校验。** 文档契约有依据，真实平台行为仍需应用联调验证。

## 2. 一个站点、两个页面，不必运行两个对外服务

| 路径 | 作用 |
| --- | --- |
| / | 绑定自己的账号、连接状态、公开收藏夹与按页收录 |
| /auth/zhihu/start | 同源 POST 发起授权 |
| /auth/zhihu/callback | 接收平台授权码，校验 state，服务端兑换 Token 并识别用户 |
| /workspace/ | 个人知识库；未登录回账号页 |
| /api/workspace/索引、图谱、笔记、搜索 | 当前账号的摘要库读取；必须携带当前工作区会话标记 |
| POST /api/workspace/import | 同源、JSON、CSRF 校验后重新读取并保存一页公开摘要 |

回调成功后直接进入工作区。首次授权没有已存摘要时显示空库，再从“账号 / 收藏”返回账号页收录。不在前端 URL 携带 Token，不跨端口转交会话，不读取作者的共享库。

- [8100 账号页](http://127.0.0.1:8100/) 与 [个人工作区](http://127.0.0.1:8100/workspace/) 属于同一个 Python 应用。
- [8099 作者本地版](http://127.0.0.1:8099/) 保留原文、分节、MOC 等演示，不应作为访客的个人库，也不要直接公开。
- 历史“应用.py + 启动一体化.ps1”仍属于共享文件库试验；新版使用 [OAuth验证服务.py](D:/项目ai/知乎/工具/OAuth验证服务.py) 与 [启动OAuth验证.ps1](D:/项目ai/知乎/工具/启动OAuth验证.ps1)。

## 3. 先预览，再在本机安全配置

### 预览未配置页面

~~~powershell
D:/python/python.exe -X utf8 D:/项目ai/知乎/工具/OAuth验证服务.py
~~~

默认只监听 127.0.0.1:8100。未配置时显示具体缺项并禁用授权，不生成假用户或测试收藏。

### 实际授权启动

先停止占用 8100 的本项目预览进程。前台启动时使用 Ctrl+C；后台启动时先核对进程路径和端口，只停止本项目对应进程，不停止原 8099 服务。然后运行：

~~~powershell
& D:/项目ai/知乎/工具/启动OAuth验证.ps1
# 只校验当前环境，缺项即报告，不访问知乎：
& D:/项目ai/知乎/工具/启动OAuth验证.ps1 -CheckOnly
# 部署环境已安全注入全部配置时，无交互启动：
& D:/项目ai/知乎/工具/启动OAuth验证.ps1 -UseEnvironment
~~~

以上是不同使用方式，不要同时启动。脚本在本机询问缺项，App Key / Access Secret 隐藏输入，不写入 .env、源码、启动参数或聊天。凭证暂存于当前进程及子进程环境；脚本结束后恢复原环境值。运行时内存不是安全凭证库，不承诺清零所有内存副本。

若执行策略阻止 .ps1，按组织策略运行，不要全局关闭执行策略。

### 配置项

| 环境变量 | 要求 | 含义 |
| --- | --- | --- |
| ZHIHU_OAUTH_APP_ID | OAuth 必需 | 当前作品对应的 App ID |
| ZHIHU_OAUTH_APP_KEY | OAuth 必需 | 后端兑换授权码的 App Key，不是收藏 Access Secret |
| ZHIHU_OAUTH_REDIRECT_URI | OAuth 必需 | 平台登记的完整回调，固定路径 /auth/zhihu/callback，无查询串、片段、尾斜杠 |
| ZHIHU_ACCESS_SECRET | 读取 / 收录收藏必需 | 收藏开放 API 的调用方凭证，不是访客 OAuth Token |
| ZHIHU_WORKSPACE_DB | 可选 | SQLite 文件的持久绝对路径；默认 D:/项目ai/知乎/数据/用户工作区.sqlite3 |

官方身份契约已作为默认值，不必再手填未知接口：

| 可选兼容覆盖项 | 官方默认值 |
| --- | --- |
| ZHIHU_OAUTH_PROFILE_URL | https://openapi.zhihu.com/user |
| ZHIHU_OAUTH_PROFILE_AUTH | oauth_bearer |
| ZHIHU_OAUTH_PROFILE_ID_PATH | /uid |
| ZHIHU_OAUTH_PROFILE_NAME_PATH | /fullname |

只有平台明确提供另一种契约时才覆盖。环境变量已设置但值为空，不会被悄悄替换为默认值；排障时检查旧配置残留，不要混用历史虚构接口字段。

OAuth 就绪与收藏 API 就绪分开显示：没有 Access Secret 仍可完成官方 OAuth 并进入个人工作区，但不能读取 / 收录收藏。配置格式通过不代表凭证有效或有接口权限。

**入口与回调必须同源。** 登记 HTTPS 域名后，应从该域名打开应用；不能仍从 127.0.0.1 点击授权。平台是否允许本机 HTTP 回调尚未实际验证，不要把本地格式校验当作平台通过。

### 依赖

Python 3.10+；当前三项依赖为 FastAPI、httpx、uvicorn，SQLite 使用标准库。清单见 [requirements-oauth.txt](D:/项目ai/知乎/工具/requirements-oauth.txt)，不包含作者的抓取 / 大模型流水线。本轮未全局安装或升级依赖。新环境可单独建虚拟环境：

~~~powershell
D:/python/python.exe -m venv D:/项目ai/知乎/.venv-oauth
D:/项目ai/知乎/.venv-oauth/Scripts/python.exe -m pip install -r D:/项目ai/知乎/工具/requirements-oauth.txt
& D:/项目ai/知乎/工具/启动OAuth验证.ps1 -Python D:/项目ai/知乎/.venv-oauth/Scripts/python.exe
~~~

发布环境应另行锁定、审核依赖版本。

## 4. 已核对的官方协议

来源：[OAuth 快速开始](https://www.zhihu.com/ring/moltbook/api/oauth/oauth_quickstart)、[获取 Access Token](https://www.zhihu.com/ring/moltbook/api/oauth/access_token)、[获取用户信息](https://www.zhihu.com/ring/moltbook/api/oauth/user_info)、[OAuth Skill](https://www.zhihu.com/ring/moltbook/api/oauth/zhihu_oauth_skill)。用户提供的[社区 Quickstart](https://www.zhihu.com/ring/moltbook/api/community/quickstart)用于理解社区接口，不能把其签名凭证替代 OAuth App Key 或收藏 Access Secret。

1. **授权**：GET https://openapi.zhihu.com/authorize，参数 redirect_uri、app_id、response_type=code、state。state 由服务端生成，与浏览器流程 Cookie 绑定，10 分钟有效，只能用一次。
2. **回调**：官方示例参数为 authorization_code；代码也兼容 code，但两者同时出现、重复参数、缺失 / 错误 state 均拒绝。授权失败不会兑换 Token 或保留旧账号可读状态。
3. **换 Token**：POST https://openapi.zhihu.com/access_token，表单编码，提交 app_id、app_key、grant_type=authorization_code、redirect_uri、code。响应根对象提供 access_token、token_type、expires_in（秒）。
4. **用户身份**：GET https://openapi.zhihu.com/user，Authorization: Bearer 使用当前用户 OAuth Token。读取根字段 uid、fullname；不用昵称或 Token 字符串充当身份，不将手机号、邮箱返回前端。
5. **公开收藏**：GET https://developer.zhihu.com/api/v1/user/favlists 与 https://developer.zhihu.com/api/v1/user/favlist_contents，使用 Access Secret、当前用户 OAuth Token 与请求时间戳。收藏鉴权头与 /user 的 OAuth Bearer 用法不同。
6. **错误**：除了 HTTP 状态，处理 HTTP 200 中的业务 401 / 403 / 404 等失败，不将错误对象识别成成功用户；不跟随上游重定向转发凭证。
7. **过期**：未发现可依赖的 refresh_token 契约，不猜刷新接口。应用会话最多 8 小时，且不超过 Token 有效期；失效后清屏、要求重新授权。

真实平台若未按文档回传 state，应保留拒绝行为并反馈平台，不能通过删掉校验“修复绑定”。

## 5. 按截图提交：作品链接与回调分别填写

截图明确：**作品链接发布时必填**；**登录回调选填，留空则回到作品链接**。本项目根页面不是 OAuth 回调处理器，因此应显式填写：

| 提交字段 | 示例 |
| --- | --- |
| 作品链接 | https://你的域名/ |
| 知乎登录回调地址 | https://你的域名/auth/zhihu/callback |
| 后端 ZHIHU_OAUTH_REDIRECT_URI | 与上一行完全一致 |

这是同一个网站的两个路径，不需要创建两个作品，也不要求两个服务器。作品入口不要填作者的 8099；回调也不要填 /workspace/。

### 是否需要服务器

需要一个其他人能访问、能持续运行 Python 的后端环境，但**不一定购买独立云服务器**：支持 Python 常驻服务、HTTPS 域名和持久磁盘的平台托管即可。只有静态网页托管不够，因为 App Key 与 Token 兑换必须在后端，用户摘要需要持久化。

127.0.0.1 指向访问者自己的电脑，不能当作多人 Demo 链接。临时 HTTPS 隧道只能用于短期联调：受本机在线与地址稳定性影响；地址变化需重新登记回调，不当作已完成上线。

### 当前部署约束

- **单实例、单进程、一个 worker**。state 与会话仅在内存，多 worker / 多副本 / 无状态函数会造成回调和登录状态不一致。扩容前需共享且受保护的会话存储，并补齐并发、容量和删除策略。
- Python 服务只暴露必要端口；外部使用 HTTPS 反向代理或平台入口。HTTPS 会话使用 Secure、HttpOnly、SameSite=Lax Cookie。
- 默认不信任代理头。反代部署时，使用明确代理 IP / CIDR 配置 --forwarded-allow-ips 或启动脚本的 -ForwardedAllowIps；禁止 *，不要把整个公网列为可信代理。
- 例如只有本机反向代理时才可使用 --host 127.0.0.1 --port 8100 --forwarded-allow-ips 127.0.0.1；其他部署拓扑按实际地址配置，不能照抄。
- 服务已关闭访问日志；代理和托管日志同样应隐藏回调查询串，不记录授权码、Cookie、Token、App Key、Access Secret 或完整上游身份响应。
- 给 ZHIHU_WORKSPACE_DB 配持久磁盘，限制文件权限并做好备份；它是含稳定 UID 的业务数据，不放在公开静态目录，不上传到公共仓库。当前使用同库 UID 隔离，不声称每个用户拥有独立加密数据库。
- 服务重启清除 Token 和会话，用户需重新登录；正确挂载持久磁盘后，已存摘要保留。退出只注销本应用会话，不等于删除摘要或撤销知乎平台上的授权。
- 公网交付前还需核验平台条款、访问频率、容量、反代设置与数据删除流程。当前版本是受限演示，不声称完成生产级多租户平台建设。

## 6. 公开读取与知识库边界

- 只列公开夹；列表上限 50，不能声称“全部收藏夹”。私密夹不显示，也不提供绕过入口。
- 内容每页 20，手动按 NextOffset 读取并去重；每页先重新读取当前用户的公开列表校验可访问范围。
- 收录请求只接收 collection_id 与 offset，不接收浏览器提交的摘要、用户 ID 或任意 URL。服务端重新取一页规范化数据后事务保存。
- 同一用户、同一收藏夹内的同一内容更新原条目，不重复新增；每个用户最多 2,000 条。
- 保存标题、摘要、作者、知乎原文链接及必要统计。**不是全文、不调用大模型、不运行作者的抓取或重构脚本。** 图谱关系仅代表摘要所属收藏夹。
- SQLite、索引、图谱、搜索、阅读和写入均按当前会话稳定 UID 限定；前端旧会话、后台页恢复、过期、退出与切号会先清屏再重新核验。
- 权限 / 收藏夹失效清除当前阅读区旧摘要，不允许继续收录；限流或网络暂时失败允许手动重试。不回退到作者账号 Token。
- 已收录的是历史快照，可能与后来修改、删除或改为私密的知乎原内容不同；当前不会自动同步删除快照。退出不删除已存摘要，不把历史快照包装成仍可实时读取的证据。
- 正常服务不挂载原共享笔记库、.raw 源文件、分析缓存、数据库文件，也不提供旧重扫、抓取或测试登录接口。

## 7. 真正的双账号验收顺序

请在应用配置与 HTTPS 回调登记完成后执行；不要把下面的待验收步骤写成已通过：

1. 在两个**独立浏览器配置**中，分别由 A、B 本人在知乎确认各自身份并授权。两个普通标签页共享 Cookie，不能用来证明隔离；有些无痕窗口也会共享同一无痕会话。
2. 各准备一个便于辨认的公开测试收藏夹，以及至少一条独有测试内容；如准备私密夹，只验证其不出现在开放列表中。
3. 从同一公网作品入口发起，核对实际回调、state 验证、稳定 UID；授权码和 Token 不截图、不写日志。
4. A 收录一页后进入工作区；B 首次工作区应无 A 的记录，再收录 B 的内容。相同昵称不能导致数据混用；共享公开夹可以同时可见，不要求两份列表完全无交集。
5. A / B 分别搜索各自独有内容，验证不返回对方已存条目；跨账号携带旧工作区标记应拒绝。退出再登录原账号，自己的摘要应保留。
6. 在授权页取消：若返回错误，显示取消 / 失败并保持未登录；若平台不跳回，验证流程到期可重新开始。不要在报告里假定平台必然采用某个取消参数。
7. 等待实际过期，或由本人在平台撤销本应用授权后触发读取，验证清屏与重新授权；这属于权限变更，需本人明确操作。单纯关闭页面不等于 Token 过期。
8. 使用平台允许的受限测试凭证 / 权限场景验证 403、配额与接口限制；不要故意高频请求耗尽额度。记录脱敏错误类别、结果和时间。
9. 校验另一个账号仍正常、网页前进 / 后退不显示旧账号内容、手机账号栏与退出入口可用。

真实验收表至少记录：两个账号的脱敏代号、不同稳定 UID 的确认结果、回调验证结果、独有内容隔离、取消、过期、权限限制。不得收集两人的密码、Cookie、Token 或完整身份响应。

## 8. 本地回归和模拟预览

~~~powershell
cd D:/项目ai/知乎
D:/python/python.exe -X utf8 -m unittest discover -s 工具/tests -p "test_*.py"
D:/AI/nodejs/node-v24.14.1-win-x64/node.exe --test 工具/tests/test_frontend.cjs 工具/tests/test_oauth_frontend.cjs 工具/tests/test_oauth_launcher.cjs 工具/tests/test_workspace_frontend.cjs
~~~

当前实际结果为 **162 / 162 Python、126 / 126 Node**。日志：[Python](D:/项目ai/知乎/outputs/统一入口-python.log)、[Node](D:/项目ai/知乎/outputs/统一入口-node.log)。

仅需要复验 UI 时，可独立启动测试脚本：

~~~powershell
D:/python/python.exe -X utf8 D:/项目ai/知乎/工具/tests/serve_public_collections_preview.py
~~~

[8101 模拟入口](http://127.0.0.1:8101/__fixture/a)只监听本机，横幅明确“仅模拟验收 · 非真实知乎授权”；使用虚构 Provider、模拟会话与系统临时目录中的专用 SQLite，不读取真实凭证、Cookie、收藏或正式用户数据库。场景切换会清空测试会话，仅用于串行 UI 检查；不能替代两个独立真实浏览器授权，也不能部署为作品。

复验完成后只停止自己启动的 8101 测试进程，保留原 8099 和需要交付的正常 8100。最新浏览器记录、已知限制与真实 0 / 2 状态见 [账号与工作区整合验收](D:/项目ai/知乎/outputs/账号与工作区整合验收-2026-09-13.md)。

## 9. 平台能提供哪些「我的数据」（2026-09-14 盘点）

依据：官方 skill `zhihu-cli-skill-0.5.3-beta` 的 `references/user-api.md` 与 `open-platform.md`。

**「知乎用户数据」这一桶（每日 10000 次）只有 5 个接口，全部是 GET：**

| 接口 | 拿到什么 | 分页 | 本项目状态 |
| --- | --- | --- | --- |
| `GET /api/v1/user/favlists` | 收藏夹列表（名称 / 描述 / 是否公开 / UrlToken） | 仅 Limit ≤ 50 | ✅ 已接入 |
| `GET /api/v1/user/favlist_contents` | 指定收藏夹的公开内容（标题 + 摘要 + 作者 + 互动数 + 收藏时间） | Offset / Limit ≤ 50 | ✅ 已接入 |
| `GET /api/v1/user/collections` | **近期**收藏（无 Offset、无 Paging，只适合取最近一批） | 无 | ⬜ 未接入（字段与 favlist_contents 同构） |
| `GET /api/v1/user/contents` | **自己的创作**：回答 / 文章 / 视频 / 想法 / 问题；`ContentType` 可选 `all·answer·article·zvideo·pin·question`，`SortField` 支持 `like_count` / `ts` | Offset / Limit ≤ 50 | ⬜ 未接入 |
| `GET /api/v1/user/followees` | **关注的人**：用户名 / 主页 / 头像 / 一句话介绍 / 性别 / 粉丝数 | Offset / Limit ≤ 50 | ⬜ 未接入 |

**「赞同」不可行（已确认，别再找）：**

- 「赞同」在官方接口里只作为**内容上的统计字段**存在（内容的 `LikeCount`、搜索接口的 `VoteUpCount`），含义是"这条内容被多少人赞过"；
- **没有**"我赞同过的内容 / 点赞列表"这类接口——用户数据桶里没有，其它桶也没有；
- 唯一能拿到"我赞同过什么"的路子是网页端 Cookie 抓取（`抓取.py --网页` 具备该能力），但那**违反本项目「只走官方开放接口」的硬约束**，且不稳定、有封号风险 —— 不做。

**顺带发现（输出方向，不是输入）：** 平台还有「知识库」桶（每日 500 次）：`GET /api/v1/knowledge/bases`、`GET .../items`、`POST /api/v1/knowledge/files`（上传）、`POST /api/v1/knowledge/search`。也就是说**归并出的知识块可以回传到知乎知识库**，这是目前完全没用的能力。

**若要做，成本评估：**

- `user/contents` 与 `user/favlist_contents` 字段高度同构（标题 / 摘要 / 链接 / 时间 / 三个互动数），可直接复用现有卡片视图、图谱与检索，改动集中在 `provider.py` 加一个方法 + 一条路由；
- `user/followees` 的数据形态是"人"而不是"内容"，可复用处少，更适合单独做「信息源」视图；
- 额度完全够：用户数据 10000 次/日，一页 20 条只消耗 1 次。
