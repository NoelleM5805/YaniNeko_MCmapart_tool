# -*- coding: utf-8 -*-
"""
把后端源码复制进安卓工程，供 Chaquopy 打包。

用法（工作区根目录）：
    python android/prepare.py

做的事：把 src/maptool/ 整体复制到 android/app/src/main/python/maptool/，
排除 __pycache__ / *.pyc / *.dll（Windows 专属、安卓用不上）。
web/ 和 data/ 都在 maptool 包里，会一起复制，后端 get_base_dir() 靠 __file__ 就能找到。

复制出来的 python/ 目录是构建产物，已在 .gitignore 里忽略，随时可重跑覆盖。
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "maptool")
DST = os.path.join(ROOT, "android", "app", "src", "main", "python", "maptool")

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_EXT = {".pyc", ".pyo", ".dll", ".pyd"}


def main():
    if not os.path.isdir(SRC):
        print("[错误] 找不到 %s" % SRC)
        return 1

    if os.path.exists(DST):
        shutil.rmtree(DST)
    os.makedirs(DST)

    copied = 0
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        rel = os.path.relpath(dirpath, SRC)
        target_dir = DST if rel == "." else os.path.join(DST, rel)
        os.makedirs(target_dir, exist_ok=True)
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in EXCLUDE_EXT:
                continue
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(target_dir, fn))
            copied += 1

    print("√ 已复制 %d 个文件到 %s" % (copied, os.path.relpath(DST, ROOT)))

    # 自检：关键资源是否就位（后端启动自检 web/index.html）
    for check in ("web/index.html", "web/css/style.css", "web/css/mobile.css",
                  "web/js/98-mobile.js", "data/blockdata.py", "data/block_icons.png"):
        p = os.path.join(DST, check)
        if not os.path.isfile(p):
            print("[警告] 缺少 %s" % check)
    print("√ 自检完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
