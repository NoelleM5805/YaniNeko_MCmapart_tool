# -*- coding: utf-8 -*-
"""
Pictomapart 入口
=================

这个文件只有一件事：把 src/ 放到 sys.path 上，然后调用 maptool 包的 main()。

为什么需要它：
  * PyInstaller 需要一个「脚本」当入口。直接把 maptool/__main__.py 当入口的话，
    里面的 `from . import __version__` 这种相对导入会因为不是包的一部分而失败。
  * 单独放一个入口，源码运行（python src/run_maptool.py）和打包运行
    （Pictomapart.exe）走的是同一段代码。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from maptool.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
