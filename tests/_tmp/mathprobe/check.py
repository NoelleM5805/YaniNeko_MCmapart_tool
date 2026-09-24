# -*- coding: utf-8 -*-
"""
数学函数一致性：MinGW libm vs Python/numpy，按位比对。

CIEDE2000 / CIE94 用到 hypot / arctan2 / cos / exp / pow 这些函数，
两边只要差 1 ULP 就可能让 argmin 选到相邻的另一个颜色，所以必须先量清楚。
"""
import math
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "probe.cpp")
EXE = os.path.join(HERE, "probe.exe")
INP = os.path.join(HERE, "inputs.bin")

N = 40000
rng = np.random.default_rng(20240922)

NAMES = [
    "hypot  std:: vs math.hypot",
    "sqrt(a*a+b*b) vs math.hypot",
    "arctan2  std vs np",
    "degrees  std vs np.degrees",
    "radians  std vs np.radians",
    "cos      std vs np.cos",
    "exp      std vs np.exp",
    "sqrt(|a|) std vs np.sqrt",
    "pow(2.4)  std vs np.power",
    "pow(1/3)  std vs np.power",
    "cbrt      std vs np.cbrt",
    "pow(7)    std vs np.power",
    "手动7次方 vs np t*t*t*t*t*t*t",
    "pow(2)    std vs np.power",
    "t*t       std vs np",
    "sin       std vs np.sin",
]


def gen():
    # Lab 的 a/b 大约在 ±128 内；sRGB 线性化输入 0..1；角度用 ±400
    a = np.concatenate([rng.uniform(-130, 130, N // 2),
                        rng.normal(0, 40, N - N // 2)])
    b = np.concatenate([rng.uniform(-130, 130, N // 2),
                        rng.normal(0, 40, N - N // 2)])
    np.stack([a, b], axis=1).astype(np.float64).tofile(INP)
    return a, b


def py_values(a, b):
    t = np.abs(a)
    mh = np.array([math.hypot(float(u), float(v)) for u, v in zip(a, b)])
    return [
        mh,                                     # 0 math.hypot
        np.sqrt(a * a + b * b),                 # 1
        np.arctan2(a, b),                       # 2
        np.degrees(a),                          # 3
        np.radians(a),                          # 4
        np.cos(a),                              # 5
        np.exp(a),                              # 6
        np.sqrt(t),                             # 7
        np.power(t, 2.4),                       # 8
        np.power(t, 1.0 / 3.0),                 # 9
        np.cbrt(t),                             # 10
        np.power(t, 7.0),                       # 11
        ((t * t * t) * (t * t * t)) * t,        # 12
        np.power(t, 2.0),                       # 13
        t * t,                                  # 14
        np.sin(a),                              # 15
    ]


def main():
    a, b = gen()
    print("编译 probe.cpp …")
    r = subprocess.run(["g++", "-O2", "-o", EXE, SRC], capture_output=True)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", "replace"))
        return 2
    r = subprocess.run([EXE, INP], capture_output=True)
    txt = r.stdout.decode().split()
    if len(txt) != N * len(NAMES):
        print("结果个数 %d，应为 %d" % (len(txt), N * len(NAMES)))
        print(r.stderr.decode("utf-8", "replace")[:400])
        return 2
    cpp = np.array([int(h, 16) for h in txt], dtype=np.uint64).reshape(N, len(NAMES))
    py = py_values(a, b)

    print()
    print("样本 %d 组（a,b ∈ Lab 的 a/b 量级）" % N)
    print("%-34s %9s   %s" % ("对比", "不一致", "示例"))
    print("-" * 78)
    worst = []
    for i, name in enumerate(NAMES):
        pbits = np.asarray(py[i], dtype=np.float64).view(np.uint64)
        diff = pbits != cpp[:, i]
        n_bad = int(diff.sum())
        worst.append((name, n_bad))
        ex = ""
        if n_bad:
            k = int(np.argmax(diff))
            cv = struct.unpack("<d", struct.pack("<Q", int(cpp[k, i])))[0]
            ex = "(%.17g, %.17g): py=%.17g cpp=%.17g" % (a[k], b[k], py[i][k], cv)
        print("%-34s %9d   %s" % (name, n_bad, ex))

    print()
    print("=" * 78)
    ok = [n for n, c in worst if c == 0]
    bad = [(n, c) for n, c in worst if c]
    print("完全一致：%d 项" % len(ok))
    for n in ok:
        print("   √ " + n)
    if bad:
        print("有差异：")
        for n, c in bad:
            print("   × %-32s %d/%d" % (n, c, N))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
