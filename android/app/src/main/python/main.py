# -*- coding: utf-8 -*-
"""安卓宿主入口：由 MainActivity 通过 Chaquopy 调用 start()。

做的事只有一件：设好安卓标记，起后端服务，把监听 URL 返回给宿主交给 WebView。
"""

import os


def start():
    # 让 maptool 走安卓分支：不开浏览器、不起「关页面自动退出」看门狗。
    os.environ.setdefault("MAPART_PLATFORM", "android")

    from maptool.__main__ import start_server

    # open_browser / start_keepalive 已按 is_android() 自动取 False，这里再显式写清。
    url, _server_thread = start_server(open_browser=False, start_keepalive=False)
    return url
