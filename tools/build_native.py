# -*- coding: utf-8 -*-
"""
编译抖动计算核心（C++ -> maptool_native.dll）
============================================

为什么是 DLL + ctypes 而不是 Python 扩展模块：
    本机 CPython 是 MSVC 编译的，编译器只有 MinGW-w64 g++。MinGW 编 CPython
    扩展要跨 CRT（msvcrt vs ucrt），PyObject* 过边界容易出问题。导出纯 C ABI
    （只有指针和整数）用 ctypes 调用就没有 ABI 风险，也不需要 Python 头文件。

用法（工作区根目录执行）：
    python tools/build_native.py             # 编译
    python tools/build_native.py --check     # 只检查工具链和源码
    python tools/build_native.py --force     # 源码没变也重编

产物：src/maptool/native/maptool_native.dll
     —— 这个 DLL 是可选的：没有它，maptool.dithering 会自动退回纯 Python 实现，
        结果完全一样，只是慢一些。
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = os.path.join(ROOT, "src", "maptool", "native")
SRC = os.path.join(NATIVE, "dither_core.cpp")
OUT = os.path.join(NATIVE, "maptool_native.dll")

CFLAGS = [
    "-O2",                # 优化
    "-std=c++17",
    "-shared",
    # 全静态：不依赖 libgcc_s / libstdc++-6 / libwinpthread-1。
    # 这个 g++ 是 x86_64-posix-seh（posix 线程模型），不加 -static 的话
    # 生成的 DLL 会依赖 libwinpthread-1.dll，ctypes 加载时报
    # "Could not find module ... or one of its dependencies"。
    # 剩下的 api-ms-win-crt-* 是 UCRT，Windows 10+ 自带，不用管。
    "-static",
    "-static-libgcc",
    "-static-libstdc++",
    "-fno-exceptions",
    "-fno-rtti",
    "-Wall",
    "-Wextra",
    "-Wno-unused-parameter",
]


def find_compiler():
    for name in ("g++", "x86_64-w64-mingw32-g++"):
        p = shutil.which(name)
        if p:
            return p
    # 常见的手动安装位置
    for d in (r"D:\mingw64\bin", r"C:\mingw64\bin", r"C:\msys64\mingw64\bin"):
        p = os.path.join(d, "g++.exe")
        if os.path.isfile(p):
            return p
    return None


def newest_mtime(paths):
    m = 0.0
    for p in paths:
        if os.path.isfile(p):
            m = max(m, os.path.getmtime(p))
    return m


def main():
    check_only = "--check" in sys.argv
    force = "--force" in sys.argv

    if not os.path.isfile(SRC):
        raise SystemExit("找不到源码：%s" % SRC)

    cxx = find_compiler()
    if not cxx:
        print("!! 找不到 g++（MinGW-w64）")
        print("   C++ 核是可选的，不装也能跑 —— dithering 会退回纯 Python 实现。")
        print("   想编译就装一个 MinGW-w64，或者把 g++.exe 放进 PATH。")
        return 0 if not check_only else 1

    r = subprocess.run([cxx, "--version"], capture_output=True)
    ver = r.stdout.decode("utf-8", "replace").split("\n")[0].strip()
    print("编译器：%s" % cxx)
    print("        %s" % ver)

    if check_only:
        print("源码  ：%s" % SRC)
        print("产物  ：%s" % OUT)
        return 0

    if not force and os.path.isfile(OUT) and os.path.getmtime(OUT) >= newest_mtime([SRC]):
        print("\n产物比源码新，跳过编译（要强制重编加 --force）")
        print("        %s" % OUT)
        return 0

    cmd = [cxx] + CFLAGS + ["-o", OUT, SRC]
    print("\n%s" % " ".join('"%s"' % c if " " in c else c for c in cmd))
    r = subprocess.run(cmd, capture_output=True)
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    if out.strip():
        print(out)
    if r.returncode != 0:
        print(err)
        raise SystemExit("编译失败（返回码 %d）" % r.returncode)
    if err.strip():
        print("警告：")
        print(err)

    size = os.path.getsize(OUT) / 1024.0
    print("\n" + "=" * 60)
    print("编译完成：%s  （%.1f KB）" % (OUT, size))
    print("=" * 60)

    # 立刻试加载一下，确认能被 ctypes 读进来
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from maptool import native
    native.reset()
    if native.available():
        print("加载测试：√ 成功，版本 %s" % native.version())
    else:
        print("加载测试：× 失败 —— %s" % native.load_error())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
