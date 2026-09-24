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
from .config import HOST, PORT, get_base_dir, web_index_path
from .keepalive import _keepalive_watchdog
from .runtime import safe_pause, stdout_is_tty
from .webapp import app


def main():
    print("=" * 60)
    print(f"地图画工具箱 v{__version__} - 后端服务")
    print(f"Python   : {sys.version.split()[0]}")
    print(f"运行目录 : {os.getcwd()}")
    print(f"资源目录 : {get_base_dir()}")

    idx = web_index_path()
    print(f"web/index.html: {'√ 存在' if idx else '× 缺失'}"
          + (f"  ({idx})" if idx else ""))
    print(f"监听地址 : http://{HOST}:{PORT}")
    print("=" * 60)

    if not idx:
        msg = ("找不到 web/index.html！\n\n"
               f"资源目录：{get_base_dir()}\n\n"
               "源码运行时请确认 src/maptool/web/index.html 存在；\n"
               "绿色版请把 web/ 和 data/ 两个目录放在 exe 旁边。")
        print("[错误] " + msg)
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
        except Exception:
            pass
        safe_pause()
        sys.exit(1)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
    except OSError as e:
        msg = (f"端口 {PORT} 无法绑定：{e}\n\n"
               f"可能有一个旧的进程还占着这个端口。\n\n"
               f"解决方法：\n"
               f"  1. 命令行执行：\n"
               f"     netstat -ano | findstr :{PORT}\n"
               f"     找到 PID 后：taskkill /PID <PID> /F\n"
               f"  2. 或者换个端口启动（不影响已有实例）：\n"
               f"     set MAPART_PORT=8899\n"
               f"     然后再运行本程序")
        print("[错误] " + msg)
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
        except Exception:
            pass
        safe_pause()
        sys.exit(2)

    url = f"http://{HOST}:{PORT}"
    server_error = {"msg": None}

    def run_server():
        try:
            # use_colors 一定要显式给布尔值：留空时 uvicorn 会去调
            # sys.stdout.isatty() 来判断，而无控制台环境下那是 None。
            kwargs = dict(host=HOST, port=PORT, log_level="warning")
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

    # 页面保活看门狗：所有页面关掉之后自动结束本进程
    threading.Thread(target=_keepalive_watchdog, daemon=True).start()

    ready = False
    for _ in range(150):
        if server_error["msg"]:
            break
        try:
            with socket.create_connection((HOST, PORT), timeout=0.3):
                ready = True
                break
        except OSError:
            time.sleep(0.1)

    if server_error["msg"]:
        msg = f"服务器启动失败：{server_error['msg']}"
        print("[错误] " + msg)
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
        except Exception:
            pass
        safe_pause()
        sys.exit(3)

    if ready:
        print(f"√ 服务器就绪：{url}")
        try:
            webbrowser.open(url)
            print("√ 浏览器已打开")
        except Exception as e:
            print(f"! 自动打开浏览器失败：{e}")
            print(f"  请手动访问：{url}")
    else:
        print(f"! 等待超时，请手动访问：{url}")

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
