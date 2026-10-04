# -*- coding: utf-8 -*-
import os
import socket
import sys
import threading
import time
import traceback
import uvicorn
import webbrowser

from . import __version__
from .config import (HOST, PORT, PORT_SCAN_LIMIT, find_available_port,
                     get_base_dir, is_android, web_index_path)
from .keepalive import _keepalive_watchdog
from .runtime import safe_pause, stdout_is_tty
from .webapp import app


class StartupError(Exception):
    """启动失败。code 对应旧的退出码（1 资源缺失 / 2 端口 / 3 服务器）。"""

    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def _show_error(msg):
    """打印错误；桌面 Windows 再弹一个对话框兜底（无控制台时也能看到）。"""
    print("[错误] " + msg)
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
    except Exception:
        pass


def _print_banner(port):
    print("=" * 60)
    print(f"地图画工具箱 v{__version__} - 后端服务")
    print(f"Python   : {sys.version.split()[0]}")
    print(f"运行目录 : {os.getcwd()}")
    print(f"资源目录 : {get_base_dir()}")
    idx = web_index_path()
    print(f"web/index.html: {'√ 存在' if idx else '× 缺失'}"
          + (f"  ({idx})" if idx else ""))
    print(f"默认端口 : {port}")
    print("=" * 60)


def start_server(host=HOST, port=None, open_browser=None, start_keepalive=None,
                 quiet=False):
    """
    启动后端服务并等待就绪。成功返回 (url, server_thread)，失败抛 StartupError。

    桌面 CLI 与安卓宿主共用这一入口：

      - 桌面：open_browser / start_keepalive 默认按平台取 True（开浏览器 + 页面
        全关自动退出），随后 main() 进入主循环等待 Ctrl+C。
      - 安卓：宿主设置 MAPART_PLATFORM=android 后，两个开关默认自动变 False ——
        不开浏览器（由 WebView 加载 url）、不起看门狗（生命周期交给 Activity）。
        宿主拿到 url 交给 WebView 即可。
    """
    if port is None:
        port = PORT
    if open_browser is None:
        open_browser = not is_android()
    if start_keepalive is None:
        start_keepalive = not is_android()

    if not quiet:
        _print_banner(port)

    idx = web_index_path()
    if not idx:
        raise StartupError(
            "找不到 web/index.html！\n\n"
            f"资源目录：{get_base_dir()}\n\n"
            "源码运行时请确认 src/maptool/web/index.html 存在；\n"
            "绿色版请把 web/ 和 data/ 两个目录放在 exe 旁边。", 1)

    actual_port = find_available_port(port, host)
    if actual_port is None:
        raise StartupError(
            f"从 {port} 开始的 {PORT_SCAN_LIMIT} 个端口都无法绑定。\n\n"
            f"可能已经有太多实例在运行，或者当前环境限制了本地监听。\n"
            f"请关闭一些旧进程后重试。", 2)
    if actual_port != port:
        print(f"! 端口 {port} 被占用，已自动切换为 {actual_port}")

    url = f"http://{host}:{actual_port}"
    server_error = {"msg": None}

    def run_server():
        try:
            # use_colors 一定要显式给布尔值：留空时 uvicorn 会去调
            # sys.stdout.isatty() 来判断，而无控制台环境下那是 None。
            kwargs = dict(host=host, port=actual_port, log_level="warning")
            try:
                import inspect as _inspect
                if "use_colors" in _inspect.signature(uvicorn.run).parameters:
                    kwargs["use_colors"] = stdout_is_tty()
            except Exception:
                pass
            uvicorn.run(app, **kwargs)
        except Exception as e:
            server_error["msg"] = str(e)
            traceback.print_exc()

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # 页面保活看门狗：所有页面关掉之后自动结束本进程（安卓下不启动）
    if start_keepalive:
        threading.Thread(target=_keepalive_watchdog, daemon=True).start()

    ready = False
    for _ in range(150):
        if server_error["msg"]:
            break
        try:
            with socket.create_connection((host, actual_port), timeout=0.3):
                ready = True
                break
        except OSError:
            time.sleep(0.1)

    if server_error["msg"]:
        raise StartupError(f"服务器启动失败：{server_error['msg']}", 3)

    if not quiet:
        if ready:
            print(f"√ 服务器就绪：{url}")
        else:
            print(f"! 等待超时，请手动访问：{url}")

    if open_browser and ready:
        try:
            webbrowser.open(url)
            if not quiet:
                print("√ 浏览器已打开")
        except Exception as e:
            if not quiet:
                print(f"! 自动打开浏览器失败：{e}")
                print(f"  请手动访问：{url}")

    return url, t


def main():
    try:
        url, t = start_server()
    except StartupError as e:
        _show_error(str(e))
        safe_pause()
        sys.exit(e.code)

    print("-" * 60)
    print("服务运行中。要停止服务，按 Ctrl+C 或直接关闭本窗口。")
    print("-" * 60)

    try:
        while True:
            time.sleep(1)
            if not t.is_alive():
                print("[警告] 服务器线程已退出")
                break
    except KeyboardInterrupt:
        print("\n已收到 Ctrl+C，正在退出…")


if __name__ == "__main__":
    main()
