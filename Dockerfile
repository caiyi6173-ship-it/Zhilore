# 知脉 · 一体化入口（知乎 OAuth 登录门禁 + 知识库工作区）
#
# 一个镜像装齐三块运行时要的东西：
#   工具/   —— FastAPI 后端 + zhihu_oauth
#   网页/   —— 前端静态资源（图谱、工作区、登录页）
#   笔记库/ —— 知识库数据（.raw/sources、wiki、_索引.json、_图谱.json）
#
# 不依赖数据库、不依赖外部存储：数据随镜像一起走，单容器即可运行。

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai

WORKDIR /app

# 只为时区装 tzdata。其余依赖都是纯 Python wheel，不需要编译工具链。
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

# pip 源可在构建时覆盖。
# 默认官方源；国内服务器构建时建议传国内源，例如：
#   docker compose build --build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX=https://pypi.org/simple

# 依赖单独一层：之后只改代码不会触发重装
COPY 工具/requirements-oauth.txt ./工具/requirements-oauth.txt
RUN pip install --no-cache-dir -i "${PIP_INDEX}" -r 工具/requirements-oauth.txt \
 && pip install --no-cache-dir -i "${PIP_INDEX}" "PyYAML>=6,<7"
# PyYAML 单独装的原因：/api/重扫 会以子进程调用 处理.py → 知识库IO.py，那里 import yaml。
# 不装的话服务能起，但一点「重扫」就 500。既有 requirements-oauth.txt 不动，避免影响测试语义。

# 运行时要的三块
COPY 工具/ ./工具/
COPY 网页/ ./网页/
COPY 笔记库/ ./笔记库/

# 清掉构建上下文带进来的字节码，避免与源码不一致
RUN find /app -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# 双保险：本机浏览器登录态（工具/_浏览器数据）带着知乎 Cookie，运行时完全不需要。
# .dockerignore 里已经排除，这里再删一次——万一以后有人动了 .dockerignore，
# 也不至于把凭据材料打进公网镜像（2026-09-15 的技术债 T1）。
RUN rm -rf /app/工具/_浏览器数据 /app/工具/抓取缓存

EXPOSE 8099

# 两个参数是容器里必须的：
#   --host 0.0.0.0  默认是 127.0.0.1，只监听容器内回环，映射出来也访问不到
#   --不开浏览器     容器里没有浏览器；不加会走 webbrowser.open 并把启动日志搞乱
CMD ["python", "工具/应用.py", "--host", "0.0.0.0", "--端口", "8099", "--不开浏览器"]
