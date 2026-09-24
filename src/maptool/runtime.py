# -*- coding: utf-8 -*-
import io
import sys
import traceback



# ============================================================
# 入口
# ============================================================
class _NullWriter:
    """
    没有控制台时的占位输出流。

    pythonw 启动、或用 PyInstaller 的 --windowed 打包之后，sys.stdout / sys.stderr
    是 None。很多第三方库（uvicorn 就是）会直接拿它当流用：

        self.use_colors = sys.stdout.isatty()      # uvicorn/logging.py
        logging.StreamHandler(sys.stderr).write(…) # 日志处理器

    None 上调用 isatty() 会抛 AttributeError，uvicorn 会把日志初始化搞失败，
    最终报成 "Unable to configure formatter 'default'"。给一个什么都不做、
    但接口完整的假流，比到处打补丁稳。
    """

    encoding = "utf-8"
    errors = "replace"
    closed = False
    name = "<no console>"

    def write(self, s):
        return len(s) if s else 0

    def writelines(self, lines):
        return None

    def flush(self):
        return None

    def isatty(self):
        return False

    def readable(self):
        return False

    def writable(self):
        return True

    def seekable(self):
        return False

    def close(self):
        return None

    def fileno(self):
        raise OSError("没有控制台，无法取得文件描述符")


def stdout_is_tty():
    st = getattr(sys, "stdout", None)
    try:
        return bool(st is not None and st.isatty())
    except Exception:
        return False


def _setup_console():
    """
    1. 没有控制台（stdout/stderr 为 None）时装上假流，避免第三方库炸。
    2. 有控制台时把编码错误降级为替换字符 —— 冻结成 exe 后代码页往往不是
       UTF-8，一 print 出 GBK 里没有的符号（✓ ✗ ⚠ …）就会抛
       UnicodeEncodeError 把进程带崩。
    """
    for name in ("stdout", "stderr"):
        st = getattr(sys, name, None)
        if st is None:                     # pythonw / --windowed：没有控制台
            setattr(sys, name, _NullWriter())
            continue
        try:
            st.reconfigure(errors="replace")      # 保留原编码，只把编不出的字符换成 ?
            continue
        except Exception:
            pass
        buf = getattr(st, "buffer", None)
        if buf is None:
            continue
        try:
            enc = getattr(st, "encoding", None) or "utf-8"
            setattr(sys, name, io.TextIOWrapper(
                buf, encoding=enc, errors="replace", line_buffering=True))
        except Exception:
            pass


def _install_excepthook():
    """
    窗口版 exe 崩溃时控制台信息是看不见的，这里弹一个对话框把错误显示出来，
    免得用户只看到"双击没反应"。
    """
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            sys.stderr.write(text)
        except Exception:
            pass
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, ("程序启动失败：\n\n" + text)[-1800:],
                    "地图画工具箱", 0x10)
        except Exception:
            pass

    sys.excepthook = hook


def safe_pause(prompt="按回车键退出…"):
    """
    安全版 input()：没有可用终端时直接静默返回。

    用 pythonw 启动、或用 PyInstaller 的 --windowed 打包之后，进程没有控制台，
    sys.stdin 可能是 None 或失效的流，此时 input() 会抛
    RuntimeError: lost sys.stdin（旧代码只捕获 EOFError，兜不住）。

    返回 True 表示确实等到了用户按键，False 表示没有交互终端、直接跳过。
    """
    stream = getattr(sys, "stdin", None)
    if stream is None:
        return False
    try:
        if getattr(stream, "closed", False):
            return False
        if not stream.isatty():          # 管道 / 重定向 / 伪终端都当作非交互
            return False
    except Exception:
        return False
    try:
        input(prompt)
        return True
    except (EOFError, KeyboardInterrupt, RuntimeError, OSError, ValueError):
        return False


_setup_console()
_install_excepthook()


