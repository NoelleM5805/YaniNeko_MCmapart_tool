# -*- coding: utf-8 -*-
"""
图标贴图集自检
==============

重新生成方块数据（tools/gen_blockdata.py）之后跑一下，确认：
  1. blockdata.py 里没有重复的方块 ID / 重复的图标坐标 / 越界坐标
  2. 贴图集尺寸与 ICON_COLS / ICON_ROWS / ICON_SIZE 一致
  3. 逐格比对：每个格子的像素必须正好等于该方块应有的贴图
     （对不上 = 图标错位，多半是生成或坐标写错了）

用法（在工作区根目录执行）：
    python tools/check_icons.py
"""

import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))       # <root>/tools
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "src", "maptool", "data")
sys.path.insert(0, os.path.join(ROOT, "src"))           # 让 maptool 包能导入

from PIL import Image

from maptool.data import blockdata as bd

import gen_blockdata as G                               # 同目录的开发脚本


def main():
    SIZE, COLS, ROWS = bd.ICON_SIZE, bd.ICON_COLS, bd.ICON_ROWS
    rows = bd.BLOCK_ROWS
    problems = []

    # ---------- 1. 数据表 ----------
    ids = [r[0] for r in rows]
    dup_ids = {k: v for k, v in Counter(ids).items() if v > 1}
    if dup_ids:
        problems.append("重复的方块 ID：%s" % dup_ids)

    coords = [(r[3], r[4]) for r in rows]
    dup_coord = {k: v for k, v in Counter(coords).items() if v > 1}
    if dup_coord:
        problems.append("重复的图标坐标：%s" % dup_coord)

    oob = [(r[0], r[3], r[4]) for r in rows
           if not (0 <= r[3] < COLS and 0 <= r[4] < ROWS)]
    if oob:
        problems.append("越界坐标：%s" % oob)

    n_props = sum(1 for r in rows if len(r) > 5 and r[5])
    print("方块数：%d ｜ 颜色组：%d ｜ 带方块状态：%d"
          % (len(rows), len({r[2] for r in rows}), n_props))

    # ---------- 2. 贴图集尺寸 ----------
    sheet_path = os.path.join(DATA, bd.ICON_SHEET)
    sheet = Image.open(sheet_path).convert("RGBA")
    want = (COLS * SIZE, ROWS * SIZE)
    print("贴图集：%s %dx%d（声明 %d 列 × %d 行 × %dpx = %dx%d）"
          % (sheet_path, sheet.width, sheet.height, COLS, ROWS, SIZE, want[0], want[1]))
    if sheet.size != want:
        problems.append("贴图集尺寸 %s != 声明 %s" % (sheet.size, want))

    # ---------- 3. 逐格比对 ----------
    cache = os.path.join(HERE, "_build_cache", "textures")
    tex_dir = os.path.join(HERE, "textures_block")

    def find_tex(bid):
        base = G.tex_base(bid)
        for suf in G.TEX_SUFFIXES:
            name = base + suf + ".png"
            for d in (tex_dir, cache):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    return p
        return None

    checked = mismatch = 0
    no_tex = []
    samples = []
    for r in rows:
        bid, label, cx, cy = r[0], r[1], r[3], r[4]
        p = find_tex(bid)
        if not p:
            no_tex.append("%s(%s)" % (label, bid))
            continue
        src = Image.open(p).convert("RGBA")
        if src.size != (SIZE, SIZE):
            src = src.resize((SIZE, SIZE), Image.NEAREST)
        got = sheet.crop((cx * SIZE, cy * SIZE, (cx + 1) * SIZE, (cy + 1) * SIZE))
        checked += 1
        if src.tobytes() != got.tobytes():
            mismatch += 1
            if len(samples) < 10:
                samples.append("%s(%s) @(%d,%d)" % (label, bid, cx, cy))

    print("逐格比对：检查 %d 个" % checked)
    if no_tex:
        print("  没有贴图（用纯色块代替，属正常）：%s" % ", ".join(no_tex))
    if mismatch:
        problems.append("有 %d 个格子的内容与该方块贴图不符：%s" % (mismatch, samples))
        print("  ✗ 不匹配 %d 个：%s" % (mismatch, ", ".join(samples)))
    else:
        print("  ✓ 全部一致")

    print()
    if problems:
        print("=" * 64)
        print("发现问题：")
        for p in problems:
            print("  ✗", p)
        print("=" * 64)
        print("建议：重跑 python tools/gen_blockdata.py 重新生成贴图集")
        return 1

    print("=" * 64)
    print("✓ 图标贴图集正常：坐标无重复、无越界，每个格子都对应正确的方块贴图")
    print("=" * 64)
    print()
    print("提示：浏览器端如果还看到错位，是缓存了旧图 —— 按 Ctrl+F5 强制刷新即可。")
    print("      页面地址栏里的 icons.png 带版本号，正常情况下会自动失效重取。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
