# -*- coding: utf-8 -*-
"""Unified account + personal summary workspace. Do NOT publicly expose 服务.py instead."""
import argparse
import ipaddress

import uvicorn

from zhihu_oauth.app import create_app

app = create_app()


def main():
    parser = argparse.ArgumentParser(description="知乎账号与个人知识库服务（不提供 8099 共享笔记库）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--forwarded-allow-ips", default="", help="仅在反向代理部署时指定可信代理 IP/CIDR；禁止 *")
    args = parser.parse_args()
    for address in filter(None, args.forwarded_allow_ips.split(",")):
        try:
            ipaddress.ip_network(address.strip(), strict=False)
        except ValueError:
            parser.error("可信代理必须是明确的 IP/CIDR，不能使用 *")
    print("个人知识库服务：单进程、仅内存会话；重启需重新登录，摘要按用户 ID 持久保存。")
    print("本机默认入口 http://127.0.0.1:8100 ，正式访问地址须与回调地址同源。")
    print("已禁用访问日志以保护回调授权码；反向代理也必须隐藏回调查询串。")
    uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False,
                proxy_headers=bool(args.forwarded_allow_ips), forwarded_allow_ips=args.forwarded_allow_ips,
                log_level="warning", limit_concurrency=64, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
