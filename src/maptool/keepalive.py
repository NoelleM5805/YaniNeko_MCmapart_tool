# -*- coding: utf-8 -*-
import os
import sys
import threading
import time



# ============================================================
# 页面保活：网页全关掉后自动退出进程
# ============================================================
# 用一条 SSE 长连接来判断页面是否还在，而不是定时 ping：
# 定时器在浏览器后台标签页里会被限流到每分钟一次，容易误判；
# 长连接则在标签页关闭的瞬间断开，最可靠。
KEEPALIVE = {
    "conns": 0,             # 当前打开的页面数
    "last_seen": 0.0,       # 最后一次有页面连接的时间
    "armed": False,         # 至少连上过一次页面才启用
    "exit_on_close": True,  # 前端可以关掉这个行为
}
KEEPALIVE_LOCK = threading.Lock()
KEEPALIVE_GRACE = 15.0      # 页面全关后等这么久再退出（容忍刷新 / 短暂重连）
KEEPALIVE_TICK = 2.0        # SSE 心跳间隔


def _keepalive_note(on):
    """前端告知：关页面要不要顺带关掉服务。"""
    with KEEPALIVE_LOCK:
        KEEPALIVE["exit_on_close"] = bool(on)
        KEEPALIVE["last_seen"] = time.time()


def _keepalive_watchdog():
    while True:
        time.sleep(KEEPALIVE_TICK)
        with KEEPALIVE_LOCK:
            armed = KEEPALIVE["armed"]
            enabled = KEEPALIVE["exit_on_close"]
            conns = KEEPALIVE["conns"]
            idle = time.time() - KEEPALIVE["last_seen"]
        if not armed or not enabled or conns > 0:
            continue
        if idle > KEEPALIVE_GRACE:
            print("所有页面已关闭，%d 秒无连接，自动退出。" % int(idle))
            sys.stdout.flush()
            os._exit(0)


