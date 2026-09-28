#!/usr/bin/env bash
# 知脉 · 服务器端一键部署
#
# 用途：项目文件已经在服务器上之后，用它完成「构建镜像 → 启动 → 自测」。
# 用法（项目根目录下执行，需要 root 或已加入 docker 组的用户）：
#     bash 部署/服务器部署.sh
#
# 说明：凭证只从 .env 读取，脚本本身不接收、不写入任何密钥。

set -euo pipefail

步骤() { printf '\n[%s] %s\n' "$1" "$2"; }
失败() { printf '\n✗ %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- 0. 定位项目根
脚本目录="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
项目根="$(cd "$脚本目录/.." && pwd)"
cd "$项目根"

[ -f docker-compose.yml ] || 失败 "当前目录没有 docker-compose.yml（项目根：$项目根）"
[ -f Dockerfile ]         || 失败 "当前目录没有 Dockerfile"

printf '项目根：%s\n' "$项目根"

# ---------------------------------------------------------------- 1. 检查 Docker
步骤 1/6 "检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  失败 "未安装 Docker。可执行：bash <(curl -sSL https://linuxmirrors.cn/docker.sh)"
fi
docker compose version >/dev/null 2>&1 || 失败 "docker compose 不可用（需要 Compose v2）"
docker info >/dev/null 2>&1 || 失败 "Docker 守护进程没在跑，先执行：systemctl start docker"
printf '  %s\n' "$(docker --version)"

# ---------------------------------------------------------------- 2. 准备 .env
步骤 2/6 "准备 .env"
if [ -f .env ]; then
  printf '  已存在 .env，保持不变\n'
else
  cp .env.example .env
  printf '  已从模板创建 .env\n'
  printf '  ⚠ 想启用真实知乎授权，请编辑 .env 填好凭证（取消注释那几行）\n'
  printf '  ⚠ 未填也能启动，只是登录按钮会保持禁用——这是刻意的，不预填假数据\n'
fi

# ---------------------------------------------------------------- 3. 构建启动
步骤 3/6 "构建镜像并启动（首次约 1–3 分钟）"
docker compose up -d --build

# ---------------------------------------------------------------- 4. 等待就绪
步骤 4/6 "等待服务就绪"
就绪=0
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:8099/api/status 2>/dev/null; then
    就绪=1; break
  fi
  sleep 2
done
if [ "$就绪" != 1 ]; then
  printf '\n--- 最近日志 ---\n'
  docker compose logs --tail=60 || true
  失败 "服务 60 秒内未就绪，日志见上"
fi

# ---------------------------------------------------------------- 5. 自测
步骤 5/6 "自测"
printf 'GET /api/status → '
curl -sS --max-time 5 http://127.0.0.1:8099/api/status | head -c 300
printf '\n\n容器状态：\n'
docker compose ps

# ---------------------------------------------------------------- 6. 后续提示
步骤 6/6 "完成"
cat <<'提示'
本机验证：
    curl -I http://127.0.0.1:8099/

要让评委访问，还差三步（顺序别换）：
    1) 云控制台安全组放行 8099（或 443，取决于是否挂了反向代理）
    2) 用域名/证书把 HTTPS 建起来，拿到固定的 https://… 地址
    3) 把该地址的 /auth/zhihu/callback 登记为知乎回调地址，
       并让 .env 里的 ZHIHU_OAUTH_REDIRECT_URI 与登记值逐字一致
提示
