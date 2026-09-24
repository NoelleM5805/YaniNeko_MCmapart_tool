# -*- coding: utf-8 -*-
"""
C++ 匹配器 vs numpy 匹配器：全空间等价性验证
============================================

误差扩散里每一步匹配的输入都是**量化后的颜色** —— `clamp(int(v+0.5), 0, 255)`，
也就是 256×256×256 = 16 777 216 种可能。所以只要把这 1677 万种颜色的匹配结果
和 numpy 版逐个比一遍，就能证明 C++ 版在**任意输入下**都等价，不需要靠抽样。

这是整个 C++ 重构的核心保证：结果不用「应该一致」，而是「证明了一致」。

用法（工作区根目录）：
    python tests/native_match_equiv.py                     # 全部算法 + 默认调色板
    python tests/native_match_equiv.py --algos ciede2000   # 只测某几个
    python tests/native_match_equiv.py --palette small     # 再加一个小调色板（更容易出现临界）

已知：MinGW 的 std::cos/std::sin 和 numpy 的 np.cos/np.sin 有约 3% 的情况
差 1 ULP，而 CIEDE2000 用到 cos/sin。这个测试就是用来确认这点差异到底会不会
让 argmin 选到别的颜色。
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np

from maptool import native
from maptool.matching import match_batch
from maptool.palette import PALETTE_GROUPS, make_palette

# numpy 侧每批处理多少个颜色：32768*59*8 ≈ 15 MB 一个中间数组，
# CIEDE2000 会同时有 30 来个中间数组，再大就吃掉几个 G 内存了。
CHUNK = 32768
TOTAL = 256 * 256 * 256


def colors_chunk(offset, count):
    """第 offset..offset+count 个 (r,g,b)，r 最慢、b 最快 —— 和 C++ 的循环顺序一致。"""
    idx = np.arange(offset, offset + count, dtype=np.int64)
    r = (idx >> 16) & 0xFF
    g = (idx >> 8) & 0xFF
    b = idx & 0xFF
    return np.stack([r, g, b], axis=1).astype(np.int32)


def numpy_lut(algo, pal, tables):
    """
    用 numpy 版把整个 256³ 空间算出来（16 MB）。

    注意：match_batch 现在**优先走 C++**，所以必须先 native.set_enabled(False)
    把它按回纯 numpy 实现 —— 否则这里比的就是 C++ 跟 C++，测试等于没测。
    """
    out = np.empty(TOTAL, dtype=np.uint8)
    native.set_enabled(False)
    try:
        for off in range(0, TOTAL, CHUNK):
            n = min(CHUNK, TOTAL - off)
            rgb = colors_chunk(off, n)
            idx = match_batch(rgb, pal, algo, tables)
            out[off:off + n] = idx.astype(np.uint8)
    finally:
        native.set_enabled(True)
    return out.reshape(256, 256, 256)


def check_algo(algo, groups, label):
    from maptool.matching import _make_dist_tables

    ids = [g["blocks"][0]["id"] for g in groups]
    pal, _used = make_palette(ids)
    tables = _make_dist_tables(algo, pal)

    print("=" * 74)
    print("%s   算法=%s   调色板=%s（%d 个颜色）" % (label, algo, "groups", pal.n))
    print("=" * 74)

    t0 = time.perf_counter()
    h = native.make_handle(algo, pal)
    cpp = h.match_lut()
    t_cpp = time.perf_counter() - t0
    print("  C++  算完 1677 万种颜色：%6.2f s" % t_cpp)

    t0 = time.perf_counter()
    npy = numpy_lut(algo, pal, tables)
    t_npy = time.perf_counter() - t0
    print("  numpy 算完 1677 万种颜色：%6.2f s   （C++ 快 %.1f×）" % (t_npy, t_npy / max(t_cpp, 1e-9)))

    diff = cpp != npy
    n_bad = int(diff.sum())
    if n_bad == 0:
        print("  [√] 全部 %d 种颜色匹配结果完全一致" % TOTAL)
        return True, n_bad

    print("  [×] 有 %d 种颜色（%.6f%%）选了不同的颜色" % (n_bad, n_bad * 100.0 / TOTAL))
    where = np.argwhere(diff)[:8]
    for r, g, b in where:
        r, g, b = int(r), int(g), int(b)
        print("      RGB(%3d,%3d,%3d)  C++ -> %-3d %s   numpy -> %-3d %s"
              % (r, g, b, cpp[r, g, b], pal.hexes[cpp[r, g, b]],
                 npy[r, g, b], pal.hexes[npy[r, g, b]]))
    return False, n_bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--algos", default="euclidean,weighted,redmean,cie76,cie94,ciede2000")
    ap.add_argument("--palette", default="default", choices=("default", "small", "both"))
    args = ap.parse_args()

    if not native.available():
        print("native 库不可用：%s" % native.load_error())
        return 2
    print("native 版本：%s" % native.version())
    print()

    algos = [a.strip() for a in args.algos.split(",") if a.strip()]

    palettes = [("默认调色板（59 色）", PALETTE_GROUPS)]
    if args.palette in ("small", "both"):
        # 挑一小撮颜色：颜色少 + 彼此接近，最容易出现「差一点点就选另一个」
        rng = np.random.default_rng(12345)
        pick = sorted(rng.choice(len(PALETTE_GROUPS), 11, replace=False).tolist())
        palettes.append(("小调色板（11 色）", [PALETTE_GROUPS[i] for i in pick]))

    results = []
    for label, groups in palettes:
        for algo in algos:
            ok, n_bad = check_algo(algo, groups, label)
            results.append((label, algo, ok, n_bad))
            print()

    print("=" * 74)
    print("汇总")
    print("=" * 74)
    bad = [(l, a, n) for l, a, ok, n in results if not ok]
    for l, a, ok, n in results:
        print("  %-24s %-12s %s" % (l, a, "一致" if ok else "差异 %d 种颜色" % n))
    print()
    if bad:
        print("有差异的算法不能用 C++ 路径：")
        for l, a, n in bad:
            print("   × %s / %s" % (l, a))
    else:
        print("全部一致 —— C++ 路径可以放心替换 Python 版")
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
