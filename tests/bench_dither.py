# -*- coding: utf-8 -*-
"""
抖动 / 匹配性能基准
===================

用来确定 C++ 重构到底该优化哪一段。
跑法（工作区根目录）：

    python tests/bench_dither.py [尺寸]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
from PIL import Image

from maptool.palette import make_palette
from maptool import dithering as D


def make_img(w, h, seed=3):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    r = 40 + 180 * (xx / w)
    g = 30 + 200 * (yy / h)
    b = 90 + 120 * np.sin((xx + yy) / 40.0)
    img = np.stack([r, g, b], axis=-1)
    img += rng.normal(0, 12, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def bench(pal, img, algo, dither, strength=1.0):
    t0 = time.perf_counter()
    idx, rgb = D.process_image(img, algo, dither, strength, pal)
    return time.perf_counter() - t0, idx


def main():
    side = int(sys.argv[1]) if len(sys.argv) > 1 else 384
    img = make_img(side, side)
    pal, used = make_palette(None)
    print("调色板：%d 组颜色 / %d 个方块" % (pal.n, len(used)))
    print("图像：%dx%d = %d 像素" % (side, side, side * side))
    print()
    cases = [
        ("weighted", "none"),
        ("weighted", "bayer8"),
        ("weighted", "floyd"),
        ("weighted", "atkinson"),
        ("weighted", "sierra_lite"),
        ("redmean", "floyd"),
        ("cie94", "floyd"),
        ("ciede2000", "none"),
        ("ciede2000", "floyd"),
    ]
    print("%-12s %-12s %10s" % ("算法", "抖动", "耗时"))
    print("-" * 38)
    for algo, dither in cases:
        dt, _ = bench(pal, img, algo, dither)
        print("%-12s %-12s %9.3f s" % (algo, dither, dt))


if __name__ == "__main__":
    main()
