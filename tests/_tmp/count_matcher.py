# -*- coding: utf-8 -*-
"""量一下误差扩散里 matcher 到底被调用多少次（cache 未命中的次数）。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
from PIL import Image

from maptool.palette import make_palette
from maptool import matching as M
from maptool import dithering as D

side = int(sys.argv[1]) if len(sys.argv) > 1 else 384
rng = np.random.default_rng(3)
yy, xx = np.mgrid[0:side, 0:side].astype(np.float64)
img = np.stack([40 + 180 * (xx / side), 30 + 200 * (yy / side),
                90 + 120 * np.sin((xx + yy) / 40.0)], axis=-1)
img += rng.normal(0, 12, img.shape)
img = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
pal, _u = make_palette(None)


def instrument(algo):
    """把 dithering 里用到的 get_matcher 换掉，数调用次数。"""
    orig = D.get_matcher
    calls = {"n": 0}

    def wrapped(k, p):
        f = orig(k, p)

        def g(r, g_, b):
            calls["n"] += 1
            return f(r, g_, b)
        return g

    D.get_matcher = wrapped
    try:
        t0 = time.perf_counter()
        idx, _rgb = D.process_image(img, algo, "floyd", 1.0, pal)
        dt = time.perf_counter() - t0
    finally:
        D.get_matcher = orig
    n = side * side
    per = dt / calls["n"] * 1e6 if calls["n"] else 0.0
    loop = dt - calls["n"] * per / 1e6
    print("%-11s 总 %6.3f s ｜ matcher %6d 次 (%.1f%% 像素) ｜ 单次 %5.2f µs ｜ "
          "推算纯循环 %6.3f s (%.0f%%)"
          % (algo, dt, calls["n"], calls["n"] * 100.0 / n, per,
             loop, loop * 100.0 / dt))


for algo in ("weighted", "redmean", "cie76", "cie94", "ciede2000"):
    instrument(algo)
