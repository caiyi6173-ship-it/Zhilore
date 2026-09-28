# -*- coding: utf-8 -*-
"""预览截图：把网页各视图截下来，方便快速检查效果"""
import os
import sys

try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

工具目录 = os.path.dirname(os.path.abspath(__file__))
项目根 = os.path.dirname(工具目录)
输出目录 = os.path.join(项目根, "outputs", "预览")
os.makedirs(输出目录, exist_ok=True)

地址 = "http://127.0.0.1:8099/"
RAG = "AI技术/RAG 到底该怎么落地：从切分到重排的完整链路.md"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 960}, device_scale_factor=1.4)

    # 1. 首页
    pg.goto(地址, wait_until="networkidle")
    pg.wait_for_timeout(1600)
    pg.screenshot(path=os.path.join(输出目录, "01-首页.png"))
    print("01 首页 完成")

    # 2. 笔记详情（含公式 / 表格 / 代码 / 图片）
    pg.goto(地址 + "#" + RAG, wait_until="networkidle")
    pg.wait_for_timeout(2200)
    pg.screenshot(path=os.path.join(输出目录, "02-笔记-顶部.png"))
    pg.evaluate("document.getElementById('content').scrollTo(0, 1500)")
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(输出目录, "03-笔记-正文.png"))
    pg.evaluate("document.getElementById('content').scrollTo(0, 3000)")
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(输出目录, "04-笔记-表格公式.png"))
    print("02-04 笔记 完成")

    # 3. 搜索面板
    pg.evaluate("document.getElementById('content').scrollTo(0, 0)")
    pg.keyboard.press("Control+k")
    pg.wait_for_timeout(400)
    pg.keyboard.type("向量", delay=60)
    pg.wait_for_timeout(1200)
    pg.screenshot(path=os.path.join(输出目录, "05-搜索.png"))
    print("05 搜索 完成")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 4. 图谱
    pg.click("#btn-graph")
    pg.wait_for_timeout(2600)
    pg.screenshot(path=os.path.join(输出目录, "06-图谱.png"))
    print("06 图谱 完成")
    pg.click("[data-close]")
    pg.wait_for_timeout(300)

    # 5. 深色主题
    pg.click("#btn-theme")
    pg.wait_for_timeout(700)
    pg.screenshot(path=os.path.join(输出目录, "07-深色笔记.png"))
    print("07 深色 完成")

    # 6. 标签云（深色下）
    pg.click("#btn-tags")
    pg.wait_for_timeout(900)
    pg.screenshot(path=os.path.join(输出目录, "08-标签云.png"))
    print("08 标签 完成")

    b.close()

print("截图目录：", 输出目录)
