# -*- coding: utf-8 -*-
"""
C++ 抖动核心 vs 纯 Python：端到端逐像素等价性
=============================================

`tests/native_match_equiv.py` 证明的是「匹配器」在全颜色空间一致；
这里验证的是**整条流水线**：同样的图片、同样的调色板、同样的参数，
走 C++ 和走纯 Python 得到的调色板下标数组必须逐像素完全相同。

覆盖：6 种颜色算法 × 10 种抖动 × 若干强度 × 若干尺寸/图片。
没有 C++ 库时跳过（只跑一遍 Python 基准）。

用法（工作区根目录）：
    python tests/dither_native_equiv.py
    python tests/dither_native_equiv.py --algos weighted,redmean
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
from PIL import Image

from maptool import native
from maptool import dithering as D
from maptool.palette import make_palette

ALGOS = ["euclidean", "weighted", "redmean", "cie76", "cie94", "ciede2000"]
DITHERS = ["none", "bayer4", "bayer8", "floyd", "atkinson", "jarvis",
           "stucki", "burkes", "sierra", "sierra_lite"]
STRENGTHS = [0.25, 0.6, 1.0]


def images():
    """几张风格不同的图：渐变、色块、噪声、纯灰阶、单色。"""
    out = []
    rng = np.random.default_rng(3)

    w, h = 96, 64
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    out.append(("渐变+色块", Image.fromarray(np.clip(np.stack(
        [40 + 180 * (xx / w), 30 + 200 * (yy / h),
         90 + 120 * np.sin((xx + yy) / 40.0)], axis=-1), 0, 255).astype(np.uint8), "RGB")))

    noise = np.clip(rng.normal(128, 55, (h, w, 3)), 0, 255).astype(np.uint8)
    out.append(("白噪声", Image.fromarray(noise, "RGB")))

    ramp = np.repeat(np.arange(256, dtype=np.uint8)[None, :128], 64, axis=0)
    out.append(("纯灰阶渐变", Image.fromarray(
        np.stack([ramp] * 3, axis=-1)[:, :96].copy(), "RGB")))

    flat = np.zeros((48, 48, 3), np.uint8)
    flat[:, :] = (200, 30, 60)
    out.append(("单一颜色", Image.fromarray(flat, "RGB")))

    # 小尺寸：验证 LUT / 哈希表在小图上也对
    small = np.clip(rng.normal(120, 70, (17, 23, 3)), 0, 255).astype(np.uint8)
    out.append(("小图 23×17", Image.fromarray(small, "RGB")))
    return out


def run_one(img, algo, dither, strength, pal, use_native):
    native.set_enabled(use_native)
    t0 = time.perf_counter()
    idx, rgb = D.process_image(img, algo, dither, strength, pal)
    dt = time.perf_counter() - t0
    return idx, rgb, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--algos", default=",".join(ALGOS))
    ap.add_argument("--dithers", default=",".join(DITHERS))
    ap.add_argument("--max-cases", type=int, default=0)
    args = ap.parse_args()

    algos = [a.strip() for a in args.algos.split(",") if a.strip()]
    dithers = [d.strip() for d in args.dithers.split(",") if d.strip()]

    pal, used = make_palette(None)
    print("调色板：%d 个颜色 / %d 个方块" % (pal.n, len(used)))

    have_native = native.available()
    print("C++ 核心：%s" % (("可用，版本 " + native.version()) if have_native
                          else ("不可用 —— " + native.load_error())))
    if not have_native:
        print("只跑一遍纯 Python 基准，跳过等价性比对。")
    print()

    imgs = images()
    cases = [(im, nm, a, d, s) for nm, im in imgs for a in algos
             for d in dithers for s in STRENGTHS]
    if args.max_cases:
        cases = cases[:args.max_cases]
    print("用例数：%d（%d 张图 × %d 算法 × %d 抖动 × %d 强度）"
          % (len(cases), len(imgs), len(algos), len(dithers), len(STRENGTHS)))
    print()

    n_pass = n_fail = n_skip = 0
    fails = []
    py_total = cpp_total = 0.0
    t_start = time.perf_counter()

    for k, (img, name, algo, dither, strength) in enumerate(cases):
        idx_py, rgb_py, t_py = run_one(img, algo, dither, strength, pal, False)
        py_total += t_py
        if not have_native:
            n_skip += 1
            continue
        try:
            idx_cpp, rgb_cpp, t_cpp = run_one(img, algo, dither, strength, pal, True)
        except Exception as e:                        # noqa: BLE001
            n_fail += 1
            fails.append("%s/%s/%s/%.2f 抛异常：%s" % (name, algo, dither, strength, e))
            native.set_enabled(True)
            continue
        cpp_total += t_cpp

        same = np.array_equal(idx_py, idx_cpp) and np.array_equal(rgb_py, rgb_cpp)
        if same:
            n_pass += 1
        else:
            n_fail += 1
            diff = int((idx_py != idx_cpp).sum())
            fails.append("%s/%s/%s/%.2f 下标数组有 %d/%d 个像素不同"
                         % (name, algo, dither, strength, diff, idx_py.size))

        if (k + 1) % 60 == 0:
            print("  …已跑 %d/%d" % (k + 1, len(cases)))

    native.set_enabled(True)
    dt = time.perf_counter() - t_start

    print()
    print("=" * 74)
    if have_native:
        print("通过 %d 项，失败 %d 项（总耗时 %.1f s）" % (n_pass, n_fail, dt))
        if py_total > 0:
            print("纯 Python 累计 %.1f s ｜ C++ 累计 %.1f s ｜ 加速 %.1f×"
                  % (py_total, cpp_total, py_total / max(cpp_total, 1e-9)))
    else:
        print("跳过 %d 项（没有 C++ 库）" % n_skip)
    if fails:
        print()
        print("失败的用例：")
        for f in fails[:40]:
            print("   × " + f)
        if len(fails) > 40:
            print("   …还有 %d 条" % (len(fails) - 40))
    print("=" * 74)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
